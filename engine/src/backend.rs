//! Audio backend: live output via CPAL, with a null-sink fallback.
//!
//! Many machines (CI, servers, containers) have no audio device. Instead of
//! failing, the engine runs a null sink: a paced thread that calls the same
//! render path and discards the output, so transport, scheduling, and stats
//! stay fully functional. The UI reports which backend is active.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

/// One user-visible output: a CPAL host (driver) plus one of its devices.
///
/// Returned by [`list_audio_devices`]; the UI shows these in the Settings
/// Audio section (FL Studio's Device dropdown / LMMS's Audio Interface +
/// device selectors are the reference designs).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AudioDevice {
    /// CPAL host id, e.g. "ALSA", "WASAPI", "CoreAudio".
    pub host_id: String,
    /// Human-readable host name (same as host_id in CPAL 0.15).
    pub host_name: String,
    /// Device name as reported by the OS, e.g. "default".
    pub device_name: String,
    /// True when this is the host's default output device.
    pub is_default: bool,
}

/// What the user asked for. `None` fields mean "system default":
/// default host, default device, and the driver's default buffer size.
///
/// The buffer size is the real-time lever both FL Studio and LMMS expose:
/// a bigger buffer gives the CPU more time to finish each block (fewer
/// underruns) at the cost of latency.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct AudioSelection {
    pub host_id: Option<String>,
    pub device_name: Option<String>,
    pub buffer_frames: Option<u32>,
}

/// Result of negotiating a stream config with a device.
///
/// Reference pattern: SDL's `SDL_OpenAudioDevice` takes a *desired* spec
/// and returns the *obtained* spec -- "these are just requests, the backend
/// may change any of these values" -- then SDL converts automatically.
/// CPAL leaves the conversion to us, so when the device can't do our
/// native stereo/44100/F32 we open its default config and convert at the
/// boundary with [`OutputAdapter`] instead of failing (the v0.42.0
/// behavior that left SteelSeries Sonar virtual devices silent).
struct NegotiatedOutput {
    /// The config the device will actually run.
    config: cpal::StreamConfig,
    /// `None` when the device runs our native format (no conversion).
    adapter: Option<OutputAdapter>,
    /// Human-readable actual format, e.g. "48000 Hz stereo" or
    /// "48000 Hz mono (converted from 44100 Hz stereo)".
    desc: String,
}

/// Converts engine-native stereo f32 @ `engine_rate` to whatever the
/// device actually runs (different rate and/or channel count).
///
/// Linear resampling + channel mapping. Sub-sample position error is
/// inaudible; the engine timeline stays sample-accurate enough for
/// event scheduling.
struct OutputAdapter {
    engine_rate: u32,
    device_rate: u32,
    device_channels: u16,
    /// Fractional engine-frame position (advanced per device block).
    engine_pos: f64,
    scratch: Vec<f32>,
}

impl OutputAdapter {
    fn new(engine_rate: u32, device_rate: u32, device_channels: u16) -> Self {
        OutputAdapter {
            engine_rate,
            device_rate,
            device_channels,
            engine_pos: 0.0,
            scratch: Vec::new(),
        }
    }

    /// Render one device block: engine renders stereo f32 into scratch,
    /// then we resample + channel-map into `out`.
    fn render_into<F>(&mut self, out: &mut [f32], render: &mut F, stats: &BackendStats)
    where
        F: FnMut(&mut [f32], u64),
    {
        let dev_ch = self.device_channels.max(1) as usize;
        let dev_frames = out.len() / dev_ch;
        if dev_frames == 0 {
            return;
        }
        let ratio = self.engine_rate as f64 / self.device_rate as f64;
        // Engine frames needed to cover the block, +1 for interpolation.
        let need = (dev_frames as f64 * ratio).ceil() as usize + 1;
        self.scratch.resize(need * 2, 0.0);
        let start_sample = self.engine_pos as u64;
        render(&mut self.scratch[..need * 2], start_sample);
        let base_pos = self.engine_pos - start_sample as f64;
        for i in 0..dev_frames {
            let epos = base_pos + i as f64 * ratio;
            let i0 = (epos as usize).min(need - 1);
            let frac = (epos - i0 as f64) as f32;
            let i1 = (i0 + 1).min(need - 1);
            let l = self.scratch[i0 * 2] * (1.0 - frac)
                + self.scratch[i1 * 2] * frac;
            let r = self.scratch[i0 * 2 + 1] * (1.0 - frac)
                + self.scratch[i1 * 2 + 1] * frac;
            let base = i * dev_ch;
            // Channel map: stereo source to whatever the device has.
            out[base] = if dev_ch == 1 { (l + r) * 0.5 } else { l };
            if dev_ch >= 2 {
                out[base + 1] = r;
                for c in 2..dev_ch {
                    out[base + c] = 0.0;
                }
            }
        }
        self.engine_pos += dev_frames as f64 * ratio;
        stats.record_block(dev_frames as u64);
    }
}

/// Pick a stream config the device actually supports.
///
/// 1. Prefer our native format (F32 stereo @ `engine_rate`) when any
///    supported config covers it -- zero conversion, zero surprises.
/// 2. Otherwise use the device's default output config and convert at
///    the boundary (see [`OutputAdapter`]).
///
/// Returns an error only when the device reports no usable config at all.
fn negotiate_output_config(
    device: &cpal::Device,
    engine_rate: u32,
    buffer_frames: Option<u32>,
) -> Result<NegotiatedOutput, String> {
    use cpal::traits::DeviceTrait;
    let buffer_size = match buffer_frames {
        Some(n) => cpal::BufferSize::Fixed(n),
        None => cpal::BufferSize::Default,
    };
    if let Ok(configs) = device.supported_output_configs() {
        for cfg in configs {
            if cfg.channels() == 2
                && cfg.sample_format() == cpal::SampleFormat::F32
                && cfg.min_sample_rate().0 <= engine_rate
                && engine_rate <= cfg.max_sample_rate().0
            {
                let mut config = cfg
                    .with_sample_rate(cpal::SampleRate(engine_rate))
                    .config();
                config.buffer_size = buffer_size;
                return Ok(NegotiatedOutput {
                    config,
                    adapter: None,
                    desc: format!("{engine_rate} Hz stereo"),
                });
            }
        }
    }
    // Native format unsupported (e.g. virtual devices locked to 48 kHz):
    // take whatever the device prefers and convert.
    let def = device
        .default_output_config()
        .map_err(|e| format!("device has no usable output config ({e})"))?;
    if def.sample_format() != cpal::SampleFormat::F32 {
        return Err(format!(
            "device default format {:?} is not float32 (unsupported)",
            def.sample_format()
        ));
    }
    let mut config = def.config();
    config.buffer_size = buffer_size;
    let desc = format!(
        "{} Hz {} (converted from {engine_rate} Hz stereo)",
        def.sample_rate().0,
        if def.channels() == 1 {
            "mono".to_string()
        } else {
            format!("{}ch", def.channels())
        },
    );
    Ok(NegotiatedOutput {
        config,
        adapter: Some(OutputAdapter::new(
            engine_rate,
            def.sample_rate().0,
            def.channels(),
        )),
        desc,
    })
}

/// Enumerate every output device on every available CPAL host.
///
/// Pure query: never opens a stream, never touches the running backend.
/// Errors (a host that cannot even be listed) are reported as `Err`;
/// individual devices whose names cannot be read are skipped.
pub fn list_audio_devices() -> Result<Vec<AudioDevice>, String> {
    list_audio_devices_filtered(Direction::Output)
}

/// Every *input* device (microphones, line-ins) on every available host.
///
/// FL Studio exposes a separate Input selector in Audio Settings (its
/// per-mixer-track Input menus read from the chosen input device); we
/// mirror that separation. Selecting an input here only records the
/// choice -- no input stream is opened in this version.
pub fn list_audio_input_devices() -> Result<Vec<AudioDevice>, String> {
    list_audio_devices_filtered(Direction::Input)
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Direction {
    Output,
    Input,
}

fn list_audio_devices_filtered(dir: Direction) -> Result<Vec<AudioDevice>, String> {
    use cpal::traits::{DeviceTrait, HostTrait};
    let mut out = Vec::new();
    for hid in cpal::available_hosts() {
        let host = match cpal::host_from_id(hid) {
            Ok(h) => h,
            Err(e) => return Err(format!("audio host '{}' unavailable: {}", hid.name(), e)),
        };
        let host_id = hid.name().to_string();
        let default_name: Option<String> = match dir {
            Direction::Output => host
                .default_output_device()
                .and_then(|d| d.name().ok()),
            Direction::Input => host
                .default_input_device()
                .and_then(|d| d.name().ok()),
        };
        let devices = host
            .devices()
            .map_err(|e| format!("cannot list devices for host '{host_id}': {e}"))?;
        for dev in devices {
            let name = match dev.name() {
                Ok(n) => n,
                Err(_) => continue, // unreadable name: skip, don't fail all
            };
            // CPAL's devices() mixes inputs and outputs; keep only the
            // ones that actually support the requested direction.
            let supports = match dir {
                Direction::Output => dev.supported_output_configs().map(|mut c| c.next().is_some()).unwrap_or(false),
                Direction::Input => dev.supported_input_configs().map(|mut c| c.next().is_some()).unwrap_or(false),
            };
            if !supports {
                continue;
            }
            out.push(AudioDevice {
                host_id: host_id.clone(),
                host_name: host_id.clone(),
                device_name: name.clone(),
                is_default: default_name.as_deref() == Some(name.as_str()),
            });
        }
    }
    Ok(out)
}

/// Resolve a user selection to a concrete (host, device, label) triple.
///
/// `None` fields fall back to the system default host/device, exactly like
/// the old hardcoded path. A device that vanished since enumeration is a
/// clean `Err`, never a panic.
fn resolve_device(
    sel: &AudioSelection,
) -> Result<(cpal::Host, cpal::Device, String), String> {
    use cpal::traits::{DeviceTrait, HostTrait};
    let host = match &sel.host_id {
        Some(id) => {
            // HostId has no FromStr; resolve by name against available hosts.
            let hid = cpal::available_hosts()
                .into_iter()
                .find(|h| h.name() == id.as_str())
                .ok_or_else(|| format!("unknown audio host '{id}'"))?;
            cpal::host_from_id(hid)
                .map_err(|e| format!("audio host '{id}' unavailable: {e}"))?
        }
        None => cpal::default_host(),
    };
    let host_name = host.id().name().to_string();
    let device = match &sel.device_name {
        Some(want) => host
            .devices()
            .map_err(|e| format!("cannot list devices for '{host_name}': {e}"))?
            .find(|d| d.name().ok().as_deref() == Some(want.as_str()))
            .ok_or_else(|| {
                format!("audio device '{want}' not found on '{host_name}'")
            })?,
        None => host
            .default_output_device()
            .ok_or_else(|| format!("no default output device on '{host_name}'"))?,
    };
    let dev_name = device.name().unwrap_or_else(|_| "unknown".to_string());
    Ok((host, device, format!("{host_name}: {dev_name}")))
}

pub struct BackendStats {
    pub callbacks: AtomicU64,
    pub max_callback_us: AtomicU64,
    pub underruns: AtomicU64,
    /// Largest audio block (frames) seen on this backend. Lets the UI turn
    /// `max_callback_us` into FL Studio's CPU-meter metric: the percentage
    /// of the available buffer time the render consumed.
    pub block_frames: AtomicU64,
}

impl BackendStats {
    pub fn new() -> Self {
        BackendStats {
            callbacks: AtomicU64::new(0),
            max_callback_us: AtomicU64::new(0),
            underruns: AtomicU64::new(0),
            block_frames: AtomicU64::new(0),
        }
    }

    fn record_max(target: &AtomicU64, value: u64) {
        // Max via compare-exchange loop; contention-free in practice.
        let mut prev = target.load(Ordering::Relaxed);
        while value > prev {
            match target.compare_exchange_weak(prev, value, Ordering::Relaxed, Ordering::Relaxed) {
                Ok(_) => break,
                Err(next) => prev = next,
            }
        }
    }

    pub fn record_callback(&self, micros: u64) {
        self.callbacks.fetch_add(1, Ordering::Relaxed);
        Self::record_max(&self.max_callback_us, micros);
    }

    pub fn record_block(&self, frames: u64) {
        Self::record_max(&self.block_frames, frames);
    }
}

/// Handle to a running backend. Dropping stops the stream/thread.
pub enum Backend {
    Live {
        _stream: cpal::Stream,
        stats: Arc<BackendStats>,
    },
    NullSink {
        stop: Arc<AtomicBool>,
        thread: Option<std::thread::JoinHandle<()>>,
        stats: Arc<BackendStats>,
    },
}

impl Backend {
    pub fn stats(&self) -> &Arc<BackendStats> {
        match self {
            Backend::Live { stats, .. } => stats,
            Backend::NullSink { stats, .. } => stats,
        }
    }
}

impl Drop for Backend {
    fn drop(&mut self) {
        if let Backend::NullSink { stop, thread, .. } = self {
            stop.store(true, Ordering::Relaxed);
            if let Some(h) = thread.take() {
                let _ = h.join();
            }
        }
        // cpal Stream stops on drop automatically.
    }
}

/// Start the best available backend. `render` is called from the audio
/// thread (or the paced null-sink thread); it must be lock-free.
///
/// A cheap probe stream is opened first: some backends (e.g. ALSA with no
/// sound card) report a "default" device that cannot actually be opened, so
/// device presence alone is not a reliable signal. If the probe fails we go
/// straight to the null sink and the real render closure is never consumed
/// by a failed live attempt.
pub fn start_backend<F>(
    sample_rate: u32,
    render: F,
) -> Result<(Backend, String), String>
where
    F: FnMut(&mut [f32], u64) + Send + 'static,
{
    start_backend_with_selection(sample_rate, render, &AudioSelection::default())
}

/// Same as [`start_backend`], but opens the user's selected host/device
/// (FL Studio's Device dropdown / LMMS's Audio Interface selector are the
/// reference designs). `None` fields in `sel` mean "system default".
///
/// When the selected device cannot be opened, the engine still starts --
/// on the null sink -- and the returned description says exactly why, so
/// the UI can show it instead of failing silently (the old code hardcoded
/// the default device with no way to pick another one).
pub fn start_backend_with_selection<F>(
    sample_rate: u32,
    render: F,
    sel: &AudioSelection,
) -> Result<(Backend, String), String>
where
    F: FnMut(&mut [f32], u64) + Send + 'static,
{
    match probe_live_output(sample_rate, sel) {
        Ok(label) => {
            // A stream opened successfully a moment ago; build the real one.
            // (Rare race: the device vanishes between probe and build. Then the
            // error is reported to the caller.)
            start_cpal(sample_rate, render, sel, &label)
        }
        Err(reason) => Ok(start_null_sink_with_reason(sample_rate, render, &reason)),
    }
}

/// Open and immediately drop a throwaway stream to test whether the
/// *selected* output really works on this machine. Returns the device label
/// on success, or the reason live output is unavailable.
///
/// The probe negotiates the stream config exactly like the real stream
/// (see [`negotiate_output_config`]): a device that rejects our native
/// stereo/44100 (e.g. virtual devices locked to 48 kHz) is probed with
/// its default config instead of failing outright.
fn probe_live_output(
    sample_rate: u32,
    sel: &AudioSelection,
) -> Result<String, String> {
    use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
    let (_host, device, label) = resolve_device(sel)?;
    let neg = negotiate_output_config(
        &device,
        sample_rate,
        sel.buffer_frames,
    )
    .map_err(|e| format!("{label}: {e}"))?;
    match device.build_output_stream(
        &neg.config,
        |_out: &mut [f32], _: &cpal::OutputCallbackInfo| {},
        |_| {},
        None,
    ) {
        Ok(stream) => match stream.play() {
            Ok(()) => Ok(format!("{label} ({})", neg.desc)),
            Err(e) => Err(format!("{label}: probe stream would not play ({e})")),
        },
        Err(e) => Err(format!("{label}: probe stream failed ({e})")),
    }
}

fn start_cpal<F>(
    sample_rate: u32,
    mut render: F,
    sel: &AudioSelection,
    label: &str,
) -> Result<(Backend, String), String>
where
    F: FnMut(&mut [f32], u64) + Send + 'static,
{
    use cpal::traits::{DeviceTrait, StreamTrait};

    // Re-resolve (the probe already validated this selection a moment ago).
    let (_host, device, _label) = resolve_device(sel)?;

    // Negotiate the real stream config: native stereo/44100 when the
    // device supports it, otherwise its default config + conversion.
    let neg = negotiate_output_config(&device, sample_rate, sel.buffer_frames)
        .map_err(|e| format!("cpal stream negotiation failed: {e}"))?;

    let stats = Arc::new(BackendStats::new());
    let stats_cb = stats.clone();
    let underruns = stats.clone();
    let mut adapter = neg.adapter;
    // Engine-timeline position for the native (no-conversion) path.
    // The conversion path tracks its own fractional position.
    let mut sample_pos: u64 = 0;

    // NOTE: `render` moves into the callback below. Callers probe first (see
    // `start_backend`), so a failure here is a rare device-vanished race and
    // the error is reported rather than recovered from.
    let stream = device
        .build_output_stream(
            &neg.config,
            move |out: &mut [f32], _info: &cpal::OutputCallbackInfo| {
                let t0 = Instant::now();
                match adapter.as_mut() {
                    // Device runs our native format: render straight in.
                    None => {
                        render(out, sample_pos);
                        sample_pos += (out.len() / 2) as u64;
                        stats_cb.record_block((out.len() / 2) as u64);
                    }
                    // Device needs conversion (rate and/or channels).
                    Some(ad) => ad.render_into(out, &mut render, &stats_cb),
                }
                stats_cb.record_callback(t0.elapsed().as_micros() as u64);
            },
            move |err| {
                // Stream errors (e.g. device underrun) are counted, not fatal.
                let _ = err;
                underruns.underruns.fetch_add(1, Ordering::Relaxed);
            },
            None,
        )
        .map_err(|e| format!("cpal stream build failed: {}", e))?;

    stream
        .play()
        .map_err(|e| format!("cpal stream play failed: {}", e))?;

    let desc = format!("live output ({label} @ {})", neg.desc);
    Ok((Backend::Live { _stream: stream, stats }, desc))
}

fn start_null_sink(
    sample_rate: u32,
    render: impl FnMut(&mut [f32], u64) + Send + 'static,
) -> (Backend, String) {
    start_null_sink_with_reason(sample_rate, render, "no audio device found")
}

/// Null sink with an explicit reason, so the UI can tell the user *why*
/// there is no audible output (e.g. the selected device is unavailable).
fn start_null_sink_with_reason(
    sample_rate: u32,
    mut render: impl FnMut(&mut [f32], u64) + Send + 'static,
    reason: &str,
) -> (Backend, String) {
    let stats = Arc::new(BackendStats::new());
    let stats_t = stats.clone();
    let stop = Arc::new(AtomicBool::new(false));
    let stop_t = stop.clone();

    // Pace the thread in real time so transport/position behave like live.
    let block_frames: usize = 512;
    let block_dur = Duration::from_secs_f64(block_frames as f64 / sample_rate as f64);
    let mut scratch = vec![0.0f32; block_frames * 2];
    let mut sample_pos: u64 = 0;

    let thread = std::thread::Builder::new()
        .name("pulsegrid-null-sink".to_string())
        .spawn(move || {
            let mut next_deadline = Instant::now() + block_dur;
            while !stop_t.load(Ordering::Relaxed) {
                let t0 = Instant::now();
                render(&mut scratch, sample_pos);
                sample_pos += block_frames as u64;
                stats_t.record_block(block_frames as u64);
                stats_t.record_callback(t0.elapsed().as_micros() as u64);
                let now = Instant::now();
                if next_deadline > now {
                    std::thread::sleep(next_deadline - now);
                } else {
                    // We fell behind: count it and reset the deadline.
                    stats_t.underruns.fetch_add(1, Ordering::Relaxed);
                }
                next_deadline += block_dur;
            }
        })
        .expect("failed to spawn null-sink thread");

    let desc = format!("null sink ({reason})");
    (
        Backend::NullSink {
            stop,
            thread: Some(thread),
            stats,
        },
        desc,
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn selection_default_is_system_default() {
        let sel = AudioSelection::default();
        assert_eq!(sel.host_id, None);
        assert_eq!(sel.device_name, None);
    }

    #[test]
    fn resolve_unknown_host_is_clean_error() {
        // Pure logic: no audio hardware touched (host lookup fails first).
        let sel = AudioSelection {
            host_id: Some("NO-SUCH-HOST-XYZ".to_string()),
            device_name: None,
            ..Default::default()
        };
        let err = match resolve_device(&sel) {
            Ok(_) => panic!("expected unknown-host error"),
            Err(e) => e,
        };
        assert!(err.contains("unknown audio host"), "got: {err}");
    }

    #[test]
    fn list_devices_never_panics_without_hardware() {
        // On a headless CI box this may be Ok(empty) or Err; it must not
        // panic. (Real devices are exercised by the Python/Xvfb layer.)
        let _ = list_audio_devices();
    }

    #[test]
    fn device_descriptor_equality() {
        let a = AudioDevice {
            host_id: "ALSA".into(),
            host_name: "ALSA".into(),
            device_name: "default".into(),
            is_default: true,
        };
        let b = a.clone();
        assert_eq!(a, b);
    }

    #[test]
    fn buffer_size_selection_is_part_of_selection() {
        let mut sel = AudioSelection::default();
        assert_eq!(sel.buffer_frames, None);
        sel.buffer_frames = Some(1024);
        assert_eq!(sel.buffer_frames, Some(1024));
    }

    #[test]
    fn stats_record_block_tracks_max() {
        let s = BackendStats::new();
        s.record_block(256);
        s.record_block(512);
        s.record_block(128);
        assert_eq!(s.block_frames.load(Ordering::Relaxed), 512);
        s.record_callback(100);
        s.record_callback(50);
        assert_eq!(s.max_callback_us.load(Ordering::Relaxed), 100);
    }

    #[test]
    fn adapter_identity_at_native_rate() {
        // Same rate, stereo: output equals the rendered input.
        let mut ad = OutputAdapter::new(44100, 44100, 2);
        let stats = BackendStats::new();
        let mut out = vec![0.0f32; 2 * 64];
        let mut render = |buf: &mut [f32], _pos: u64| {
            for (i, s) in buf.iter_mut().enumerate() {
                *s = i as f32;
            }
        };
        ad.render_into(&mut out, &mut render, &stats);
        for (i, s) in out.iter().enumerate() {
            assert!((s - i as f32).abs() < 1e-5, "i={i} s={s}");
        }
        assert_eq!(stats.block_frames.load(Ordering::Relaxed), 64);
    }

    #[test]
    fn adapter_upsample_is_linear() {
        // Device at 2x the engine rate: linear interpolation between
        // engine frames.
        let mut ad = OutputAdapter::new(44100, 88200, 2);
        let stats = BackendStats::new();
        let mut out = vec![0.0f32; 2 * 8];
        // Engine renders a ramp: frame k -> (k, -k).
        let mut render = |buf: &mut [f32], _pos: u64| {
            for k in 0..buf.len() / 2 {
                buf[k * 2] = k as f32;
                buf[k * 2 + 1] = -(k as f32);
            }
        };
        ad.render_into(&mut out, &mut render, &stats);
        // Device frame 1 sits halfway between engine frames 0 and 1.
        assert!((out[2] - 0.5).abs() < 1e-5, "l={}", out[2]);
        assert!((out[3] + 0.5).abs() < 1e-5, "r={}", out[3]);
        // Device frame 2 lands exactly on engine frame 1.
        assert!((out[4] - 1.0).abs() < 1e-5);
    }

    #[test]
    fn adapter_mono_mixes_down() {
        let mut ad = OutputAdapter::new(44100, 44100, 1);
        let stats = BackendStats::new();
        let mut out = vec![0.0f32; 4];
        let mut render = |buf: &mut [f32], _pos: u64| {
            for k in 0..buf.len() / 2 {
                buf[k * 2] = 1.0;
                buf[k * 2 + 1] = 0.5;
            }
        };
        ad.render_into(&mut out, &mut render, &stats);
        for s in out {
            assert!((s - 0.75).abs() < 1e-6, "s={s}");
        }
    }

    #[test]
    fn adapter_advances_engine_position() {
        // 48000 Hz device: engine_pos advances by 44100/48000 per frame.
        use std::cell::RefCell;
        let mut ad = OutputAdapter::new(44100, 48000, 2);
        let stats = BackendStats::new();
        let seen = RefCell::new(Vec::new());
        let mut render = |buf: &mut [f32], pos: u64| {
            seen.borrow_mut().push(pos);
            buf.fill(0.0);
        };
        let mut out = vec![0.0f32; 2 * 480];
        ad.render_into(&mut out, &mut render, &stats);
        // 480 device frames consume 441 engine frames.
        assert!((ad.engine_pos - 441.0).abs() < 1e-9, "pos={}", ad.engine_pos);
        assert_eq!(*seen.borrow(), vec![0]);
        let mut out2 = vec![0.0f32; 2 * 480];
        ad.render_into(&mut out2, &mut render, &stats);
        // Second block continues where the first left off (fractional).
        assert_eq!(*seen.borrow(), vec![0, 441]);
    }
}
