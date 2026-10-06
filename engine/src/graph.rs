//! Audio graph.
//!
//! Topology (fixed for now, designed to grow):
//!
//! ```text
//!   TrackEvent list ──► VoiceBank ──► Gain/Pan ──┐
//!   TrackEvent list ──► VoiceBank ──► Gain/Pan ──┼──► MixBus ──► Master ──► out
//!   ...                                          ┘
//! ```
//!
//! * Each track owns a [`TrackStrip`]: its own voice pool (16 voices) plus a
//!   gain/pan stage. Stage 4 (mixer) will expose these parameters in the UI
//!   and allow effect inserts between Gain/Pan and the MixBus.
//! * All buffers are preallocated for the maximum block size; `render`
//!   processes the requested prefix.

use std::f32::consts::PI;

use crate::effects::{Effect, FxParams};
use crate::debug::{DebugEvent, DebugEventKind, SharedDebugState};
use crate::plugins::{HostedClapInstrument, InstrumentNoteEvent};
use crate::synth::{Instrument, Voice};
use crate::timeline::{
    AudioClipEvent, AutoCurve, AutoParam, GeneratorLayerParams, LayerMode,
    SendData, SendTap, TrackEvent, TrackParams,
};
use crate::vst3::{HostedVst3Instrument, Vst3NoteEvent};

/// A hosted instrument plugin (CLAP or VST3).
enum HostedInstrument {
    Clap(HostedClapInstrument),
    Vst3(HostedVst3Instrument),
}

impl HostedInstrument {
    fn process_notes(&mut self, notes: &[InstrumentNoteEvent], out: &mut [f32]) {
        match self {
            HostedInstrument::Clap(p) => p.process_notes(notes, out),
            HostedInstrument::Vst3(p) => {
                // Convert to VST3 MIDI note events.
                let vst3_notes: Vec<Vst3NoteEvent> = notes
                    .iter()
                    .filter_map(|n| match *n {
                        InstrumentNoteEvent::On { key, velocity, .. } => {
                            Some(Vst3NoteEvent::On { key, velocity })
                        }
                        InstrumentNoteEvent::Off { key, .. } => {
                            Some(Vst3NoteEvent::Off { key })
                        }
                        InstrumentNoteEvent::Choke { key, .. } => {
                            Some(Vst3NoteEvent::Off { key })
                        }
                    })
                    .collect();
                p.process_notes(&vst3_notes, out);
            }
        }
    }

    fn queue_param(&mut self, id: u32, value: f64) {
        match self {
            HostedInstrument::Clap(p) => p.queue_param(id, value),
            HostedInstrument::Vst3(p) => p.queue_param(id, value),
        }
    }
}

/// A hosted generator layer: the instrument plugin plus per-layer mix settings.
struct HostedGeneratorLayer {
    plugin: HostedInstrument,
    gain: f32,
    pitch_offset: i32,
    enabled: bool,
}

/// Maximum audio block the graph can render in one call (frames).
pub const MAX_BLOCK_FRAMES: usize = 4096;

/// Voices per track. Fixed pool, round-robin allocation.
const VOICES_PER_TRACK: usize = 16;

pub(crate) fn pan_gains(gain: f32, pan: f32) -> (f32, f32) {
    // Constant-power pan: pan -1 => hard left, +1 => hard right.
    let angle = (pan + 1.0) * PI / 4.0;
    (gain * angle.cos(), gain * angle.sin())
}

/// Build a live effect chain from validated params. Params are validated
/// when the Song is built, so a build failure here is silently skipped
/// (defensive; the audio thread must never panic). Plugin main-thread
/// instances are pushed to `sink` keyed by their slot for state saving.
fn build_fx_chain(
    params: &[FxParams],
    sample_rate: u32,
    track: usize,
    sink: &mut crate::plugins::PluginInstanceSink,
) -> Vec<Effect> {
    params
        .iter()
        .enumerate()
        .filter_map(|(i, p)| {
            Effect::build(
                p,
                sample_rate,
                Some(crate::plugins::PluginSlotKey::Fx { track, index: i }),
                sink,
            )
            .ok()
        })
        .collect()
}

/// Queue plugin parameter changes without rebuilding the chain.
/// Returns false when the change needs a full rebuild (plugin added,
/// removed, swapped, or a built-in effect changed).
fn fx_update_in_place(
    fx: &mut [Effect],
    old: &[FxParams],
    new: &[FxParams],
) -> bool {
    if fx.len() != old.len() || old.len() != new.len() {
        return false;
    }
    for ((effect, o), n) in fx.iter_mut().zip(old.iter()).zip(new.iter()) {
        match (effect, o, n) {
            (
                Effect::Plugin(p),
                FxParams::Plugin { plugin_id: a, .. },
                FxParams::Plugin {
                    plugin_id: b,
                    params,
                    ..
                },
            ) if a == b => {
                for (id, value) in params {
                    p.queue_param(*id, *value);
                }
            }
            _ if o == n => {}
            _ => return false,
        }
    }
    true
}

struct TrackStrip {
    voices: Vec<Voice>,
    next_voice: usize,
    event_idx: usize,
    prev_loop_pos: u64,
    /// Audio-clip cursor: index of the next clip to consider, plus the
    /// loop position of the last rendered frame (separate from
    /// `prev_loop_pos`, which the note paths own).
    audio_idx: usize,
    audio_prev_loop_pos: u64,
    /// Currently sounding audio clips (a clip is a continuous region,
    /// unlike a note trigger, so playheads persist across frames).
    active_clips: Vec<ClipPlayhead>,
    gain_l: f32,
    gain_r: f32,
    /// Static mix values; automation curves override them per block.
    base_gain: f32,
    base_pan: f32,
    muted: bool,
    /// FL Studio-style velocity tracking (3xOsc Volume Tracking model):
    /// per-note velocity -> voice lowpass cutoff. `vel_track` is the
    /// bipolar amount (-1..1, 0 = off); `vel_track_mid` the middle
    /// velocity (0..1) where no offset is generated. Evaluated per note
    /// at trigger time (note domain), not per block.
    vel_track: f32,
    vel_track_mid: f32,
    /// FL Studio-style keyboard tracking (Channel Keyboard Tracker
    /// model): per-note pitch -> voice lowpass cutoff. `key_track` is
    /// the bipolar amount (-1..1, 0 = off); `key_track_mid` the middle
    /// MIDI note (0..127) where no offset is generated. Independent
    /// from velocity tracking; both sum in the cutoff exponent.
    key_track: f32,
    key_track_mid: f32,
    fx: Vec<Effect>,
    fx_params: Vec<FxParams>,
    sample_rate: u32,
    /// Generator layers (FL Layer-style note fan-out). Empty = built-in voices.
    generator_layers: Vec<HostedGeneratorLayer>,
    generator_layer_params: Vec<GeneratorLayerParams>,
    /// How note events fan out across layers.
    layer_mode: LayerMode,
    /// Round-robin index for Sequential mode.
    rr_idx: usize,
    /// Pending note-offs: (absolute sample, note_id, key, layer_idx).
    pending_offs: Vec<(u64, i32, u8, usize)>,
    next_note_id: i32,
    /// Scratch for note events within one block, per layer.
    layer_events: Vec<Vec<InstrumentNoteEvent>>,
    /// Scratch buffer for mixing layer outputs.
    layer_scratch: Vec<f32>,
    /// This strip's index (for debug reporting).
    track_idx: usize,
    /// Shared debug state (peaks, voices, event log).
    debug: SharedDebugState,
    /// Smart Disable: consecutive silent blocks. When this exceeds
    /// SILENT_BLOCKS_TO_DISABLE and no voices are active, the FX chain
    /// is skipped (saves CPU on sparse arrangements).
    silent_blocks: u32,
    /// Cached voice count for Smart Disable (set in render(), read in
    /// should_skip_fx() after sends are applied).
    cached_voice_count: u32,
}

/// One sounding audio clip: a playhead into a shared [`SampleBuffer`].
/// Position is kept in buffer-frame domain as f64 (f32 loses integer
/// precision past ~8M frames, i.e. ~3 minutes at 48 kHz).
struct ClipPlayhead {
    buffer: std::sync::Arc<crate::sample::SampleBuffer>,
    /// Current read position in buffer frames.
    fpos: f64,
    /// Buffer frames advanced per output frame; negative = reversed.
    step: f64,
    /// Output frames remaining.
    remaining: u64,
    gain_l: f32,
    gain_r: f32,
}

/// Linear-interpolated stereo sample read at a fractional buffer position.
fn sample_at(buffer: &crate::sample::SampleBuffer, fpos: f64) -> (f32, f32) {
    let len = buffer.len;
    if len == 0 {
        return (0.0, 0.0);
    }
    let p = fpos.clamp(0.0, (len - 1) as f64);
    let i0 = p as usize;
    let i1 = (i0 + 1).min(len - 1);
    let t = (p - i0 as f64) as f32;
    let frames = &buffer.frames;
    (
        frames[2 * i0] * (1.0 - t) + frames[2 * i1] * t,
        frames[2 * i0 + 1] * (1.0 - t) + frames[2 * i1 + 1] * t,
    )
}

/// Blocks of silence before the FX chain is smart-disabled.
/// At 64 frames/block and 48kHz, 16 blocks ~ 21ms -- enough for delay
/// tails to decay, short enough to re-enable instantly on new audio.
const SILENT_BLOCKS_TO_DISABLE: u32 = 16;
/// Peak threshold below which a block counts as silent.
const SILENCE_THRESHOLD: f32 = 1e-6;

impl TrackStrip {
    fn new(
        sample_rate: u32,
        params: TrackParams,
        fx_params: &[FxParams],
        layers: &[GeneratorLayerParams],
        layer_mode: LayerMode,
        track_idx: usize,
        debug: SharedDebugState,
        sink: &mut crate::plugins::PluginInstanceSink,
    ) -> Self {
        let mut voices = Vec::with_capacity(VOICES_PER_TRACK);
        for _ in 0..VOICES_PER_TRACK {
            voices.push(Voice::new(sample_rate));
        }
        let (gain_l, gain_r) = pan_gains(params.gain, params.pan);
        let hosted_layers: Vec<HostedGeneratorLayer> = layers
            .iter()
            .enumerate()
            .filter_map(|(li, l)| {
                let path = std::path::Path::new(&l.generator.path);
                if l.generator.is_vst3 {
                    // VST3 instrument.
                    HostedVst3Instrument::load(path, sample_rate, &l.generator.params)
                        .ok()
                        .map(|plugin| HostedGeneratorLayer {
                            plugin: HostedInstrument::Vst3(plugin),
                            gain: l.gain,
                            pitch_offset: l.pitch_offset,
                            enabled: l.enabled,
                        })
                } else {
                    // CLAP instrument.
                    HostedClapInstrument::load(
                        path,
                        &l.generator.plugin_id,
                        sample_rate,
                        &l.generator.params,
                        l.generator.state.as_deref(),
                        Some(crate::plugins::PluginSlotKey::Instrument {
                            track: track_idx,
                            layer: li,
                        }),
                        &sink.events,
                    )
                    .ok()
                    .map(|(plugin, instance)| {
                        sink.push(
                            crate::plugins::PluginSlotKey::Instrument {
                                track: track_idx,
                                layer: li,
                            },
                            instance,
                        );
                        HostedGeneratorLayer {
                            plugin: HostedInstrument::Clap(plugin),
                            gain: l.gain,
                            pitch_offset: l.pitch_offset,
                            enabled: l.enabled,
                        }
                    })
                }
            })
            .collect();
        TrackStrip {
            voices,
            next_voice: 0,
            event_idx: 0,
            prev_loop_pos: 0,
            audio_idx: 0,
            audio_prev_loop_pos: 0,
            active_clips: Vec::new(),
            gain_l,
            gain_r,
            base_gain: params.gain,
            base_pan: params.pan,
            muted: params.muted,
            vel_track: params.vel_track,
            vel_track_mid: params.vel_track_mid,
            key_track: params.key_track,
            key_track_mid: params.key_track_mid,
            fx: build_fx_chain(fx_params, sample_rate, track_idx, sink),
            fx_params: fx_params.to_vec(),
            sample_rate,
            generator_layers: hosted_layers,
            generator_layer_params: layers.to_vec(),
            layer_mode,
            rr_idx: 0,
            pending_offs: Vec::new(),
            next_note_id: 1,
            layer_events: Vec::new(),
            layer_scratch: vec![0.0; MAX_BLOCK_FRAMES * 2],
            track_idx,
            debug,
            silent_blocks: 0,
            cached_voice_count: 0,
        }
    }

    fn reset(&mut self) {
        self.event_idx = 0;
        self.prev_loop_pos = 0;
        self.audio_idx = 0;
        self.audio_prev_loop_pos = 0;
        self.active_clips.clear();
        for v in self.voices.iter_mut() {
            v.silence();
        }
        self.pending_offs.clear();
        for ev in self.layer_events.iter_mut() {
            ev.clear();
        }
        self.silent_blocks = 0;
    }

    /// Swap the generator layers (rebuild instruments). Called from
    /// `update_params` when the layer config changed.
    fn set_generator_layers(
        &mut self,
        layers: &[GeneratorLayerParams],
        mode: LayerMode,
        sink: &mut crate::plugins::PluginInstanceSink,
        pruned: &mut Vec<crate::plugins::PluginSlotKey>,
    ) {
        if layers == self.generator_layer_params && mode == self.layer_mode {
            return;
        }
        // Choke any sounding notes from the old instruments.
        self.pending_offs.clear();
        // The old instruments are dropped; their registry entries are
        // stale (the replacements are fresh: params + defaults describe
        // them fully, so no live state is lost).
        let old_layers = self.generator_layer_params.len();
        for li in 0..old_layers.max(layers.len()) {
            pruned.push(crate::plugins::PluginSlotKey::Instrument {
                track: self.track_idx,
                layer: li,
            });
        }
        let track_idx = self.track_idx;
        self.generator_layers = layers
            .iter()
            .enumerate()
            .filter_map(|(li, l)| {
                let path = std::path::Path::new(&l.generator.path);
                if l.generator.is_vst3 {
                    HostedVst3Instrument::load(path, self.sample_rate, &l.generator.params)
                        .ok()
                        .map(|plugin| HostedGeneratorLayer {
                            plugin: HostedInstrument::Vst3(plugin),
                            gain: l.gain,
                            pitch_offset: l.pitch_offset,
                            enabled: l.enabled,
                        })
                } else {
                    HostedClapInstrument::load(
                        path,
                        &l.generator.plugin_id,
                        self.sample_rate,
                        &l.generator.params,
                        l.generator.state.as_deref(),
                        Some(crate::plugins::PluginSlotKey::Instrument {
                            track: track_idx,
                            layer: li,
                        }),
                        &sink.events,
                    )
                    .ok()
                    .map(|(plugin, instance)| {
                        sink.push(
                            crate::plugins::PluginSlotKey::Instrument {
                                track: track_idx,
                                layer: li,
                            },
                            instance,
                        );
                        HostedGeneratorLayer {
                            plugin: HostedInstrument::Clap(plugin),
                            gain: l.gain,
                            pitch_offset: l.pitch_offset,
                            enabled: l.enabled,
                        }
                    })
                }
            })
            .collect();
        self.generator_layer_params = layers.to_vec();
        self.layer_mode = mode;
        self.rr_idx = 0;
    }

    /// Queue a generator parameter change (no rebuild).
    /// Applies to all layers (automation targets the track's generators).
    fn set_generator_param(&mut self, id: u32, value: f64) {
        for layer in self.generator_layers.iter_mut() {
            layer.plugin.queue_param(id, value);
        }
        // Update params in all layer specs.
        for gp in self.generator_layer_params.iter_mut() {
            if let Some(slot) = gp.generator.params.iter_mut().find(|(pid, _)| *pid == id) {
                slot.1 = value;
            } else {
                gp.generator.params.push((id, value));
            }
        }
    }

    fn trigger(&mut self, ev_sample: u64, instrument: Instrument, pitch: u8,
               velocity: f64, note_len_secs: f64, pan: f32) {
        // Prefer a silent voice so sustained notes aren't cut; fall back to
        // round-robin stealing only when every voice is busy.
        let idx = self
            .voices
            .iter()
            .position(|v| !v.is_active())
            .unwrap_or_else(|| {
                let i = self.next_voice % VOICES_PER_TRACK;
                self.next_voice = self.next_voice.wrapping_add(1);
                i
            });
        let seed = ev_sample.wrapping_mul(0x9E37_79B9_7F4A_7C15);
        let (vt, vtm) = (self.vel_track, self.vel_track_mid);
        let (kt, ktm) = (self.key_track, self.key_track_mid);
        self.voices[idx].trigger(
            instrument, pitch, velocity, note_len_secs, seed, vt, vtm, kt,
            ktm, pan,
        );
    }

    /// Render `frames` stereo frames into `out` (must hold at least
    /// frames*2; only the prefix is touched, so stale scratch data can
    /// never be re-processed by the FX chain).
    ///
    /// Audio clips mix into the track buffer *after* the voice/generator
    /// path, i.e. pre-fader: clip gain/pan are per-instance (FL Clip
    /// Properties), and the track's gain/pan/FX then apply to the mix of
    /// notes and clips exactly like FL Studio's channel strip.
    fn render(
        &mut self,
        events: &[TrackEvent],
        audio: &[AudioClipEvent],
        loop_samples: u64,
        abs_start: u64,
        frames: usize,
        out: &mut [f32],
    ) {
        let out = &mut out[..frames * 2];
        if !self.generator_layers.is_empty() {
            self.render_generator(events, loop_samples, abs_start, frames, out);
        } else {
            self.render_voices(events, loop_samples, abs_start, frames, out);
        }
        self.render_audio_clips(audio, loop_samples, abs_start, frames, out);
        // Debug: active voice/note count for this block.
        let active = if !self.generator_layers.is_empty() {
            // Generator: sounding notes = scheduled but not yet off/choked.
            self.pending_offs.len() as u32
        } else {
            self.voices.iter().filter(|v| v.is_active()).count() as u32
        };
        self.debug.set_voices(self.track_idx, active);
        self.cached_voice_count = active;
    }

    /// Smart Disable check: returns true if the FX chain should be skipped.
    /// Called AFTER sends are applied (so received sends prevent disabling).
    fn should_skip_fx(&mut self, buf: &[f32]) -> bool {
        // Smart Disable (FL Studio concept): if the voice/generator output
        // was silent and no voices are active, count silent blocks; after
        // the tail window, skip the FX chain entirely.
        // Note: buf includes received sends, so a silent track receiving
        // an active send will NOT be disabled (correct behavior).
        let peak = buf.iter().fold(0.0f32, |a, &b| a.max(b.abs()));
        let active = self.debug_voice_count();
        if peak < SILENCE_THRESHOLD && active == 0 {
            self.silent_blocks += 1;
        } else {
            self.silent_blocks = 0;
        }
        self.silent_blocks > SILENT_BLOCKS_TO_DISABLE
    }

    /// Process the FX chain on the buffer (after sends are mixed in).
    /// `sidechain` is the track's sidechain bus for sidechain-capable
    /// effects (Ducker); other effects ignore it.
    fn process_fx(&mut self, out: &mut [f32], sidechain: &[f32]) {
        for fx in self.fx.iter_mut() {
            fx.process(out, Some(sidechain));
        }
    }

    /// Mix this track's audio clips into `out` (pre-fader, after the
    /// voice/generator path).
    ///
    /// Timeline model (LMMS SampleTrack): a clip is a continuous region;
    /// the playhead persists across frames instead of being a discrete
    /// trigger. `loop_pos` is the song position in loop samples; on a
    /// loop wrap the active playheads are discarded and cursors restart
    /// from the wrap point (same as the voice path).
    fn render_audio_clips(
        &mut self,
        clips: &[AudioClipEvent],
        loop_samples: u64,
        abs_start: u64,
        frames: usize,
        out: &mut [f32],
    ) {
        let loop_pos = (abs_start % loop_samples) as u64;
        if loop_pos < self.audio_prev_loop_pos {
            // Loop wrap: discard active playheads and rescan from the
            // wrap point (same as the voice path).
            self.active_clips.clear();
            self.audio_idx = 0;
            while self.audio_idx < clips.len()
                && clips[self.audio_idx].sample + clips[self.audio_idx].play_frames
                    < loop_pos
            {
                self.audio_idx += 1;
            }
        }

        // Spawn clips whose region starts within this block.
        let block_end = loop_pos + frames as u64;
        while self.audio_idx < clips.len() {
            let c = &clips[self.audio_idx];
            if c.sample >= block_end {
                break;
            }
            let end = c.sample + c.play_frames;
            if end > loop_pos && !c.muted {
                // Spawn a playhead (FL "make unique" model: each instance
                // carries its own source window and properties).
                let dir = if c.reversed { -1.0 } else { 1.0 };
                let ratio = c.ratio as f64;
                let src_start = c.offset_frames as f64;
                let fpos = if c.reversed {
                    // Reverse: buffer read starts at the end of the source
                    // window and advances backwards.
                    (src_start + c.play_frames as f64 * ratio - 1.0).max(0.0)
                } else {
                    src_start
                };
                let (gain_l, gain_r) = pan_gains(c.gain, c.pan);
                let first_frame = c.sample.max(loop_pos);
                let skip = (first_frame - c.sample) as f64 * ratio;
                self.active_clips.push(ClipPlayhead {
                    buffer: c.buffer.clone(),
                    fpos: fpos + dir * skip,
                    step: dir * ratio,
                    remaining: (end - first_frame) as u64,
                    gain_l,
                    gain_r,
                });
            }
            self.audio_idx += 1;
        }

        // Mix active playheads.
        for i in 0..frames {
            let mut k = 0;
            while k < self.active_clips.len() {
                let (l, r) = {
                    let p = &self.active_clips[k];
                    sample_at(&p.buffer, p.fpos)
                };
                out[2 * i] += l * self.active_clips[k].gain_l;
                out[2 * i + 1] += r * self.active_clips[k].gain_r;
                let p = &mut self.active_clips[k];
                p.fpos += p.step;
                p.remaining -= 1;
                if p.remaining == 0 {
                    self.active_clips.swap_remove(k);
                } else {
                    k += 1;
                }
            }
        }
        self.audio_prev_loop_pos = block_end;
    }

    /// Total FX latency in stereo frames (for PDC). Sum of all effect latencies.
    fn fx_latency(&self) -> u32 {
        self.fx.iter().map(|fx| fx.latency_samples()).sum()
    }

    /// Get the cached voice count for Smart Disable.
    fn debug_voice_count(&self) -> u32 {
        // We need to track this; for now, use the debug state.
        // Actually, let's store it in the strip.
        self.cached_voice_count
    }

    /// Built-in voice bank path (original behavior).
    fn render_voices(
        &mut self,
        events: &[TrackEvent],
        loop_samples: u64,
        abs_start: u64,
        frames: usize,
        out: &mut [f32],
    ) {
        for i in 0..frames {
            let abs = abs_start + i as u64;
            let loop_pos = abs % loop_samples;
            if loop_pos < self.prev_loop_pos {
                self.event_idx = 0; // wrapped around the arrangement loop
            }
            self.prev_loop_pos = loop_pos;

            while self.event_idx < events.len() && events[self.event_idx].sample <= loop_pos {
                let ev = &events[self.event_idx];
                if !self.muted {
                    let len_secs = ev.len_samples as f64 / self.sample_rate as f64;
                    self.trigger(abs, ev.instrument, ev.pitch, ev.velocity, len_secs, ev.pan);
                    self.debug.log_event(DebugEvent {
                        track: self.track_idx as u16,
                        kind: DebugEventKind::On,
                        key: ev.pitch.min(127),
                        note_id: -1,
                        sample: abs,
                    });
                }
                self.event_idx += 1;
            }

            if self.muted {
                out[2 * i] = 0.0;
                out[2 * i + 1] = 0.0;
                continue;
            }
            // Per-note pan: each voice carries its own constant-power
            // pan gains (set at trigger from the note's pan), summed in
            // stereo before the track fader. Note pan therefore composes
            // with track pan, like FL Studio's note/channel pan layering.
            let mut l = 0.0f32;
            let mut r = 0.0f32;
            for v in self.voices.iter_mut() {
                let s = v.process();
                let (pl, pr) = v.pan_gains();
                l += s * pl;
                r += s * pr;
            }
            // Pre-fader: the track gain/pan stage is applied separately
            // (apply_fader) so pre-fader send taps can read this buffer.
            out[2 * i] = l;
            out[2 * i + 1] = r;
        }
    }

    /// Track gain/pan ("fader") stage, applied after rendering and after
    /// the pre-fader tap snapshot. Kept separate from `render` so sends
    /// can tap the signal before or after it (FL Studio's pre/post
    /// distinction). Note: unlike FL's post-FX fader, Pulsegrid's fader
    /// sits pre-FX; the POST send tap is post-FX (post everything).
    fn apply_fader(&mut self, buf: &mut [f32]) {
        debug_assert!(buf.len() % 2 == 0);
        for i in (0..buf.len()).step_by(2) {
            buf[i] *= self.gain_l;
            buf[i + 1] *= self.gain_r;
        }
    }

    /// Plugin generator path with layering: note events fan out to
    /// generator layers (FL Layer-style), each layer renders, outputs mixed.
    fn render_generator(
        &mut self,
        events: &[TrackEvent],
        loop_samples: u64,
        abs_start: u64,
        frames: usize,
        out: &mut [f32],
    ) {
        let n_layers = self.generator_layers.len();
        // Ensure per-layer event buffers.
        if self.layer_events.len() != n_layers {
            self.layer_events = vec![Vec::with_capacity(64); n_layers];
        } else {
            for ev in self.layer_events.iter_mut() {
                ev.clear();
            }
        }

        // Helper: which layers does a note go to? Returns Vec of layer indices.
        // (Defined inline via closure-like logic below.)

        for i in 0..frames {
            let abs = abs_start + i as u64;
            let loop_pos = abs % loop_samples;
            if loop_pos < self.prev_loop_pos {
                self.event_idx = 0; // wrapped around the arrangement loop
                // Loop wrap: choke all sounding notes on all layers.
                for (_sample, note_id, key, layer_idx) in self.pending_offs.drain(..) {
                    if layer_idx < self.layer_events.len() {
                        self.layer_events[layer_idx].push(InstrumentNoteEvent::Choke {
                            offset: i as u32,
                            key,
                            note_id,
                        });
                    }
                    self.debug.log_event(DebugEvent {
                        track: self.track_idx as u16,
                        kind: DebugEventKind::Choke,
                        key,
                        note_id,
                        sample: abs,
                    });
                }
            }
            self.prev_loop_pos = loop_pos;

            while self.event_idx < events.len() && events[self.event_idx].sample <= loop_pos {
                let ev = &events[self.event_idx];
                if !self.muted {
                    let note_id = self.next_note_id;
                    self.next_note_id = self.next_note_id.wrapping_add(1);

                    // Determine target layers based on mode.
                    let enabled: Vec<usize> = self.generator_layers
                        .iter()
                        .enumerate()
                        .filter(|(_, l)| l.enabled)
                        .map(|(idx, _)| idx)
                        .collect();
                    let targets: Vec<usize> = match self.layer_mode {
                        LayerMode::All => enabled,
                        LayerMode::Random => {
                            if enabled.is_empty() {
                                Vec::new()
                            } else {
                                // Simple deterministic pseudo-random from note_id.
                                let idx = (note_id as usize).wrapping_mul(6364136223846793005)
                                    % enabled.len();
                                vec![enabled[idx]]
                            }
                        }
                        LayerMode::Sequential => {
                            if enabled.is_empty() {
                                Vec::new()
                            } else {
                                let idx = enabled[self.rr_idx % enabled.len()];
                                self.rr_idx = self.rr_idx.wrapping_add(1);
                                vec![idx]
                            }
                        }
                    };

                    for &layer_idx in targets.iter() {
                        let layer = &self.generator_layers[layer_idx];
                        // Apply pitch offset (clamp to MIDI range).
                        let key = (ev.pitch as i32 + layer.pitch_offset)
                            .clamp(0, 127) as u8;
                        // NOTE: per-note pan (ev.pan) is intentionally NOT
                        // forwarded to CLAP instruments: CLAP note events
                        // carry no standard per-note pan, and our hosted
                        // path only sends note on/off (+params). This
                        // mirrors FL Studio's documented "plugins have to
                        // support it" limitation for note.pan - the value
                        // is stored on the note regardless.
                        self.layer_events[layer_idx].push(InstrumentNoteEvent::On {
                            offset: i as u32,
                            key,
                            velocity: ev.velocity.clamp(0.0, 1.0),
                            note_id,
                        });
                        // Schedule note-off for this layer.
                        let off_abs = abs
                            .saturating_sub(loop_pos)
                            .saturating_add(ev.sample)
                            .saturating_add(ev.len_samples.max(1));
                        self.pending_offs.push((off_abs, note_id, key, layer_idx));
                    }
                    self.debug.log_event(DebugEvent {
                        track: self.track_idx as u16,
                        kind: DebugEventKind::On,
                        key: ev.pitch.min(127),
                        note_id,
                        sample: abs,
                    });
                }
                self.event_idx += 1;
            }

            // Fire due note-offs (to their specific layers).
            let mut fired = Vec::new();
            for (idx, (off_abs, note_id, key, layer_idx)) in
                self.pending_offs.iter().enumerate()
            {
                if *off_abs <= abs {
                    if *layer_idx < self.layer_events.len() {
                        self.layer_events[*layer_idx].push(InstrumentNoteEvent::Off {
                            offset: i as u32,
                            key: *key,
                            velocity: 0.0,
                            note_id: *note_id,
                        });
                    }
                    self.debug.log_event(DebugEvent {
                        track: self.track_idx as u16,
                        kind: DebugEventKind::Off,
                        key: *key,
                        note_id: *note_id,
                        sample: abs,
                    });
                    fired.push(idx);
                }
            }
            for idx in fired.into_iter().rev() {
                self.pending_offs.remove(idx);
            }
        }

        if self.muted {
            for s in out.iter_mut() {
                *s = 0.0;
            }
            for ev in self.layer_events.iter_mut() {
                ev.clear();
            }
            self.pending_offs.clear();
            return;
        }

        // Render each layer and mix with per-layer gain.
        for s in out.iter_mut() {
            *s = 0.0;
        }
        let n = frames * 2;
        for (layer_idx, layer) in self.generator_layers.iter_mut().enumerate() {
            let notes = std::mem::take(&mut self.layer_events[layer_idx]);
            // Render to scratch, then mix with layer gain.
            let scratch = &mut self.layer_scratch[..n];
            for s in scratch.iter_mut() {
                *s = 0.0;
            }
            layer.plugin.process_notes(&notes, scratch);
            let lg = layer.gain;
            for i in 0..n {
                out[i] += scratch[i] * lg;
            }
        }
        // Pre-fader: the track gain/pan stage is applied separately
        // (apply_fader) so pre-fader send taps can read this buffer.
    }

    /// Silence all voices immediately (used on mute transitions).
    fn silence_voices(&mut self) {
        for v in self.voices.iter_mut() {
            v.silence();
        }
    }

    /// Apply automation curves for one block. Gain/pan curves override
    /// the static mix values; FX curves live-update the effect instances
    /// (the validated `fx_params` stay untouched as the base values).
    /// `beat` is the arrangement beat at the block start.
    fn apply_automation(&mut self, curves: &[AutoCurve], beat: f64) {
        if curves.is_empty() {
            return;
        }
        let mut gain = self.base_gain;
        let mut pan = self.base_pan;
        for c in curves {
            match c.param {
                AutoParam::Gain => {
                    gain = c.value_at(beat, self.base_gain as f64) as f32;
                }
                AutoParam::Pan => {
                    pan = c.value_at(beat, self.base_pan as f64) as f32;
                }
                AutoParam::Fx { index, param } => {
                    let base = self
                        .fx_params
                        .get(index)
                        .and_then(|p| p.param_value(param));
                    if let (Some(base), Some(fx)) = (base, self.fx.get_mut(index)) {
                        let v = c.value_at(beat, base as f64) as f32;
                        // Values were range-checked at Song build time;
                        // ignore the error defensively (never panic on the
                        // audio thread).
                        let _ = fx.set_param(param, v, self.sample_rate);
                    }
                }
                AutoParam::Generator { param } => {
                    // Base is the first layer's recorded static value.
                    let base = self
                        .generator_layer_params
                        .first()
                        .and_then(|l| {
                            l.generator.params.iter().find(|(id, _)| *id == param)
                        })
                        .map(|(_, v)| *v);
                    if let Some(base) = base {
                        let v = c.value_at(beat, base);
                        // Queued as a CLAP param event; no rebuild, the
                        // plugin keeps its DSP state.
                        self.set_generator_param(param, v);
                    }
                }
                AutoParam::Send { .. } => {
                    // Send amounts are resolved by the graph's
                    // send-mixing pass (per edge, per block), not by the
                    // strip. Nothing to do here.
                }
            }
        }
        let (gl, gr) = pan_gains(gain, pan);
        self.gain_l = gl;
        self.gain_r = gr;
    }
}

/// A simple delay line for Plugin Delay Compensation.
/// The public API is in stereo FRAMES (CLAP reports latency in frames;
/// the ring itself works on interleaved scalars, so one frame = two
/// scalar positions). PDC doesn't make slow plugins faster -- it delays
/// fast paths so all tracks arrive at the Master simultaneously
/// (FL Studio-style).
struct PdcDelay {
    buf: Vec<f32>,
    pos: usize,
    /// Delay in interleaved scalar samples (= frames * 2).
    delay: usize,
}

impl PdcDelay {
    fn new_frames(delay_frames: usize) -> Self {
        let mut d = PdcDelay {
            buf: Vec::new(),
            pos: 0,
            delay: 0,
        };
        d.set_delay_frames(delay_frames);
        d
    }

    /// Process a block: output = input delayed by the configured frames.
    /// `io` is modified in-place (interleaved stereo).
    fn process(&mut self, io: &mut [f32]) {
        if self.delay == 0 {
            return;
        }
        let n = io.len();
        let buf_len = self.buf.len();
        for i in 0..n {
            // Write current input to ring.
            self.buf[self.pos] = io[i];
            // Read delayed sample.
            let read_pos = (self.pos + buf_len - self.delay) % buf_len;
            io[i] = self.buf[read_pos];
            self.pos = (self.pos + 1) % buf_len;
        }
    }

    /// Resize the delay (when PDC is recalculated). Clears the buffer
    /// to avoid stale audio.
    fn set_delay_frames(&mut self, delay_frames: usize) {
        let delay = delay_frames * 2;
        if delay != self.delay {
            self.delay = delay;
            // Buffer needs delay + max block size for the ring.
            let size = delay + MAX_BLOCK_FRAMES * 2;
            self.buf = vec![0.0; size.max(1)];
            self.pos = 0;
        }
    }
}

/// One resolved inter-track route (parallel send or exclusive output).
#[derive(Clone, Copy, Debug)]
struct SendEdge {
    dst: usize,
    /// Linear 0-1 edge gain (automatable for parallel sends).
    amount: f32,
    /// Stereo position of the sent signal, -1..1.
    pan: f32,
    tap: SendTap,
    /// Sidechain send: feeds the destination's sidechain bus instead
    /// of its audible path (FL "sidechain to this track").
    sidechain: bool,
    /// True for exclusive output routes (amount is fixed 1.0 and the
    /// edge can never be automated).
    is_output_route: bool,
}

/// PDC plan in stereo frames, computed purely from FX latencies and the
/// routing DAG (FL Studio applies PDC to inter-track routing AND to
/// sidechains -- the detector path is part of the latency graph).
struct PdcPlan {
    /// Per-track input alignment: delay the track's own voice content
    /// by this much so it meets incoming routed audio on the same
    /// timeline. A track with latent senders must wait for them.
    input_align: Vec<u32>,
    /// Per-track output delay applied post-FX, pre-master.
    final_delay: Vec<u32>,
    /// Slowest total path in the graph.
    max_latency: u32,
    /// Per-sidechain-edge detector delay, in the same order as the
    /// `sc_edges` passed in. Delays the sender's tap signal so the
    /// detector transient meets the destination's program signal at
    /// the sidechain consumer's input.
    sc_delay: Vec<u32>,
}

/// Compute the PDC plan from per-track FX latencies (stereo frames),
/// the routing DAG's audible edges, and its sidechain edges.
///
/// Audible edges propagate latency as before:
///   input_align[dst] = max over audible senders (input_align[src] + fx_lat[src])
///
/// Sidechain edges are inaudible, so they never shift the
/// destination's timing *directly* -- but FL Studio's APDC explicitly
/// covers sidechains: a sidechain consumer's detector input and
/// program input must be temporally equivalent. Two mechanisms do that:
///
/// 1. When the detector path is SLOWER than the program path at the
///    consumer, the destination's input alignment is raised so its
///    program waits for the detector. The raise propagates through
///    the audible DAG like any other latency, so the Master stays
///    aligned (final_delay is recomputed from the raised totals).
/// 2. The residual per-edge difference is applied as a delay line on
///    the SC-bus feed itself. It is per-edge (not per-bus) because
///    senders can have different path latencies.
///
/// `sc_consumer_pre[dst]` is the latency of the FX *before* the first
/// sidechain-consuming FX on dst (0 when the track has no consumer);
/// `sc_edges` are (src, dst, post_tap) with post_tap selecting the
/// full source FX latency as the tap point.
///
/// The union of audible + sidechain edges is a DAG (project validation
/// rejects cycles across all sends), so a single topological pass
/// settles every input_align: each sender is finalized before its
/// destinations, with no fixpoint iteration.
fn compute_pdc_plan(
    n_tracks: usize,
    fx_latency: &[u32],
    aud_edges: &[(usize, usize)],
    sc_edges: &[(usize, usize, bool)],
    sc_consumer_pre: &[u32],
    has_sc_consumer: &[bool],
) -> PdcPlan {
    // Kahn's algorithm over the union graph (sends form a DAG via
    // cycle detection).
    let mut in_degree = vec![0usize; n_tracks];
    for &(_, dst) in aud_edges {
        in_degree[dst] += 1;
    }
    for &(_, dst, _) in sc_edges {
        in_degree[dst] += 1;
    }
    let mut queue: Vec<usize> =
        (0..n_tracks).filter(|&i| in_degree[i] == 0).collect();
    let mut order = Vec::with_capacity(n_tracks);
    while let Some(idx) = queue.pop() {
        order.push(idx);
        for &(src, dst) in aud_edges {
            if src == idx {
                in_degree[dst] -= 1;
                if in_degree[dst] == 0 {
                    queue.push(dst);
                }
            }
        }
        for &(src, dst, _) in sc_edges {
            if src == idx {
                in_degree[dst] -= 1;
                if in_degree[dst] == 0 {
                    queue.push(dst);
                }
            }
        }
    }
    for i in 0..n_tracks {
        if !order.contains(&i) {
            order.push(i); // cycle fallback (shouldn't happen)
        }
    }
    let mut input_align = vec![0u32; n_tracks];
    for &dst in order.iter() {
        let mut best = 0u32;
        for &(src, d) in aud_edges {
            if d == dst {
                best = best.max(input_align[src] + fx_latency[src]);
            }
        }
        if has_sc_consumer[dst] {
            let pre = sc_consumer_pre[dst];
            for &(src, d, post_tap) in sc_edges {
                if d == dst {
                    let tap_lat = if post_tap { fx_latency[src] } else { 0 };
                    let sc_arrival = input_align[src] + tap_lat;
                    // Program arrival at the consumer =
                    // input_align[dst] + pre. Raise input_align[dst]
                    // when the detector is slower.
                    if sc_arrival > pre {
                        best = best.max(sc_arrival - pre);
                    }
                }
            }
        }
        input_align[dst] = best;
    }
    let totals: Vec<u32> = input_align
        .iter()
        .zip(fx_latency.iter())
        .map(|(a, f)| a + f)
        .collect();
    let max_latency = totals.iter().copied().max().unwrap_or(0);
    let final_delay: Vec<u32> =
        totals.iter().map(|t| max_latency - t).collect();
    // Every path through the graph -- direct, routed, or sidechain --
    // then arrives at the Master delayed by exactly max_total frames,
    // and each detector meets its program at the consumer's input.
    let sc_delay: Vec<u32> = sc_edges
        .iter()
        .map(|&(src, dst, post_tap)| {
            if !has_sc_consumer[dst] {
                return 0;
            }
            let tap_lat = if post_tap { fx_latency[src] } else { 0 };
            let prog = input_align[dst] + sc_consumer_pre[dst];
            let sc = input_align[src] + tap_lat;
            prog.saturating_sub(sc)
        })
        .collect();
    PdcPlan {
        input_align,
        final_delay,
        max_latency,
        sc_delay,
    }
}

/// The mix graph. Owns all per-track strips and scratch buffers.
pub struct Graph {
    strips: Vec<TrackStrip>,
    debug: SharedDebugState,
    master_gain: f32,
    strip_buf: Vec<f32>,
    mix_buf: Vec<f32>,
    /// Per-strip working buffers (post-fader, pre-FX at Phase 1 end;
    /// post-FX after Phase 2). send_bufs[i] holds strip i's output.
    send_bufs: Vec<Vec<f32>>,
    /// Per-strip pre-fader tap snapshots (post input-alignment, pre
    /// gain/pan). Pre-tap sends read these; post-tap sends read the
    /// post-FX buffers instead (FL pre/post-fader send semantics).
    tap_bufs: Vec<Vec<f32>>,
    /// Per-strip sidechain buses. Sidechain sends mix into these;
    /// sidechain-capable effects (Ducker) read them. Never audible.
    sc_bufs: Vec<Vec<f32>>,
    /// Per-sidechain-edge detector delay lines, parallel to
    /// `sends_from` (only sidechain edges use their slot). Each
    /// sender's tap is delayed so its transient meets the
    /// destination's program signal at the sidechain consumer's input
    /// (FL Studio APDC covers sidechains).
    sc_delays: Vec<Vec<PdcDelay>>,
    /// Scratch buffer for per-edge sidechain delay processing (avoids
    /// allocation in the render hot path).
    sc_scratch: Vec<f32>,
    /// Send routes: sends_from[i] = [SendEdge].
    /// This INCLUDES exclusive output routes (merged as fixed edges),
    /// so topo order, PDC latency and send mixing all treat them uniformly.
    sends_from: Vec<Vec<SendEdge>>,
    /// Exclusive output routes: output_routes[i] = Some(dst) means track i
    /// is routed to track dst ONLY (FL "route to this track only") and does
    /// NOT reach the Master directly. None = route to Master (default).
    automation: Vec<Vec<AutoCurve>>,
    sample_rate: u32,
    samples_per_beat: f64,
    loop_beats: f64,
    output_routes: Vec<Option<usize>>,
    /// Multithreading enabled (FL-style global switch).
    /// When true, Phase 1 (voice rendering) uses rayon thread pool.
    pub multithreaded: bool,
    /// Plugin Delay Compensation: per-track output delay lines (post-FX,
    /// pre-master), driven by the PDC plan's final_delay.
    pdc_delays: Vec<PdcDelay>,
    /// Plugin Delay Compensation: per-track input alignment delay lines
    /// (pre-fader), driven by the PDC plan's input_align.
    input_pdc: Vec<PdcDelay>,
    /// Per-track total latency in stereo frames (for UI display).
    track_latencies: Vec<u32>,
}

/// Merge parallel sends and exclusive output routes into one edge list.
///
/// Output routes (FL "route to this track only") become amount-1.0 edges so
/// topological order, PDC latency and the send-mixing pass treat them like
/// any other inter-track route. Out-of-range or self routes are dropped
/// (Python validates; this is a backstop).
fn build_routes(
    sends: &[Vec<SendData>],
    outputs: &[Option<usize>],
    n_tracks: usize,
) -> (Vec<Vec<SendEdge>>, Vec<Option<usize>>) {
    let mut sends_from: Vec<Vec<SendEdge>> = sends
        .iter()
        .map(|v| {
            v.iter()
                .filter(|s| s.to_track < n_tracks)
                .map(|s| SendEdge {
                    dst: s.to_track,
                    amount: s.amount.clamp(0.0, 1.0),
                    pan: s.pan,
                    tap: s.tap,
                    sidechain: s.sidechain,
                    is_output_route: false,
                })
                .collect()
        })
        .collect();
    // Pad in case the caller passes fewer entries than tracks.
    sends_from.resize_with(n_tracks, Vec::new);
    let mut output_routes = vec![None; n_tracks];
    for (i, out) in outputs.iter().enumerate().take(n_tracks) {
        if let Some(dst) = out {
            if *dst < n_tracks && *dst != i {
                // Exclusive output route: fixed post-tap edge, never
                // automated, no per-send pan (pan lives on the track).
                sends_from[i].push(SendEdge {
                    dst: *dst,
                    amount: 1.0,
                    pan: 0.0,
                    tap: SendTap::Post,
                    sidechain: false,
                    is_output_route: true,
                });
                output_routes[i] = Some(*dst);
            }
        }
    }
    (sends_from, output_routes)
}

impl Graph {
    pub fn new(
        sample_rate: u32,
        track_params: &[TrackParams],
        track_fx: &[Vec<FxParams>],
        track_layers: &[Vec<GeneratorLayerParams>],
        track_layer_modes: &[LayerMode],
        automation: &[Vec<AutoCurve>],
        sends: &[Vec<SendData>],
        outputs: &[Option<usize>],
        tempo: f64,
        loop_samples: u64,
        debug: SharedDebugState,
        sink: &mut crate::plugins::PluginInstanceSink,
    ) -> Self {
        let strips = track_params
            .iter()
            .zip(track_fx.iter())
            .zip(track_layers.iter())
            .zip(track_layer_modes.iter())
            .enumerate()
            .map(|(i, (((p, fx), layers), mode))| {
                TrackStrip::new(sample_rate, *p, fx, layers, *mode, i, debug.clone(), sink)
            })
            .collect::<Vec<_>>();
        let n_tracks = strips.len();
        let samples_per_beat = 60.0 / tempo * sample_rate as f64;
        let (sends_from, output_routes) = build_routes(sends, outputs, n_tracks);
        let mut graph = Graph {
            strips,
            debug,
            master_gain: 0.85,
            strip_buf: vec![0.0; MAX_BLOCK_FRAMES * 2],
            mix_buf: vec![0.0; MAX_BLOCK_FRAMES * 2],
            send_bufs: vec![vec![0.0; MAX_BLOCK_FRAMES * 2]; n_tracks],
            tap_bufs: vec![vec![0.0; MAX_BLOCK_FRAMES * 2]; n_tracks],
            sc_bufs: vec![vec![0.0; MAX_BLOCK_FRAMES * 2]; n_tracks],
            sc_delays: Vec::new(),
            sc_scratch: vec![0.0; MAX_BLOCK_FRAMES * 2],
            sends_from,
            output_routes,
            automation: automation.to_vec(),
            sample_rate,
            samples_per_beat,
            loop_beats: loop_samples as f64 / samples_per_beat,
            multithreaded: true,
            pdc_delays: Vec::new(),
            input_pdc: Vec::new(),
            track_latencies: vec![0; n_tracks],
        };
        graph.recalc_pdc();
        graph
    }

    /// Recalculate Plugin Delay Compensation.
    /// Computes the PDC plan (input alignment + output delays + per-edge
    /// sidechain detector delays, in stereo frames) from per-track FX
    /// latency and the routing DAG, then applies it to the per-track
    /// delay lines. FL Studio-style: PDC covers inter-track routing AND
    /// sidechains, so a latent sender cannot push its destination's own
    /// content early at the Master, and a detector transient always
    /// meets its program signal at the sidechain consumer's input.
    fn recalc_pdc(&mut self) {
        let n = self.strips.len();
        let fx_lat: Vec<u32> =
            self.strips.iter().map(|s| s.fx_latency()).collect();
        // Latency of the FX *before* the first sidechain-consuming FX
        // on each track (the program-path latency at the consumer).
        let mut sc_consumer_pre = vec![0u32; n];
        let mut has_sc_consumer = vec![false; n];
        for (i, strip) in self.strips.iter().enumerate() {
            let mut pre = 0u32;
            for fx in strip.fx.iter() {
                if fx.uses_sidechain() {
                    has_sc_consumer[i] = true;
                    break;
                }
                pre += fx.latency_samples();
            }
            sc_consumer_pre[i] = pre;
        }
        // Split edges: audible edges drive input alignment directly;
        // sidechain edges are inaudible (they must not inflate the
        // destination's input alignment by themselves) but they still
        // order the topo sort -- the SC bus must be filled before the
        // destination's FX runs -- and they feed the detector-alignment
        // computation.
        let mut aud_edges: Vec<(usize, usize)> = Vec::new();
        let mut sc_plan_edges: Vec<(usize, usize, bool)> = Vec::new();
        // (src, edge_idx) for each sidechain edge, parallel to
        // sc_plan_edges, so plan delays map back onto sc_delays.
        let mut sc_apply_idx: Vec<(usize, usize)> = Vec::new();
        for (src, routes) in self.sends_from.iter().enumerate() {
            for (ei, e) in routes.iter().enumerate() {
                if e.sidechain {
                    sc_plan_edges.push((src, e.dst, e.tap == SendTap::Post));
                    sc_apply_idx.push((src, ei));
                } else {
                    aud_edges.push((src, e.dst));
                }
            }
        }
        let plan = compute_pdc_plan(
            n,
            &fx_lat,
            &aud_edges,
            &sc_plan_edges,
            &sc_consumer_pre,
            &has_sc_consumer,
        );
        if self.pdc_delays.len() != n {
            self.pdc_delays =
                (0..n).map(|_| PdcDelay::new_frames(0)).collect();
        }
        if self.input_pdc.len() != n {
            self.input_pdc =
                (0..n).map(|_| PdcDelay::new_frames(0)).collect();
        }
        if self.sc_delays.len() != n {
            self.sc_delays = (0..n).map(|_| Vec::new()).collect();
        }
        for (src, routes) in self.sends_from.iter().enumerate() {
            if self.sc_delays[src].len() != routes.len() {
                self.sc_delays[src] = (0..routes.len())
                    .map(|_| PdcDelay::new_frames(0))
                    .collect();
            }
        }
        for (i, delay) in self.pdc_delays.iter_mut().enumerate() {
            delay.set_delay_frames(plan.final_delay[i] as usize);
        }
        for (i, delay) in self.input_pdc.iter_mut().enumerate() {
            delay.set_delay_frames(plan.input_align[i] as usize);
        }
        for (i, &(src, ei)) in sc_apply_idx.iter().enumerate() {
            self.sc_delays[src][ei]
                .set_delay_frames(plan.sc_delay[i] as usize);
        }
        self.track_latencies = plan
            .input_align
            .iter()
            .zip(fx_lat.iter())
            .map(|(a, f)| a + f)
            .collect();
    }

    /// Get per-track total latencies in stereo frames (for UI display).
    pub fn track_latencies(&self) -> &[u32] {
        &self.track_latencies
    }

    pub fn track_count(&self) -> usize {
        self.strips.len()
    }

    /// Topological order for send processing (Kahn's algorithm).
    /// Sends form a DAG (cycle detection at validation), so this always
    /// succeeds. Sources come before destinations.
    fn topo_order(&self) -> Vec<usize> {
        let n = self.strips.len();
        let mut in_degree = vec![0usize; n];
        for routes in self.sends_from.iter() {
            for e in routes.iter() {
                in_degree[e.dst] += 1;
            }
        }
        let mut queue: Vec<usize> = (0..n)
            .filter(|&i| in_degree[i] == 0)
            .collect();
        let mut order = Vec::with_capacity(n);
        while let Some(idx) = queue.pop() {
            order.push(idx);
            for e in self.sends_from[idx].iter() {
                in_degree[e.dst] -= 1;
                if in_degree[e.dst] == 0 {
                    queue.push(e.dst);
                }
            }
        }
        // If there's a cycle (shouldn't happen), append remaining.
        for i in 0..n {
            if !order.contains(&i) {
                order.push(i);
            }
        }
        order
    }

    /// Update per-track gain/pan/mute without touching voices (no clicks).
    /// Effect chains rebuild only when their params actually changed.
    /// Rebuilt plugin slots are reported in `pruned` (their registry
    /// entries are stale); fresh instances are pushed to `sink`.
    pub fn update_params(
        &mut self,
        track_params: &[TrackParams],
        track_fx: &[Vec<FxParams>],
        track_layers: &[Vec<GeneratorLayerParams>],
        track_layer_modes: &[LayerMode],
        automation: &[Vec<AutoCurve>],
        sends: &[Vec<SendData>],
        outputs: &[Option<usize>],
        tempo: f64,
        loop_samples: u64,
        sink: &mut crate::plugins::PluginInstanceSink,
        pruned: &mut Vec<crate::plugins::PluginSlotKey>,
    ) {
        for ((((strip, p), fx), layers), mode) in self
            .strips
            .iter_mut()
            .zip(track_params.iter())
            .zip(track_fx.iter())
            .zip(track_layers.iter())
            .zip(track_layer_modes.iter())
        {
            let (gl, gr) = pan_gains(p.gain, p.pan);
            strip.gain_l = gl;
            strip.gain_r = gr;
            strip.base_gain = p.gain;
            strip.base_pan = p.pan;
            strip.vel_track = p.vel_track;
            strip.vel_track_mid = p.vel_track_mid;
            strip.key_track = p.key_track;
            strip.key_track_mid = p.key_track_mid;
            if strip.muted != p.muted {
                strip.muted = p.muted;
                if p.muted {
                    strip.silence_voices();
                }
            }
            if strip.fx_params != *fx {
                if fx_update_in_place(&mut strip.fx, &strip.fx_params, fx) {
                    // Only plugin parameter values changed: queued as
                    // CLAP events, no rebuild (plugin DSP state kept).
                    strip.fx_params = fx.clone();
                } else {
                    // Structural rebuild: prune the track's FX slots
                    // (fresh plugins need no live state).
                    let track = strip.track_idx;
                    for i in 0..strip.fx_params.len().max(fx.len()) {
                        pruned.push(crate::plugins::PluginSlotKey::Fx { track, index: i });
                    }
                    strip.fx = build_fx_chain(fx, strip.sample_rate, strip.track_idx, sink);
                    strip.fx_params = fx.clone();
                }
            }
            // Generator layers: rebuild if layers or mode changed.
            // (Param-only optimization is deferred; rebuild is correct.)
            if strip.generator_layer_params != *layers || strip.layer_mode != *mode {
                strip.set_generator_layers(layers, *mode, sink, pruned);
            }
        }
        self.automation = automation.to_vec();
        let n_tracks = self.strips.len();
        let (sends_from, output_routes) = build_routes(sends, outputs, n_tracks);
        self.sends_from = sends_from;
        self.output_routes = output_routes;
        self.samples_per_beat = 60.0 / tempo * self.sample_rate as f64;
        self.loop_beats = loop_samples as f64 / self.samples_per_beat;
        // FX chains or sends may have changed: recalculate PDC.
        self.recalc_pdc();
    }

    /// Resync event cursors to the current loop position without silencing
    /// voices: events at or before `loop_pos` are skipped (they're in the
    /// past), later events will trigger normally. Audio clips seek into
    /// the source buffer: a clip whose region contains `loop_pos` spawns
    /// a mid-clip playhead (LMMS `SampleTrack::play` offset behavior).
    pub fn sync_cursors(
        &mut self,
        track_events: &[Vec<TrackEvent>],
        track_audio_clips: &[Vec<AudioClipEvent>],
        loop_pos: u64,
    ) {
        for (strip, events) in self.strips.iter_mut().zip(track_events.iter()) {
            strip.prev_loop_pos = loop_pos;
            strip.event_idx = events.partition_point(|e| e.sample <= loop_pos);
        }
        for (strip, clips) in self.strips.iter_mut().zip(track_audio_clips.iter()) {
            strip.audio_prev_loop_pos = loop_pos;
            strip.audio_idx = clips.partition_point(|c| c.sample <= loop_pos);
            // If `loop_pos` is inside a clip region, back up one so the
            // spawn logic seeks into it instead of skipping it.
            if strip.audio_idx > 0 {
                let prev = &clips[strip.audio_idx - 1];
                if prev.sample + prev.play_frames > loop_pos {
                    strip.audio_idx -= 1;
                }
            }
            strip.active_clips.clear();
        }
    }

    pub fn reset(&mut self) {
        for s in self.strips.iter_mut() {
            s.reset();
        }
    }

    /// Render `frames` (<= MAX_BLOCK_FRAMES) stereo frames.
    /// `track_events[i]` and `track_audio_clips[i]` belong to `strips[i]`.
    pub fn render(
        &mut self,
        track_events: &[Vec<TrackEvent>],
        track_audio_clips: &[Vec<AudioClipEvent>],
        loop_samples: u64,
        abs_start: u64,
        out: &mut [f32],
    ) {
        let frames = out.len() / 2;
        debug_assert!(frames <= MAX_BLOCK_FRAMES);
        debug_assert_eq!(track_events.len(), self.strips.len());
        debug_assert_eq!(track_audio_clips.len(), self.strips.len());
        debug_assert_eq!(self.automation.len(), self.strips.len());

        // Automation is evaluated once per block at the block's start beat
        // (~11 ms granularity at 512 frames / 44.1 kHz — plenty smooth for
        // mix moves, and cheap enough to never threaten the audio thread).
        let beat = (abs_start as f64 / self.samples_per_beat) % self.loop_beats;

        let n = frames * 2;
        for x in self.mix_buf[..n].iter_mut() {
            *x = 0.0;
        }
        // Topological render order (sends form a DAG via cycle detection).
        // Each track: voices -> input-align -> tap snapshot -> fader ->
        // mix incoming sends (tapped pre-fader or post-FX from sources)
        // -> FX -> output. This matches LMMS: sends mixed BEFORE
        // destination FX; and FL: post taps are post-source-FX, pre taps
        // are pre-fader, and sidechain sends feed the SC bus.
        let order = self.topo_order();
        let automation = &self.automation;
        // Phase 1: render all voices (no FX yet).
        // Dependency-free: each track's voices are independent.
        // When multithreaded, use rayon thread pool (FL-style parallel
        // generators). Each track is processed by exactly one thread;
        // CLAP instances are per-track so this is safe.
        //
        // Per track: render (pre-fader) -> PDC input alignment -> tap
        // snapshot -> fader. The tap snapshot carries the track's own
        // content on the shared timeline (aligned with incoming sends);
        // the fader is applied after so pre-taps read pre-fader signal.
        if self.multithreaded && self.strips.len() > 1 {
            use rayon::prelude::*;
            // Borrow-split for parallel access.
            let strips = &mut self.strips;
            let send_bufs = &mut self.send_bufs;
            let tap_bufs = &mut self.tap_bufs;
            let input_pdc = &mut self.input_pdc;
            strips
                .par_iter_mut()
                .zip(send_bufs.par_iter_mut())
                .zip(tap_bufs.par_iter_mut())
                .zip(input_pdc.par_iter_mut())
                .enumerate()
                .for_each(|(idx, (((strip, buf), tap), align))| {
                    strip.apply_automation(&automation[idx], beat);
                    strip.render(
                        &track_events[idx],
                        &track_audio_clips[idx],
                        loop_samples,
                        abs_start,
                        frames,
                        &mut buf[..n],
                    );
                    align.process(&mut buf[..n]);
                    tap[..n].copy_from_slice(&buf[..n]);
                    strip.apply_fader(&mut buf[..n]);
                });
        } else {
            for (idx, strip) in self.strips.iter_mut().enumerate() {
                strip.apply_automation(&automation[idx], beat);
                let buf = &mut self.send_bufs[idx][..n];
                strip.render(
                    &track_events[idx],
                    &track_audio_clips[idx],
                    loop_samples,
                    abs_start,
                    frames,
                    buf,
                );
                self.input_pdc[idx].process(buf);
                self.tap_bufs[idx][..n].copy_from_slice(&buf[..n]);
                strip.apply_fader(buf);
            }
        }
        // Phase 2: process in topological order.
        // For each track: mix incoming sends, then run FX.
        // We need post-FX outputs; store them in a separate vec.
        let mut post_fx: Vec<Vec<f32>> =
            vec![vec![0.0; n]; self.strips.len()];
        // Clear sidechain buses (sidechain sends re-fill them).
        for sc in self.sc_bufs.iter_mut() {
            sc[..n].fill(0.0);
        }
        // Resolve per-block send amounts: automation modulates the edge
        // gain, never the topology (the FL/LMMS shared invariant).
        // Output routes are fixed at 1.0 and never automated.
        let mut edges: Vec<Vec<SendEdge>> = self.sends_from.clone();
        for (src, list) in edges.iter_mut().enumerate() {
            for e in list.iter_mut() {
                if e.is_output_route {
                    continue;
                }
                if let Some(curve) = self.automation[src]
                    .iter()
                    .find(|c| c.param == AutoParam::Send { dest: e.dst })
                {
                    e.amount = curve.value_at(beat, e.amount as f64) as f32;
                }
            }
        }
        // Build reverse map: for each dst, list of (src, edge_idx, edge).
        let mut incoming: Vec<Vec<(usize, usize, SendEdge)>> =
            vec![Vec::new(); self.strips.len()];
        for (src, routes) in edges.iter().enumerate() {
            for (ei, e) in routes.iter().enumerate() {
                incoming[e.dst].push((src, ei, *e));
            }
        }
        for idx in order {
            // Mix incoming sends. Sources are already processed
            // (topological order); pre-taps were snapshotted in Phase 1.
            if !incoming[idx].is_empty() {
                // Copy to avoid borrow issues.
                let sends: Vec<(usize, usize, SendEdge)> =
                    incoming[idx].clone();
                // Disjoint field borrows: read taps/post-FX, write
                // send + sidechain buffers.
                let send_bufs = &mut self.send_bufs;
                let tap_bufs = &self.tap_bufs;
                let sc_bufs = &mut self.sc_bufs;
                let sc_delays = &mut self.sc_delays;
                let sc_scratch = &mut self.sc_scratch;
                for (src, ei, e) in sends {
                    let src_sig: &[f32] = match e.tap {
                        SendTap::Pre => &tap_bufs[src][..n],
                        SendTap::Post => &post_fx[src][..n],
                    };
                    let (gl, gr) = pan_gains(1.0, e.pan);
                    if e.sidechain {
                        // Sidechain feed is level-independent (FL
                        // Studio): the edge exists, so the SC bus gets
                        // the signal regardless of `amount`; pan still
                        // applies. Never audible at the destination.
                        //
                        // Detector-path PDC (FL Studio APDC covers
                        // sidechains): the sender's tap is delayed so
                        // its transient meets the destination's program
                        // signal at the sidechain consumer's input.
                        // Per-edge delay: senders can have different
                        // path latencies.
                        let scratch = &mut sc_scratch[..n];
                        scratch.copy_from_slice(src_sig);
                        sc_delays[src][ei].process(scratch);
                        let dst = &mut sc_bufs[idx][..n];
                        for i in (0..n).step_by(2) {
                            dst[i] += scratch[i] * gl;
                            dst[i + 1] += scratch[i + 1] * gr;
                        }
                    } else {
                        let a = e.amount;
                        let dst = &mut send_bufs[idx][..n];
                        for i in (0..n).step_by(2) {
                            dst[i] += src_sig[i] * a * gl;
                            dst[i + 1] += src_sig[i + 1] * a * gr;
                        }
                    }
                }
            }
            // Run FX (with Smart Disable checked AFTER sends).
            // Sidechain-capable effects read the track's SC bus.
            // (Disjoint field borrows: no allocation in the hot path.)
            let strip = &mut self.strips[idx];
            let buf = &mut self.send_bufs[idx][..n];
            let sc = &self.sc_bufs[idx][..n];
            if strip.should_skip_fx(&buf[..n]) {
                buf.fill(0.0);
            } else {
                strip.process_fx(buf, sc);
            }
            // Store post-FX output for downstream sends.
            post_fx[idx][..n].copy_from_slice(&buf[..n]);
        }
        // Phase 2.5: Apply PDC output delay lines (post-FX, pre-master).
        // Each track's output is delayed by the plan's final_delay so all
        // tracks arrive at the Master simultaneously (FL Studio-style).
        for (idx, buf) in self.send_bufs.iter_mut().enumerate() {
            if idx < self.pdc_delays.len() {
                self.pdc_delays[idx].process(&mut buf[..n]);
            }
        }
        // Phase 3: mix to master, record peaks.
        // Note: send_bufs now holds post-FX outputs (after Phase 2).
        // Tracks with an exclusive output route (FL "route to this track
        // only") do NOT reach the Master directly; their audio already
        // flowed into the destination's buffer in Phase 2.
        for (idx, buf) in self.send_bufs.iter().enumerate() {
            let sb = &buf[..n];
            let mut peak = 0.0f32;
            for i in 0..n {
                let a = sb[i].abs();
                if a > peak {
                    peak = a;
                }
            }
            self.debug.record_peak(idx, peak);
            if self.output_routes[idx].is_some() {
                continue;
            }
            let mb = &mut self.mix_buf[..n];
            for i in 0..n {
                mb[i] += sb[i];
            }
        }
        for i in 0..n {
            out[i] = (self.mix_buf[i] * self.master_gain).tanh();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::synth::Instrument;
    use crate::timeline::TrackEvent;

    #[test]
    fn pdc_delay_line_delays_by_exact_frames() {
        // Frame semantics: delay(2 frames) on [1,2,3,4,5,6]
        // -> [0,0,0,0,1,2] (frames, not scalar samples).
        let mut d = PdcDelay::new_frames(2);
        let mut buf = vec![1.0, 2.0, 3.0, 4.0, 5.0, 6.0];
        d.process(&mut buf);
        assert_eq!(buf, vec![0.0, 0.0, 0.0, 0.0, 1.0, 2.0]);
        // Second block continues: [7,8] -> [3,4]
        let mut buf2 = vec![7.0, 8.0];
        d.process(&mut buf2);
        assert_eq!(buf2, vec![3.0, 4.0]);
    }

    #[test]
    fn pdc_delay_zero_is_passthrough() {
        let mut d = PdcDelay::new_frames(0);
        let mut buf = vec![1.0, 2.0, 3.0];
        d.process(&mut buf);
        assert_eq!(buf, vec![1.0, 2.0, 3.0]);
    }

    #[test]
    fn pan_law_endpoints() {
        let (l, r) = pan_gains(1.0, -1.0);
        assert!((l - 1.0).abs() < 1e-6 && r.abs() < 1e-6);
        let (l, r) = pan_gains(1.0, 1.0);
        assert!(l.abs() < 1e-6 && (r - 1.0).abs() < 1e-6);
        let (l, r) = pan_gains(1.0, 0.0);
        assert!((l - r).abs() < 1e-6);
        // constant power: l^2 + r^2 == gain^2
        assert!((l * l + r * r - 1.0).abs() < 1e-6);
    }

    fn debug_state() -> crate::debug::SharedDebugState {
        std::sync::Arc::new(crate::debug::DebugState::new(64))
    }

    fn tp(gain: f32, pan: f32) -> TrackParams {
        TrackParams {
            vel_track: 0.0,
            vel_track_mid: 0.5,
            key_track: 0.0,
            key_track_mid: 60.0,
            gain,
            pan,
            muted: false,
        }
    }

    /// Empty per-track audio-clip lists (most tests exercise only notes).
    fn no_audio(n: usize) -> Vec<Vec<AudioClipEvent>> {
        (0..n).map(|_| Vec::new()).collect()
    }

    #[test]
    fn hard_pan_routes_to_one_side() {
        let mut g = Graph::new(44100, &[tp(1.0, -1.0)], &[vec![]], &[vec![]], &[LayerMode::All], &[vec![]], &[vec![]], &[None], 120.0, 44100 * 4, debug_state(), &mut crate::plugins::PluginInstanceSink::for_tests());
        let events = vec![vec![TrackEvent {
            sample: 0,
            len_samples: 4410,
            instrument: Instrument::Lead,
            pitch: 69,
            velocity: 1.0,
            pan: 0.0,
        }]];
        let mut out = vec![0.0f32; 2048 * 2];
        g.render(&events, &no_audio(events.len()), 44100 * 4, 0, &mut out);
        let left_peak: f32 = out.iter().step_by(2).map(|s| s.abs()).fold(0.0, f32::max);
        let right_peak: f32 = out.iter().skip(1).step_by(2).map(|s| s.abs()).fold(0.0, f32::max);
        assert!(left_peak > 0.05, "left channel should carry the note");
        assert!(right_peak < 1e-6, "right channel should be silent");
    }

    #[test]
    fn two_tracks_sum() {
        let mut g = Graph::new(
            44100,
            &[tp(1.0, 0.0), tp(1.0, 0.0)],
            &[vec![], vec![]],
            &[vec![], vec![]],
            &[LayerMode::All, LayerMode::All],
            &[vec![], vec![]],
            &[vec![], vec![]],
            &[None, None],
            120.0,
            44100 * 4,
            debug_state(),
            &mut crate::plugins::PluginInstanceSink::for_tests(),
        );
        let ev = |s| TrackEvent {
            sample: s,
            len_samples: 4410,
            instrument: Instrument::Lead,
            pitch: 69,
            velocity: 1.0,
            pan: 0.0,
        };
        let events = vec![vec![ev(0)], vec![ev(0)]];
        let mut out = vec![0.0f32; 2048 * 2];
        g.render(&events, &no_audio(events.len()), 44100 * 4, 0, &mut out);
        let peak: f32 = out.iter().map(|s| s.abs()).fold(0.0, f32::max);
        assert!(peak > 0.05);
    }

    #[test]
    fn pitchshift_multithread_parity() {
        // Two strips, each with a pitch-shift insert; single- vs
        // multi-threaded strip rendering must be bit-identical
        // (strip-local effect state, sequential mixdown).
        let fx = || {
            vec![crate::effects::FxParams::PitchShift {
                semitones: 5.0,
                mix: 1.0,
            }]
        };
        let ev = |s| TrackEvent {
            sample: s,
            len_samples: 4410,
            instrument: Instrument::Lead,
            pitch: 69,
            velocity: 1.0,
            pan: 0.0,
        };
        let events = vec![vec![ev(0)], vec![ev(2205)]];
        let mut run = |mt: bool| {
            let mut g = Graph::new(
                44100,
                &[tp(1.0, 0.0), tp(1.0, 0.0)],
                &[fx(), fx()],
                &[vec![], vec![]],
                &[LayerMode::All, LayerMode::All],
                &[vec![], vec![]],
                &[vec![], vec![]],
                &[None, None],
                120.0,
                44100 * 4,
                debug_state(),
                &mut crate::plugins::PluginInstanceSink::for_tests(),
            );
            g.multithreaded = mt;
            let mut out = vec![0.0f32; 2048 * 2];
            g.render(&events, &no_audio(events.len()), 44100 * 4, 0, &mut out);
            out
        };
        let single = run(false);
        let multi = run(true);
        assert!(single.iter().zip(multi.iter()).all(|(a, b)| a.to_bits() == b.to_bits()),
            "single vs multithreaded pitch-shift render must be bit-identical");
    }

    fn auto_gain(points: Vec<(f64, f64)>) -> Vec<Vec<AutoCurve>> {
        let n = points.len();
        vec![vec![AutoCurve {
            param: AutoParam::Gain,
            points,
            tangents: vec![(None, None); n],
            interp: crate::timeline::InterpMode::Linear,
            tension: 0.5,
            lfo: None,
        }]]
    }

    #[test]
    fn gain_automation_moves_level_per_block() {
        // 120 BPM: beat = 22050 samples. 8-beat loop, gain ramps 0 -> 2.
        let loop_samples = 22050u64 * 8;
        let mut g = Graph::new(
            44100,
            &[tp(1.0, 0.0)],
            &[vec![]],
            &[vec![]],
            &[LayerMode::All],
            &auto_gain(vec![(0.0, 0.0), (8.0, 2.0)]),
            &[vec![]],
            &[None],
            120.0,
            loop_samples,
            debug_state(),
            &mut crate::plugins::PluginInstanceSink::for_tests(),
        );
        let events = vec![vec![TrackEvent {
            sample: 0,
            len_samples: 44100,
            instrument: Instrument::Lead,
            pitch: 69,
            velocity: 1.0,
            pan: 0.0,
        }]];
        let peak = |out: &[f32]| out.iter().map(|s| s.abs()).fold(0.0f32, f32::max);

        // Block at beat 0: gain ~0 -> near silence.
        let mut quiet = vec![0.0f32; 2048 * 2];
        g.render(&events, &no_audio(events.len()), loop_samples, 0, &mut quiet);
        // Block at beat 7: gain ~1.75 -> loud.
        let mut loud = vec![0.0f32; 2048 * 2];
        g.render(&events, &no_audio(events.len()), loop_samples, 22050 * 7, &mut loud);
        assert!(peak(&quiet) < 0.01, "gain 0 should silence the block");
        assert!(peak(&loud) > 0.2, "gain ~1.75 should be loud");
    }

    #[test]
    fn note_pan_steers_voice_stereo() {
        // Per-note pan (FL/LMMS note-pan model): the voice carries its
        // own constant-power pan gains, applied before the track fader.
        let loop_samples = 22050u64 * 8;
        let mk = || {
            Graph::new(
                44100,
                &[tp(1.0, 0.0)],
                &[vec![]],
                &[vec![]],
                &[LayerMode::All],
                &[vec![]],
                &[vec![]],
                &[None],
                120.0,
                loop_samples,
                debug_state(),
                &mut crate::plugins::PluginInstanceSink::for_tests(),
            )
        };
        let ev = |pan: f32| {
            vec![vec![TrackEvent {
                sample: 0,
                len_samples: 44100,
                instrument: Instrument::Kick,
                pitch: 36,
                velocity: 1.0,
                pan,
            }]]
        };
        let peaks = |out: &[f32]| {
            let l = out.iter().step_by(2).map(|s| s.abs()).fold(0.0f32, f32::max);
            let r = out.iter().skip(1).step_by(2).map(|s| s.abs()).fold(0.0f32, f32::max);
            (l, r)
        };
        // Hard left: right channel silent.
        let mut g = mk();
        let mut out = vec![0.0f32; 2048 * 2];
        g.render(&ev(-1.0), &no_audio(ev(-1.0).len()), loop_samples, 0, &mut out);
        let (l, r) = peaks(&out);
        assert!(l > 0.1, "hard-left note should sound left, got {l}");
        assert!(r < 1e-6, "hard-left note should be silent right, got {r}");
        // Hard right: mirror image.
        let mut g = mk();
        let mut out = vec![0.0f32; 2048 * 2];
        g.render(&ev(1.0), &no_audio(ev(1.0).len()), loop_samples, 0, &mut out);
        let (l, r) = peaks(&out);
        assert!(r > 0.1, "hard-right note should sound right, got {r}");
        assert!(l < 1e-6, "hard-right note should be silent left, got {l}");
        // Center: equal energy both sides.
        let mut g = mk();
        let mut out = vec![0.0f32; 2048 * 2];
        g.render(&ev(0.0), &no_audio(ev(0.0).len()), loop_samples, 0, &mut out);
        let (l, r) = peaks(&out);
        assert!(l > 0.1 && r > 0.1, "centered note should sound both sides");
        assert!((l - r).abs() < 1e-6, "centered note should be balanced");
    }

    #[test]
    fn fx_automation_updates_effect_live() {
        use crate::effects::FxParamId;
        // Filter cutoff ramps 200 -> 18000 Hz; a bright lead gets louder
        // in the second block as the filter opens.
        let loop_samples = 22050u64 * 8;
        let fx = vec![crate::effects::FxParams::Filter { cutoff: 200.0 }];
        let mut g = Graph::new(
            44100,
            &[tp(1.0, 0.0)],
            &[fx],
            &[vec![]],
            &[LayerMode::All],
            &[vec![AutoCurve {
                param: AutoParam::Fx { index: 0, param: FxParamId::FilterCutoff },
                points: vec![(0.0, 200.0), (8.0, 18000.0)],
                tangents: vec![(None, None); 2],
                interp: crate::timeline::InterpMode::Linear,
                tension: 0.5,
                lfo: None,
            }]],
            &[vec![]],
            &[None],
            120.0,
            loop_samples,
            debug_state(),
            &mut crate::plugins::PluginInstanceSink::for_tests(),
        );
        let events = vec![vec![TrackEvent {
            sample: 0,
            len_samples: 44100,
            instrument: Instrument::Lead,
            pitch: 69,
            velocity: 1.0,
            pan: 0.0,
        }]];
        // Brightness: max abs difference between consecutive left-channel
        // frames (a lowpassed signal changes slowly, a bright one fast).
        let brightness = |out: &[f32]| {
            let left: Vec<f32> = out.iter().step_by(2).copied().collect();
            left.windows(2)
                .map(|w| (w[0] - w[1]).abs())
                .fold(0.0f32, f32::max)
        };
        let mut dark = vec![0.0f32; 4096 * 2];
        g.render(&events, &no_audio(events.len()), loop_samples, 0, &mut dark);
        let mut bright = vec![0.0f32; 4096 * 2];
        g.render(&events, &no_audio(events.len()), loop_samples, 22050 * 7, &mut bright);
        assert!(
            brightness(&bright) > brightness(&dark) * 2.0,
            "opening the filter should brighten the sound"
        );
    }

    fn lead_note(sample: u64, velocity: f64) -> TrackEvent {
        TrackEvent {
            sample,
            len_samples: 4410,
            instrument: Instrument::Lead,
            pitch: 69,
            velocity,
            pan: 0.0,
        }
    }

    fn master_peak(g: &mut Graph, events: &[Vec<TrackEvent>],
                   loop_samples: u64) -> f32 {
        let mut out = vec![0.0f32; 2048 * 2];
        g.render(events, &no_audio(events.len()), loop_samples, 0, &mut out);
        out.iter().map(|s| s.abs()).fold(0.0f32, f32::max)
    }

    /// FL "route to this track only": the source must NOT reach the Master
    /// directly. With identical notes on source and bus, a parallel send
    /// (amount 1.0) delivers the source twice (direct + via bus) while the
    /// exclusive route delivers it once.
    #[test]
    fn route_only_removes_direct_master_path() {
        let loop_samples = 44100u64 * 4;
        let events = vec![vec![lead_note(0, 0.15)], vec![lead_note(0, 0.15)]];
        let tps = [tp(1.0, 0.0), tp(1.0, 0.0)];
        let nofx: Vec<Vec<crate::effects::FxParams>> = vec![vec![], vec![]];
        let layers = [vec![], vec![]];
        let modes = [LayerMode::All, LayerMode::All];
        let auto = [vec![], vec![]];
        // Parallel send: source -> master AND source -> bus (amount 1.0).
        let mut g_send = Graph::new(
            44100, &tps, &nofx, &layers, &modes, &auto,
            &[vec![crate::timeline::SendData {
                to_track: 1,
                amount: 1.0,
                tap: crate::timeline::SendTap::Post,
                pan: 0.0,
                sidechain: false,
            }],
              vec![]],
            &[None, None],
            120.0, loop_samples, debug_state(),
            &mut crate::plugins::PluginInstanceSink::for_tests(),
        );
        // Exclusive route: source -> bus ONLY.
        let mut g_route = Graph::new(
            44100, &tps, &nofx, &layers, &modes, &auto,
            &[vec![], vec![]],
            &[Some(1), None],
            120.0, loop_samples, debug_state(),
            &mut crate::plugins::PluginInstanceSink::for_tests(),
        );
        let peak_send = master_peak(&mut g_send, &events, loop_samples);
        let peak_route = master_peak(&mut g_route, &events, loop_samples);
        assert!(peak_send > 0.01 && peak_route > 0.01,
                "both routings must produce audible output");
        assert!(
            peak_route < peak_send * 0.8,
            "route-only ({peak_route}) must be quieter than parallel send \
             ({peak_send}): the direct master path must be gone"
        );
    }

    /// The subgroup's FX must process the routed signal (series, not just
    /// a parallel tap): a dark lowpass on the bus dulls the routed source.
    #[test]
    fn route_only_signal_flows_through_bus_fx() {
        let loop_samples = 44100u64 * 4;
        // High bright note; the bus lowpass at 200 Hz should gut it.
        let events = vec![vec![TrackEvent {
            sample: 0,
            len_samples: 4410,
            instrument: Instrument::Lead,
            pitch: 96,
            velocity: 0.5,
            pan: 0.0,
        }], vec![]];
        let tps = [tp(1.0, 0.0), tp(1.0, 0.0)];
        let dark_fx = vec![vec![], vec![crate::effects::FxParams::Filter {
            cutoff: 200.0,
        }]];
        let layers = [vec![], vec![]];
        let modes = [LayerMode::All, LayerMode::All];
        let auto = [vec![], vec![]];
        let sends: Vec<Vec<crate::timeline::SendData>> = vec![vec![], vec![]];
        let mut g_direct = Graph::new(
            44100, &tps, &dark_fx, &layers, &modes, &auto, &sends,
            &[None, None], 120.0, loop_samples, debug_state(),
            &mut crate::plugins::PluginInstanceSink::for_tests(),
        );
        let mut g_routed = Graph::new(
            44100, &tps, &dark_fx, &layers, &modes, &auto, &sends,
            &[Some(1), None], 120.0, loop_samples, debug_state(),
            &mut crate::plugins::PluginInstanceSink::for_tests(),
        );
        let peak_direct = master_peak(&mut g_direct, &events, loop_samples);
        let peak_routed = master_peak(&mut g_routed, &events, loop_samples);
        assert!(peak_direct > 0.01, "direct path must be audible");
        assert!(
            peak_routed < peak_direct * 0.5,
            "routed signal ({peak_routed}) must be dulled by the bus \
             lowpass vs direct ({peak_direct})"
        );
    }

    /// Helper: a parallel send edge with full routing options.
    fn send_edge(
        to: usize,
        amount: f32,
        tap: crate::timeline::SendTap,
        pan: f32,
        sidechain: bool,
    ) -> crate::timeline::SendData {
        crate::timeline::SendData {
            to_track: to,
            amount,
            tap,
            pan,
            sidechain,
        }
    }

    /// Stereo master peaks (separate L/R) for pan tests.
    fn master_lr_peak(
        g: &mut Graph,
        events: &[Vec<TrackEvent>],
        loop_samples: u64,
    ) -> (f32, f32) {
        let mut out = vec![0.0f32; 2048 * 2];
        g.render(events, &no_audio(events.len()), loop_samples, 0, &mut out);
        let mut l = 0.0f32;
        let mut r = 0.0f32;
        for i in (0..out.len()).step_by(2) {
            l = l.max(out[i].abs());
            r = r.max(out[i + 1].abs());
        }
        (l, r)
    }

    /// Pre-fader taps read the signal before the track gain/pan stage;
    /// post taps read after it. With the source fader at zero, a pre-tap
    /// send still delivers audio while a post-tap send goes silent
    /// (FL Studio pre/post-fader send semantics).
    #[test]
    fn send_tap_pre_vs_post_fader() {
        let loop_samples = 44100u64 * 4;
        let events = vec![vec![lead_note(0, 0.5)], vec![]];
        // Source fader at zero: its direct master path is silent.
        let tps = [tp(0.0, 0.0), tp(1.0, 0.0)];
        let nofx: Vec<Vec<crate::effects::FxParams>> = vec![vec![], vec![]];
        let layers = [vec![], vec![]];
        let modes = [LayerMode::All, LayerMode::All];
        let auto = [vec![], vec![]];
        let outputs = [None, None];
        let mut mk = |tap| {
            Graph::new(
                44100,
                &tps,
                &nofx,
                &layers,
                &modes,
                &auto,
                &[vec![send_edge(1, 1.0, tap, 0.0, false)], vec![]],
                &outputs,
                120.0,
                loop_samples,
                debug_state(),
                &mut crate::plugins::PluginInstanceSink::for_tests(),
            )
        };
        let mut g_pre = mk(crate::timeline::SendTap::Pre);
        let mut g_post = mk(crate::timeline::SendTap::Post);
        let peak_pre = master_peak(&mut g_pre, &events, loop_samples);
        let peak_post = master_peak(&mut g_post, &events, loop_samples);
        assert!(
            peak_pre > 0.01,
            "pre-fader tap must survive a zero fader (got {peak_pre})"
        );
        assert!(
            peak_post < 1e-6,
            "post-fader tap must be silent with a zero fader (got {peak_post})"
        );
    }

    /// Per-send pan steers the routed copy independently of the source
    /// track's pan. Hard-right send under a centered direct path:
    /// master R must be ~2x master L (constant-power law).
    #[test]
    fn send_pan_steers_routed_signal() {
        let loop_samples = 44100u64 * 4;
        let events = vec![vec![lead_note(0, 0.15)], vec![]];
        let tps = [tp(1.0, 0.0), tp(1.0, 0.0)];
        let nofx: Vec<Vec<crate::effects::FxParams>> = vec![vec![], vec![]];
        let layers = [vec![], vec![]];
        let modes = [LayerMode::All, LayerMode::All];
        let auto = [vec![], vec![]];
        let mut g = Graph::new(
            44100,
            &tps,
            &nofx,
            &layers,
            &modes,
            &auto,
            &[vec![send_edge(
                1,
                1.0,
                crate::timeline::SendTap::Post,
                1.0,
                false,
            )], vec![]],
            &[None, None],
            120.0,
            loop_samples,
            debug_state(),
            &mut crate::plugins::PluginInstanceSink::for_tests(),
        );
        let (l, r) = master_lr_peak(&mut g, &events, loop_samples);
        assert!(l > 0.01, "left channel must carry the direct path (got {l})");
        assert!(
            r > l * 1.7,
            "hard-right send must push R well above L (L={l}, R={r})"
        );
    }

    /// Send amount automation modulates the edge gain, never the route
    /// itself (the FL/LMMS shared invariant). Pre-tap send from a
    /// zero-fader source isolates the send at the master.
    #[test]
    fn send_amount_automation_modulates_edge_gain() {
        let loop_samples = 44100u64 * 4; // 8 beats at 120 BPM
        let long_note = TrackEvent {
            sample: 0,
            len_samples: loop_samples,
            instrument: Instrument::Lead,
            pitch: 69,
            velocity: 0.5,
            pan: 0.0,
        };
        let events = vec![vec![long_note], vec![]];
        let tps = [tp(0.0, 0.0), tp(1.0, 0.0)];
        let nofx: Vec<Vec<crate::effects::FxParams>> = vec![vec![], vec![]];
        let layers = [vec![], vec![]];
        let modes = [LayerMode::All, LayerMode::All];
        let outputs = [None, None];
        let sends =
            [vec![send_edge(1, 1.0, crate::timeline::SendTap::Pre, 0.0, false)], vec![]];
        // Automated 0.0 -> 1.0 over the 8-beat loop.
        let auto_ramp = [vec![crate::timeline::AutoCurve {
            param: crate::timeline::AutoParam::Send { dest: 1 },
            points: vec![(0.0, 0.0), (8.0, 1.0)],
            tangents: vec![(None, None); 2],
            interp: crate::timeline::InterpMode::Linear,
            tension: 0.5,
            lfo: None,
        }], vec![]];
        let auto_flat: [Vec<crate::timeline::AutoCurve>; 2] = [vec![], vec![]];
        // Beat 0: amount 0 -> silent.
        let mut g_a = Graph::new(
            44100, &tps, &nofx, &layers, &modes, &auto_ramp, &sends,
            &outputs, 120.0, loop_samples, debug_state(), &mut crate::plugins::PluginInstanceSink::for_tests(),
        );
        let mut out_a = vec![0.0f32; 2048 * 2];
        g_a.render(&events, &no_audio(events.len()), loop_samples, 0, &mut out_a);
        let peak_a = out_a.iter().map(|s| s.abs()).fold(0.0f32, f32::max);
        // Beat 4: amount ~0.5 -> audible.
        let mut g_b = Graph::new(
            44100, &tps, &nofx, &layers, &modes, &auto_ramp, &sends,
            &outputs, 120.0, loop_samples, debug_state(), &mut crate::plugins::PluginInstanceSink::for_tests(),
        );
        let mut out_b = vec![0.0f32; 2048 * 2];
        g_b.render(&events, &no_audio(events.len()), loop_samples, 4 * 22050, &mut out_b);
        let peak_b = out_b.iter().map(|s| s.abs()).fold(0.0f32, f32::max);
        // Static amount 1.0 at beat 4: twice as loud as the automated 0.5.
        let mut g_c = Graph::new(
            44100, &tps, &nofx, &layers, &modes, &auto_flat, &sends,
            &outputs, 120.0, loop_samples, debug_state(), &mut crate::plugins::PluginInstanceSink::for_tests(),
        );
        let mut out_c = vec![0.0f32; 2048 * 2];
        g_c.render(&events, &no_audio(events.len()), loop_samples, 4 * 22050, &mut out_c);
        let peak_c = out_c.iter().map(|s| s.abs()).fold(0.0f32, f32::max);
        assert!(peak_a < 1e-6, "amount 0 must silence the send (got {peak_a})");
        assert!(peak_b > 0.01, "amount ~0.5 must be audible (got {peak_b})");
        assert!(
            peak_c > peak_b * 1.5,
            "static amount 1.0 ({peak_c}) must beat automated ~0.5 ({peak_b})"
        );
    }

    /// A sidechain send feeds the destination's sidechain bus only: it
    /// must not contribute audible audio (FL "sidechain to this track").
    #[test]
    fn sidechain_send_is_inaudible() {
        let loop_samples = 44100u64 * 4;
        let events = vec![vec![lead_note(0, 0.5)], vec![]];
        let tps = [tp(1.0, 0.0), tp(1.0, 0.0)];
        let nofx: Vec<Vec<crate::effects::FxParams>> = vec![vec![], vec![]];
        let layers = [vec![], vec![]];
        let modes = [LayerMode::All, LayerMode::All];
        let auto = [vec![], vec![]];
        let outputs = [None, None];
        let mut mk = |sidechain| {
            Graph::new(
                44100,
                &tps,
                &nofx,
                &layers,
                &modes,
                &auto,
                &[vec![send_edge(
                    1,
                    1.0,
                    crate::timeline::SendTap::Post,
                    0.0,
                    sidechain,
                )], vec![]],
                &outputs,
                120.0,
                loop_samples,
                debug_state(),
                &mut crate::plugins::PluginInstanceSink::for_tests(),
            )
        };
        let mut g_sc = mk(true);
        let mut g_norm = mk(false);
        let peak_sc = master_peak(&mut g_sc, &events, loop_samples);
        let peak_norm = master_peak(&mut g_norm, &events, loop_samples);
        assert!(
            peak_sc > 0.01,
            "direct path must stay audible (got {peak_sc})"
        );
        assert!(
            peak_sc < peak_norm * 0.7,
            "sidechain send ({peak_sc}) must not double the master like \
             a normal send ({peak_norm})"
        );
    }

    /// End to end: a sidechain send drives the native Ducker on the
    /// destination track. One throwaway block lets the envelope settle,
    /// then the measured block must show real gain reduction.
    #[test]
    fn ducker_ducks_on_sidechain() {
        let loop_samples = 44100u64 * 4;
        let long_note = |pitch| TrackEvent {
            sample: 0,
            len_samples: loop_samples,
            instrument: Instrument::Lead,
            pitch,
            velocity: 0.5,
            pan: 0.0,
        };
        let events = vec![vec![long_note(57)], vec![long_note(69)]];
        let tps = [tp(1.0, 0.0), tp(1.0, 0.0)];
        let ducker_fx: Vec<Vec<crate::effects::FxParams>> = vec![
            vec![],
            vec![crate::effects::FxParams::Ducker {
                threshold: 0.05,
                ratio: 10.0,
                attack_ms: 1.0,
                release_ms: 50.0,
            }],
        ];
        let layers = [vec![], vec![]];
        let modes = [LayerMode::All, LayerMode::All];
        let auto = [vec![], vec![]];
        let outputs = [None, None];
        let mut mk = |sidechain| {
            Graph::new(
                44100,
                &tps,
                &ducker_fx,
                &layers,
                &modes,
                &auto,
                &[vec![send_edge(
                    1,
                    1.0,
                    crate::timeline::SendTap::Post,
                    0.0,
                    sidechain,
                )], vec![]],
                &outputs,
                120.0,
                loop_samples,
                debug_state(),
                &mut crate::plugins::PluginInstanceSink::for_tests(),
            )
        };
        // Without the sidechain feed the ducker must be transparent.
        let mut g_clean = mk(false);
        // With it, track 1 must duck.
        let mut g_duck = mk(true);
        let mut trash = vec![0.0f32; 2048 * 2];
        let mut out_clean = vec![0.0f32; 2048 * 2];
        let mut out_duck = vec![0.0f32; 2048 * 2];
        g_clean.render(&events, &no_audio(events.len()), loop_samples, 0, &mut trash);
        g_duck.render(&events, &no_audio(events.len()), loop_samples, 0, &mut trash);
        g_clean.render(&events, &no_audio(events.len()), loop_samples, 4096, &mut out_clean);
        g_duck.render(&events, &no_audio(events.len()), loop_samples, 4096, &mut out_duck);
        let peak_clean =
            out_clean.iter().map(|s| s.abs()).fold(0.0f32, f32::max);
        let peak_duck =
            out_duck.iter().map(|s| s.abs()).fold(0.0f32, f32::max);
        assert!(peak_clean > 0.01, "unducked mix must be audible");
        assert!(
            peak_duck < peak_clean * 0.8,
            "ducked mix ({peak_duck}) must be quieter than clean ({peak_clean})"
        );
    }

    /// PDC plan: a latent source pushes its destination's input alignment
    /// (chain 0 -> 1 -> 2, FX latencies [64, 0, 0] frames).
    #[test]
    fn pdc_plan_chain_aligns_input() {
        let plan = compute_pdc_plan(
            3,
            &[64, 0, 0],
            &[(0, 1), (1, 2)],
            &[],
            &[0, 0, 0],
            &[false, false, false],
        );
        assert_eq!(plan.input_align, vec![0, 64, 64]);
        assert_eq!(plan.final_delay, vec![0, 0, 0]);
        assert_eq!(plan.max_latency, 64);
    }

    /// PDC plan: fan-in takes the max over senders (0 -> 2, 1 -> 2 with
    /// latencies [32, 64, 0]). The slower sender sets the alignment.
    #[test]
    fn pdc_plan_fan_in_takes_max() {
        let plan = compute_pdc_plan(
            3,
            &[32, 64, 0],
            &[(0, 2), (1, 2)],
            &[],
            &[0, 0, 0],
            &[false, false, false],
        );
        assert_eq!(plan.input_align, vec![0, 0, 64]);
        // Totals [32, 64, 64] -> max 64; track 0's output waits 32.
        assert_eq!(plan.final_delay, vec![32, 0, 0]);
        assert_eq!(plan.max_latency, 64);
    }

    /// PDC plan: alignment accumulates through nested routing
    /// (0 -> 1 -> 2, latencies [16, 32, 0]).
    #[test]
    fn pdc_plan_nested_accumulates() {
        let plan = compute_pdc_plan(
            3,
            &[16, 32, 0],
            &[(0, 1), (1, 2)],
            &[],
            &[0, 0, 0],
            &[false, false, false],
        );
        // input_align [0, 16, 48]; totals [16, 48, 48].
        assert_eq!(plan.input_align, vec![0, 16, 48]);
        assert_eq!(plan.final_delay, vec![32, 0, 0]);
        assert_eq!(plan.max_latency, 48);
    }

    /// PDC plan: sidechain detector delay. The destination's program
    /// path is slower (2112 frames of FX before the Ducker) than the
    /// pre-tap SC path, so the detector feed is delayed by the
    /// difference (FL Studio APDC covers sidechains).
    #[test]
    fn pdc_plan_sc_edge_delays_detector() {
        let plan = compute_pdc_plan(
            2,
            &[0, 2112],
            &[],
            &[(0, 1, false)],
            &[0, 2112],
            &[false, true],
        );
        // Detector arrival 0, program arrival at consumer 2112.
        assert_eq!(plan.input_align, vec![0, 0]);
        assert_eq!(plan.sc_delay, vec![2112]);
        assert_eq!(plan.final_delay, vec![2112, 0]);
        assert_eq!(plan.max_latency, 2112);
    }

    /// PDC plan: slow detector raises the destination's input
    /// alignment. Post-tap SC from a 2112-frame-latent source meets a
    /// Ducker with no FX before it, so the program path waits 2112
    /// frames -- and the Master stays aligned (totals equal).
    #[test]
    fn pdc_plan_sc_edge_raises_input_align() {
        let plan = compute_pdc_plan(
            2,
            &[2112, 0],
            &[],
            &[(0, 1, true)],
            &[0, 0],
            &[false, true],
        );
        assert_eq!(plan.input_align, vec![0, 2112]);
        // Detector and program now arrive together: no bus delay.
        assert_eq!(plan.sc_delay, vec![0]);
        // Master alignment preserved: both totals are 2112.
        assert_eq!(plan.final_delay, vec![0, 0]);
        assert_eq!(plan.max_latency, 2112);
    }

    /// PDC plan: a sidechain edge into a track with no sidechain
    /// consumer changes nothing (the bus is never read).
    #[test]
    fn pdc_plan_sc_no_consumer_no_delay() {
        let plan = compute_pdc_plan(
            2,
            &[0, 2112],
            &[],
            &[(0, 1, false)],
            &[0, 0],
            &[false, false],
        );
        assert_eq!(plan.input_align, vec![0, 0]);
        assert_eq!(plan.sc_delay, vec![0]);
    }

    /// PDC plan: an SC-driven input-align raise propagates downstream
    /// through audible routing (kick ->sc-> bass ->aud-> bus).
    #[test]
    fn pdc_plan_sc_raise_propagates_through_audible_dag() {
        let plan = compute_pdc_plan(
            3,
            &[2112, 0, 0],
            &[(1, 2)],
            &[(0, 1, true)],
            &[0, 0, 0],
            &[false, true, false],
        );
        // Bass waits for the slow detector; the bus waits for the bass.
        assert_eq!(plan.input_align, vec![0, 2112, 2112]);
        assert_eq!(plan.sc_delay, vec![0]);
        assert_eq!(plan.final_delay, vec![0, 0, 0]);
        assert_eq!(plan.max_latency, 2112);
    }

    /// Render consecutive 2048-frame blocks, concatenated.
    fn render_blocks(
        g: &mut Graph,
        events: &[Vec<TrackEvent>],
        loop_samples: u64,
        blocks: usize,
    ) -> Vec<f32> {
        let mut out = Vec::with_capacity(blocks * 2048 * 2);
        for b in 0..blocks {
            let mut blk = vec![0.0f32; 2048 * 2];
            g.render(
                events,
                &no_audio(events.len()),
                loop_samples,
                (b * 2048) as u64,
                &mut blk,
            );
            out.extend_from_slice(&blk);
        }
        out
    }

    /// RMS of `out` over [lo, hi) frames, relative to the steady-state
    /// RMS measured near the end of the render.
    fn window_ratio(out: &[f32], lo: usize, hi: usize) -> f32 {
        let rms = |a: usize, b: usize| {
            let mut s = 0.0f32;
            let mut n = 0u32;
            for i in (a * 2..b * 2).step_by(2) {
                s += out[i] * out[i] + out[i + 1] * out[i + 1];
                n += 1;
            }
            (s / n.max(1) as f32).sqrt()
        };
        let steady = rms(12000, 14000).max(1e-6);
        rms(lo, hi) / steady
    }

    /// Build the SC-PDC end-to-end rig: track 0 = kick (fader at 0 so
    /// it never reaches the Master, pre-tap SC still feeds the bus),
    /// track 1 = sustained bass with a Ducker. `latent_first` inserts
    /// a unity PitchShift (2112 frames, transparent) before the
    /// Ducker.
    fn sc_pdc_rig(latent_first: bool) -> Graph {
        let loop_samples = 44100u64 * 4;
        let tps = [tp(0.0, 0.0), tp(1.0, 0.0)];
        let ducker = crate::effects::FxParams::Ducker {
            threshold: 0.05,
            ratio: 10.0,
            attack_ms: 1.0,
            release_ms: 10.0,
        };
        let bass_fx: Vec<crate::effects::FxParams> = if latent_first {
            vec![
                crate::effects::FxParams::PitchShift {
                    semitones: 0.0,
                    mix: 0.0,
                },
                ducker,
            ]
        } else {
            vec![ducker]
        };
        let fx = vec![vec![], bass_fx];
        let layers = [vec![], vec![]];
        let modes = [LayerMode::All, LayerMode::All];
        let auto = [vec![], vec![]];
        Graph::new(
            44100,
            &tps,
            &fx,
            &layers,
            &modes,
            &auto,
            &[vec![send_edge(
                1,
                1.0,
                crate::timeline::SendTap::Pre,
                0.0,
                true,
            )], vec![]],
            &[None, None],
            120.0,
            loop_samples,
            debug_state(),
            &mut crate::plugins::PluginInstanceSink::for_tests(),
        )
    }

    /// End to end: the sidechain detector is PDC-aligned to the
    /// destination's program path. A 2112-frame-latent FX before the
    /// Ducker must move the duck pulse ~2112 frames later (FL Studio
    /// APDC covers sidechains). The duck pulse is narrow (5 ms kick +
    /// 10 ms release << 2112), so early/late windows discriminate.
    #[test]
    fn sc_pdc_delays_detector_to_ducker() {
        let loop_samples = 44100u64 * 4;
        // Short loud kick burst + sustained bass, both from beat 0.
        let kick = TrackEvent {
            sample: 0,
            len_samples: 220,
            instrument: Instrument::Lead,
            pitch: 36,
            velocity: 0.9,
            pan: 0.0,
        };
        let bass = TrackEvent {
            sample: 0,
            len_samples: loop_samples,
            instrument: Instrument::Lead,
            pitch: 69,
            velocity: 0.5,
            pan: 0.0,
        };
        let events = vec![vec![kick], vec![bass]];
        // Control: no latent FX before the Ducker -> the duck pulse
        // fires early, then the bass recovers.
        let mut g_early = sc_pdc_rig(false);
        let out_early = render_blocks(&mut g_early, &events, loop_samples, 8);
        let early_duck = window_ratio(&out_early, 200, 1200);
        let early_recovered = window_ratio(&out_early, 5000, 7000);
        assert!(
            early_duck < 0.6,
            "control must duck early (ratio {early_duck})"
        );
        assert!(
            early_recovered > 0.8,
            "control must recover after the early pulse (ratio {early_recovered})"
        );
        // Latent FX before the Ducker -> SC PDC moves the duck pulse
        // ~2112 frames later, aligned to the delayed program.
        let mut g_lat = sc_pdc_rig(true);
        let out_lat = render_blocks(&mut g_lat, &events, loop_samples, 8);
        let lat_duck = window_ratio(&out_lat, 2300, 3300);
        let lat_recovered = window_ratio(&out_lat, 6000, 8000);
        assert!(
            lat_duck < 0.5,
            "duck pulse must be delayed ~2112 by SC PDC (ratio {lat_duck})"
        );
        assert!(
            lat_recovered > 0.8,
            "duck pulse must be a shifted pulse, not a level drop (ratio {lat_recovered})"
        );
    }

    /// Graph level: a slow pre-tap SC path raises the destination's
    /// input alignment (visible via track_latencies) so the program
    /// waits for the detector.
    #[test]
    fn sc_pdc_slow_detector_raises_track_latency() {
        let loop_samples = 44100u64 * 4;
        let tps = [tp(0.0, 0.0), tp(1.0, 0.0), tp(1.0, 0.0)];
        let fx: Vec<Vec<crate::effects::FxParams>> = vec![
            vec![],
            vec![crate::effects::FxParams::Ducker {
                threshold: 0.05,
                ratio: 10.0,
                attack_ms: 1.0,
                release_ms: 50.0,
            }],
            vec![crate::effects::FxParams::PitchShift {
                semitones: 0.0,
                mix: 0.0,
            }],
        ];
        let layers = [vec![], vec![], vec![]];
        let modes = [LayerMode::All, LayerMode::All, LayerMode::All];
        let auto = [vec![], vec![], vec![]];
        // Track 2 (2112 latent) ->aud-> track 0 (kick) ->sc(pre)-> track 1.
        let sends = vec![
            vec![send_edge(1, 1.0, crate::timeline::SendTap::Pre, 0.0, true)],
            vec![],
            vec![send_edge(
                0,
                1.0,
                crate::timeline::SendTap::Post,
                0.0,
                false,
            )],
        ];
        let g = Graph::new(
            44100,
            &tps,
            &fx,
            &layers,
            &modes,
            &auto,
            &sends,
            &[None, None, None],
            120.0,
            loop_samples,
            debug_state(),
            &mut crate::plugins::PluginInstanceSink::for_tests(),
        );
        // Kick's input waits 2112 for its latent sender; the bass waits
        // 2112 for the slow detector; the latent track is 2112 by itself.
        assert_eq!(g.track_latencies(), &[2112, 2112, 2112]);
    }

    /// TrackParams with explicit velocity-tracking settings.
    fn tp_vt(gain: f32, pan: f32, vel_track: f32, vel_track_mid: f32) -> TrackParams {
        TrackParams {
            gain,
            pan,
            muted: false,
            vel_track,
            vel_track_mid,
            key_track: 0.0,
            key_track_mid: 60.0,
        }
    }

    /// TrackParams with explicit keyboard-tracking settings.
    fn tp_kt(gain: f32, pan: f32, key_track: f32, key_track_mid: f32) -> TrackParams {
        TrackParams {
            gain,
            pan,
            muted: false,
            vel_track: 0.0,
            vel_track_mid: 0.5,
            key_track,
            key_track_mid,
        }
    }

    /// Render one lead note at `velocity` and return its normalized
    /// brightness: RMS frame-to-frame slew over RMS level, measured on
    /// the sustain portion (attack skipped). Amplitude-invariant, so it
    /// measures TIMBRE (filter cutoff), not loudness -- the honest
    /// metric for per-note velocity tracking.
    fn note_norm_brightness(
        vel_track: f32,
        vel_track_mid: f32,
        velocity: f64,
    ) -> f32 {
        let loop_samples = 44100u64 * 4;
        let events = vec![vec![TrackEvent {
            sample: 0,
            len_samples: 44100,
            instrument: Instrument::Lead,
            pitch: 69,
            velocity,
            pan: 0.0,
        }]];
        let tps = [tp_vt(1.0, 0.0, vel_track, vel_track_mid)];
        let nofx = [Vec::new()];
        let layers = [Vec::new()];
        let modes = [LayerMode::All];
        let auto = [Vec::new()];
        let sends: [Vec<crate::timeline::SendData>; 1] = [Vec::new()];
        let mut g = Graph::new(
            44100, &tps, &nofx, &layers, &modes, &auto, &sends, &[None],
            120.0, loop_samples, debug_state(), &mut crate::plugins::PluginInstanceSink::for_tests(),
        );
        let mut out = vec![0.0f32; 8192 * 2];
        g.render(&events, &no_audio(events.len()), loop_samples, 0, &mut out[..4096 * 2]);
        g.render(&events, &no_audio(events.len()), loop_samples, 8192, &mut out[4096 * 2..]);
        let left: Vec<f32> = out.iter().step_by(2).copied().collect();
        // Skip ~20 ms of attack; measure the sustain portion.
        let steady = &left[880..];
        let sig_rms =
            (steady.iter().map(|s| s * s).sum::<f32>() / steady.len() as f32)
                .sqrt();
        let diff_rms = (steady
            .windows(2)
            .map(|w| (w[0] - w[1]).powi(2))
            .sum::<f32>()
            / steady.len() as f32)
            .sqrt();
        diff_rms / sig_rms.max(1e-9)
    }

    /// Positive velocity tracking: a hard note is brighter than a soft
    /// note (FL 3xOsc/Sawer behavior: velocity -> cutoff).
    #[test]
    fn vel_track_brightens_hard_notes() {
        let bright = note_norm_brightness(1.0, 0.5, 1.0);
        let dark = note_norm_brightness(1.0, 0.5, 0.15);
        assert!(
            bright > dark * 1.5,
            "hard note ({bright:.4}) must be clearly brighter than soft ({dark:.4})"
        );
    }

    /// Tracking off: timbre must not depend on velocity (velocity only
    /// scales loudness, which the normalized metric removes).
    #[test]
    fn vel_track_off_gives_velocity_independent_timbre() {
        let hi = note_norm_brightness(0.0, 0.5, 1.0);
        let lo = note_norm_brightness(0.0, 0.5, 0.15);
        let ratio = hi / lo.max(1e-9);
        assert!(
            (0.85..=1.18).contains(&ratio),
            "tracking off: timbre must match (ratio {ratio:.3})"
        );
    }

    /// Bipolar tracking: negative amount inverts the relationship --
    /// hard notes go darker (FL's bipolar Mod X/Y behavior).
    #[test]
    fn vel_track_negative_amount_inverts() {
        let hard = note_norm_brightness(-1.0, 0.5, 1.0);
        let soft = note_norm_brightness(-1.0, 0.5, 0.15);
        assert!(
            soft > hard * 1.5,
            "negative amount: soft note ({soft:.4}) must be brighter than hard ({hard:.4})"
        );
    }

    /// The middle velocity generates no offset: a note AT the middle
    /// with tracking on must match tracking off (FL's MID semantic).
    #[test]
    fn vel_track_mid_is_no_offset_point() {
        let at_mid = note_norm_brightness(1.0, 0.5, 0.5);
        let no_track = note_norm_brightness(0.0, 0.5, 0.8);
        let ratio = at_mid / no_track.max(1e-9);
        assert!(
            (0.85..=1.18).contains(&ratio),
            "note at mid velocity must match untracked timbre (ratio {ratio:.3})"
        );
    }

    /// Render one lead note at `pitch` and return its normalized
    /// brightness (same amplitude-invariant timbre metric as the
    /// velocity-tracking tests). Velocity is fixed at 0.5 with
    /// velocity tracking off, so only keyboard tracking can move it.
    fn note_norm_brightness_at_pitch(
        key_track: f32,
        key_track_mid: f32,
        pitch: u8,
    ) -> f32 {
        let loop_samples = 44100u64 * 4;
        let events = vec![vec![TrackEvent {
            sample: 0,
            len_samples: 44100,
            instrument: Instrument::Lead,
            pitch,
            velocity: 0.5,
            pan: 0.0,
        }]];
        let tps = [tp_kt(1.0, 0.0, key_track, key_track_mid)];
        let nofx = [Vec::new()];
        let layers = [Vec::new()];
        let modes = [LayerMode::All];
        let auto = [Vec::new()];
        let sends: [Vec<crate::timeline::SendData>; 1] = [Vec::new()];
        let mut g = Graph::new(
            44100, &tps, &nofx, &layers, &modes, &auto, &sends, &[None],
            120.0, loop_samples, debug_state(), &mut crate::plugins::PluginInstanceSink::for_tests(),
        );
        let mut out = vec![0.0f32; 8192 * 2];
        g.render(&events, &no_audio(events.len()), loop_samples, 0, &mut out[..4096 * 2]);
        g.render(&events, &no_audio(events.len()), loop_samples, 8192, &mut out[4096 * 2..]);
        let left: Vec<f32> = out.iter().step_by(2).copied().collect();
        // Skip ~20 ms of attack; measure the sustain portion.
        let steady = &left[880..];
        let sig_rms =
            (steady.iter().map(|s| s * s).sum::<f32>() / steady.len() as f32)
                .sqrt();
        let diff_rms = (steady
            .windows(2)
            .map(|w| (w[0] - w[1]).powi(2))
            .sum::<f32>()
            / steady.len() as f32)
            .sqrt();
        diff_rms / sig_rms.max(1e-9)
    }

    /// High/low brightness ratio at a given tracking amount. Pitch
    /// itself moves the metric (higher fundamentals slew faster), so
    /// the honest assertion is differential: tracking must move the
    /// ratio relative to untracked, not achieve an absolute value.
    fn key_track_hi_lo_ratio(key_track: f32, key_track_mid: f32) -> f32 {
        let hi = note_norm_brightness_at_pitch(key_track, key_track_mid, 81);
        let lo = note_norm_brightness_at_pitch(key_track, key_track_mid, 57);
        hi / lo.max(1e-9)
    }

    /// Positive keyboard tracking: the high/low brightness spread must
    /// widen vs untracked (FL Channel Keyboard Tracker: pitch ->
    /// cutoff, +1.0 = 100% tracking, mid = 69).
    #[test]
    fn key_track_brightens_high_notes() {
        let tracked = key_track_hi_lo_ratio(1.0, 69.0);
        let untracked = key_track_hi_lo_ratio(0.0, 69.0);
        assert!(
            tracked > untracked * 1.5,
            "tracked spread ({tracked:.3}) must clearly exceed untracked ({untracked:.3})"
        );
    }

    /// Tracking amount scales the spread monotonically: half amount
    /// lands between off and full.
    #[test]
    fn key_track_amount_scales_spread() {
        let off = key_track_hi_lo_ratio(0.0, 69.0);
        let half = key_track_hi_lo_ratio(0.5, 69.0);
        let full = key_track_hi_lo_ratio(1.0, 69.0);
        assert!(
            off < half && half < full,
            "spread must grow with amount (off={off:.3}, half={half:.3}, full={full:.3})"
        );
    }

    /// Bipolar tracking: negative amount compresses/inverts the spread
    /// vs untracked -- low notes go brighter (FL's bipolar tracker).
    #[test]
    fn key_track_negative_amount_inverts() {
        let neg = key_track_hi_lo_ratio(-1.0, 69.0);
        let untracked = key_track_hi_lo_ratio(0.0, 69.0);
        assert!(
            neg < untracked / 1.5,
            "negative spread ({neg:.3}) must clearly undercut untracked ({untracked:.3})"
        );
    }

    /// The middle note generates no offset: a note AT the mid with
    /// tracking on must match tracking off (FL's MID semantic).
    #[test]
    fn key_track_mid_is_no_offset_point() {
        let at_mid = note_norm_brightness_at_pitch(1.0, 69.0, 69);
        let no_track = note_norm_brightness_at_pitch(0.0, 69.0, 69);
        let ratio = at_mid / no_track.max(1e-9);
        assert!(
            (0.85..=1.18).contains(&ratio),
            "note at mid pitch must match untracked timbre (ratio {ratio:.3})"
        );
    }

    /// The two trackers are independent modulation sources that sum:
    /// key + velocity tracking together brighten more than either alone.
    #[test]
    fn key_and_vel_track_sum_in_cutoff() {
        let loop_samples = 44100u64 * 4;
        let mk = |vt: f32, kt: f32, pitch: u8, velocity: f64| {
            let events = vec![vec![TrackEvent {
                sample: 0,
                len_samples: 44100,
                instrument: Instrument::Lead,
                pitch,
                velocity,
                pan: 0.0,
            }]];
            let tps = [TrackParams {
                gain: 1.0,
                pan: 0.0,
                muted: false,
                vel_track: vt,
                vel_track_mid: 0.5,
                key_track: kt,
                key_track_mid: 69.0,
            }];
            let nofx = [Vec::new()];
            let layers = [Vec::new()];
            let modes = [LayerMode::All];
            let auto = [Vec::new()];
            let sends: [Vec<crate::timeline::SendData>; 1] = [Vec::new()];
            let mut g = Graph::new(
                44100, &tps, &nofx, &layers, &modes, &auto, &sends,
                &[None], 120.0, loop_samples, debug_state(),
                &mut crate::plugins::PluginInstanceSink::for_tests(),
            );
            let mut out = vec![0.0f32; 8192 * 2];
            g.render(&events, &no_audio(events.len()), loop_samples, 0, &mut out[..4096 * 2]);
            g.render(&events, &no_audio(events.len()), loop_samples, 8192, &mut out[4096 * 2..]);
            let left: Vec<f32> =
                out.iter().step_by(2).copied().collect();
            let steady = &left[880..];
            let sig_rms = (steady.iter().map(|s| s * s).sum::<f32>()
                / steady.len() as f32)
                .sqrt();
            let diff_rms = (steady
                .windows(2)
                .map(|w| (w[0] - w[1]).powi(2))
                .sum::<f32>()
                / steady.len() as f32)
                .sqrt();
            diff_rms / sig_rms.max(1e-9)
        };
        // High hard note, both trackers on: maximum brightness.
        let both = mk(1.0, 1.0, 81, 1.0);
        // Same note, only key tracking: less bright.
        let key_only = mk(0.0, 1.0, 81, 1.0);
        assert!(
            both > key_only * 1.2,
            "key+vel ({both:.4}) must exceed key-only ({key_only:.4})"
        );
    }

    // ---- Audio clip rendering (FL Audio Clip / LMMS SampleClip model) ----

    /// Ramp buffer: L = i/8, R = -i/8 over 8 stereo frames. Direction and
    /// channel identity are both verifiable.
    fn ramp_buffer() -> std::sync::Arc<crate::sample::SampleBuffer> {
        let mut frames = Vec::with_capacity(16);
        for i in 0..8 {
            let v = i as f32 / 8.0;
            frames.push(v);
            frames.push(-v);
        }
        std::sync::Arc::new(crate::sample::SampleBuffer {
            frames: std::sync::Arc::new(frames),
            len: 8,
            source_path: "test".to_string(),
            source_sample_rate: 44100,
        })
    }

    fn audio_graph() -> Graph {
        Graph::new(
            44100,
            &[tp(1.0, 0.0)],
            &[vec![]],
            &[vec![]],
            &[LayerMode::All],
            &[vec![]],
            &[vec![]],
            &[None],
            120.0,
            44100 * 4,
            debug_state(),
            &mut crate::plugins::PluginInstanceSink::for_tests(),
        )
    }

    fn ramp_clip(
        buffer: &std::sync::Arc<crate::sample::SampleBuffer>,
        start: u64,
    ) -> AudioClipEvent {
        AudioClipEvent {
            sample: start,
            buffer: buffer.clone(),
            offset_frames: 0,
            play_frames: 8,
            ratio: 1.0,
            gain: 1.0,
            pan: 0.0,
            reversed: false,
            muted: false,
        }
    }

    fn render_audio(g: &mut Graph, clips: &[Vec<AudioClipEvent>], frames: usize) -> Vec<f32> {
        let events: Vec<Vec<TrackEvent>> = vec![Vec::new()];
        let mut out = vec![0.0f32; frames * 2];
        g.render(&events, clips, 44100 * 4, 0, &mut out);
        out
    }

    /// Full expected chain for a clip sample value `v` with clip pan
    /// `clip_pan`: clip constant-power pan, then the track fader
    /// (gain 1.0, pan 0.0 -> 0.7071), then master (0.85 * tanh).
    fn expected_l(v: f32, clip_pan: f32) -> f32 {
        let ck = ((clip_pan + 1.0) * std::f32::consts::PI / 4.0).cos();
        let tk = std::f32::consts::FRAC_PI_4.cos();
        (0.85 * v * ck * tk).tanh()
    }

    fn expected_r(v: f32, clip_pan: f32) -> f32 {
        let ck = ((clip_pan + 1.0) * std::f32::consts::PI / 4.0).sin();
        let tk = std::f32::consts::FRAC_PI_4.cos();
        (0.85 * v * ck * tk).tanh()
    }

    #[test]
    fn audio_clip_renders_ramp() {
        let buf = ramp_buffer();
        let mut g = audio_graph();
        let clips = vec![vec![ramp_clip(&buf, 0)]];
        let out = render_audio(&mut g, &clips, 8);
        // Center pan: L carries the ramp, R the inverted ramp.
        for i in 0..8 {
            let v = i as f32 / 8.0;
            assert!(
                (out[2 * i] - expected_l(v, 0.0)).abs() < 1e-5,
                "frame {i}: left {} != {}",
                out[2 * i],
                expected_l(v, 0.0)
            );
            assert!(
                (out[2 * i + 1] - expected_r(-v, 0.0)).abs() < 1e-5,
                "frame {i}: right mismatch"
            );
        }
    }

    #[test]
    fn audio_clip_reverse_reads_backwards() {
        let buf = ramp_buffer();
        let mut g = audio_graph();
        let mut c = ramp_clip(&buf, 0);
        c.reversed = true;
        let clips = vec![vec![c]];
        let out = render_audio(&mut g, &clips, 8);
        // First output frame = last buffer frame (7/8), last = first (0).
        assert!(
            (out[0] - expected_l(7.0 / 8.0, 0.0)).abs() < 1e-5,
            "reversed head: {}",
            out[0]
        );
        assert!(out[2 * 7].abs() < 1e-5, "reversed tail");
    }

    #[test]
    fn audio_clip_muted_is_silent() {
        let buf = ramp_buffer();
        let mut g = audio_graph();
        let mut c = ramp_clip(&buf, 0);
        c.muted = true;
        let clips = vec![vec![c]];
        let out = render_audio(&mut g, &clips, 8);
        assert!(out.iter().all(|s| s.abs() < 1e-6));
    }

    #[test]
    fn audio_clip_hard_pan_right() {
        let buf = ramp_buffer();
        let mut g = audio_graph();
        let mut c = ramp_clip(&buf, 0);
        c.pan = 1.0;
        let clips = vec![vec![c]];
        let out = render_audio(&mut g, &clips, 8);
        let left_peak: f32 = out.iter().step_by(2).map(|s| s.abs()).fold(0.0, f32::max);
        let right_peak: f32 =
            out.iter().skip(1).step_by(2).map(|s| s.abs()).fold(0.0, f32::max);
        assert!(left_peak < 1e-6, "hard right: left must be silent");
        // Peak buffer value 7/8 through the chain.
        let expect = expected_r(-7.0 / 8.0, 1.0).abs();
        assert!(
            (right_peak - expect).abs() < 1e-5,
            "hard right: {right_peak} != {expect}"
        );
    }

    #[test]
    fn audio_clip_pitch_ratio_resamples() {
        // ratio 2.0 over 4 output frames covers all 8 buffer frames.
        let buf = ramp_buffer();
        let mut g = audio_graph();
        let mut c = ramp_clip(&buf, 0);
        c.play_frames = 4;
        c.ratio = 2.0;
        let clips = vec![vec![c]];
        let out = render_audio(&mut g, &clips, 4);
        for i in 0..4 {
            let v = (2 * i) as f32 / 8.0;
            assert!(
                (out[2 * i] - expected_l(v, 0.0)).abs() < 1e-5,
                "frame {i}: {} != {}",
                out[2 * i],
                expected_l(v, 0.0)
            );
        }
    }

    #[test]
    fn audio_clip_seeks_mid_clip_on_sync() {
        // Seek to sample 4 of the 8-frame clip: output starts at ramp 4/8.
        let buf = ramp_buffer();
        let mut g = audio_graph();
        let clips = vec![vec![ramp_clip(&buf, 0)]];
        g.sync_cursors(&[Vec::new()], &clips, 4);
        let mut out = vec![0.0f32; 4 * 2];
        let events: Vec<Vec<TrackEvent>> = vec![Vec::new()];
        g.render(&events, &clips, 44100 * 4, 4, &mut out);
        assert!(
            (out[0] - expected_l(4.0 / 8.0, 0.0)).abs() < 1e-5,
            "mid-clip seek should start at ramp 4/8, got {}",
            out[0]
        );
        // Still plays forward from there.
        assert!(out[2] > out[0], "ramp must keep rising after the seek");
    }
}
