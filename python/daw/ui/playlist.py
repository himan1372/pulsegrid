"""Playlist timeline: tracks as lanes, clips as blocks on a beat grid.

Architecture (painter extraction): this widget is the INTERACTION layer.
It owns canvases, event bindings, and view state (zoom, selection, drag
ghost, playhead, snap). All drawing is delegated to
playlist_painter.PlaylistPainter, which receives only presentation state
(PlaylistDraw, built by build_draw_state()) -- never the project model,
never input events.

Click empty space to place a clip of the currently selected pattern.
Drag a clip to move it (ghost preview; the project only changes on
drop, so undo restores the pre-drag position). Double-click a clip to
open its pattern in the step editor. Delete key removes the selected
clip. The snap selector quantizes placement and dragging to bars or
individual beats.
"""

import tkinter as tk
from tkinter import ttk

from .playlist_painter import (
    BAR_W,
    HEADER_W,
    PATTERN_COLORS,
    ROW_H,
    RULER_H,
    VISIBLE_BARS,
    PlaylistPainter,
    build_draw_state,
)
from .geometry_cache import LaneGeometryCache
from .renderer import TkCanvasRenderer
from .strip_presentation import build_strip_presentations, strip_geometry
from .widgets import Tooltip

SNAP_BEATS = {"Bar": 4, "Beat": 1}


class Playlist(ttk.Frame):
    def __init__(self, master, on_place, on_move, on_delete, on_edit_pattern,
                 on_add_track, on_clip_menu=None, on_edit_audio_clip=None,
                 peaks_provider=None, on_track_menu=None,
                 dirty_tracker=None):
        super().__init__(master)
        self._on_place = on_place
        self._on_move = on_move
        self._on_delete = on_delete
        self._on_edit_pattern = on_edit_pattern
        self._on_add_track = on_add_track
        self._on_clip_menu = on_clip_menu
        self._on_edit_audio_clip = on_edit_audio_clip
        self._on_track_menu = on_track_menu
        self._dirty_tracker = dirty_tracker
        # peaks_provider(audio_clip) -> [(min, max)] waveform buckets,
        # already windowed to the clip's source region (reversed when the
        # clip is reversed). Set by the app; None = no waveforms.
        self._peaks_provider = peaks_provider

        self._project = None
        self._pattern_colors = {}
        self._pattern_names = {}
        self._selected = None  # (track_id, clip_index)
        self._drag = None      # {track_id, clip_index, bar0, x0}
        self._playhead_beats = None
        self._bars = VISIBLE_BARS
        self._bar_w = BAR_W
        # The renderer: owns all canvas drawing, knows nothing of the
        # project model or input events (painter extraction).
        self._painter = PlaylistPainter()

        # -- header column ------------------------------------------------
        left = ttk.Frame(self, width=HEADER_W)
        left.pack(side="left", fill="y")
        left.pack_propagate(False)
        title_row = ttk.Frame(left)
        title_row.pack(fill="x")
        ttk.Label(title_row, text="Playlist", font=("", 10, "bold"),
                  padding=(8, 4)).pack(side="left")
        ttk.Button(title_row, text="-", width=3,
                   command=self.zoom_out).pack(side="right", padx=(0, 4))
        ttk.Button(title_row, text="+", width=3,
                   command=self.zoom_in).pack(side="right")
        snap_row = ttk.Frame(left)
        snap_row.pack(fill="x", pady=(0, 2))
        ttk.Label(snap_row, text="Snap:", font=("", 8),
                  foreground="#8b949e").pack(side="left", padx=(8, 2))
        self._snap_var = tk.StringVar(value="Bar")
        self._snap_combo = ttk.Combobox(snap_row, textvariable=self._snap_var,
                                        values=["Bar", "Beat"], state="readonly",
                                        width=5, font=("", 8))
        self._snap_combo.pack(side="left")
        self._snap_combo.bind("<<ComboboxSelected>>", self._on_snap_changed)
        Tooltip(self._snap_combo, "Quantize clip placement and dragging\n"
                                  "to bars or individual beats")
        self._track_headers = ttk.Frame(left)
        self._track_headers.pack(side="top", fill="x")
        ttk.Button(left, text="+ Track", command=self._on_add_track).pack(
            side="bottom", fill="x", padx=8, pady=6)

        # -- right column: ruler + track lanes + scrollbar ----------------
        right = ttk.Frame(self)
        right.pack(side="left", fill="both", expand=True)

        self._ruler = tk.Canvas(right, height=RULER_H, bg="#0d1117",
                                highlightthickness=0)
        self._ruler.pack(side="top", fill="x")
        from .scroll import bind_wheel as _bind_wheel
        _bind_wheel(self._ruler,
                    xscroll=lambda n: self._ruler.xview_scroll(n, "units"),
                    wheel_is_horizontal=True)

        lanes = ttk.Frame(right)
        lanes.pack(side="top", fill="both", expand=True)
        self._lanes = lanes
        self._canvases = {}  # track_id -> Canvas
        # Retained geometry, one cache per lane (research topic 35).
        self._lane_caches: dict = {}

        self._scroll = ttk.Scrollbar(right, orient="horizontal",
                                     command=self._on_scroll)
        self._scroll.pack(side="bottom", fill="x")

    # -- data -----------------------------------------------------------

    def set_project(self, project) -> None:
        """Sync lanes with the project, rebuilding only what changed.

        Lane canvases are kept across calls; only added/removed tracks
        cause widget changes. Clip edits redraw just the affected lane.
        """
        old_ids = set(self._canvases)
        new_tracks = list(project.tracks)
        new_ids = [t.id for t in new_tracks]
        track_changed = old_ids != set(new_ids)
        # Identity edits (rename/recolor/icon/link ripple) don't change
        # track ids, but the headers must still refresh.
        new_identities = {t.id: (t.name, t.color_idx, t.icon)
                          for t in new_tracks}
        identity_changed = (
            not track_changed
            and new_identities != getattr(self, "_header_identities", None))

        self._project = project
        self._selected = None
        self._drag = None
        # Stable pattern colors by order (presentation state).
        self._pattern_colors = {
            p.id: PATTERN_COLORS[i % len(PATTERN_COLORS)]
            for i, p in enumerate(project.patterns)
        }
        self._pattern_names = {p.id: p.name for p in project.patterns}
        bars = max(VISIBLE_BARS, project.arrangement_bars() + 4)
        if bars != self._bars:
            self._bars = bars
            self._layout_scroll()

        if track_changed or identity_changed:
            for child in self._track_headers.winfo_children():
                child.destroy()
            self._build_headers(new_tracks)
            self._header_identities = new_identities

        if track_changed:
            for child in self._lanes.winfo_children():
                child.destroy()
            self._canvases.clear()

            for track in new_tracks:
                cv = tk.Canvas(self._lanes, height=ROW_H, bg="#0d1117",
                               highlightthickness=0, xscrollincrement=1)
                cv.pack(fill="x")
                # Drop target for Browser patterns.
                cv._pulsegrid_track_id = track.id
                cv.bind("<Button-1>", lambda e, t=track: self._click(e, t))
                cv.bind("<B1-Motion>", lambda e, t=track: self._drag_motion(e, t))
                cv.bind("<ButtonRelease-1>", lambda e, t=track: self._drag_end(e, t))
                cv.bind("<Double-Button-1>", lambda e, t=track: self._double(e, t))
                cv.bind("<Button-3>", lambda e, t=track: self._right_click(e, t))
                cv.bind("<Delete>", lambda _e: self._delete_selected())
                # Wheel scrolling (FL conventions): the timeline's scrollable
                # axis is horizontal, so the wheel scrolls time here;
                # Shift+wheel also scrolls time; middle-drag pans.
                from .scroll import bind_wheel, bind_middle_pan
                bind_wheel(cv,
                           xscroll=lambda n, c=cv: c.xview_scroll(n, "units"),
                           wheel_is_horizontal=True)
                bind_middle_pan(cv)
                self._canvases[track.id] = cv

            self._layout_scroll()
            self.refresh()
        else:
            # Track list unchanged: just redraw the lanes (clips may differ).
            # Individual lanes are cheap; full widget rebuild is skipped.
            self.refresh()

    def _build_headers(self, tracks) -> None:
        """Build track headers from strip-level presentation objects."""
        for track in tracks:
            hdr = ttk.Frame(self._track_headers, height=ROW_H)
            hdr.pack(fill="x")
            hdr.pack_propagate(False)
            # Header presentation (research topic 33): the same
            # strip-level presentation object the mixer strip reads --
            # color chip + icon badge + name (FL Track Mode: one
            # identity, many surfaces).
            pres = build_strip_presentations([track])[0]
            geo = strip_geometry(pres)
            chip = tk.Canvas(hdr, width=geo["header_chip_width"],
                             height=ROW_H, bg=geo["accent_color"],
                             highlightthickness=0)
            chip.pack(side="left", fill="y", padx=(0, 4))
            name_lbl = ttk.Label(hdr, text=geo["name"], padding=(4, 2))
            name_lbl.pack(side="left", anchor="w")
            if geo["badge_text"]:
                ttk.Label(hdr, text=geo["badge_text"],
                          font=("", 8)).pack(side="left", padx=(2, 0))
            for w in (hdr, chip, name_lbl):
                w.bind("<Button-3>",
                       lambda e, t=track: self._track_menu(t, e))

    def _lane_renderer(self, track_id: str) -> TkCanvasRenderer:
        return TkCanvasRenderer(self._canvases[track_id])

    def _lane_cache(self, track_id: str) -> LaneGeometryCache:
        cache = self._lane_caches.get(track_id)
        if cache is None:
            cache = LaneGeometryCache()
            self._lane_caches[track_id] = cache
        return cache

    def refresh_lane(self, track_id: str) -> None:
        """Redraw just one track's lane (no other lanes flash)."""
        track = next((t for t in self._project.tracks if t.id == track_id),
                     None)
        if track is not None and track_id in self._canvases:
            draw = self._draw_state()
            lane = next(l for l in draw.lanes if l.track_id == track_id)
            self._painter.draw_lane(self._lane_renderer(track_id), lane,
                                    draw)
            # Full redraw: the cache now retains the whole lane.
            self._lane_cache(track_id).sync(lane.clips)
            self._draw_playhead()

    def refresh_clips(self, track_id: str, tags) -> None:
        """Partial lane redraw (research topics 34+35).

        The retained geometry cache diffs the fresh presentation state
        against the retained ClipDraws; only added/changed node ids are
        redrawn (draw commands are emitted solely for dirty nodes -- the
        brief's section-31 third layer). The grid and all other clips are
        untouched. Dirty rects are logged in the DirtyTracker. Falls back
        to a full lane redraw when a tag is unknown or the cache is cold.
        """
        from .dirty_regions import DirtyDomain
        if track_id not in self._canvases:
            return
        draw = self._draw_state()
        lane = next(l for l in draw.lanes if l.track_id == track_id)
        known = {c.tag for c in lane.clips}
        if not set(tags) <= known:
            self.refresh_lane(track_id)
            return
        cache = self._lane_cache(track_id)
        renderer = self._lane_renderer(track_id)
        if not cache.warm:
            # Cold cache: draw the requested tags, then retain the lane.
            rects = self._painter.redraw_clips(renderer, lane, tags)
            cache.sync(lane.clips)
        else:
            diff = cache.sync(lane.clips)
            dirty = (set(diff["added"]) | set(diff["changed"])) & set(tags)
            # Removed ids (shouldn't happen on this path, but be safe).
            for tag in set(diff["removed"]) & set(tags):
                renderer.delete(tag)
            # Redraw in lane order so surviving z-order matches a full
            # build as closely as possible.
            ordered = [c.tag for c in lane.clips if c.tag in dirty]
            rects = self._painter.redraw_clips(renderer, lane, ordered)
        self._draw_playhead()
        if self._dirty_tracker is not None:
            self._dirty_tracker.mark_dirty(
                DirtyDomain.PLAYLIST_LANES, rects,
                note=f"lane {track_id}: {len(tags)} clip(s) partial")

    def delete_clips(self, track_id: str, tags) -> None:
        """Partial lane update for clip deletion: remove the tagged items.

        No redraw needed -- the grid lines underneath are untouched, so
        the vacated area is already correct.
        """
        from .dirty_regions import DirtyDomain, DirtyRect
        if track_id not in self._canvases:
            return
        renderer = self._lane_renderer(track_id)
        rects = []
        for tag in tags:
            bbox = renderer.bbox(tag)
            if bbox:
                rects.append(DirtyRect(bbox[0], bbox[1], bbox[2], bbox[3]))
            renderer.delete(tag)
        self._lane_cache(track_id).drop(tags)
        self._draw_playhead()
        if self._dirty_tracker is not None:
            self._dirty_tracker.mark_dirty(
                DirtyDomain.PLAYLIST_LANES, rects,
                note=f"lane {track_id}: {len(tags)} clip(s) deleted")

    # -- zoom -------------------------------------------------------------

    def zoom_in(self) -> None:
        self._bar_w = min(96, self._bar_w + 12)
        self._layout_scroll()
        self.refresh()

    def zoom_out(self) -> None:
        self._bar_w = max(24, self._bar_w - 12)
        self._layout_scroll()
        self.refresh()

    def refresh(self) -> None:
        if self._project is None:
            return
        bars = max(VISIBLE_BARS, self._project.arrangement_bars() + 4)
        if bars != self._bars:
            self._bars = bars
            self._layout_scroll()
        draw = self._draw_state()
        x0 = self._ruler.canvasx(0)
        x1 = self._ruler.canvasx(self._ruler.winfo_width())
        self._painter.draw_ruler(self._ruler, draw, x0, x1)
        lane_by_id = {l.track_id: l for l in draw.lanes}
        for track in self._project.tracks:
            lane = lane_by_id[track.id]
            self._painter.draw_lane(self._lane_renderer(track.id), lane,
                                    draw)
            # Fresh full build: retain the lane geometry.
            self._lane_cache(track.id).sync(lane.clips)
        self._draw_playhead()

    def set_playhead(self, beats) -> None:
        self._playhead_beats = beats
        self._draw_playhead()

    # -- presentation state -------------------------------------------------

    def _draw_state(self):
        """Build the presentation state for the renderer.

        The single extraction boundary: everything the painter needs,
        resolved from the project model + view state, as plain data.
        """
        drag_ghost = None
        if self._drag:
            drag_ghost = (self._drag["track_id"],
                          self._drag["kind"],
                          self._drag["clip_index"],
                          self._drag["ghost_beat"])
        audio_peaks = {}
        sample_names = {}
        if self._project is not None and self._peaks_provider is not None:
            for track in self._project.tracks:
                for acl in track.audio_clips:
                    if acl.id:
                        try:
                            audio_peaks[acl.id] = (
                                self._peaks_provider(acl) or [])
                        except Exception:
                            audio_peaks[acl.id] = []
            sample_names = {s.id: (s.name or s.id)
                            for s in self._project.samples}
        return build_draw_state(
            self._project,
            bar_w=self._bar_w,
            bars=self._bars,
            show_beat_lines=self._snap_beats() == 1,
            selected=self._selected,
            drag_ghost=drag_ghost,
            pattern_colors=self._pattern_colors,
            pattern_names=self._pattern_names,
            audio_peaks=audio_peaks,
            sample_names=sample_names,
        )

    # -- drawing (delegated to PlaylistPainter) -------------------------------

    def _layout_scroll(self) -> None:
        self._painter.layout_scroll(
            list(self._canvases.values()), self._ruler,
            self._bars, self._bar_w)
        self._sync_scroll()

    def _on_scroll(self, *args) -> None:
        for cv in self._canvases.values():
            cv.xview(*args)
        self._ruler.xview(*args)
        self._sync_scroll()

    def _sync_scroll(self) -> None:
        if not self._canvases:
            return
        first, last = next(iter(self._canvases.values())).xview()
        self._scroll.set(first, last)

    def _draw_playhead(self) -> None:
        # Move existing playhead lines instead of delete/create (no flicker
        # during playback; _poll_position calls this ~25x/sec).
        if self._playhead_beats is None or self._project is None:
            for cv in self._canvases.values():
                self._painter.clear_playhead(cv)
            self._painter.clear_playhead(self._ruler)
            return
        x = self._playhead_beats / 4 * self._bar_w
        for cv in self._canvases.values():
            self._painter.move_playhead(cv, x, ROW_H)
        self._painter.move_playhead(self._ruler, x, RULER_H)

    # -- interaction ----------------------------------------------------

    def _on_snap_changed(self, _event=None) -> None:
        self.refresh()  # redraw beat subdivisions for the new resolution

    def _snap_beats(self) -> int:
        return SNAP_BEATS.get(self._snap_var.get(), 4)

    def _beat_w(self) -> float:
        return self._bar_w / 4.0

    def _quantize(self, beat: int) -> int:
        snap = self._snap_beats()
        return max(0, (beat // snap) * snap)

    def _beat_at(self, canvas, event) -> int:
        raw = int(canvas.canvasx(event.x) // self._beat_w())
        return self._quantize(raw)

    def beat_at_point(self, canvas, x_root: int) -> int:
        """Beat under a root-window x coordinate (for drag-and-drop)."""
        x = x_root - canvas.winfo_rootx()
        raw = int(canvas.canvasx(x) // self._beat_w())
        return self._quantize(raw)

    def _clip_hit(self, track, beat):
        """(kind, index) of the clip covering `beat`, or None.

        kind is "clip" (pattern) or "aclip" (audio). Pattern clips win
        ties (they're drawn first).
        """
        for i, clip in enumerate(track.clips):
            if clip.start_beat <= beat < clip.start_beat + clip.bars * 4:
                return ("clip", i)
        for j, acl in enumerate(track.audio_clips):
            if acl.start_beat <= beat < acl.start_beat + acl.length_beats:
                return ("aclip", j)
        return None

    def _hit_clip(self, track, kind, index):
        if kind == "clip":
            return track.clips[index]
        return track.audio_clips[index]

    def _click(self, event, track) -> None:
        cv = self._canvases[track.id]
        cv.focus_set()
        beat = self._beat_at(cv, event)
        hit = self._clip_hit(track, beat)
        if hit is None:
            self._selected = None
            self.refresh()
            self._on_place(track.id, beat)
        else:
            kind, idx = hit
            self._selected = (track.id, kind, idx)
            clip = self._hit_clip(track, kind, idx)
            start = clip.start_beat
            self._drag = {"track_id": track.id, "kind": kind,
                          "clip_index": idx, "beat0": start,
                          "ghost_beat": start, "x0": event.x}
            self.refresh()

    def _drag_motion(self, event, track) -> None:
        if not self._drag or self._drag["track_id"] != track.id:
            return
        cv = self._canvases[track.id]
        dx_beats = int(round((event.x - self._drag["x0"]) / self._beat_w()))
        new_beat = self._quantize(self._drag["beat0"] + dx_beats)
        # Live ghost only: the project is untouched until drop, so undo
        # restores the pre-drag position. Move the existing items instead
        # of redrawing the lane (dirty-region update, no flashing).
        if new_beat != self._drag["ghost_beat"]:
            dx_px = (new_beat - self._drag["ghost_beat"]) * self._beat_w()
            self._drag["ghost_beat"] = new_beat
            tag = ("clip-" if self._drag["kind"] == "clip"
                   else "aclip-") + str(self._drag["clip_index"])
            cv.move(tag, dx_px, 0)
            # Keep the retained geometry cache in sync with the direct
            # canvas move. A refresh() landing mid-drag (resize, undo,
            # playhead lane rebuild) diffs the cache: if the cache still
            # held the pre-move geometry, the delete+redraw would briefly
            # show the clip at two positions -- the "stretched ghost"
            # artifact reported on Windows (v0.43.0).
            cached = self._lane_cache(track.id).get(tag)
            if cached is not None:
                cached.x0 += dx_px
                cached.x1 += dx_px

    def _drag_end(self, event, track) -> None:
        if not self._drag or self._drag["track_id"] != track.id:
            return
        info = self._drag
        self._drag = None
        if info["ghost_beat"] != info["beat0"]:
            self._on_move(track.id, info["kind"], info["clip_index"],
                          info["beat0"], info["ghost_beat"])
        self.refresh()

    def _double(self, event, track) -> None:
        cv = self._canvases[track.id]
        beat = self._beat_at(cv, event)
        hit = self._clip_hit(track, beat)
        if hit is not None:
            kind, idx = hit
            if kind == "clip":
                self._on_edit_pattern(track.clips[idx].pattern_id)
            elif self._on_edit_audio_clip is not None:
                self._on_edit_audio_clip(track.id, idx)

    def _track_menu(self, track, event):
        """Right-click on a track header: identity menu (rename/color/icon/link)."""
        if self._on_track_menu is not None:
            self._on_track_menu(track.id, event.x_root, event.y_root)

    def _right_click(self, event, track) -> None:
        if self._on_clip_menu is None:
            return
        cv = self._canvases[track.id]
        beat = self._beat_at(cv, event)
        hit = self._clip_hit(track, beat)
        if hit is not None:
            kind, idx = hit
            self._selected = (track.id, kind, idx)
            self.refresh()
            self._on_clip_menu(track.id, kind, idx, event.x_root, event.y_root)

    def _delete_selected(self) -> None:
        if self._selected is not None:
            track_id, kind, clip_index = self._selected
            self._selected = None
            self._on_delete(track_id, kind, clip_index)
