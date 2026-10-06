"""Live play capture (score logger): typing-keyboard piano + capture buffer.

Implements the FL Studio Recording Systems concepts within our constraints:
- Typing keyboard acts as a piano (infographic section 2).
- Score Logger: an always-on buffer capturing played notes with timestamps,
  independent of the Record button; "dump to pattern" commits them.
- Input quantize: snap captured notes to the grid on dump.

Honest scope: no live audio preview (engine has no live-note path and we
have no audio device to verify with). Notes are captured with timing;
dumping to a pattern and pressing Play gives the audio payoff.
"""

from dataclasses import dataclass, field
import time

# FL Studio-style typing keyboard piano layout.
# Lower row: white keys C-B. Upper row: black keys. QWERTY: next octave.
# (keysym -> semitone offset from base C)
KEY_TO_SEMITONE = {
    # Lower octave: Z=MIDI C, S=C#, X=D, D=D#, C=E, V=F, G=F#, B=G, H=G#, N=A, J=A#, M=B
    "z": 0, "s": 1, "x": 2, "d": 3, "c": 4, "v": 5, "g": 6,
    "b": 7, "h": 8, "n": 9, "j": 10, "m": 11,
    # Upper octave: Q=C, 2=C#, W=D, 3=D#, E=E, R=F, 5=F#, T=G, 6=G#, Y=A, 7=A#, U=B
    "q": 12, "2": 13, "w": 14, "3": 15, "e": 16, "r": 17, "5": 18,
    "t": 19, "6": 20, "y": 21, "7": 22, "u": 23,
    # Third octave start: I=C, 9=C#, O=D, 0=D#, P=E
    "i": 24, "9": 25, "o": 26, "0": 27, "p": 28,
}

# Base octave: C4 (MIDI 60) by default; Z/X keys shift it.
BASE_MIDI = 60


@dataclass
class CapturedNote:
    """One captured note: pitch, velocity, start/end in beats."""
    pitch: int
    velocity: float
    start_beat: float
    end_beat: float = 0.0  # 0 = still held

    @property
    def length_beats(self) -> float:
        if self.end_beat <= self.start_beat:
            return 0.25  # default: 16th note if released instantly
        return self.end_beat - self.start_beat


@dataclass
class CaptureBuffer:
    """Always-on circular buffer of played notes (the score logger).

    Notes are timestamped in transport beats when the transport is playing,
    otherwise in wall-clock beats at the current tempo (free-time capture).
    """
    max_notes: int = 1000
    notes: list = field(default_factory=list)
    _held: dict = field(default_factory=dict)  # pitch -> CapturedNote

    def note_on(self, pitch: int, velocity: float, beat: float) -> None:
        if pitch in self._held:
            return  # already held; ignore repeats
        note = CapturedNote(pitch=pitch, velocity=velocity, start_beat=beat)
        self._held[pitch] = note
        self.notes.append(note)
        # Circular: drop oldest.
        while len(self.notes) > self.max_notes:
            old = self.notes.pop(0)
            self._held.pop(old.pitch, None)

    def note_off(self, pitch: int, beat: float) -> None:
        note = self._held.pop(pitch, None)
        if note is not None:
            note.end_beat = max(beat, note.start_beat + 0.05)

    def clear(self) -> None:
        self.notes.clear()
        self._held.clear()

    def release_all(self, beat: float) -> None:
        for pitch in list(self._held):
            self.note_off(pitch, beat)


def quantize_beat(beat: float, grid: float) -> float:
    """Snap a beat position to the grid (in beats)."""
    if grid <= 0:
        return beat
    return round(beat / grid) * grid


# Quantize grid options: (label, grid_in_beats)
QUANTIZE_GRIDS = [
    ("Off", 0.0),
    ("1/4", 1.0),
    ("1/8", 0.5),
    ("1/16", 0.25),
    ("1/32", 0.125),
]
