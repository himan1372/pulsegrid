//! Silent choke verification: debug instrumentation for the audio engine.
//!
//! Implements the FL Studio "Silent Choke Verification" infographic's
//! toolset without needing audio hardware:
//!
//! - **Event log** (infographic §4, MIDI/event-level observation): a
//!   lock-free-ish record of note on/off/choke events with sample
//!   positions, drained by the UI thread.
//! - **Voice counts** (infographic §2, polyphony indicators): per-track
//!   active voice/note counts, updated per audio block.
//! - **Peak meters** (infographic §1, visual meter monitoring): per-track
//!   peak levels, updated per audio block; the UI applies ballistics.
//!
//! Threading: the audio thread must never block or allocate. Peaks and
//! voice counts are `AtomicU32`s (f32 bit-cast for peaks). The event log
//! uses `try_lock` — if the UI thread is draining, the audio thread skips
//! recording that event rather than waiting. This matches the engine's
//! existing `try_read` philosophy on the song slot.

use std::sync::atomic::{AtomicU32, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

/// Capacity of the debug event ring buffer.
pub const DEBUG_EVENT_CAP: usize = 4096;

/// Fixed track capacity: avoids resizing across arrangement changes.
/// Tracks beyond this are not metered (songs are capped below this).
/// Must stay in sync with the Python-side MAX_TRACKS.
pub const MAX_DEBUG_TRACKS: usize = 64;

/// Kind of a debug note event.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DebugEventKind {
    /// Note started (built-in voice triggered or CLAP note-on sent).
    On,
    /// Note ended normally (CLAP note-off sent).
    Off,
    /// Voice cut short (loop-wrap choke, CLAP NOTE_CHOKE).
    Choke,
}

impl DebugEventKind {
    pub fn as_str(self) -> &'static str {
        match self {
            DebugEventKind::On => "on",
            DebugEventKind::Off => "off",
            DebugEventKind::Choke => "choke",
        }
    }
}

/// One recorded note event.
#[derive(Clone, Copy, Debug)]
pub struct DebugEvent {
    pub track: u16,
    pub kind: DebugEventKind,
    pub key: u8,
    pub note_id: i32,
    /// Absolute sample position in the engine's timeline.
    pub sample: u64,
}

/// Shared debug state. `Arc`d between the audio thread (`AudioCore`) and
/// the control thread (`Engine`, exposed to Python).
pub struct DebugState {
    peaks: Vec<AtomicU32>,
    voices: Vec<AtomicU32>,
    events: Mutex<Vec<DebugEvent>>,
    /// Monotonic event counter (also the ring position source).
    event_head: AtomicUsize,
}

impl DebugState {
    pub fn new(tracks: usize) -> Self {
        DebugState {
            peaks: (0..tracks).map(|_| AtomicU32::new(0)).collect(),
            voices: (0..tracks).map(|_| AtomicU32::new(0)).collect(),
            events: Mutex::new(Vec::with_capacity(256)),
            event_head: AtomicUsize::new(0),
        }
    }

    /// Record the peak level of one rendered block (audio thread).
    pub fn record_peak(&self, track: usize, peak: f32) {
        if let Some(slot) = self.peaks.get(track) {
            // Keep the max: a simple peak-hold; the UI applies decay.
            let bits = peak.to_bits();
            let mut cur = slot.load(Ordering::Relaxed);
            while (f32::from_bits(cur) < peak)
                && slot
                    .compare_exchange_weak(
                        cur,
                        bits,
                        Ordering::Relaxed,
                        Ordering::Relaxed,
                    )
                    .is_err()
            {
                cur = slot.load(Ordering::Relaxed);
            }
        }
    }

    /// Read and reset peaks (control thread). Returns per-track peaks.
    pub fn take_peaks(&self) -> Vec<f32> {
        self.peaks
            .iter()
            .map(|s| f32::from_bits(s.swap(0, Ordering::Relaxed)))
            .collect()
    }

    /// Set the active voice/note count for a track (audio thread).
    pub fn set_voices(&self, track: usize, n: u32) {
        if let Some(slot) = self.voices.get(track) {
            slot.store(n, Ordering::Relaxed);
        }
    }

    /// Read per-track voice counts (control thread).
    pub fn voice_counts(&self) -> Vec<u32> {
        self.voices
            .iter()
            .map(|s| s.load(Ordering::Relaxed))
            .collect()
    }

    /// Log a note event (audio thread). Never blocks: skips if contended.
    pub fn log_event(&self, ev: DebugEvent) {
        if let Ok(mut events) = self.events.try_lock() {
            let head = self.event_head.fetch_add(1, Ordering::Relaxed);
            if events.len() < DEBUG_EVENT_CAP {
                events.push(ev);
            } else {
                // Ring overwrite: oldest is dropped.
                events[head % DEBUG_EVENT_CAP] = ev;
            }
        }
    }

    /// Drain recorded events (control thread).
    pub fn drain_events(&self) -> Vec<DebugEvent> {
        self.events
            .lock()
            .map(|mut e| std::mem::take(&mut *e))
            .unwrap_or_default()
    }

    /// Number of tracks this state was sized for.
    pub fn track_count(&self) -> usize {
        self.peaks.len()
    }
}

/// Shared handle type.
pub type SharedDebugState = Arc<DebugState>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn peaks_take_and_reset() {
        let d = DebugState::new(2);
        d.record_peak(0, 0.5);
        d.record_peak(0, 0.8);
        d.record_peak(0, 0.3); // lower: peak-hold keeps 0.8
        d.record_peak(1, 0.1);
        let p = d.take_peaks();
        assert!((p[0] - 0.8).abs() < 1e-6);
        assert!((p[1] - 0.1).abs() < 1e-6);
        // Take resets.
        assert_eq!(d.take_peaks(), vec![0.0, 0.0]);
    }

    #[test]
    fn events_drain() {
        let d = DebugState::new(1);
        d.log_event(DebugEvent {
            track: 0,
            kind: DebugEventKind::Choke,
            key: 69,
            note_id: 7,
            sample: 1234,
        });
        let evs = d.drain_events();
        assert_eq!(evs.len(), 1);
        assert_eq!(evs[0].kind, DebugEventKind::Choke);
        assert!(d.drain_events().is_empty());
    }
}
