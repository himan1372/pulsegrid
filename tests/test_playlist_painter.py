"""Tests for the playlist painter extraction.

Verifies the extraction boundary from the FL/LMMS painter research:
  * build_draw_state() is pure (no tkinter) and resolves geometry,
    selection, drag ghosts, and pattern colors/names from the model.
  * PlaylistPainter draws only from presentation state (uses a fake
    canvas, so no display is needed).
"""

from daw.project import Clip, Pattern, PlaylistTrack, new_default_project
from daw.ui.playlist_painter import (
    BAR_W,
    ROW_H,
    PlaylistPainter,
    build_draw_state,
)


class FakeCanvas:
    """Records canvas draw calls (no tkinter needed)."""

    def __init__(self):
        self.calls = []
        self._tags = set()

    def delete(self, tag):
        self.calls.append(("delete", tag))
        if tag == "all":
            self._tags.clear()
        else:
            self._tags.discard(tag)

    def create_line(self, *args, **kwargs):
        self.calls.append(("line", args, kwargs))
        for t in kwargs.get("tags", ()):
            self._tags.add(t)

    def create_rectangle(self, *args, **kwargs):
        self.calls.append(("rect", args, kwargs))
        for t in kwargs.get("tags", ()):
            self._tags.add(t)

    def create_text(self, *args, **kwargs):
        self.calls.append(("text", args, kwargs))
        for t in kwargs.get("tags", ()):
            self._tags.add(t)

    def find_withtag(self, tag):
        return [1] if tag in self._tags else []

    def coords(self, *args):
        self.calls.append(("coords", args))

    def configure(self, **kwargs):
        self.calls.append(("configure", kwargs))

    def xview_moveto(self, frac):
        self.calls.append(("xview_moveto", frac))


def _project():
    p = new_default_project()
    pat = p.patterns[0]
    p.tracks[0].clips.clear()
    p.tracks[0].clips.append(Clip(pattern_id=pat.id, start_beat=4, bars=2))
    p.tracks[0].clips.append(Clip(pattern_id=pat.id, start_beat=16, bars=1))
    return p


def _state(p, **kw):
    colors = {pt.id: "#ff0000" for pt in p.patterns}
    names = {pt.id: pt.name for pt in p.patterns}
    args = dict(bar_w=BAR_W, bars=16, show_beat_lines=False,
                selected=None, drag_ghost=None,
                pattern_colors=colors, pattern_names=names)
    args.update(kw)
    return build_draw_state(p, **args)


def test_build_draw_state_is_pure_and_geometric():
    p = _project()
    draw = _state(p)
    assert draw.bars == 16 and draw.bar_w == BAR_W
    lane = draw.lanes[0]
    assert len(lane.clips) == 2
    beat_w = BAR_W / 4.0
    c0 = lane.clips[0]
    assert c0.x0 == 4 * beat_w + 1
    assert c0.x1 == (4 + 8) * beat_w - 1
    assert c0.color == "#ff0000"
    assert c0.label.endswith("x2")
    assert c0.selected is False


def test_selection_flag_in_state():
    p = _project()
    tid = p.tracks[0].id
    draw = _state(p, selected=(tid, "clip", 1))
    assert draw.lanes[0].clips[0].selected is False
    assert draw.lanes[0].clips[1].selected is True


def test_drag_ghost_overrides_position():
    p = _project()
    tid = p.tracks[0].id
    draw = _state(p, drag_ghost=(tid, "clip", 0, 24))
    beat_w = BAR_W / 4.0
    assert draw.lanes[0].clips[0].x0 == 24 * beat_w + 1
    # Other clips unaffected.
    assert draw.lanes[0].clips[1].x0 == 16 * beat_w + 1


def test_unknown_pattern_falls_back():
    p = _project()
    p.tracks[0].clips.append(Clip(pattern_id="missing", start_beat=0,
                                  bars=1))
    draw = _state(p)
    last = draw.lanes[0].clips[-1]
    assert last.color == "#8b949e"
    assert last.label.startswith("?")


def test_painter_draws_lane_from_state_only():
    p = _project()
    draw = _state(p, show_beat_lines=True)
    lane = draw.lanes[0]
    cv = FakeCanvas()
    from daw.ui.renderer import TkCanvasRenderer
    PlaylistPainter().draw_lane(TkCanvasRenderer(cv), lane, draw)
    rects = [c for c in cv.calls if c[0] == "rect"]
    assert len(rects) == 2
    # First call is delete("all").
    assert cv.calls[0] == ("delete", "all")
    # Bar grid lines: bars+1 strong + beat subdivisions.
    lines = [c for c in cv.calls if c[0] == "line"]
    assert len(lines) == 17 + 16 * 4 - 16  # 17 bars, 48 beat subdivs
    # Selected clip gets a white outline.
    draw2 = _state(p, selected=(p.tracks[0].id, "clip", 0))
    cv2 = FakeCanvas()
    PlaylistPainter().draw_lane(TkCanvasRenderer(cv2), draw2.lanes[0],
                                draw2)
    rect0 = [c for c in cv2.calls if c[0] == "rect"][0]
    assert rect0[2]["outline"] == "#ffffff"
    assert rect0[2]["width"] == 2


def test_painter_ruler_culls_to_visible_range():
    p = _project()
    draw = _state(p)
    cv = FakeCanvas()
    # Visible window covers bars 2..5 only.
    PlaylistPainter().draw_ruler(cv, draw, 2 * BAR_W, 5 * BAR_W)
    texts = [c for c in cv.calls if c[0] == "text"]
    labels = [c[2]["text"] for c in texts]
    assert labels == ["3", "4", "5", "6"]


def test_playhead_move_reuses_item():
    cv = FakeCanvas()
    painter = PlaylistPainter()
    painter.move_playhead(cv, 100.0, ROW_H)
    assert ("line",) == (cv.calls[0][0],)  # created
    painter.move_playhead(cv, 150.0, ROW_H)
    kinds = [c[0] for c in cv.calls]
    assert kinds == ["line", "coords"]  # moved, not recreated
    assert cv.calls[1][1] == ("playhead", 150.0, 0, 150.0, ROW_H)


def test_clear_playhead():
    cv = FakeCanvas()
    painter = PlaylistPainter()
    painter.move_playhead(cv, 100.0, ROW_H)
    painter.clear_playhead(cv)
    assert cv.calls[-1] == ("delete", "playhead")
