"""Automated silent choke verification (infographic section 3).

Renders a choke scenario offline and analyzes the waveform for the
expected energy drop -- the deterministic equivalent of "verify with
your eyes, not your ears". No audio hardware needed.

Scenario: a note that crosses the arrangement loop wrap. After the
wrap, the voice must be choked (CLAP NOTE_CHOKE); the region between
the wrap point and the note's next re-trigger must be near-silent.
If the old bug were present (note-offs silently dropped), the voice
would ring through that region.
"""

import math
import os
import struct
import tempfile
import wave

from .engine_bridge import EngineBridge
from .project import (
    Channel,
    Clip,
    Generator,
    GeneratorLayer,
    Note,
    Pattern,
    PlaylistTrack,
    Project,
)


def _read_wav_mono(path: str) -> tuple[list[float], int]:
    """Read a 16-bit stereo WAV, return (mono samples, sample_rate)."""
    with wave.open(path, "rb") as w:
        n = w.getnframes()
        sr = w.getframerate()
        raw = w.readframes(n)
    # 16-bit stereo.
    vals = struct.unpack(f"<{n * 2}h", raw)
    mono = [(vals[i] + vals[i + 1]) / 2.0 / 32768.0
            for i in range(0, len(vals), 2)]
    return mono, sr


def _peak(samples: list[float], start: int, end: int) -> float:
    start = max(0, start)
    end = min(len(samples), end)
    if start >= end:
        return 0.0
    return max(abs(s) for s in samples[start:end])


def verify_loop_wrap_choke(
    synth_path: str,
    synth_id: str = "org.pulsegrid.test-synth",
) -> dict:
    """Render a wrap-crossing note and check the choke.

    Returns a report dict with measurements and a pass/fail verdict.
    """
    sr = 44100
    bpm = 120.0
    beat = 60.0 / bpm
    # The arrangement loop has a 4-bar minimum (16 beats). The note
    # starts at beat 14 and lasts 4 beats, crossing the wrap at beat 16.
    # Without the choke, it would ring until beat 18; with the choke it
    # is cut at beat 16.
    project = Project(
        name="choke-verify",
        tempo=bpm,
        patterns=[
            Pattern(
                id="p1",
                name="test",
                steps=64,
                channels=[
                    Channel(
                        id="c1",
                        name="ch",
                        instrument="lead",
                        pitch=69,
                        notes=[Note(start=56.0, length=16.0,
                                    pitch=69, vel=0.9)],
                    ),
                ],
            )
        ],
        tracks=[],
    )
    track = PlaylistTrack(id="t1", name="Test")
    track.generator_layers = [GeneratorLayer(
        generator=Generator.plugin(
            synth_id, synth_path, {"11": 0.0, "12": 0.01, "13": 0.3}))]
    track.clips.append(Clip(pattern_id="p1", start_beat=0, bars=4))
    project.tracks.append(track)
    project.selected_pattern = "p1"

    bridge = EngineBridge(sample_rate=sr)
    bridge.push_project(project)

    with tempfile.TemporaryDirectory() as tmp:
        wav_path = os.path.join(tmp, "choke.wav")
        bridge.render_wav(wav_path, loops=2)
        mono, actual_sr = _read_wav_mono(wav_path)

    # Sample positions.
    wrap_sample = int(16 * beat * actual_sr)  # beat 16 = wrap
    # Window A: note sounding just before wrap.
    a0 = int((16 * beat - 0.5 * beat) * actual_sr)
    a1 = wrap_sample
    # Window B: after wrap; the note would have rung until beat 18.
    # Skip a small guard after the wrap for the choke to take effect.
    b0 = wrap_sample + int(0.05 * actual_sr)
    b1 = int((18 * beat - 0.1 * beat) * actual_sr)

    peak_a = _peak(mono, a0, a1)
    peak_b = _peak(mono, b0, b1)
    ratio = (peak_b / peak_a) if peak_a > 1e-6 else 0.0

    # The choke must cut the voice: post-wrap energy should be a small
    # fraction of the sounding energy. Threshold 10% is generous (a
    # proper choke gives ~0%).
    passed = peak_a > 0.01 and ratio < 0.10

    return {
        "passed": passed,
        "peak_before_wrap": peak_a,
        "peak_after_wrap": peak_b,
        "ratio": ratio,
        "threshold": 0.10,
        "wrap_sample": wrap_sample,
        "detail": (
            f"peak before wrap: {peak_a:.4f}, peak after wrap: {peak_b:.4f} "
            f"(ratio {ratio:.3f}, threshold 0.10). "
            + ("CHOKE VERIFIED: voice cut at the loop wrap."
               if passed else
               "CHOKE FAILED: significant energy after the wrap point -- "
               "voice may be hanging.")
        ),
    }
