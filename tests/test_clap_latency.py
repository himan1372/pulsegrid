"""Tests for runtime CLAP latency changes (research topic 32).

Uses the bundled test plugin (tests/fixtures/clap/PulsegridTestGain.clap),
whose Lookahead parameter (id 8) is structural: toggling it flips the
reported latency between 0 and 480 samples, and the plugin calls the
host's request_restart() when it changes while active — the exact CLAP
latency-change contract from the brief.

Covered:
- empty restart/latency queues initially,
- reported latency starts at 0 and is re-queried post-activation,
- a structural param change fires request_restart() through the real
  CLAP ABI during offline render,
- process_plugin_restarts() preserves state (FOR_DUPLICATE), reports
  the new 480-sample latency, and recalculates PDC (measured: the
  non-latent track's kick shifts by exactly 480 samples),
- a fresh load with lookahead=1 does NOT spuriously request a restart
  (initial params are flushed pre-activation).
"""

import os
import struct
import wave

import pytest

from daw.engine_bridge import EngineBridge, EngineError
from daw.project import (
    Channel,
    Clip,
    Effect,
    Note,
    Pattern,
    PlaylistTrack,
    Project,
    empty_project,
    register_plugin,
)

FIXTURE_CLAP = os.path.join(
    os.path.dirname(__file__), "fixtures", "clap", "PulsegridTestGain.clap")
PLUGIN_ID = "org.pulsegrid.test-gain"
LOOKAHEAD_PARAM = 8
LOOKAHEAD_LATENCY = 480


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


def _project_with_plugin(engine, plugin_path, lookahead=0.0, gain=1.0):
    """Two-track project: track 0 carries the test plugin as an insert,
    track 1 has a kick at beat 0 for PDC measurement."""
    params = engine.clap_plugin_params(plugin_path, PLUGIN_ID)
    register_plugin(PLUGIN_ID, "Pulsegrid Test Gain", params)
    proj = empty_project()
    pat = Pattern(
        id="p1", name="P",
        channels=[Channel(id="c1", name="Kick", instrument="kick", pitch=36,
                          notes=[Note(start=0, length=4, pitch=36, vel=1.0)])])
    proj.patterns.append(pat)
    trk0 = proj.tracks[0]
    # Track 0 carries only the plugin (no notes of its own): its
    # reported latency is what PDC must compensate on the other track.
    trk0.effects.append(Effect.plugin(PLUGIN_ID, plugin_path,
                                     {7: gain, LOOKAHEAD_PARAM: lookahead}))
    trk1 = PlaylistTrack(id="track-2", name="Track 2")
    proj.tracks.append(trk1)
    trk1.clips.append(Clip(pattern_id="p1", start_beat=0, bars=4))
    proj.validate()
    return proj


def _kick_onset(path):
    """First sample index where |mono| exceeds a threshold."""
    with wave.open(path, "rb") as w:
        n = w.getnframes()
        raw = w.readframes(n)
    samples = struct.unpack("<%dh" % (n * 2), raw)
    mono = [abs(samples[i] + samples[i + 1]) / 2 for i in range(0, len(samples), 2)]
    for i, v in enumerate(mono):
        if v > 200:  # well above the noise floor, below the kick peak
            return i
    raise AssertionError("no kick onset found")


def test_restart_queues_empty_initially(engine, plugin_path):
    proj = _project_with_plugin(engine, plugin_path)
    engine.push_project(proj)
    engine.play()
    try:
        assert engine.take_restart_requests() == []
        assert engine.process_plugin_restarts() == []
    finally:
        engine.stop()


def test_latency_initially_zero(engine, plugin_path):
    proj = _project_with_plugin(engine, plugin_path)
    engine.push_project(proj)
    engine.play()
    try:
        assert engine.plugin_latency_samples(0, fx_index=0) == 0
    finally:
        engine.stop()


def _poll_latency(engine, want, timeout_s=5.0):
    """Poll the live latency query until the audio thread has applied
    the structural param change (the plugin's request_restart() is
    filed synchronously with the change, so the request is queued by
    the time this returns True)."""
    import time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if engine.plugin_latency_samples(0, fx_index=0) == want:
            return True
        time.sleep(0.05)
    return False


def test_structural_change_requests_restart_e2e(engine, plugin_path):
    """Flip Lookahead 0 -> 1 on the LIVE instance (param event reaches
    the audio thread, the plugin calls request_restart()), then service
    it: the slot restarts, latency becomes 480, state (gain) is
    preserved."""
    proj = _project_with_plugin(engine, plugin_path, lookahead=0.0, gain=1.0)
    engine.push_project(proj)
    engine.play()
    try:
        assert engine.plugin_latency_samples(0, fx_index=0) == 0
        # Structural change on the live instance: the null-sink backend
        # renders in real time, so the queued param event is applied
        # within a few blocks.
        proj2 = _project_with_plugin(engine, plugin_path, lookahead=1.0, gain=1.0)
        engine.push_project(proj2)
        assert _poll_latency(engine, LOOKAHEAD_LATENCY), "latency never changed"
        # Service it: drains the queued request, preserves state via
        # FOR_DUPLICATE, re-queries latency post-activation,
        # recalculates PDC.
        restarted = engine.process_plugin_restarts()
        assert len(restarted) == 1
        assert restarted[0]["track"] == "0" and restarted[0]["fx_index"] == "0"
        assert engine.plugin_latency_samples(0, fx_index=0) == LOOKAHEAD_LATENCY
        # Queues are drained; the fresh instance does not re-request.
        import time
        time.sleep(0.3)
        assert engine.take_restart_requests() == []
    finally:
        engine.stop()


def test_restart_request_notification_shape(engine, plugin_path):
    """The queued notification carries kind/track/fx_index for the UI."""
    proj = _project_with_plugin(engine, plugin_path, lookahead=0.0)
    engine.push_project(proj)
    engine.play()
    try:
        proj2 = _project_with_plugin(engine, plugin_path, lookahead=1.0)
        engine.push_project(proj2)
        assert _poll_latency(engine, LOOKAHEAD_LATENCY), "latency never changed"
        reqs = engine.take_restart_requests()
        assert len(reqs) == 1, f"expected one restart request, got {reqs}"
        assert reqs[0]["kind"] == "restart"
        assert reqs[0]["track"] == "0" and reqs[0]["fx_index"] == "0"
        assert reqs[0]["layer_index"] == ""
    finally:
        engine.stop()


def test_restart_preserves_gain_state(engine, plugin_path):
    """The FOR_DUPLICATE blob saved at restart time carries the live
    gain, so the restarted instance sounds identical."""
    proj = _project_with_plugin(engine, plugin_path, lookahead=0.0, gain=1.5)
    engine.push_project(proj)
    engine.play()
    try:
        proj2 = _project_with_plugin(engine, plugin_path, lookahead=1.0, gain=1.5)
        engine.push_project(proj2)
        assert _poll_latency(engine, LOOKAHEAD_LATENCY), "latency never changed"
        restarted = engine.process_plugin_restarts()
        assert len(restarted) == 1, "restart expected"
        blob_b64 = engine.save_plugin_preset_blob(0, fx_index=0)
        import base64
        blob = base64.b64decode(blob_b64)
        gain = struct.unpack("<d", blob[4:12])[0]
        assert abs(gain - 1.5) < 1e-9, f"gain not preserved: {gain}"
        assert blob[20] == 1, "lookahead flag not preserved in blob"
    finally:
        engine.stop()


def test_fresh_load_with_lookahead_does_not_restart(engine, plugin_path, tmp_path):
    """A fresh load with lookahead=1 must not request a restart: the
    initial params are flushed pre-activation, so the first block's
    param application is not a *change*."""
    proj = _project_with_plugin(engine, plugin_path, lookahead=1.0)
    engine.push_project(proj)
    engine.play()
    try:
        engine.render_wav(str(tmp_path / "y.wav"), loops=1)
        assert engine.take_restart_requests() == []
        assert engine.plugin_latency_samples(0, fx_index=0) == LOOKAHEAD_LATENCY
    finally:
        engine.stop()


def test_pdc_recalculated_after_latency_change(engine, plugin_path, tmp_path):
    """Host-side proof that the new latency feeds PDC: with the latent
    plugin on track 0, track 1's kick must arrive 480 samples later
    than with no latent plugin — the host delays the shorter path."""
    # Baseline: lookahead off -> no PDC shift.
    proj = _project_with_plugin(engine, plugin_path, lookahead=0.0)
    engine.push_project(proj)
    base = str(tmp_path / "base.wav")
    engine.render_wav(base, loops=1)
    onset_base = _kick_onset(base)
    # Structural change on the live instance + restart.
    engine.play()
    try:
        proj2 = _project_with_plugin(engine, plugin_path, lookahead=1.0)
        engine.push_project(proj2)
        assert _poll_latency(engine, LOOKAHEAD_LATENCY), "latency never changed"
        restarted = engine.process_plugin_restarts()
        assert len(restarted) == 1, "restart expected"
    finally:
        engine.stop()
    late = str(tmp_path / "late.wav")
    engine.render_wav(late, loops=1)
    onset_late = _kick_onset(late)
    assert onset_late - onset_base == LOOKAHEAD_LATENCY, (
        f"PDC did not compensate the new latency: onset moved "
        f"{onset_late - onset_base}, expected {LOOKAHEAD_LATENCY}")


def test_plugin_latency_samples_no_instance(engine):
    assert engine.plugin_latency_samples(0, fx_index=0) is None
