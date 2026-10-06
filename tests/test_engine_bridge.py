"""Engine bridge tests for the v4 paths (needs the built Rust extension).

These exercise the native `render_wav_file` function: per-note velocity,
beat-resolution clip starts, the progress callback contract, and cancel.
Skipped when the extension is not built (run `maturin develop`).
"""

import os
import struct
import wave

import pytest

native = pytest.importorskip("daw._daw_engine_rs")

from daw.engine_bridge import EngineBridge
from daw.project import new_default_project


def _arr(vel=0.9, start_beat=0, tempo=120.0, length=1.0):
    return {
        "tempo": tempo,
        "patterns": [{
            "id": "p1", "name": "P", "steps": 16,
            "channels": [{
                "instrument": "kick",
                "notes": [{"start": 0.0, "len": length,
                           "pitch": 36, "vel": vel}],
            }],
        }],
        "tracks": [{
            "name": "T", "gain": 1.0, "pan": 0.0, "muted": False,
            "effects": [],
            "clips": [{"pattern": 0, "start_beat": start_beat, "bars": 1}],
        }],
    }


def _render(arr, path, loops=1, progress=None):
    return native.render_wav_file(
        arr, 44100, path, loops, progress or (lambda d, t: True))


def _samples(path):
    with wave.open(path, "rb") as w:
        raw = w.readframes(w.getnframes())
    return struct.unpack("<%dh" % (len(raw) // 2), raw)


def test_velocities_scale_amplitude(tmp_path):
    loud = str(tmp_path / "loud.wav")
    soft = str(tmp_path / "soft.wav")
    assert _render(_arr(vel=1.0), loud) is True
    assert _render(_arr(vel=0.2), soft) is True
    peak_loud = max(abs(v) for v in _samples(loud))
    peak_soft = max(abs(v) for v in _samples(soft))
    assert peak_loud > 0
    assert peak_soft < peak_loud * 0.5


def test_clip_start_beat_offsets_timing(tmp_path):
    # 120 BPM @ 44100 Hz: one bar (4 beats) = 88200 samples.
    b0 = str(tmp_path / "b0.wav")
    b4 = str(tmp_path / "b4.wav")
    assert _render(_arr(start_beat=0), b0) is True
    assert _render(_arr(start_beat=4), b4) is True

    def first_loud(path, thresh=2000):
        vals = _samples(path)
        for i in range(0, len(vals), 2):
            if abs(vals[i]) > thresh:
                return i // 2
        return None

    f0, f4 = first_loud(b0), first_loud(b4)
    assert f0 is not None and f4 is not None
    assert abs((f4 - f0) - 88200) < 200


def _goertzel(samples, freq, sr=44100):
    """Magnitude of `freq` in mono samples (left channel)."""
    n = len(samples)
    w = 2.0 * __import__("math").pi * freq / sr
    cw, sw = __import__("math").cos(w), __import__("math").sin(w)
    coeff = 2.0 * cw
    s0 = s1 = s2 = 0.0
    for x in samples:
        s0 = x + coeff * s1 - s2
        s2, s1 = s1, s0
    return abs(s1 * cw - s2 + 1j * s1 * sw) / n


def _pitchshift_arr(semitones):
    return {
        "tempo": 120.0,
        "patterns": [{
            "id": "p1", "name": "P", "steps": 16,
            "channels": [{
                "instrument": "lead",
                "notes": [{"start": 0.0, "len": 4.0,
                           "pitch": 69, "vel": 0.9}],
            }],
        }],
        "tracks": [{
            "name": "T", "gain": 1.0, "pan": 0.0, "muted": False,
            "effects": [{"type": "pitchshift", "semitones": semitones,
                         "pitch_mix": 1.0}],
            "clips": [{"pattern": 0, "start_beat": 0, "bars": 1}],
        }],
    }


def test_pitchshift_effect_shifts_rendered_pitch(tmp_path):
    """End-to-end: bridge -> Rust engine -> WAV; +12 semitones moves the
    lead's 440 Hz fundamental to ~880 Hz (differential Goertzel)."""
    dry_p, wet_p = str(tmp_path / "dry.wav"), str(tmp_path / "wet.wav")
    assert _render(_pitchshift_arr(0.0), dry_p) is True
    assert _render(_pitchshift_arr(12.0), wet_p) is True
    # Note region: the lead decays fast; measure the first 0.4 s past
    # the effect's ~48 ms priming latency.
    dry = _samples(dry_p)[0::2][4000:4000 + 17640]
    wet = _samples(wet_p)[0::2][4000:4000 + 17640]
    dry = [x / 32768.0 for x in dry]
    wet = [x / 32768.0 for x in wet]
    dry_440, dry_880 = _goertzel(dry, 440.0), _goertzel(dry, 880.0)
    wet_440, wet_880 = _goertzel(wet, 440.0), _goertzel(wet, 880.0)
    assert dry_440 > 3 * dry_880, (dry_440, dry_880)
    assert wet_880 > 3 * wet_440, (wet_880, wet_440)


def test_render_progress_is_monotonic(tmp_path):
    calls = []
    path = str(tmp_path / "prog.wav")
    assert _render(_arr(), path, progress=lambda d, t: calls.append((d, t)) or True) is True
    totals = {t for _, t in calls}
    assert len(totals) == 1
    total = totals.pop()
    assert [d for d, _ in calls] == list(range(1, total + 1))


def test_render_cancel_aborts(tmp_path):
    path = str(tmp_path / "cancel.wav")
    seen = []

    def canceller(done, _total):
        seen.append(done)
        return done < 5

    assert _render(_arr(), path, loops=2, progress=canceller) is False
    assert len(seen) == 5


def test_arrangement_dict_v6_contract():
    d = EngineBridge.arrangement_dict(new_default_project())
    ch = d["patterns"][0]["channels"][0]
    assert set(ch) == {"instrument", "notes"}
    n = ch["notes"][0]
    assert set(n) == {"start", "len", "pitch", "vel", "pan"}
    track = d["tracks"][0]
    assert set(track) >= {"name", "gain", "pan", "muted", "effects",
                          "automation", "clips"}
    clip = track["clips"][0]
    assert set(clip) == {"pattern", "start_beat", "bars"}
    # The engine itself must accept the dict (native validation).
    EngineBridge().push_project(new_default_project())


def _auto_arr(points, param="gain", instrument="kick", effects=()):
    """1-bar clip, notes on every beat, one automation lane."""
    d = _arr(tempo=120.0)
    d["patterns"][0]["channels"][0]["instrument"] = instrument
    d["patterns"][0]["channels"][0]["notes"] = [
        {"start": float(s), "len": 4.0, "pitch": 69 if instrument == "lead" else 36,
         "vel": 1.0}
        for s in range(0, 16, 4)
    ]
    d["tracks"][0]["effects"] = list(effects)
    d["tracks"][0]["automation"] = [{"param": param, "points": points}]
    return d


def _peak_at(vals, beat, sr=44100, window_s=0.125):
    """Peak of the left channel in a window starting at `beat`."""
    f0 = int(beat * sr // 2)
    f1 = f0 + int(window_s * sr)
    return max(abs(v) for v in vals[f0 * 2:f1 * 2:2])


def _bright_at(vals, beat, sr=44100, window_s=0.045):
    """Max consecutive-frame difference (brightness) at `beat`."""
    f0 = int(beat * sr // 2)
    f1 = f0 + int(window_s * sr)
    left = vals[f0 * 2:f1 * 2:2]
    return max(abs(a - b) for a, b in zip(left, left[1:]))


def test_automation_gain_sweep_renders(tmp_path):
    # Sweep 0.05 -> 0.5 across the bar (stays clear of master saturation).
    out = str(tmp_path / "auto_gain.wav")
    assert _render(_auto_arr([[0.0, 0.05], [3.9, 0.5]]), out) is True
    vals = _samples(out)
    # Kicks on beats 0..3; compare the attack peak of the first and last.
    p0 = _peak_at(vals, 0.0)
    p3 = _peak_at(vals, 3.0)
    assert p3 > p0 * 5, f"later beat should be much louder ({p0} vs {p3})"


def test_automation_filter_sweep_renders(tmp_path):
    out = str(tmp_path / "auto_filter.wav")
    d = _auto_arr([[0.0, 200.0], [3.9, 18000.0]], param="fx0.cutoff",
                  instrument="lead",
                  effects=[{"type": "filter", "cutoff": 200.0}])
    assert _render(d, out) is True
    vals = _samples(out)
    # Tight windows at the note attacks: cutoff ~200 Hz vs ~14 kHz.
    b0 = _bright_at(vals, 0.0)
    b3 = _bright_at(vals, 3.0)
    assert b3 > b0 * 2.0, f"opening filter should brighten ({b0} vs {b3})"


def test_automation_before_first_point_uses_static(tmp_path):
    """With the first point at beat 2, beat 0 uses the static gain."""
    out = str(tmp_path / "auto_base.wav")
    d = _auto_arr([[2.0, 0.5], [3.9, 0.5]])
    d["tracks"][0]["gain"] = 0.1
    assert _render(d, out) is True
    vals = _samples(out)
    p0 = _peak_at(vals, 0.0)
    p3 = _peak_at(vals, 3.0)
    # Beat 0 at static gain 0.1, beat 3 at automated gain 0.5.
    assert p3 > p0 * 3, f"automated beat should be louder ({p0} vs {p3})"


def test_note_length_affects_render(tmp_path):
    """A longer note sustains longer (voice release follows note length)."""
    def tail_energy(path, skip=40000):
        vals = _samples(path)[skip:]
        return sum(v * v for v in vals[::2]) ** 0.5

    def lead_arr(length):
        d = _arr(tempo=120.0, length=length)
        d["patterns"][0]["channels"][0]["instrument"] = "lead"
        d["patterns"][0]["channels"][0]["notes"][0]["pitch"] = 69
        return d

    short = str(tmp_path / "nlen_short.wav")
    longp = str(tmp_path / "nlen_long.wav")
    assert _render(lead_arr(1.0), short) is True
    assert _render(lead_arr(8.0), longp) is True
    # The 8-step note is still sounding well after the 1-step note died.
    assert tail_energy(longp) > tail_energy(short) * 4


def test_threaded_render_matches_blocking(tmp_path):
    """The worker-thread entry point renders identical audio to the dict."""
    from daw.engine_bridge import EngineBridge
    bridge = EngineBridge()
    project = new_default_project()
    arr = EngineBridge.arrangement_dict(project)
    path = str(tmp_path / "threaded.wav")
    assert bridge.render_wav_threaded(arr, path, 1, lambda d, t: True) is True
    assert os.path.getsize(path) > 1000


# -- v0.25.0 routing options (bridge serialization) --

def test_bridge_sends_carry_routing_options():
    from daw.engine_bridge import EngineBridge
    from daw.project import PlaylistTrack, Send, empty_project
    p = empty_project()
    p.tracks.append(PlaylistTrack(id="track-2", name="Track 2"))
    t0, t1 = p.tracks[0], p.tracks[1]
    t0.sends.append(Send(to_track_id=t1.id, amount=0.6, tap="pre",
                         pan=-0.25, sidechain=True))
    d = EngineBridge.arrangement_dict(p)
    snd = d["tracks"][0]["sends"][0]
    assert snd == {"to": t1.id, "amount": 0.6, "tap": "pre", "pan": -0.25,
                   "sidechain": True}


def test_bridge_translates_send_lane_to_engine_index():
    from daw.engine_bridge import EngineBridge
    from daw.project import (AutomationLane, AutoPoint, PlaylistTrack,
                             Send, empty_project)
    p = empty_project()
    p.tracks.append(PlaylistTrack(id="track-2", name="Track 2"))
    t0, t1 = p.tracks[0], p.tracks[1]
    t0.sends.append(Send(to_track_id=t1.id, amount=0.5))
    p.automation.append(AutomationLane(
        id="a1", track_id=t0.id, param=f"send.{t1.id}.amount",
        points=[AutoPoint(0.0, 0.0), AutoPoint(8.0, 100.0)]))
    d = EngineBridge.arrangement_dict(p)
    lanes = d["tracks"][0]["automation"]
    assert len(lanes) == 1
    # Engine addresses the destination by track index (t1 is index 1).
    assert lanes[0]["param"] == "send.1.amount"
    assert lanes[0]["points"][1][1] == 100.0


def test_bridge_passes_vel_track():
    from daw.engine_bridge import EngineBridge
    from daw.project import new_default_project
    p = new_default_project()
    p.tracks[0].vel_track = 0.8
    p.tracks[0].vel_track_mid = 0.4
    d = EngineBridge.arrangement_dict(p)
    assert d["tracks"][0]["vel_track"] == 0.8
    assert d["tracks"][0]["vel_track_mid"] == 0.4


def test_bridge_passes_key_track():
    from daw.engine_bridge import EngineBridge
    from daw.project import new_default_project
    p = new_default_project()
    p.tracks[0].key_track = 0.8
    p.tracks[0].key_track_mid = 69.0
    d = EngineBridge.arrangement_dict(p)
    assert d["tracks"][0]["key_track"] == 0.8
    assert d["tracks"][0]["key_track_mid"] == 69.0


def test_bridge_passes_curve_interp_and_tension():
    from daw.engine_bridge import EngineBridge
    from daw.project import AutomationLane, AutoPoint, new_default_project
    p = new_default_project()
    p.automation.append(AutomationLane(
        id="auto-1", track_id=p.tracks[0].id, param="gain",
        points=[AutoPoint(0.0, 0.5), AutoPoint(8.0, 1.5)],
        interp="smooth", tension=0.75))
    d = EngineBridge.arrangement_dict(p)
    lane = d["tracks"][0]["automation"][0]
    assert lane["interp"] == "smooth"
    assert lane["tension"] == 0.75


# -- v0.34.0 automation curve shapes: engine E2E ------------------------------
# Render a sustained note through a gain ramp (0 -> 1 over 4 beats) and
# prove the engine honors the lane's interpolation mode, not just
# linear. Window: beats 0.25-0.75 (mean linear gain ~0.125 there).

def _curve_arr(interp, tension=0.5):
    return {
        "tempo": 120.0,
        "patterns": [{
            "id": "p1", "name": "P", "steps": 16,
            "channels": [{
                "instrument": "lead",
                "notes": [{"start": 0.0, "len": 4.0,
                           "pitch": 60, "vel": 0.9}],
            }],
        }],
        "tracks": [{
            "name": "T", "gain": 1.0, "pan": 0.0, "muted": False,
            "effects": [],
            "clips": [{"pattern": 0, "start_beat": 0, "bars": 1}],
            "automation": [{
                "param": "gain",
                "points": [[0.0, 0.0], [4.0, 1.0]],
                "interp": interp,
                "tension": tension,
            }],
        }],
    }


def _window_rms(path):
    vals = _samples(path)
    n = len(vals) // 2  # stereo interleave: use left channel
    left = [vals[i * 2] / 32768.0 for i in range(n)]
    # 120 bpm: beat = 22050 samples; window beats 0.25-0.75.
    seg = left[11025:33075]
    return (sum(v * v for v in seg) / len(seg)) ** 0.5


def test_engine_renders_curve_shapes(tmp_path):
    got = {}
    for interp in ("linear", "hold", "wave"):
        path = str(tmp_path / f"{interp}.wav")
        assert _render(_curve_arr(interp), path) is True
        got[interp] = _window_rms(path)
    # Linear: quiet ramp in the window. Hold: silent (step not reached).
    # Wave (1 cycle): wobbles well above the ramp there.
    assert got["linear"] > 0, "lead note did not render audibly"
    assert got["hold"] < got["linear"] * 0.2
    assert got["wave"] > got["linear"] * 2.0


def test_engine_curve_defaults_to_linear(tmp_path):
    # Missing interp/tension must still render (additive bridge fields).
    arr = _curve_arr("linear")
    del arr["tracks"][0]["automation"][0]["interp"]
    del arr["tracks"][0]["automation"][0]["tension"]
    path = str(tmp_path / "default.wav")
    assert _render(arr, path) is True
    assert _window_rms(path) > 0


def test_note_pan_bridge_conversion():
    """FL-style 0.0-1.0 -> engine -1.0..1.0 at the bridge."""
    from daw.project import Project, Pattern, Channel, Note, PlaylistTrack, Clip
    pat = Pattern(id="p1", name="P", steps=16, channels=[
        Channel(id="c1", name="C", instrument="kick", pitch=36, notes=[
            Note(start=0.0, length=1.0, pitch=36, vel=1.0, pan=0.0),
            Note(start=4.0, length=1.0, pitch=36, vel=1.0, pan=0.5),
            Note(start=8.0, length=1.0, pitch=36, vel=1.0, pan=1.0),
        ]),
    ])
    proj = Project(name="P", tempo=120.0, patterns=[pat], tracks=[
        PlaylistTrack(id="t1", name="T", clips=[
            Clip(pattern_id="p1", start_beat=0, bars=1)]),
    ])
    d = EngineBridge.arrangement_dict(proj)
    pans = [n["pan"] for n in d["patterns"][0]["channels"][0]["notes"]]
    assert pans == [-1.0, 0.0, 1.0]


def _pan_arr(pan):
    """Raw bridge dict with one kick panned in engine (-1..1) convention."""
    d = _arr()
    d["patterns"][0]["channels"][0]["notes"][0]["pan"] = pan
    return d


def test_note_pan_renders_to_channels(tmp_path):
    """End-to-end: bridge -> Rust engine -> WAV; a hard-left note is
    silent on the right channel (and vice versa)."""
    def peaks(path):
        vals = [x / 32768.0 for x in _samples(path)]
        left = vals[0::2][:11025]
        right = vals[1::2][:11025]
        return max(abs(v) for v in left), max(abs(v) for v in right)

    lp, rp = str(tmp_path / "left.wav"), str(tmp_path / "right.wav")
    assert _render(_pan_arr(-1.0), lp) is True
    assert _render(_pan_arr(1.0), rp) is True
    ll, lr = peaks(lp)
    rl, rr = peaks(rp)
    assert ll > 0.1, f"hard-left note should sound left (got {ll})"
    assert lr < 1e-4, f"hard-left note should be silent right (got {lr})"
    assert rr > 0.1, f"hard-right note should sound right (got {rr})"
    assert rl < 1e-4, f"hard-right note should be silent left (got {rl})"


def _sine_wav(path, freq=440.0, secs=1.0, sample_rate=44100, channels=1):
    import math
    n = int(sample_rate * secs)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        frames = b"".join(
            struct.pack("<h", int(16000 * math.sin(
                2 * math.pi * freq * i / sample_rate)))
            for i in range(n) for _ in range(channels))
        w.writeframes(frames)
    return str(path)


def _project_with_clip(tmp_path, **clip_kw):
    from daw.project import AudioClip, SampleAsset, empty_project
    wav = _sine_wav(tmp_path / "tone.wav")
    p = empty_project()
    p.samples.append(SampleAsset(id="s1", path=wav, name="tone.wav"))
    kw = dict(id="c1", asset_id="s1", start_beat=0.0, length_beats=4.0)
    kw.update(clip_kw)
    p.tracks[0].audio_clips.append(AudioClip(**kw))
    p.validate()
    return p


def test_arrangement_dict_carries_samples_and_clips(tmp_path):
    p = _project_with_clip(tmp_path, gain=0.8, pan=0.75,
                           pitch_semitones=12.0, reverse=True)
    d = EngineBridge.arrangement_dict(p, str(tmp_path))
    assert d["samples"] == [{"id": "s1",
                             "path": str(tmp_path / "tone.wav")}]
    ac = d["tracks"][0]["audio_clips"][0]
    assert ac["asset"] == "s1"
    assert ac["gain"] == 0.8
    # Python pan 0.75 -> engine +0.5.
    assert abs(ac["pan"] - 0.5) < 1e-9
    assert ac["pitch_semitones"] == 12.0
    assert ac["reverse"] is True
    assert ac["muted"] is False


def test_engine_renders_audio_clip_e2e(tmp_path):
    # +12 semitones of 440 Hz must read as ~880 Hz in the render.
    import math
    p = _project_with_clip(tmp_path, pitch_semitones=12.0)
    d = EngineBridge.arrangement_dict(p, str(tmp_path))
    out = str(tmp_path / "clip.wav")
    assert native.render_wav_file(d, 44100, out, 1,
                                  lambda done, total: True) is True
    sig = _samples(out)
    left = [s / 32768 for s in sig[0::2]]
    seg = left[44100 // 4:44100 // 4 + 44100 // 2]

    def goertzel(x, target, fs=44100):
        nn = len(x)
        k = int(0.5 + nn * target / fs)
        w_ = 2 * math.pi * k / nn
        c = 2 * math.cos(w_)
        s0 = s1 = s2 = 0.0
        for v in x:
            s0 = v + c * s1 - s2
            s2 = s1
            s1 = s0
        return math.sqrt(s1 * s1 + s2 * s2 - c * s1 * s2) / nn

    assert goertzel(seg, 880) > goertzel(seg, 440) * 3


def test_engine_rejects_unloaded_sample(tmp_path):
    from daw.project import AudioClip, empty_project
    p = empty_project()
    p.tracks[0].audio_clips.append(
        AudioClip(id="c1", asset_id="ghost", start_beat=0.0,
                  length_beats=4.0))
    # Project-level validation catches it before the engine.
    import pytest as _pytest
    from daw.project import ProjectError
    with _pytest.raises(ProjectError):
        p.validate()


def test_pyengine_sample_load_peaks(tmp_path):
    wav = _sine_wav(tmp_path / "tone.wav")
    eng = native.PyEngine(44100)
    info = dict(eng.load_sample("s1", wav))
    assert info["frames"] == 44100
    assert abs(info["duration_secs"] - 1.0) < 1e-6
    peaks = eng.sample_peaks("s1", 16)
    assert len(peaks) == 16
    assert max(hi for _, hi in peaks) > 0.3
    eng.unload_sample("s1")
    import pytest as _pytest
    with _pytest.raises(Exception):
        eng.sample_peaks("s1", 16)
