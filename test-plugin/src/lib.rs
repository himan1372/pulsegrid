//! Minimal CLAP audio-effect plugin for Pulsegrid's hosting tests.
//!
//! Stereo in/out, one parameter (gain 0..2, default 1). Multiplies the
//! input by the gain. Deterministic and dependency-free apart from
//! clack, so the test suite never needs network access or third-party
//! plugin downloads.

use std::ffi::CStr;
use std::sync::atomic::{AtomicU64, Ordering};

use clack_common::events::event_types::ParamValueEvent;
use clack_common::events::spaces::CoreEventSpace;
use clack_common::utils::ClapId;
use clack_extensions::audio_ports::{
    AudioPortFlags, AudioPortInfo, AudioPortInfoWriter, AudioPortType, PluginAudioPortsImpl,
};
use clack_extensions::params::{
    ParamDisplayWriter, ParamInfo, ParamInfoFlags, ParamInfoWriter,
    PluginAudioProcessorParams, PluginMainThreadParams,
};
use clack_extensions::state::{PluginState, PluginStateImpl};
use clack_plugin::prelude::*;

pub const GAIN_PARAM_ID: u32 = 7;
/// Structural parameter: toggling it changes the reported latency
/// (0 samples off / 480 samples on), modelling e.g. a lookahead
/// limiter. The plugin calls `host->request_restart()` when it
/// changes while active, per the CLAP latency contract.
pub const LOOKAHEAD_PARAM_ID: u32 = 8;
pub const LOOKAHEAD_LATENCY_SAMPLES: u32 = 480;

pub struct TestGain;

impl Plugin for TestGain {
    type AudioProcessor<'a> = TestGainAudio<'a>;
    type Shared<'a> = TestGainShared;
    type MainThread<'a> = TestGainMain<'a>;

    fn declare_extensions(
        builder: &mut PluginExtensions<Self>,
        _shared: Option<&Self::Shared<'_>>,
    ) {
        builder.register::<clack_extensions::params::PluginParams>();
        builder.register::<clack_extensions::audio_ports::PluginAudioPorts>();
        builder.register::<clack_extensions::gui::PluginGui>();
        builder.register::<clack_extensions::latency::PluginLatency>();
        builder.register::<PluginState>();
        builder.register::<clack_extensions::state_context::PluginStateContext>();
        builder.register::<TestPresetLoad>();
    }
}

impl DefaultPluginFactory for TestGain {
    fn get_descriptor() -> PluginDescriptor {
        PluginDescriptor::new("org.pulsegrid.test-gain", "Pulsegrid Test Gain")
            .with_vendor("Pulsegrid")
            .with_features([c"audio-effect", c"utility"])
    }

    fn new_shared(_host: HostSharedHandle<'_>) -> Result<Self::Shared<'_>, PluginError> {
        Ok(TestGainShared {
            gain_bits: AtomicU64::new(1.0f64.to_bits()),
            lookahead: std::sync::atomic::AtomicBool::new(false),
        })
    }

    fn new_main_thread<'a>(
        host: HostMainThreadHandle<'a>,
        shared: &'a Self::Shared<'a>,
    ) -> Result<Self::MainThread<'a>, PluginError> {
        Ok(TestGainMain {
            host,
            shared,
            save_count: std::cell::Cell::new(0),
        })
    }
}

/// The host handle, kept so the main thread can call host callbacks
/// (`mark_dirty()`, preset-load `loaded()`/`on_error()`).

/// Cross-thread plugin state, mirroring what real plugins do: the audio
/// processor writes applied parameter values here so the main thread
/// (param queries, state save) always sees the live values.
pub struct TestGainShared {
    gain_bits: AtomicU64,
    /// Structural DSP switch from the Lookahead parameter. The audio
    /// processor writes it here when a param event arrives (so the
    /// main thread's latency report and state save always see the
    /// live value), and calls `host->request_restart()` when it
    /// changes while active.
    lookahead: std::sync::atomic::AtomicBool,
}

impl<'a> PluginShared<'a> for TestGainShared {}

pub struct TestGainMain<'a> {
    host: HostMainThreadHandle<'a>,
    shared: &'a TestGainShared,
    /// Non-parameter state: counts how many times the state was saved.
    /// Deliberately NOT a CLAP parameter, so tests can prove the blob
    /// carries state that parameters alone cannot.
    save_count: std::cell::Cell<u64>,
}

impl<'a> TestGainMain<'a> {
    fn live_gain(&self) -> f64 {
        f64::from_bits(self.shared.gain_bits.load(Ordering::Relaxed))
    }
    fn set_live_gain(&self, gain: f64) {
        self.shared
            .gain_bits
            .store(gain.to_bits(), Ordering::Relaxed);
    }
    fn lookahead_on(&self) -> bool {
        self.shared
            .lookahead
            .load(std::sync::atomic::Ordering::Relaxed)
    }

    /// Call the host's `mark_dirty()` (CLAP_EXT_STATE host side).
    /// Models a plugin whose *non-parameter* state changed outside any
    /// parameter edit — the exact case the dirty-tracking path exists
    /// for.
    fn report_dirty(&self) {
        use clack_extensions::state::HostState;
        if let Some(ext) = self.host.get_extension::<HostState>() {
            ext.mark_dirty(&self.host);
        }
    }

    /// Call the host's preset-load `loaded()` callback so the host can
    /// keep its preset browser in sync (raw FFI: clack 0.2 has no safe
    /// plugin-side wrapper for this stable extension).
    fn report_preset_loaded(&self, location: &str, load_key: Option<&str>) {
        use clack_common::extensions::Extension;
        if let Some(ext) = self.host.get_extension::<HostPresetLoadCaller>() {
            let loaded = self.host.use_extension(ext.raw()).loaded;
            if let Some(loaded) = loaded {
                let location_c = std::ffi::CString::new(location).unwrap();
                let load_key_c = load_key.map(|k| std::ffi::CString::new(k).unwrap());
                unsafe {
                    loaded(
                        self.host.as_raw(),
                        0, // file location kind
                        location_c.as_ptr(),
                        load_key_c
                            .as_ref()
                            .map(|c| c.as_ptr())
                            .unwrap_or(std::ptr::null()),
                    )
                };
            }
        }
    }

    /// Call the host's preset-load `on_error()` callback.
    fn report_preset_error(&self, location: &str, message: &str) {
        use clack_common::extensions::Extension;
        if let Some(ext) = self.host.get_extension::<HostPresetLoadCaller>() {
            let on_error = self.host.use_extension(ext.raw()).on_error;
            if let Some(on_error) = on_error {
                let location_c = std::ffi::CString::new(location).unwrap();
                let message_c = std::ffi::CString::new(message).unwrap();
                unsafe {
                    on_error(
                        self.host.as_raw(),
                        0,
                        location_c.as_ptr(),
                        std::ptr::null(),
                        -1,
                        message_c.as_ptr(),
                    )
                };
            }
        }
    }
}

impl<'a> PluginMainThread<'a, TestGainShared> for TestGainMain<'a> {}

impl PluginMainThreadParams for TestGainMain<'_> {
    fn count(&self) -> u32 {
        2
    }

    fn get_info(&self, param_index: u32, info: &mut ParamInfoWriter) {
        if param_index == 0 {
            info.set(&ParamInfo {
                id: ClapId::from_raw(GAIN_PARAM_ID).unwrap(),
                flags: ParamInfoFlags::empty(),
                cookie: Default::default(),
                name: b"Gain",
                module: b"",
                min_value: 0.0,
                max_value: 2.0,
                default_value: 1.0,
            });
        } else if param_index == 1 {
            info.set(&ParamInfo {
                id: ClapId::from_raw(LOOKAHEAD_PARAM_ID).unwrap(),
                flags: ParamInfoFlags::IS_STEPPED,
                cookie: Default::default(),
                name: b"Lookahead",
                module: b"",
                min_value: 0.0,
                max_value: 1.0,
                default_value: 0.0,
            });
        }
    }

    fn get_value(&self, param_id: ClapId) -> Option<f64> {
        if param_id.get() == GAIN_PARAM_ID {
            Some(self.live_gain())
        } else if param_id.get() == LOOKAHEAD_PARAM_ID {
            Some(
                if self
                    .shared
                    .lookahead
                    .load(std::sync::atomic::Ordering::Relaxed)
                {
                    1.0
                } else {
                    0.0
                },
            )
        } else {
            None
        }
    }

    fn value_to_text(
        &self,
        _param_id: ClapId,
        value: f64,
        writer: &mut ParamDisplayWriter,
    ) -> Result<(), std::fmt::Error> {
        use std::fmt::Write;
        write!(writer, "{:.2}", value)
    }

    fn text_to_value(&self, _param_id: ClapId, _text: &CStr) -> Option<f64> {
        None
    }

    fn flush(&self, input: &InputEvents, _output: &mut OutputEvents) {
        // Apply param events so a GUI-only instance (never
        // activated/processing) reflects flushed values.
        for event in input.iter() {
            if let Some(param_event) = event.as_event::<ParamValueEvent>() {
                if param_event.param_id() == ClapId::from_raw(GAIN_PARAM_ID) {
                    self.set_live_gain(param_event.value());
                } else if param_event.param_id() == ClapId::from_raw(LOOKAHEAD_PARAM_ID) {
                    self.shared.lookahead.store(
                        param_event.value() >= 0.5,
                        std::sync::atomic::Ordering::Relaxed,
                    );
                }
            }
        }
    }
}

/// Latency extension (`CLAP_EXT_LATENCY`, plugin side): the reported
/// latency follows the live Lookahead switch — 480 samples on, 0 off.
/// The host must query this only while the plugin is activated
/// (CLAP 1.2.2+); Pulsegrid queries it right after `activate()`.
impl clack_extensions::latency::PluginLatencyImpl for TestGainMain<'_> {
    fn get(&self) -> u32 {
        if self
            .shared
            .lookahead
            .load(std::sync::atomic::Ordering::Relaxed)
        {
            LOOKAHEAD_LATENCY_SAMPLES
        } else {
            0
        }
    }
}

/// Magic bytes "PGST" (little-endian u32) heading the state blob, so a
/// corrupt/truncated blob fails loudly instead of restoring garbage.
const STATE_MAGIC: u32 = 0x5453_4750;

/// Opaque state blob layout (host must NOT parse this; shown here only
/// because this is the test plugin):
/// [u32 magic LE][f64 gain LE][u64 save_count LE][u8 lookahead] = 21 bytes.
/// The lookahead byte is part of the blob (not just a parameter) so a
/// host restart preserves the structural DSP switch along with the
/// reported latency it implies.
///
/// The state-*context* save appends one more field:
/// [u32 context LE] = 25 bytes total, recording which
/// `StateContextType` the host asked for so tests can verify the host
/// used the right context. Context load accepts both layouts (the spec
/// declares them mutually loadable).
const STATE_CTX_LEN: usize = 25;
/// Ordinary (non-context) blob length.
const STATE_LEN: usize = 21;

impl PluginStateImpl for TestGainMain<'_> {
    fn save(&self, output: &mut clack_common::stream::OutputStream) -> Result<(), PluginError> {
        use std::io::Write;
        // Non-parameter state evolves here: the host cannot reproduce
        // this counter from parameter values alone.
        let count = self.save_count.get() + 1;
        self.save_count.set(count);
        let mut buf = Vec::with_capacity(STATE_LEN);
        buf.extend_from_slice(&STATE_MAGIC.to_le_bytes());
        buf.extend_from_slice(&self.live_gain().to_le_bytes());
        buf.extend_from_slice(&count.to_le_bytes());
        buf.push(self.lookahead_on() as u8);
        output
            .write_all(&buf)
            .map_err(|_| PluginError::Message("state write failed"))?;
        Ok(())
    }

    fn load(&self, input: &mut clack_common::stream::InputStream) -> Result<(), PluginError> {
        use std::io::Read;
        let mut buf = [0u8; STATE_LEN];
        input
            .read_exact(&mut buf)
            .map_err(|_| PluginError::Message("state read failed"))?;
        let magic = u32::from_le_bytes(buf[0..4].try_into().unwrap());
        if magic != STATE_MAGIC {
            return Err(PluginError::Message("bad state magic"));
        }
        let gain = f64::from_le_bytes(buf[4..12].try_into().unwrap());
        let count = u64::from_le_bytes(buf[12..20].try_into().unwrap());
        let lookahead = buf[20] != 0;
        self.set_live_gain(gain.clamp(0.0, 2.0));
        self.save_count.set(count);
        self.shared
            .lookahead
            .store(lookahead, std::sync::atomic::Ordering::Relaxed);
        Ok(())
    }
}

/// State-context save/load (`CLAP_EXT_STATE_CONTEXT`, stable since
/// CLAP 1.2.0). Behaves like the ordinary state save/load, but tags
/// the blob with the context the host requested.
impl clack_extensions::state_context::PluginStateContextImpl for TestGainMain<'_> {
    fn save(
        &self,
        output: &mut clack_common::stream::OutputStream,
        context_type: clack_extensions::state_context::StateContextType,
    ) -> Result<(), PluginError> {
        use std::io::Write;
        let count = self.save_count.get() + 1;
        self.save_count.set(count);
        let mut buf = Vec::with_capacity(STATE_CTX_LEN);
        buf.extend_from_slice(&STATE_MAGIC.to_le_bytes());
        buf.extend_from_slice(&self.live_gain().to_le_bytes());
        buf.extend_from_slice(&count.to_le_bytes());
        buf.push(self.lookahead_on() as u8);
        buf.extend_from_slice(&(context_type as u32).to_le_bytes());
        output
            .write_all(&buf)
            .map_err(|_| PluginError::Message("state write failed"))?;
        Ok(())
    }

    fn load(
        &self,
        input: &mut clack_common::stream::InputStream,
        _context_type: clack_extensions::state_context::StateContextType,
    ) -> Result<(), PluginError> {
        use std::io::Read;
        // Accept both the 21-byte ordinary blob and the 25-byte
        // contextual blob (spec-declared mutually loadable).
        let mut buf = [0u8; STATE_CTX_LEN];
        let mut len = 0;
        while len < STATE_CTX_LEN {
            match input.read(&mut buf[len..]) {
                Ok(0) => break,
                Ok(n) => len += n,
                Err(_) => {
                    return Err(PluginError::Message("state read failed"));
                }
            }
        }
        if len != STATE_LEN && len != STATE_CTX_LEN {
            return Err(PluginError::Message("state read failed"));
        }
        let magic = u32::from_le_bytes(buf[0..4].try_into().unwrap());
        if magic != STATE_MAGIC {
            return Err(PluginError::Message("bad state magic"));
        }
        let gain = f64::from_le_bytes(buf[4..12].try_into().unwrap());
        let count = u64::from_le_bytes(buf[12..20].try_into().unwrap());
        let lookahead = buf[20] != 0;
        self.set_live_gain(gain.clamp(0.0, 2.0));
        self.save_count.set(count);
        self.shared
            .lookahead
            .store(lookahead, std::sync::atomic::Ordering::Relaxed);
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Native preset loading (CLAP_EXT_PRESET_LOAD, raw FFI)
// ---------------------------------------------------------------------------

use clack_common::extensions::{
    Extension, ExtensionImplementation, HostExtensionSide, PluginExtensionSide,
    RawExtension, RawExtensionImplementation,
};
use clap_sys::ext::preset_load::{
    clap_host_preset_load, clap_plugin_preset_load, CLAP_EXT_PRESET_LOAD,
};

/// Plugin-side view of the *host's* preset-load extension, for calling
/// `loaded()` / `on_error()` (raw FFI: clack 0.2 has no safe wrapper).
#[derive(Copy, Clone)]
struct HostPresetLoadCaller(
    RawExtension<HostExtensionSide, clap_host_preset_load>,
);

impl HostPresetLoadCaller {
    fn raw(
        &self,
    ) -> &RawExtension<HostExtensionSide, clap_host_preset_load> {
        &self.0
    }
}

// SAFETY: repr(C)-compatible with `clap_host_preset_load`.
unsafe impl Extension for HostPresetLoadCaller {
    const IDENTIFIERS: &[&CStr] = &[CLAP_EXT_PRESET_LOAD];
    type ExtensionSide = HostExtensionSide;

    #[inline]
    unsafe fn from_raw(raw: RawExtension<Self::ExtensionSide>) -> Self {
        // SAFETY: the caller guarantees the pointer type, as usual.
        Self(unsafe { raw.cast() })
    }
}

/// Plugin-side implementation of `CLAP_EXT_PRESET_LOAD`
/// (`from_location`). Native preset format for the test plugin: a
/// UTF-8 text file containing `gain=<float>`. On success the plugin
/// calls the host's `loaded()` (browser sync) and `mark_dirty()`
/// (the preset changed non-parameter-visible state); on failure it
/// calls `on_error()` and returns false.
#[derive(Copy, Clone)]
struct TestPresetLoad(
    RawExtension<PluginExtensionSide, clap_plugin_preset_load>,
);

// SAFETY: repr(C)-compatible with `clap_plugin_preset_load`.
unsafe impl Extension for TestPresetLoad {
    const IDENTIFIERS: &[&CStr] = &[CLAP_EXT_PRESET_LOAD];
    type ExtensionSide = PluginExtensionSide;

    #[inline]
    unsafe fn from_raw(raw: RawExtension<Self::ExtensionSide>) -> Self {
        // SAFETY: the caller guarantees the pointer type, as usual.
        Self(unsafe { raw.cast() })
    }
}

// SAFETY: the struct layout matches `clap_plugin_preset_load`.
unsafe impl<P> ExtensionImplementation<P> for TestPresetLoad
where
    P: Plugin,
    for<'a> P: Plugin<MainThread<'a> = TestGainMain<'a>>,
{
    const IMPLEMENTATION: RawExtensionImplementation =
        RawExtensionImplementation::new(&clap_plugin_preset_load {
            from_location: Some(test_preset_from_location::<P>),
        });
}

#[allow(clippy::missing_safety_doc)]
unsafe extern "C" fn test_preset_from_location<P>(
    plugin: *const clap_sys::plugin::clap_plugin,
    _location_kind: u32,
    location: *const std::os::raw::c_char,
    _load_key: *const std::os::raw::c_char,
) -> bool
where
    P: Plugin,
    for<'a> P: Plugin<MainThread<'a> = TestGainMain<'a>>,
{
    use clack_plugin::extensions::prelude::PluginWrapper;
    PluginWrapper::<P>::handle(plugin, |p| {
        // SAFETY: CLAP guarantees non-null UTF-8-ish C strings here;
        // lossy conversion keeps a hostile path from panicking us.
        let location = unsafe { CStr::from_ptr(location) }.to_string_lossy();
        let main = p.main_thread();
        let result: Result<(), String> = (|| {
            let text = std::fs::read_to_string(location.as_ref())
                .map_err(|e| format!("cannot read preset file: {e}"))?;
            let gain: f64 = text
                .trim()
                .strip_prefix("gain=")
                .ok_or_else(|| "preset must be `gain=<float>`".to_string())?
                .parse()
                .map_err(|_| "gain is not a number".to_string())?;
            if !(0.0..=2.0).contains(&gain) {
                return Err("gain out of range 0..=2".to_string());
            }
            main.set_live_gain(gain);
            Ok(())
        })();
        match result {
            Ok(()) => {
                // The preset changed the plugin's state: tell the host
                // (browser sync + dirty tracking, the real CLAP flow).
                main.report_preset_loaded(&location, None);
                main.report_dirty();
                Ok(())
            }
            Err(msg) => {
                main.report_preset_error(&location, &msg);
                Err(clack_plugin::extensions::prelude::PluginWrapperError::Message(
                    clap_sys::ext::log::CLAP_LOG_HOST_MISBEHAVING,
                    "preset load failed",
                ))
            }
        }
    })
    .is_some()
}

impl PluginAudioPortsImpl for TestGainMain<'_> {
    fn count(&self, _is_input: bool) -> u32 {
        1
    }

    fn get(&self, index: u32, is_input: bool, writer: &mut AudioPortInfoWriter) {
        if index == 0 {
            writer.set(&AudioPortInfo {
                id: ClapId::new(0),
                name: if is_input { b"Stereo In" } else { b"Stereo Out" },
                channel_count: 2,
                flags: AudioPortFlags::IS_MAIN,
                port_type: Some(AudioPortType::STEREO),
                in_place_pair: None,
            });
        }
    }
}

pub struct TestGainAudio<'a> {
    gain: f64,
    shared: &'a TestGainShared,
    /// Thread-safe host handle, kept so the audio thread can call
    /// `request_restart()` when a structural parameter changes.
    host: clack_plugin::host::HostSharedHandle<'a>,
}

impl<'a> PluginAudioProcessor<'a, TestGainShared, TestGainMain<'a>> for TestGainAudio<'a> {
    fn activate(
        host: HostAudioProcessorHandle<'a>,
        main_thread: &TestGainMain<'a>,
        shared: &'a TestGainShared,
        _audio_config: PluginAudioConfiguration,
    ) -> Result<Self, PluginError> {
        // Pick up the live gain, which a state load may have set before
        // activation (blob is authoritative over params).
        Ok(Self {
            gain: main_thread.live_gain(),
            shared,
            host: host.into(),
        })
    }

    fn process(
        &mut self,
        _process: Process,
        mut audio: Audio,
        events: Events,
    ) -> Result<ProcessStatus, PluginError> {
        self.apply_param_events(events.input);
        for mut port_pair in &mut audio {
            let Some(channel_pairs) = port_pair.channels()?.into_f32() else {
                continue;
            };
            for channel_pair in channel_pairs {
                match channel_pair {
                    ChannelPair::InputOutput(input, output) => {
                        for (i, o) in input.iter().zip(output.iter_mut()) {
                            *o = *i * self.gain as f32;
                        }
                    }
                    ChannelPair::InPlace(buf) => {
                        for s in buf.iter_mut() {
                            *s *= self.gain as f32;
                        }
                    }
                    ChannelPair::InputOnly(_) => {}
                    ChannelPair::OutputOnly(buf) => buf.fill(0.0),
                }
            }
        }
        Ok(ProcessStatus::Continue)
    }
}

impl<'a> TestGainAudio<'a> {
    fn apply_param_events(&mut self, input: &InputEvents) {
        for ev in input.iter() {
            if let Some(CoreEventSpace::ParamValue(p)) = ev.as_core_event() {
                let pv: &ParamValueEvent = p;
                if pv.param_id().map(|id| id.get()) == Some(GAIN_PARAM_ID) {
                    let gain = pv.value().clamp(0.0, 2.0);
                    self.gain = gain;
                    // Publish so the main thread (get_value, state save)
                    // observes the same value the DSP is using.
                    self.shared
                        .gain_bits
                        .store(gain.to_bits(), Ordering::Relaxed);
                } else if pv.param_id().map(|id| id.get()) == Some(LOOKAHEAD_PARAM_ID) {
                    let on = pv.value() >= 0.5;
                    // Structural change: the reported latency flips
                    // between 0 and 480 samples. Per the CLAP latency
                    // contract the plugin must NOT apply this inside
                    // process(); it asks the host to restart it so the
                    // host can re-activate, re-query the latency, and
                    // recalculate PDC.
                    if self.shared.lookahead.swap(on, Ordering::Relaxed) != on {
                        self.host.request_restart();
                    }
                }
            }
        }
    }
}

impl<'a> PluginAudioProcessorParams for TestGainAudio<'a> {
    fn flush(&mut self, input: &InputEvents, _output: &mut OutputEvents) {
        self.apply_param_events(input);
    }
}

clack_export_entry!(SinglePluginEntry<TestGain>);

// ---------------------------------------------------------------------------
// Minimal X11 floating GUI (for host GUI-path verification only)
// ---------------------------------------------------------------------------

use clack_extensions::gui::{
    GuiApiType, GuiConfiguration, GuiSize, PluginGuiImpl,
};
use clack_plugin::prelude::PluginError;
use std::cell::RefCell;
use std::ffi::CString;

struct TestGui {
    conn: Option<x11rb::rust_connection::RustConnection>,
    window: Option<x11rb::protocol::xproto::Window>,
}

impl TestGui {
    fn new() -> Self {
        TestGui { conn: None, window: None }
    }
}

thread_local! {
    static GUI: RefCell<TestGui> = RefCell::new(TestGui::new());
}

impl PluginGuiImpl for TestGainMain<'_> {
    fn is_api_supported(&self, configuration: GuiConfiguration) -> bool {
        configuration.is_floating
            && configuration.api_type == GuiApiType::X11
    }

    fn get_preferred_api(&self) -> Option<GuiConfiguration<'_>> {
        None
    }

    fn create(&self, configuration: GuiConfiguration) -> Result<(), PluginError> {
        if !self.is_api_supported(configuration) {
            return Err(PluginError::Message("unsupported GUI api"));
        }
        use x11rb::connection::Connection;
        use x11rb::protocol::xproto::*;
        use x11rb::wrapper::ConnectionExt as _;
        let (conn, screen_num) = x11rb::connect(None)
            .map_err(|_| PluginError::Message("x11 connect failed"))?;
        let screen = &conn.setup().roots[screen_num];
        let win = conn.generate_id()
            .map_err(|_| PluginError::Message("x11 id failed"))?;
        let title = CString::new("Pulsegrid Test Gain").unwrap();
        conn.create_window(
            screen.root_depth,
            win,
            screen.root,
            0, 0, 320, 120, 0,
            WindowClass::INPUT_OUTPUT,
            screen.root_visual,
            &CreateWindowAux::new()
                .event_mask(EventMask::EXPOSURE | EventMask::STRUCTURE_NOTIFY)
                .background_pixel(screen.white_pixel),
        )
        .map_err(|_| PluginError::Message("x11 create_window failed"))?;
        conn.change_property8(
            PropMode::REPLACE,
            win,
            AtomEnum::WM_NAME,
            AtomEnum::STRING,
            title.as_bytes(),
        )
        .map_err(|_| PluginError::Message("x11 title failed"))?;
        conn.flush()
            .map_err(|_| PluginError::Message("x11 flush failed"))?;
        GUI.with(|g| {
            let mut g = g.borrow_mut();
            g.conn = Some(conn);
            g.window = Some(win);
        });
        Ok(())
    }

    fn destroy(&self) {
        use x11rb::connection::Connection as _;
        use x11rb::protocol::xproto::ConnectionExt as _;
        GUI.with(|g| {
            let mut g = g.borrow_mut();
            if let (Some(conn), Some(win)) = (g.conn.take(), g.window.take()) {
                let _ = conn.destroy_window(win);
                let _ = conn.flush();
            }
        });
    }

    fn get_size(&self) -> Option<GuiSize> {
        Some(GuiSize { width: 320, height: 120 })
    }

    fn show(&self) -> Result<(), PluginError> {
        use x11rb::connection::Connection as _;
        use x11rb::protocol::xproto::ConnectionExt as _;
        GUI.with(|g| {
            let g = g.borrow();
            match (g.conn.as_ref(), g.window) {
                (Some(conn), Some(win)) => {
                    conn.map_window(win)
                        .map_err(|_| PluginError::Message("x11 map failed"))?;
                    conn.flush()
                        .map_err(|_| PluginError::Message("x11 flush failed"))?;
                    Ok(())
                }
                _ => Err(PluginError::Message("no gui window")),
            }
        })
    }

    fn hide(&self) -> Result<(), PluginError> {
        use x11rb::connection::Connection as _;
        use x11rb::protocol::xproto::ConnectionExt as _;
        GUI.with(|g| {
            let g = g.borrow();
            match (g.conn.as_ref(), g.window) {
                (Some(conn), Some(win)) => {
                    conn.unmap_window(win)
                        .map_err(|_| PluginError::Message("x11 unmap failed"))?;
                    conn.flush()
                        .map_err(|_| PluginError::Message("x11 flush failed"))?;
                    Ok(())
                }
                _ => Err(PluginError::Message("no gui window")),
            }
        })
    }

    fn set_scale(&self, _scale: f64) -> Result<(), PluginError> {
        Ok(())
    }

    fn set_size(&self, _size: GuiSize) -> Result<(), PluginError> {
        Ok(())
    }

    fn set_parent(
        &self,
        _window: clack_extensions::gui::Window,
    ) -> Result<(), PluginError> {
        Err(PluginError::Message("embedded not supported"))
    }

    fn set_transient(
        &self,
        _window: clack_extensions::gui::Window,
    ) -> Result<(), PluginError> {
        Ok(())
    }
}
