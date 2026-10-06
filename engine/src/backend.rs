//! Audio backend: live output via CPAL, with a null-sink fallback.
//!
//! Many machines (CI, servers, containers) have no audio device. Instead of
//! failing, the engine runs a null sink: a paced thread that calls the same
//! render path and discards the output, so transport, scheduling, and stats
//! stay fully functional. The UI reports which backend is active.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

pub struct BackendStats {
    pub callbacks: AtomicU64,
    pub max_callback_us: AtomicU64,
    pub underruns: AtomicU64,
}

impl BackendStats {
    pub fn new() -> Self {
        BackendStats {
            callbacks: AtomicU64::new(0),
            max_callback_us: AtomicU64::new(0),
            underruns: AtomicU64::new(0),
        }
    }

    pub fn record_callback(&self, micros: u64) {
        self.callbacks.fetch_add(1, Ordering::Relaxed);
        // Max via compare-exchange loop; contention-free in practice.
        let mut prev = self.max_callback_us.load(Ordering::Relaxed);
        while micros > prev {
            match self.max_callback_us.compare_exchange_weak(
                prev,
                micros,
                Ordering::Relaxed,
                Ordering::Relaxed,
            ) {
                Ok(_) => break,
                Err(next) => prev = next,
            }
        }
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
    if probe_live_output(sample_rate) {
        // A stream opened successfully a moment ago; build the real one.
        // (Rare race: the device vanishes between probe and build. Then the
        // error is reported to the caller.)
        start_cpal(sample_rate, render)
    } else {
        Ok(start_null_sink(sample_rate, render))
    }
}

/// Open and immediately drop a throwaway stream to test whether live
/// output really works on this machine.
fn probe_live_output(sample_rate: u32) -> bool {
    use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};
    let host = cpal::default_host();
    let device = match host.default_output_device() {
        Some(d) => d,
        None => return false,
    };
    let config = cpal::StreamConfig {
        channels: 2,
        sample_rate: cpal::SampleRate(sample_rate),
        buffer_size: cpal::BufferSize::Default,
    };
    match device.build_output_stream(
        &config,
        |_out: &mut [f32], _: &cpal::OutputCallbackInfo| {},
        |_| {},
        None,
    ) {
        Ok(stream) => stream.play().is_ok(),
        Err(_) => false,
    }
}

fn start_cpal<F>(
    sample_rate: u32,
    mut render: F,
) -> Result<(Backend, String), String>
where
    F: FnMut(&mut [f32], u64) + Send + 'static,
{
    use cpal::traits::{DeviceTrait, HostTrait, StreamTrait};

    let host = cpal::default_host();
    let device = host
        .default_output_device()
        .ok_or_else(|| "no default output device".to_string())?;
    let dev_name = device.name().unwrap_or_else(|_| "unknown".to_string());

    let config = cpal::StreamConfig {
        channels: 2,
        sample_rate: cpal::SampleRate(sample_rate),
        buffer_size: cpal::BufferSize::Default,
    };

    let stats = Arc::new(BackendStats::new());
    let stats_cb = stats.clone();
    let underruns = stats.clone();
    let mut sample_pos: u64 = 0;

    // NOTE: `render` moves into the callback below. Callers probe first (see
    // `start_backend`), so a failure here is a rare device-vanished race and
    // the error is reported rather than recovered from.
    let stream = device
        .build_output_stream(
            &config,
            move |out: &mut [f32], _info: &cpal::OutputCallbackInfo| {
                let t0 = Instant::now();
                render(out, sample_pos);
                sample_pos += (out.len() / 2) as u64;
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

    let desc = format!("live output ({}: {})", host.id().name(), dev_name);
    Ok((Backend::Live { _stream: stream, stats }, desc))
}

fn start_null_sink(
    sample_rate: u32,
    mut render: impl FnMut(&mut [f32], u64) + Send + 'static,
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

    let desc = "null sink (no audio device found)".to_string();
    (
        Backend::NullSink {
            stop,
            thread: Some(thread),
            stats,
        },
        desc,
    )
}
