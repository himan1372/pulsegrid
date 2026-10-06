"""Step sequencer grid: 16 steps x N channels, canvas-based.

Click toggles steps. The playhead highlight follows engine position.
Row headers (name + MIDI pitch) live in a side panel so they can use
native widgets.
"""

import tkinter as tk
from tkinter import ttk
from typing import Callable

from .widgets import Tooltip

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

INSTRUMENT_COLORS = {
    "kick": "#ff7b72",
    "snare": "#ffa657",
    "hat": "#d2a8ff",
    "bass": "#7ee787",
    "lead": "#79c0ff",
}

CELL = 44
ROW_H = 52
GRID_BG = "#0d1117"
GRID_LINE = "#21262d"
BEAT_LINE = "#3d444d"


def midi_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{(midi // 12) - 1}"


class Sequencer(ttk.Frame):
    def __init__(self, parent, on_toggle: Callable[[str, int], None],
                 on_pitch: Callable[[str, int], None],
                 on_channel_menu: Callable[[str, object], None] | None = None):
        super().__init__(parent)
        self._on_toggle = on_toggle
        self._on_pitch = on_pitch
        self._on_channel_menu = on_channel_menu
        self._pattern = None
        self._playhead: int | None = None
        self._pitch_vars: dict[str, tk.IntVar] = {}

        # Header panel (row labels + pitch editors)
        self.headers = ttk.Frame(self)
        self.headers.pack(side="left", fill="y")

        # Grid canvas
        self.canvas = tk.Canvas(self, bg=GRID_BG, highlightthickness=0)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.canvas.bind("<Button-1>", self._on_click)
        self.canvas.bind("<Configure>", lambda _e: self._draw())

    # -- data -----------------------------------------------------------

    def set_pattern(self, pattern) -> None:
        self._pattern = pattern
        # Rebuild header rows
        for child in self.headers.winfo_children():
            child.destroy()
        self._pitch_vars.clear()
        # Spacer to align with canvas top padding
        spacer = ttk.Frame(self.headers, height=8)
        spacer.pack(fill="x")
        for ch in pattern.channels:
            row = ttk.Frame(self.headers, height=ROW_H, width=210)
            row.pack(fill="x", pady=0)
            row.pack_propagate(False)
            # Drop target for Browser instruments.
            row._pulsegrid_channel_id = ch.id
            color = INSTRUMENT_COLORS.get(ch.instrument, "#8b949e")
            dot = tk.Canvas(row, width=12, height=12, bg="#161b22",
                            highlightthickness=0)
            dot.pack(side="left", padx=(8, 4))
            dot.create_oval(1, 1, 11, 11, fill=color, outline=color)
            ttk.Label(row, text=ch.name, width=7, font=("", 10, "bold")).pack(side="left")
            var = tk.IntVar(value=ch.pitch)
            self._pitch_vars[ch.id] = var
            spin = ttk.Spinbox(row, from_=0, to=127, width=4, textvariable=var,
                               command=lambda cid=ch.id: self._pitch_changed(cid))
            spin.pack(side="left", padx=(4, 2))
            spin.bind("<Return>", lambda _e, cid=ch.id: self._pitch_changed(cid))
            spin.bind("<FocusOut>", lambda _e, cid=ch.id: self._pitch_changed(cid))
            Tooltip(spin, "Default pitch for new notes\n"
                          "(existing notes keep their own pitch;\n"
                          "edit pitches in the Piano Roll)")
            name_var = tk.StringVar(value=midi_name(ch.pitch))
            var.trace_add("write", lambda *_a, v=name_var, cid=ch.id: v.set(midi_name(self._safe_pitch(cid))))
            ttk.Label(row, textvariable=name_var, width=4,
                      foreground="#8b949e").pack(side="left")
            # Right-click anywhere on the row opens the channel menu.
            if self._on_channel_menu is not None:
                for target in [row] + list(row.winfo_children()):
                    target.bind("<Button-3>",
                                lambda e, cid=ch.id: self._on_channel_menu(cid, e),
                                add="+")
        self._draw()

    def _safe_pitch(self, channel_id: str) -> int:
        try:
            return max(0, min(127, int(self._pitch_vars[channel_id].get())))
        except (tk.TclError, ValueError):
            return 60

    def _pitch_changed(self, channel_id: str) -> None:
        pitch = self._safe_pitch(channel_id)
        self._pitch_vars[channel_id].set(pitch)
        self._on_pitch(channel_id, pitch)

    def refresh(self) -> None:
        """Redraw after external project changes (undo, load)."""
        if self._pattern is None:
            return
        for ch in self._pattern.channels:
            if ch.id in self._pitch_vars:
                self._pitch_vars[ch.id].set(ch.pitch)
        self._draw()

    def set_playhead(self, step: int | None) -> None:
        if step == self._playhead:
            return
        self._playhead = step
        # Only the playhead highlight changes: delete its tag and redraw
        # just those outlines instead of the whole grid.
        c = self.canvas
        c.delete("playhead")
        if step is not None and self._pattern is not None:
            steps = self._pattern.steps
            if 0 <= step < steps:
                for r in range(len(self._pattern.channels)):
                    x0, y0 = 8 + step * CELL, 8 + r * ROW_H
                    x1, y1 = x0 + CELL, y0 + ROW_H
                    c.create_rectangle(x0 + 2, y0 + 4, x1 - 2, y1 - 4,
                                       outline="#58a6ff", width=2,
                                       tags=("playhead",))

    # -- rendering ------------------------------------------------------

    def _on_click(self, event) -> None:
        if self._pattern is None:
            return
        col = (event.x - 8) // CELL
        row = (event.y - 8) // ROW_H
        if 0 <= col < self._pattern.steps and 0 <= row < len(self._pattern.channels):
            self._on_toggle(self._pattern.channels[row].id, col)

    def _draw(self) -> None:
        c = self.canvas
        c.delete("all")
        if self._pattern is None:
            return
        steps = self._pattern.steps
        rows = len(self._pattern.channels)
        width = 8 + steps * CELL + 8
        height = 8 + rows * ROW_H + 8
        c.config(scrollregion=(0, 0, width, height))

        # Cells
        for r, ch in enumerate(self._pattern.channels):
            color = INSTRUMENT_COLORS.get(ch.instrument, "#8b949e")
            for s in range(steps):
                x0, y0 = 8 + s * CELL, 8 + r * ROW_H
                x1, y1 = x0 + CELL, y0 + ROW_H
                on = ch.covers(s)
                fill = color if on else GRID_BG
                outline = BEAT_LINE if s % 4 == 0 else GRID_LINE
                c.create_rectangle(x0 + 2, y0 + 4, x1 - 2, y1 - 4,
                                   fill=fill, outline=outline, width=1 if s % 4 else 2,
                                   stipple="" if on else "gray25")
        # Playhead highlight is managed separately by set_playhead()
        # (incremental updates, no full redraw during playback).
        # Bar numbers
        for s in range(0, steps, 4):
            c.create_text(8 + s * CELL + 2, 6, text=str(s // 4 + 1),
                          fill="#8b949e", font=("", 8), anchor="sw")

        # Empty state hint
        if not any(ch.notes for ch in self._pattern.channels):
            c.create_text(width / 2, height / 2,
                          text="Click steps to build your pattern -- press Space to play it",
                          fill="#8b949e", font=("", 11, "italic"))
        # Re-apply the playhead highlight after a full redraw.
        step, self._playhead = self._playhead, None
        self.set_playhead(step)
