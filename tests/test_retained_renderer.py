"""Topic 35: retained-mode presentation + renderer abstraction.

Covers:
- Renderer interface (TkCanvasRenderer maps node ids to canvas tags).
- RecordingRenderer: partial redraws emit draw commands ONLY for
  dirty node ids (the brief's section-31 third layer).
- LaneGeometryCache: unchanged clips retain geometry BY IDENTITY;
  only added/changed ids come back dirty.
- Widget-level: warm cache makes refresh_clips touch only dirty clips.
"""

import pytest
import tkinter as tk

from daw.project import Clip, new_default_project
from daw.ui.geometry_cache import LaneGeometryCache
from daw.ui.renderer import RecordingRenderer, Renderer, TkCanvasRenderer
from daw.ui.playlist_painter import (
    PlaylistPainter, PlaylistDraw, LaneDraw, ClipDraw)


def _lane(n=3):
    clips = [ClipDraw(x0=i * 100.0, x1=i * 100.0 + 90.0, color="#f778ba",
                      label=f"Clip {i}", selected=False, tag=f"clip-{i}")
             for i in range(n)]
    return LaneDraw(track_id="t1", clips=clips)


def _draw(lane):
    return PlaylistDraw(bars=4, bar_w=60.0, show_beat_lines=True,
                        lanes=[lane])


# -- renderer interface -------------------------------------------------


def test_renderer_is_abstract():
    with pytest.raises(TypeError):
        Renderer()


def test_tkcanvas_renderer_maps_node_ids_to_tags():
    root = tk.Tk(); root.withdraw()
    cv = tk.Canvas(root, width=100, height=50)
    r = TkCanvasRenderer(cv)
    r.draw_rect(0, 0, 10, 10, fill="#fff", node_id="node-1")
    r.draw_text(5, 5, text="hi", node_id="node-1")
    r.draw_line([0, 0, 10, 10], node_id="line-1")
    r.draw_polygon([0, 0, 10, 0, 10, 10], node_id="poly-1")
    assert r.has_node("node-1")
    assert len(cv.find_withtag("node-1")) == 2
    assert r.bbox("node-1") is not None
    r.delete("node-1")
    assert not r.has_node("node-1")
    assert not cv.find_withtag("node-1")
    r.draw_rect(0, 0, 5, 5)
    r.clear()
    assert not cv.find_all()
    root.destroy()


def test_recording_renderer_records_all_primitives():
    r = RecordingRenderer()
    r.draw_rect(0, 0, 1, 1, fill="#fff", node_id="a")
    r.draw_text(0, 0, text="t", node_id="a")
    r.draw_line([0, 0, 1, 1], node_id="b")
    r.draw_polygon([0, 0, 1, 1, 2, 2], node_id="c")
    r.delete("b")
    assert r.node_ids_drawn() == {"a", "b", "c"}
    assert not r.has_node("b")
    r.reset()
    assert r.commands == []


# -- partial draw-command generation (brief section 31, layer 3) ---------


def test_partial_redraw_emits_commands_only_for_dirty_nodes():
    lane = _lane(3)
    rec = RecordingRenderer()
    # Full draw: all clips get commands.
    PlaylistPainter().draw_lane(rec, lane, _draw(lane))
    assert rec.node_ids_drawn() == {"clip-0", "clip-1", "clip-2"}
    # Partial redraw of one clip: commands ONLY for that node.
    rec.reset()
    from daw.ui.dirty_regions import DirtyRect
    rects = PlaylistPainter().redraw_clips(rec, lane, ["clip-1"])
    assert rec.node_ids_drawn() == {"clip-1"}
    kinds = [c[0] for c in rec.commands]
    assert "delete" in kinds and "rect" in kinds and "text" in kinds
    assert isinstance(rects[0], DirtyRect)


def test_redraw_unknown_tag_emits_no_commands():
    lane = _lane(2)
    rec = RecordingRenderer()
    rects = PlaylistPainter().redraw_clips(rec, lane, ["clip-99"])
    assert rec.commands == []
    assert rects == []


# -- geometry cache ------------------------------------------------------


def test_cache_cold_sync_marks_everything_added():
    cache = LaneGeometryCache()
    assert not cache.warm
    diff = cache.sync(_lane(2).clips)
    assert sorted(diff["added"]) == ["clip-0", "clip-1"]
    assert diff["changed"] == [] and diff["removed"] == []
    assert cache.warm


def test_cache_unchanged_clips_retain_identity():
    lane = _lane(2)
    cache = LaneGeometryCache()
    cache.sync(lane.clips)
    first_retained = cache.get("clip-0")
    # Same geometry (fresh but equal ClipDraw objects): all unchanged,
    # and the retained object is the ORIGINAL one (identity kept).
    lane2 = _lane(2)
    diff = cache.sync(lane2.clips)
    assert diff["unchanged"] == ["clip-0", "clip-1"]
    assert diff["added"] == [] and diff["changed"] == []
    assert cache.get("clip-0") is first_retained


def test_cache_moving_one_clip_marks_only_it_changed():
    lane = _lane(2)
    cache = LaneGeometryCache()
    cache.sync(lane.clips)
    moved = _lane(2)
    moved.clips[1] = ClipDraw(x0=250.0, x1=340.0, color="#f778ba",
                              label="Clip 1", selected=False,
                              tag="clip-1")
    diff = cache.sync(moved.clips)
    assert diff["changed"] == ["clip-1"]
    assert diff["unchanged"] == ["clip-0"]


def test_cache_added_removed_clips():
    lane = _lane(2)
    cache = LaneGeometryCache()
    cache.sync(lane.clips)
    diff = cache.sync([lane.clips[0],
                       ClipDraw(x0=300.0, x1=390.0, color="#f778ba",
                                label="new", selected=False,
                                tag="clip-9")])
    assert diff["added"] == ["clip-9"]
    assert diff["removed"] == ["clip-1"]
    assert cache.get("clip-1") is None
    cache.drop(["clip-9"])
    assert cache.get("clip-9") is None


def test_cache_invalidate_forces_cold():
    lane = _lane(1)
    cache = LaneGeometryCache()
    cache.sync(lane.clips)
    cache.invalidate()
    assert not cache.warm
    diff = cache.sync(_lane(1).clips)
    assert diff["added"] == ["clip-0"]


def test_cache_detects_color_label_selection_changes():
    lane = _lane(1)
    cache = LaneGeometryCache()
    cache.sync(lane.clips)
    relabeled = [ClipDraw(x0=0.0, x1=90.0, color="#f778ba",
                          label="renamed", selected=False, tag="clip-0")]
    assert cache.sync(relabeled)["changed"] == ["clip-0"]
    selected = [ClipDraw(x0=0.0, x1=90.0, color="#f778ba",
                         label="renamed", selected=True, tag="clip-0")]
    assert cache.sync(selected)["changed"] == ["clip-0"]


# -- widget level (display) ----------------------------------------------

def test_widget_warm_cache_partial_refresh_touches_only_dirty():
    from daw.ui.playlist import Playlist
    root = tk.Tk(); root.withdraw()
    proj = new_default_project()
    pl = Playlist(root, lambda *a: None, lambda *a: None,
                  lambda *a: None, lambda *a: None, lambda *a: None)
    pl.pack()
    tid = proj.tracks[0].id
    pl.set_project(proj)
    cv = pl._canvases[tid]
    root.update_idletasks()
    # Place a second clip: cache is warm from set_project.
    pat = next(iter(proj.patterns))
    proj.tracks[0].clips.append(Clip(pattern_id=pat.id, start_beat=4,
                                     bars=2))
    items_before = set(cv.find_all())
    # Partial refresh targeting only the new clip's tag.
    draw = pl._draw_state()
    lane = next(l for l in draw.lanes if l.track_id == tid)
    new_tag = [c.tag for c in lane.clips][-1]
    pl.refresh_clips(tid, [new_tag])
    root.update_idletasks()
    items_after = set(cv.find_all())
    # Everything except the new clip's items survives untouched.
    survivors = items_before & items_after
    assert survivors == items_before, (
        f"warm partial refresh destroyed {len(items_before - items_after)} "
        "unrelated canvas items")
    root.destroy()


def test_widget_full_refresh_resyncs_cache():
    from daw.ui.playlist import Playlist
    root = tk.Tk(); root.withdraw()
    proj = new_default_project()
    pl = Playlist(root, lambda *a: None, lambda *a: None,
                  lambda *a: None, lambda *a: None, lambda *a: None)
    pl.pack()
    tid = proj.tracks[0].id
    pl.set_project(proj)
    root.update_idletasks()
    cache = pl._lane_caches[tid]
    assert cache.warm
    pat = next(iter(proj.patterns))
    proj.tracks[0].clips.append(Clip(pattern_id=pat.id, start_beat=4,
                                     bars=2))
    pl.refresh_lane(tid)
    root.update_idletasks()
    draw = pl._draw_state()
    lane = next(l for l in draw.lanes if l.track_id == tid)
    diff = cache.sync(lane.clips)
    assert diff["added"] == [] and diff["changed"] == []
    root.destroy()
