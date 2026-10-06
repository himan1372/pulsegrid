//! Pulsegrid audio engine: transport, graph rendering, offline render.
//!
//! Threading model (see docs/ARCHITECTURE.md):
//! * Control thread (Python): owns [`Engine`]. Builds immutable [`Song`]
//!   snapshots (whole arrangements) and publishes them through a lock-free
//!   slot. Never touches per-sample state.
//! * Audio thread (CPAL callback or paced null-sink thread): owns
//!   [`AudioCore`] and the [`Graph`]. Uses `try_read` on the song slot so it
//!   never blocks; on contention it keeps rendering the previous snapshot.
//! * Communication UI -> audio is one-way and bounded: atomics (`playing`,
//!   `reset`, `position`). No locks, no allocation, no I/O, no Python on
//!   the audio path.

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, RwLock};

use crate::backend;
use crate::debug::{SharedDebugState, MAX_DEBUG_TRACKS};
use crate::effects::FxParams;
use crate::graph::{Graph, MAX_BLOCK_FRAMES};
use crate::synth::Instrument;
use crate::timeline::{
    ArrangementData, ChannelData, ClipData, GeneratorLayerParams, GeneratorParams, LayerMode,
    NoteData, PatternData, RawAutoCurve, SendData, Song, TrackData,
};
use crate::wav;

/// Shared transport flags. Written by the control thread, read by audio.
pub(crate) struct Control {
    pub(crate) playing: AtomicBool,
    reset: AtomicBool,
    position: AtomicU64,
    /// Multithreading enabled (FL-style global switch).
    pub(crate) multithreaded: AtomicBool,
    /// Plugin slots whose instances were rebuilt on the audio thread.
    /// The control thread drains this and drops the matching registry
    /// entries (they are stale; the replacements are fresh plugins whose
    /// state is fully described by params).
    pub(crate) pruned_slots: std::sync::Mutex<Vec<crate::plugins::PluginSlotKey>>,
}

impl Control {
    pub(crate) fn new() -> Self {
        Control {
            playing: AtomicBool::new(false),
            reset: AtomicBool::new(false),
            position: AtomicU64::new(0),
            multithreaded: AtomicBool::new(true),
            pruned_slots: std::sync::Mutex::new(Vec::new()),
        }
    }
}

/// Lock-free song slot. The control thread replaces the `Arc<Song>` whenever
/// the arrangement changes; the audio thread picks it up with `try_read`.
type SongSlot = Arc<RwLock<Arc<Song>>>;

/// Per-block audio rendering state. Lives on the audio thread.
pub struct AudioCore {
    song_slot: SongSlot,
    debug: SharedDebugState,
    song: Arc<Song>,
    graph: Graph,
    sample_pos: u64,
}

impl AudioCore {
    pub fn new(
        song_slot: SongSlot,
        initial: Arc<Song>,
        debug: SharedDebugState,
        sink: &mut crate::plugins::PluginInstanceSink,
    ) -> Self {
        let graph = Graph::new(
            initial.sample_rate,
            &initial.track_params,
            &initial.track_fx,
            &initial.track_generator_layers,
            &initial.track_layer_modes,
            &initial.automation,
            &initial.track_sends,
            &initial.track_outputs,
            initial.tempo,
            initial.loop_samples,
            debug.clone(),
            sink,
        );
        AudioCore {
            song_slot,
            debug: debug.clone(),
            song: initial,
            graph,
            sample_pos: 0,
        }
    }

    /// Render one block of stereo-interleaved f32 samples into `out`.
    /// Blocks larger than MAX_BLOCK_FRAMES are chunked internally.
    pub(crate) fn render_block(&mut self, out: &mut [f32], control: &Control) {
        debug_assert!(out.len() % 2 == 0);

        if control.reset.swap(false, Ordering::AcqRel) {
            self.sample_pos = 0;
            self.graph.reset();
        }
        if !control.playing.load(Ordering::Acquire) {
            out.fill(0.0);
            return;
        }

        // Pick up a new arrangement snapshot if the control thread published
        // one. Voices keep ringing; only the event cursors resync so edits
        // apply at the next block without cutting sustained notes.
        //
        // NOTE: plugin instances created here live and die on the audio
        // thread (PluginInstance is !Send). Their main-thread handles
        // are dropped, exactly as before this change. The control-thread
        // registry is told which slots were rebuilt via `pruned` so it
        // never saves stale state: rebuilt plugins are fresh, so params
        // (+ defaults) fully describe them.
        if let Ok(guard) = self.song_slot.try_read() {
            if !Arc::ptr_eq(&guard, &self.song) {
                let new_song = guard.clone();
                // Fresh event sinks: instances built here live and die
                // on the audio thread (their handles are dropped, per
                // the NOTE above), so their mark_dirty/preset reports
                // have no registry to reach. Control-thread builds use
                // the Engine's long-lived sinks instead.
                let mut sink = crate::plugins::PluginInstanceSink::new(
                    crate::plugins::PluginEventSinks::default(),
                );
                let mut pruned = Vec::new();
                if new_song.track_params.len() != self.graph.track_count() {
                    // Full rebuild: every plugin slot is fresh.
                    for t in 0..new_song.track_params.len() {
                        for i in 0..new_song.track_fx[t].len() {
                            pruned.push(crate::plugins::PluginSlotKey::Fx { track: t, index: i });
                        }
                        for li in 0..new_song.track_generator_layers[t].len() {
                            pruned.push(crate::plugins::PluginSlotKey::Instrument {
                                track: t,
                                layer: li,
                            });
                        }
                    }
                    self.graph = Graph::new(
                        new_song.sample_rate,
                        &new_song.track_params,
                        &new_song.track_fx,
                        &new_song.track_generator_layers,
                        &new_song.track_layer_modes,
                        &new_song.automation,
                        &new_song.track_sends,
                        &new_song.track_outputs,
                        new_song.tempo,
                        new_song.loop_samples,
                        self.debug.clone(),
                        &mut sink,
                    );
                } else {
                    self.graph.update_params(
                        &new_song.track_params,
                        &new_song.track_fx,
                        &new_song.track_generator_layers,
                        &new_song.track_layer_modes,
                        &new_song.automation,
                        &new_song.track_sends,
                        &new_song.track_outputs,
                        new_song.tempo,
                        new_song.loop_samples,
                        &mut sink,
                        &mut pruned,
                    );
                }
                // Drop audio-thread instances (leak semantics unchanged)
                // and report pruned slots to the control thread.
                drop(sink);
                if !pruned.is_empty() {
                    let mut p = control.pruned_slots.lock().unwrap();
                    p.extend(pruned);
                }
                let loop_pos = self.sample_pos % new_song.loop_samples;
                self.graph.sync_cursors(
                    &new_song.track_events,
                    &new_song.track_audio_clips,
                    loop_pos,
                );
                self.song = new_song;
            }
        }

        let mut offset = 0;
        let total_frames = out.len() / 2;
        // Sync multithreading flag from control (FL-style global switch).
        self.graph.multithreaded = control.multithreaded.load(Ordering::Acquire);
        while offset < total_frames {
            let chunk = (total_frames - offset).min(MAX_BLOCK_FRAMES);
            let out_chunk = &mut out[offset * 2..(offset + chunk) * 2];
            self.graph.render(
                &self.song.track_events,
                &self.song.track_audio_clips,
                self.song.loop_samples,
                self.sample_pos + offset as u64,
                out_chunk,
            );
            offset += chunk;
        }
        self.sample_pos += total_frames as u64;
        control.position.store(self.sample_pos, Ordering::Release);
    }
}

/// Control-thread engine handle. Cheap to call from Python; every method is
/// coarse-grained (whole-arrangement snapshots, transport commands, offline
/// renders). No per-sample work crosses the bridge.
pub struct Engine {
    sample_rate: u32,
    arrangement: ArrangementData,
    song: Arc<Song>,
    song_slot: SongSlot,
    control: Arc<Control>,
    backend: Option<backend::Backend>,
    backend_desc: String,
    /// The user's chosen audio output (host/device). `None`/`None` means
    /// "System default". Applied whenever the backend is (re)built.
    audio_selection: backend::AudioSelection,
    /// The user's chosen *input* device (FL Studio's separate Input
    /// selector). Stored and persisted; no input stream is opened in
    /// this version (input monitoring/recording is future work).
    audio_input_selection: backend::AudioSelection,
    /// Open native plugin GUIs, keyed by GUI id. `PluginGui` is
    /// main-thread-only (`!Send`), so it must live here on the control
    /// thread — never in the audio graph.
    plugin_guis: HashMap<u64, crate::plugins::PluginGui>,
    next_gui_id: u64,
    /// Main-thread CLAP plugin instances, keyed by arrangement slot.
    /// Populated when the control thread builds the audio graph
    /// (`play()`); used by `save_plugin_states()`. `PluginInstance` is
    /// `!Send`, so these can never live in the audio graph.
    plugin_instances: HashMap<
        crate::plugins::PluginSlotKey,
        clack_host::prelude::PluginInstance<crate::plugins::PulsegridHost>,
    >,
    /// Long-lived host-event sinks for CLAP plugins: `mark_dirty()`
    /// reports and preset-load `loaded()`/`on_error()` callbacks.
    /// Cloned into every graph build's `PluginInstanceSink` so
    /// instances from any build report into the same place; drained
    /// on the control thread (`take_plugin_dirty_slots`,
    /// `take_preset_events`).
    plugin_events: crate::plugins::PluginEventSinks,
    debug: SharedDebugState,
    /// Decoded sample assets, keyed by asset id. Populated by
    /// `load_sample` on the control thread; snapshots (`Song`) hold
    /// `Arc` clones, so the audio thread never touches this map.
    sample_registry: HashMap<String, Arc<crate::sample::SampleBuffer>>,
}

impl Engine {
    pub fn sample_rate(&self) -> u32 {
        self.sample_rate
    }

    /// Shared debug instrumentation (peaks, voice counts, event log).
    pub fn debug_state(&self) -> SharedDebugState {
        self.debug.clone()
    }

    /// Open a plugin's native floating GUI. `params` are (clap_id, value)
    /// pairs flushed into the GUI instance so it reflects the project.
    /// Returns a GUI id for `plugin_gui_params`/`close_plugin_gui`.
    pub fn open_plugin_gui(
        &mut self,
        path: &std::path::Path,
        plugin_id: &str,
        params: &[(u32, f64)],
    ) -> Result<u64, String> {
        let gui = crate::plugins::PluginGui::open(path, plugin_id, params)?;
        let id = self.next_gui_id;
        self.next_gui_id += 1;
        self.plugin_guis.insert(id, gui);
        Ok(id)
    }

    /// Read current parameter values from an open GUI (for syncing
    /// user tweaks back to the project). Returns (clap_id, value).
    pub fn plugin_gui_params(&mut self, gui_id: u64) -> Vec<(u32, f64)> {
        match self.plugin_guis.get_mut(&gui_id) {
            Some(gui) => gui.read_params(),
            None => Vec::new(),
        }
    }

    /// Hide and destroy a plugin GUI. Idempotent.
    pub fn close_plugin_gui(&mut self, gui_id: u64) {
        if let Some(mut gui) = self.plugin_guis.remove(&gui_id) {
            gui.close();
        }
    }

    pub fn new(sample_rate: u32) -> Result<Self, String> {
        if !(8000..=192000).contains(&sample_rate) {
            return Err(format!("sample rate {} out of range", sample_rate));
        }
        // Placeholder arrangement so the engine is valid before the first
        // set_arrangement call: one silent pattern (a channel with no steps
        // set renders nothing) on one track.
        let arrangement = ArrangementData {
            tempo: 128.0,
            truncate_notes: false,
            patterns: vec![PatternData {
                id: String::new(),
                name: String::new(),
                steps: 16,
                channels: vec![ChannelData {
                    instrument: Instrument::Kick,
                    notes: Vec::new(),
                }],
            }],
            tracks: vec![TrackData {
                name: String::new(),
                gain: 1.0,
                pan: 0.0,
                muted: false,
                vel_track: 0.0,
                vel_track_mid: 0.5,
                key_track: 0.0,
                key_track_mid: 60.0,
                fx: Vec::new(),
                clips: Vec::new(),
                audio_clips: Vec::new(),
                automation: Vec::new(),
                generator_layers: Vec::new(),
                layer_mode: LayerMode::All,
                sends: Vec::new(),
                output: None,
            }],
            samples: Vec::new(),
        };
        let sample_registry: HashMap<String, Arc<crate::sample::SampleBuffer>> = HashMap::new();
        let song = Arc::new(Song::from_arrangement(
            sample_rate,
            &arrangement,
            &sample_registry,
        )?);
        let song_slot: SongSlot = Arc::new(RwLock::new(song.clone()));
        Ok(Engine {
            sample_rate,
            arrangement,
            song,
            song_slot,
            control: Arc::new(Control::new()),
            backend: None,
            backend_desc: "not started".to_string(),
            audio_selection: backend::AudioSelection::default(),
            audio_input_selection: backend::AudioSelection::default(),
            plugin_guis: HashMap::new(),
            next_gui_id: 1,
            plugin_instances: HashMap::new(),
            plugin_events: crate::plugins::PluginEventSinks::default(),
            debug: Arc::new(crate::debug::DebugState::new(MAX_DEBUG_TRACKS)),
            sample_registry,
        })
    }

    fn rebuild_song(&mut self) -> Result<(), String> {
        let song = Arc::new(Song::from_arrangement(
            self.sample_rate,
            &self.arrangement,
            &self.sample_registry,
        )?);
        self.song = song.clone();
        // Control-thread write; the audio thread only ever try_reads.
        *self
            .song_slot
            .write()
            .map_err(|_| "song slot poisoned".to_string())? = song;
        Ok(())
    }

    /// Replace the whole arrangement (coarse-grained bridge call).
    pub fn set_arrangement(&mut self, arrangement: ArrangementData) -> Result<(), String> {
        self.arrangement = arrangement;
        self.rebuild_song()
    }

    pub fn set_tempo(&mut self, bpm: f64) -> Result<(), String> {
        if !(20.0..=300.0).contains(&bpm) {
            return Err(format!("tempo {} out of range 20-300 BPM", bpm));
        }
        self.arrangement.tempo = bpm;
        self.rebuild_song()
    }

    /// Enable/disable multithreaded rendering (FL-style global switch).
    pub fn set_multithreaded(&mut self, enabled: bool) {
        self.control.multithreaded.store(enabled, Ordering::Release);
    }

    /// Whether an asset id is already decoded in the registry.
    pub fn has_sample(&self, asset_id: &str) -> bool {
        self.sample_registry.contains_key(asset_id)
    }

    /// Decode an audio file into the sample registry under `asset_id`
    /// (control thread only; decoding never happens on the audio path).
    /// Re-loading an existing id replaces the buffer; snapshots already
    /// built keep their `Arc` to the old buffer until the next
    /// `set_arrangement`.
    pub fn load_sample(&mut self, asset_id: &str, path: &str) -> Result<SampleInfo, String> {
        if asset_id.is_empty() {
            return Err("sample asset id must not be empty".to_string());
        }
        let buffer = crate::sample::decode_file(std::path::Path::new(path), self.sample_rate)?;
        let info = SampleInfo {
            frames: buffer.len,
            sample_rate: self.sample_rate,
            source_sample_rate: buffer.source_sample_rate,
            source_path: buffer.source_path.clone(),
            duration_secs: buffer.len as f64 / self.sample_rate as f64,
        };
        self.sample_registry
            .insert(asset_id.to_string(), Arc::new(buffer));
        Ok(info)
    }

    /// Drop a decoded asset from the registry. Snapshots already built
    /// keep working (they own `Arc`s); new snapshots referencing the id
    /// will fail loudly until it is re-loaded.
    pub fn unload_sample(&mut self, asset_id: &str) {
        self.sample_registry.remove(asset_id);
    }

    /// Peak pairs for waveform display: `n` (min, max) buckets over the
    /// whole buffer. Control thread only.
    pub fn sample_peaks(&self, asset_id: &str, n: usize) -> Result<Vec<(f32, f32)>, String> {
        let buffer = self
            .sample_registry
            .get(asset_id)
            .ok_or_else(|| format!("sample '{}' is not loaded", asset_id))?;
        Ok(crate::sample::peak_pairs(buffer, n))
    }

    pub fn play(&mut self) -> Result<(), String> {
        self.play_from(0)
    }

    /// Start playback at `start_sample` (frames). Used by
    /// [`Engine::reapply_audio_backend`] so a live device switch keeps the
    /// musical position: the fresh audio graph renders from the same
    /// sample instead of snapping the playhead back to zero.
    fn play_from(&mut self, start_sample: u64) -> Result<(), String> {
        let rebuilt = self.backend.is_none();
        if rebuilt {
            let song_slot = self.song_slot.clone();
            let initial = self.song.clone();
            let control = self.control.clone();
            let mut sink = crate::plugins::PluginInstanceSink::new(self.plugin_events.clone());
            let mut core = AudioCore::new(song_slot, initial, self.debug.clone(), &mut sink);
            core.sample_pos = start_sample;
            // Keep the main-thread plugin instances for state saving.
            // (Fresh registry: the graph was just rebuilt from the
            // current arrangement, so no stale entries can exist.)
            self.plugin_instances = sink.entries.into_iter().collect();
            let render = move |out: &mut [f32], _pos: u64| {
                core.render_block(out, &control);
            };
            let (backend, desc) =
                backend::start_backend_with_selection(self.sample_rate, render, &self.audio_selection)?;
            self.backend = Some(backend);
            self.backend_desc = desc;
            // The graph is fresh (pristine); a stale reset flag (e.g. left
            // by stop()) must not zero the restored position on the first
            // block. Reused-backend restarts use the flag below instead.
            self.control.reset.store(false, Ordering::Release);
        } else {
            // Reusing the backend (play after stop): ask the audio thread
            // for a full reset. render_block zeroes sample_pos there, which
            // is correct because this path always starts at 0.
            debug_assert_eq!(start_sample, 0);
            self.control.reset.store(true, Ordering::Release);
        }
        self.control.position.store(start_sample, Ordering::Release);
        self.control.playing.store(true, Ordering::Release);
        Ok(())
    }

    pub fn stop(&mut self) {
        self.control.playing.store(false, Ordering::Release);
        self.control.reset.store(true, Ordering::Release);
        self.control.position.store(0, Ordering::Release);
    }

    pub fn is_playing(&self) -> bool {
        self.control.playing.load(Ordering::Acquire)
    }

    pub fn position_beats(&self) -> f64 {
        let pos = self.control.position.load(Ordering::Acquire);
        self.song.beats_at(pos)
    }

    pub fn backend_name(&self) -> String {
        self.backend_desc.clone()
    }

    /// Every output device on every available host (for the Settings UI).
    pub fn audio_devices(&self) -> Result<Vec<backend::AudioDevice>, String> {
        backend::list_audio_devices()
    }

    /// Remember the user's chosen output. Takes effect on the next backend
    /// build; call [`Engine::reapply_audio_backend`] to switch live.
    /// `None`/`None` means "System default". The buffer-size choice is kept.
    pub fn set_audio_selection(
        &mut self,
        host_id: Option<String>,
        device_name: Option<String>,
    ) {
        self.audio_selection.host_id = host_id;
        self.audio_selection.device_name = device_name;
    }

    /// Set the audio buffer size in frames (FL/LMMS "buffer length" analog).
    /// `None` = the driver's default. Bigger = more time insurance per
    /// block (fewer underruns) at the cost of latency. Takes effect on the
    /// next backend build; call [`Engine::reapply_audio_backend`] to apply
    /// live. Non-positive values are rejected.
    pub fn set_buffer_frames(&mut self, frames: Option<u32>) -> Result<(), String> {
        if let Some(n) = frames {
            if n == 0 || n > 1 << 16 {
                return Err(format!("buffer size {n} out of range 1-65536"));
            }
        }
        self.audio_selection.buffer_frames = frames;
        Ok(())
    }

    /// Live backend counters: (callbacks, max_callback_us, underruns,
    /// block_frames). The UI turns these into FL Studio's CPU-meter metric
    /// (max render time as % of the buffer deadline) and the underrun count.
    pub fn backend_stats(&self) -> (u64, u64, u64, u64) {
        use std::sync::atomic::Ordering;
        match &self.backend {
            Some(b) => {
                let s = b.stats();
                (
                    s.callbacks.load(Ordering::Relaxed),
                    s.max_callback_us.load(Ordering::Relaxed),
                    s.underruns.load(Ordering::Relaxed),
                    s.block_frames.load(Ordering::Relaxed),
                )
            }
            None => (0, 0, 0, 0),
        }
    }

    /// The currently requested output selection.
    pub fn audio_selection(&self) -> backend::AudioSelection {
        self.audio_selection.clone()
    }

    /// The currently requested buffer size in frames (`None` = default).
    pub fn buffer_frames(&self) -> Option<u32> {
        self.audio_selection.buffer_frames
    }

    /// Every input device on every available host (for the Settings UI).
    pub fn audio_input_devices(&self) -> Result<Vec<backend::AudioDevice>, String> {
        backend::list_audio_input_devices()
    }

    /// Remember the user's chosen input device. Stored + persisted; no
    /// input stream is opened in this version.
    /// `None`/`None` means "System default".
    pub fn set_audio_input(&mut self, host_id: Option<String>, device_name: Option<String>) {
        self.audio_input_selection.host_id = host_id;
        self.audio_input_selection.device_name = device_name;
    }

    /// The currently requested input as (host_id, device_name).
    pub fn audio_input(&self) -> (Option<String>, Option<String>) {
        (
            self.audio_input_selection.host_id.clone(),
            self.audio_input_selection.device_name.clone(),
        )
    }

    /// Drop the current backend so the selected device is (re)opened.
    ///
    /// If the transport is playing, the backend is rebuilt immediately and
    /// playback continues from the same position (FL Studio keeps the
    /// transport running across a device switch; LMMS instead requires a
    /// restart for some audio changes -- we do the friendlier thing).
    /// If the rebuild fails, the transport is stopped rather than left in
    /// a zombie "playing but silent" state, and the error is returned.
    pub fn reapply_audio_backend(&mut self) -> Result<(), String> {
        let was_playing = self.control.playing.load(Ordering::Acquire);
        let pos = self.control.position.load(Ordering::Acquire);
        self.backend = None;
        if was_playing {
            // Rebuild from the same sample so the playhead does not jump.
            if let Err(e) = self.play_from(pos) {
                self.control.playing.store(false, Ordering::Release);
                return Err(e);
            }
        } else {
            self.backend_desc = "audio device changed -- press Play".to_string();
        }
        Ok(())
    }

    /// Deterministic offline render of the current arrangement.
    pub fn render_wav(&self, path: &str, loops: u32) -> Result<(), String> {
        if loops == 0 || loops > 1024 {
            return Err("loops must be 1-1024".to_string());
        }
        let song = Arc::new(Song::from_arrangement(
            self.sample_rate,
            &self.arrangement,
            &self.sample_registry,
        )?);
        let slot: SongSlot = Arc::new(RwLock::new(song.clone()));
        let mut sink = crate::plugins::PluginInstanceSink::new(self.plugin_events.clone());
        let mut core = AudioCore::new(slot, song.clone(), self.debug.clone(), &mut sink);
        // Offline render: don't disturb the live registry (the throwaway
        // instances are dropped with the sink).
        let control = Control::new();
        control.playing.store(true, Ordering::Release);

        let total_frames = song.loop_samples as usize * loops as usize;
        let mut out = vec![0.0f32; total_frames * 2];
        // Render in blocks to mirror the live path.
        let block = 512usize;
        for chunk in out.chunks_mut(block * 2) {
            core.render_block(chunk, &control);
        }
        wav::write_wav_stereo(path, self.sample_rate, &out)
            .map_err(|e| format!("failed to write {}: {}", path, e))?;
        Ok(())
    }

    /// Save every live plugin's opaque state blob (CLAP state extension).
    /// Main thread only — never call from the audio thread.
    ///
    /// Returns `(track, fx_index, layer_index, plugin_id, state_base64)`
    /// tuples: `fx_index` is `Some` for insert effects, `layer_index` is
    /// `Some` for generator layers. Slots rebuilt on the audio thread
    /// since the last control-thread graph build are pruned first (their
    /// plugins are fresh; params fully describe them). Slots whose plugin
    /// lacks the state extension are skipped (params-only, as before).
    /// A slot that no longer resolves against the current arrangement is
    /// skipped rather than misattributed.
    pub fn save_plugin_states(
        &mut self,
    ) -> Vec<(usize, Option<usize>, Option<usize>, String, String)> {
        // Drain audio-thread prune reports: those registry entries are stale.
        let pruned: Vec<crate::plugins::PluginSlotKey> = {
            let mut p = self.control.pruned_slots.lock().unwrap();
            std::mem::take(&mut *p)
        };
        for key in pruned {
            self.plugin_instances.remove(&key);
        }
        let mut out = Vec::new();
        for (key, instance) in self.plugin_instances.iter_mut() {
            // Project save uses the FOR_PROJECT state context when the
            // plugin implements it (stable since CLAP 1.2.0): "save my
            // state as part of this song". Falls back to the ordinary
            // state save otherwise.
            let bytes = match crate::plugins::save_plugin_state_ctx(
                instance,
                clack_extensions::state_context::StateContextType::ForProject,
            ) {
                Ok(b) => b,
                Err(_) => continue, // no state extension: params-only
            };
            let (track, fx_index, layer_index, plugin_id) = match key {
                crate::plugins::PluginSlotKey::Fx { track, index } => {
                    let id = self
                        .arrangement
                        .tracks
                        .get(*track)
                        .and_then(|t| t.fx.get(*index))
                        .and_then(|fx| match fx {
                            crate::effects::FxParams::Plugin { plugin_id, .. } => {
                                Some(plugin_id.clone())
                            }
                            _ => None,
                        });
                    match id {
                        Some(id) => (*track, Some(*index), None, id),
                        None => continue,
                    }
                }
                crate::plugins::PluginSlotKey::Instrument { track, layer } => {
                    let id = self
                        .arrangement
                        .tracks
                        .get(*track)
                        .and_then(|t| t.generator_layers.get(*layer))
                        .map(|l| l.generator.plugin_id.clone());
                    match id {
                        Some(id) => (*track, None, Some(*layer), id),
                        None => continue,
                    }
                }
            };
            out.push((
                track,
                fx_index,
                layer_index,
                plugin_id,
                crate::plugins::base64_encode(&bytes),
            ));
        }
        out
    }

    /// Drain plugin slots that called `mark_dirty()` since the last
    /// drain. The UI polls this and marks the project dirty — this is
    /// how *non-parameter* plugin state changes (which the host cannot
    /// otherwise observe) reach the "modified / needs save" flag.
    /// Control thread only.
    pub fn take_plugin_dirty_slots(&self) -> Vec<crate::plugins::PluginSlotKey> {
        let mut d = self.plugin_events.dirty.lock().unwrap();
        std::mem::take(&mut *d)
    }

    /// Drain preset-load `loaded()` / `on_error()` reports since the
    /// last drain. The UI uses these to keep its preset browser in
    /// sync with the plugin's own preset state. Control thread only.
    pub fn take_preset_events(
        &self,
    ) -> (
        Vec<crate::plugins::PresetLoadedEvent>,
        Vec<crate::plugins::PresetLoadError>,
    ) {
        let loaded = std::mem::take(&mut *self.plugin_events.preset_loaded.lock().unwrap());
        let errors = std::mem::take(&mut *self.plugin_events.preset_errors.lock().unwrap());
        (loaded, errors)
    }

    /// Drain plugin restart requests (`request_restart()`) and
    /// latency-change notifications (`latency.changed()`) since the
    /// last drain, as `(kind, slot)` pairs (`kind` is `"restart"` or
    /// `"latency_changed"`). The UI polls this and calls
    /// `process_plugin_restarts()` to service them. Control thread
    /// only.
    pub fn take_restart_requests(&self) -> Vec<(String, crate::plugins::PluginSlotKey)> {
        let mut out = Vec::new();
        let mut restarts = self.plugin_events.restart_requested.lock().unwrap();
        for slot in restarts.drain(..) {
            out.push(("restart".to_string(), slot));
        }
        drop(restarts);
        let mut changed = self.plugin_events.latency_changed.lock().unwrap();
        for slot in changed.drain(..) {
            if !out.iter().any(|(_, s)| *s == slot) {
                out.push(("latency_changed".to_string(), slot));
            }
        }
        out
    }

    /// Restart every slot with a pending restart request or
    /// latency-change notification (see `take_restart_requests`;
    /// this drains both queues itself).
    ///
    /// This is the host side of CLAP's runtime latency-change
    /// contract: the plugin reported a structural change (usually via
    /// `request_restart()`), so the host restarts the instance —
    /// deactivate/reactivate — re-queries its latency *after*
    /// activation (the only lifecycle point where the new value is
    /// valid, per CLAP 1.2.2+), and recalculates PDC from the fresh
    /// values. A bare `latency.changed()` without a restart request
    /// is treated the same way, defensively.
    ///
    /// Each slot's state is first preserved with the FOR_DUPLICATE
    /// state context ("recreate this instance"), stashed into the
    /// arrangement, and restored by the rebuild — so the restart is
    /// lossless for plugins that implement the state extension.
    /// Returns the restarted slots. Control thread only.
    pub fn process_plugin_restarts(
        &mut self,
    ) -> Result<Vec<crate::plugins::PluginSlotKey>, String> {
        let mut slots: Vec<crate::plugins::PluginSlotKey> = Vec::new();
        {
            let mut restarts = self.plugin_events.restart_requested.lock().unwrap();
            for slot in restarts.drain(..) {
                if !slots.contains(&slot) {
                    slots.push(slot);
                }
            }
        }
        {
            let mut changed = self.plugin_events.latency_changed.lock().unwrap();
            for slot in changed.drain(..) {
                if !slots.contains(&slot) {
                    slots.push(slot);
                }
            }
        }
        if slots.is_empty() {
            return Ok(slots);
        }
        // Preserve each slot's state with the FOR_DUPLICATE context
        // before the rebuild drops the old instances.
        for slot in &slots {
            if let Some(instance) = self.plugin_instances.get_mut(slot) {
                match crate::plugins::save_plugin_state_ctx(
                    instance,
                    clack_extensions::state_context::StateContextType::ForDuplicate,
                ) {
                    Ok(blob) => self.stash_arrangement_state_blob(*slot, blob),
                    Err(_) => {} // no state extension: params describe it
                }
            }
        }
        // Rebuild the song data, then rebuild the graph on the control
        // thread with the Engine's long-lived sinks (same as `play()`):
        // fresh instances re-query latency post-activation, the
        // registry is refreshed, and `Graph::new` recalculates PDC.
        // The live audio thread (if any) picks up the new song on its
        // next block, exactly like `set_arrangement`.
        self.rebuild_song()?;
        let mut sink = crate::plugins::PluginInstanceSink::new(self.plugin_events.clone());
        let _graph = crate::graph::Graph::new(
            self.sample_rate,
            &self.song.track_params,
            &self.song.track_fx,
            &self.song.track_generator_layers,
            &self.song.track_layer_modes,
            &self.song.automation,
            &self.song.track_sends,
            &self.song.track_outputs,
            self.song.tempo,
            self.song.loop_samples,
            self.debug.clone(),
            &mut sink,
        );
        self.plugin_instances = sink.entries.into_iter().collect();
        Ok(slots)
    }

    /// Write a state blob into the arrangement slot's stored state so
    /// the next graph rebuild restores it (used by restarts).
    fn stash_arrangement_state_blob(&mut self, slot: crate::plugins::PluginSlotKey, blob: Vec<u8>) {
        match slot {
            crate::plugins::PluginSlotKey::Fx { track, index } => {
                if let Some(fx) = self
                    .arrangement
                    .tracks
                    .get_mut(track)
                    .and_then(|t| t.fx.get_mut(index))
                {
                    if let crate::effects::FxParams::Plugin { state, .. } = fx {
                        *state = Some(blob);
                    }
                }
            }
            crate::plugins::PluginSlotKey::Instrument { track, layer } => {
                if let Some(gen) = self
                    .arrangement
                    .tracks
                    .get_mut(track)
                    .and_then(|t| t.generator_layers.get_mut(layer))
                {
                    gen.generator.state = Some(blob);
                }
            }
        }
    }

    /// The plugin's currently reported latency in samples for a slot,
    /// queried live from the registry instance (which is activated,
    /// so per CLAP 1.2.2+ this is a legal query point). `None` when
    /// the slot has no live instance. Control thread only.
    pub fn plugin_latency_samples(&mut self, slot: crate::plugins::PluginSlotKey) -> Option<u32> {
        let instance = self.plugin_instances.get_mut(&slot)?;
        let handle = instance.plugin_handle();
        Some(
            handle
                .get_extension::<clack_extensions::latency::PluginLatency>()
                .map(|ext| ext.get(&handle))
                .unwrap_or(0),
        )
    }

    /// Look up a registry instance by arrangement slot.
    fn plugin_instance_mut(
        &mut self,
        slot: crate::plugins::PluginSlotKey,
    ) -> Option<&mut clack_host::prelude::PluginInstance<crate::plugins::PulsegridHost>> {
        self.plugin_instances.get_mut(&slot)
    }

    /// Save one slot's plugin state with the FOR_PRESET context
    /// ("save my state as a reusable preset"). Returns the raw blob;
    /// the Python side Base64-encodes it into the preset file (a host
    /// serialization decision, like project storage).
    pub fn save_plugin_preset_blob(
        &mut self,
        slot: crate::plugins::PluginSlotKey,
    ) -> Result<Vec<u8>, String> {
        let instance = self
            .plugin_instance_mut(slot)
            .ok_or_else(|| "no live plugin instance for that slot".to_string())?;
        crate::plugins::save_plugin_state_ctx(
            instance,
            clack_extensions::state_context::StateContextType::ForPreset,
        )
    }

    /// Does the slot's plugin implement `CLAP_EXT_PRESET_LOAD`
    /// (native preset files via `from_location()`)?
    pub fn plugin_supports_preset_load(&mut self, slot: crate::plugins::PluginSlotKey) -> bool {
        match self.plugin_instance_mut(slot) {
            Some(instance) => {
                let handle = instance.plugin_handle();
                handle
                    .get_extension::<crate::plugins::PluginPresetLoad>()
                    .is_some()
            }
            None => false,
        }
    }

    /// Load a preset blob (previously saved with
    /// `save_plugin_preset_blob`) into the slot's registry instance
    /// using the FOR_PRESET context, then rescan the plugin's current
    /// parameter values. Returns `(params, blob_base64)`: the caller
    /// stores both in the project in one undoable edit, then rebuilds
    /// the graph (the blob is authoritative at load, as usual).
    ///
    /// Per CLAP, rescanned parameter values are NOT recorded as
    /// automation — the caller must not turn them into automation
    /// points.
    pub fn load_plugin_preset_blob(
        &mut self,
        slot: crate::plugins::PluginSlotKey,
        blob: &[u8],
    ) -> Result<(Vec<(u32, f64)>, String), String> {
        let instance = self
            .plugin_instance_mut(slot)
            .ok_or_else(|| "no live plugin instance for that slot".to_string())?;
        let handle = instance.plugin_handle();
        {
            use clack_extensions::state_context::PluginStateContext;
            let ext: Option<PluginStateContext> = handle.get_extension();
            let ok = match ext {
                Some(ext) => {
                    let mut cursor = std::io::Cursor::new(blob);
                    ext.load(
                        &handle,
                        &mut cursor,
                        clack_extensions::state_context::StateContextType::ForPreset,
                    )
                    .is_ok()
                }
                None => false,
            };
            if !ok {
                // Fall back to the ordinary state load (spec-declared
                // compatible); a missing extension or corrupt blob is
                // an error here, unlike project load which is lenient.
                let state_ext: Option<clack_extensions::state::PluginState> =
                    handle.get_extension();
                match state_ext {
                    Some(ext) => {
                        let mut cursor = std::io::Cursor::new(blob);
                        ext.load(&handle, &mut cursor)
                            .map_err(|e| format!("preset blob rejected: {}", e))?;
                    }
                    None => return Err("plugin does not support the state extension".to_string()),
                }
            }
        }
        let params = crate::plugins::plugin_param_values(instance)?;
        Ok((params, crate::plugins::base64_encode(blob)))
    }

    /// Ask the slot's plugin to load one of its *native* preset files
    /// (`CLAP_EXT_PRESET_LOAD from_location()`), then rescan parameter
    /// values and capture the resulting state blob (FOR_PRESET
    /// context). Returns `(params, blob_base64)` for the caller to
    /// store in the project in one undoable edit.
    ///
    /// This is distinct from blob preset load: the plugin parses its
    /// own native preset format. `load_key` selects a preset inside a
    /// container file (`None` for plain files).
    pub fn plugin_preset_from_location(
        &mut self,
        slot: crate::plugins::PluginSlotKey,
        path: &str,
        load_key: Option<&str>,
    ) -> Result<(Vec<(u32, f64)>, String), String> {
        // Drain any stale preset events first so the caller sees only
        // events from this load.
        let _ = self.take_preset_events();
        let (params, blob) = {
            let instance = self
                .plugin_instance_mut(slot)
                .ok_or_else(|| "no live plugin instance for that slot".to_string())?;
            let handle = instance.plugin_handle();
            let ext: Option<crate::plugins::PluginPresetLoad> = handle.get_extension();
            match ext {
                Some(ext) => ext.from_location(&handle, path, load_key)?,
                None => return Err("plugin does not support native preset loading".to_string()),
            }
            // Rescan current values (NOT automation, per CLAP) and
            // capture the post-load state.
            let params = crate::plugins::plugin_param_values(instance)?;
            let blob = crate::plugins::save_plugin_state_ctx(
                instance,
                clack_extensions::state_context::StateContextType::ForPreset,
            )
            .unwrap_or_default();
            (params, blob)
        };
        Ok((params, crate::plugins::base64_encode(&blob)))
    }

    pub fn stats_snapshot(&self) -> EngineStats {
        let (callbacks, max_us, underruns) = match &self.backend {
            Some(b) => {
                let s = b.stats();
                (
                    s.callbacks.load(Ordering::Relaxed),
                    s.max_callback_us.load(Ordering::Relaxed),
                    s.underruns.load(Ordering::Relaxed),
                )
            }
            None => (0, 0, 0),
        };
        EngineStats {
            callbacks,
            max_callback_us: max_us,
            underruns,
            sample_rate: self.sample_rate,
            backend: self.backend_desc.clone(),
            playing: self.is_playing(),
        }
    }

    /// Build an ArrangementData from raw parts (used by the Python bridge).
    #[allow(clippy::too_many_arguments)]
    pub fn make_arrangement(
        tempo: f64,
        truncate_notes: bool,
        patterns: Vec<RawPattern>,
        tracks: Vec<RawTrack>,
        samples: Vec<String>,
    ) -> Result<ArrangementData, String> {
        let mut pat_data = Vec::with_capacity(patterns.len());
        for rp in patterns {
            let mut ch_data = Vec::with_capacity(rp.channels.len());
            for (inst_name, raw_notes) in rp.channels {
                let instrument = Instrument::from_str(&inst_name)
                    .ok_or_else(|| format!("unknown instrument '{}'", inst_name))?;
                let mut notes = Vec::with_capacity(raw_notes.len());
                for rn in raw_notes {
                    if rn.pitch > 127 {
                        return Err("pitch out of MIDI range 0-127".to_string());
                    }
                    if !(0.0..=1.0).contains(&rn.velocity) {
                        return Err("velocity out of range 0-1".to_string());
                    }
                    if !(-1.0..=1.0).contains(&rn.pan) {
                        return Err("note pan out of range -1..1".to_string());
                    }
                    notes.push(NoteData {
                        start_step: rn.start_step,
                        len_steps: rn.len_steps,
                        pitch: rn.pitch,
                        velocity: rn.velocity,
                        pan: rn.pan as f32,
                    });
                }
                ch_data.push(ChannelData { instrument, notes });
            }
            pat_data.push(PatternData {
                id: rp.id,
                name: rp.name,
                steps: rp.steps,
                channels: ch_data,
            });
        }
        let mut track_data = Vec::with_capacity(tracks.len());
        for rt in tracks {
            let mut clips = Vec::with_capacity(rt.clips.len());
            for rc in rt.clips {
                clips.push(ClipData {
                    pattern: rc.pattern,
                    start_beat: rc.start_beat,
                    bars: rc.bars,
                });
            }
            let mut fx = Vec::with_capacity(rt.effects.len());
            for re in rt.effects {
                fx.push(make_fx(&re)?);
            }
            let mut audio_clips = Vec::with_capacity(rt.audio_clips.len());
            for ra in rt.audio_clips {
                audio_clips.push(crate::timeline::RawAudioClip {
                    asset: ra.asset,
                    start_beat: ra.start_beat,
                    length_beats: ra.length_beats,
                    start_offset_beats: ra.start_offset_beats,
                    gain: ra.gain,
                    pan: ra.pan,
                    pitch_semitones: ra.pitch_semitones,
                    fine_cents: ra.fine_cents,
                    reverse: ra.reverse,
                    muted: ra.muted,
                });
            }
            if !(-1.0..=1.0).contains(&rt.vel_track) {
                return Err("vel_track out of range -1..1".to_string());
            }
            if !(0.0..=1.0).contains(&rt.vel_track_mid) {
                return Err("vel_track_mid out of range 0..1".to_string());
            }
            if !(-1.0..=1.0).contains(&rt.key_track) {
                return Err("key_track out of range -1..1".to_string());
            }
            if !(0.0..=127.0).contains(&rt.key_track_mid) {
                return Err("key_track_mid out of range 0..127".to_string());
            }
            track_data.push(TrackData {
                name: rt.name,
                gain: rt.gain,
                pan: rt.pan,
                muted: rt.muted,
                vel_track: rt.vel_track,
                vel_track_mid: rt.vel_track_mid,
                key_track: rt.key_track,
                key_track_mid: rt.key_track_mid,
                fx,
                clips,
                audio_clips,
                automation: rt.automation,
                generator_layers: rt
                    .generator_layers
                    .into_iter()
                    .map(|l| GeneratorLayerParams {
                        generator: GeneratorParams {
                            plugin_id: l.plugin_id,
                            path: l.path,
                            params: l.params,
                            state: l.state,
                            is_vst3: l.is_vst3,
                        },
                        gain: l.gain,
                        pitch_offset: l.pitch_offset,
                        enabled: l.enabled,
                    })
                    .collect(),
                layer_mode: match rt.layer_mode.as_str() {
                    "random" => LayerMode::Random,
                    "sequential" => LayerMode::Sequential,
                    _ => LayerMode::All,
                },
                sends: rt.sends,
                output: rt.output,
            });
        }
        Ok(ArrangementData {
            tempo,
            truncate_notes,
            patterns: pat_data,
            tracks: track_data,
            samples,
        })
    }
}

/// Raw bridge types (plain data, validated in make_arrangement / Song).
pub struct RawNote {
    pub start_step: f64,
    pub len_steps: f64,
    pub pitch: u8,
    pub velocity: f64,
    /// Per-note pan in engine convention: -1.0 left .. +1.0 right.
    pub pan: f64,
}

pub struct RawPattern {
    pub id: String,
    pub name: String,
    pub steps: usize,
    pub channels: Vec<(String, Vec<RawNote>)>,
}

pub struct RawClip {
    /// Index into the patterns list.
    pub pattern: usize,
    pub start_beat: u32,
    pub bars: u32,
}

/// Raw audio clip from the bridge: timeline sample playback with
/// per-instance properties (FL Clip Properties model). Validated in
/// `Song::from_arrangement`.
pub struct RawAudioClip {
    /// Asset id; resolved against the engine's sample registry.
    pub asset: String,
    pub start_beat: f64,
    pub length_beats: f64,
    pub start_offset_beats: f64,
    pub gain: f32,
    /// Engine convention: -1.0 left .. +1.0 right.
    pub pan: f32,
    pub pitch_semitones: f32,
    pub fine_cents: f32,
    pub reverse: bool,
    pub muted: bool,
}

pub struct RawTrack {
    pub name: String,
    pub gain: f32,
    pub pan: f32,
    pub muted: bool,
    /// FL Studio-style velocity tracking amount (bipolar -1..1, 0 = off).
    pub vel_track: f32,
    /// Middle velocity (0..1) where velocity tracking generates no offset.
    pub vel_track_mid: f32,
    /// FL Studio-style keyboard tracking amount (bipolar -1..1, 0 = off).
    pub key_track: f32,
    /// Middle MIDI note (0..127) where keyboard tracking generates no offset.
    pub key_track_mid: f32,
    pub effects: Vec<RawFx>,
    pub clips: Vec<RawClip>,
    pub audio_clips: Vec<RawAudioClip>,
    pub automation: Vec<RawAutoCurve>,
    pub generator_layers: Vec<RawGeneratorLayer>,
    pub layer_mode: String,
    pub sends: Vec<SendData>,
    /// Exclusive output route: track index, or None for Master.
    pub output: Option<usize>,
}

/// Raw generator layer from the bridge.
#[derive(Clone, Debug)]
pub struct RawGeneratorLayer {
    pub plugin_id: String,
    pub path: String,
    pub params: Vec<(u32, f64)>,
    pub gain: f32,
    pub pitch_offset: i32,
    pub enabled: bool,
    /// Opaque CLAP state blob (Base64-decoded by the bridge).
    pub state: Option<Vec<u8>>,
    /// True for VST3 instruments, false for CLAP.
    pub is_vst3: bool,
}

/// Raw generator from the bridge: plugin instrument or None.
/// (Legacy single-generator form; converted to a one-layer stack.)
#[derive(Clone, Debug)]
pub struct RawGenerator {
    pub plugin_id: String,
    pub path: String,
    pub params: Vec<(u32, f64)>,
    pub is_vst3: bool,
}

/// Raw effect from the bridge: kind + the union of all effect params
/// (only the relevant ones are read per kind). Plugin effects carry
/// their CLAP identity and parameter values.
pub struct RawFx {
    pub kind: String,
    pub time_ms: f32,
    pub feedback: f32,
    pub mix: f32,
    pub amount: f32,
    pub cutoff: f32,
    pub threshold: f32,
    pub ratio: f32,
    pub attack_ms: f32,
    pub release_ms: f32,
    pub semitones: f32,
    pub plugin_id: String,
    pub plugin_path: String,
    pub plugin_params: Vec<(u32, f64)>,
    /// Opaque CLAP state blob (already Base64-decoded by the bridge).
    pub plugin_state: Option<Vec<u8>>,
}

fn make_fx(raw: &RawFx) -> Result<FxParams, String> {
    match raw.kind.as_str() {
        "delay" => Ok(FxParams::Delay {
            time_ms: raw.time_ms,
            feedback: raw.feedback,
            mix: raw.mix,
        }),
        "drive" => Ok(FxParams::Drive { amount: raw.amount }),
        "filter" => Ok(FxParams::Filter { cutoff: raw.cutoff }),
        "ducker" => Ok(FxParams::Ducker {
            threshold: raw.threshold,
            ratio: raw.ratio,
            attack_ms: raw.attack_ms,
            release_ms: raw.release_ms,
        }),
        "pitchshift" => Ok(FxParams::PitchShift {
            semitones: raw.semitones,
            mix: raw.mix,
        }),
        "plugin" => Ok(FxParams::Plugin {
            plugin_id: raw.plugin_id.clone(),
            path: raw.plugin_path.clone(),
            params: raw.plugin_params.clone(),
            state: raw.plugin_state.clone(),
        }),
        "vst3" => Ok(FxParams::Vst3Plugin {
            plugin_id: raw.plugin_id.clone(),
            path: raw.plugin_path.clone(),
            params: raw.plugin_params.clone(),
        }),
        other => Err(format!("unknown effect '{}'", other)),
    }
}

pub struct EngineStats {
    pub callbacks: u64,
    pub max_callback_us: u64,
    pub underruns: u64,
    pub sample_rate: u32,
    pub backend: String,
    pub playing: bool,
}

/// Decoded sample asset metadata, returned by `load_sample`.
pub struct SampleInfo {
    /// Stereo frames at the engine sample rate.
    pub frames: usize,
    /// Engine sample rate the buffer was converted to.
    pub sample_rate: u32,
    /// Original file's sample rate.
    pub source_sample_rate: u32,
    pub source_path: String,
    pub duration_secs: f64,
}
