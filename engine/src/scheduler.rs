//! Sample-accurate scheduling.
//!
//! A [`Song`] is an immutable, precomputed snapshot: tempo, loop length in
//! samples, and a sorted event list for one loop iteration. The audio thread
//! walks the event list with an index -- no searching, no allocation.

use crate::synth::Instrument;

/// One note trigger inside a loop, positioned at an exact sample offset.
#[derive(Clone, Debug)]
pub struct NoteEvent {
    /// Sample offset from the start of the loop.
    pub sample: u64,
    pub channel: usize,
    pub instrument: Instrument,
    pub pitch: u8,
    pub velocity: f64,
}

/// Control-thread description of a pattern. Plain data, no audio state.
#[derive(Clone, Debug)]
pub struct PatternData {
    pub tempo: f64,
    pub steps: usize,
    pub channels: Vec<ChannelData>,
}

#[derive(Clone, Debug)]
pub struct ChannelData {
    pub instrument: Instrument,
    pub pitch: u8,
    pub steps: Vec<bool>,
}

/// Immutable playback snapshot consumed by the audio thread.
#[derive(Clone, Debug)]
pub struct Song {
    pub sample_rate: u32,
    pub tempo: f64,
    /// Samples per 16th-note step.
    pub step_samples: u64,
    /// Samples per loop iteration.
    pub loop_samples: u64,
    /// Events sorted by `sample` within one loop.
    pub events: Vec<NoteEvent>,
    /// Gate length (seconds) for pitched instruments: 90% of a step.
    pub gate_secs: f64,
}

impl Song {
    pub fn from_pattern(sample_rate: u32, pattern: &PatternData) -> Result<Self, String> {
        if !(20.0..=300.0).contains(&pattern.tempo) {
            return Err(format!("tempo {} out of range 20-300 BPM", pattern.tempo));
        }
        if pattern.steps == 0 || pattern.steps > 256 {
            return Err(format!("step count {} out of range 1-256", pattern.steps));
        }
        if pattern.channels.is_empty() {
            // An empty pattern is valid: it renders silence. The UI always
            // pushes real channels before playback.
        }

        let beats_per_step = 0.25; // 16th notes
        let secs_per_beat = 60.0 / pattern.tempo;
        let step_samples = ((secs_per_beat * beats_per_step) * sample_rate as f64).round() as u64;
        if step_samples == 0 {
            return Err("step duration underflows one sample".to_string());
        }
        let loop_samples = step_samples * pattern.steps as u64;
        let gate_secs = secs_per_beat * beats_per_step * 0.9;

        let mut events = Vec::new();
        for (ch_idx, ch) in pattern.channels.iter().enumerate() {
            if ch.steps.len() != pattern.steps {
                return Err(format!(
                    "channel {} has {} steps, expected {}",
                    ch_idx,
                    ch.steps.len(),
                    pattern.steps
                ));
            }
            for (step, on) in ch.steps.iter().enumerate() {
                if *on {
                    events.push(NoteEvent {
                        sample: step as u64 * step_samples,
                        channel: ch_idx,
                        instrument: ch.instrument,
                        pitch: ch.pitch,
                        velocity: 0.9,
                    });
                }
            }
        }
        // Sort by sample so the audio thread can walk linearly. Stable order
        // keeps determinism when several events share a sample.
        events.sort_by_key(|e| e.sample);

        Ok(Song {
            sample_rate,
            tempo: pattern.tempo,
            step_samples,
            loop_samples,
            events,
            gate_secs,
        })
    }

    /// Convert an absolute sample position to (loop_index, position_in_loop).
    #[inline]
    pub fn loop_position(&self, abs_sample: u64) -> u64 {
        abs_sample % self.loop_samples
    }

    /// Beats elapsed for an absolute sample position (for UI readouts).
    pub fn beats_at(&self, abs_sample: u64) -> f64 {
        abs_sample as f64 / self.sample_rate as f64 / (60.0 / self.tempo)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn test_pattern() -> PatternData {
        PatternData {
            tempo: 120.0,
            steps: 4,
            channels: vec![ChannelData {
                instrument: Instrument::Kick,
                pitch: 36,
                steps: vec![true, false, true, false],
            }],
        }
    }

    #[test]
    fn song_timing_at_120bpm() {
        let song = Song::from_pattern(44100, &test_pattern()).unwrap();
        // 120 BPM -> beat = 0.5s -> 16th = 0.125s = 5512.5 -> 5512 samples (rounded)
        assert_eq!(song.step_samples, 5513);
        assert_eq!(song.loop_samples, 5513 * 4);
        assert_eq!(song.events.len(), 2);
        assert_eq!(song.events[0].sample, 0);
        assert_eq!(song.events[1].sample, 5513 * 2);
    }

    #[test]
    fn rejects_bad_tempo() {
        let mut p = test_pattern();
        p.tempo = 5.0;
        assert!(Song::from_pattern(44100, &p).is_err());
    }

    #[test]
    fn rejects_mismatched_steps() {
        let mut p = test_pattern();
        p.channels[0].steps = vec![true, false];
        assert!(Song::from_pattern(44100, &p).is_err());
    }
}
