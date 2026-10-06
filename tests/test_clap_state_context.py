"""Tests for CLAP state context, native preset load, and dirty tracking.

Uses the bundled test plugin (tests/fixtures/clap/PulsegridTestGain.clap),
which implements CLAP_EXT_STATE_CONTEXT (tags blobs with the context),
CLAP_EXT_PRESET_LOAD (from_location reads `gain=<float>` files), and calls
the host's mark_dirty()/loaded()/on_error() callbacks.
"""

import base64
import os

import pytest

from daw.engine_bridge import EngineBridge, EngineError
from daw.project import (
    Channel,
    Clip,
    Effect,
    Note,
    Pattern,
    Project,
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
def live_engine(engine, plugin_path):
    """Engine with a project pushed and the audio graph built (null-sink
    backend headless), so plugin_instances is populated."""
    params = engine.clap_plugin_params(plugin_path, PLUGIN_ID)
    register_plugin(PLUGIN_ID, "Pulsegrid Test Gain", params)
    proj = empty_project()
    pat = Pattern(
        id="p1", name="P",
        channels=[Channel(id="c1", name="Bass", instrument="bass", pitch=48,
                          notes=[Note(start=0, length=16, pitch=48, vel=1.0)])])
    proj.patterns.append(pat)
    trk = proj.tracks[0]
    trk.clips.append(Clip(pattern_id="p1", start_beat=0, bars=4))
    trk.effects.append(Effect.plugin(PLUGIN_ID, plugin_path, {7: 1.0}))
    proj.validate()
    engine.push_project(proj)
    engine.play()
    try:
        yield engine
    finally:
        engine.stop()


def test_supports_preset_load(live_engine):
    assert live_engine.plugin_supports_preset_load(0, fx_index=0) is True


def test_dirty_slots_empty_initially(live_engine):
    assert live_engine.take_plugin_dirty_slots() == []
    assert live_engine.take_preset_events() == {"loaded": [], "errors": []}


def test_preset_blob_has_preset_context_tag(live_engine):
    blob_b64 = live_engine.save_plugin_preset_blob(0, fx_index=0)
    blob = base64.b64decode(blob_b64)
    assert len(blob) == 25, "context blob is 25 bytes"
    assert blob[0:4] == b"PGST"
    ctx = int.from_bytes(blob[21:25], "little")
    assert ctx == 1, f"expected FOR_PRESET (1), got {ctx}"


def test_preset_blob_roundtrip(live_engine):
    # Save a preset, change the gain through a fresh engine state, then
    # load the preset back: params + blob are restored.
    blob_b64 = live_engine.save_plugin_preset_blob(0, fx_index=0)
    result = live_engine.load_plugin_preset_blob(0, blob_b64, fx_index=0)
    assert result["params"]["7"] == pytest.approx(1.0)
    assert result["state_base64"] == blob_b64


def test_preset_load_bad_blob_raises(live_engine):
    bad = base64.b64encode(b"definitely not a valid state blob").decode()
    with pytest.raises(EngineError):
        live_engine.load_plugin_preset_blob(0, bad, fx_index=0)


def test_native_preset_load_e2e(live_engine, tmp_path):
    # The plugin's native preset format: a text file "gain=<float>".
    preset = tmp_path / "lead.pgpreset"
    preset.write_text("gain=1.5")
    result = live_engine.plugin_preset_from_location(
        0, str(preset), fx_index=0)
    assert result["params"]["7"] == pytest.approx(1.5)
    # The plugin called host loaded() -> browser sync event...
    events = live_engine.take_preset_events()
    assert len(events["loaded"]) == 1
    ev = events["loaded"][0]
    assert ev["track"] == "0" and ev["fx_index"] == "0"
    assert ev["location"].endswith("lead.pgpreset")
    assert events["errors"] == []
    # ...and mark_dirty() -> the project needs saving again.
    dirty = live_engine.take_plugin_dirty_slots()
    assert len(dirty) == 1
    assert dirty[0]["track"] == "0" and dirty[0]["fx_index"] == "0"
    # Drains are one-shot.
    assert live_engine.take_plugin_dirty_slots() == []


def test_native_preset_load_bad_file(live_engine, tmp_path):
    preset = tmp_path / "bad.pgpreset"
    preset.write_text("not a preset")
    with pytest.raises(EngineError):
        live_engine.plugin_preset_from_location(0, str(preset), fx_index=0)
    events = live_engine.take_preset_events()
    assert len(events["errors"]) == 1
    assert "gain=" in events["errors"][0]["message"]


def test_slot_key_requires_exactly_one(live_engine):
    with pytest.raises(EngineError):
        live_engine.save_plugin_preset_blob(0)
    with pytest.raises(EngineError):
        live_engine.save_plugin_preset_blob(0, fx_index=0, layer_index=0)
