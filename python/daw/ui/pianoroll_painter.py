"""PianoRollPainter: dedicated renderer for the piano roll canvas.

LMMS lesson (March 2026 "Pianoroll refactor into Pianorollpainter"):
separate the piano roll's *rendering* from its *state/interaction*.

The PianoRoll widget owns: the note list, selection, mouse gestures,
undo commits. This painter owns: everything drawn on the canvas --
the grid, the notes, the playhead -- as retained canvas items updated
via dirty-region edits (coords/itemconfig), never widget trees.

This is the FL Studio philosophy at tkinter scale: the piano roll is a
draw-command surface, not a collection of widgets.
"""

import tkinter as tk

ROW_H = 14
COL_W = 36
GUTTER = 46
PITCH_TOP = 96   # C7
PITCH_BOTTOM = 24  # C1
VEL_LANE_H = 90  # Velocity lane height (below the note grid)
PAN_BAR_COLOR = "#e8a33d"  # LMMS-style orange for pan bars
VEL_LANE_GAP = 8  # Gap between grid and lane

NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]

INSTRUMENT_COLORS = {
    "kick": "#f778ba", "snare": "#ffa657", "hat": "#d2a8ff",
    "bass": "#7ee787", "lead": "#79c0ff",
}


def pitch_name(p: int) -> str:
    return f"{NOTE_NAMES[p % 12]}{p // 12 - 1}"


def is_black(p: int) -> bool:
    return NOTE_NAMES[p % 12] in ("C#", "D#", "F#", "G#", "A#")


def shade(hex_color: str, factor: float) -> str:
    """Scale a #rrggbb color's brightness (for velocity shading)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    f = max(0.15, min(1.0, factor))
    return f"#{int(r * f):02x}{int(g * f):02x}{int(b * f):02x}"


class PianoRollPainter:
    """Renders piano-roll state onto a tkinter Canvas."""

    def __init__(self, canvas: tk.Canvas):
        self._cv = canvas
        self._playhead_step = None

    # -- full scene -------------------------------------------------------

    def draw_all(self, notes, n_steps: int, instrument: str,
                 playhead_step, selected_idx, lane_mode: str = "Vel") -> None:
        """Rebuild the whole scene (structural changes only)."""
        cv = self._cv
        cv.delete("all")
        width = GUTTER + n_steps * COL_W
        grid_h = (PITCH_TOP - PITCH_BOTTOM + 1) * ROW_H
        lane_y0 = grid_h + VEL_LANE_GAP
        height = lane_y0 + VEL_LANE_H
        cv.configure(scrollregion=(0, 0, width, height))
        color = INSTRUMENT_COLORS.get(instrument, "#8b949e")

        for p in range(PITCH_BOTTOM, PITCH_TOP + 1):
            row = PITCH_TOP - p
            y0 = row * ROW_H
            bg = "#11161d" if is_black(p) else "#0d1117"
            cv.create_rectangle(0, y0, width, y0 + ROW_H, fill=bg, outline="")
            if NOTE_NAMES[p % 12] == "C":
                cv.create_text(GUTTER - 6, y0 + ROW_H / 2,
                               text=pitch_name(p), anchor="e",
                               fill="#8b949e", font=("", 8))
                cv.create_line(GUTTER, y0, width, y0, fill="#2a3340")
            for s in range(n_steps + 1):
                x = GUTTER + s * COL_W
                strong = s % 4 == 0
                cv.create_line(x, y0, x, y0 + ROW_H,
                               fill="#2a3340" if strong else "#1a222c")

        for i, n in enumerate(notes):
            self._draw_note(i, n, color, selected=(i == selected_idx))

        # Lower lane (below the grid): velocity or pan bars.
        self._draw_lower_lane(notes, n_steps, color, lane_y0, lane_mode)

        self._playhead_step = None
        if playhead_step is not None:
            self.set_playhead(playhead_step, n_steps)

    def _draw_note(self, i: int, n, color: str, selected: bool) -> None:
        coords = self.note_coords(n)
        if coords is None:
            return
        x0, y0, x1, y1 = coords
        self._cv.create_rectangle(
            x0, y0, x1, y1,
            fill=shade(color, 0.3 + 0.7 * n.vel),
            outline="#ffffff" if selected else "#0d1117",
            width=2 if selected else 1,
            tags=(f"note-{i}", "note"))

    def _draw_lower_lane(self, notes, n_steps: int, color: str,
                         lane_y0: float, lane_mode: str = "Vel") -> None:
        """Draw the lower lane: per-note velocity or pan bars.

        Bars are note-attached (positioned by note.start, move with notes),
        not independent timeline automation. Pan mode draws LMMS-style
        bidirectional orange bars from a center line (up = left, down =
        right); velocity mode draws FL/LMMS-style bars from the bottom.
        """
        cv = self._cv
        width = GUTTER + n_steps * COL_W
        lane_y1 = lane_y0 + VEL_LANE_H
        # Lane background.
        cv.create_rectangle(0, lane_y0, width, lane_y1,
                            fill="#0a0e13", outline="")
        cv.create_line(0, lane_y0, width, lane_y0, fill="#2a3340")
        # Label.
        cv.create_text(GUTTER - 6, lane_y0 + 12,
                       text="Pan" if lane_mode == "Pan" else "Vel", anchor="e",
                       fill="#8b949e", font=("", 8, "bold"))
        # Grid lines (match note grid).
        for s in range(n_steps + 1):
            x = GUTTER + s * COL_W
            strong = s % 4 == 0
            cv.create_line(x, lane_y0, x, lane_y1,
                           fill="#1a222c" if strong else "#11161d")
        if lane_mode == "Pan":
            # Center line: 0.5 (center). L/R labels.
            cy = lane_y0 + VEL_LANE_H / 2
            cv.create_line(GUTTER, cy, width, cy, fill="#3a4556")
            cv.create_text(GUTTER - 6, lane_y0 + 14, text="L", anchor="e",
                           fill="#8b949e", font=("", 8))
            cv.create_text(GUTTER - 6, lane_y1 - 8, text="R", anchor="e",
                           fill="#8b949e", font=("", 8))
            for i, n in enumerate(notes):
                self._draw_pan_bar(i, n, lane_y0)
        else:
            for i, n in enumerate(notes):
                self._draw_vel_bar(i, n, color, lane_y0)

    def _draw_vel_bar(self, i: int, n, color: str, lane_y0: float) -> None:
        """Draw a single velocity bar."""
        coords = self.vel_bar_coords(n, lane_y0)
        if coords is None:
            return
        x0, y0, x1, y1 = coords
        self._cv.create_rectangle(
            x0, y0, x1, y1,
            fill=shade(color, 0.4 + 0.6 * n.vel),
            outline="",
            tags=(f"vel-{i}", "velbar"))

    @staticmethod
    def vel_bar_coords(n, lane_y0: float):
        """Canvas bbox for a note's velocity bar."""
        # Bar centered on the note's start step.
        cx = GUTTER + n.start * COL_W + COL_W / 2
        bar_w = min(14, COL_W * 0.4)
        x0 = cx - bar_w / 2
        x1 = cx + bar_w / 2
        # Height proportional to velocity (0.0-1.0).
        bar_h = max(2, n.vel * (VEL_LANE_H - 20))
        y1 = lane_y0 + VEL_LANE_H - 8  # Bottom margin.
        y0 = y1 - bar_h
        return (x0, y0, x1, y1)

    @staticmethod
    def lane_y0() -> float:
        """Y-coordinate where the velocity lane starts."""
        grid_h = (PITCH_TOP - PITCH_BOTTOM + 1) * ROW_H
        return grid_h + VEL_LANE_GAP

    def update_vel_bar(self, i: int, n, color: str) -> None:
        """Dirty-region update: move/resize a single velocity bar."""
        coords = self.vel_bar_coords(n, self.lane_y0())
        if coords is None:
            return
        x0, y0, x1, y1 = coords
        self._cv.coords(f"vel-{i}", x0, y0, x1, y1)
        self._cv.itemconfig(f"vel-{i}",
                            fill=shade(color, 0.4 + 0.6 * n.vel))

    def _draw_pan_bar(self, i: int, n, lane_y0: float) -> None:
        """Draw a single pan bar (bidirectional from the center line)."""
        coords = self.pan_bar_coords(n, lane_y0)
        if coords is None:
            return
        x0, y0, x1, y1 = coords
        self._cv.create_rectangle(
            x0, y0, x1, y1,
            fill=PAN_BAR_COLOR, outline="",
            tags=(f"pan-{i}", "panbar"))

    @staticmethod
    def pan_bar_coords(n, lane_y0: float):
        """Canvas bbox for a note's pan bar (0.0 left .. 1.0 right)."""
        # Bar centered on the note's start step.
        cx = GUTTER + n.start * COL_W + COL_W / 2
        bar_w = min(14, COL_W * 0.4)
        x0 = cx - bar_w / 2
        x1 = cx + bar_w / 2
        cy = lane_y0 + VEL_LANE_H / 2
        half = VEL_LANE_H / 2 - 10
        # Up from center = left (pan < 0.5), down = right (pan > 0.5).
        extent = abs(n.pan - 0.5) * 2 * half
        if n.pan < 0.5:
            y0, y1 = cy - max(2, extent), cy
        else:
            y0, y1 = cy, cy + max(2, extent)
        return (x0, y0, x1, y1)

    def update_pan_bar(self, i: int, n) -> None:
        """Dirty-region update: move/resize a single pan bar."""
        coords = self.pan_bar_coords(n, self.lane_y0())
        if coords is None:
            return
        x0, y0, x1, y1 = coords
        # The widget only calls this in Pan mode (mode switches do a
        # full redraw), so the tag always exists here.
        self._cv.coords(f"pan-{i}", x0, y0, x1, y1)

    # -- dirty-region updates ---------------------------------------------

    @staticmethod
    def note_coords(n):
        """Canvas bbox for a note, or None if outside the pitch range."""
        row = PITCH_TOP - n.pitch
        if not (0 <= row < PITCH_TOP - PITCH_BOTTOM + 1):
            return None
        x0 = GUTTER + n.start * COL_W + 1
        x1 = GUTTER + (n.start + n.length) * COL_W - 1
        return (x0, row * ROW_H + 2, x1, (row + 1) * ROW_H - 2)

    def refresh_note(self, i: int, n, color: str,
                     draw_all_fallback) -> None:
        """Move/resize just the canvas item for note i (no full redraw)."""
        cv = self._cv
        tag = f"note-{i}"
        if not cv.find_withtag(tag):
            draw_all_fallback()
            return
        coords = self.note_coords(n)
        if coords is None:
            cv.delete(tag)
            return
        cv.coords(tag, *coords)
        cv.itemconfig(tag, fill=shade(color, 0.3 + 0.7 * n.vel))

    def set_selection(self, old_idx, new_idx) -> None:
        """Update selection outlines without redrawing."""
        cv = self._cv
        if old_idx is not None:
            cv.itemconfig(f"note-{old_idx}", outline="#0d1117", width=1)
        if new_idx is not None:
            cv.itemconfig(f"note-{new_idx}", outline="#ffffff", width=2)

    def set_playhead(self, step, n_steps: int) -> None:
        """Move the playhead line incrementally."""
        cv = self._cv
        if (self._playhead_step is not None and
                cv.find_withtag("playhead")):
            x = GUTTER + step * COL_W
            cv.coords("playhead", x, 0, x,
                      (PITCH_TOP - PITCH_BOTTOM + 1) * ROW_H)
        else:
            cv.delete("playhead")
            if 0 <= step < n_steps:
                x = GUTTER + step * COL_W
                cv.create_line(x, 0, x,
                               (PITCH_TOP - PITCH_BOTTOM + 1) * ROW_H,
                               fill="#f85149", width=2, tags="playhead")
        self._playhead_step = step

    def clear_playhead(self) -> None:
        self._cv.delete("playhead")
        self._playhead_step = None

    def color_for(self, instrument: str) -> str:
        return INSTRUMENT_COLORS.get(instrument, "#8b949e")

    def update_note_shading(self, i: int, n, color: str) -> None:
        """Dirty-region: update a note's fill for its velocity."""
        self._cv.itemconfig(f"note-{i}",
                            fill=shade(color, 0.3 + 0.7 * n.vel))
