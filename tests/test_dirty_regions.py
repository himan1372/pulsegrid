"""Tests for per-region dirty flags (research topic 34).

Pure unit tests for DirtyRect / merge_rects / DirtyTracker /
DirtyDomain. Widget probes (playlist partial redraw, automation dirty
rect) need an X11 display (run under xvfb-run).
"""

import os

import pytest

from daw.project import new_default_project
from daw.ui.dirty_regions import (
    DirtyDomain,
    DirtyEvent,
    DirtyRect,
    DirtyTracker,
    merge_rects,
)

needs_display = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="widget probes need an X11 display (run under xvfb-run)",
)


# -- DirtyRect ---------------------------------------------------------------

def test_dirty_rect_area():
    r = DirtyRect(10, 20, 30, 40)
    assert r.area == 400.0


def test_dirty_rect_rejects_inverted():
    with pytest.raises(ValueError):
        DirtyRect(30, 20, 10, 40)


def test_dirty_rect_union():
    a = DirtyRect(0, 0, 10, 10)
    b = DirtyRect(5, 5, 20, 15)
    u = a.union(b)
    assert (u.x0, u.y0, u.x1, u.y1) == (0, 0, 20, 15)


def test_dirty_rect_intersects():
    a = DirtyRect(0, 0, 10, 10)
    assert a.intersects(DirtyRect(9, 9, 20, 20))
    assert not a.intersects(DirtyRect(10, 0, 20, 10))  # edge-touch: no
    assert not a.intersects(DirtyRect(50, 50, 60, 60))


def test_dirty_rect_padded_and_contains():
    r = DirtyRect(10, 10, 20, 20).padded(5)
    assert (r.x0, r.y0, r.x1, r.y1) == (5, 5, 25, 25)
    assert r.contains(5, 5)
    assert not r.contains(25, 25)  # half-open


def test_merge_rects_coalesces_overlaps():
    rects = [DirtyRect(0, 0, 10, 10), DirtyRect(5, 5, 15, 15),
             DirtyRect(100, 100, 110, 110)]
    merged = merge_rects(rects)
    assert len(merged) == 2
    big = next(r for r in merged if r.x0 == 0)
    assert (big.x1, big.y1) == (15, 15)


def test_merge_rects_chain():
    # A-B overlap and B-C overlap, but A-C do not: one pass unions
    # A+B, the second pass merges with C.
    rects = [DirtyRect(0, 0, 10, 10), DirtyRect(9, 0, 19, 10),
             DirtyRect(18, 0, 28, 10)]
    merged = merge_rects(rects)
    assert len(merged) == 1
    assert (merged[0].x0, merged[0].x1) == (0, 28)


def test_merge_rects_empty():
    assert merge_rects([]) == []


# -- DirtyTracker --------------------------------------------------------------

def test_tracker_marks_semantic_and_geometric():
    t = DirtyTracker()
    t.whole_domain(DirtyDomain.MIXER_STRIPS, note="vol")
    t.mark_dirty(DirtyDomain.PLAYLIST_LANES,
                 [DirtyRect(0, 0, 100, 60)], note="clip move")
    assert t.is_dirty(DirtyDomain.MIXER_STRIPS)
    assert t.is_dirty(DirtyDomain.PLAYLIST_LANES)
    assert not t.is_dirty(DirtyDomain.AUTOMATION)
    evs = t.recent(10)
    assert evs[0].whole_domain
    assert not evs[1].whole_domain
    assert evs[1].merged_area == pytest.approx(6000.0)


def test_tracker_caps_events_and_clears():
    t = DirtyTracker(max_events=5)
    for _ in range(8):
        t.whole_domain(DirtyDomain.STATUS)
    assert t.event_count() == 5
    t.clear()
    assert t.event_count() == 0
    assert not t.is_dirty(DirtyDomain.STATUS)


def test_dirty_domains_cover_fl_analog_categories():
    names = {d.name for d in DirtyDomain}
    # FL's HW_Dirty_* analogs: mixer display/controls, patterns,
    # performance, plus Pulsegrid's editors.
    assert {"MIXER_STRIPS", "MIXER_METERS", "PLAYLIST_LANES",
            "PLAYLIST_HEADERS", "AUTOMATION", "TRANSPORT"} <= names


# -- widget probes ---------------------------------------------------------------

@needs_display
def test_playlist_partial_redraw_leaves_other_clips_untouched():
    import tkinter as tk
    from daw.ui.playlist import Playlist
    p = new_default_project()
    root = tk.Tk()
    try:
        pl = Playlist(root, lambda *a: None, lambda *a: None,
                      lambda *a: None, lambda *a: None, lambda *a: None)
        pl.pack()
        pl.set_project(p)
        tid = p.tracks[0].id
        cv = pl._canvases[tid]
        before = cv.find_all()
        # Exactly one clip on track-1 initially; find its tag items.
        clip_items_before = set(cv.find_withtag("clip-0"))
        assert clip_items_before
        # Partial redraw of clip-0 only.
        draw = pl._draw_state()
        lane = next(l for l in draw.lanes if l.track_id == tid)
        from daw.ui.renderer import TkCanvasRenderer
        rects = pl._painter.redraw_clips(TkCanvasRenderer(cv), lane,
                                         ["clip-0"])
        assert len(rects) == 1 and rects[0].area > 0
        after = cv.find_all()
        # Grid lines and playhead untouched: only clip-0's items were
        # deleted and recreated (same total item count).
        assert len(after) == len(before)
        assert set(cv.find_withtag("clip-0"))
    finally:
        root.destroy()


@needs_display
def test_playlist_refresh_clips_moves_clip_partially():
    import tkinter as tk
    from daw.ui.playlist import Playlist
    from daw.ui.dirty_regions import DirtyDomain, DirtyTracker
    p = new_default_project()
    tracker = DirtyTracker()
    root = tk.Tk()
    try:
        pl = Playlist(root, lambda *a: None, lambda *a: None,
                      lambda *a: None, lambda *a: None, lambda *a: None,
                      dirty_tracker=tracker)
        pl.pack()
        pl.set_project(p)
        tid = p.tracks[0].id
        cv = pl._canvases[tid]
        x_before = cv.bbox("clip-0")[0]
        # Move the clip in the model, then partial-refresh just its tag.
        p.tracks[0].clips[0].start_beat += 16
        pl._project = p
        pl.refresh_clips(tid, ["clip-0"])
        x_after = cv.bbox("clip-0")[0]
        assert x_after > x_before
        # The tracker logged a geometric (not whole-domain) event.
        evs = [e for e in tracker.recent(10)
               if e.domain is DirtyDomain.PLAYLIST_LANES]
        assert evs and not evs[-1].whole_domain
        assert evs[-1].merged_area > 0
    finally:
        root.destroy()


@needs_display
def test_playlist_delete_clips_removes_only_tagged_items():
    import tkinter as tk
    from daw.ui.playlist import Playlist
    p = new_default_project()
    root = tk.Tk()
    try:
        pl = Playlist(root, lambda *a: None, lambda *a: None,
                      lambda *a: None, lambda *a: None, lambda *a: None)
        pl.pack()
        pl.set_project(p)
        tid = p.tracks[0].id
        cv = pl._canvases[tid]
        n_before = len(cv.find_all())
        n_clip = len(cv.find_withtag("clip-0"))
        assert n_clip > 0
        pl.delete_clips(tid, ["clip-0"])
        assert len(cv.find_withtag("clip-0")) == 0
        assert len(cv.find_all()) == n_before - n_clip
    finally:
        root.destroy()


@needs_display
def test_playlist_refresh_clips_falls_back_on_unknown_tag():
    import tkinter as tk
    from daw.ui.playlist import Playlist
    p = new_default_project()
    root = tk.Tk()
    try:
        pl = Playlist(root, lambda *a: None, lambda *a: None,
                      lambda *a: None, lambda *a: None, lambda *a: None)
        pl.pack()
        pl.set_project(p)
        tid = p.tracks[0].id
        # Unknown tag -> full lane redraw, no crash.
        pl.refresh_clips(tid, ["clip-999"])
        assert pl._canvases[tid].find_withtag("clip-0")
    finally:
        root.destroy()


@needs_display
def test_automation_point_rect_is_sane():
    import tkinter as tk
    from daw.ui.automation import AutomationEditor, POINT_R
    root = tk.Tk()
    try:
        ed = AutomationEditor(root, lambda *a: None, lambda *a: None)
        ed.pack()
        p = new_default_project()
        ed.set_project(p)
        root.update_idletasks()
        from daw.project import AutoPoint
        pt = AutoPoint(beat=4.0, value=0.75)
        r = ed._point_rect(pt)
        assert r is not None
        assert r.x1 - r.x0 == pytest.approx(2 * POINT_R)
        assert r.area > 0
        u = r.union(r.padded(10))
        assert u.area > r.area
    finally:
        root.destroy()
