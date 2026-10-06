//! CLAP plugin hosting for audio effects.
//!
//! ARCHITECTURE DECISION (deliberate, CLAP-only): Pulsegrid's sole
//! third-party plugin ABI is CLAP. There is no VST/VST2/VST3/AU/LV2
//! hosting and none is planned. Rationale (from the FL-vs-LMMS
//! CLAP-only research): one lifecycle model, one event model, one
//! state model, one extension-discovery mechanism -- instead of a
//! compatibility layer per legacy format. Native built-in effects
//! (delay/drive/filter) and CLAP plugins are the only two effect
//! kinds; native built-in voices and CLAP instruments are the only
//! two generator kinds.
//!
//! V1 scope, stated honestly:
//! * Audio-effect plugins only. Instruments need note routing, which is a
//!   separate project.
//! * Stereo in / stereo out. Plugins without a stereo pair are rejected
//!   at load with a clear error.
//! * No plugin GUIs — embedding foreign windows in the UI toolkit is out
//!   of scope; Pulsegrid shows a generic parameter editor instead.
//! * Plugin state: the CLAP state extension blob is saved/restored when
//!   the plugin supports it (opaque bytes; the host never parses them).
//!   Parameter values are ALSO saved, as a fallback for plugins without
//!   the state extension and for human-readable inspection. When a state
//!   blob loads successfully it is authoritative (params are not applied
//!   on top of it).
//! * Plugin libraries stay loaded for the session once used (normal host
//!   behavior); per-instance data is freed when the effect is removed.
//!
//! Threading: plugin *instances* are created and activated on whichever
//! thread builds the effect chain; the *started audio processor* is
//! `Send` and lives in the graph, where `process()` is called from the
//! single audio/render thread. Parameter changes (UI or automation) are
//! queued as CLAP param-value events and flushed on the next block, so
//! no locks are needed on the audio thread.
//!
//! State blobs: the main-thread `PluginInstance` is `!Send`, so it cannot
//! live in the audio graph. `load()` returns it alongside the started
//! processor; the control thread keeps it in a registry
//! (`Engine::plugin_instances`) and calls `save_plugin_state()` at
//! project-save time. State save/load are main-thread CLAP operations
//! (never called from the audio thread).
//!
//! THREADING CONTRACT (mirrors CLAP's thread rules):
//! * Control/main thread only: `PluginInstance::new` (create+init),
//!   `activate`, port/latency/param descriptor queries, state
//!   save/load, GUI sessions, plugin scanning. `Engine::
//!   save_plugin_states` and the instance registry live here.
//! * Audio/render thread only: `process_block` / `process_notes`
//!   (i.e. CLAP `process`), and `queue_param` (which becomes CLAP
//!   param-value events flushed with the next block -- no locks).
//! * Graph-build thread (control thread on `play()`/`render_wav`,
//!   audio thread on live arrangement updates): instantiation +
//!   activation + `start_processing` happen together in `load()`.
//!   This is a deliberate pragmatic liberty (CLAP nominally wants
//!   create/init on the main thread); it is the pre-existing
//!   architecture, kept explicit here rather than hidden.
//! * Never on the audio thread: state save/load, GUI calls, scanning,
//!   or anything that can block (I/O, locks, allocation-heavy work
//!   beyond the preallocated scratch buffers).

use std::collections::HashSet;
use std::ffi::CString;
use std::path::{Path, PathBuf};

use clack_common::events::event_types::{
    NoteChokeEvent, NoteOffEvent, NoteOnEvent, ParamValueEvent,
};
use clack_common::events::Pckn;
use clack_common::utils::ClapId;
use clack_extensions::audio_ports::{AudioPortInfoBuffer, PluginAudioPorts};
use clack_extensions::latency::PluginLatency;
use clack_extensions::params::{ParamInfoBuffer, PluginParams};
use clack_extensions::state::PluginState;
use clack_host::events::io::EventBuffer;
use clack_host::prelude::*;

// ---------------------------------------------------------------------------
// Host implementation
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Host implementation
// ---------------------------------------------------------------------------

pub(crate) struct PulsegridShared {
    slot: Option<PluginSlotKey>,
    events: PluginEventSinks,
}

impl PulsegridShared {
    fn new(slot: Option<PluginSlotKey>, events: PluginEventSinks) -> Self {
        PulsegridShared { slot, events }
    }
}

impl<'a> SharedHandler<'a> for PulsegridShared {
    /// The plugin asks to be deactivated and re-activated so a
    /// structural change can apply (CLAP: latency, audio ports, or
    /// buffer reallocations may only change across a restart). This
    /// is `[thread-safe]` — it may arrive from the audio thread, so
    /// it only records the slot; the control thread performs the
    /// actual restart (`Engine::process_plugin_restarts`).
    fn request_restart(&self) {
        if let Some(slot) = self.slot {
            self.events.request_restart_slot(slot);
        }
    }
    fn request_process(&self) {}
    fn request_callback(&self) {}
}

/// Per-instance main-thread host state. Each `PluginInstance` gets its
/// own, carrying the slot it belongs to (if any) plus the shared
/// event sinks. This is what the plugin talks to when it calls
/// `mark_dirty()` or the preset-load `loaded()`/`on_error()` callbacks.
pub(crate) struct PulsegridMainThread {
    slot: Option<PluginSlotKey>,
    events: PluginEventSinks,
}

impl PulsegridMainThread {
    fn new(slot: Option<PluginSlotKey>, events: PluginEventSinks) -> Self {
        PulsegridMainThread { slot, events }
    }
}

impl<'a> MainThreadHandler<'a> for PulsegridMainThread {}

/// `CLAP_EXT_STATE` host side: the plugin reports that its state has
/// changed and should be saved again. Per the CLAP spec, a parameter
/// value change is *implicitly* dirty, so this callback specifically
/// captures non-parameter state changes the host cannot otherwise
/// observe (internal mappings, loaded samples, custom switches...).
/// The slot is recorded; the control thread drains it and marks the
/// project dirty.
impl clack_extensions::state::HostStateImpl for PulsegridMainThread {
    fn mark_dirty(&self) {
        if let Some(slot) = self.slot {
            self.events.mark_dirty_slot(slot);
        }
    }
}

/// `CLAP_EXT_LATENCY` host side: the plugin reports that its latency
/// has changed and should be re-queried. Per CLAP, latency may only
/// change across `activate()` — an active plugin should call
/// `request_restart()` first — so the control thread handles this by
/// restarting the slot and re-querying post-activation (the only
/// lifecycle point where the new value is valid), then PDC is
/// recalculated from the fresh value.
impl clack_extensions::latency::HostLatencyImpl for PulsegridMainThread {
    fn changed(&self) {
        if let Some(slot) = self.slot {
            self.events.report_latency_changed(slot);
        }
    }
}

/// Host-side receiver for `CLAP_EXT_PRESET_LOAD` callbacks
/// (`loaded` / `on_error`). Implemented on the main-thread handler so
/// each instance reports against its own slot.
pub trait HostPresetLoadImpl {
    fn preset_loaded(&self, location_kind: u32, location: &str, load_key: Option<&str>);
    fn preset_load_error(&self, location: &str, os_error: i32, message: &str);
}

impl HostPresetLoadImpl for PulsegridMainThread {
    fn preset_loaded(&self, location_kind: u32, location: &str, load_key: Option<&str>) {
        if let Ok(mut v) = self.events.preset_loaded.lock() {
            v.push(PresetLoadedEvent {
                slot: self.slot,
                location_kind,
                location: location.to_string(),
                load_key: load_key.map(str::to_string),
            });
        }
    }

    fn preset_load_error(&self, location: &str, os_error: i32, message: &str) {
        if let Ok(mut v) = self.events.preset_errors.lock() {
            v.push(PresetLoadError {
                slot: self.slot,
                location: location.to_string(),
                os_error,
                message: message.to_string(),
            });
        }
    }
}

/// Marker type for the host side of `CLAP_EXT_PRESET_LOAD`.
///
/// Raw FFI: clack 0.2 ships no safe wrapper for this stable CLAP
/// extension, so this mirrors clack's own `ExtensionImplementation`
/// pattern using `clap-sys` types directly. The plugin calls
/// `loaded()` after a successful `from_location()` so the host can
/// keep its preset browser in sync; `on_error()` reports load
/// failures with an OS error code and message.
#[derive(Copy, Clone)]
pub struct HostPresetLoad(
    clack_common::extensions::RawExtension<
        clack_common::extensions::HostExtensionSide,
        clap_sys::ext::preset_load::clap_host_preset_load,
    >,
);

// SAFETY: repr(C)-compatible with `clap_host_preset_load`.
unsafe impl clack_common::extensions::Extension for HostPresetLoad {
    const IDENTIFIERS: &[&std::ffi::CStr] = &[clap_sys::ext::preset_load::CLAP_EXT_PRESET_LOAD];
    type ExtensionSide = clack_common::extensions::HostExtensionSide;

    #[inline]
    unsafe fn from_raw(raw: clack_common::extensions::RawExtension<Self::ExtensionSide>) -> Self {
        // SAFETY: the caller guarantees the pointer type, as usual.
        Self(unsafe { raw.cast() })
    }
}

// SAFETY: the struct layout matches `clap_host_preset_load`.
unsafe impl<H: HostHandlers> clack_common::extensions::ExtensionImplementation<H> for HostPresetLoad
where
    for<'a> H: HostHandlers<MainThread<'a>: HostPresetLoadImpl>,
{
    const IMPLEMENTATION: clack_common::extensions::RawExtensionImplementation =
        clack_common::extensions::RawExtensionImplementation::new(
            &clap_sys::ext::preset_load::clap_host_preset_load {
                on_error: Some(host_preset_load_on_error::<H>),
                loaded: Some(host_preset_load_loaded::<H>),
            },
        );
}

#[allow(clippy::missing_safety_doc)]
unsafe extern "C" fn host_preset_load_on_error<H>(
    host: *const clap_sys::host::clap_host,
    _location_kind: u32,
    location: *const std::os::raw::c_char,
    _load_key: *const std::os::raw::c_char,
    os_error: i32,
    msg: *const std::os::raw::c_char,
) where
    H: HostHandlers,
    for<'a> H: HostHandlers<MainThread<'a>: HostPresetLoadImpl>,
{
    use clack_host::extensions::prelude::HostWrapper;
    let _ = HostWrapper::<H>::handle_main_thread(host, |h| {
        let location = if location.is_null() {
            String::new()
        } else {
            unsafe { std::ffi::CStr::from_ptr(location) }
                .to_string_lossy()
                .into_owned()
        };
        let message = if msg.is_null() {
            String::new()
        } else {
            unsafe { std::ffi::CStr::from_ptr(msg) }
                .to_string_lossy()
                .into_owned()
        };
        h.preset_load_error(&location, os_error, &message);
        Ok(())
    });
}

#[allow(clippy::missing_safety_doc)]
unsafe extern "C" fn host_preset_load_loaded<H>(
    host: *const clap_sys::host::clap_host,
    location_kind: u32,
    location: *const std::os::raw::c_char,
    load_key: *const std::os::raw::c_char,
) where
    H: HostHandlers,
    for<'a> H: HostHandlers<MainThread<'a>: HostPresetLoadImpl>,
{
    use clack_host::extensions::prelude::HostWrapper;
    let _ = HostWrapper::<H>::handle_main_thread(host, |h| {
        let location = if location.is_null() {
            String::new()
        } else {
            unsafe { std::ffi::CStr::from_ptr(location) }
                .to_string_lossy()
                .into_owned()
        };
        let load_key = if load_key.is_null() {
            None
        } else {
            Some(
                unsafe { std::ffi::CStr::from_ptr(load_key) }
                    .to_string_lossy()
                    .into_owned(),
            )
        };
        h.preset_loaded(location_kind, &location, load_key.as_deref());
        Ok(())
    });
}

/// Plugin-side handle for `CLAP_EXT_PRESET_LOAD` (raw FFI, same reason
/// as `HostPresetLoad`): asks the plugin to load one of its *native*
/// preset files. This is distinct from state-context save/load, which
/// moves opaque host-managed blobs; preset-load delegates to the
/// plugin's own native preset parser via `from_location()`.
#[derive(Copy, Clone)]
pub struct PluginPresetLoad(
    clack_common::extensions::RawExtension<
        clack_common::extensions::PluginExtensionSide,
        clap_sys::ext::preset_load::clap_plugin_preset_load,
    >,
);

// SAFETY: repr(C)-compatible with `clap_plugin_preset_load`.
unsafe impl clack_common::extensions::Extension for PluginPresetLoad {
    const IDENTIFIERS: &[&std::ffi::CStr] = &[clap_sys::ext::preset_load::CLAP_EXT_PRESET_LOAD];
    type ExtensionSide = clack_common::extensions::PluginExtensionSide;

    #[inline]
    unsafe fn from_raw(raw: clack_common::extensions::RawExtension<Self::ExtensionSide>) -> Self {
        // SAFETY: the caller guarantees the pointer type, as usual.
        Self(unsafe { raw.cast() })
    }
}

/// `CLAP_PRESET_DISCOVERY_LOCATION_FILE` (a plain file path).
const PRESET_LOCATION_FILE: u32 = 0;

impl PluginPresetLoad {
    /// Ask the plugin to load a native preset file. `load_key`
    /// selects a preset inside a container file (`None` for plain
    /// files). Main thread only, per the CLAP spec. On success the
    /// plugin is expected to call the host's `loaded()` callback; the
    /// host should then rescan parameter values (those rescans are
    /// *not* recorded as automation, per `CLAP_PARAM_RESCAN_VALUES`).
    pub fn from_location(
        &self,
        plugin: &PluginMainThreadHandle,
        location: &str,
        load_key: Option<&str>,
    ) -> Result<(), String> {
        let from_location = plugin
            .use_extension(&self.0)
            .from_location
            .ok_or_else(|| "plugin preset-load extension has no from_location".to_string())?;
        let location_c = CString::new(location).map_err(|e| format!("bad preset path: {e}"))?;
        let load_key_c: Option<CString> = load_key
            .map(CString::new)
            .transpose()
            .map_err(|e| format!("bad load_key: {e}"))?;
        // SAFETY: the extension struct was obtained from the plugin via
        // clack's checked `get_extension`; the C strings outlive the call.
        let ok = unsafe {
            from_location(
                plugin.as_raw(),
                PRESET_LOCATION_FILE,
                location_c.as_ptr(),
                load_key_c
                    .as_ref()
                    .map(|c| c.as_ptr())
                    .unwrap_or(std::ptr::null()),
            )
        };
        if ok {
            Ok(())
        } else {
            Err(format!("plugin refused to load preset '{location}'"))
        }
    }
}

pub(crate) struct PulsegridHost;

impl HostHandlers for PulsegridHost {
    type Shared<'a> = PulsegridShared;
    type MainThread<'a> = PulsegridMainThread;
    type AudioProcessor<'a> = ();

    fn declare_extensions(builder: &mut HostExtensions<Self>, _shared: &Self::Shared<'_>) {
        use clack_extensions::latency::HostLatency;
        use clack_extensions::state::HostState;
        builder.register::<HostState>();
        builder.register::<HostLatency>();
        builder.register::<HostPresetLoad>();
    }
}

fn host_info() -> Result<HostInfo, String> {
    HostInfo::new(
        "Pulsegrid",
        "Pulsegrid",
        "https://pulsegrid.example",
        "0.5.0",
    )
    .map_err(|e| format!("bad host info: {:?}", e))
}

// ---------------------------------------------------------------------------
// Plugin state blobs (CLAP state extension)
// ---------------------------------------------------------------------------

/// Identifies one plugin slot in the arrangement, for the control-thread
/// instance registry. Effect indices and layer indices are positional
/// within their track, matching the project's track order.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
pub enum PluginSlotKey {
    /// Track `track`, insert-effect `index`.
    Fx { track: usize, index: usize },
    /// Track `track`, generator layer `layer`.
    Instrument { track: usize, layer: usize },
}

/// Sink for main-thread plugin instances created during graph builds.
/// The audio graph cannot own them (`PluginInstance` is `!Send`); the
/// control thread collects them into `Engine::plugin_instances` for
/// state saving.
///
/// The sink also carries the shared host-event sinks: every instance
/// created during the build (audio-graph instance and registry
/// instance alike) reports `mark_dirty()` and preset-load `loaded()`
/// callbacks into the same `PluginEventSinks`, which the `Engine`
/// owns long-term and drains on the control thread. This is how a
/// plugin's "my non-parameter state changed, save me again" and "I
/// loaded this native preset" reach the project dirty flag and the
/// preset browser sync without the audio thread being involved.
pub struct PluginInstanceSink {
    /// `(slot, main-thread instance)` pairs for the control-thread
    /// registry (`Engine::plugin_instances`).
    pub entries: Vec<(PluginSlotKey, PluginInstance<PulsegridHost>)>,
    /// Shared host-event sinks (dirty + preset-load), cloned into
    /// every instance's `PulsegridMainThread` handler.
    pub events: PluginEventSinks,
}

impl PluginInstanceSink {
    pub fn new(events: PluginEventSinks) -> Self {
        PluginInstanceSink {
            entries: Vec::new(),
            events,
        }
    }

    /// Throwaway sink for tests (fresh event sinks, dropped).
    pub fn for_tests() -> Self {
        PluginInstanceSink::new(PluginEventSinks::default())
    }

    /// Record one built instance under its slot (mirrors the old
    /// `Vec::push` call sites).
    pub fn push(&mut self, key: PluginSlotKey, instance: PluginInstance<PulsegridHost>) {
        self.entries.push((key, instance));
    }
}

/// A preset-load completion reported by a plugin via the host-side
/// `loaded()` callback of `CLAP_EXT_PRESET_LOAD`. This is the browser
/// synchronization hook: the host learns which native preset the
/// plugin now has loaded.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PresetLoadedEvent {
    /// Which slot's plugin reported the load (None for transient
    /// instances such as the plugin picker validator).
    pub slot: Option<PluginSlotKey>,
    /// `location_kind` from the preset-load call (0 = file).
    pub location_kind: u32,
    /// Location string passed to `from_location`.
    pub location: String,
    /// `load_key` for presets inside container files (None for plain
    /// file presets).
    pub load_key: Option<String>,
}

/// A preset-load failure reported via the host-side `on_error()`
/// callback of `CLAP_EXT_PRESET_LOAD`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PresetLoadError {
    pub slot: Option<PluginSlotKey>,
    pub location: String,
    pub os_error: i32,
    pub message: String,
}

/// Host-side event sinks shared by every plugin instance. `Engine`
/// owns one long-term; each graph build clones it into its sink so
/// instances created by any build report into the same place. Drained
/// on the control thread (`take_plugin_dirty_slots`,
/// `take_preset_events`).
#[derive(Clone, Default)]
pub struct PluginEventSinks {
    /// Slots whose plugin called `mark_dirty()` since the last drain.
    /// Per CLAP, a parameter-value change is implicitly dirty; this
    /// sink specifically captures *non-parameter* state changes the
    /// host cannot otherwise observe.
    pub dirty: std::sync::Arc<std::sync::Mutex<Vec<PluginSlotKey>>>,
    /// Preset-load completions (`loaded()` callbacks).
    pub preset_loaded: std::sync::Arc<std::sync::Mutex<Vec<PresetLoadedEvent>>>,
    /// Preset-load failures (`on_error()` callbacks).
    pub preset_errors: std::sync::Arc<std::sync::Mutex<Vec<PresetLoadError>>>,
    /// Slots whose plugin called the thread-safe `request_restart()`
    /// core callback: the plugin needs deactivation/reactivation so a
    /// structural change — new latency, audio ports, reallocated
    /// buffers — can apply. Drained on the control thread, which
    /// performs the restart.
    pub restart_requested: std::sync::Arc<std::sync::Mutex<Vec<PluginSlotKey>>>,
    /// Slots whose plugin called `latency.changed()` (main-thread):
    /// the reported latency changed and should be re-queried so PDC
    /// can be recalculated. Per CLAP, an *active* plugin should call
    /// `request_restart()` first; the control thread treats a bare
    /// `changed()` as a restart request too, so the re-query happens
    /// after re-activation (the only lifecycle point where the new
    /// latency is valid).
    pub latency_changed: std::sync::Arc<std::sync::Mutex<Vec<PluginSlotKey>>>,
}

impl PluginEventSinks {
    /// Record a dirty slot (deduplicated; the drain clears it).
    pub fn mark_dirty_slot(&self, slot: PluginSlotKey) {
        if let Ok(mut d) = self.dirty.lock() {
            if !d.contains(&slot) {
                d.push(slot);
            }
        }
    }

    /// Record a restart request (deduplicated; the drain clears it).
    pub fn request_restart_slot(&self, slot: PluginSlotKey) {
        if let Ok(mut d) = self.restart_requested.lock() {
            if !d.contains(&slot) {
                d.push(slot);
            }
        }
    }

    /// Record a latency-change notification (deduplicated).
    pub fn report_latency_changed(&self, slot: PluginSlotKey) {
        if let Ok(mut d) = self.latency_changed.lock() {
            if !d.contains(&slot) {
                d.push(slot);
            }
        }
    }
}

/// Explicit plugin lifecycle, mirroring CLAP's state machine
/// (create/init -> activate -> start_processing -> process ->
/// stop_processing -> deactivate -> destroy).
///
/// Pulsegrid collapses create+init into `load()`, and teardown
/// (stop/deactivate/destroy) into drop -- the started processor keeps
/// the plugin alive and the host-side handle is released without
/// calling back into the plugin (documented leak semantics, safer
/// than tearing down audio state from the wrong thread). The states
/// below are the ones Pulsegrid actually walks through; tracking them
/// explicitly (rather than inferring liveness) is what makes illegal
/// transitions fail loudly in debug builds.
///
/// ```text
/// Created --activate--> Active --start_processing--> Processing
/// ```
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum PluginLifecycle {
    /// Instantiated (CLAP create + init done), not yet activated.
    Created,
    /// Activated with an audio configuration; not processing.
    Active,
    /// `start_processing` done: `process()` may be called, but only on
    /// the audio thread.
    Processing,
}

/// Try to restore a plugin's opaque state blob via the CLAP state
/// extension. Returns true when the blob was accepted, in which case it
/// is authoritative and the host must NOT apply parameter values on top
/// (the blob already contains them, plus non-parameter state).
///
/// The blob is treated as fully opaque: the host never inspects it.
/// A missing extension, empty bytes, or a failed load all mean "fall
/// back to parameters".
fn try_load_state(instance: &mut PluginInstance<PulsegridHost>, state: Option<&[u8]>) -> bool {
    let bytes = match state {
        Some(b) if !b.is_empty() => b,
        _ => return false,
    };
    let handle = instance.plugin_handle();
    let ext: Option<PluginState> = handle.get_extension();
    match ext {
        Some(ext) => {
            let mut cursor = std::io::Cursor::new(bytes);
            ext.load(&handle, &mut cursor).is_ok()
        }
        None => false,
    }
}

/// Save a plugin's opaque state blob via the CLAP state extension.
/// Main thread only (never call from the audio thread).
pub fn save_plugin_state(instance: &mut PluginInstance<PulsegridHost>) -> Result<Vec<u8>, String> {
    let handle = instance.plugin_handle();
    let ext: Option<PluginState> = handle.get_extension();
    match ext {
        Some(ext) => {
            let mut buf = Vec::new();
            ext.save(&handle, &mut buf)
                .map_err(|e| format!("state save failed: {}", e))?;
            Ok(buf)
        }
        None => Err("plugin does not support the state extension".to_string()),
    }
}

/// Save a plugin's state with an explicit CLAP state *context*
/// (`CLAP_EXT_STATE_CONTEXT`, stable since CLAP 1.2.0).
///
/// "Save my state differently depending on why you are saving it":
/// `ForPreset` (reusable preset), `ForDuplicate` (new instance),
/// `ForProject` (song storage). A plugin may, for example, skip
/// re-binding external hardware connections when duplicating.
///
/// When the plugin implements the context extension, its contextual
/// save is used; otherwise this falls back to the ordinary state
/// save. Per the spec, a plugin implementing state-context MUST also
/// implement ordinary state, and contextual blobs remain loadable via
/// the ordinary `load()` — so the fallback is always safe.
pub fn save_plugin_state_ctx(
    instance: &mut PluginInstance<PulsegridHost>,
    context: clack_extensions::state_context::StateContextType,
) -> Result<Vec<u8>, String> {
    use clack_extensions::state_context::PluginStateContext;
    let handle = instance.plugin_handle();
    if let Some(ext) = handle.get_extension::<PluginStateContext>() {
        let mut buf = Vec::new();
        ext.save(&handle, &mut buf, context)
            .map_err(|e| format!("state-context save failed: {}", e))?;
        return Ok(buf);
    }
    save_plugin_state(instance)
}

/// Best-effort `params.flush()` of `params` into a not-yet-activated
/// instance's main thread, so `get_value()` (and anything derived from
/// it, like the latency report) reflects the project's values before
/// activation. Silent no-op when the plugin has no params extension
/// or is already active.
fn flush_params_into_main_thread(
    instance: &mut PluginInstance<PulsegridHost>,
    params: &[(u32, f64)],
) {
    let handle = instance.plugin_handle();
    let Some(params_ext): Option<PluginParams> = handle.get_extension() else {
        return;
    };
    let Some(mut inactive) = instance.inactive_plugin_handle() else {
        return;
    };
    let mut in_events = EventBuffer::with_capacity(params.len().max(1));
    for (id, value) in params {
        if let Some(clap_id) = ClapId::from_raw(*id) {
            let ev = ParamValueEvent::new(0, clap_id, Pckn::match_all(), *value);
            in_events.push(&ev);
        }
    }
    let mut out_events = EventBuffer::with_capacity(8);
    params_ext.flush(
        &mut inactive,
        &in_events.as_input(),
        &mut out_events.as_output(),
    );
}

/// Try to restore a plugin's opaque state blob with an explicit state
/// context, falling back to the ordinary state load. Returns true when
/// the blob was accepted (authoritative over params, as with
/// `try_load_state`).
fn try_load_state_ctx(
    instance: &mut PluginInstance<PulsegridHost>,
    state: Option<&[u8]>,
    context: clack_extensions::state_context::StateContextType,
) -> bool {
    use clack_extensions::state_context::PluginStateContext;
    let bytes = match state {
        Some(b) if !b.is_empty() => b,
        _ => return false,
    };
    let handle = instance.plugin_handle();
    if let Some(ext) = handle.get_extension::<PluginStateContext>() {
        let mut cursor = std::io::Cursor::new(bytes);
        if ext.load(&handle, &mut cursor, context).is_ok() {
            return true;
        }
        // Contextual load failed: fall through to the ordinary load,
        // which the spec declares compatible.
    }
    try_load_state(instance, state)
}

/// Minimal Base64 encoder (RFC 4648, standard alphabet). The project
/// format is JSON text, so opaque state blobs are stored as Base64
/// strings — a host serialization decision, not a CLAP requirement.
/// Hand-rolled to avoid adding a dependency for one call site.
pub fn base64_encode(bytes: &[u8]) -> String {
    const ALPHABET: &[u8; 64] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let mut out = String::with_capacity((bytes.len() + 2) / 3 * 4);
    for chunk in bytes.chunks(3) {
        let mut n: u32 = 0;
        for (i, &b) in chunk.iter().enumerate() {
            n |= (b as u32) << (16 - 8 * i);
        }
        let pad = 3 - chunk.len();
        for i in 0..4 - pad {
            out.push(ALPHABET[((n >> (18 - 6 * i)) & 63) as usize] as char);
        }
        for _ in 0..pad {
            out.push('=');
        }
    }
    out
}

/// Base64 decoder matching `base64_encode`. Returns an error string on
/// invalid input (corrupt project data must not panic the engine).
pub fn base64_decode(s: &str) -> Result<Vec<u8>, String> {
    fn val(c: u8) -> Result<u32, String> {
        match c {
            b'A'..=b'Z' => Ok((c - b'A') as u32),
            b'a'..=b'z' => Ok((c - b'a' + 26) as u32),
            b'0'..=b'9' => Ok((c - b'0' + 52) as u32),
            b'+' => Ok(62),
            b'/' => Ok(63),
            _ => Err(format!("invalid base64 character: {}", c as char)),
        }
    }
    let bytes: Vec<u8> = s.bytes().filter(|&b| b != b'\r' && b != b'\n').collect();
    if bytes.len() % 4 != 0 {
        return Err("base64 length must be a multiple of 4".to_string());
    }
    let mut out = Vec::with_capacity(bytes.len() / 4 * 3);
    for chunk in bytes.chunks(4) {
        let pad = chunk.iter().rev().take_while(|&&b| b == b'=').count();
        if pad > 2 {
            return Err("invalid base64 padding".to_string());
        }
        let mut n: u32 = 0;
        for (i, &b) in chunk.iter().enumerate() {
            if b == b'=' {
                if i < 4 - pad {
                    return Err("misplaced base64 padding".to_string());
                }
                continue;
            }
            n |= val(b)? << (18 - 6 * i);
        }
        out.push((n >> 16) as u8);
        if pad < 2 {
            out.push((n >> 8) as u8);
        }
        if pad < 1 {
            out.push(n as u8);
        }
    }
    Ok(out)
}

// ---------------------------------------------------------------------------
// Scanning
// ---------------------------------------------------------------------------

/// One discovered CLAP audio-effect plugin.
#[derive(Clone, Debug)]
pub struct ClapPluginInfo {
    pub path: PathBuf,
    pub id: String,
    pub name: String,
    pub vendor: String,
}

/// Directories searched for CLAP plugins: `$CLAP_PATH`, `~/.clap`,
/// `/usr/lib/clap`.
pub fn clap_search_paths() -> Vec<PathBuf> {
    let mut dirs = Vec::new();
    if let Ok(env) = std::env::var("CLAP_PATH") {
        dirs.extend(std::env::split_paths(&env));
    }
    if let Some(home) = std::env::var_os("HOME") {
        dirs.push(PathBuf::from(home).join(".clap"));
    }
    dirs.push(PathBuf::from("/usr/lib/clap"));
    dirs
}

fn is_clap_library(path: &Path) -> bool {
    match path.extension().and_then(|e| e.to_str()) {
        Some("clap") => true,
        Some("so") | Some("dll") | Some("dylib") => true,
        _ => false,
    }
}

/// Load one bundle file and list its plugins matching `feature`
/// (b"audio-effect" or b"instrument").
fn scan_bundle(path: &Path, feature: &[u8]) -> Vec<ClapPluginInfo> {
    let mut out = Vec::new();
    // SAFETY: loading a plugin bundle is inherently unsafe; a broken
    // bundle can only fail its own load, which we contain per file.
    let entry = match unsafe { PluginEntry::load(path) } {
        Ok(e) => e,
        Err(_) => return out,
    };
    let factory = match entry.get_plugin_factory() {
        Some(f) => f,
        None => return out,
    };
    for desc in factory.plugin_descriptors() {
        if !desc.features().any(|f| f.to_bytes() == feature) {
            continue;
        }
        let (Some(id), Some(name)) = (desc.id(), desc.name()) else {
            continue;
        };
        out.push(ClapPluginInfo {
            path: path.to_path_buf(),
            id: id.to_string_lossy().into_owned(),
            name: name.to_string_lossy().into_owned(),
            vendor: desc
                .vendor()
                .map(|v| v.to_string_lossy().into_owned())
                .unwrap_or_default(),
        });
    }
    out
}

/// Scan all search paths for CLAP plugins with the given feature.
/// A crashing or unreadable bundle only removes itself, never the scan.
fn scan_clap_feature(feature: &[u8]) -> Vec<ClapPluginInfo> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();
    for dir in clap_search_paths() {
        let entries = match std::fs::read_dir(&dir) {
            Ok(e) => e,
            Err(_) => continue,
        };
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                // Some hosts ship `.clap` folders containing the library.
                let inner = match std::fs::read_dir(&path) {
                    Ok(e) => e,
                    Err(_) => continue,
                };
                for sub in inner.flatten() {
                    let sub = sub.path();
                    if is_clap_library(&sub) {
                        for info in scan_bundle(&sub, feature) {
                            if seen.insert((info.id.clone(), info.path.clone())) {
                                out.push(info);
                            }
                        }
                    }
                }
            } else if is_clap_library(&path) {
                for info in scan_bundle(&path, feature) {
                    if seen.insert((info.id.clone(), info.path.clone())) {
                        out.push(info);
                    }
                }
            }
        }
    }
    out.sort_by(|a, b| {
        a.name
            .to_lowercase()
            .cmp(&b.name.to_lowercase())
            .then(a.id.cmp(&b.id))
    });
    out
}

/// Scan all search paths for CLAP audio-effect plugins. A crashing or
/// unreadable bundle only removes itself, never the scan.
pub fn scan_clap_plugins() -> Vec<ClapPluginInfo> {
    scan_clap_feature(b"audio-effect")
}

/// Scan all search paths for CLAP instrument plugins.
pub fn scan_clap_instruments() -> Vec<ClapPluginInfo> {
    scan_clap_feature(b"instrument")
}

/// Find the library path for a plugin id (first match in scan order).
pub fn find_plugin_path(plugin_id: &str) -> Option<PathBuf> {
    scan_clap_plugins()
        .into_iter()
        .find(|p| p.id == plugin_id)
        .map(|p| p.path)
}

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

/// One plugin parameter, as the generic editor needs it.
#[derive(Clone, Debug)]
pub struct ClapParamInfo {
    pub id: u32,
    pub name: String,
    pub min: f64,
    pub max: f64,
    pub default: f64,
}

fn cstr(s: &str) -> Result<CString, String> {
    CString::new(s).map_err(|_| format!("plugin id {:?} has an interior NUL", s))
}

fn param_infos_of(
    instance: &mut PluginInstance<PulsegridHost>,
) -> Result<Vec<ClapParamInfo>, String> {
    let handle = instance.plugin_handle();
    let params: PluginParams = handle
        .get_extension()
        .ok_or_else(|| "plugin does not expose the params extension".to_string())?;
    let count = params.count(&handle);
    let mut out = Vec::with_capacity(count as usize);
    for i in 0..count {
        let mut buf = ParamInfoBuffer::new();
        let Some(info) = params.get_info(&handle, i, &mut buf) else {
            continue;
        };
        out.push(ClapParamInfo {
            id: info.id.get(),
            name: String::from_utf8_lossy(info.name).into_owned(),
            min: info.min_value,
            max: info.max_value,
            default: info.default_value,
        });
    }
    Ok(out)
}

/// Read a main-thread instance's current parameter values as
/// `(clap_id, value)` pairs. Used after preset loads to rescan values
/// into the project. Per CLAP (`CLAP_PARAM_RESCAN_VALUES`), rescanned
/// values are NOT recorded as automation — the caller must treat them
/// as a state sync, not an automation edit.
pub fn plugin_param_values(
    instance: &mut PluginInstance<PulsegridHost>,
) -> Result<Vec<(u32, f64)>, String> {
    let handle = instance.plugin_handle();
    let params: PluginParams = handle
        .get_extension()
        .ok_or_else(|| "plugin does not expose the params extension".to_string())?;
    let count = params.count(&handle);
    let mut out = Vec::with_capacity(count as usize);
    for i in 0..count {
        let mut buf = ParamInfoBuffer::new();
        let Some(info) = params.get_info(&handle, i, &mut buf) else {
            continue;
        };
        if let Some(v) = params.get_value(&handle, info.id) {
            out.push((info.id.get(), v));
        }
    }
    Ok(out)
}

/// Instantiate a plugin just long enough to enumerate its parameters.
/// The instance is dropped on return (its library stays loaded).
pub fn clap_plugin_params(path: &Path, plugin_id: &str) -> Result<Vec<ClapParamInfo>, String> {
    let entry = unsafe { PluginEntry::load(path) }
        .map_err(|e| format!("cannot load {}: {:?}", path.display(), e))?;
    let id = cstr(plugin_id)?;
    let main_thread = PulsegridMainThread::new(None, PluginEventSinks::default());
    let mut instance: PluginInstance<PulsegridHost> = PluginInstance::new(
        |_| PulsegridShared::new(None, PluginEventSinks::default()),
        |_| main_thread,
        &entry,
        id.as_c_str(),
        &host_info()?,
    )
    .map_err(|e| format!("cannot instantiate '{}': {:?}", plugin_id, e))?;
    param_infos_of(&mut instance)
}

// ---------------------------------------------------------------------------
// Hosted plugin instance (lives in the audio graph)
// ---------------------------------------------------------------------------

/// A running CLAP effect: the started audio processor (Send, used on the
/// audio thread) plus scratch buffers and queued parameter changes.
pub struct HostedClapPlugin {
    processor: StartedPluginAudioProcessor<PulsegridHost>,
    /// (param id, value) changes queued since the last block; flushed as
    /// CLAP param-value events with the next `process()` call. Only
    /// touched on the audio thread.
    pending: Vec<(u32, f64)>,
    in_bufs: [Vec<f32>; 2],
    out_bufs: [Vec<f32>; 2],
    in_events: EventBuffer,
    out_events: EventBuffer,
    /// Reported latency in samples (CLAP latency extension, 0 if unsupported).
    /// Used for Plugin Delay Compensation.
    latency: u32,
    /// Explicit lifecycle state (Created -> Active -> Processing).
    /// `process_block` debug-asserts Processing.
    lifecycle: PluginLifecycle,
}

// The processor is Send; everything else here is plain data.
unsafe impl Send for HostedClapPlugin {}

impl HostedClapPlugin {
    /// Load, instantiate, activate and start a plugin. `params` are
    /// applied as initial values. `state` is an opaque CLAP state blob
    /// (from a project save); when present and accepted by the plugin it
    /// is authoritative and `params` are skipped. Rejects plugins without
    /// a stereo in/out pair.
    ///
    /// Returns the started processor (Send, lives in the audio graph)
    /// plus the main-thread instance (`!Send`, kept by the control thread
    /// for state saving).
    pub fn load(
        path: &Path,
        plugin_id: &str,
        sample_rate: u32,
        params: &[(u32, f64)],
        state: Option<&[u8]>,
        slot: Option<PluginSlotKey>,
        events: &PluginEventSinks,
    ) -> Result<(Self, PluginInstance<PulsegridHost>), String> {
        let entry = unsafe { PluginEntry::load(path) }
            .map_err(|e| format!("cannot load {}: {:?}", path.display(), e))?;
        let id_c = cstr(plugin_id)?;
        let main_thread = PulsegridMainThread::new(slot, events.clone());
        let shared = PulsegridShared::new(slot, events.clone());
        let mut instance: PluginInstance<PulsegridHost> = PluginInstance::new(
            |_| shared,
            |_| main_thread,
            &entry,
            id_c.as_c_str(),
            &host_info()?,
        )
        .map_err(|e| format!("cannot instantiate '{}': {:?}", plugin_id, e))?;
        // Lifecycle: Created (instantiation done).
        let mut lifecycle = PluginLifecycle::Created;

        // Restore the opaque state blob before activation when the
        // plugin accepts it (authoritative: includes params).
        // Project load uses the FOR_PROJECT state context when the
        // plugin implements it ("load my state as part of this song"),
        // falling back to the ordinary state load.
        let state_loaded = try_load_state_ctx(
            &mut instance,
            state,
            clack_extensions::state_context::StateContextType::ForProject,
        );

        // No blob: the project's parameter values are authoritative.
        // Flush them into the main-thread instance before activation so
        // its view (get_value, latency report, state save) is coherent
        // with the activation state. Without this, a structural
        // parameter (e.g. the test plugin's Lookahead) would read back
        // its default until the first audio block applied the queued
        // event — and that first application would look like a runtime
        // change, spuriously requesting a restart on every fresh load.
        if !state_loaded {
            flush_params_into_main_thread(&mut instance, params);
        }

        // Require a stereo input and a stereo output port. Audio-port
        // descriptors are valid pre-activation; latency is NOT queried
        // here on purpose (see below).
        {
            let handle = instance.plugin_handle();
            let ports: PluginAudioPorts = handle
                .get_extension()
                .ok_or_else(|| format!("plugin '{}' does not expose audio ports", plugin_id))?;
            let mut port_buf = AudioPortInfoBuffer::new();
            for is_input in [true, false] {
                if ports.count(&handle, is_input) < 1 {
                    return Err(format!(
                        "plugin '{}' has no {} audio ports",
                        plugin_id,
                        if is_input { "input" } else { "output" }
                    ));
                }
                let info = ports
                    .get(&handle, 0, is_input, &mut port_buf)
                    .ok_or_else(|| format!("plugin '{}': cannot read audio port", plugin_id))?;
                if info.channel_count != 2 {
                    return Err(format!(
                        "plugin '{}': only stereo in/out is supported (found {} channels)",
                        plugin_id, info.channel_count
                    ));
                }
            }
        }

        let config = PluginAudioConfiguration {
            sample_rate: sample_rate as f64,
            min_frames_count: 1,
            max_frames_count: crate::graph::MAX_BLOCK_FRAMES as u32,
        };
        let stopped = instance
            .activate(|_, _| (), config)
            .map_err(|e| format!("cannot activate '{}': {:?}", plugin_id, e))?;
        // Lifecycle: Created -> Active.
        debug_assert_eq!(lifecycle, PluginLifecycle::Created);
        lifecycle = PluginLifecycle::Active;
        // CLAP 1.2.2+: the plugin's latency may only be queried while
        // it is activated — before activation it may not know the
        // sample rate yet, so an early query can return garbage (this
        // caused a real clap-wrapper compatibility issue fixed by
        // "check active state before querying latency").
        let reported_latency: u32 = {
            let handle = instance.plugin_handle();
            handle
                .get_extension::<PluginLatency>()
                .map(|ext| ext.get(&handle))
                .unwrap_or(0)
        };
        let processor = stopped
            .start_processing()
            .map_err(|e| format!("cannot start '{}': {:?}", plugin_id, e))?;
        // Lifecycle: Active -> Processing.
        debug_assert_eq!(lifecycle, PluginLifecycle::Active);
        lifecycle = PluginLifecycle::Processing;
        // The main-thread instance is returned to the caller: the control
        // thread keeps it in a registry for state saving (`!Send`, so it
        // can never live in the audio graph). Dropping it only releases
        // the host-side handle; the started processor keeps the plugin
        // alive (same leak semantics as before, now explicit).

        let mut plugin = HostedClapPlugin {
            processor,
            pending: Vec::new(),
            in_bufs: [Vec::new(), Vec::new()],
            out_bufs: [Vec::new(), Vec::new()],
            in_events: EventBuffer::with_capacity(64),
            out_events: EventBuffer::with_capacity(8),
            latency: reported_latency,
            lifecycle,
        };
        if !state_loaded {
            for (id, value) in params {
                plugin.queue_param(*id, *value);
            }
        }
        Ok((plugin, instance))
    }

    /// Queue a parameter change for the next audio block. Audio thread
    /// only (called from `update_params` and automation).
    pub fn queue_param(&mut self, id: u32, value: f64) {
        if let Some(slot) = self.pending.iter_mut().find(|(pid, _)| *pid == id) {
            slot.1 = value;
        } else {
            self.pending.push((id, value));
        }
    }

    /// Run one stereo block through the plugin (in place on `buf`).
    /// Plugin Delay Compensation: query the CLAP latency extension.
    /// Returns 0 if the plugin doesn't support it.
    pub fn latency_samples(&self) -> u32 {
        self.latency
    }

    /// Current lifecycle state (for tests and debug assertions).
    pub fn lifecycle(&self) -> PluginLifecycle {
        self.lifecycle
    }

    pub fn process_block(&mut self, buf: &mut [f32]) {
        debug_assert_eq!(
            self.lifecycle,
            PluginLifecycle::Processing,
            "process_block called on a plugin that is not Processing"
        );
        let frames = buf.len() / 2;
        if frames == 0 {
            return;
        }
        for ch in 0..2 {
            self.in_bufs[ch].resize(frames, 0.0);
            self.out_bufs[ch].resize(frames, 0.0);
            for (i, s) in self.in_bufs[ch].iter_mut().enumerate() {
                *s = buf[2 * i + ch];
            }
        }

        // Param changes become input events for this block.
        self.in_events.clear();
        for (id, value) in self.pending.drain(..) {
            if let Some(clap_id) = ClapId::from_raw(id) {
                let ev = ParamValueEvent::new(0, clap_id, Pckn::match_all(), value);
                self.in_events.push(&ev);
            }
        }
        self.out_events.clear();

        let mut in_ports = AudioPorts::with_capacity(2, 1);
        let mut out_ports = AudioPorts::with_capacity(2, 1);
        let inputs = in_ports.with_input_buffers([AudioPortBuffer {
            latency: 0,
            channels: AudioPortBufferType::f32_input_only(
                self.in_bufs
                    .iter_mut()
                    .map(|b| InputChannel::variable(b.as_mut_slice())),
            ),
        }]);
        let mut outputs = out_ports.with_output_buffers([AudioPortBuffer {
            latency: 0,
            channels: AudioPortBufferType::f32_output_only(
                self.out_bufs.iter_mut().map(|b| b.as_mut_slice()),
            ),
        }]);

        let status = self.processor.process(
            &inputs,
            &mut outputs,
            &self.in_events.as_input(),
            &mut self.out_events.as_output(),
            None,
            None,
        );
        if status.is_err() {
            // A failing plugin must not take down the graph: pass audio
            // through unchanged.
            return;
        }

        for ch in 0..2 {
            for (i, s) in self.out_bufs[ch].iter().enumerate() {
                buf[2 * i + ch] = *s;
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Hosted instrument (generator) instance
// ---------------------------------------------------------------------------

/// A note event for an instrument: on/off with timing, key, velocity.
#[derive(Clone, Copy, Debug)]
pub enum InstrumentNoteEvent {
    On {
        /// Offset in frames from the block start.
        offset: u32,
        key: u8,
        velocity: f64,
        note_id: i32,
    },
    Off {
        offset: u32,
        key: u8,
        velocity: f64,
        note_id: i32,
    },
    /// Cut a voice short immediately (loop wrap / transport stop), bypassing
    /// the release phase. Sent as CLAP_EVENT_NOTE_CHOKE.
    Choke {
        /// Offset in frames from the block start.
        offset: u32,
        key: u8,
        note_id: i32,
    },
}

/// A running CLAP instrument: the started audio processor (Send, used on
/// the audio thread) plus scratch buffers. Takes note events, renders
/// stereo audio. No audio input (generators are sound sources).
pub struct HostedClapInstrument {
    processor: StartedPluginAudioProcessor<PulsegridHost>,
    pending: Vec<(u32, f64)>,
    in_bufs: [Vec<f32>; 2],
    out_bufs: [Vec<f32>; 2],
    in_events: EventBuffer,
    out_events: EventBuffer,
    /// Whether the plugin has an audio input port (most instruments don't).
    has_input: bool,
    /// Explicit lifecycle state (Created -> Active -> Processing).
    lifecycle: PluginLifecycle,
}

// The processor is Send; everything else here is plain data.
unsafe impl Send for HostedClapInstrument {}

impl HostedClapInstrument {
    /// Load, instantiate, activate and start an instrument plugin.
    /// `params` are applied as initial values; `state` is an opaque CLAP
    /// state blob that, when accepted, is authoritative (params skipped).
    /// Requires a stereo output. Returns the started processor plus the
    /// main-thread instance (kept by the control thread for state saving).
    pub fn load(
        path: &Path,
        plugin_id: &str,
        sample_rate: u32,
        params: &[(u32, f64)],
        state: Option<&[u8]>,
        slot: Option<PluginSlotKey>,
        events: &PluginEventSinks,
    ) -> Result<(Self, PluginInstance<PulsegridHost>), String> {
        let entry = unsafe { PluginEntry::load(path) }
            .map_err(|e| format!("cannot load {}: {:?}", path.display(), e))?;
        let id_c = cstr(plugin_id)?;
        let main_thread = PulsegridMainThread::new(slot, events.clone());
        let shared = PulsegridShared::new(slot, events.clone());
        let mut instance: PluginInstance<PulsegridHost> = PluginInstance::new(
            |_| shared,
            |_| main_thread,
            &entry,
            id_c.as_c_str(),
            &host_info()?,
        )
        .map_err(|e| format!("cannot instantiate '{}': {:?}", plugin_id, e))?;
        // Lifecycle: Created (instantiation done).
        let mut lifecycle = PluginLifecycle::Created;

        // Restore the opaque state blob before activation when the
        // plugin accepts it (authoritative: includes params).
        // Project load uses the FOR_PROJECT state context when the
        // plugin implements it ("load my state as part of this song"),
        // falling back to the ordinary state load.
        let state_loaded = try_load_state_ctx(
            &mut instance,
            state,
            clack_extensions::state_context::StateContextType::ForProject,
        );

        // Require a stereo output port. Input is optional (most
        // instruments have none); remember whether it exists.
        let has_input = {
            let handle = instance.plugin_handle();
            let ports: PluginAudioPorts = handle
                .get_extension()
                .ok_or_else(|| format!("plugin '{}' does not expose audio ports", plugin_id))?;
            let has_input = ports.count(&handle, true) >= 1;
            if ports.count(&handle, false) < 1 {
                return Err(format!("plugin '{}' has no audio output ports", plugin_id));
            }
            let mut port_buf = AudioPortInfoBuffer::new();
            let info = ports
                .get(&handle, 0, false, &mut port_buf)
                .ok_or_else(|| format!("plugin '{}': cannot read audio port", plugin_id))?;
            if info.channel_count != 2 {
                return Err(format!(
                    "plugin '{}': only stereo output is supported (found {} channels)",
                    plugin_id, info.channel_count
                ));
            }
            has_input
        };

        let config = PluginAudioConfiguration {
            sample_rate: sample_rate as f64,
            min_frames_count: 1,
            max_frames_count: crate::graph::MAX_BLOCK_FRAMES as u32,
        };
        let stopped = instance
            .activate(|_, _| (), config)
            .map_err(|e| format!("cannot activate '{}': {:?}", plugin_id, e))?;
        // Lifecycle: Created -> Active.
        debug_assert_eq!(lifecycle, PluginLifecycle::Created);
        lifecycle = PluginLifecycle::Active;
        let processor = stopped
            .start_processing()
            .map_err(|e| format!("cannot start '{}': {:?}", plugin_id, e))?;
        // Lifecycle: Active -> Processing.
        debug_assert_eq!(lifecycle, PluginLifecycle::Active);
        lifecycle = PluginLifecycle::Processing;

        let mut inst = HostedClapInstrument {
            processor,
            pending: Vec::new(),
            in_bufs: [Vec::new(), Vec::new()],
            out_bufs: [Vec::new(), Vec::new()],
            in_events: EventBuffer::with_capacity(128),
            out_events: EventBuffer::with_capacity(8),
            has_input,
            lifecycle,
        };
        if !state_loaded {
            for (id, value) in params {
                inst.queue_param(*id, *value);
            }
        }
        Ok((inst, instance))
    }

    /// Queue a parameter change for the next audio block.
    pub fn queue_param(&mut self, id: u32, value: f64) {
        if let Some(slot) = self.pending.iter_mut().find(|(pid, _)| *pid == id) {
            slot.1 = value;
        } else {
            self.pending.push((id, value));
        }
    }

    /// Render one block: `notes` are note on/off events, `out` receives
    /// interleaved stereo (must hold at least frames*2).
    /// Current lifecycle state (for tests and debug assertions).
    pub fn lifecycle(&self) -> PluginLifecycle {
        self.lifecycle
    }

    pub fn process_notes(&mut self, notes: &[InstrumentNoteEvent], out: &mut [f32]) {
        debug_assert_eq!(
            self.lifecycle,
            PluginLifecycle::Processing,
            "process_notes called on a plugin that is not Processing"
        );
        let frames = out.len() / 2;
        if frames == 0 {
            return;
        }
        for ch in 0..2 {
            self.out_bufs[ch].resize(frames, 0.0);
            self.out_bufs[ch].fill(0.0);
        }

        self.in_events.clear();
        // Note events first.
        for note in notes {
            let pckn = |offset: u32, key: u8, note_id: i32| {
                (offset, Pckn::from_raw(0, 0, key as i16, note_id))
            };
            match *note {
                InstrumentNoteEvent::On {
                    offset,
                    key,
                    velocity,
                    note_id,
                } => {
                    let (offset, pckn) = pckn(offset, key, note_id);
                    let ev = NoteOnEvent::new(offset, pckn, velocity);
                    self.in_events.push(&ev);
                }
                InstrumentNoteEvent::Off {
                    offset,
                    key,
                    velocity,
                    note_id,
                } => {
                    let (offset, pckn) = pckn(offset, key, note_id);
                    let ev = NoteOffEvent::new(offset, pckn, velocity);
                    self.in_events.push(&ev);
                }
                InstrumentNoteEvent::Choke {
                    offset,
                    key,
                    note_id,
                } => {
                    let (offset, pckn) = pckn(offset, key, note_id);
                    let ev = NoteChokeEvent::new(offset, pckn);
                    self.in_events.push(&ev);
                }
            }
        }
        // Then queued param changes.
        for (id, value) in self.pending.drain(..) {
            if let Some(clap_id) = ClapId::from_raw(id) {
                let ev = ParamValueEvent::new(0, clap_id, Pckn::match_all(), value);
                self.in_events.push(&ev);
            }
        }
        self.out_events.clear();

        let mut out_ports = AudioPorts::with_capacity(2, 1);
        let mut outputs = out_ports.with_output_buffers([AudioPortBuffer {
            latency: 0,
            channels: AudioPortBufferType::f32_output_only(
                self.out_bufs.iter_mut().map(|b| b.as_mut_slice()),
            ),
        }]);

        // Provide input buffers only if the plugin declares an input port.
        // Most instruments have none; the silent dummy is ignored anyway.
        let mut in_ports = AudioPorts::with_capacity(2, 1);
        let status = if self.has_input {
            for ch in 0..2 {
                self.in_bufs[ch].resize(frames, 0.0);
                self.in_bufs[ch].fill(0.0);
            }
            let inputs = in_ports.with_input_buffers([AudioPortBuffer {
                latency: 0,
                channels: AudioPortBufferType::f32_input_only(
                    self.in_bufs
                        .iter_mut()
                        .map(|b| InputChannel::variable(b.as_mut_slice())),
                ),
            }]);
            self.processor.process(
                &inputs,
                &mut outputs,
                &self.in_events.as_input(),
                &mut self.out_events.as_output(),
                None,
                None,
            )
        } else {
            // No input ports: pass an empty buffer list.
            let no_inputs: [AudioPortBuffer<
                std::iter::Empty<InputChannel<'_, f32>>,
                std::iter::Empty<InputChannel<'_, f64>>,
            >; 0] = [];
            let inputs = in_ports.with_input_buffers(no_inputs);
            self.processor.process(
                &inputs,
                &mut outputs,
                &self.in_events.as_input(),
                &mut self.out_events.as_output(),
                None,
                None,
            )
        };
        if status.is_err() {
            // A failing plugin must not take down the graph: output silence.
            for s in out.iter_mut() {
                *s = 0.0;
            }
            return;
        }

        for ch in 0..2 {
            for (i, s) in self.out_bufs[ch].iter().enumerate() {
                out[2 * i + ch] = *s;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_plugin_path() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../tests/fixtures/clap/PulsegridTestGain.clap")
    }

    #[test]
    fn scan_finds_test_plugin_via_clap_path() {
        let dir = test_plugin_path().parent().unwrap().to_path_buf();
        std::env::set_var("CLAP_PATH", &dir);
        let found = scan_clap_plugins();
        std::env::remove_var("CLAP_PATH");
        let hit = found
            .iter()
            .find(|p| p.id == "org.pulsegrid.test-gain")
            .expect("test plugin should be found by scan");
        assert_eq!(hit.name, "Pulsegrid Test Gain");
        assert_eq!(hit.path, test_plugin_path());
    }

    #[test]
    fn plugin_params_enumerated() {
        let params = clap_plugin_params(&test_plugin_path(), "org.pulsegrid.test-gain")
            .expect("param enumeration should work");
        assert_eq!(params.len(), 2);
        assert_eq!(params[0].id, 7);
        assert_eq!(params[0].name, "Gain");
        assert_eq!(params[0].min, 0.0);
        assert_eq!(params[0].max, 2.0);
        assert_eq!(params[0].default, 1.0);
        // Structural latency-switch parameter (0/1 stepped).
        assert_eq!(params[1].id, 8);
        assert_eq!(params[1].name, "Lookahead");
        assert_eq!(params[1].default, 0.0);
    }

    #[test]
    fn plugin_processes_audio_with_gain() {
        let path = test_plugin_path();
        let (mut plugin, _instance) = HostedClapPlugin::load(
            &path,
            "org.pulsegrid.test-gain",
            44100,
            &[(7, 2.0)],
            None,
            None,
            &PluginEventSinks::default(),
        )
        .expect("plugin should load");
        // Stereo block of 0.5.
        let mut buf = vec![0.5f32; 256];
        plugin.process_block(&mut buf);
        for s in &buf {
            assert!(
                (*s - 1.0).abs() < 1e-5,
                "gain 2.0 should double 0.5 to 1.0, got {}",
                s
            );
        }
    }

    #[test]
    fn plugin_param_change_takes_effect_next_block() {
        let path = test_plugin_path();
        let (mut plugin, _instance) = HostedClapPlugin::load(
            &path,
            "org.pulsegrid.test-gain",
            44100,
            &[],
            None,
            None,
            &PluginEventSinks::default(),
        )
        .expect("plugin should load");
        // Default gain is 1.0: passthrough.
        let mut buf = vec![0.25f32; 128];
        plugin.process_block(&mut buf);
        assert!((buf[0] - 0.25).abs() < 1e-5);
        // Queue silence; the next block applies it.
        plugin.queue_param(7, 0.0);
        plugin.process_block(&mut buf);
        assert!(
            buf.iter().all(|s| s.abs() < 1e-5),
            "gain 0.0 should silence the block"
        );
    }

    #[test]
    fn missing_plugin_is_a_clear_error() {
        let err = match HostedClapPlugin::load(
            Path::new("/nonexistent/ghost.clap"),
            "org.ghost.nope",
            44100,
            &[],
            None,
            None,
            &PluginEventSinks::default(),
        ) {
            Ok(_) => panic!("should not load"),
            Err(e) => e,
        };
        assert!(err.contains("cannot load"), "got: {}", err);
    }

    /// The state blob must carry state that parameters alone cannot:
    /// the test plugin's save counter is non-parameter state, and a
    /// loaded blob is authoritative over conflicting params.
    #[test]
    fn state_blob_round_trips_opaque_non_param_state() {
        let path = test_plugin_path();
        // Instance A: params say gain 1.0.
        let (mut a, mut a_inst) = HostedClapPlugin::load(
            &path,
            "org.pulsegrid.test-gain",
            44100,
            &[(7, 1.0)],
            None,
            None,
            &PluginEventSinks::default(),
        )
        .expect("plugin should load");
        // Two saves: the non-parameter save counter advances, so the
        // blobs differ even though no parameter changed.
        let blob1 = save_plugin_state(&mut a_inst).expect("state should save");
        let blob2 = save_plugin_state(&mut a_inst).expect("state should save");
        assert_eq!(blob1.len(), 21, "test blob layout is 21 bytes");
        assert_ne!(
            blob1, blob2,
            "save counter (non-param state) must advance between saves"
        );
        // Gain bits identical across both saves (host never parses this;
        // the test only checks the plugin's own layout).
        assert_eq!(&blob1[4..12], &blob2[4..12]);

        // Instance B: params say gain 0.25, but the blob says 1.0.
        // The blob must win (authoritative) — params are skipped.
        let (mut b, mut b_inst) = HostedClapPlugin::load(
            &path,
            "org.pulsegrid.test-gain",
            44100,
            &[(7, 0.25)],
            Some(&blob2),
            None,
            &PluginEventSinks::default(),
        )
        .expect("plugin should load with state");
        let mut buf = vec![0.5f32; 128];
        b.process_block(&mut buf);
        assert!(
            (buf[0] - 0.5).abs() < 1e-5,
            "blob gain 1.0 must beat param 0.25, got {}",
            buf[0]
        );

        // B's next save continues the blob's non-param counter (2 -> 3):
        // the loaded state, not a fresh default, was restored.
        let blob3 = save_plugin_state(&mut b_inst).expect("state should save");
        assert_ne!(blob2, blob3);
        assert_eq!(&blob2[4..12], &blob3[4..12], "gain preserved through load");
        // Counter bytes: blob2 ends with 2, blob3 ends with 3.
        assert_eq!(&blob2[12..20], &2u64.to_le_bytes());
        assert_eq!(&blob3[12..20], &3u64.to_le_bytes());

        // Sanity: A still processes with its param gain.
        let mut buf_a = vec![0.5f32; 128];
        a.process_block(&mut buf_a);
        assert!((buf_a[0] - 0.5).abs() < 1e-5);
    }

    /// `load()` walks the explicit lifecycle Created -> Active ->
    /// Processing, and the finished plugin reports Processing.
    #[test]
    fn plugin_lifecycle_reaches_processing() {
        let path = test_plugin_path();
        let (plugin, _instance) = HostedClapPlugin::load(
            &path,
            "org.pulsegrid.test-gain",
            44100,
            &[],
            None,
            None,
            &PluginEventSinks::default(),
        )
        .expect("plugin should load");
        assert_eq!(plugin.lifecycle(), PluginLifecycle::Processing);
    }

    /// The instrument loader walks the same lifecycle.
    #[test]
    fn instrument_lifecycle_reaches_processing() {
        let synth = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../tests/fixtures/clap/PulsegridTestSynth.clap");
        let (inst, _instance) = HostedClapInstrument::load(
            &synth,
            "org.pulsegrid.test-synth",
            44100,
            &[],
            None,
            None,
            &PluginEventSinks::default(),
        )
        .expect("test synth should load");
        assert_eq!(inst.lifecycle(), PluginLifecycle::Processing);
    }

    /// A corrupt blob must not break loading: params are the fallback.
    #[test]
    fn corrupt_state_blob_falls_back_to_params() {
        let path = test_plugin_path();
        let (mut plugin, _instance) = HostedClapPlugin::load(
            &path,
            "org.pulsegrid.test-gain",
            44100,
            &[(7, 2.0)],
            Some(b"definitely not a valid state blob"),
            None,
            &PluginEventSinks::default(),
        )
        .expect("plugin should load despite bad blob");
        let mut buf = vec![0.5f32; 128];
        plugin.process_block(&mut buf);
        assert!(
            (buf[0] - 1.0).abs() < 1e-5,
            "param fallback gain 2.0 expected, got {}",
            buf[0]
        );
    }

    /// A plugin without the state extension: save errors cleanly, and a
    /// blob is ignored in favor of params. (The test synth has no state
    /// extension.)
    #[test]
    fn plugin_without_state_ext_uses_params_only() {
        let synth = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../tests/fixtures/clap/PulsegridTestSynth.clap");
        let (mut inst, mut main) = HostedClapInstrument::load(
            &synth,
            "org.pulsegrid.test-synth",
            44100,
            &[],
            None,
            None,
            &PluginEventSinks::default(),
        )
        .expect("test synth should load");
        let err = save_plugin_state(&mut main).expect_err("no state ext -> error");
        assert!(err.contains("state extension"), "got: {}", err);
        // A blob passed to a stateless plugin is ignored, not fatal.
        let (mut inst2, _main2) = HostedClapInstrument::load(
            &synth,
            "org.pulsegrid.test-synth",
            44100,
            &[],
            Some(b"opaque bytes the plugin cannot read"),
            None,
            &PluginEventSinks::default(),
        )
        .expect("stateless plugin should load despite blob");
        // Both instances render a note identically.
        for inst in [&mut inst, &mut inst2] {
            let notes = vec![InstrumentNoteEvent::On {
                offset: 0,
                key: 69,
                velocity: 0.8,
                note_id: 1,
            }];
            let mut out = vec![0.0f32; 512 * 2];
            inst.process_notes(&notes, &mut out);
            assert!(
                out.iter().any(|s| s.abs() > 1e-6),
                "synth should render audio"
            );
        }
    }

    // -- CLAP state context / preset load / dirty tracking (v0.35.0) -------

    use clack_extensions::state_context::StateContextType;

    fn ctx_test_instance(
        slot: Option<PluginSlotKey>,
        events: &PluginEventSinks,
    ) -> (HostedClapPlugin, PluginInstance<PulsegridHost>) {
        let path = test_plugin_path();
        HostedClapPlugin::load(
            &path,
            "org.pulsegrid.test-gain",
            44100,
            &[],
            None,
            slot,
            events,
        )
        .expect("test plugin should load")
    }

    #[test]
    fn state_context_save_tags_context() {
        // Each context produces a 25-byte blob whose trailing u32 is the
        // context the host requested (1=preset, 2=duplicate, 3=project).
        let events = PluginEventSinks::default();
        let (_p, mut inst) = ctx_test_instance(None, &events);
        for (ctx, want) in [
            (StateContextType::ForPreset, 1u32),
            (StateContextType::ForDuplicate, 2u32),
            (StateContextType::ForProject, 3u32),
        ] {
            let blob = save_plugin_state_ctx(&mut inst, ctx).expect("context save should work");
            assert_eq!(blob.len(), 25, "context blob is 25 bytes");
            let got = u32::from_le_bytes(blob[21..25].try_into().unwrap());
            assert_eq!(got, want, "context tag mismatch");
        }
        // Ordinary save is still the 21-byte untagged blob.
        let plain = save_plugin_state(&mut inst).expect("plain save");
        assert_eq!(plain.len(), 21);
    }

    #[test]
    fn state_context_load_accepts_plain_blob() {
        // Spec-declared compatibility: a contextual load accepts an
        // ordinary (untagged) blob and vice versa.
        let events = PluginEventSinks::default();
        let (_p, mut inst) = ctx_test_instance(None, &events);
        let plain = save_plugin_state(&mut inst).expect("plain save");
        assert!(try_load_state_ctx(
            &mut inst,
            Some(&plain),
            StateContextType::ForPreset
        ));
        let ctx_blob =
            save_plugin_state_ctx(&mut inst, StateContextType::ForProject).expect("context save");
        assert!(try_load_state(&mut inst, Some(&ctx_blob)));
    }

    #[test]
    fn latency_changed_records_slot() {
        use clack_extensions::latency::HostLatencyImpl;
        let events = PluginEventSinks::default();
        let slot = PluginSlotKey::Fx { track: 0, index: 3 };
        let mt = PulsegridMainThread::new(Some(slot), events.clone());
        // Direct host-callback drive (the plugin calls this through the
        // CLAP ABI; here we drive the same trait method).
        mt.changed();
        mt.changed(); // deduplicated
        let changed = events.latency_changed.lock().unwrap();
        assert_eq!(*changed, vec![slot]);
        // A slot-less (transient) instance reports nowhere.
        let mt_none = PulsegridMainThread::new(None, events.clone());
        mt_none.changed();
        assert_eq!(changed.len(), 1);
    }

    #[test]
    fn request_restart_records_slot() {
        let events = PluginEventSinks::default();
        let slot = PluginSlotKey::Instrument { track: 1, layer: 0 };
        let shared = PulsegridShared::new(Some(slot), events.clone());
        // Direct host-callback drive: this is the thread-safe core
        // callback, so it only records; the control thread restarts.
        shared.request_restart();
        shared.request_restart(); // deduplicated
        let restarts = events.restart_requested.lock().unwrap();
        assert_eq!(*restarts, vec![slot]);
        let shared_none = PulsegridShared::new(None, events.clone());
        shared_none.request_restart();
        assert_eq!(restarts.len(), 1);
    }

    #[test]
    fn structural_param_change_requests_restart_through_abi() {
        // Full round trip through the real CLAP ABI: a structural
        // parameter change (Lookahead off -> on) makes the plugin call
        // host->request_restart(); the host records the slot. The
        // cached latency stays 0 until the host actually restarts.
        let events = PluginEventSinks::default();
        let slot = PluginSlotKey::Fx { track: 0, index: 0 };
        let (mut plugin, _inst) = HostedClapPlugin::load(
            &test_plugin_path(),
            "org.pulsegrid.test-gain",
            44100,
            &[(7, 1.0)],
            None,
            Some(slot),
            &events,
        )
        .expect("plugin should load");
        assert_eq!(plugin.latency_samples(), 0);
        assert!(events.restart_requested.lock().unwrap().is_empty());
        // Structural param change arrives as an audio-block event.
        plugin.queue_param(8, 1.0);
        let mut buf = vec![0.5f32; 256];
        plugin.process_block(&mut buf);
        assert_eq!(*events.restart_requested.lock().unwrap(), vec![slot]);
        // The old instance keeps its old latency: the new value only
        // becomes valid after the host restarts (re-activate +
        // re-query, per CLAP 1.2.2+).
        assert_eq!(plugin.latency_samples(), 0);
    }

    #[test]
    fn mark_dirty_records_slot() {
        use clack_extensions::state::HostStateImpl;
        let events = PluginEventSinks::default();
        let slot = PluginSlotKey::Fx { track: 2, index: 1 };
        let mt = PulsegridMainThread::new(Some(slot), events.clone());
        // Direct host-callback drive (the plugin calls this through the
        // CLAP ABI; here we drive the same trait method).
        mt.mark_dirty();
        mt.mark_dirty(); // deduplicated
        let dirty = events.dirty.lock().unwrap();
        assert_eq!(*dirty, vec![slot]);
        // A slot-less (transient) instance reports nowhere.
        let mt_none = PulsegridMainThread::new(None, events.clone());
        mt_none.mark_dirty();
        assert_eq!(dirty.len(), 1);
    }

    fn preset_tmp_file(name: &str, contents: &str) -> std::path::PathBuf {
        let dir =
            std::env::temp_dir().join(format!("pulsegrid-preset-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join(name);
        std::fs::write(&path, contents).unwrap();
        path
    }

    #[test]
    fn preset_load_from_location_e2e() {
        // Full round trip through the real CLAP ABI: host asks the
        // plugin to load a native preset file; the plugin parses it,
        // calls host loaded() + mark_dirty().
        let preset = preset_tmp_file("gain.preset", "gain=1.5");
        let events = PluginEventSinks::default();
        let slot = PluginSlotKey::Fx { track: 0, index: 0 };
        let (_p, mut inst) = ctx_test_instance(Some(slot), &events);
        let handle = inst.plugin_handle();
        let ext: Option<PluginPresetLoad> = handle.get_extension();
        assert!(ext.is_some(), "test plugin implements preset-load");
        ext.unwrap()
            .from_location(&handle, preset.to_str().unwrap(), None)
            .expect("preset load should succeed");
        // The plugin applied the preset's gain...
        let params = plugin_param_values(&mut inst).expect("param rescan");
        let gain = params.iter().find(|(id, _)| *id == 7).map(|(_, v)| *v);
        assert!(
            gain.map(|g| (g - 1.5).abs() < 1e-9).unwrap_or(false),
            "gain should be 1.5 after preset load, got {:?}",
            gain
        );
        // ...reported loaded() for browser sync...
        let loaded = events.preset_loaded.lock().unwrap();
        assert_eq!(loaded.len(), 1);
        assert_eq!(loaded[0].slot, Some(slot));
        assert!(loaded[0].location.ends_with("gain.preset"));
        assert_eq!(loaded[0].load_key, None);
        drop(loaded);
        // ...and marked its non-parameter state dirty.
        let dirty = events.dirty.lock().unwrap();
        assert_eq!(*dirty, vec![slot]);
    }

    #[test]
    fn preset_load_bad_file_reports_error() {
        let preset = preset_tmp_file("bad.preset", "not a preset");
        let events = PluginEventSinks::default();
        let (_p, mut inst) = ctx_test_instance(None, &events);
        let handle = inst.plugin_handle();
        let ext: Option<PluginPresetLoad> = handle.get_extension();
        let err = ext
            .unwrap()
            .from_location(&handle, preset.to_str().unwrap(), None)
            .expect_err("bad preset should fail");
        assert!(err.contains("refused"), "got: {}", err);
        let errors = events.preset_errors.lock().unwrap();
        assert_eq!(errors.len(), 1, "on_error should have been called");
        assert!(
            errors[0].message.contains("gain="),
            "got: {}",
            errors[0].message
        );
    }
}

// ---------------------------------------------------------------------------
// Native plugin GUIs (floating windows)
// ---------------------------------------------------------------------------

use clack_extensions::gui::{GuiApiType, GuiConfiguration, PluginGui as PluginGuiExt};

/// Preferred GUI APIs per platform, tried in order.
fn gui_api_candidates() -> Vec<GuiApiType<'static>> {
    #[cfg(target_os = "windows")]
    return vec![GuiApiType::WIN32];
    #[cfg(target_os = "macos")]
    return vec![GuiApiType::COCOA];
    #[cfg(not(any(target_os = "windows", target_os = "macos")))]
    return vec![GuiApiType::X11, GuiApiType::WAYLAND];
}

/// A plugin's native GUI as a floating window, owned by the control
/// thread. The plugin manages its own window and event loop; the host
/// only creates, shows, hides and destroys it.
///
/// This is a *GUI-only* instance: it never processes audio. Parameter
/// values are flushed in before showing (so the GUI reflects the
/// project) and can be read back for syncing to the audio instance.
pub struct PluginGui {
    instance: PluginInstance<PulsegridHost>,
    gui: PluginGuiExt,
    param_ids: Vec<u32>,
    shown: bool,
}

impl PluginGui {
    /// Open a plugin's floating GUI. `params` are flushed to the
    /// instance first so the GUI opens with the project's values.
    pub fn open(path: &Path, plugin_id: &str, params: &[(u32, f64)]) -> Result<Self, String> {
        let entry = unsafe { PluginEntry::load(path) }
            .map_err(|e| format!("cannot load {}: {:?}", path.display(), e))?;
        let id_c = cstr(plugin_id)?;
        let main_thread = PulsegridMainThread::new(None, PluginEventSinks::default());
        let mut instance: PluginInstance<PulsegridHost> = PluginInstance::new(
            |_| PulsegridShared::new(None, PluginEventSinks::default()),
            |_| main_thread,
            &entry,
            id_c.as_c_str(),
            &host_info()?,
        )
        .map_err(|e| format!("cannot instantiate '{}': {:?}", plugin_id, e))?;

        // Flush the project's param values into the fresh instance.
        let param_ids: Vec<u32> = {
            let params_ext: PluginParams = instance
                .plugin_handle()
                .get_extension()
                .ok_or_else(|| format!("plugin '{}' does not expose params", plugin_id))?;
            let mut handle = instance
                .inactive_plugin_handle()
                .ok_or_else(|| "plugin is unexpectedly active".to_string())?;
            let mut in_events = EventBuffer::with_capacity(params.len().max(1));
            for (id, value) in params {
                if let Some(clap_id) = ClapId::from_raw(*id) {
                    let ev = ParamValueEvent::new(0, clap_id, Pckn::match_all(), *value);
                    in_events.push(&ev);
                }
            }
            let mut out_events = EventBuffer::with_capacity(8);
            params_ext.flush(
                &mut handle,
                &in_events.as_input(),
                &mut out_events.as_output(),
            );
            // Remember the ids for read-back.
            params.iter().map(|(id, _)| *id).collect()
        };

        // Find a GUI API the plugin supports as a floating window.
        let gui: PluginGuiExt = instance
            .plugin_handle()
            .get_extension()
            .ok_or_else(|| format!("plugin '{}' has no GUI extension", plugin_id))?;
        let config = {
            let handle = instance.plugin_handle();
            // Prefer the plugin's own hint, else try platform APIs.
            let mut chosen: Option<GuiConfiguration> = None;
            if let Some(pref) = gui.get_preferred_api(&handle) {
                if pref.is_floating
                    && gui.is_api_supported(
                        &handle,
                        GuiConfiguration {
                            api_type: pref.api_type,
                            is_floating: true,
                        },
                    )
                {
                    chosen = Some(GuiConfiguration {
                        api_type: pref.api_type,
                        is_floating: true,
                    });
                }
            }
            if chosen.is_none() {
                for api in gui_api_candidates() {
                    let cfg = GuiConfiguration {
                        api_type: api,
                        is_floating: true,
                    };
                    if gui.is_api_supported(&handle, cfg) {
                        chosen = Some(cfg);
                        break;
                    }
                }
            }
            chosen.ok_or_else(|| {
                format!(
                    "plugin '{}' does not support a floating GUI on this platform",
                    plugin_id
                )
            })?
        };

        {
            let handle = instance.plugin_handle();
            gui.create(&handle, config)
                .map_err(|e| format!("plugin '{}': GUI create failed: {:?}", plugin_id, e))?;
            gui.show(&handle)
                .map_err(|e| format!("plugin '{}': GUI show failed: {:?}", plugin_id, e))?;
        }

        Ok(PluginGui {
            instance,
            gui,
            param_ids,
            shown: true,
        })
    }

    /// Read current parameter values from the GUI instance (for syncing
    /// user tweaks back to the project/audio).
    pub fn read_params(&mut self) -> Vec<(u32, f64)> {
        let handle = self.instance.plugin_handle();
        let Some(params_ext): Option<PluginParams> = handle.get_extension() else {
            return Vec::new();
        };
        self.param_ids
            .iter()
            .filter_map(|id| {
                ClapId::from_raw(*id)
                    .and_then(|cid| params_ext.get_value(&handle, cid).map(|v| (*id, v)))
            })
            .collect()
    }

    /// Hide and destroy the GUI. Idempotent.
    pub fn close(&mut self) {
        if self.shown {
            let handle = self.instance.plugin_handle();
            let _ = self.gui.hide(&handle);
            self.gui.destroy(&handle);
            self.shown = false;
        }
    }
}

impl Drop for PluginGui {
    fn drop(&mut self) {
        self.close();
    }
}

#[cfg(test)]
mod instrument_tests {
    use super::*;

    fn test_synth_path() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../tests/fixtures/clap/PulsegridTestSynth.clap")
    }

    #[test]
    fn instrument_renders_note() {
        let (mut inst, _instance) = HostedClapInstrument::load(
            &test_synth_path(),
            "org.pulsegrid.test-synth",
            44100,
            &[],
            None,
            None,
            &PluginEventSinks::default(),
        )
        .expect("test synth should load");
        let notes = vec![InstrumentNoteEvent::On {
            offset: 0,
            key: 69,
            velocity: 0.8,
            note_id: 1,
        }];
        let mut out = vec![0.0f32; 512 * 2];
        inst.process_notes(&notes, &mut out);
        let peak = out.iter().map(|s| s.abs()).fold(0.0f32, f32::max);
        assert!(peak > 0.01, "synth should render audio, peak={}", peak);
    }
}
