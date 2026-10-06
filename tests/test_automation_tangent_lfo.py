"""Topic 36: per-node tangent locking + LFO layer.

Covers:
- Model: AutoPoint tangent round-trip, LfoSettings validation,
  additive serialization (old dicts without tangent/LFO keys load).
- Evaluator: locked-tangent vectors (mirroring the Rust tests),
  neighbor-move invariance for locked tangents, LFO add/multiply
  vectors, wave shapes, LFO on the static base, disabled passthrough.
- Bridge: tangents ride the arrangement dict (scaled by the unit
  conversion), LFO dict passes through.
- UI (display): tangent handles drawn in smooth mode only,
  Alt+right-click resets tangents, LFO enable draws the preview
  overlay and commits the layer to the project.
"""

import math

import pytest

from daw.curve_eval import (
    auto_tangent,
    lfo_wave,
    sample_curve,
    value_at,
)
from daw.project import (
    AutomationLane,
    AutoPoint,
    LfoSettings,
    ProjectError,
    new_default_project,
)


# -- model ---------------------------------------------------------------


def test_autopoint_tangent_round_trip():
    p = AutoPoint(2.0, 0.75, in_tan=-1.5, out_tan=2.5)
    q = AutoPoint.from_dict(p.to_dict())
    assert (q.beat, q.value, q.in_tan, q.out_tan) == (2.0, 0.75, -1.5, 2.5)


def test_autopoint_old_dicts_default_to_auto():
    p = AutoPoint.from_dict({"beat": 1.0, "value": 0.5})
    assert p.in_tan is None and p.out_tan is None


def test_lfo_settings_validation():
    LfoSettings().validate("lane-1")
    with pytest.raises(ProjectError):
        LfoSettings(shape="bogus").validate("lane-1")
    with pytest.raises(ProjectError):
        LfoSettings(combine="bogus").validate("lane-1")
    with pytest.raises(ProjectError):
        LfoSettings(speed=0.0).validate("lane-1")
    with pytest.raises(ProjectError):
        LfoSettings(speed=-1.0).validate("lane-1")
    with pytest.raises(ProjectError):
        LfoSettings(skew=1.5).validate("lane-1")
    with pytest.raises(ProjectError):
        LfoSettings(pulse_width=2.0).validate("lane-1")


def test_lane_lfo_round_trip_and_old_dicts():
    proj = new_default_project()
    lane = AutomationLane(
        id="a1", track_id=proj.tracks[0].id, param="gain",
        points=[AutoPoint(0.0, 0.5, out_tan=1.0)],
        lfo=LfoSettings(enabled=True, shape="pulse", combine="multiply"))
    lane.validate(proj.tracks[0], 64.0)
    back = AutomationLane.from_dict(lane.to_dict())
    assert back.lfo.enabled and back.lfo.shape == "pulse"
    assert back.lfo.combine == "multiply"
    assert back.points[0].out_tan == 1.0
    # Old dicts (no "lfo" key) load with no LFO layer.
    d = lane.to_dict()
    del d["lfo"]
    assert AutomationLane.from_dict(d).lfo is None


# -- evaluator: tangents --------------------------------------------------


def test_locked_tangents_override_auto():
    pts = [(0.0, 0.0), (4.0, 8.0)]
    locked = [(0.0, 0.0, None, 0.0), (4.0, 8.0, 0.0, None)]
    # Same fixed vectors as the Rust test.
    assert value_at(locked, "smooth", 0.5, 2.0, 0.0) == pytest.approx(4.0)
    assert value_at(pts, "smooth", 0.5, 1.0, 0.0) == pytest.approx(1.625)
    assert value_at(locked, "smooth", 0.5, 1.0, 0.0) == pytest.approx(1.25)


def test_locked_tangent_survives_neighbor_move():
    def mk(v2, locked):
        t = (0.0, 0.0) if locked else (None, None)
        return [(0.0, 0.0, None, None),
                (4.0, 4.0, 0.0, 0.0) if locked else (4.0, 4.0, None, None),
                (8.0, v2, None, None)]
    before = value_at(mk(8.0, True), "smooth", 0.5, 2.0, 0.0)
    after = value_at(mk(0.0, True), "smooth", 0.5, 2.0, 0.0)
    assert before == pytest.approx(after)
    a0 = value_at(mk(8.0, False), "smooth", 0.5, 2.0, 0.0)
    a1 = value_at(mk(0.0, False), "smooth", 0.5, 2.0, 0.0)
    assert abs(a0 - a1) > 1e-6  # auto tangents reshape


def test_auto_tangent_matches_legacy_formula():
    pts = [(0.0, 0.0), (4.0, 8.0), (8.0, 8.0)]
    # Segment 0 outgoing of point 0: 0.5 * (8-0)/(4-0) = 1.0.
    assert auto_tangent(pts, 0, 0, 0.5) == pytest.approx(1.0)
    # Segment 0 incoming of point 1: 0.5 * (8-0)/(8-0) = 0.5.
    assert auto_tangent(pts, 1, 1, 0.5) == pytest.approx(0.5)
    # Tangents do not affect non-smooth modes.
    pts_l = [(0.0, 0.0, None, 99.0), (4.0, 8.0, -99.0, None)]
    assert value_at(pts_l, "linear", 0.5, 2.0, 0.0) == pytest.approx(4.0)


def test_sample_curve_uses_locked_tangents():
    locked = [(0.0, 0.0, None, 0.0), (4.0, 8.0, 0.0, None)]
    # 101 samples/segment puts t=0.5 exactly on a sample.
    s = sample_curve(locked, "smooth", 0.5, samples_per_segment=101)
    mid = min(s, key=lambda p: abs(p[0] - 2.0))
    assert mid[0] == pytest.approx(2.0)
    assert mid[1] == pytest.approx(4.0)


# -- evaluator: LFO --------------------------------------------------------


def _lfo(**kw):
    d = {"enabled": True, "speed": 1.0, "shape": "sine", "skew": 0.0,
         "pulse_width": 0.5, "level": 0.5, "combine": "add"}
    d.update(kw)
    return d


def test_lfo_add_and_multiply_vectors():
    pts = [(0.0, 1.0), (4.0, 1.0)]
    lfo = _lfo()
    assert value_at(pts, "hold", 0.5, 0.25, 0.0, lfo) == pytest.approx(1.5)
    assert value_at(pts, "hold", 0.5, 0.75, 0.0, lfo) == pytest.approx(0.5)
    assert value_at(pts, "hold", 0.5, 1.25, 0.0, lfo) == pytest.approx(1.5)
    pts2 = [(0.0, 2.0), (4.0, 2.0)]
    mul = _lfo(combine="multiply")
    assert value_at(pts2, "hold", 0.5, 0.25, 0.0, mul) == pytest.approx(3.0)
    assert value_at(pts2, "hold", 0.5, 0.75, 0.0, mul) == pytest.approx(1.0)
    # Disabled LFO passes the base through untouched.
    off = _lfo(enabled=False)
    assert value_at(pts2, "hold", 0.5, 0.25, 0.0, off) == pytest.approx(2.0)
    assert value_at(pts, "hold", 0.5, 0.25, 0.0, None) == pytest.approx(1.0)


def test_lfo_applies_on_static_base():
    pts = [(0.0, 1.0), (4.0, 1.0)]
    # Before the first point the static base holds -- the LFO still
    # modulates it (the layer is on the lane, not on the points).
    assert value_at(pts, "hold", 0.5, 0.25, 9.0, _lfo()) == pytest.approx(1.5)


def test_lfo_wave_shapes():
    assert lfo_wave("sine", 0.0, 0.5, 0.0) == pytest.approx(0.0)
    assert lfo_wave("sine", 0.0, 0.5, 0.25) == pytest.approx(1.0)
    assert lfo_wave("saw", 0.0, 0.5, 0.0) == pytest.approx(-1.0)
    assert lfo_wave("saw", 0.0, 0.5, 0.5) == pytest.approx(0.0)
    assert lfo_wave("pulse", 0.0, 0.25, 0.1) == pytest.approx(1.0)
    assert lfo_wave("pulse", 0.0, 0.25, 0.5) == pytest.approx(-1.0)
    # Symmetric triangle at skew 0: peak at phase 0.5.
    assert lfo_wave("triangle", 0.0, 0.5, 0.0) == pytest.approx(-1.0)
    assert lfo_wave("triangle", 0.0, 0.5, 0.5) == pytest.approx(1.0)
    assert lfo_wave("triangle", 0.0, 0.5, 0.25) == pytest.approx(0.0)
    # Skew extremes approach saws.
    assert lfo_wave("triangle", -1.0, 0.5, 0.02) > 0.9
    assert lfo_wave("triangle", 1.0, 0.5, 0.98) > 0.9
    # Negative level inverts.
    lfo = _lfo(level=-0.5)
    assert value_at([(0.0, 1.0), (4.0, 1.0)], "hold", 0.5, 0.25, 0.0,
                    lfo) == pytest.approx(0.5)


def test_sample_curve_lfo_overlay_differs_from_base():
    pts = [(0.0, 1.0), (4.0, 1.0)]
    base = sample_curve(pts, "hold", 0.5)
    mod = sample_curve(pts, "hold", 0.5, samples_per_segment=100,
                       lfo=_lfo())
    assert all(b == pytest.approx(1.0) for _, b in base)
    assert max(v for _, v in mod) == pytest.approx(1.5, abs=0.02)
    assert min(v for _, v in mod) == pytest.approx(0.5, abs=0.02)


# -- bridge -----------------------------------------------------------------


def test_bridge_passes_tangents_and_lfo():
    from daw.engine_bridge import EngineBridge
    proj = new_default_project()
    tid = proj.tracks[0].id
    proj.automation.append(AutomationLane(
        id="a1", track_id=tid, param="gain",
        points=[AutoPoint(0.0, 0.5, out_tan=2.0),
                AutoPoint(4.0, 1.0, in_tan=-1.0)],
        interp="smooth", tension=0.5,
        lfo=LfoSettings(enabled=True, shape="triangle", skew=0.5,
                        combine="multiply")))
    d = EngineBridge.arrangement_dict(proj)
    lane = d["tracks"][0]["automation"][0]
    # gain is unitless: conv == 1.0, tangents pass through.
    assert lane["points"][0][2] is None
    assert lane["points"][0][3] == pytest.approx(2.0)
    assert lane["points"][1][2] == pytest.approx(-1.0)
    assert lane["lfo"]["shape"] == "triangle"
    assert lane["lfo"]["skew"] == pytest.approx(0.5)
    assert lane["lfo"]["combine"] == "multiply"


def test_bridge_scales_tangents_with_unit_conversion():
    # A % -unit param (delay mix): values are scaled by 0.01 for the
    # engine, and tangents (dvalue/dbeat) must scale identically.
    from daw.engine_bridge import EngineBridge
    proj = new_default_project()
    tid = proj.tracks[0].id
    from daw.project import Effect
    proj.tracks[0].effects.append(Effect.default("delay"))
    proj.automation.append(AutomationLane(
        id="a1", track_id=tid, param="fx0.mix",
        points=[AutoPoint(0.0, 50.0, out_tan=100.0),
                AutoPoint(4.0, 50.0)],
        interp="smooth"))
    d = EngineBridge.arrangement_dict(proj)
    lane = d["tracks"][0]["automation"][0]
    assert lane["points"][0][1] == pytest.approx(0.5)
    assert lane["points"][0][3] == pytest.approx(1.0)  # 100 * 0.01


# -- end-to-end: the engine renders the LFO -------------------------------


def _half_beat_peaks(proj, tmp_path, tag):
    """Peak amplitude per half-beat for the first 4 beats."""
    import struct
    import wave

    from daw.engine_bridge import EngineBridge
    eng = EngineBridge()
    out = str(tmp_path / f"lfo_{tag}.wav")
    assert eng.render_wav_threaded(
        EngineBridge.arrangement_dict(proj), out, loops=1,
        progress=lambda d, t: True)
    with wave.open(out) as w:
        n = w.getnframes()
        stereo = struct.unpack(f"<{n * 2}h", w.readframes(n))
    mono = [stereo[i] / 32768.0 for i in range(0, len(stereo), 2)]
    sr = 44100
    spb = sr * 60.0 / proj.tempo
    return [max(abs(v) for v in mono[int(i * spb / 2):int((i + 1) * spb / 2)]
                 or [0.0]) for i in range(8)]


def test_engine_renders_lfo_modulation(tmp_path):
    """A gain lane with a sine LFO must wobble the rendered output.

    Renders through the real Rust engine (bridge -> PyO3 -> timeline).
    LFO speed 0.5 cyc/beat: phase = beat/2 % 1, so beats 0.5-1.0 sit
    near the sine peak (gain ~1.5) and beats 1.5-2.0 near the trough
    (gain ~0.5). Comparing the SAME windows with and without the LFO
    factors out the note pattern's own dynamics.
    """
    proj = new_default_project()
    tid = proj.tracks[0].id
    base_peaks = _half_beat_peaks(proj, tmp_path, "base")
    proj.automation.append(AutomationLane(
        id="a1", track_id=tid, param="gain",
        points=[AutoPoint(0.0, 1.0), AutoPoint(8.0, 1.0)],
        interp="hold",
        lfo=LfoSettings(enabled=True, speed=0.5, shape="sine",
                        level=0.5, combine="add")))
    lfo_peaks = _half_beat_peaks(proj, tmp_path, "lfo")
    # Near the sine peak the LFO render must be louder than baseline,
    # near the trough quieter.
    assert lfo_peaks[1] > 1.3 * base_peaks[1], (lfo_peaks, base_peaks)
    assert lfo_peaks[3] < 0.75 * base_peaks[3], (lfo_peaks, base_peaks)


# -- UI probes (display) ------------------------------------------------------

import os
import tkinter as tk
from types import SimpleNamespace

from daw.ui.automation import AutomationEditor

needs_display = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="widget probes need an X11 display (run under xvfb-run)",
)


def _editor_with_smooth_lane():
    # NOTE: no withdraw() -- the canvas needs real geometry, which a
    # withdrawn toplevel never computes (Xvfb provides the display).
    root = tk.Tk()
    proj = new_default_project()
    tid = proj.tracks[0].id
    proj.automation.append(AutomationLane(
        id="a1", track_id=tid, param="gain",
        points=[AutoPoint(0.0, 0.5), AutoPoint(8.0, 1.5)],
        interp="smooth", tension=0.5))
    ed = AutomationEditor(root, lambda *a: None, lambda *a: None, None)
    ed.pack(fill="both", expand=True)
    ed.set_project(proj, tid, "gain")
    root.update()
    root.update_idletasks()
    ed._draw()
    root.update_idletasks()
    return root, ed, proj, tid


@needs_display
def test_tangent_handles_drawn_in_smooth_mode_only():
    root, ed, proj, tid = _editor_with_smooth_lane()
    try:
        assert ed._canvas.find_withtag("tan"), "no handles in smooth mode"
        # Switch to linear: handles must vanish (tangents inert there).
        ed._interp = "linear"
        ed._draw()
        root.update_idletasks()
        assert not ed._canvas.find_withtag("tan")
    finally:
        root.destroy()


@needs_display
def test_tangent_handle_hit_test_and_lock():
    root, ed, proj, tid = _editor_with_smooth_lane()
    try:
        cv = ed._canvas
        w, h = cv.winfo_width(), cv.winfo_height()
        # Out-handle of point 0 sits at beat+TAN_HB along the tangent.
        p = ed._points[0]
        eff = ed._effective_tangents(sorted(ed._points,
                                            key=lambda q: q.beat))
        hx = ed._x(p.beat + ed.TAN_HB, w)
        hy = ed._y(p.value + eff[0][1] * ed.TAN_HB, h)
        hit = ed._tangent_handle_at(SimpleNamespace(x=hx, y=hy))
        assert hit == (0, "out"), hit
        # Drag it: press + motion locks the tangent.
        ed._press(SimpleNamespace(x=hx, y=hy))
        assert ed._tan_drag == (0, "out")
        # Move the cursor up one value-span quarter: steep positive slope.
        ed._motion(SimpleNamespace(x=hx, y=hy - 40))
        assert ed._points[0].out_tan is not None, "drag did not lock"
        ed._release()
    finally:
        root.destroy()


@needs_display
def test_alt_right_click_resets_tangents():
    root, ed, proj, tid = _editor_with_smooth_lane()
    try:
        cv = ed._canvas
        w, h = cv.winfo_width(), cv.winfo_height()
        ed._points[1].in_tan = 3.0
        ed._points[1].out_tan = -2.0
        p = sorted(ed._points, key=lambda q: q.beat)[1]
        x, y = ed._x(p.beat, w), ed._y(p.value, h)
        # Alt+right-click: tangents reset, point survives.
        ed._right_click(SimpleNamespace(x=x, y=y, state=0x0008))
        assert p.in_tan is None and p.out_tan is None
        assert len(ed._points) == 2
        # Plain right-click still deletes.
        ed._right_click(SimpleNamespace(x=x, y=y, state=0))
        assert len(ed._points) == 1
    finally:
        root.destroy()


@needs_display
def test_lfo_enable_draws_overlay_and_commits():
    commits = []

    def on_commit(tid, param, points, interp, tension, lfo=None):
        commits.append(lfo)

    root = tk.Tk()
    try:
        proj = new_default_project()
        tid = proj.tracks[0].id
        proj.automation.append(AutomationLane(
            id="a1", track_id=tid, param="gain",
            points=[AutoPoint(0.0, 0.5), AutoPoint(8.0, 0.5)],
            interp="linear"))
        ed = AutomationEditor(root, on_commit, lambda *a: None, None)
        ed.pack(fill="both", expand=True)
        ed.set_project(proj, tid, "gain")
        root.update()
        root.update_idletasks()
        ed._draw()
        root.update_idletasks()
        assert not ed._canvas.find_withtag("lfoenv")
        ed._lfo_enabled_var.set(True)
        ed._on_lfo_toggled()
        root.update_idletasks()
        assert ed._canvas.find_withtag("lfoenv"), "no LFO overlay"
        # The commit carried the LFO layer.
        assert commits and commits[-1] is not None
        assert commits[-1].enabled
        # And the project lane has it (via the app-level commit path
        # shape: AutomationLane accepts the LfoSettings).
        lane = AutomationLane(
            id="a1", track_id=tid, param="gain",
            points=[AutoPoint(0.0, 0.5)], lfo=commits[-1])
        lane.validate(proj.tracks[0], 64.0)
    finally:
        root.destroy()
