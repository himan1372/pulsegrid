//! Track modulators: real-time MSEG (multi-segment envelope generator)
//! modulators that can be assigned to plugin parameters.
//!
//! Unlike timeline automation (which is per-parameter and bound to the
//! song position), modulators are per-track, tempo-synced, and evaluated
//! every audio block. One modulator can drive many destinations (a
//! modulation graph), each with its own amount and polarity.
//!
//! Design notes (informed by studying MegaMorph's documented architecture,
//! implemented originally for Pulsegrid):
//! - MSEG: nodes (time in beats, value 0..1), linear segments, loop
//! - Assignment: modulator -> (fx | generator layer, param id)
//! - Amount + polarity (positive/negative/bipolar) per assignment
//! - Modulation applies as an offset to the parameter's base value
//!   (the base comes from the user/automation; the modulator adds to it)

/// A single MSEG node: time in beats, value 0..1.
#[derive(Clone, Copy, Debug)]
pub struct MsegNode {
    pub time_beats: f64,
    pub value: f64,
}

/// Where a modulation assignment points.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ModTarget {
    /// Effect slot index on this track.
    Fx(usize),
    /// Generator layer index on this track.
    Gen(usize),
}

/// How the modulator value maps to a parameter offset.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ModPolarity {
    /// 0..1 -> 0..+amount
    Positive,
    /// 0..1 -> 0..-amount
    Negative,
    /// 0..1 -> -amount..+amount (centered on base)
    Bipolar,
}

/// One modulator -> parameter routing.
#[derive(Clone, Debug)]
pub struct ModAssignment {
    pub target: ModTarget,
    pub param_id: u32,
    /// 0..1 depth.
    pub amount: f64,
    pub polarity: ModPolarity,
    /// Parameter range for clamping.
    pub param_min: f64,
    pub param_max: f64,
}

/// Modulator specification (from the project).
#[derive(Clone, Debug)]
pub struct ModulatorParams {
    pub id: String,
    pub name: String,
    /// Nodes sorted by time_beats.
    pub nodes: Vec<MsegNode>,
    pub loop_enabled: bool,
    /// Loop length in bars (tempo-synced).
    pub length_bars: f64,
    /// Per-modulator rate multiplier.
    pub rate_mult: f64,
    pub assignments: Vec<ModAssignment>,
}

/// Runtime modulator state.
pub struct ModulatorState {
    pub params: ModulatorParams,
    phase_beats: f64,
    /// Base parameter value per assignment (from user/automation).
    bases: Vec<f64>,
}

impl ModulatorState {
    pub fn new(params: ModulatorParams, bases: Vec<f64>) -> Self {
        debug_assert_eq!(bases.len(), params.assignments.len());
        Self {
            params,
            phase_beats: 0.0,
            bases,
        }
    }

    /// Update a base value (called when user/automation changes a param).
    pub fn set_base(&mut self, assignment_idx: usize, value: f64) {
        if let Some(b) = self.bases.get_mut(assignment_idx) {
            *b = value;
        }
    }

    /// Advance the phase by `block_beats` and return the current MSEG
    /// value (0..1).
    pub fn advance(&mut self, block_beats: f64) -> f64 {
        // Rate multiplier scales how fast we move through the envelope.
        self.phase_beats += block_beats * self.params.rate_mult;
        let length_beats = self.params.length_bars * 4.0;
        if length_beats <= 0.0 {
            return self.eval(0.0);
        }
        if self.params.loop_enabled {
            self.phase_beats %= length_beats;
        } else {
            self.phase_beats = self.phase_beats.min(length_beats);
        }
        self.eval(self.phase_beats)
    }

    /// Evaluate the MSEG at `phase_beats` (linear interpolation).
    fn eval(&self, phase: f64) -> f64 {
        let nodes = &self.params.nodes;
        if nodes.is_empty() {
            return 0.0;
        }
        if nodes.len() == 1 {
            return nodes[0].value.clamp(0.0, 1.0);
        }
        // Before first node: hold first value.
        if phase <= nodes[0].time_beats {
            return nodes[0].value.clamp(0.0, 1.0);
        }
        // Find segment.
        for w in nodes.windows(2) {
            let (a, b) = (w[0], w[1]);
            if phase >= a.time_beats && phase <= b.time_beats {
                let span = b.time_beats - a.time_beats;
                if span <= 0.0 {
                    return b.value.clamp(0.0, 1.0);
                }
                let t = (phase - a.time_beats) / span;
                return (a.value + t * (b.value - a.value)).clamp(0.0, 1.0);
            }
        }
        // Past last node: hold last value.
        nodes.last().unwrap().value.clamp(0.0, 1.0)
    }

    /// Compute the final parameter value for an assignment given the
    /// modulator value (0..1).
    pub fn apply(&self, assignment_idx: usize, mod_value: f64) -> Option<f64> {
        let a = self.params.assignments.get(assignment_idx)?;
        let base = *self.bases.get(assignment_idx)?;
        let range = a.param_max - a.param_min;
        let offset = match a.polarity {
            ModPolarity::Positive => a.amount * mod_value * range,
            ModPolarity::Negative => -a.amount * mod_value * range,
            ModPolarity::Bipolar => a.amount * (mod_value * 2.0 - 1.0) * range,
        };
        Some((base + offset).clamp(a.param_min, a.param_max))
    }

    pub fn assignments(&self) -> &[ModAssignment] {
        &self.params.assignments
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn mseg(nodes: &[(f64, f64)]) -> ModulatorState {
        let params = ModulatorParams {
            id: "m1".to_string(),
            name: "Test".to_string(),
            nodes: nodes
                .iter()
                .map(|(t, v)| MsegNode {
                    time_beats: *t,
                    value: *v,
                })
                .collect(),
            loop_enabled: true,
            length_bars: 1.0,
            rate_mult: 1.0,
            assignments: vec![],
        };
        ModulatorState::new(params, vec![])
    }

    #[test]
    fn mseg_linear_interpolation() {
        let mut m = mseg(&[(0.0, 0.0), (4.0, 1.0)]);
        // Advance 2 beats (half of 4-beat loop) -> value 0.5
        let v = m.advance(2.0);
        assert!((v - 0.5).abs() < 1e-9, "got {}", v);
    }

    #[test]
    fn mseg_loop_wraps() {
        let mut m = mseg(&[(0.0, 0.0), (4.0, 1.0)]);
        // Advance 6 beats -> wraps to 2 beats -> 0.5
        let v = m.advance(6.0);
        assert!((v - 0.5).abs() < 1e-9, "got {}", v);
    }

    #[test]
    fn mseg_holds_ends() {
        let mut m = mseg(&[(1.0, 0.25), (3.0, 0.75)]);
        // Before first node
        assert!((m.eval(0.0) - 0.25).abs() < 1e-9);
        // After last node
        assert!((m.eval(4.0) - 0.75).abs() < 1e-9);
    }

    #[test]
    fn polarity_positive() {
        let params = ModulatorParams {
            id: "m".to_string(),
            name: "t".to_string(),
            nodes: vec![],
            loop_enabled: true,
            length_bars: 1.0,
            rate_mult: 1.0,
            assignments: vec![ModAssignment {
                target: ModTarget::Fx(0),
                param_id: 1,
                amount: 0.5,
                polarity: ModPolarity::Positive,
                param_min: 0.0,
                param_max: 1.0,
            }],
        };
        let m = ModulatorState::new(params, vec![0.4]);
        // mod=1.0, amount=0.5, base=0.4 -> 0.9
        let v = m.apply(0, 1.0).unwrap();
        assert!((v - 0.9).abs() < 1e-9, "got {}", v);
    }

    #[test]
    fn polarity_bipolar() {
        let params = ModulatorParams {
            id: "m".to_string(),
            name: "t".to_string(),
            nodes: vec![],
            loop_enabled: true,
            length_bars: 1.0,
            rate_mult: 1.0,
            assignments: vec![ModAssignment {
                target: ModTarget::Fx(0),
                param_id: 1,
                amount: 0.5,
                polarity: ModPolarity::Bipolar,
                param_min: 0.0,
                param_max: 1.0,
            }],
        };
        let m = ModulatorState::new(params, vec![0.5]);
        // mod=1.0 -> offset = 0.5 * (2*1-1) * 1 = 0.5 -> 1.0
        let v = m.apply(0, 1.0).unwrap();
        assert!((v - 1.0).abs() < 1e-9, "got {}", v);
        // mod=0.0 -> offset = 0.5 * (-1) = -0.5 -> 0.0
        let v = m.apply(0, 0.0).unwrap();
        assert!((v - 0.0).abs() < 1e-9, "got {}", v);
        // mod=0.5 -> offset 0 -> 0.5 (base)
        let v = m.apply(0, 0.5).unwrap();
        assert!((v - 0.5).abs() < 1e-9, "got {}", v);
    }

    #[test]
    fn polarity_negative_clamps() {
        let params = ModulatorParams {
            id: "m".to_string(),
            name: "t".to_string(),
            nodes: vec![],
            loop_enabled: true,
            length_bars: 1.0,
            rate_mult: 1.0,
            assignments: vec![ModAssignment {
                target: ModTarget::Gen(0),
                param_id: 2,
                amount: 1.0,
                polarity: ModPolarity::Negative,
                param_min: 0.0,
                param_max: 1.0,
            }],
        };
        let m = ModulatorState::new(params, vec![0.2]);
        // mod=1.0 -> 0.2 - 1.0 = -0.8 -> clamped to 0.0
        let v = m.apply(0, 1.0).unwrap();
        assert!((v - 0.0).abs() < 1e-9, "got {}", v);
    }
}
