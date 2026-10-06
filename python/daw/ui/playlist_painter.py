"""PlaylistPainter: dedicated renderer for the playlist timeline.

Painter-extraction architecture (FL Studio vs LMMS "Further Painter
Extraction" research):

    PROJECT/AUDIO MODEL
            |
            v
    PRESENTATION STATE          <- PlaylistDraw (plain data, no tkinter)
            |
    +-------+-------+
    |               |
    v               v
  INTERACTION    RENDERER        <- PlaylistPainter (draws state only)
  (Playlist)        |
  mouse/keyboard    v
  drag/selection  CANVAS
  editing cmds

The central principle: INTERACTION CHANGES STATE. RENDERING DISPLAYS
STATE. The painter never inspects the project model, never handles
input, and never mutates anything -- it receives a PlaylistDraw built by
build_draw_state() and issues canvas draw commands.

Two update frequencies (from the research):
  * structural  -> draw_ruler / draw_lane (rebuild the scene)
  * continuous  -> move_playhead (dirty-region update, no rebuild)

This mirrors the existing pianoroll_painter.py pattern.
"""

from __future__ import annotations

from dataclasses import dataclass, field

BAR_W = 44
ROW_H = 46
RULER_H = 22
HEADER_W = 150
VISIBLE_BARS = 16

PATTERN_COLORS = [
    "#f778ba", "#79c0ff", "#7ee787", "#ffa657", "#d2a8ff",
    "#ff9e64", "#56d4dd", "#e3b341",
]

PLAYHEAD_COLOR = "#f85149"

# Audio clips get their own lane color (FL's Playlist audio-clip tint vs
# the Channel Rack pattern colors): steel blue, distinct from every
# pattern palette entry.
AUDIO_CLIP_COLOR = "#58a6ff"
AUDIO_CLIP_WAVE = "#0d1117"


# -- presentation state (plain data, no tkinter) --------------------------

@dataclass
class ClipDraw:
    """One clip block, fully resolved to pixel geometry."""
    x0: float
    x1: float
    color: str
    label: str
    selected: bool
    tag: str  # canvas tag, e.g. "clip-2" or "aclip-0"
    kind: str = "pattern"  # "pattern" | "audio"
    peaks: list = field(default_factory=list)  # [(min, max)] waveform, audio only
    muted: bool = False
    reversed: bool = False


@dataclass
class LaneDraw:
    """One track lane's drawable content."""
    track_id: str
    clips: list[ClipDraw] = field(default_factory=list)


@dataclass
class PlaylistDraw:
    """Everything the playlist shows, independent of how it was drawn."""
    bars: int
    bar_w: float
    show_beat_lines: bool
    lanes: list[LaneDraw] = field(default_factory=list)
    pattern_legend: dict = field(default_factory=dict)  # pattern_id -> color


def build_draw_state(project, *, bar_w: float, bars: int,
                     show_beat_lines: bool,
                     selected: tuple | None,
                     drag_ghost: tuple | None,
                     pattern_colors: dict,
                     pattern_names: dict,
                     audio_peaks: dict | None = None,
                     sample_names: dict | None = None) -> PlaylistDraw:
    """Build the presentation state from the project model.

    Pure function: no tkinter, no interaction state beyond the explicit
    arguments. `selected` is (track_id, kind, index) or None; `drag_ghost`
    is (track_id, kind, index, ghost_beat) or None. `audio_peaks` maps
    audio-clip id -> pre-windowed [(min, max)] peaks (the widget slices
    the source window and reverses for reversed clips); `sample_names`
    maps sample id -> display name.
    """
    beat_w = bar_w / 4.0
    audio_peaks = audio_peaks or {}
    sample_names = sample_names or {}
    lanes = []
    for track in project.tracks:
        clips = []
        for i, clip in enumerate(track.clips):
            start_beat = clip.start_beat
            if (drag_ghost is not None
                    and drag_ghost[0] == track.id
                    and drag_ghost[1] == "clip"
                    and drag_ghost[2] == i):
                start_beat = drag_ghost[3]
            x0 = start_beat * beat_w + 1
            x1 = (start_beat + clip.bars * 4) * beat_w - 1
            color = pattern_colors.get(clip.pattern_id, "#8b949e")
            clips.append(ClipDraw(
                x0=x0,
                x1=x1,
                color=color,
                label=f"{pattern_names.get(clip.pattern_id, '?')} "
                      f"x{clip.bars}",
                selected=selected == (track.id, "clip", i),
                tag=f"clip-{i}",
            ))
        for j, acl in enumerate(track.audio_clips):
            start_beat = acl.start_beat
            if (drag_ghost is not None
                    and drag_ghost[0] == track.id
                    and drag_ghost[1] == "aclip"
                    and drag_ghost[2] == j):
                start_beat = drag_ghost[3]
            x0 = start_beat * beat_w + 1
            x1 = (start_beat + acl.length_beats) * beat_w - 1
            name = sample_names.get(acl.asset_id, "?")
            flags = []
            if acl.reverse:
                flags.append("rev")
            if acl.muted:
                flags.append("mute")
            if acl.pitch_semitones or acl.fine_cents:
                flags.append(f"{acl.pitch_semitones:+.0f}st")
            label = name + (" [" + " ".join(flags) + "]" if flags else "")
            clips.append(ClipDraw(
                x0=x0,
                x1=x1,
                color=AUDIO_CLIP_COLOR,
                label=label,
                selected=selected == (track.id, "aclip", j),
                tag=f"aclip-{j}",
                kind="audio",
                peaks=audio_peaks.get(acl.id, []),
                muted=acl.muted,
                reversed=acl.reverse,
            ))
        lanes.append(LaneDraw(track_id=track.id, clips=clips))
    return PlaylistDraw(
        bars=bars,
        bar_w=bar_w,
        show_beat_lines=show_beat_lines,
        lanes=lanes,
        pattern_legend=dict(pattern_colors),
    )


# -- renderer ---------------------------------------------------------------

class PlaylistPainter:
    """Draws a PlaylistDraw onto tkinter canvases.

    Stateless with respect to the project: every draw method takes only
    canvases and presentation state. The widget (interaction layer) owns
    the canvases and decides when to redraw.
    """

    # -- structural (infrequent) ----------------------------------------

    def draw_ruler(self, canvas, draw: PlaylistDraw,
                   x0: float, x1: float) -> None:
        """Draw bar numbers for the visible pixel range [x0, x1]."""
        canvas.delete("all")
        bar0 = max(0, int(x0 // draw.bar_w))
        bar1 = int(x1 // draw.bar_w) + 1
        for bar in range(bar0, min(bar1, draw.bars)):
            x = bar * draw.bar_w
            canvas.create_line(x, 8, x, RULER_H, fill="#30363d")
            canvas.create_text(x + 4, 4, text=f"{bar + 1}", anchor="nw",
                               fill="#8b949e", font=("", 8))

    def draw_lane(self, renderer, lane: LaneDraw,
                  draw: PlaylistDraw) -> None:
        """Draw one track lane: grid lines + clip blocks.

        Draws through the Renderer interface (research topic 35): the
        painter emits primitives, the backend draws them.
        """
        renderer.clear()
        beat_w = draw.bar_w / 4.0
        for bar in range(draw.bars + 1):
            x = bar * draw.bar_w
            renderer.draw_line([x, 0, x, ROW_H], fill="#21262d")
        if draw.show_beat_lines:
            for b in range(draw.bars * 4 + 1):
                if b % 4:
                    renderer.draw_line([b * beat_w, 0, b * beat_w, ROW_H],
                                       fill="#181f28")
        for clip in lane.clips:
            self.draw_clip(renderer, clip)

    def draw_clip(self, renderer, clip: ClipDraw) -> None:
        """Draw one clip block (pattern or audio) as retained node clip.tag."""
        if clip.kind == "audio":
            self._draw_audio_clip(renderer, clip)
            return
        renderer.draw_rect(
            clip.x0, 4, clip.x1, ROW_H - 4,
            fill=clip.color,
            outline="#ffffff" if clip.selected else clip.color,
            width=2 if clip.selected else 1,
            node_id=clip.tag)
        renderer.draw_text(clip.x0 + 6, ROW_H // 2, text=clip.label,
                           anchor="w", fill="#0d1117",
                           font=("", 9, "bold"), node_id=clip.tag)

    def redraw_clips(self, renderer, lane: LaneDraw,
                     tags) -> "list[DirtyRect]":
        """Partial lane redraw (research topic 34) through the Renderer.

        Deletes and redraws ONLY the clips with the given node ids --
        the grid lines and every other clip are untouched. This is partial
        INVALIDATION + partial DRAW-COMMAND generation: draw commands are
        emitted only for dirty nodes (the brief's section-31 third layer;
        RecordingRenderer proves it in tests). Pixel-level repainting
        stays the toolkit's job.

        Returns the dirty rects (clip bounds, padded) so the caller can
        log them in the DirtyTracker.
        """
        from .dirty_regions import DirtyRect
        by_tag = {c.tag: c for c in lane.clips}
        rects: list[DirtyRect] = []
        for tag in tags:
            clip = by_tag.get(tag)
            if clip is None:
                continue
            renderer.delete(tag)
            self.draw_clip(renderer, clip)
            rects.append(DirtyRect(clip.x0 - 2, 0, clip.x1 + 2, ROW_H))
        return rects

    def _draw_audio_clip(self, renderer, clip: ClipDraw) -> None:
        """Draw one audio clip: tinted block + waveform + label."""
        fill = clip.color if not clip.muted else "#3a4552"
        renderer.draw_rect(
            clip.x0, 4, clip.x1, ROW_H - 4,
            fill=fill,
            outline="#ffffff" if clip.selected else fill,
            width=2 if clip.selected else 1,
            node_id=clip.tag)
        # Waveform: one vertical line per peak bucket, stretched across
        # the clip rect. Peaks are pre-windowed to the clip's source
        # window by the widget (reversed for reversed clips).
        peaks = clip.peaks
        w = clip.x1 - clip.x0
        if peaks and w > 4:
            mid = ROW_H / 2
            amp = (ROW_H - 14) / 2
            n = len(peaks)
            # One line per pixel column (downsample when narrow).
            cols = max(1, int(w - 4))
            for x in range(cols):
                lo, hi = peaks[(x * n) // cols]
                y0 = mid - hi * amp
                y1 = mid - lo * amp
                if y1 - y0 < 1:
                    y1 = y0 + 1
                renderer.draw_line([clip.x0 + 2 + x, y0,
                                    clip.x0 + 2 + x, y1],
                                   fill=AUDIO_CLIP_WAVE, node_id=clip.tag)
        renderer.draw_text(clip.x0 + 6, 10, text=clip.label,
                           anchor="nw", fill="#0d1117",
                           font=("", 8, "bold"), node_id=clip.tag)

    # -- continuous (high frequency) -------------------------------------

    def draw_playhead(self, canvas, x: float, height: float) -> None:
        """Create the playhead line (first paint)."""
        canvas.create_line(x, 0, x, height, fill=PLAYHEAD_COLOR, width=2,
                           tags=("playhead",))

    def move_playhead(self, canvas, x: float, height: float) -> None:
        """Move the existing playhead; create it if missing.

        Dirty-region update: no delete/create churn at ~25 fps.
        """
        if canvas.find_withtag("playhead"):
            canvas.coords("playhead", x, 0, x, height)
        else:
            self.draw_playhead(canvas, x, height)

    def clear_playhead(self, canvas) -> None:
        canvas.delete("playhead")

    # -- layout ------------------------------------------------------------

    def layout_scroll(self, canvases, ruler, bars: int,
                      bar_w: float) -> None:
        """Set scroll regions for lanes + ruler."""
        total = bars * bar_w
        for cv in canvases:
            cv.configure(scrollregion=(0, 0, total, ROW_H))
            cv.xview_moveto(0)
        ruler.configure(scrollregion=(0, 0, total, RULER_H))
        ruler.xview_moveto(0)
