//! Synthesizer voices for the Pulsegrid engine.
//!
//! All DSP state is preallocated before playback. Voice triggering only flips
//! flags and resets counters -- no allocation happens on the audio thread.
//! Noise is generated from a seeded SplitMix64 stream so offline renders are
//! bit-deterministic.

/// Built-in instrument kinds. Original Pulsegrid synthesis, not modeled on
/// any third-party product.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Instrument {
    Kick,
    Snare,
    Hat,
    Bass,
    Lead,
}

impl Instrument {
    pub fn from_str(s: &str) -> Option<Self> {
        match s {
            "kick" => Some(Instrument::Kick),
            "snare" => Some(Instrument::Snare),
            "hat" => Some(Instrument::Hat),
            "bass" => Some(Instrument::Bass),
            "lead" => Some(Instrument::Lead),
            _ => None,
        }
    }
}

/// MIDI note number -> frequency in Hz (A4 = 440).
pub fn midi_to_freq(midi: u8) -> f64 {
    440.0 * 2f64.powf((midi as f64 - 69.0) / 12.0)
}

/// Deterministic PRNG (SplitMix64). Seeded per note trigger so renders are
/// reproducible regardless of scheduling order.
struct SplitMix64(u64);

impl SplitMix64 {
    fn next_u64(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }

    /// Uniform sample in [-1.0, 1.0).
    fn next_f32(&mut self) -> f32 {
        // Use the top 24 bits for a float in [0,1), then remap.
        let bits = (self.next_u64() >> 40) as f32 / 16_777_216.0;
        bits * 2.0 - 1.0
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum EnvStage {
    Attack,
    Decay,
    Release,
    Done,
}

/// Velocity-tracking range in octaves at full bipolar deflection.
/// With amount = +/-1 and |velocity - mid| = 0.5, the per-note cutoff
/// moves by (1.0 * 0.5 * VEL_TRACK_RANGE_OCT) = 2 octaves -- a strong
/// but musical extreme, matching the spirit of FL's 3xOsc Volume
/// Tracking (bipolar amount around a middle velocity).
const VEL_TRACK_RANGE_OCT: f64 = 4.0;

/// A single preallocated voice. Reused from the pool; `trigger` rearms it.
pub struct Voice {
    active: bool,
    instrument: Instrument,
    phase: f64,
    freq: f64,
    // Kick pitch envelope
    pitch_start: f64,
    pitch_end: f64,
    pitch_t: f64,
    // ADSR / decay envelope state
    env_stage: EnvStage,
    env_level: f64,
    env_t: f64,
    attack: f64,
    decay: f64,
    sustain: f64,
    release: f64,
    note_len: f64, // seconds before release (pitched instruments)
    decay_time: f64, // seconds for pure-decay instruments
    noise: SplitMix64,
    lp_z: f64,      // one-pole lowpass state
    lp_alpha: f64,  // one-pole lowpass coefficient
    pan_l: f32,     // constant-power gains from the note's pan
    pan_r: f32,     // (set at trigger; 0.5/0.5-ish when centered)
    hp_z: f64,      // one-pole highpass state
    hp_prev: f64,
    hp_alpha: f64,
    age: u64,       // samples since trigger
    max_age: u64,   // hard kill to bound CPU
    velocity: f64,
    sample_rate: f64,
}

impl Voice {
    pub fn new(sample_rate: u32) -> Self {
        Voice {
            active: false,
            instrument: Instrument::Kick,
            phase: 0.0,
            freq: 440.0,
            pitch_start: 0.0,
            pitch_end: 0.0,
            pitch_t: 0.0,
            env_stage: EnvStage::Done,
            env_level: 0.0,
            env_t: 0.0,
            attack: 0.005,
            decay: 0.05,
            sustain: 0.8,
            release: 0.08,
            note_len: 0.25,
            decay_time: 0.3,
            noise: SplitMix64(0),
            lp_z: 0.0,
            lp_alpha: 1.0,
            hp_z: 0.0,
            hp_prev: 0.0,
            hp_alpha: 0.0,
            age: 0,
            max_age: sample_rate as u64 * 4,
            velocity: 1.0,
            sample_rate: sample_rate as f64,
            // Centered until the first trigger sets them from note pan.
            pan_l: 1.0,
            pan_r: 1.0,
        }
    }

    fn one_pole_alpha(cutoff: f64, sr: f64) -> f64 {
        // Coefficient for a one-pole lowpass: y += a * (x - y)
        let x = (-2.0 * std::f64::consts::PI * cutoff / sr).exp();
        1.0 - x
    }

    /// Rearm this voice for a new note. No allocation.
    ///
    /// `vel_track` is the track's velocity-tracking amount (bipolar
    /// -1..1, 0 = off) and `vel_track_mid` the middle velocity (0..1)
    /// where no offset is generated -- FL Studio's 3xOsc Volume Tracking
    /// model. `key_track` is the keyboard-tracking amount (bipolar
    /// -1..1, 0 = off) and `key_track_mid` the middle MIDI note (0..127)
    /// where no offset is generated -- FL Studio's Channel Keyboard
    /// Tracker model. Both are evaluated per note at trigger time (note
    /// domain): the voice's lowpass cutoff becomes
    /// base * 2^(vel_track * (velocity - vel_mid) * VEL_TRACK_RANGE_OCT
    ///            + key_track * (midi - key_mid) / 12).
    /// +1.0 key_track is 100% tracking: one octave of pitch moves the
    /// cutoff one octave (the traditional synthesizer definition).
    pub fn trigger(
        &mut self,
        instrument: Instrument,
        midi: u8,
        velocity: f64,
        note_len: f64,
        seed: u64,
        vel_track: f32,
        vel_track_mid: f32,
        key_track: f32,
        key_track_mid: f32,
        pan: f32,
    ) {
        let sr = self.sample_rate;
        // Base lowpass cutoff per instrument (None = bypassed filter).
        // Velocity tracking modulates this per note; the hat's bypassed
        // lowpass has no base cutoff to modulate, so hats skip tracking.
        let mut lp_cutoff: Option<f64> = None;
        self.active = true;
        self.instrument = instrument;
        self.phase = 0.0;
        self.freq = midi_to_freq(midi);
        self.velocity = velocity.clamp(0.0, 1.0);
        self.noise = SplitMix64(seed.wrapping_add(0x9E37_79B9_7F4A_7C15));
        self.lp_z = 0.0;
        self.hp_z = 0.0;
        self.hp_prev = 0.0;
        self.age = 0;
        // Per-note pan: constant-power gains, like the track fader.
        let (pl, pr) = crate::graph::pan_gains(1.0, pan.clamp(-1.0, 1.0));
        self.pan_l = pl;
        self.pan_r = pr;
        self.env_level = 0.0;
        self.env_t = 0.0;
        self.pitch_t = 0.0;

        match instrument {
            Instrument::Kick => {
                self.pitch_start = 160.0;
                self.pitch_end = 48.0;
                self.decay_time = 0.38;
                self.max_age = (sr * 1.0) as u64;
                self.env_stage = EnvStage::Decay;
                self.env_level = 1.0;
            }
            Instrument::Snare => {
                self.decay_time = 0.22;
                self.max_age = (sr * 0.8) as u64;
                self.env_stage = EnvStage::Decay;
                self.env_level = 1.0;
                // crude bandpass: HP 1400 + LP 7500
                self.hp_alpha = Self::one_pole_alpha(1400.0, sr);
                lp_cutoff = Some(7500.0);
            }
            Instrument::Hat => {
                self.decay_time = 0.05;
                self.max_age = (sr * 0.3) as u64;
                self.env_stage = EnvStage::Decay;
                self.env_level = 1.0;
                self.hp_alpha = Self::one_pole_alpha(7500.0, sr);
                lp_cutoff = None; // bypass LP: no velocity tracking
            }
            Instrument::Bass => {
                self.attack = 0.005;
                self.decay = 0.06;
                self.sustain = 0.85;
                self.release = 0.09;
                self.note_len = note_len;
                self.max_age = (sr * (note_len + 1.0)) as u64;
                self.env_stage = EnvStage::Attack;
                lp_cutoff = Some(900.0);
                self.hp_alpha = 0.0; // bypass HP
            }
            Instrument::Lead => {
                self.attack = 0.004;
                self.decay = 0.08;
                self.sustain = 0.7;
                self.release = 0.12;
                self.note_len = note_len;
                self.max_age = (sr * (note_len + 1.0)) as u64;
                self.env_stage = EnvStage::Attack;
                lp_cutoff = Some(3200.0);
                self.hp_alpha = 0.0;
            }
        }

        // Per-note velocity tracking (note domain): offset the voice's
        // lowpass cutoff around the middle velocity. Bipolar amount, so
        // velocity can raise or lower the cutoff (FL Studio's tracker).
        // Per-note keyboard tracking (note domain): offset the cutoff
        // around the middle MIDI note -- +1.0 is 100% tracking (one
        // octave of pitch = one octave of cutoff). The two trackers are
        // independent modulation sources (FL separates key and velocity
        // tracking) and sum in the exponent. Exponential mapping keeps
        // the musical effect in octaves.
        self.lp_alpha = match lp_cutoff {
            Some(base) => {
                let vel_oct = vel_track as f64
                    * (self.velocity - vel_track_mid as f64)
                    * VEL_TRACK_RANGE_OCT;
                let key_oct = key_track as f64
                    * (midi as f64 - key_track_mid as f64)
                    / 12.0;
                let cutoff = (base * 2f64.powf(vel_oct + key_oct))
                    .clamp(40.0, 20000.0);
                Self::one_pole_alpha(cutoff, sr)
            }
            None => 1.0, // bypassed filter: unchanged
        };
    }

    pub fn is_active(&self) -> bool {
        self.active
    }

    pub fn silence(&mut self) {
        self.active = false;
        self.env_stage = EnvStage::Done;
        self.env_level = 0.0;
    }

    fn envelope(&mut self) -> f64 {
        let dt = 1.0 / self.sample_rate;
        match self.env_stage {
            EnvStage::Attack => {
                self.env_t += dt;
                if self.env_t >= self.attack {
                    self.env_stage = EnvStage::Decay;
                    self.env_t = 0.0;
                    self.env_level = 1.0;
                } else {
                    self.env_level = self.env_t / self.attack;
                }
            }
            EnvStage::Decay => {
                self.env_t += dt;
                // exponential-ish decay toward sustain
                let k = (-dt / (self.decay * 0.35 + 1e-6)).exp();
                self.env_level = self.sustain + (self.env_level - self.sustain) * k;
                let gate = self.age as f64 / self.sample_rate;
                if gate >= self.note_len {
                    self.env_stage = EnvStage::Release;
                    self.env_t = 0.0;
                }
            }
            EnvStage::Release => {
                self.env_t += dt;
                let k = (-dt / (self.release * 0.35 + 1e-6)).exp();
                self.env_level *= k;
                if self.env_level < 0.0008 {
                    self.env_stage = EnvStage::Done;
                    self.active = false;
                }
            }
            EnvStage::Done => {
                self.active = false;
            }
        }
        self.env_level
    }

    /// Pure exponential decay used by drum voices.
    fn decay_env(&mut self) -> f64 {
        let t = self.age as f64 / self.sample_rate;
        (-t / (self.decay_time * 0.35 + 1e-6)).exp()
    }

    fn lowpass(&mut self, x: f64) -> f64 {
        self.lp_z += self.lp_alpha * (x - self.lp_z);
        self.lp_z
    }

    fn highpass(&mut self, x: f64) -> f64 {
        // y = a * (y_prev + x - x_prev)
        let y = self.hp_alpha * (self.hp_z + x - self.hp_prev);
        self.hp_prev = x;
        self.hp_z = y;
        y
    }

    /// Render one mono sample. Returns 0.0 when the voice finished.
    /// Constant-power pan gains captured at trigger from the note's pan.
    pub fn pan_gains(&self) -> (f32, f32) {
        (self.pan_l, self.pan_r)
    }

    pub fn process(&mut self) -> f32 {
        if !self.active {
            return 0.0;
        }
        self.age += 1;
        if self.age > self.max_age {
            self.silence();
            return 0.0;
        }
        let sr = self.sample_rate;
        let out = match self.instrument {
            Instrument::Kick => {
                let d = self.decay_env();
                if d < 0.0008 {
                    self.silence();
                    return 0.0;
                }
                // Pitch sweep 160 -> 48 Hz
                self.pitch_t += 1.0 / sr;
                let k = (-self.pitch_t / 0.035).exp();
                let f = self.pitch_end + (self.pitch_start - self.pitch_end) * k;
                self.phase += f / sr;
                let tone = (2.0 * std::f64::consts::PI * self.phase).sin();
                // attack click: a touch of noise in the first 6 ms
                let click = if self.pitch_t < 0.006 {
                    self.noise.next_f32() as f64 * 0.35 * (-self.pitch_t / 0.002).exp()
                } else {
                    0.0
                };
                (tone * 0.95 + click) * d
            }
            Instrument::Snare => {
                let d = self.decay_env();
                if d < 0.0008 {
                    self.silence();
                    return 0.0;
                }
                let n = self.noise.next_f32() as f64;
                let body_phase = 2.0 * std::f64::consts::PI * 190.0 * (self.age as f64 / sr);
                let body = body_phase.sin() * 0.5 + (2.0 * body_phase).sin() * 0.18;
                let hp = self.highpass(n * 0.8 + body);
                let shaped = self.lowpass(hp);
                shaped * d
            }
            Instrument::Hat => {
                let d = self.decay_env();
                if d < 0.0008 {
                    self.silence();
                    return 0.0;
                }
                let n = self.noise.next_f32() as f64;
                self.highpass(n) * d * 0.6
            }
            Instrument::Bass => {
                let env = self.envelope();
                if !self.active {
                    return 0.0;
                }
                self.phase += self.freq / sr;
                // naive saw, tamed by the lowpass
                let saw = 2.0 * (self.phase - self.phase.floor()) - 1.0;
                // subtle sub sine for weight
                let sub = (2.0 * std::f64::consts::PI * self.phase).sin() * 0.4;
                self.lowpass(saw * 0.7 + sub) * env
            }
            Instrument::Lead => {
                let env = self.envelope();
                if !self.active {
                    return 0.0;
                }
                self.phase += self.freq / sr;
                let sq = if (2.0 * std::f64::consts::PI * self.phase).sin() >= 0.0 {
                    1.0
                } else {
                    -1.0
                };
                self.lowpass(sq * 0.55) * env
            }
        };
        (out * self.velocity) as f32
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn midi_to_freq_a4() {
        let f = midi_to_freq(69);
        assert!((f - 440.0).abs() < 1e-9);
    }

    #[test]
    fn kick_decays_to_silence() {
        let mut v = Voice::new(44100);
        v.trigger(Instrument::Kick, 36, 1.0, 0.3, 12345, 0.0, 0.5, 0.0, 60.0, 0.0);
        let mut last = 1.0f32;
        for _ in 0..44100 {
            last = v.process();
        }
        assert!(last.abs() < 1e-3, "kick should be silent after 1s");
        assert!(!v.is_active());
    }

    #[test]
    fn noise_is_deterministic() {
        let mut a = Voice::new(44100);
        let mut b = Voice::new(44100);
        a.trigger(Instrument::Snare, 40, 1.0, 0.2, 999, 0.0, 0.5, 0.0, 60.0, 0.0);
        b.trigger(Instrument::Snare, 40, 1.0, 0.2, 999, 0.0, 0.5, 0.0, 60.0, 0.0);
        for _ in 0..1000 {
            assert_eq!(a.process().to_bits(), b.process().to_bits());
        }
    }

    #[test]
    fn all_instruments_parse() {
        for name in ["kick", "snare", "hat", "bass", "lead"] {
            assert!(Instrument::from_str(name).is_some());
        }
        assert!(Instrument::from_str("nope").is_none());
    }
}
