"""Integration tests for CLAP plugin hosting (no GUI).

Uses the bundled test plugin (tests/fixtures/clap/PulsegridTestGain.clap),
a minimal stereo gain built from test-plugin/. Set CLAP_PATH to the
fixtures dir, or the tests locate it directly.
"""

import os
import struct
import wave

import pytest

from daw.engine_bridge import EngineBridge, EngineError
from daw.project import (
    FORMAT_VERSION,
    AutomationLane,
    AutoPoint,
    Channel,
    Clip,
    Effect,
    Note,
    Pattern,
    PlaylistTrack,
    Project,
    ProjectError,
    auto_param_spec,
    empty_project,
    register_plugin,
)

FIXTURE_CLAP = os.path.join(
    os.path.dirname(__file__), "fixtures", "clap", "PulsegridTestGain.clap")
PLUGIN_ID = "org.pulsegrid.test-gain"


@pytest.fixture()
def engine():
    os.environ["CLAP_PATH"] = os.path.dirname(FIXTURE_CLAP)
    return EngineBridge()


@pytest.fixture()
def plugin_path(engine):
    found = engine.scan_clap_plugins()
    hit = [p for p in found if p["id"] == PLUGIN_ID]
    assert hit, f"test plugin not found in scan: {found}"
    return hit[0]["path"]


@pytest.fixture()
def plugin_params(engine, plugin_path):
    params = engine.clap_plugin_params(plugin_path, PLUGIN_ID)
    register_plugin(PLUGIN_ID, "Pulsegrid Test Gain", params)
    return params


def _bass_project(plugin_path, gain):
    proj = empty_project()
    pat = Pattern(
        id="p1", name="P",
        channels=[Channel(id="c1", name="Bass", instrument="bass", pitch=48,
                          notes=[Note(start=0, length=16, pitch=48, vel=1.0)])])
    proj.patterns.append(pat)
    trk = proj.tracks[0]
    trk.clips.append(Clip(pattern_id="p1", start_beat=0, bars=4))
    trk.effects.append(Effect.plugin(PLUGIN_ID, plugin_path, {7: gain}))
    proj.validate()
    return proj


def _render_peak(engine, proj):
    arr = EngineBridge.arrangement_dict(proj)
    out = "/tmp/pulsegrid_plugin_test.wav"
    assert engine.render_wav_threaded(arr, out, loops=1,
                                      progress=lambda d, t: True)
    with wave.open(out) as w:
        n = w.getnframes()
        samples = struct.unpack(f"<{n * 2}h", w.readframes(n))
    return max(abs(s) for s in samples[::2])


def test_scan_finds_test_plugin(engine):
    found = engine.scan_clap_plugins()
    ids = [p["id"] for p in found]
    assert PLUGIN_ID in ids


def test_plugin_params_listed(engine, plugin_path):
    params = engine.clap_plugin_params(plugin_path, PLUGIN_ID)
    assert len(params) == 2
    p = params[0]
    assert p["id"] == 7 and p["name"] == "Gain"
    assert (p["min"], p["max"], p["default"]) == (0.0, 2.0, 1.0)
    q = params[1]
    assert q["id"] == 8 and q["name"] == "Lookahead"
    assert (q["min"], q["max"], q["default"]) == (0.0, 1.0, 0.0)


def test_check_plugin_ok(engine, plugin_path):
    engine.check_clap_plugin(plugin_path, PLUGIN_ID)


def test_check_plugin_missing_raises(engine):
    with pytest.raises(EngineError):
        engine.check_clap_plugin("/nonexistent/ghost.clap", "org.ghost.nope")


def test_plugin_renders_audio(engine, plugin_path, plugin_params):
    proj = _bass_project(plugin_path, 1.0)
    assert _render_peak(engine, proj) > 1000


def test_plugin_gain_zero_silences(engine, plugin_path, plugin_params):
    proj = _bass_project(plugin_path, 0.0)
    assert _render_peak(engine, proj) < 10


def test_plugin_effect_round_trip(plugin_params):
    fx = Effect.plugin(PLUGIN_ID, FIXTURE_CLAP, {7: 0.5})
    fx.validate()
    d = fx.to_dict()
    assert d["plugin_id"] == PLUGIN_ID
    fx2 = Effect.from_dict(d)
    assert fx2.kind == "plugin" and fx2.params == {"7": 0.5}
    ep = fx.engine_params()
    assert ep["type"] == "plugin" and ep["params"] == {"7": 0.5}


def test_plugin_effect_validation():
    with pytest.raises(ProjectError):
        Effect(kind="plugin", params={}).validate()  # no id/path
    with pytest.raises(ProjectError):
        Effect(kind="plugin", params={"x": 1.0},
               plugin_id="a", plugin_path="b").validate()  # bad key


def test_plugin_automation_spec(plugin_params):
    proj = _bass_project(FIXTURE_CLAP, 1.0)
    track = proj.tracks[0]
    label, lo, hi, unit = auto_param_spec("fx0.p7", track)
    assert lo == 0.0 and hi == 2.0 and "Gain" in label


def test_plugin_automation_renders(engine, plugin_path, plugin_params):
    proj = _bass_project(plugin_path, 1.0)
    track = proj.tracks[0]
    # Automate gain 0 -> 2 across the loop: first half silent-ish,
    # second half louder than the static baseline.
    lane = AutomationLane(id="a1", track_id=track.id, param="fx0.p7",
                          points=[AutoPoint(0.0, 0.0), AutoPoint(8.0, 2.0)])
    proj.automation.append(lane)
    proj.validate()
    peak = _render_peak(engine, proj)
    assert peak > 1000  # second half at gain 2.0 is loud


def test_format_version_bumped():
    assert FORMAT_VERSION == 16


def test_v6_project_migrates_cleanly():
    # A v6 dict without plugins loads as v7.
    proj = empty_project()
    d = proj.to_dict()
    d["version"] = 6
    loaded = Project.from_dict(d)
    assert loaded.validate() is None


# -- native GUI sessions -----------------------------------------------------

needs_display = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="plugin GUI tests need an X11 display (run under xvfb-run)",
)




PLUGIN_PATH = os.path.join("tests", "fixtures", "clap", "PulsegridTestGain.clap")
PLUGIN_ID = "org.pulsegrid.test-gain"


@needs_display
def test_open_gui_flushes_params():
    """open_plugin_gui flushes project values; read-back reflects them."""
    eng = EngineBridge(44100)
    gui_id = eng.open_plugin_gui(PLUGIN_PATH, PLUGIN_ID, {"7": 0.5})
    try:
        params = eng.plugin_gui_params(gui_id)
        assert abs(params["7"] - 0.5) < 1e-9, params
    finally:
        eng.close_plugin_gui(gui_id)


@needs_display
def test_close_gui_is_idempotent():
    eng = EngineBridge(44100)
    gui_id = eng.open_plugin_gui(PLUGIN_PATH, PLUGIN_ID, {})
    eng.close_plugin_gui(gui_id)
    eng.close_plugin_gui(gui_id)  # must not raise
    eng.close_plugin_gui(999999)  # unknown id must not raise


@needs_display
def test_open_gui_missing_plugin_raises():
    eng = EngineBridge(44100)
    with pytest.raises(EngineError):
        eng.open_plugin_gui("/nonexistent/ghost.clap", "org.ghost.nope", {})


@needs_display
def test_gui_params_unknown_id_empty():
    eng = EngineBridge(44100)
    assert eng.plugin_gui_params(999999) == {}


# -- instrument generators ---------------------------------------------------

SYNTH_PATH = os.path.join("tests", "fixtures", "clap", "PulsegridTestSynth.clap")
SYNTH_ID = "org.pulsegrid.test-synth"


def test_scan_instruments_finds_test_synth():
    eng = EngineBridge(44100)
    found = [p for p in eng.scan_clap_instruments() if p["id"] == SYNTH_ID]
    assert len(found) == 1, f"test synth not found: {found}"
    assert found[0]["name"] == "Pulsegrid Test Synth"


def test_check_instrument_ok_and_missing():
    eng = EngineBridge(44100)
    eng.check_clap_instrument(SYNTH_PATH, SYNTH_ID)  # must not raise
    with pytest.raises(EngineError):
        eng.check_clap_instrument("/nonexistent/ghost.clap", "org.ghost.nope")


def test_synth_params_enumerated():
    eng = EngineBridge(44100)
    params = eng.clap_plugin_params(SYNTH_PATH, SYNTH_ID)
    ids = {int(p["id"]) for p in params}
    assert ids == {11, 12, 13}, ids


def test_generator_v8_roundtrip(tmp_path):
    from daw.project import Generator, GeneratorLayer
    project = empty_project()
    gen = Generator.plugin(SYNTH_ID, SYNTH_PATH, {"11": 1.0, "12": 0.05})
    project.tracks[0].generator_layers = [GeneratorLayer(generator=gen)]
    path = str(tmp_path / "gen.pulsegrid.json")
    project.save(path)
    loaded = Project.load(path)
    assert len(loaded.tracks[0].generator_layers) == 1
    assert loaded.tracks[0].generator_layers[0].generator.plugin_id == SYNTH_ID
    assert loaded.tracks[0].generator_layers[0].generator.params["11"] == 1.0


def test_v7_project_migrates_to_v8():
    # A v7 dict without generators loads with generator=None.
    project = empty_project()
    data = project.to_dict()
    data["version"] = 7
    # Strip generator fields to simulate a v7 file.
    for t in data["playlist"]["tracks"]:
        t.pop("generator", None)
        t.pop("generator_layers", None)
    loaded = Project.from_dict(data)
    assert all(len(t.generator_layers) == 0 for t in loaded.tracks)


def test_render_with_generator_produces_audio(tmp_path):
    from daw.project import Generator, GeneratorLayer, Note, Clip
    project = empty_project()
    track = project.tracks[0]
    track.generator_layers = [GeneratorLayer(generator=Generator.plugin(
        SYNTH_ID, SYNTH_PATH, {"11": 0.0, "12": 0.01, "13": 0.1}))]
    pat = project.patterns[0]
    pat.channels[0].notes.append(
        Note(start=0.0, length=4.0, pitch=69, vel=0.8))
    track.clips.append(Clip(pattern_id=pat.id, start_beat=0, bars=1))

    eng = EngineBridge(44100)
    eng.push_project(project)
    wav_path = str(tmp_path / "gen.wav")
    eng.render_wav(wav_path, loops=1)

    with wave.open(wav_path, "rb") as w:
        frames = w.readframes(w.getnframes())
    samples = struct.unpack(f"<{len(frames)//2}h", frames)
    peak = max(abs(s) for s in samples)
    assert peak > 1000, f"generator should render audible audio, peak={peak}"


def test_generator_param_change_no_rebuild(tmp_path):
    """Changing a generator param value queues it without rebuilding."""
    from daw.project import Generator, GeneratorLayer, Note, Clip
    project = empty_project()
    track = project.tracks[0]
    track.generator_layers = [GeneratorLayer(generator=Generator.plugin(
        SYNTH_ID, SYNTH_PATH, {"11": 0.0}))]  # sine
    pat = project.patterns[0]
    pat.channels[0].notes.append(
        Note(start=0.0, length=4.0, pitch=69, vel=0.8))
    track.clips.append(Clip(pattern_id=pat.id, start_beat=0, bars=1))

    eng = EngineBridge(44100)
    eng.push_project(project)
    # Change wave to square (param 11 = 1.0) — should not rebuild.
    track.generator_layers[0].generator.params["11"] = 1.0
    eng.push_project(project)  # must not raise


def test_v8_project_migrates_to_v9():
    # A v8 dict without truncate_notes loads with truncate_notes=False.
    project = empty_project()
    data = project.to_dict()
    data["version"] = 8
    data.pop("truncate_notes", None)
    loaded = Project.from_dict(data)
    assert loaded.truncate_notes is False


def test_truncate_notes_round_trip():
    project = empty_project()
    project.truncate_notes = True
    loaded = Project.from_dict(project.to_dict())
    assert loaded.truncate_notes is True


def test_gen_automation_param_parses():
    from daw.project import parse_auto_param, Generator, GeneratorLayer
    assert parse_auto_param("gen.p11") == ("gen", -1, "p11")
    project = empty_project()
    track = project.tracks[0]
    track.generator_layers = [GeneratorLayer(generator=Generator.plugin(
        SYNTH_ID, SYNTH_PATH, {"11": 0.0, "12": 0.01, "13": 0.1}))]
    label, lo, hi, unit = auto_param_spec("gen.p11", track)
    assert "Wave" in label or "11" in label


def test_render_with_gen_automation(tmp_path, engine):
    # A gen.p11 (wave) automation lane renders without error and the
    # engine accepts the arrangement.
    from daw.project import Generator, GeneratorLayer
    project = empty_project()
    track = project.tracks[0]
    track.generator_layers = [GeneratorLayer(generator=Generator.plugin(
        SYNTH_ID, SYNTH_PATH, {"11": 0.0, "12": 0.01, "13": 0.1}))]
    pat = project.patterns[0]
    pat.channels[0].notes.append(
        Note(start=0, length=4, pitch=69, vel=0.9))
    track.clips.append(Clip(pattern_id=pat.id, start_beat=0, bars=1))
    project.automation.append(AutomationLane(
        id=project.new_automation_id(),
        track_id=track.id,
        param="gen.p11",
        points=[AutoPoint(beat=0.0, value=0.0),
                AutoPoint(beat=4.0, value=1.0)],
    ))
    engine.push_project(project)
    out = str(tmp_path / "gen_auto.wav")
    engine.render_wav(out, loops=1)
    assert os.path.exists(out) and os.path.getsize(out) > 1000


def test_choke_verification_passes():
    # The automated silent choke verification (infographic section 3)
    # must pass: the v0.7.1 NOTE_CHOKE fix cuts the voice at the wrap.
    import os
    from daw.verify_choke import verify_loop_wrap_choke
    synth = os.path.join(os.path.dirname(__file__), "fixtures",
                         "clap", "PulsegridTestSynth.clap")
    report = verify_loop_wrap_choke(synth)
    assert report["passed"], report["detail"]
    assert report["ratio"] < 0.10


def test_debug_bridge_methods(engine):
    # Debug instrumentation is exposed and safe to call when idle.
    from daw.project import empty_project
    engine.push_project(empty_project())
    peaks = engine.debug_peaks()
    voices = engine.debug_voices()
    assert len(peaks) == 64 and len(voices) == 64
    assert all(v == 0 for v in voices)
    assert engine.debug_events() == []
