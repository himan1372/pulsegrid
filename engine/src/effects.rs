//! Built-in insert effects for track strips.
//!
//! Four small, original effects covering the basics:
//! * [`Delay`]: tempo-agnostic feedback echo (ms-based).
//! * [`Drive`]: soft-saturation overdrive.
//! * [`Filter`]: one-pole lowpass.
//! * [`Ducker`]: sidechain-driven gain reduction (envelope follower on
//!   the track's sidechain bus; FL Studio-style external sidechain).
//!
//! All state is preallocated at build time; `process` does no allocation.
//! Effects are built on the control thread or on arrangement change --
//! never per block.

use std::f32::consts::PI;

use crate::plugins::HostedClapPlugin;
use crate::vst3::HostedVst3Plugin;

/// Validated effect parameters (control-thread data).
#[derive(Clone, Debug, PartialEq)]
pub enum FxParams {
    Delay {
        time_ms: f32,
        feedback: f32,
        mix: f32,
    },
    Drive {
        amount: f32,
    },
    Filter {
        cutoff: f32,
    },
    /// Sidechain ducker: an envelope follower reads the track's
    /// sidechain bus (fed by sidechain-marked sends) and applies
    /// downward compression to the main buffer. Engine units:
    /// threshold linear 0.01-1.0, ratio 1-20, attack/release ms.
    Ducker {
        threshold: f32,
        ratio: f32,
        attack_ms: f32,
        release_ms: f32,
    },
    /// Pitch shifter (duration-preserving). Engine units:
    /// semitones -12..=12, mix 0..=1.
    PitchShift { semitones: f32, mix: f32 },
    /// A hosted CLAP audio-effect plugin. `params` are (CLAP param id,
    /// value) pairs; the plugin library is resolved from `plugin_id`
    /// (falling back to `path`) when the effect is built.
    Plugin {
        plugin_id: String,
        path: String,
        params: Vec<(u32, f64)>,
        /// Opaque CLAP state blob from the project (Base64-decoded by the
        /// bridge). When the plugin accepts it, it is authoritative and
        /// `params` are skipped. `None` = params only.
        state: Option<Vec<u8>>,
    },
    /// A hosted VST3 audio-effect plugin. `params` are (VST3 param id,
    /// normalized value) pairs; the plugin library is resolved from
    /// `plugin_id` (falling back to `path`) when the effect is built.
    /// VST3 state blobs are not yet persisted (params only in v1).
    Vst3Plugin {
        plugin_id: String,
        path: String,
        params: Vec<(u32, f64)>,
    },
}

/// Addressable effect parameter for automation. Engine units.
/// `PluginParam` carries the raw CLAP parameter id.
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum FxParamId {
    DelayTimeMs,
    DelayFeedback,
    DelayMix,
    DriveAmount,
    FilterCutoff,
    DuckerThreshold,
    DuckerRatio,
    DuckerAttackMs,
    DuckerReleaseMs,
    PitchSemitones,
    PitchMix,
    PluginParam(u32),
}

impl FxParamId {
    /// Parse "fx{i}.name" suffixes: ("time_ms", "feedback", "mix",
    /// "amount", "cutoff", "threshold", "ratio", "attack_ms",
    /// "release_ms") or "p{clap_id}" for plugin parameters.
    pub fn from_name(name: &str) -> Option<Self> {
        if let Some(id) = name.strip_prefix('p') {
            if let Ok(id) = id.parse::<u32>() {
                return Some(FxParamId::PluginParam(id));
            }
        }
        match name {
            "time_ms" => Some(FxParamId::DelayTimeMs),
            "feedback" => Some(FxParamId::DelayFeedback),
            "mix" => Some(FxParamId::DelayMix),
            "amount" => Some(FxParamId::DriveAmount),
            "cutoff" => Some(FxParamId::FilterCutoff),
            "threshold" => Some(FxParamId::DuckerThreshold),
            "ratio" => Some(FxParamId::DuckerRatio),
            "attack_ms" => Some(FxParamId::DuckerAttackMs),
            "release_ms" => Some(FxParamId::DuckerReleaseMs),
            "semitones" => Some(FxParamId::PitchSemitones),
            "pitch_mix" => Some(FxParamId::PitchMix),
            _ => None,
        }
    }

    /// Engine-unit range for validation. Plugin params are validated
    /// against the plugin's own range at load; here we accept any finite
    /// value (checked in `set_param` against the queued value).
    pub fn range(&self) -> (f32, f32) {
        match self {
            FxParamId::DelayTimeMs => (10.0, 2000.0),
            FxParamId::DelayFeedback => (0.0, 0.95),
            FxParamId::DelayMix => (0.0, 1.0),
            FxParamId::DriveAmount => (0.0, 1.0),
            FxParamId::FilterCutoff => (20.0, 20000.0),
            FxParamId::DuckerThreshold => (0.01, 1.0),
            FxParamId::DuckerRatio => (1.0, 20.0),
            FxParamId::DuckerAttackMs => (0.1, 100.0),
            FxParamId::DuckerReleaseMs => (1.0, 1000.0),
            FxParamId::PitchSemitones => (-12.0, 12.0),
            FxParamId::PitchMix => (0.0, 1.0),
            FxParamId::PluginParam(_) => (f32::NEG_INFINITY, f32::INFINITY),
        }
    }
}

impl FxParams {
    /// Current engine-unit value of an automatable param, or None when
    /// the param does not belong to this effect kind.
    pub fn param_value(&self, param: FxParamId) -> Option<f32> {
        match (self, param) {
            (FxParams::Delay { time_ms, .. }, FxParamId::DelayTimeMs) => Some(*time_ms),
            (FxParams::Delay { feedback, .. }, FxParamId::DelayFeedback) => {
                Some(*feedback)
            }
            (FxParams::Delay { mix, .. }, FxParamId::DelayMix) => Some(*mix),
            (FxParams::Drive { amount }, FxParamId::DriveAmount) => Some(*amount),
            (FxParams::Filter { cutoff }, FxParamId::FilterCutoff) => Some(*cutoff),
            (FxParams::Ducker { threshold, .. }, FxParamId::DuckerThreshold) => {
                Some(*threshold)
            }
            (FxParams::Ducker { ratio, .. }, FxParamId::DuckerRatio) => Some(*ratio),
            (FxParams::Ducker { attack_ms, .. }, FxParamId::DuckerAttackMs) => {
                Some(*attack_ms)
            }
            (FxParams::Ducker { release_ms, .. }, FxParamId::DuckerReleaseMs) => {
                Some(*release_ms)
            }
            (FxParams::PitchShift { semitones, .. }, FxParamId::PitchSemitones) => {
                Some(*semitones)
            }
            (FxParams::PitchShift { mix, .. }, FxParamId::PitchMix) => Some(*mix),
            (FxParams::Plugin { params, .. }, FxParamId::PluginParam(id)) => {
                params.iter().find(|(pid, _)| *pid == id).map(|(_, v)| *v as f32)
            }
            (FxParams::Vst3Plugin { params, .. }, FxParamId::PluginParam(id)) => {
                params.iter().find(|(pid, _)| *pid == id).map(|(_, v)| *v as f32)
            }
            _ => None,
        }
    }
}

impl FxParams {
    /// Range-check params without allocating effect state.
    pub fn validate(&self) -> Result<(), String> {
        match self {
            FxParams::Delay {
                time_ms,
                feedback,
                mix,
            } => {
                if !(10.0..=2000.0).contains(time_ms) {
                    return Err(format!("delay time {} ms out of range 10-2000", time_ms));
                }
                if !(0.0..=0.95).contains(feedback) {
                    return Err(format!(
                        "delay feedback {} out of range 0-0.95",
                        feedback
                    ));
                }
                if !(0.0..=1.0).contains(mix) {
                    return Err(format!("delay mix {} out of range 0-1", mix));
                }
            }
            FxParams::Drive { amount } => {
                if !(0.0..=1.0).contains(amount) {
                    return Err(format!("drive amount {} out of range 0-1", amount));
                }
            }
            FxParams::Filter { cutoff } => {
                if !(20.0..=20000.0).contains(cutoff) {
                    return Err(format!(
                        "filter cutoff {} Hz out of range 20-20000",
                        cutoff
                    ));
                }
            }
            FxParams::Ducker {
                threshold,
                ratio,
                attack_ms,
                release_ms,
            } => {
                if !(0.01..=1.0).contains(threshold) {
                    return Err(format!(
                        "ducker threshold {} out of range 0.01-1",
                        threshold
                    ));
                }
                if !(1.0..=20.0).contains(ratio) {
                    return Err(format!("ducker ratio {} out of range 1-20", ratio));
                }
                if !(0.1..=100.0).contains(attack_ms) {
                    return Err(format!(
                        "ducker attack {} ms out of range 0.1-100",
                        attack_ms
                    ));
                }
                if !(1.0..=1000.0).contains(release_ms) {
                    return Err(format!(
                        "ducker release {} ms out of range 1-1000",
                        release_ms
                    ));
                }
            }
            FxParams::PitchShift { semitones, mix } => {
                if !(-12.0..=12.0).contains(semitones) {
                    return Err(format!(
                        "pitch shift semitones {} out of range -12..12",
                        semitones
                    ));
                }
                if !(0.0..=1.0).contains(mix) {
                    return Err(format!(
                        "pitch shift mix {} out of range 0-1",
                        mix
                    ));
                }
            }
            FxParams::Plugin {
                plugin_id,
                path,
                params,
                state: _,
            } => {
                if plugin_id.is_empty() {
                    return Err("plugin effect has no plugin id".to_string());
                }
                if path.is_empty() {
                    return Err("plugin effect has no library path".to_string());
                }
                if params.len() > 1024 {
                    return Err("plugin effect has too many parameters".to_string());
                }
                for (id, value) in params {
                    if !value.is_finite() {
                        return Err(format!("plugin param {} has non-finite value", id));
                    }
                }
            }
            FxParams::Vst3Plugin {
                plugin_id,
                path,
                params,
            } => {
                if plugin_id.is_empty() {
                    return Err("VST3 effect has no plugin id".to_string());
                }
                if path.is_empty() {
                    return Err("VST3 effect has no library path".to_string());
                }
                if params.len() > 1024 {
                    return Err("VST3 effect has too many parameters".to_string());
                }
                for (id, value) in params {
                    if !value.is_finite() {
                        return Err(format!("VST3 param {} has non-finite value", id));
                    }
                    if !(0.0..=1.0).contains(value) {
                        return Err(format!(
                            "VST3 param {} out of normalized range 0.0-1.0",
                            id
                        ));
                    }
                }
            }
        }
        Ok(())
    }
}

pub(crate) struct Delay {
    buf: Vec<f32>, // stereo-interleaved, max_delay*2
    max_delay: usize,
    write: usize,
    delay: usize,
    feedback: f32,
    mix: f32,
}

impl Delay {
    fn new(sample_rate: u32, time_ms: f32, feedback: f32, mix: f32) -> Result<Self, String> {
        if !(10.0..=2000.0).contains(&time_ms) {
            return Err(format!("delay time {} ms out of range 10-2000", time_ms));
        }
        if !(0.0..=0.95).contains(&feedback) {
            return Err(format!("delay feedback {} out of range 0-0.95", feedback));
        }
        if !(0.0..=1.0).contains(&mix) {
            return Err(format!("delay mix {} out of range 0-1", mix));
        }
        let max_delay = (sample_rate as f32 * 2.0) as usize;
        let delay = ((sample_rate as f32 * time_ms / 1000.0).round() as usize)
            .clamp(1, max_delay);
        Ok(Delay {
            buf: vec![0.0; max_delay * 2],
            max_delay,
            write: 0,
            delay,
            feedback,
            mix,
        })
    }

    fn process(&mut self, buf: &mut [f32]) {
        debug_assert!(buf.len() % 2 == 0);
        let dry_gain = 1.0 - self.mix;
        for i in (0..buf.len()).step_by(2) {
            let read = (self.write + self.max_delay - self.delay) % self.max_delay;
            for ch in 0..2 {
                let delayed = self.buf[read * 2 + ch];
                let dry = buf[i + ch];
                buf[i + ch] = dry * dry_gain + delayed * self.mix;
                self.buf[self.write * 2 + ch] = dry + delayed * self.feedback;
            }
            self.write = (self.write + 1) % self.max_delay;
        }
    }

    /// Live update for automation: only the read offset changes, the
    /// delay-line state is preserved (no clicks from rebuilding).
    fn set_time_ms(&mut self, sample_rate: u32, time_ms: f32) -> Result<(), String> {
        if !(10.0..=2000.0).contains(&time_ms) {
            return Err(format!("delay time {} ms out of range 10-2000", time_ms));
        }
        self.delay = ((sample_rate as f32 * time_ms / 1000.0).round() as usize)
            .clamp(1, self.max_delay);
        Ok(())
    }

    fn set_feedback(&mut self, feedback: f32) -> Result<(), String> {
        if !(0.0..=0.95).contains(&feedback) {
            return Err(format!(
                "delay feedback {} out of range 0-0.95",
                feedback
            ));
        }
        self.feedback = feedback;
        Ok(())
    }

    fn set_mix(&mut self, mix: f32) -> Result<(), String> {
        if !(0.0..=1.0).contains(&mix) {
            return Err(format!("delay mix {} out of range 0-1", mix));
        }
        self.mix = mix;
        Ok(())
    }
}

pub(crate) struct Drive {
    amount: f32,
    norm: f32,
}

impl Drive {
    fn new(amount: f32) -> Result<Self, String> {
        if !(0.0..=1.0).contains(&amount) {
            return Err(format!("drive amount {} out of range 0-1", amount));
        }
        let k = 1.0 + amount * 9.0;
        Ok(Drive {
            amount,
            norm: k.tanh(),
        })
    }

    fn process(&self, buf: &mut [f32]) {
        // A 0% drive is a bypass: the normalized tanh curve would
        // otherwise boost small signals (tanh(x)/tanh(1) > x).
        if self.amount == 0.0 {
            return;
        }
        let k = 1.0 + self.amount * 9.0;
        for s in buf.iter_mut() {
            *s = (*s * k).tanh() / self.norm;
        }
    }

    /// Live update for automation (stateless apart from `norm`).
    fn set_amount(&mut self, amount: f32) -> Result<(), String> {
        if !(0.0..=1.0).contains(&amount) {
            return Err(format!("drive amount {} out of range 0-1", amount));
        }
        self.amount = amount;
        self.norm = (1.0 + amount * 9.0).tanh();
        Ok(())
    }
}

pub(crate) struct Filter {
    alpha: f32,
    s: [f32; 2],
}

impl Filter {
    fn new(sample_rate: u32, cutoff: f32) -> Result<Self, String> {
        if !(20.0..=20000.0).contains(&cutoff) {
            return Err(format!("filter cutoff {} Hz out of range 20-20000", cutoff));
        }
        let alpha = 1.0 - (-2.0 * PI * cutoff / sample_rate as f32).exp();
        Ok(Filter { alpha, s: [0.0; 2] })
    }

    fn process(&mut self, buf: &mut [f32]) {
        debug_assert!(buf.len() % 2 == 0);
        for i in (0..buf.len()).step_by(2) {
            for ch in 0..2 {
                self.s[ch] += self.alpha * (buf[i + ch] - self.s[ch]);
                buf[i + ch] = self.s[ch];
            }
        }
    }

    /// Live update for automation: only the coefficient changes, the
    /// filter state is preserved (no clicks from rebuilding).
    fn set_cutoff(&mut self, sample_rate: u32, cutoff: f32) -> Result<(), String> {
        if !(20.0..=20000.0).contains(&cutoff) {
            return Err(format!(
                "filter cutoff {} Hz out of range 20-20000",
                cutoff
            ));
        }
        self.alpha = 1.0 - (-2.0 * PI * cutoff / sample_rate as f32).exp();
        Ok(())
    }
}

/// Sidechain ducker: an envelope follower on the track's sidechain bus
/// (fed by sidechain-marked sends) drives downward compression of the
/// main buffer. This is the FL Studio-style external sidechain: the
/// routing graph provides the signal, the plugin decides what to do
/// with it. With no sidechain signal the envelope rests at zero and
/// the effect is transparent.
pub(crate) struct Ducker {
    threshold: f32,
    ratio: f32,
    attack_ms: f32,
    release_ms: f32,
    attack_coef: f32,
    release_coef: f32,
    env: f32,
    sample_rate: u32,
}

impl Ducker {
    fn coef(ms: f32, sample_rate: u32) -> f32 {
        // One-pole smoothing coefficient for a time constant of `ms`.
        1.0 - (-1.0 / (ms * sample_rate as f32 / 1000.0)).exp()
    }

    fn new(
        sample_rate: u32,
        threshold: f32,
        ratio: f32,
        attack_ms: f32,
        release_ms: f32,
    ) -> Result<Self, String> {
        let mut d = Ducker {
            threshold: 0.5,
            ratio: 4.0,
            attack_ms: 0.0,
            release_ms: 0.0,
            attack_coef: 0.0,
            release_coef: 0.0,
            env: 0.0,
            sample_rate,
        };
        d.set_threshold(threshold)?;
        d.set_ratio(ratio)?;
        d.set_attack_ms(sample_rate, attack_ms)?;
        d.set_release_ms(sample_rate, release_ms)?;
        Ok(d)
    }

    /// `sidechain` is the track's sidechain bus (stereo-interleaved,
    /// same length as `buf`, or None). Only the peak envelope matters.
    fn process(&mut self, buf: &mut [f32], sidechain: Option<&[f32]>) {
        debug_assert!(buf.len() % 2 == 0);
        for i in (0..buf.len()).step_by(2) {
            let sc = sidechain
                .map(|sc| {
                    let l = sc.get(i).copied().unwrap_or(0.0).abs();
                    let r = sc.get(i + 1).copied().unwrap_or(0.0).abs();
                    l.max(r)
                })
                .unwrap_or(0.0);
            let coef = if sc > self.env {
                self.attack_coef
            } else {
                self.release_coef
            };
            self.env += (sc - self.env) * coef;
            let gain = if self.env > self.threshold && self.env > 1e-9 {
                // Standard downward compressor curve above threshold.
                let compressed =
                    self.threshold + (self.env - self.threshold) / self.ratio;
                (compressed / self.env).min(1.0)
            } else {
                1.0
            };
            buf[i] *= gain;
            buf[i + 1] *= gain;
        }
    }

    fn set_threshold(&mut self, threshold: f32) -> Result<(), String> {
        if !(0.01..=1.0).contains(&threshold) {
            return Err(format!(
                "ducker threshold {} out of range 0.01-1",
                threshold
            ));
        }
        self.threshold = threshold;
        Ok(())
    }

    fn set_ratio(&mut self, ratio: f32) -> Result<(), String> {
        if !(1.0..=20.0).contains(&ratio) {
            return Err(format!("ducker ratio {} out of range 1-20", ratio));
        }
        self.ratio = ratio;
        Ok(())
    }

    fn set_attack_ms(&mut self, sample_rate: u32, ms: f32) -> Result<(), String> {
        if !(0.1..=100.0).contains(&ms) {
            return Err(format!("ducker attack {} ms out of range 0.1-100", ms));
        }
        self.attack_ms = ms;
        self.attack_coef = Self::coef(ms, sample_rate);
        Ok(())
    }

    fn set_release_ms(&mut self, sample_rate: u32, ms: f32) -> Result<(), String> {
        if !(1.0..=1000.0).contains(&ms) {
            return Err(format!(
                "ducker release {} ms out of range 1-1000",
                ms
            ));
        }
        self.release_ms = ms;
        self.release_coef = Self::coef(ms, sample_rate);
        Ok(())
    }
}

    /// Pitch shifter (duration-preserving) via a dual-head delay line.
///
/// Classic harmonizer architecture: two read heads advance through the
/// input delay line at `ratio` (2^(semitones/12)) samples per output
/// sample - i.e. the output is the input resampled at `ratio`, which
/// scales pitch by `ratio` while the 1:1 input/output sample clock
/// preserves duration. Each head's delay drifts with (1 - ratio) per
/// sample; when a head leaves the [D_MIN, D_MIN + N] window it wraps by
/// the full N frames. The heads stay N/2 apart and each carries a
/// sin^2 fade that is 0 at its wrap moment, so the wrap discontinuity
/// is always hidden under the other head (fades sum to 1).
///
/// Why this instead of granular overlap-add: naive fixed-hop granular
/// resynthesis suffers phase cancellation between overlapping grains at
/// ratios < 1 (a -12 semitone shift measured 186 Hz instead of 220 Hz in
/// testing - wrong pitch, not just low quality). The delay-line
/// formulation is exact resampling between wraps, so pitch is correct
/// by construction; the residual artifact is gentle delay modulation
/// (warble), typical of this algorithm class.
///
/// This is the "pitch shifting" row of the time/pitch operation table
/// (see the time-stretching research notes): creative-grade processing,
/// NOT a transparent Elastique-style stretcher - no formant
/// preservation, no transient detection. True time-stretch of arbitrary
/// duration belongs at a future sample-clip layer: stretching live
/// insert input slower than realtime would accumulate unbounded
/// latency, so only pitch (not time) is offered on the live insert
/// path.
///
/// Reported PDC latency is the nominal head delay (D_MIN + N/2); the
/// true delay modulates around it, which is inherent to the algorithm.
pub(crate) struct PitchShift {
    semitones: f32, // -12..=12
    mix: f32,       // 0..=1
    ratio: f32,     // 2^(semitones/12)
    /// Circular input delay line, stereo-interleaved.
    buf: Vec<f32>,
    /// Monotonic frame counter; buffer index is `write % CAP`.
    write: u64,
    /// Read-head delays in frames; kept N/2 apart, each wrapped into
    /// [D_MIN, D_MIN + N].
    d1: f32,
    d2: f32,
    /// Dry delay line (stereo-interleaved, LAT_NOMINAL frames) for
    /// wet/dry time alignment.
    dry: Vec<f32>,
    dry_write: usize,
    /// True when |semitones| is tiny: the heads are bypassed and the
    /// output is the dry delayed signal (mixing two fixed delays at
    /// unity would comb-filter).
    unity: bool,
}

impl PitchShift {
    /// Delay-modulation range (frames); also the wrap distance.
    const N: usize = 4096;
    /// Minimum head delay (frames); leaves interpolation margin.
    const D_MIN: usize = 64;
    /// Circular delay-line capacity (frames); exceeds D_MIN + N.
    const CAP: usize = 8192;
    /// Nominal latency (frames) = D_MIN + N/2; reported for PDC and
    /// used for the dry delay line.
    const LAT_NOMINAL: usize = 2112;

    fn new(semitones: f32, mix: f32) -> Result<Self, String> {
        if !(-12.0..=12.0).contains(&semitones) {
            return Err(format!(
                "pitch shift semitones {} out of range -12..12",
                semitones
            ));
        }
        if !(0.0..=1.0).contains(&mix) {
            return Err(format!("pitch shift mix {} out of range 0-1", mix));
        }
        let lo = Self::D_MIN as f32;
        Ok(PitchShift {
            semitones,
            mix,
            ratio: 2.0f32.powf(semitones / 12.0),
            buf: vec![0.0; Self::CAP * 2],
            write: 0,
            d1: lo + Self::N as f32 / 4.0,
            d2: lo + Self::N as f32 * 3.0 / 4.0,
            dry: vec![0.0; Self::LAT_NOMINAL * 2],
            dry_write: 0,
            unity: semitones.abs() < 0.01,
        })
    }

    /// Linear-interpolated read `delay` frames behind the write head.
    /// The delay line is zero-primed, so reads before any input are
    /// silence. No allocation.
    fn delay_read(&self, delay: f32, ch: usize) -> f32 {
        let di = delay as u64; // floor; delay > 0 always
        let frac = delay - di as f32;
        let cap = Self::CAP as u64;
        let i0 = self.write.wrapping_sub(di) % cap;
        let i1 = self.write.wrapping_sub(di).wrapping_sub(1) % cap;
        let s0 = self.buf[i0 as usize * 2 + ch];
        let s1 = self.buf[i1 as usize * 2 + ch];
        s0 * (1.0 - frac) + s1 * frac
    }

    fn process(&mut self, buf: &mut [f32]) {
        debug_assert!(buf.len() % 2 == 0);
        let wet_gain = self.mix;
        let dry_gain = 1.0 - self.mix;
        let lo = Self::D_MIN as f32;
        let n = Self::N as f32;
        let hi = lo + n;
        // Dry path first (its delay is fixed).
        for i in (0..buf.len()).step_by(2) {
            let in_l = buf[i];
            let in_r = buf[i + 1];
            // Record input into the delay line.
            let bi = (self.write % Self::CAP as u64) as usize * 2;
            self.buf[bi] = in_l;
            self.buf[bi + 1] = in_r;
            // Dry delay line for time alignment.
            let dry_l = self.dry[self.dry_write * 2];
            let dry_r = self.dry[self.dry_write * 2 + 1];
            self.dry[self.dry_write * 2] = in_l;
            self.dry[self.dry_write * 2 + 1] = in_r;
            self.dry_write = (self.dry_write + 1) % Self::LAT_NOMINAL;
            // Shifted signal.
            let (sh_l, sh_r) = if self.unity {
                (dry_l, dry_r)
            } else {
                // Head delays drift at (1 - ratio); wrap by N to stay
                // in range. Steps are < 1 sample (ratio in [0.5, 2]),
                // so at most one wrap per head per sample.
                self.d1 += 1.0 - self.ratio;
                self.d2 += 1.0 - self.ratio;
                if self.d1 < lo {
                    self.d1 += n;
                } else if self.d1 > hi {
                    self.d1 -= n;
                }
                if self.d2 < lo {
                    self.d2 += n;
                } else if self.d2 > hi {
                    self.d2 -= n;
                }
                // sin^2 fades: 0 at the wrap moment, 1 mid-cycle; the
                // heads are N/2 apart so the fades sum to 1.
                let t1 = std::f32::consts::PI * (self.d1 - lo) / n;
                let t2 = std::f32::consts::PI * (self.d2 - lo) / n;
                let f1 = t1.sin() * t1.sin();
                let f2 = t2.sin() * t2.sin();
                (
                    self.delay_read(self.d1, 0) * f1
                        + self.delay_read(self.d2, 0) * f2,
                    self.delay_read(self.d1, 1) * f1
                        + self.delay_read(self.d2, 1) * f2,
                )
            };
            buf[i] = sh_l * wet_gain + dry_l * dry_gain;
            buf[i + 1] = sh_r * wet_gain + dry_r * dry_gain;
            self.write += 1;
        }
    }

    /// Live update for automation: only the ratio changes; head delays
    /// continue from their current positions (no clicks from
    /// retuning). Crossing the unity threshold toggles the bypass.
    fn set_semitones(&mut self, semitones: f32) -> Result<(), String> {
        if !(-12.0..=12.0).contains(&semitones) {
            return Err(format!(
                "pitch shift semitones {} out of range -12..12",
                semitones
            ));
        }
        self.semitones = semitones;
        self.ratio = 2.0f32.powf(semitones / 12.0);
        self.unity = semitones.abs() < 0.01;
        Ok(())
    }

    fn set_mix(&mut self, mix: f32) -> Result<(), String> {
        if !(0.0..=1.0).contains(&mix) {
            return Err(format!("pitch shift mix {} out of range 0-1", mix));
        }
        self.mix = mix;
        Ok(())
    }
}

/// A live effect instance. Built from [`FxParams`] on arrangement change.
/// Crate-internal: only the graph consumes effects.
pub(crate) enum Effect {
    Delay(Delay),
    Drive(Drive),
    Filter(Filter),
    Ducker(Ducker),
    PitchShift(PitchShift),
    Plugin(HostedClapPlugin),
    Vst3Plugin(HostedVst3Plugin),
}

impl Effect {
    /// Build a live effect. `slot` identifies this effect's plugin slot
    /// (track + insert index); when the effect is a CLAP plugin, its
    /// main-thread instance is pushed to `sink` with that key so the
    /// control thread can save its state blob later.
    pub fn build(
        params: &FxParams,
        sample_rate: u32,
        slot: Option<crate::plugins::PluginSlotKey>,
        sink: &mut crate::plugins::PluginInstanceSink,
    ) -> Result<Self, String> {
        match params {
            FxParams::Delay {
                time_ms,
                feedback,
                mix,
            } => Delay::new(sample_rate, *time_ms, *feedback, *mix).map(Effect::Delay),
            FxParams::Drive { amount } => Drive::new(*amount).map(Effect::Drive),
            FxParams::Filter { cutoff } => {
                Filter::new(sample_rate, *cutoff).map(Effect::Filter)
            }
            FxParams::Ducker {
                threshold,
                ratio,
                attack_ms,
                release_ms,
            } => Ducker::new(sample_rate, *threshold, *ratio, *attack_ms, *release_ms)
                .map(Effect::Ducker),
            FxParams::PitchShift { semitones, mix } => {
                PitchShift::new(*semitones, *mix).map(Effect::PitchShift)
            }
            FxParams::Plugin {
                plugin_id,
                path,
                params,
                state,
            } => {
                // Prefer the stored path; fall back to a fresh scan by id
                // (the user may have moved their plugin folders).
                let lib = if std::path::Path::new(path).is_file() {
                    std::path::PathBuf::from(path)
                } else {
                    crate::plugins::find_plugin_path(plugin_id).ok_or_else(|| {
                        format!(
                            "plugin '{}' not found (looked in {} and the CLAP search paths)",
                            plugin_id, path
                        )
                    })?
                };
                let (hosted, instance) = HostedClapPlugin::load(
                    &lib,
                    plugin_id,
                    sample_rate,
                    params,
                    state.as_deref(),
                    slot,
                    &sink.events,
                )?;
                if let Some(key) = slot {
                    sink.push(key, instance);
                }
                Ok(Effect::Plugin(hosted))
            }
            FxParams::Vst3Plugin {
                plugin_id,
                path,
                params,
            } => {
                let lib = if std::path::Path::new(path).is_file() {
                    std::path::PathBuf::from(path)
                } else {
                    crate::vst3::find_vst3_path(plugin_id).ok_or_else(|| {
                        format!(
                            "VST3 plugin '{}' not found (looked in {} and the VST3 search paths)",
                            plugin_id, path
                        )
                    })?
                };
                let hosted =
                    HostedVst3Plugin::load(&lib, sample_rate, params)?;
                Ok(Effect::Vst3Plugin(hosted))
            }
        }
    }

    pub fn process(&mut self, buf: &mut [f32], sidechain: Option<&[f32]>) {
        match self {
            Effect::Delay(d) => d.process(buf),
            Effect::Drive(d) => d.process(buf),
            Effect::Filter(f) => f.process(buf),
            Effect::Ducker(d) => d.process(buf, sidechain),
            Effect::PitchShift(p) => p.process(buf),
            Effect::Plugin(p) => p.process_block(buf),
            Effect::Vst3Plugin(p) => p.process_block(buf),
        }
    }

    /// Plugin Delay Compensation: reported latency in stereo frames.
    /// Built-in FX have zero latency except the granular pitch shifter,
    /// whose grain lookahead is a fixed GRAIN_LEN frames. CLAP plugins
    /// report via the latency extension (CLAP defines latency in audio
    /// frames).
    pub fn latency_samples(&self) -> u32 {
        match self {
            Effect::Delay(_) | Effect::Drive(_) | Effect::Filter(_) | Effect::Ducker(_) => 0,
            Effect::PitchShift(_) => PitchShift::LAT_NOMINAL as u32,
            Effect::Plugin(p) => p.latency_samples(),
            Effect::Vst3Plugin(p) => p.latency_samples(),
        }
    }

    /// True for effects that read the track's sidechain bus (detector
    /// input). Only the Ducker does today: CLAP hosting is stereo
    /// in/out with no sidechain routing, so hosted plugins never do.
    /// The PDC planner uses this to align the detector path with the
    /// program path (FL Studio's APDC explicitly covers sidechains).
    pub fn uses_sidechain(&self) -> bool {
        matches!(self, Effect::Ducker(_))
    }

    /// Live parameter update for automation. Range-checked; applying a
    /// param to the wrong effect kind is an error. Never reallocates.
    /// Plugin parameter changes are queued as CLAP events for the next
    /// audio block.
    /// Queue a raw plugin parameter change (for modulators). Works for
    /// both CLAP and VST3 plugin effects. No-op for native effects.
    pub fn queue_plugin_param(&mut self, id: u32, value: f64) {
        match self {
            Effect::Plugin(p) => p.queue_param(id, value),
            Effect::Vst3Plugin(p) => p.queue_param(id, value),
            _ => {}
        }
    }

    pub fn set_param(
        &mut self,
        param: FxParamId,
        value: f32,
        sample_rate: u32,
    ) -> Result<(), String> {
        if let FxParamId::PluginParam(id) = param {
            return match self {
                Effect::Plugin(p) => {
                    if !value.is_finite() {
                        return Err("plugin param value is not finite".to_string());
                    }
                    p.queue_param(id, value as f64);
                    Ok(())
                }
                _ => Err("automation param does not match effect kind".to_string()),
            };
        }
        let (lo, hi) = param.range();
        if !(lo..=hi).contains(&value) {
            return Err(format!("automation value {} out of range", value));
        }
        match (self, param) {
            (Effect::Delay(d), FxParamId::DelayTimeMs) => {
                d.set_time_ms(sample_rate, value)
            }
            (Effect::Delay(d), FxParamId::DelayFeedback) => d.set_feedback(value),
            (Effect::Delay(d), FxParamId::DelayMix) => d.set_mix(value),
            (Effect::Drive(d), FxParamId::DriveAmount) => d.set_amount(value),
            (Effect::Filter(f), FxParamId::FilterCutoff) => {
                f.set_cutoff(sample_rate, value)
            }
            (Effect::Ducker(d), FxParamId::DuckerThreshold) => d.set_threshold(value),
            (Effect::Ducker(d), FxParamId::DuckerRatio) => d.set_ratio(value),
            (Effect::Ducker(d), FxParamId::DuckerAttackMs) => {
                d.set_attack_ms(sample_rate, value)
            }
            (Effect::Ducker(d), FxParamId::DuckerReleaseMs) => {
                d.set_release_ms(sample_rate, value)
            }
            (Effect::PitchShift(p), FxParamId::PitchSemitones) => {
                p.set_semitones(value)
            }
            (Effect::PitchShift(p), FxParamId::PitchMix) => p.set_mix(value),
            _ => Err("automation param does not match effect kind".to_string()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn delay_echoes() {
        let mut d = Delay::new(44100, 100.0, 0.0, 1.0).unwrap();
        let mut buf = vec![0.0f32; 44100 * 2]; // 1s stereo
        buf[0] = 1.0;
        buf[1] = 1.0;
        d.process(&mut buf);
        // 100ms = 4410 samples; echo should appear there (wet only).
        assert!(buf[4410 * 2].abs() > 0.5);
        assert!(buf[0].abs() < 1e-6); // dry removed at mix=1
    }

    #[test]
    fn drive_is_transparent_at_zero() {
        let d = Drive::new(0.0).unwrap();
        let mut buf = vec![0.25f32; 64];
        let before = buf.clone();
        d.process(&mut buf);
        for (a, b) in buf.iter().zip(before.iter()) {
            assert!((a - b).abs() < 1e-4);
        }
    }

    #[test]
    fn filter_attenuates_highs() {
        let mut f = Filter::new(44100, 200.0).unwrap();
        // Nyquist tone on both channels: the buffer is stereo-interleaved,
        // so frames (not samples) must alternate.
        let mut buf = vec![0.0f32; 2048];
        for (i, s) in buf.iter_mut().enumerate() {
            *s = if (i / 2) % 2 == 0 { 1.0 } else { -1.0 };
        }
        f.process(&mut buf);
        let tail_peak: f32 = buf[1024..].iter().map(|s| s.abs()).fold(0.0, f32::max);
        assert!(tail_peak < 0.05);
    }

    #[test]
    fn rejects_out_of_range() {
        assert!(Delay::new(44100, 5000.0, 0.5, 0.5).is_err());
        assert!(Drive::new(2.0).is_err());
        assert!(Filter::new(44100, 5.0).is_err());
        assert!(Ducker::new(44100, 0.0, 4.0, 10.0, 100.0).is_err());
        assert!(Ducker::new(44100, 0.5, 0.5, 10.0, 100.0).is_err());
    }

    #[test]
    fn ducker_is_transparent_without_sidechain() {
        let mut d = Ducker::new(44100, 0.5, 4.0, 1.0, 50.0).unwrap();
        let mut buf = vec![0.4f32; 512];
        let before = buf.clone();
        // No sidechain signal at all.
        d.process(&mut buf, None);
        for (a, b) in buf.iter().zip(before.iter()) {
            assert!((a - b).abs() < 1e-6);
        }
        // Silent sidechain bus: envelope stays at zero.
        let sc = vec![0.0f32; 512];
        d.process(&mut buf, Some(&sc));
        for (a, b) in buf.iter().zip(before.iter()) {
            assert!((a - b).abs() < 1e-6);
        }
    }

    #[test]
    fn ducker_ducks_on_sidechain_hit() {
        // Fast attack so the envelope tracks the hit within the block.
        let mut d = Ducker::new(44100, 0.25, 4.0, 0.1, 50.0).unwrap();
        let mut buf = vec![0.4f32; 2048];
        // Loud sidechain hit on the second half of the block.
        let mut sc = vec![0.0f32; 2048];
        for s in sc[1024..].iter_mut() {
            *s = 1.0;
        }
        d.process(&mut buf, Some(&sc));
        // First half untouched (envelope still ~0 there).
        assert!((buf[0] - 0.4).abs() < 1e-4);
        // Tail of the block is ducked: env -> 1.0, threshold 0.25,
        // ratio 4 -> gain -> (0.25 + 0.75/4)/1.0 = 0.4375.
        let tail = buf[2000];
        assert!(
            (tail - 0.4 * 0.4375).abs() < 0.03,
            "expected ducked tail ~0.175, got {}",
            tail
        );
    }

    #[test]
    fn ducker_below_threshold_does_not_duck() {
        let mut d = Ducker::new(44100, 0.5, 4.0, 0.1, 50.0).unwrap();
        let mut buf = vec![0.4f32; 1024];
        // Sidechain below threshold the whole block.
        let sc = vec![0.2f32; 1024];
        d.process(&mut buf, Some(&sc));
        for s in buf.iter() {
            assert!((*s - 0.4).abs() < 1e-4);
        }
    }
}

#[cfg(test)]
mod plugin_tests {
    use super::*;
    use std::path::PathBuf;

    fn test_plugin_path() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .join("../tests/fixtures/clap/PulsegridTestGain.clap")
    }

    #[test]
    fn effect_build_plugin_applies_initial_params() {
        let path = test_plugin_path();
        let params = FxParams::Plugin {
            plugin_id: "org.pulsegrid.test-gain".to_string(),
            path: path.display().to_string(),
            params: vec![(7, 0.0)],
            state: None,
        };
        params.validate().expect("params should validate");
        let mut sink = crate::plugins::PluginInstanceSink::new(crate::plugins::PluginEventSinks::default());
        let mut effect = Effect::build(&params, 44100, None, &mut sink).expect("build should work");
        let mut buf = vec![0.5f32; 256];
        effect.process(&mut buf, None);
        assert!(
            buf.iter().all(|s| s.abs() < 1e-4),
            "gain 0.0 should silence, got peak {}",
            buf.iter().map(|s| s.abs()).fold(0.0f32, f32::max)
        );
    }

    #[test]
    fn effect_set_param_plugin_queues_event() {
        let path = test_plugin_path();
        let params = FxParams::Plugin {
            plugin_id: "org.pulsegrid.test-gain".to_string(),
            path: path.display().to_string(),
            params: vec![(7, 1.0)],
            state: None,
        };
        let mut sink = crate::plugins::PluginInstanceSink::new(crate::plugins::PluginEventSinks::default());
        let mut effect = Effect::build(&params, 44100, None, &mut sink).expect("build should work");
        effect
            .set_param(FxParamId::PluginParam(7), 0.0, 44100)
            .expect("set_param should work");
        let mut buf = vec![0.5f32; 256];
        effect.process(&mut buf, None);
        assert!(
            buf.iter().all(|s| s.abs() < 1e-4),
            "after set_param(0.0), should silence"
        );
    }
    // ---- Granular pitch shifter ----

    fn sine_440(len_frames: usize) -> Vec<f32> {
        (0..len_frames)
            .flat_map(|i| {
                let s = (2.0 * std::f32::consts::PI * 440.0 * i as f32
                    / 44100.0)
                    .sin()
                    * 0.5;
                [s, s]
            })
            .collect()
    }

    /// Dominant frequency via zero crossings on the left channel,
    /// skipping `skip` priming frames.
    fn dominant_freq(buf: &[f32], skip: usize) -> f32 {
        let frames = buf.len() / 2;
        let mut crossings = 0u32;
        let mut prev = buf[skip * 2];
        for i in (skip + 1)..frames {
            let s = buf[i * 2];
            if (prev <= 0.0) != (s <= 0.0) {
                crossings += 1;
            }
            prev = s;
        }
        crossings as f32 / 2.0 * 44100.0 / (frames - skip) as f32
    }

    fn correlation(a: &[f32], b: &[f32]) -> f32 {
        // Left channel only.
        let n = a.len() / 2;
        let (mut sxy, mut sxx, mut syy) = (0.0f64, 0.0f64, 0.0f64);
        for i in 0..n {
            let x = a[i * 2] as f64;
            let y = b[i * 2] as f64;
            sxy += x * y;
            sxx += x * x;
            syy += y * y;
        }
        (sxy / (sxx * syy).sqrt()) as f32
    }

    #[test]
    fn pitchshift_up_octave() {
        let mut p = PitchShift::new(12.0, 1.0).unwrap();
        let mut buf = sine_440(44100 * 2);
        // Process in realistic blocks.
        for chunk in buf.chunks_mut(512 * 2) {
            p.process(chunk);
        }
        let f = dominant_freq(&buf, 16384);
        assert!(
            (870.0..890.0).contains(&f),
            "expected ~880 Hz, measured {:.1}",
            f
        );
    }

    #[test]
    fn pitchshift_down_octave() {
        let mut p = PitchShift::new(-12.0, 1.0).unwrap();
        let mut buf = sine_440(44100 * 2);
        for chunk in buf.chunks_mut(512 * 2) {
            p.process(chunk);
        }
        let f = dominant_freq(&buf, 16384);
        assert!(
            (215.0..225.0).contains(&f),
            "expected ~220 Hz, measured {:.1}",
            f
        );
    }

    #[test]
    fn pitchshift_fifth_up() {
        // +7 semitones: 440 * 2^(7/12) = 659.26 Hz.
        let mut p = PitchShift::new(7.0, 1.0).unwrap();
        let mut buf = sine_440(44100 * 2);
        for chunk in buf.chunks_mut(512 * 2) {
            p.process(chunk);
        }
        let f = dominant_freq(&buf, 16384);
        assert!(
            (650.0..668.0).contains(&f),
            "expected ~659 Hz, measured {:.1}",
            f
        );
    }

    #[test]
    fn pitchshift_unity_is_transparent() {
        let mut p = PitchShift::new(0.0, 1.0).unwrap();
        let input = sine_440(44100 * 2);
        let mut buf = input.clone();
        for chunk in buf.chunks_mut(512 * 2) {
            p.process(chunk);
        }
        let lat = PitchShift::LAT_NOMINAL * 2;
        let corr = correlation(&buf[lat..], &input[..input.len() - lat]);
        assert!(corr > 0.999, "unity correlation too low: {}", corr);
    }

    #[test]
    fn pitchshift_mix_zero_is_delayed_dry() {
        let mut p = PitchShift::new(7.0, 0.0).unwrap();
        let input = sine_440(44100);
        let mut buf = input.clone();
        p.process(&mut buf);
        let lat = PitchShift::LAT_NOMINAL;
        // Priming region is silence.
        assert!(buf[..lat * 2].iter().all(|s| s.abs() < 1e-6));
        // Afterwards: exact delayed copy of the input.
        for i in lat..input.len() / 2 {
            assert!(
                (buf[i * 2] - input[(i - lat) * 2]).abs() < 1e-6,
                "dry mismatch at frame {}",
                i
            );
        }
    }

    #[test]
    fn pitchshift_reports_grain_latency() {
        let e = Effect::build(
            &FxParams::PitchShift {
                semitones: 5.0,
                mix: 1.0,
            },
            44100,
            None,
            &mut crate::plugins::PluginInstanceSink::for_tests(),
        )
        .unwrap();
        assert_eq!(e.latency_samples(), PitchShift::LAT_NOMINAL as u32);
    }

    #[test]
    fn pitchshift_param_validation() {
        assert!(PitchShift::new(13.0, 1.0).is_err());
        assert!(PitchShift::new(-12.0, 1.0).is_ok());
        assert!(PitchShift::new(0.0, 1.5).is_err());
        let mut p = PitchShift::new(0.0, 1.0).unwrap();
        assert!(p.set_semitones(12.5).is_err());
        assert!(p.set_semitones(-12.0).is_ok());
        assert!(p.set_mix(2.0).is_err());
        // Automation path range-checks too.
        let mut e = Effect::PitchShift(p);
        assert!(e
            .set_param(FxParamId::PitchSemitones, 20.0, 44100)
            .is_err());
        assert!(e.set_param(FxParamId::PitchMix, 0.5, 44100).is_ok());
    }

    #[test]
    fn pitchshift_no_nan_on_silence() {
        let mut p = PitchShift::new(-7.0, 1.0).unwrap();
        let mut buf = vec![0.0f32; 44100 * 2];
        p.process(&mut buf);
        assert!(buf.iter().all(|s| s.is_finite()));
    }

}
