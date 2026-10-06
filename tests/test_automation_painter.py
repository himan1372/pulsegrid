"""Tests for the automation curve painter (research topic 30).

The brief's painter contract (section 52): "Here are values. Draw
them." The envelope painter must sample the curve evaluator
(daw.curve_eval) and never implement its own shape math. These tests
prove that by driving _draw with a fake canvas and comparing the env
line coordinates against sample_curve output exactly.

Real-widget checks (curve menu, tension slider, commit signature) need
a display and live under xvfb-run.
"""

import os

import pytest

from daw.curve_eval import sample_curve
from daw.project import AutomationLane, AutoPoint, new_default_project
from daw.ui.automation import AutomationEditor

needs_display = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="widget probes need an X11 display (run under xvfb-run)",
)


class FakeCanvas:
    """Records canvas draw calls (no tkinter needed)."""

    def __init__(self, width=600, height=300):
        self.width = width
        self.height = height
        self.calls = []
        self._next_id = 1
        self._by_tag = {}

    def delete(self, tag):
        self.calls.append(("delete", tag, {}, None))
        if tag == "all":
            self._by_tag.clear()

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height

    def _record(self, kind, args, kwargs):
        self._next_id += 1
        item = self._next_id
        self.calls.append((kind, args, kwargs, item))
        tags = kwargs.get("tags", ())
        if isinstance(tags, str):
            tags = (tags,)
        for t in tags:
            self._by_tag.setdefault(t, []).append(item)
        return item

    def create_line(self, *args, **kwargs):
        return self._record("line", args, kwargs)

    def create_polygon(self, *args, **kwargs):
        return self._record("polygon", args, kwargs)

    def create_oval(self, *args, **kwargs):
        return self._record("oval", args, kwargs)

    def create_rectangle(self, *args, **kwargs):
        return self._record("rect", args, kwargs)

    def create_text(self, *args, **kwargs):
        return self._record("text", args, kwargs)

    def coords(self, item, *new):
        for kind, args, kwargs, iid in self.calls:
            if iid == item and kind in ("line", "polygon"):
                if new:
                    self.calls[self.calls.index((kind, args, kwargs, iid))] = (
                        kind, new, kwargs, iid)
                    return None
                return list(args[0]) if len(args) == 1 else list(args)
        raise AssertionError(f"item {item} not found")

    def find_withtag(self, tag):
        return tuple(self._by_tag.get(tag, ()))

    def type(self, item):
        for kind, _a, _k, iid in self.calls:
            if iid == item:
                return kind
        raise AssertionError(f"item {item} not found")


def _editor_with_lane(interp="linear", tension=0.5):
    """AutomationEditor with a real canvas swapped for a fake one.

    Bypasses __init__'s tkinter widgets (needs display); only the
    painter path (_draw/_refresh_envelope) is exercised.
    """
    ed = AutomationEditor.__new__(AutomationEditor)
    p = new_default_project()
    t = p.tracks[0]
    p.automation.append(AutomationLane(
        id="auto-1", track_id=t.id, param="gain",
        points=[AutoPoint(0.0, 0.5), AutoPoint(8.0, 1.5)],
        interp=interp, tension=tension))
    ed._project = p
    ed._track_id = t.id
    ed._param = "gain"
    ed._interp = interp
    ed._tension = tension
    ed._drag_idx = None
    ed._tan_drag = None
    ed._lfo = None
    ed._points = [AutoPoint(0.0, 0.5), AutoPoint(8.0, 1.5)]
    ed._canvas = FakeCanvas()
    return ed


def _env_line_coords(ed):
    items = ed._canvas.find_withtag("env")
    lines = [i for i in items if ed._canvas.type(i) == "line"]
    assert lines, "no env line drawn"
    coords = ed._canvas.coords(lines[0])
    return coords[0::2], coords[1::2]


def test_painter_samples_evaluator_exactly():
    # The env line must equal the evaluator's samples mapped through
    # the editor's x/y transforms -- the painter adds no shape math.
    for interp, tension in [("linear", 0.5), ("smooth", 1.0),
                            ("stairs", 0.75), ("pulse", 0.5),
                            ("wave", 0.5)]:
        ed = _editor_with_lane(interp, tension)
        ed._draw()
        xs, ys = _env_line_coords(ed)
        cv = ed._canvas
        expected = sample_curve([(0.0, 0.5), (8.0, 1.5)],
                                interp, tension)
        exp_x = [ed._x(b, cv.width) for b, _ in expected]
        exp_y = [ed._y(v, cv.height) for _, v in expected]
        assert xs == pytest.approx(exp_x), interp
        assert ys == pytest.approx(exp_y), interp


def test_hold_painter_draws_flat_step():
    ed = _editor_with_lane("hold", 0.5)
    ed._draw()
    _xs, ys = _env_line_coords(ed)
    assert max(ys) - min(ys) == pytest.approx(0.0)
    # But the smooth painter over the same points is not flat.
    ed2 = _editor_with_lane("smooth", 1.0)
    ed2._draw()
    _xs2, ys2 = _env_line_coords(ed2)
    assert max(ys2) - min(ys2) > 2.0


def test_fill_polygon_valid():
    # Fill polygon must start/end at the baseline and track the line.
    ed = _editor_with_lane("wave", 0.5)
    ed._draw()
    items = ed._canvas.find_withtag("env")
    polys = [i for i in items if ed._canvas.type(i) == "polygon"]
    assert polys, "no fill polygon drawn"
    coords = ed._canvas.coords(polys[0])
    xs, ys = _env_line_coords(ed)
    fill_x = coords[0::2]
    base_y = ed._y(0.0, ed._canvas.height)
    assert fill_x[0] == pytest.approx(xs[0])
    assert fill_x[-1] == pytest.approx(xs[-1])
    assert coords[1] == pytest.approx(base_y)
    assert coords[-1] == pytest.approx(base_y)


# -- real-widget probes (needs display) -------------------------------------

@needs_display
def test_curve_controls_load_lane_settings():
    import tkinter as tk
    commits = []

    def on_commit(tid, param, points, interp, tension, lfo=None):
        commits.append((interp, tension, lfo))

    root = tk.Tk()
    root.withdraw()
    try:
        p = new_default_project()
        t = p.tracks[0]
        p.automation.append(AutomationLane(
            id="auto-1", track_id=t.id, param="gain",
            points=[AutoPoint(0.0, 0.5)],
            interp="stairs", tension=0.25))
        ed = AutomationEditor(root, on_commit, lambda *a: None, None)
        ed.pack(fill="both", expand=True)
        ed.set_project(p, t.id, "gain")
        root.update()
        assert ed._interp_var.get() == "Stairs"
        assert abs(ed._tension_var.get() - 25.0) < 1e-6
        # Commit callback carries the new signature.
        ed._commit_points()
        assert commits[-1] == ("stairs", 0.25, None)
    finally:
        root.destroy()


@needs_display
def test_tension_widget_disabled_for_linear_and_hold():
    import tkinter as tk
    root = tk.Tk()
    root.withdraw()
    try:
        p = new_default_project()
        ed = AutomationEditor(root, lambda *a: None,
                              lambda *a: None, None)
        ed.pack(fill="both", expand=True)
        ed.set_project(p)
        root.update()
        for interp, expect in [("linear", "disabled"),
                               ("hold", "disabled"),
                               ("smooth", "normal"),
                               ("stairs", "normal"),
                               ("pulse", "normal"),
                               ("wave", "normal")]:
            ed._interp = interp
            ed._sync_curve_widgets()
            root.update()
            assert ed._tension_scale.cget("state") == expect, interp
    finally:
        root.destroy()
