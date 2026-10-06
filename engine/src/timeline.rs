//! Arrangement timeline: patterns, clips, tracks.
//!
//! A [`Song`] is an immutable, precomputed snapshot of the whole
//! arrangement: tempo, loop length in samples, and per-track event lists
//! sorted by sample. The audio thread walks each track's list with an index
//! -- no searching, no allocation.

use std::collections::HashMap;
use std::sync::Arc;

use crate::effects::{FxParamId, FxParams};
use crate::sample::SampleBuffer;
use crate::synth::Instrument;

/// An automatable parameter: track gain/pan, one effect parameter, one
/// generator (instrument) parameter, or one send amount. Send automation
/// is the FL/LMMS shared invariant: the route exists statically, and
/// automation modulates the edge gain -- never the topology.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum AutoParam {
    Gain,
    Pan,
    Fx { index: usize, param: FxParamId },
    /// `param` is the raw CLAP parameter id of the track's generator.
    Generator { param: u32 },
    /// `dest` is the destination track index of one of this track's
    /// sends. Output routes (exclusive) cannot be automated.
    Send { dest: usize },
}

impl AutoParam {
    /// Parse "gain", "pan", "fx{i}.{name}", "gen.p{id}", or
    /// "send.{n}.amount" (n = destination track index). `fx` is the
    /// track's effect chain (index/kind check); `gen` is the track's
    /// generator, used to validate "gen.p{id}"; `sends` validates that
    /// the send being automated actually exists.
    pub fn parse(
        s: &str,
        fx: &[FxParams],
        gen: Option<&GeneratorParams>,
        sends: &[SendData],
        n_tracks: usize,
    ) -> Result<Self, String> {
        match s {
            "gain" => Ok(AutoParam::Gain),
            "pan" => Ok(AutoParam::Pan),
            _ => {
                if let Some(rest) = s.strip_prefix("send.") {
                    let (idx_str, name) = rest
                        .split_once('.')
                        .ok_or_else(|| format!("unknown automation param '{}'", s))?;
                    if name != "amount" {
                        return Err(format!("unknown automation param '{}'", s));
                    }
                    let dest: usize = idx_str
                        .parse()
                        .map_err(|_| format!("unknown automation param '{}'", s))?;
                    if dest >= n_tracks {
                        return Err(format!(
                            "automation param '{}': no such destination track",
                            s
                        ));
                    }
                    if !sends.iter().any(|sd| sd.to_track == dest) {
                        return Err(format!(
                            "automation param '{}': track has no send to that destination",
                            s
                        ));
                    }
                    return Ok(AutoParam::Send { dest });
                }
                if let Some(rest) = s.strip_prefix("gen.") {
                    let id_str = rest
                        .strip_prefix('p')
                        .ok_or_else(|| format!("unknown automation param '{}'", s))?;
                    let param: u32 = id_str
                        .parse()
                        .map_err(|_| format!("unknown automation param '{}'", s))?;
                    let gen = gen.ok_or_else(|| {
                        format!("automation param '{}': track has no generator", s)
                    })?;
                    if !gen.params.iter().any(|(id, _)| *id == param) {
                        return Err(format!(
                            "automation param '{}': generator has no recorded value for this parameter",
                            s
                        ));
                    }
                    return Ok(AutoParam::Generator { param });
                }
                let rest = s
                    .strip_prefix("fx")
                    .ok_or_else(|| format!("unknown automation param '{}'", s))?;
                let (idx, name) = rest
                    .split_once('.')
                    .ok_or_else(|| format!("unknown automation param '{}'", s))?;
                let index: usize = idx
                    .parse()
                    .map_err(|_| format!("unknown automation param '{}'", s))?;
                let param = FxParamId::from_name(name)
                    .ok_or_else(|| format!("unknown automation param '{}'", s))?;
                let kind = fx
                    .get(index)
                    .ok_or_else(|| format!("automation param '{}': no such effect", s))?;
                if kind.param_value(param).is_none() {
                    return Err(match kind {
                        FxParams::Plugin { .. } => format!(
                            "automation param '{}': plugin has no recorded value for this parameter (re-add the plugin to refresh its parameters)",
                            s
                        ),
                        _ => format!(
                            "automation param '{}' does not match effect kind",
                            s
                        ),
                    });
                }
                Ok(AutoParam::Fx { index, param })
            }
        }
    }

    /// Engine-unit range for value validation.
    pub fn range(&self) -> (f64, f64) {
        match self {
            AutoParam::Gain => (0.0, 2.0),
            AutoParam::Pan => (-1.0, 1.0),
            AutoParam::Fx { param, .. } => {
                let (lo, hi) = param.range();
                (lo as f64, hi as f64)
            }
            // Generator params are in the plugin's real units; the range is
            // not known to the engine, so accept anything (the Python side
            // clamps to the plugin's reported range in the editor).
            AutoParam::Generator { .. } => (f64::NEG_INFINITY, f64::INFINITY),
            // Send amount is a linear 0-1 edge gain.
            AutoParam::Send { .. } => (0.0, 1.0),
        }
    }
}

/// One automation curve: (beat, value) control points, sorted by beat.
/// Linear interpolation between points; before the first point the
/// static base value holds, after the last point the last value holds.
/// Interpolation mode for an automation lane (research topic 30:
/// automation curve shapes).
///
/// Per-lane, like LMMS's per-clip progression (Linear / Cubic Hermite /
/// Discrete) -- not per-segment like FL Studio's per-segment curve
/// types. The mode selects the curve *evaluator*; the renderer only
/// ever sees sampled values (brief section 52: the renderer must not
/// determine the curve shape).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum InterpMode {
    /// Piecewise linear (the pre-v0.34.0 behavior).
    Linear,
    /// Cubic Hermite with Catmull-Rom tangents scaled by tension:
    /// tension 0 flattens slopes at control points (levels off, like
    /// LMMS's low-tension description), 1 is full Catmull-Rom (smooth,
    /// may overshoot).
    Smooth,
    /// Step: the value of the last point at or before t (FL's Hold /
    /// LMMS's Discrete progression).
    Hold,
    /// Quantized steps between points (FL's Stairs idea); tension sets
    /// the step count, 2..16.
    Stairs,
    /// Square-wave alternation between the point values (FL's Pulse
    /// idea); tension sets the cycle count, 1..8.
    Pulse,
    /// Sine wobble around the linear ramp between points (FL's Wave
    /// idea); tension sets the cycle count, 1..8.
    Wave,
}

impl InterpMode {
    pub fn from_str(s: &str) -> Option<InterpMode> {
        Some(match s {
            "linear" => InterpMode::Linear,
            "smooth" => InterpMode::Smooth,
            "hold" => InterpMode::Hold,
            "stairs" => InterpMode::Stairs,
            "pulse" => InterpMode::Pulse,
            "wave" => InterpMode::Wave,
            _ => return None,
        })
    }

    pub fn as_str(&self) -> &'static str {
        match self {
            InterpMode::Linear => "linear",
            InterpMode::Smooth => "smooth",
            InterpMode::Hold => "hold",
            InterpMode::Stairs => "stairs",
            InterpMode::Pulse => "pulse",
            InterpMode::Wave => "wave",
        }
    }

    /// Whether the tension parameter shapes this mode (Linear and Hold
    /// ignore it; documented so the UI can disable the tension slider).
    pub fn uses_tension(&self) -> bool {
        !matches!(self, InterpMode::Linear | InterpMode::Hold)
    }
}

#[derive(Clone, Debug)]
pub struct AutoCurve {
    pub param: AutoParam,
    pub points: Vec<(f64, f64)>,
    /// Per-point (in_tangent, out_tangent) in value per beat, parallel to
    /// `points`. None = AUTO (Catmull-Rom from neighbors, scaled by
    /// tension); Some = LOCKED user tangent, used verbatim (research
    /// topic 36; LMMS m_inTangent/m_outTangent/m_lockedTangents analog).
    /// Only the Smooth (cubic Hermite) evaluator reads these.
    pub tangents: Vec<(Option<f64>, Option<f64>)>,
    pub interp: InterpMode,
    /// 0..1. Smooth: tangent scale. Stairs/Pulse/Wave: shape frequency.
    /// Ignored by Linear/Hold.
    pub tension: f32,
    /// Optional LFO modulation layer (research topic 36; FL Automation
    /// Clip LFO analog). Applied AFTER the base spline is evaluated --
    /// the spline itself is never modified.
    pub lfo: Option<Lfo>,
}

/// LFO waveform shape. Names match Python curve_eval exactly.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum LfoShape {
    Sine,
    Triangle,
    Saw,
    Pulse,
}

impl LfoShape {
    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "sine" => Some(LfoShape::Sine),
            "triangle" => Some(LfoShape::Triangle),
            "saw" => Some(LfoShape::Saw),
            "pulse" => Some(LfoShape::Pulse),
            _ => None,
        }
    }
}

/// How the LFO combines with the base spline value.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum LfoCombine {
    /// final = base + level * wave (bipolar params, e.g. pan).
    Add,
    /// final = base * (1 + level * wave) (unipolar params, e.g. gain).
    Multiply,
}

impl LfoCombine {
    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "add" => Some(LfoCombine::Add),
            "multiply" => Some(LfoCombine::Multiply),
            _ => None,
        }
    }
}

/// Persistent LFO modulation layer on an automation lane.
#[derive(Clone, Debug)]
pub struct Lfo {
    pub enabled: bool,
    /// Cycles per arrangement beat. Phase = (beat * speed) % 1, so
    /// realtime and offline render agree exactly.
    pub speed: f32,
    pub shape: LfoShape,
    /// -1..1: morphs the triangle toward saw / reverse saw.
    pub skew: f32,
    /// 0..1: duty cycle of the pulse shape.
    pub pulse_width: f32,
    /// Amplitude; may be negative (inverts the wave).
    pub level: f32,
    pub combine: LfoCombine,
}

impl Lfo {
    /// Bipolar (-1..1) waveform at `phase` in [0, 1).
    pub fn wave(&self, phase: f64) -> f64 {
        let p = phase % 1.0;
        match self.shape {
            LfoShape::Sine => (2.0 * std::f64::consts::PI * p).sin(),
            LfoShape::Saw => 2.0 * p - 1.0,
            LfoShape::Pulse => {
                if p < self.pulse_width as f64 {
                    1.0
                } else {
                    -1.0
                }
            }
            LfoShape::Triangle => {
                // Rise time morphs with skew: 0 -> symmetric triangle
                // (peak at phase 0.5); +1 -> rising saw; -1 -> falling saw.
                let rise = (0.5 * (1.0 + self.skew.clamp(-1.0, 1.0) as f64))
                    .clamp(0.01, 0.99);
                if p < rise {
                    -1.0 + 2.0 * p / rise
                } else {
                    1.0 - 2.0 * (p - rise) / (1.0 - rise)
                }
            }
        }
    }

    /// Apply the modulation layer to a base spline value.
    pub fn apply(&self, base: f64, beat: f64) -> f64 {
        if !self.enabled {
            return base;
        }
        let phase = (beat * self.speed as f64) % 1.0;
        let wave = self.wave(phase);
        match self.combine {
            LfoCombine::Add => base + self.level as f64 * wave,
            LfoCombine::Multiply => base * (1.0 + self.level as f64 * wave),
        }
    }
}

impl AutoCurve {
    pub fn value_at(&self, beat: f64, base: f64) -> f64 {
        let pts = &self.points;
        let mut val = if pts.is_empty() || beat < pts[0].0 {
            base
        } else {
            let mut v = pts.last().unwrap().1;
            for (i, w) in pts.windows(2).enumerate() {
                let (b0, v0) = w[0];
                let (b1, v1) = w[1];
                if beat < b1 {
                    let t =
                        ((beat - b0) / (b1 - b0).max(1e-9)).clamp(0.0, 1.0);
                    v = self.eval_segment(i, t, b0, v0, b1, v1);
                    break;
                }
            }
            v
        };
        // The LFO is a separate modulation layer: it never modifies the
        // stored spline, only the evaluated value (brief section 26).
        if let Some(lfo) = &self.lfo {
            val = lfo.apply(val, beat);
        }
        val
    }

    /// Automatic (Catmull-Rom, tension-scaled) tangent for point `i`.
    /// `side` 0 = outgoing, 1 = incoming. Mirrors the pre-topic-36
    /// Smooth formula exactly, so lanes without locked tangents are
    /// bit-identical to before.
    fn auto_tangent(&self, i: usize, side: usize) -> f64 {
        let pts = &self.points;
        let tension = self.tension.clamp(0.0, 1.0) as f64;
        let (b, v) = pts[i];
        let n = pts.len();
        let (pb, pv, nb, nv) = if side == 0 {
            let (pb, pv) = if i > 0 { pts[i - 1] } else { (b, v) };
            let (nb, nv) = pts[i + 1];
            (pb, pv, nb, nv)
        } else {
            let (pb, pv) = pts[i - 1];
            let (nb, nv) = if i + 1 < n { pts[i + 1] } else { (b, v) };
            (pb, pv, nb, nv)
        };
        tension * (nv - pv) / (nb - pb).max(1e-9)
    }

    /// Evaluate segment `i` (points[i] -> points[i+1]) at t in 0..1.
    /// Pure math -- the playback and the UI painter share this shape
    /// language (the painter samples it; it never reimplements it).
    fn eval_segment(
        &self,
        i: usize,
        t: f64,
        b0: f64,
        v0: f64,
        b1: f64,
        v1: f64,
    ) -> f64 {
        let pts = &self.points;
        let tension = self.tension.clamp(0.0, 1.0) as f64;
        match self.interp {
            InterpMode::Linear => v0 + (v1 - v0) * t,
            InterpMode::Hold => v0,
            InterpMode::Smooth => {
                let dt = (b1 - b0).max(1e-9);
                // Locked (user) tangents win; otherwise the automatic
                // Catmull-Rom tangent (LMMS locked-tangent semantics).
                let (_in_tan, out_tan) = self
                    .tangents
                    .get(i)
                    .copied()
                    .unwrap_or((None, None));
                let (next_in, _) = self
                    .tangents
                    .get(i + 1)
                    .copied()
                    .unwrap_or((None, None));
                let m0 = out_tan.unwrap_or_else(|| self.auto_tangent(i, 0));
                let m1 =
                    next_in.unwrap_or_else(|| self.auto_tangent(i + 1, 1));
                let t2 = t * t;
                let t3 = t2 * t;
                (2.0 * t3 - 3.0 * t2 + 1.0) * v0
                    + (t3 - 2.0 * t2 + t) * dt * m0
                    + (-2.0 * t3 + 3.0 * t2) * v1
                    + (t3 - t2) * dt * m1
            }
            InterpMode::Stairs => {
                let steps = 2 + (tension * 14.0).round() as usize; // 2..16
                let idx =
                    ((t * steps as f64).floor() as usize).min(steps - 1);
                v0 + (v1 - v0) * idx as f64 / (steps - 1) as f64
            }
            InterpMode::Pulse => {
                let cycles = 1.0 + (tension * 7.0).round(); // 1..8
                if (t * cycles).fract() < 0.5 {
                    v0
                } else {
                    v1
                }
            }
            InterpMode::Wave => {
                let cycles = 1.0 + (tension * 7.0).round(); // 1..8
                let ramp = v0 + (v1 - v0) * t;
                ramp + (v1 - v0) * 0.5
                    * (2.0 * std::f64::consts::PI * cycles * t).sin()
            }
        }
    }
}

/// One note trigger, positioned at an exact sample offset in the arrangement.
#[derive(Clone, Debug)]
pub struct TrackEvent {
    /// Absolute sample offset from the start of the arrangement loop.
    pub sample: u64,
    /// Note length in samples (drives the voice release for pitched
    /// instruments; one-shot drums ignore it).
    pub len_samples: u64,
    pub instrument: Instrument,
    pub pitch: u8,
    pub velocity: f64,
    /// Per-note pan (-1.0 left .. +1.0 right), applied at the voice
    /// before the track fader (FL/LMMS note-pan model).
    pub pan: f32,
}

/// One note in a channel: start/length in steps, MIDI pitch, velocity.
#[derive(Clone, Debug)]
pub struct NoteData {
    pub start_step: f64,
    pub len_steps: f64,
    /// Per-note pan (-1.0 left .. +1.0 right). 0.0 = center.
    pub pan: f32,
    pub pitch: u8,
    pub velocity: f64,
}

/// Control-thread description of one pattern (a reusable musical phrase).
#[derive(Clone, Debug)]
pub struct PatternData {
    pub id: String,
    #[allow(dead_code)]
    pub name: String,
    pub steps: usize,
    pub channels: Vec<ChannelData>,
}

#[derive(Clone, Debug)]
pub struct ChannelData {
    pub instrument: Instrument,
    /// Notes (start/length in steps). May overlap (chords).
    pub notes: Vec<NoteData>,
}

/// A clip places a pattern on a track: it starts at `start_beat` (beats
/// from the arrangement start) and the pattern repeats to fill `bars`
/// bars.
#[derive(Clone, Debug)]
pub struct ClipData {
    /// Index into `ArrangementData.patterns`.
    pub pattern: usize,
    pub start_beat: u32,
    pub bars: u32,
}

/// Raw audio clip from the bridge (FL Studio's Clip Properties model):
/// a timeline object referencing a loaded sample asset, with
/// per-instance playback properties. The asset holds the shared decoded
/// data (like LMMS's `shared_ptr<const SampleBuffer>`); each clip keeps
/// its own gain/pan/pitch/reverse/trim state.
#[derive(Clone, Debug)]
pub struct RawAudioClip {
    /// Asset id; must match an entry in the engine's sample registry.
    pub asset: String,
    /// Clip start in beats (fractional allowed: the grid is a snapping aid).
    pub start_beat: f64,
    /// Clip length in beats (timeline duration).
    pub length_beats: f64,
    /// Trim into the sample, in beats of sample time.
    pub start_offset_beats: f64,
    /// Linear gain 0.0-2.0.
    pub gain: f32,
    /// Per-clip pan in engine convention: -1.0 left .. +1.0 right.
    pub pan: f32,
    /// Pitch shift in semitones (resample ratio; changes duration, like
    /// LMMS's Sample frequency — NOT time-stretch).
    pub pitch_semitones: f32,
    /// Fine pitch in cents.
    pub fine_cents: f32,
    pub reverse: bool,
    pub muted: bool,
}

/// Playback-ready audio clip event, precomputed by
/// [`Song::from_arrangement`]. Mirrors LMMS `SampleTrack::play` math:
/// playback position = song position - clip start - start offset,
/// clamped to min(clip length, remaining sample).
#[derive(Clone, Debug)]
pub struct AudioClipEvent {
    /// Absolute sample offset of the clip start in the arrangement loop.
    pub sample: u64,
    /// Decoded audio, shared with every clip using this asset.
    pub buffer: std::sync::Arc<crate::sample::SampleBuffer>,
    /// Offset into the buffer (buffer frames) where playback starts.
    pub offset_frames: u64,
    /// Output frames to play: min(clip length, remaining buffer / ratio).
    pub play_frames: u64,
    /// Buffer frames advanced per output frame (pitch as resample ratio).
    pub ratio: f32,
    pub gain: f32,
    /// -1.0 left .. +1.0 right.
    pub pan: f32,
    pub reversed: bool,
    pub muted: bool,
}

/// Raw automation curve from the bridge: param id string ("gain",
/// "pan", "fx0.cutoff") plus (beat, value) points in engine units,
/// One raw automation point: beat, value, and optional locked
/// in/out tangents (value per beat; None = auto).
#[derive(Clone, Debug)]
pub struct RawAutoPoint {
    pub beat: f64,
    pub value: f64,
    pub in_tan: Option<f64>,
    pub out_tan: Option<f64>,
}

/// Raw LFO layer as passed from Python (strings validated in
/// [`Song::from_arrangement`]).
#[derive(Clone, Debug)]
pub struct RawLfo {
    pub enabled: bool,
    pub speed: f32,
    pub shape: String,
    pub skew: f32,
    pub pulse_width: f32,
    pub level: f32,
    pub combine: String,
}

/// Raw automation lane: points (with optional per-point tangents),
/// plus the interpolation mode name, tension, and optional LFO layer
/// (all optional -- older dicts default to linear / 0.5 / no tangents /
/// no LFO).
/// Parsed and validated into [`AutoCurve`] by [`Song::from_arrangement`].
#[derive(Clone, Debug)]
pub struct RawAutoCurve {
    pub param: String,
    pub points: Vec<RawAutoPoint>,
    pub interp: String,
    pub tension: f32,
    pub lfo: Option<RawLfo>,
}

#[derive(Clone, Debug)]
pub struct TrackData {
    #[allow(dead_code)]
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
    pub fx: Vec<FxParams>,
    pub clips: Vec<ClipData>,
    /// Audio clips: timeline sample playback (FL Audio Clip / LMMS
    /// SampleClip model). Rendered into the track strip before the
    /// fader, so they flow through the track's gain/pan/FX chain.
    pub audio_clips: Vec<RawAudioClip>,
    pub automation: Vec<RawAutoCurve>,
    /// Generator layers (FL Layer-style). Empty = built-in voices.
    pub generator_layers: Vec<GeneratorLayerParams>,
    /// How note events fan out across layers.
    pub layer_mode: LayerMode,
    /// Sends: tapped from the source (pre-fader or post-FX per send) and
    /// mixed into the destination buffer BEFORE its FX chain (LMMS order).
    pub sends: Vec<SendData>,
    /// Exclusive output route (FL "route to this track only"): the track's
    /// post-FX output is mixed into the destination's buffer BEFORE its FX
    /// chain (like a send with amount 1.0), and the track does NOT reach
    /// the Master directly. None = route to Master (default).
    pub output: Option<usize>,
}

/// Where a send taps its source signal (FL Studio's two "send"
/// mechanisms, minus the in-chain FX-slot tap which is a later step).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum SendTap {
    /// Voice/generator output before the track's gain/pan (fader).
    /// FL Studio's pre-fader concept (Fruity Send taps pre-fader too).
    Pre,
    /// Post-FX output (current default; FL's ordinary mixer send is
    /// post-fader, and Pulsegrid's fader sits pre-FX -- documented).
    Post,
}

/// One send route: source track -> destination track.
#[derive(Clone, Debug)]
pub struct SendData {
    /// Index into the tracks array.
    pub to_track: usize,
    /// Linear amount 0.0-1.0 (automatable; ignored for sidechain sends,
    /// whose feed is level-independent like FL Studio's).
    pub amount: f32,
    /// Where the signal is tapped from the source.
    pub tap: SendTap,
    /// Stereo position of the sent signal, -1..1 (Fruity Send has
    /// per-send pan; ordinary mixer sends do not).
    pub pan: f32,
    /// Sidechain send (FL "sidechain to this track"): feeds the
    /// destination's sidechain bus for sidechain-capable effects
    /// instead of its audible path.
    pub sidechain: bool,
}

/// A track's sound source: currently only plugin instruments.
/// (Built-in voices are the default when this is None.)
#[derive(Clone, Debug, PartialEq)]
pub struct GeneratorParams {
    pub plugin_id: String,
    pub path: String,
    /// str(CLAP param id) -> value in the plugin's real units.
    pub params: Vec<(u32, f64)>,
    /// Opaque CLAP state blob (Base64-decoded by the bridge).
    pub state: Option<Vec<u8>>,
    /// True for VST3 instruments, false for CLAP.
    pub is_vst3: bool,
}

/// How a track's generator layers respond to note events (FL Layer-style).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum LayerMode {
    /// All enabled layers play every note (default).
    All,
    /// One random enabled layer per note.
    Random,
    /// Round-robin across enabled layers.
    Sequential,
}

/// A single generator layer within a track's layer stack.
/// One note event fans out to layers according to the track's LayerMode.
#[derive(Clone, Debug, PartialEq)]
pub struct GeneratorLayerParams {
    pub generator: GeneratorParams,
    /// Per-layer gain (0.0-2.0, default 1.0).
    pub gain: f32,
    /// Pitch offset in semitones (e.g., 12 = octave up).
    pub pitch_offset: i32,
    /// Muted layers are skipped in fan-out.
    pub enabled: bool,
}

/// Per-track mix parameters consumed by the graph.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TrackParams {
    pub gain: f32,
    pub pan: f32,
    pub muted: bool,
    /// FL Studio-style velocity tracking amount (bipolar -1..1, 0 = off).
    pub vel_track: f32,
    /// Middle velocity (0..1) where velocity tracking generates no offset.
    pub vel_track_mid: f32,
    /// FL Studio-style keyboard tracking amount (bipolar -1..1, 0 = off):
    /// per-note pitch -> voice lowpass cutoff. +1.0 = 100% tracking
    /// (one octave of pitch moves the cutoff one octave).
    pub key_track: f32,
    /// Middle MIDI note (0..127) where keyboard tracking generates no
    /// offset -- FL's MID semantic. Notes above produce positive
    /// offsets, below negative (for positive amounts).
    pub key_track_mid: f32,
}

/// Control-thread description of the whole arrangement.
#[derive(Clone, Debug)]
pub struct ArrangementData {
    pub tempo: f64,
    /// When true, notes are cut at clip boundaries instead of ringing past
    /// them (FL Studio's "Play truncated notes in clips").
    pub truncate_notes: bool,
    pub patterns: Vec<PatternData>,
    pub tracks: Vec<TrackData>,
    /// Sample asset ids referenced by audio clips. The engine resolves
    /// each against its sample registry (populated by `load_sample`);
    /// a missing asset is a loud error, like a missing pattern.
    pub samples: Vec<String>,
}

/// Immutable playback snapshot consumed by the audio thread.
#[derive(Clone, Debug)]
pub struct Song {
    pub sample_rate: u32,
    pub tempo: f64,
    /// Samples per arrangement loop.
    pub loop_samples: u64,
    /// Per-track events, each sorted by `sample`.
    pub track_events: Vec<Vec<TrackEvent>>,
    /// Per-track audio clip events, each sorted by `sample`.
    pub track_audio_clips: Vec<Vec<AudioClipEvent>>,
    /// Per-track mix parameters (gain, pan, mute).
    pub track_params: Vec<TrackParams>,
    /// Per-track insert effect chains (validated).
    pub track_fx: Vec<Vec<FxParams>>,
    /// Per-track automation curves (validated, points sorted by beat).
    pub automation: Vec<Vec<AutoCurve>>,
    /// Per-track generator layers (empty = built-in voices).
    pub track_generator_layers: Vec<Vec<GeneratorLayerParams>>,
    /// Per-track layer modes.
    pub track_layer_modes: Vec<LayerMode>,
    /// Per-track sends: Vec<(destination track index, amount)>.
    pub track_sends: Vec<Vec<SendData>>,
    /// Per-track exclusive output routes (None = Master).
    pub track_outputs: Vec<Option<usize>>,
}

impl Song {
    pub fn from_arrangement(
        sample_rate: u32,
        arr: &ArrangementData,
        samples: &HashMap<String, Arc<SampleBuffer>>,
    ) -> Result<Self, String> {
        if !(20.0..=300.0).contains(&arr.tempo) {
            return Err(format!("tempo {} out of range 20-300 BPM", arr.tempo));
        }
        if arr.patterns.is_empty() {
            return Err("arrangement has no patterns".to_string());
        }
        if arr.tracks.is_empty() {
            return Err("arrangement has no tracks".to_string());
        }

        // Validate patterns.
        for pat in arr.patterns.iter() {
            if pat.steps == 0 || pat.steps > 256 {
                return Err(format!(
                    "pattern '{}': step count {} out of range 1-256",
                    pat.id, pat.steps
                ));
            }
            if pat.channels.is_empty() {
                return Err(format!("pattern '{}' has no channels", pat.id));
            }
            for (ci, ch) in pat.channels.iter().enumerate() {
                if ch.notes.len() > 4096 {
                    return Err(format!(
                        "pattern '{}' channel {}: too many notes ({})",
                        pat.id,
                        ci,
                        ch.notes.len()
                    ));
                }
                for n in &ch.notes {
                    if !(0.0..pat.steps as f64).contains(&n.start_step) {
                        return Err(format!(
                            "pattern '{}' channel {}: note start {} out of range",
                            pat.id, ci, n.start_step
                        ));
                    }
                    if !(0.0 < n.len_steps && n.len_steps <= 64.0) {
                        return Err(format!(
                            "pattern '{}' channel {}: note length {} out of range",
                            pat.id, ci, n.len_steps
                        ));
                    }
                    if n.pitch > 127 {
                        return Err(format!(
                            "pattern '{}' channel {}: pitch out of MIDI range",
                            pat.id, ci
                        ));
                    }
                    if !(0.0..=1.0).contains(&n.velocity) {
                        return Err(format!(
                            "pattern '{}' channel {}: velocity out of range 0-1",
                            pat.id, ci
                        ));
                    }
                    if !(-1.0..=1.0).contains(&n.pan) {
                        return Err(format!(
                            "pattern '{}' channel {}: note pan {} out of range -1..1",
                            pat.id, ci, n.pan
                        ));
                    }
                }
            }
        }

        let secs_per_beat = 60.0 / arr.tempo;
        let samples_per_beat_f = secs_per_beat * sample_rate as f64;
        let samples_per_beat = samples_per_beat_f.round() as u64;
        if samples_per_beat == 0 {
            return Err("beat duration underflows one sample".to_string());
        }
        // Keep the step duration exact (unrounded) and round each event's
        // offset individually. Rounding once per step and multiplying would
        // accumulate up to half a sample of drift per step, so a 16-step
        // pattern would no longer land exactly on bar lines.
        let exact_step_samples = samples_per_beat_f * 0.25;
        if exact_step_samples.round() as u64 == 0 {
            return Err("step duration underflows one sample".to_string());
        }
        let samples_per_bar = samples_per_beat * 4;

        // Arrangement length: furthest clip end, minimum 4 bars so an empty
        // timeline still loops sanely (silence).
        let mut total_bars: u32 = 4;
        let mut track_audio_clips: Vec<Vec<AudioClipEvent>> =
            vec![Vec::new(); arr.tracks.len()];
        for (ti, track) in arr.tracks.iter().enumerate() {
            if !(0.0..=2.0).contains(&track.gain) {
                return Err(format!("track '{}': gain out of range 0-2", track.name));
            }
            if !(-1.0..=1.0).contains(&track.pan) {
                return Err(format!("track '{}': pan out of range -1..1", track.name));
            }
            for clip in &track.clips {
                if clip.pattern >= arr.patterns.len() {
                    return Err(format!(
                        "track '{}': clip references missing pattern index {}",
                        track.name, clip.pattern
                    ));
                }
                if clip.bars == 0 || clip.bars > 256 {
                    return Err(format!(
                        "track '{}': clip length {} out of range 1-256 bars",
                        track.name, clip.bars
                    ));
                }
                total_bars = total_bars.max((clip.start_beat + clip.bars * 4 + 3) / 4);
            }
            // Audio clips: validate, resolve the shared sample buffer, and
            // precompute playback geometry (LMMS SampleTrack::play math,
            // done once per snapshot instead of per block).
            for ac in &track.audio_clips {
                let buffer = samples.get(&ac.asset).ok_or_else(|| {
                    format!(
                        "track '{}': audio clip references unloaded sample '{}'",
                        track.name, ac.asset
                    )
                })?;
                if !ac.start_beat.is_finite() || ac.start_beat < 0.0 || ac.start_beat > 65536.0
                {
                    return Err(format!(
                        "track '{}': audio clip start_beat {} out of range",
                        track.name, ac.start_beat
                    ));
                }
                if !ac.length_beats.is_finite()
                    || ac.length_beats <= 0.0
                    || ac.length_beats > 4096.0
                {
                    return Err(format!(
                        "track '{}': audio clip length {} out of range",
                        track.name, ac.length_beats
                    ));
                }
                if !ac.start_offset_beats.is_finite() || ac.start_offset_beats < 0.0 {
                    return Err(format!(
                        "track '{}': audio clip start offset {} out of range",
                        track.name, ac.start_offset_beats
                    ));
                }
                if !(0.0..=2.0).contains(&ac.gain) {
                    return Err(format!(
                        "track '{}': audio clip gain {} out of range 0-2",
                        track.name, ac.gain
                    ));
                }
                if !(-1.0..=1.0).contains(&ac.pan) {
                    return Err(format!(
                        "track '{}': audio clip pan {} out of range -1..1",
                        track.name, ac.pan
                    ));
                }
                if !(-48.0..=48.0).contains(&ac.pitch_semitones) {
                    return Err(format!(
                        "track '{}': audio clip pitch {} out of range -48..48 semitones",
                        track.name, ac.pitch_semitones
                    ));
                }
                if !(-100.0..=100.0).contains(&ac.fine_cents) {
                    return Err(format!(
                        "track '{}': audio clip fine pitch {} out of range",
                        track.name, ac.fine_cents
                    ));
                }
                let clip_start = (ac.start_beat * samples_per_beat_f).round() as u64;
                let clip_len = (ac.length_beats * samples_per_beat_f).round() as u64;
                let offset_frames =
                    (ac.start_offset_beats * samples_per_beat_f).round() as u64;
                if offset_frames >= buffer.len as u64 {
                    return Err(format!(
                        "track '{}': audio clip start offset past end of sample '{}'",
                        track.name, ac.asset
                    ));
                }
                // Pitch as a resample ratio (duration changes with pitch,
                // like LMMS's Sample frequency - not time-stretch).
                let ratio =
                    2f32.powf((ac.pitch_semitones + ac.fine_cents / 100.0) / 12.0);
                let buf_avail = buffer.len as u64 - offset_frames;
                let max_out = (buf_avail as f64 / ratio as f64).floor() as u64;
                let play_frames = clip_len.min(max_out);
                if play_frames == 0 {
                    // Degenerate (sub-sample clip or extreme pitch-up
                    // trim): nothing to play; skip rather than error.
                    continue;
                }
                track_audio_clips[ti].push(AudioClipEvent {
                    sample: clip_start,
                    buffer: buffer.clone(),
                    offset_frames,
                    play_frames,
                    ratio,
                    gain: ac.gain,
                    pan: ac.pan,
                    reversed: ac.reverse,
                    muted: ac.muted,
                });
                total_bars = total_bars.max(
                    ((clip_start + clip_len + samples_per_bar - 1) / samples_per_bar) as u32,
                );
            }
            // Stable sort keeps determinism when clips share a start sample.
            track_audio_clips[ti].sort_by_key(|e| e.sample);
            for fx in &track.fx {
                fx.validate()
                    .map_err(|e| format!("track '{}': {}", track.name, e))?;
            }
        }
        let loop_samples = total_bars as u64 * samples_per_bar;
        let loop_beats = loop_samples as f64 / samples_per_beat as f64;

        // Parse + validate automation curves. Points are sorted by beat;
        // duplicate beats keep the last value. Beats are in arrangement
        // time and wrap with the loop, so they must fall inside it.
        let n_tracks = arr.tracks.len();
        let mut automation: Vec<Vec<AutoCurve>> = Vec::with_capacity(n_tracks);
        for track in &arr.tracks {
            let mut curves = Vec::with_capacity(track.automation.len());
            for raw in &track.automation {
                // For generator automation, validate against the first layer's params.
                let gen_ref = track.generator_layers.first().map(|l| &l.generator);
                let param =
                    AutoParam::parse(&raw.param, &track.fx, gen_ref, &track.sends, n_tracks)
                        .map_err(|e| format!("track '{}': {}", track.name, e))?;
                let (lo, hi) = param.range();
                if raw.points.is_empty() {
                    return Err(format!(
                        "track '{}': automation '{}' has no points",
                        track.name, raw.param
                    ));
                }
                if raw.points.len() > 512 {
                    return Err(format!(
                        "track '{}': automation '{}' has too many points ({})",
                        track.name,
                        raw.param,
                        raw.points.len()
                    ));
                }
                for rp in &raw.points {
                    if !rp.beat.is_finite()
                        || !(0.0..loop_beats).contains(&rp.beat)
                    {
                        return Err(format!(
                            "track '{}': automation '{}' point beat {} outside arrangement",
                            track.name, raw.param, rp.beat
                        ));
                    }
                    if !rp.value.is_finite() || !(lo..=hi).contains(&rp.value)
                    {
                        return Err(format!(
                            "track '{}': automation '{}' value {} out of range",
                            track.name, raw.param, rp.value
                        ));
                    }
                    for (name, tan) in
                        [("in_tan", rp.in_tan), ("out_tan", rp.out_tan)]
                    {
                        if let Some(m) = tan {
                            if !m.is_finite() {
                                return Err(format!(
                                    "track '{}': automation '{}' {} is not finite",
                                    track.name, raw.param, name
                                ));
                            }
                        }
                    }
                }
                let mut rps = raw.points.clone();
                rps.sort_by(|a, b| a.beat.partial_cmp(&b.beat).unwrap());
                // Keep the last point at each duplicate beat (tangents ride
                // along): the sort is stable, so reverse, dedup (keeps
                // first of each run), and reverse back.
                rps.reverse();
                rps.dedup_by(|a, b| a.beat == b.beat);
                rps.reverse();
                let points: Vec<(f64, f64)> =
                    rps.iter().map(|rp| (rp.beat, rp.value)).collect();
                let tangents: Vec<(Option<f64>, Option<f64>)> = rps
                    .iter()
                    .map(|rp| (rp.in_tan, rp.out_tan))
                    .collect();
                let interp = InterpMode::from_str(&raw.interp)
                    .ok_or_else(|| {
                        format!(
                            "track '{}': automation '{}' has unknown interp '{}'",
                            track.name, raw.param, raw.interp
                        )
                    })?;
                let tension = raw.tension.clamp(0.0, 1.0);
                let lfo = match &raw.lfo {
                    None => None,
                    Some(rl) => {
                        let shape = LfoShape::from_str(&rl.shape).ok_or_else(
                            || {
                                format!(
                                    "track '{}': automation '{}' has unknown LFO shape '{}'",
                                    track.name, raw.param, rl.shape
                                )
                            },
                        )?;
                        let combine =
                            LfoCombine::from_str(&rl.combine).ok_or_else(
                                || {
                                    format!(
                                        "track '{}': automation '{}' has unknown LFO combine '{}'",
                                        track.name, raw.param, rl.combine
                                    )
                                },
                            )?;
                        if !(rl.speed > 0.0) || !rl.speed.is_finite() {
                            return Err(format!(
                                "track '{}': automation '{}' LFO speed must be positive",
                                track.name, raw.param
                            ));
                        }
                        Some(Lfo {
                            enabled: rl.enabled,
                            speed: rl.speed,
                            shape,
                            skew: rl.skew.clamp(-1.0, 1.0),
                            pulse_width: rl.pulse_width.clamp(0.0, 1.0),
                            level: rl.level,
                            combine,
                        })
                    }
                };
                curves.push(AutoCurve {
                    param,
                    points,
                    tangents,
                    interp,
                    tension,
                    lfo,
                });
            }
            automation.push(curves);
        }

        // Expand clips into per-track event lists.
        let mut track_events: Vec<Vec<TrackEvent>> = vec![Vec::new(); arr.tracks.len()];
        for (ti, track) in arr.tracks.iter().enumerate() {
            for clip in &track.clips {
                let pat = &arr.patterns[clip.pattern];
                let pattern_samples =
                    (pat.steps as f64 * exact_step_samples).round() as u64;
                let pattern_bars_num = pat.steps as f64 / 16.0;
                // How many whole pattern iterations fit in the clip.
                let iterations =
                    ((clip.bars as f64 / pattern_bars_num).ceil() as u32).max(1);
                let clip_start = clip.start_beat as u64 * samples_per_beat;
                let clip_end = clip_start + clip.bars as u64 * samples_per_bar;
                for it in 0..iterations {
                    let iter_start = clip_start + it as u64 * pattern_samples;
                    if iter_start >= clip_end {
                        break;
                    }
                    for ch in &pat.channels {
                        for n in &ch.notes {
                            let s = iter_start
                                + (n.start_step * exact_step_samples).round() as u64;
                            let len_samples =
                                (n.len_steps * exact_step_samples).round() as u64;
                            if s < clip_end {
                                let mut len_samples = len_samples.max(1);
                                if arr.truncate_notes {
                                    // Cut the note at the clip boundary.
                                    len_samples =
                                        len_samples.min(clip_end - s).max(1);
                                }
                                track_events[ti].push(TrackEvent {
                                    sample: s,
                                    len_samples,
                                    instrument: ch.instrument,
                                    pitch: n.pitch,
                                    velocity: n.velocity,
                                    pan: n.pan,
                                });
                            }
                        }
                    }
                }
            }
            // Stable sort keeps determinism when events share a sample.
            track_events[ti].sort_by_key(|e| e.sample);
        }

        let track_params = arr
            .tracks
            .iter()
            .map(|t| TrackParams {
                gain: t.gain,
                pan: t.pan,
                muted: t.muted,
                vel_track: t.vel_track,
                vel_track_mid: t.vel_track_mid,
                key_track: t.key_track,
                key_track_mid: t.key_track_mid,
            })
            .collect();
        let track_fx = arr.tracks.iter().map(|t| t.fx.clone()).collect();
        let track_generator_layers = arr
            .tracks
            .iter()
            .map(|t| t.generator_layers.clone())
            .collect();
        let track_layer_modes = arr.tracks.iter().map(|t| t.layer_mode).collect();
        let track_sends = arr.tracks.iter().map(|t| t.sends.clone()).collect();
        let track_outputs = arr.tracks.iter().map(|t| t.output).collect();

        Ok(Song {
            sample_rate,
            tempo: arr.tempo,
            loop_samples,
            track_events,
            track_audio_clips,
            track_params,
            track_fx,
            track_generator_layers,
            track_layer_modes,
            track_sends,
            track_outputs,
            automation,
        })
    }

    /// Beats elapsed for an absolute sample position (for UI readouts).
    pub fn beats_at(&self, abs_sample: u64) -> f64 {
        abs_sample as f64 / self.sample_rate as f64 / (60.0 / self.tempo)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn song(arr: &ArrangementData) -> Song {
        Song::from_arrangement(44100, arr, &HashMap::new()).unwrap()
    }

    fn song_err(arr: &ArrangementData) -> bool {
        Song::from_arrangement(44100, arr, &HashMap::new()).is_err()
    }

    fn drum_pattern() -> PatternData {
        PatternData {
            id: "drums".to_string(),
            name: "Drums".to_string(),
            steps: 16,
            channels: vec![ChannelData {
                instrument: Instrument::Kick,
                notes: vec![
                    NoteData { start_step: 0.0, len_steps: 1.0, pitch: 36, velocity: 0.9, pan: 0.0 },
                    NoteData { start_step: 8.0, len_steps: 1.0, pitch: 36, velocity: 0.9, pan: 0.0 },
                ],
            }],
        }
    }

    fn lead_pattern() -> PatternData {
        // Variable lengths + a chord (two notes, same start).
        PatternData {
            id: "lead".to_string(),
            name: "Lead".to_string(),
            steps: 16,
            channels: vec![ChannelData {
                instrument: Instrument::Lead,
                notes: vec![
                    NoteData { start_step: 0.0, len_steps: 4.0, pitch: 60, velocity: 1.0, pan: 0.0 },
                    NoteData { start_step: 4.0, len_steps: 2.0, pitch: 64, velocity: 0.8, pan: 0.0 },
                    NoteData { start_step: 4.0, len_steps: 2.0, pitch: 67, velocity: 0.8, pan: 0.0 },
                ],
            }],
        }
    }

    fn arrangement() -> ArrangementData {
        ArrangementData {
            tempo: 120.0,
            truncate_notes: false,
            patterns: vec![drum_pattern()],
            samples: Vec::new(),
            tracks: vec![TrackData {
                name: "T1".to_string(),
                gain: 1.0,
                pan: 0.0,
                muted: false,
                vel_track: 0.0,
                vel_track_mid: 0.5,
                key_track: 0.0,
                key_track_mid: 60.0,
                fx: Vec::new(),
                clips: vec![ClipData {
                    pattern: 0,
                    start_beat: 8,
                    bars: 2,
                }],
                audio_clips: Vec::new(),
                automation: Vec::new(),
                generator_layers: Vec::new(),
                layer_mode: LayerMode::All,
                sends: Vec::new(),
                output: None,
            }],
        }
    }

    #[test]
    fn clip_placed_at_bar_offset() {
        let song = song(&arrangement());
        // 120 BPM: beat = 22050 samples, bar = 88200. Clip at bar 2.
        assert_eq!(song.loop_samples, 4 * 88200);
        let evs = &song.track_events[0];
        assert_eq!(evs.len(), 4); // 2 kicks/bar x 2 bars
        assert_eq!(evs[0].sample, 2 * 88200);
        // step 8 lands exactly on the bar's midpoint (no rounding drift)
        assert_eq!(evs[1].sample, 2 * 88200 + 44100);
        // the second pattern iteration starts exactly on bar 3
        assert_eq!(evs[2].sample, 3 * 88200);
        assert_eq!(evs[3].sample, 3 * 88200 + 44100);
        // 1-step notes at 120 BPM last a quarter beat = 5512.5 -> 5513 samples
        // (Rust round() rounds half away from zero).
        assert_eq!(evs[0].len_samples, 5513);
    }

    #[test]
    fn note_lengths_and_chords_schedule() {
        let mut arr = arrangement();
        arr.patterns.push(lead_pattern());
        arr.tracks[0].clips.push(ClipData { pattern: 1, start_beat: 0, bars: 1 });
        let song = song(&arr);
        let evs: Vec<&TrackEvent> = song.track_events[0]
            .iter()
            .filter(|e| e.pitch >= 60)
            .collect();
        assert_eq!(evs.len(), 3);
        // 4-step note = one beat = 22050 samples; 2-step = 11025.
        assert_eq!(evs[0].len_samples, 22050);
        assert_eq!(evs[1].len_samples, 11025);
        assert_eq!(evs[2].len_samples, 11025);
        // chord: same start sample, different pitches.
        assert_eq!(evs[1].sample, evs[2].sample);
        assert_ne!(evs[1].pitch, evs[2].pitch);
        assert!((evs[0].velocity - 1.0).abs() < 1e-9);
    }

    #[test]
    fn fractional_note_start_renders_sample_accurate() {
        // Sub-step timing: fractional steps round to the nearest sample.
        // At 120 BPM, 1 step = 1/16 note = 5512.5 samples; 0.5 steps
        // (a 1/32 note offset) = 2756.25 -> rounds to 2756.
        let mut arr = arrangement();
        arr.patterns[0].channels[0].notes.push(NoteData {
            start_step: 0.5,
            len_steps: 0.25,
            pitch: 36,
            velocity: 0.9,
            pan: 0.0,
        });
        let song = song(&arr);
        let ev = song.track_events[0]
            .iter()
            .find(|e| e.pitch == 36 && e.len_samples < 5513)
            .expect("fractional note present");
        // Clip starts at beat 8: 8 * 22050 = 176400.
        assert_eq!(ev.sample, 176400 + 2756);
        // 0.25 steps = 1378.125 -> 1378 samples.
        assert_eq!(ev.len_samples, 1378);
    }

    #[test]
    fn rejects_bad_note() {
        let mut arr = arrangement();
        arr.patterns[0].channels[0].notes.push(NoteData {
            start_step: 99.0, len_steps: 1.0, pitch: 36, velocity: 0.9,
            pan: 0.0,
        });
        assert!(song_err(&arr));
        let mut arr = arrangement();
        arr.patterns[0].channels[0].notes.push(NoteData {
            start_step: 0.0, len_steps: 0.0, pitch: 36, velocity: 0.9,
            pan: 0.0,
        });
        assert!(song_err(&arr));
    }

    #[test]
    fn empty_timeline_loops_four_bars_silently() {
        let mut arr = arrangement();
        arr.tracks[0].clips.clear();
        let song = song(&arr);
        assert_eq!(song.loop_samples, 4 * 88200);
        assert!(song.track_events[0].is_empty());
    }

    #[test]
    fn rejects_dangling_pattern_ref() {
        let mut arr = arrangement();
        arr.tracks[0].clips[0].pattern = 7;
        assert!(song_err(&arr));
    }

    #[test]
    fn rejects_bad_gain() {
        let mut arr = arrangement();
        arr.tracks[0].gain = 5.0;
        assert!(song_err(&arr));
    }

    fn rp(beat: f64, value: f64) -> RawAutoPoint {
        RawAutoPoint { beat, value, in_tan: None, out_tan: None }
    }

    fn auto_arr() -> ArrangementData {
        let mut arr = arrangement();
        arr.tracks[0].automation = vec![RawAutoCurve {
            param: "gain".to_string(),
            points: vec![rp(0.0, 0.0), rp(8.0, 2.0)],
            interp: "linear".to_string(),
            tension: 0.5,
            lfo: None,
            }];
        arr
    }

    #[test]
    fn automation_curve_interpolates() {
        let song = song(&auto_arr());
        let curve = &song.automation[0][0];
        assert_eq!(curve.param, AutoParam::Gain);
        // Before the first point the static base holds.
        assert!((curve.value_at(-1.0, 1.0) - 1.0).abs() < 1e-9);
        // Linear between points; after the last the last value holds.
        assert!((curve.value_at(0.0, 1.0) - 0.0).abs() < 1e-9);
        assert!((curve.value_at(4.0, 1.0) - 1.0).abs() < 1e-9);
        assert!((curve.value_at(8.0, 1.0) - 2.0).abs() < 1e-9);
        assert!((curve.value_at(100.0, 1.0) - 2.0).abs() < 1e-9);
    }

    /// Test helper: a two-point gain curve with the given mode/tension.
    fn mode_curve(interp: InterpMode, tension: f32) -> AutoCurve {
        AutoCurve {
            param: AutoParam::Gain,
            points: vec![(0.0, 0.0), (4.0, 8.0)],
            tangents: vec![(None, None), (None, None)],
            interp,
            tension,
            lfo: None,
        }
    }

    #[test]
    fn locked_tangents_override_auto() {
        // Flat locked tangents: the curve departs/arrives horizontally.
        let c = smooth_curve_locked();
        // Hermite with m0 = m1 = 0 at t = 0.5: exactly the midpoint value.
        assert!((c.value_at(2.0, 0.0) - 4.0).abs() < 1e-9);
        // The auto-tangent version of the same lane is NOT flat here:
        // auto m0 = 0.5 * (8-0)/(4-0) = 1.0 gives 4.5 at t = 0.25.
        let auto = mode_curve(InterpMode::Smooth, 0.5);
        let v_locked = c.value_at(1.0, 0.0);
        let v_auto = auto.value_at(1.0, 0.0);
        assert!((v_auto - 1.625).abs() < 1e-9, "auto v={v_auto}");
        assert!((v_locked - 1.25).abs() < 1e-9, "locked v={v_locked}");
    }

    #[test]
    fn locked_tangent_survives_neighbor_move() {
        // Three-point lane; the middle point's tangents are locked to 0.
        // Moving the LAST point must not change segment 0's shape.
        let mk = |v2: f64| AutoCurve {
            param: AutoParam::Gain,
            points: vec![(0.0, 0.0), (4.0, 4.0), (8.0, v2)],
            tangents: vec![(None, None), (Some(0.0), Some(0.0)), (None, None)],
            interp: InterpMode::Smooth,
            tension: 0.5,
            lfo: None,
        };
        let before = mk(8.0).value_at(2.0, 0.0);
        let after = mk(0.0).value_at(2.0, 0.0);
        assert!((before - after).abs() < 1e-12,
                "locked segment changed: {before} vs {after}");
        // Sanity: with AUTO tangents the same move DOES reshape segment 0.
        let mk_auto = |v2: f64| AutoCurve {
            param: AutoParam::Gain,
            points: vec![(0.0, 0.0), (4.0, 4.0), (8.0, v2)],
            tangents: vec![(None, None); 3],
            interp: InterpMode::Smooth,
            tension: 0.5,
            lfo: None,
        };
        let a0 = mk_auto(8.0).value_at(2.0, 0.0);
        let a1 = mk_auto(0.0).value_at(2.0, 0.0);
        assert!((a0 - a1).abs() > 1e-6, "auto segment should reshape");
    }

    #[test]
    fn lfo_add_and_multiply() {
        let mut c = lfo_curve(); // base 1.0, sine, speed 1, level 0.5, add
        assert!((c.value_at(0.25, 0.0) - 1.5).abs() < 1e-9);
        assert!((c.value_at(0.75, 0.0) - 0.5).abs() < 1e-9);
        assert!((c.value_at(1.25, 0.0) - 1.5).abs() < 1e-9); // phase wraps
        // Multiply on a base of 2: 2 * (1 + 0.5 * wave).
        c.points = vec![(0.0, 2.0), (4.0, 2.0)];
        if let Some(lfo) = c.lfo.as_mut() {
            lfo.combine = LfoCombine::Multiply;
        }
        assert!((c.value_at(0.25, 0.0) - 3.0).abs() < 1e-9);
        assert!((c.value_at(0.75, 0.0) - 1.0).abs() < 1e-9);
        // Disabled LFO passes the base spline through untouched.
        if let Some(lfo) = c.lfo.as_mut() {
            lfo.enabled = false;
        }
        assert!((c.value_at(0.25, 0.0) - 2.0).abs() < 1e-9);
    }

    #[test]
    #[test]
    fn lfo_survives_from_arrangement() {
        let mut arr = arrangement();
        arr.tracks[0].automation = vec![RawAutoCurve {
            param: "gain".to_string(),
            points: vec![rp(0.0, 1.0), rp(8.0, 1.0)],
            interp: "hold".to_string(),
            tension: 0.5,
            lfo: Some(RawLfo {
                enabled: true,
                speed: 1.0,
                shape: "sine".to_string(),
                skew: 0.0,
                pulse_width: 0.5,
                level: 0.5,
                combine: "add".to_string(),
            }),
        }];
        let song = song(&arr);
        let curve = &song.automation[0][0];
        assert!(curve.lfo.is_some(), "LFO lost in from_arrangement");
        assert!((curve.value_at(0.25, 0.0) - 1.5).abs() < 1e-9);
    }

    #[test]
    fn lfo_wave_shapes() {
        let mk = |shape| Lfo {
            enabled: true, speed: 1.0, shape, skew: 0.0,
            pulse_width: 0.25, level: 1.0, combine: LfoCombine::Add,
        };
        let sine = mk(LfoShape::Sine);
        assert!((sine.wave(0.0) - 0.0).abs() < 1e-12);
        assert!((sine.wave(0.25) - 1.0).abs() < 1e-12);
        let saw = mk(LfoShape::Saw);
        assert!((saw.wave(0.0) + 1.0).abs() < 1e-12);
        assert!((saw.wave(0.5) - 0.0).abs() < 1e-12);
        let pulse = mk(LfoShape::Pulse); // duty 0.25
        assert!((pulse.wave(0.1) - 1.0).abs() < 1e-12);
        assert!((pulse.wave(0.5) + 1.0).abs() < 1e-12);
        let tri = mk(LfoShape::Triangle); // skew 0: peak at phase 0.5
        assert!((tri.wave(0.0) + 1.0).abs() < 1e-12);
        assert!((tri.wave(0.5) - 1.0).abs() < 1e-12);
        // Skew -1 collapses the rise: near-falling-saw.
        let mut tri_saw = mk(LfoShape::Triangle);
        tri_saw.skew = -1.0;
        assert!(tri_saw.wave(0.02) > 0.9, "skew -1 ~= falling saw");
        // LFO applies on top of the pre-first-point static base too.
        let c = lfo_curve();
        assert!((c.value_at(0.25, 9.0) - 1.5).abs() < 1e-9);
    }

    fn smooth_curve_locked() -> AutoCurve {
        // Two-point smooth lane with both tangents locked to 0
        // (perfectly flat departure/arrival).
        AutoCurve {
            param: AutoParam::Gain,
            points: vec![(0.0, 0.0), (4.0, 8.0)],
            tangents: vec![(None, Some(0.0)), (Some(0.0), None)],
            interp: InterpMode::Smooth,
            tension: 0.5,
            lfo: None,
        }
    }

    fn lfo_curve() -> AutoCurve {
        // Static base (hold) with an add-mode sine LFO: the base spline
        // never changes, only the evaluated value.
        AutoCurve {
            param: AutoParam::Gain,
            points: vec![(0.0, 1.0), (4.0, 1.0)],
            tangents: vec![(None, None), (None, None)],
            interp: InterpMode::Hold,
            tension: 0.5,
            lfo: Some(Lfo {
                enabled: true,
                speed: 1.0,
                shape: LfoShape::Sine,
                skew: 0.0,
                pulse_width: 0.5,
                level: 0.5,
                combine: LfoCombine::Add,
            }),
        }
    }

    #[test]
    fn interp_mode_names_roundtrip() {
        for (name, mode) in [
            ("linear", InterpMode::Linear),
            ("smooth", InterpMode::Smooth),
            ("hold", InterpMode::Hold),
            ("stairs", InterpMode::Stairs),
            ("pulse", InterpMode::Pulse),
            ("wave", InterpMode::Wave),
        ] {
            assert_eq!(InterpMode::from_str(name), Some(mode));
            assert_eq!(mode.as_str(), name);
        }
        assert_eq!(InterpMode::from_str("bogus"), None);
        // Only Linear and Hold ignore tension (UI disables the slider).
        assert!(!InterpMode::Linear.uses_tension());
        assert!(!InterpMode::Hold.uses_tension());
        for m in [
            InterpMode::Smooth,
            InterpMode::Stairs,
            InterpMode::Pulse,
            InterpMode::Wave,
        ] {
            assert!(m.uses_tension());
        }
    }

    #[test]
    fn interp_hold_is_step() {
        let c = mode_curve(InterpMode::Hold, 0.5);
        assert!((c.value_at(0.0, 9.0) - 0.0).abs() < 1e-9);
        assert!((c.value_at(2.0, 9.0) - 0.0).abs() < 1e-9);
        assert!((c.value_at(3.999, 9.0) - 0.0).abs() < 1e-9);
        // At/after the last point the last value holds.
        assert!((c.value_at(4.0, 9.0) - 8.0).abs() < 1e-9);
        assert!((c.value_at(100.0, 9.0) - 8.0).abs() < 1e-9);
        // Before the first point the static base still holds.
        assert!((c.value_at(-1.0, 9.0) - 9.0).abs() < 1e-9);
    }

    #[test]
    fn interp_smooth_midpoint_and_endpoints() {
        let c = mode_curve(InterpMode::Smooth, 1.0);
        // Endpoints are exact for any tension.
        assert!((c.value_at(0.0, 0.0) - 0.0).abs() < 1e-9);
        // Symmetric segment: midpoint is exact regardless of tension.
        assert!((c.value_at(2.0, 0.0) - 4.0).abs() < 1e-9);
        let c_half = mode_curve(InterpMode::Smooth, 0.5);
        assert!((c_half.value_at(2.0, 0.0) - 4.0).abs() < 1e-9);
    }

    #[test]
    fn interp_smooth_zero_tension_is_smoothstep() {
        // Tension 0 flattens tangents at the points: pure smoothstep
        // 3t^2 - 2t^3. At t = 0.25: 0.15625 * 8 = 1.25.
        let c = mode_curve(InterpMode::Smooth, 0.0);
        assert!((c.value_at(1.0, 0.0) - 1.25).abs() < 1e-9);
    }

    #[test]
    fn interp_stairs_quantizes() {
        // tension 0.5 -> 2 + round(7) = 9 steps over (0,0)->(4,8).
        let c = mode_curve(InterpMode::Stairs, 0.5);
        // t = 0.125 -> idx floor(1.125) = 1 -> 8 * 1/8 = 1.0.
        assert!((c.value_at(0.5, 0.0) - 1.0).abs() < 1e-9);
        // t = 0.5 -> idx 4 -> 8 * 4/8 = 4.0.
        assert!((c.value_at(2.0, 0.0) - 4.0).abs() < 1e-9);
        // Just before the end the top step holds.
        assert!((c.value_at(3.999, 0.0) - 8.0).abs() < 1e-9);
    }

    #[test]
    fn interp_pulse_alternates() {
        // tension 0 -> 1 cycle: first half v0, second half v1.
        let c = mode_curve(InterpMode::Pulse, 0.0);
        assert!((c.value_at(1.0, 0.0) - 0.0).abs() < 1e-9);
        assert!((c.value_at(3.0, 0.0) - 8.0).abs() < 1e-9);
        // tension 1 -> 8 cycles: beat 0.2 (t=0.05) -> 0.4 < 0.5 -> v0.
        let c8 = mode_curve(InterpMode::Pulse, 1.0);
        assert!((c8.value_at(0.2, 0.0) - 0.0).abs() < 1e-9);
        // beat 0.4 (t=0.1) -> 0.8 >= 0.5 -> v1.
        assert!((c8.value_at(0.4, 0.0) - 8.0).abs() < 1e-9);
    }

    #[test]
    fn interp_wave_wobbles_around_ramp() {
        // tension 0 -> 1 cycle: ramp + 4*sin(2*pi*t).
        let c = mode_curve(InterpMode::Wave, 0.0);
        // t = 0.25: ramp 2.0 + 4*sin(pi/2) = 6.0.
        assert!((c.value_at(1.0, 0.0) - 6.0).abs() < 1e-9);
        // t = 0.75: ramp 6.0 + 4*sin(3pi/2) = 2.0.
        assert!((c.value_at(3.0, 0.0) - 2.0).abs() < 1e-9);
        // Endpoints stay exact (sin(2*pi*n) = 0).
        assert!((c.value_at(0.0, 0.0) - 0.0).abs() < 1e-9);
    }

    #[test]
    fn interp_unknown_name_rejected() {
        let mut arr = arrangement();
        arr.tracks[0].automation = vec![RawAutoCurve {
            param: "gain".to_string(),
            points: vec![rp(0.0, 0.0), rp(4.0, 1.0)],
            interp: "bogus".to_string(),
            tension: 0.5,
            lfo: None,
            }];
        assert!(song_err(&arr));
    }

    #[test]
    fn interp_tension_clamped() {
        let mut arr = arrangement();
        arr.tracks[0].automation = vec![RawAutoCurve {
            param: "gain".to_string(),
            points: vec![rp(0.0, 0.0), rp(4.0, 1.0)],
            interp: "smooth".to_string(),
            tension: 99.0,
            lfo: None,
            }];
        let song = song(&arr);
        assert!((song.automation[0][0].tension - 1.0).abs() < 1e-9);
    }

    #[test]
    fn automation_parses_fx_param() {
        use crate::effects::FxParams as EP;
        let mut arr = arrangement();
        arr.tracks[0].fx = vec![EP::Filter { cutoff: 8000.0 }];
        arr.tracks[0].automation = vec![RawAutoCurve {
            param: "fx0.cutoff".to_string(),
            points: vec![rp(0.0, 200.0), rp(4.0, 18000.0)],
            interp: "linear".to_string(),
            tension: 0.5,
            lfo: None,
            }];
        let song = song(&arr);
        assert_eq!(
            song.automation[0][0].param,
            AutoParam::Fx { index: 0, param: crate::effects::FxParamId::FilterCutoff }
        );
    }

    #[test]
    fn rejects_bad_automation() {
        // Unknown param.
        let mut arr = arrangement();
        arr.tracks[0].automation = vec![RawAutoCurve {
            param: "reverb".to_string(),
            points: vec![rp(0.0, 1.0)],
            interp: "linear".to_string(),
            tension: 0.5,
            lfo: None,
            }];
        assert!(song_err(&arr));
        // fx index out of range.
        let mut arr = arrangement();
        arr.tracks[0].automation = vec![RawAutoCurve {
            param: "fx3.cutoff".to_string(),
            points: vec![rp(0.0, 1000.0)],
            interp: "linear".to_string(),
            tension: 0.5,
            lfo: None,
            }];
        assert!(song_err(&arr));
        // Value out of range.
        let mut arr = arrangement();
        arr.tracks[0].automation = vec![RawAutoCurve {
            param: "gain".to_string(),
            points: vec![rp(0.0, 5.0)],
            interp: "linear".to_string(),
            tension: 0.5,
            lfo: None,
            }];
        assert!(song_err(&arr));
        // Beat past the arrangement end (4-bar loop -> 16 beats).
        let mut arr = arrangement();
        arr.tracks[0].automation = vec![RawAutoCurve {
            param: "gain".to_string(),
            points: vec![rp(0.0, 1.0), rp(32.0, 1.0)],
            interp: "linear".to_string(),
            tension: 0.5,
            lfo: None,
            }];
        assert!(song_err(&arr));
        // Empty points.
        let mut arr = arrangement();
        arr.tracks[0].automation = vec![RawAutoCurve {
            param: "gain".to_string(),
            points: vec![],
            interp: "linear".to_string(),
            tension: 0.5,
            lfo: None,
            }];
        assert!(song_err(&arr));
    }

    #[test]
    fn automation_dedups_beats_keeping_last() {
        let mut arr = arrangement();
        arr.tracks[0].automation = vec![RawAutoCurve {
            param: "gain".to_string(),
            points: vec![rp(4.0, 0.5), rp(0.0, 0.0), rp(4.0, 1.5)],
            interp: "linear".to_string(),
            tension: 0.5,
            lfo: None,
            }];
        let song = song(&arr);
        let pts = &song.automation[0][0].points;
        assert_eq!(pts.len(), 2);
        assert_eq!(pts[0], (0.0, 0.0));
        assert_eq!(pts[1], (4.0, 1.5)); // last value wins
    }

    #[test]
    fn automation_parses_generator_param() {
        let mut arr = arrangement();
        arr.tracks[0].generator_layers = vec![GeneratorLayerParams {
            generator: GeneratorParams {
                plugin_id: "test".to_string(),
                path: "/tmp/test.clap".to_string(),
                params: vec![(11, 0.5), (12, 0.01)],
                state: None,
                is_vst3: false,
            },
            gain: 1.0,
            pitch_offset: 0,
            enabled: true,
        }];
        arr.tracks[0].automation = vec![RawAutoCurve {
            param: "gen.p11".to_string(),
            points: vec![rp(0.0, 0.0), rp(4.0, 1.0)],
            interp: "linear".to_string(),
            tension: 0.5,
            lfo: None,
            }];
        let song = song(&arr);
        assert_eq!(
            song.automation[0][0].param,
            AutoParam::Generator { param: 11 }
        );
    }

    #[test]
    fn generator_automation_rejects_unknown_param() {
        let mut arr = arrangement();
        arr.tracks[0].generator_layers = vec![GeneratorLayerParams {
            generator: GeneratorParams {
                plugin_id: "test".to_string(),
                path: "/tmp/test.clap".to_string(),
                params: vec![(11, 0.5)],
                state: None,
                is_vst3: false,
            },
            gain: 1.0,
            pitch_offset: 0,
            enabled: true,
        }];
        arr.tracks[0].automation = vec![RawAutoCurve {
            param: "gen.p99".to_string(),
            points: vec![rp(0.0, 0.0)],
            interp: "linear".to_string(),
            tension: 0.5,
            lfo: None,
            }];
        assert!(song_err(&arr));
    }

    #[test]
    fn generator_automation_rejects_missing_generator() {
        let mut arr = arrangement();
        arr.tracks[0].automation = vec![RawAutoCurve {
            param: "gen.p11".to_string(),
            points: vec![rp(0.0, 0.0)],
            interp: "linear".to_string(),
            tension: 0.5,
            lfo: None,
            }];
        assert!(song_err(&arr));
    }

    #[test]
    fn truncate_notes_cuts_at_clip_end() {
        // A pattern with a note much longer than the clip.
        fn long_pattern() -> PatternData {
            PatternData {
                id: "long".to_string(),
                name: "Long".to_string(),
                steps: 16,
                channels: vec![ChannelData {
                    instrument: Instrument::Lead,
                    notes: vec![NoteData {
                        start_step: 0.0,
                        len_steps: 64.0, // 4 bars, clip is 1 bar
                        pitch: 69,
                        velocity: 0.9,
                        pan: 0.0,
                    }],
                }],
            }
        }
        fn arr_with(truncate: bool) -> ArrangementData {
            let mut arr = arrangement();
            arr.truncate_notes = truncate;
            arr.patterns = vec![long_pattern()];
            arr.tracks[0].clips = vec![ClipData {
                pattern: 0,
                start_beat: 0,
                bars: 1,
            }];
            arr
        }
        let beat = 44100u64 * 60 / 120;
        let clip_end = 4 * beat; // 1 bar
        // With truncation the note is cut at the clip end.
        let song_t = song(&arr_with(true));
        for e in &song_t.track_events[0] {
            assert!(
                e.sample + e.len_samples <= clip_end,
                "event extends past clip end"
            );
        }
        // Without truncation the note rings past the clip end.
        let song2 = song(&arr_with(false));
        let max_end = song2.track_events[0]
            .iter()
            .map(|e| e.sample + e.len_samples)
            .max()
            .unwrap_or(0);
        assert!(max_end > clip_end);
    }

    fn test_buffer(frames: usize) -> Arc<SampleBuffer> {
        Arc::new(SampleBuffer {
            frames: Arc::new(vec![0.0f32; frames * 2]),
            len: frames,
            source_path: "test".to_string(),
            source_sample_rate: 44100,
        })
    }

    fn audio_arr(asset: &str, start_beat: f64, length_beats: f64) -> ArrangementData {
        let mut arr = arrangement();
        arr.samples = vec![asset.to_string()];
        arr.tracks[0].audio_clips.push(RawAudioClip {
            asset: asset.to_string(),
            start_beat,
            length_beats,
            start_offset_beats: 0.0,
            gain: 1.0,
            pan: 0.0,
            pitch_semitones: 0.0,
            fine_cents: 0.0,
            reverse: false,
            muted: false,
        });
        arr
    }

    #[test]
    fn audio_clip_needs_loaded_sample() {
        // Declared but not decoded: loud error, like a missing plugin.
        let arr = audio_arr("ghost", 0.0, 1.0);
        assert!(Song::from_arrangement(44100, &arr, &HashMap::new()).is_err());
    }

    #[test]
    fn audio_clip_event_geometry() {
        // 120 bpm: one beat = 22050 samples at 44.1 kHz.
        let mut reg = HashMap::new();
        reg.insert("s1".to_string(), test_buffer(44100));
        let mut arr = audio_arr("s1", 1.0, 2.0);
        {
            let ac = &mut arr.tracks[0].audio_clips[0];
            ac.start_offset_beats = 0.5;
            ac.pitch_semitones = 12.0; // ratio 2.0: duration halves
        }
        let song = Song::from_arrangement(44100, &arr, &reg).unwrap();
        let ev = &song.track_audio_clips[0][0];
        assert_eq!(ev.sample, 22050);
        assert_eq!(ev.offset_frames, 11025);
        assert!((ev.ratio - 2.0).abs() < 1e-6);
        // 2 beats = 44100 out frames, but only 33075 buffer frames remain
        // after the offset, and ratio 2 consumes 2 per out frame.
        assert_eq!(ev.play_frames, 16537);
    }

    #[test]
    fn audio_clip_pitch_down_extends_play() {
        // -12 semitones: ratio 0.5, buffer lasts twice as long.
        let mut reg = HashMap::new();
        reg.insert("s1".to_string(), test_buffer(44100));
        let mut arr = audio_arr("s1", 0.0, 4.0);
        arr.tracks[0].audio_clips[0].pitch_semitones = -12.0;
        let song = Song::from_arrangement(44100, &arr, &reg).unwrap();
        let ev = &song.track_audio_clips[0][0];
        assert!((ev.ratio - 0.5).abs() < 1e-6);
        // 4 beats = 88200 out frames; buffer allows 44100/0.5 = 88200.
        assert_eq!(ev.play_frames, 88200);
    }

    #[test]
    fn audio_clip_bad_geometry_rejected() {
        let mut reg = HashMap::new();
        reg.insert("s1".to_string(), test_buffer(44100));
        // Negative start.
        let mut arr = audio_arr("s1", -1.0, 1.0);
        assert!(Song::from_arrangement(44100, &arr, &reg).is_err());
        // Zero length.
        let mut arr = audio_arr("s1", 0.0, 0.0);
        assert!(Song::from_arrangement(44100, &arr, &reg).is_err());
        // Offset past the end of the sample.
        let mut arr = audio_arr("s1", 0.0, 1.0);
        arr.tracks[0].audio_clips[0].start_offset_beats = 10.0;
        assert!(Song::from_arrangement(44100, &arr, &reg).is_err());
        // Pitch out of range.
        let mut arr = audio_arr("s1", 0.0, 1.0);
        arr.tracks[0].audio_clips[0].pitch_semitones = 100.0;
        assert!(Song::from_arrangement(44100, &arr, &reg).is_err());
    }
}
