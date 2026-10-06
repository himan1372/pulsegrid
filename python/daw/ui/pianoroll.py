"""Piano Roll: note editing on a time/pitch grid for one channel.

Notes have variable lengths and may overlap (chords). Click empty space
to add a note, drag a note's body to move it, drag its right edge to
resize it, right-click to delete, Alt+drag to set velocity (or pan when
the lower lane is in Pan mode). The lower lane edits per-note velocity
or per-note pan (FL Studio note.pan: 0.0 left, 0.5 center, 1.0 right) -
an LMMS-style Vel/Pan toggle. One drag gesture = one undo step: edits
accumulate locally and commit on release.

Sub-step timing (FL Studio model): note positions are continuous (the
engine renders fractional steps sample-accurately); the snap selector is
only a placement aid. Snap values are fractions of a 1/16-note step:
1/16 = 1 step, 1/32 = 1/2 step, 1/64 = 1/4 step, 1/128 = 1/8 step.
"Off" disables snapping (free placement). Holding Alt during a
move/resize/paint drag temporarily bypasses snap; Alt+mousewheel nudges
the selected note by the snap increment. Wheel scrolls vertically,
Shift+wheel scrolls horizontally, middle-drag pans (FL conventions).

Architecture (LMMS PianoRollPainter lesson): this widget owns state and
interaction; all canvas rendering is delegated to PianoRollPainter.
"""

import tkinter as tk
from tkinter import ttk

from ..project import Note
from .pianoroll_painter import (PianoRollPainter, ROW_H, COL_W, GUTTER,
                                PITCH_TOP, PITCH_BOTTOM, VEL_LANE_H)
from .widgets import Tooltip

RESIZE_PX = 6

# Snap increments in steps (1 step = 1/16 note). "Off" = 0.0 = free.
SNAP_STEPS = [("1/16", 1.0), ("1/32", 0.5), ("1/64", 0.25),
              ("1/128", 0.125), ("Off", 0.0)]
# Shortest note the editor will create (1/128 note); keeps tiny notes
# selectable even with snap off.
MIN_NOTE_LEN = 0.125


# Alt (Mod1) held while pressing a note: adjust its velocity by dragging.
# Alt held DURING a move/resize/paint drag: bypass snap (FL-style).
ALT_MASK = 0x0008
# Shift mask for Shift+mousewheel note nudge.
SHIFT_MASK = 0x0001


def pan_label(pan: float) -> str:
    """Short readout for a note pan: e.g. 'L30', 'C', 'R45'."""
    off = round((pan - 0.5) * 200)  # -100..+100
    if abs(off) <= 2:
        return "C"
    side = "L" if off < 0 else "R"
    return f"{side}{abs(off)}"


def snap_step(s: float, inc: float) -> float:
    """Snap a step position to increments of `inc` (round-half-up).

    `inc <= 0` means snap off: the position is returned unchanged, so
    note placement stays continuous (the engine renders fractional
    steps sample-accurately).
    """
    import math
    if inc <= 0.0:
        return s
    return math.floor(s / inc + 0.5) * inc


class PianoRoll(ttk.Frame):
    def __init__(self, master, on_commit):
        """on_commit(channel_id, notes): one gesture's edits."""
        super().__init__(master)
        self._on_commit = on_commit
        self._pattern = None
        self._channel_id = None
        self._notes: list[Note] = []
        self._selected = None  # index into _notes
        self._playhead_step = None
        self._gesture = None  # "paint" | "move" | "resize" | "velocity" | None
        self._grab = None      # gesture state dict
        self._vel_anchor = None  # (y_root, vel) when adjusting velocity

        top = ttk.Frame(self)
        top.pack(side="top", fill="x", padx=8, pady=(6, 2))
        ttk.Label(top, text="Channel:").pack(side="left")
        self._ch_var = tk.StringVar()
        self._ch_combo = ttk.Combobox(top, textvariable=self._ch_var,
                                      state="readonly", width=18)
        self._ch_combo.pack(side="left", padx=6)
        self._ch_combo.bind("<<ComboboxSelected>>", self._on_channel_picked)
        Tooltip(self._ch_combo, "Channel to edit in the piano roll")
        ttk.Label(top, text="Snap:").pack(side="left", padx=(12, 2))
        self._snap_var = tk.StringVar(value="1/16")
        self._snap_combo = ttk.Combobox(top, textvariable=self._snap_var,
                                        state="readonly", width=6,
                                        values=[label for label, _ in SNAP_STEPS])
        self._snap_combo.pack(side="left")
        Tooltip(self._snap_combo,
                "Snap grid for note placement (fractions of a 1/16-note step).\n"
                "Note positions themselves are continuous - the engine renders\n"
                "fractional steps sample-accurately; snap is only a placement aid.\n"
                "'Off' places notes freely. Hold Alt while dragging to\n"
                "temporarily bypass snap. Shift+mousewheel nudges the selected note.")
        # LMMS-style lane mode: the lower lane edits per-note velocity
        # or per-note pan (FL Studio note.pan: 0.0 left .. 1.0 right).
        ttk.Label(top, text="Lane:").pack(side="left", padx=(12, 2))
        self._lane_var = tk.StringVar(value="Vel")
        lane_frame = ttk.Frame(top)
        lane_frame.pack(side="left")
        for label in ("Vel", "Pan"):
            ttk.Radiobutton(lane_frame, text=label, value=label,
                            variable=self._lane_var,
                            command=self._on_lane_mode).pack(side="left")
        Tooltip(lane_frame,
                "Lower lane edits per-note Velocity or per-note Pan.\n"
                "Pan is a true note property (0.0 left, 0.5 center, 1.0\n"
                "right) - simultaneous notes can have independent pans.")
        ttk.Label(top, text="Click: add | drag note: move | drag right edge: length | "
                            "right-click: delete | Alt+drag: vel/pan | "
                            "Shift+wheel: nudge",
                  foreground="#8b949e", font=("", 9)).pack(side="left", padx=12)
        self._vel_label = ttk.Label(top, text="", font=("", 9, "bold"),
                                    foreground="#79c0ff")
        self._vel_label.pack(side="left")
        Tooltip(top, "Click empty space to add a note (1 step).\n"
                     "Drag a note's body to move it, drag its right edge\n"
                     "to change its length. Right-click deletes a note.\n"
                     "Hold Alt and drag a note up/down to set its velocity.\n"
                     "Notes may overlap: stack them for chords.")

        body = ttk.Frame(self)
        body.pack(side="top", fill="both", expand=True)
        self._canvas = tk.Canvas(body, bg="#0d1117", highlightthickness=0)
        self._canvas.pack(side="left", fill="both", expand=True)
        scroll = ttk.Scrollbar(body, orient="vertical",
                               command=self._canvas.yview)
        scroll.pack(side="right", fill="y")
        self._canvas.configure(yscrollcommand=scroll.set)
        self._canvas.bind("<Button-1>", self._press)
        self._canvas.bind("<B1-Motion>", self._motion)
        self._canvas.bind("<ButtonRelease-1>", self._release)
        self._canvas.bind("<Button-3>", self._right_click)
        # Alt+mousewheel: nudge the selected note (was Shift+wheel; Shift
        # is now horizontal scroll per FL convention -- see scroll.py).
        self._canvas.bind("<Alt-MouseWheel>", self._nudge_wheel)
        self._canvas.bind("<Alt-Button-4>", lambda e: self._nudge(-1))
        self._canvas.bind("<Alt-Button-5>", lambda e: self._nudge(1))
        # Wheel scrolling (FL conventions): wheel = vertical (pitch),
        # Shift+wheel = horizontal (time), middle-drag = pan both.
        from .scroll import bind_wheel, bind_middle_pan
        bind_wheel(self._canvas,
                   xscroll=lambda n: self._canvas.xview_scroll(n, "units"),
                   yscroll=lambda n: self._canvas.yview_scroll(n, "units"))
        bind_middle_pan(self._canvas)
        # All rendering goes through the painter (state/interaction stay here).
        self._painter = PianoRollPainter(self._canvas)

    # -- data -----------------------------------------------------------

    def set_pattern(self, pattern, channel_id=None, names=None) -> None:
        self._pattern = pattern
        self._selected = None
        if names is not None:
            self._ch_combo["values"] = names
            self._ch_var.set(names[0] if names else "")
        if channel_id is None:
            channel_id = pattern.channels[0].id if pattern.channels else None
        self._channel_id = channel_id
        try:
            idx = next(i for i, c in enumerate(pattern.channels)
                       if c.id == channel_id)
        except StopIteration:
            idx = 0
            self._channel_id = pattern.channels[0].id if pattern.channels else None
        if names:
            self._ch_combo.current(idx)
        self._load_channel()
        self.refresh()

    def _on_channel_picked(self, _event=None) -> None:
        idx = self._ch_combo.current()
        if self._pattern is None or not (0 <= idx < len(self._pattern.channels)):
            return
        self._channel_id = self._pattern.channels[idx].id
        self._load_channel()
        self.refresh()

    def _load_channel(self) -> None:
        ch = self._find_channel()
        if ch is None:
            self._notes = []
        else:
            self._notes = [Note(start=n.start, length=n.length,
                                pitch=n.pitch, vel=n.vel, pan=n.pan) for n in ch.notes]
        self._selected = None

    def _find_channel(self):
        if self._pattern is None:
            return None
        for c in self._pattern.channels:
            if c.id == self._channel_id:
                return c
        return None

    @property
    def channel_id(self):
        return self._channel_id

    def refresh(self) -> None:
        self._draw()

    def set_playhead(self, step) -> None:
        if step == self._playhead_step:
            return
        self._playhead_step = step
        if step is None or self._pattern is None:
            self._painter.clear_playhead()
        else:
            self._painter.set_playhead(step, self._pattern.steps)

    # -- drawing (delegated to the painter) ---------------------------------

    def _draw(self) -> None:
        if self._pattern is None:
            self._canvas.delete("all")
            return
        ch = self._find_channel()
        self._painter.draw_all(
            self._notes, self._pattern.steps,
            ch.instrument if ch else "",
            self._playhead_step, self._selected,
            lane_mode=self._lane_mode())

    def _note_coords(self, i):
        """Canvas bbox for note i, or None if off-screen."""
        return self._painter.note_coords(self._notes[i])

    def _refresh_note_item(self, i):
        """Move/resize just the canvas item for note i (no full redraw)."""
        ch = self._find_channel()
        color = self._painter.color_for(ch.instrument if ch else "")
        self._painter.refresh_note(i, self._notes[i], color,
                                   draw_all_fallback=self._draw)
        # Also update the lane bar (note-attached, moves with the note).
        if self._lane_mode() == "Pan":
            self._painter.update_pan_bar(i, self._notes[i])
        else:
            self._painter.update_vel_bar(i, self._notes[i], color)

    def _painter_color(self) -> str:
        ch = self._find_channel()
        return self._painter.color_for(ch.instrument if ch else "")

    def _set_selection(self, idx) -> None:
        """Update the selection outline via the painter."""
        old = self._selected
        self._selected = idx
        self._painter.set_selection(old, idx)

    # -- interaction ------------------------------------------------------

    def _on_lane_mode(self) -> None:
        """Redraw the lower lane for the newly selected Vel/Pan mode."""
        self._vel_label.config(text="")
        self._draw()

    def _lane_mode(self) -> str:
        return self._lane_var.get()

    def _snap_inc(self) -> float:
        """Current snap increment in steps; 0.0 = snap off."""
        label = self._snap_var.get()
        return next(inc for name, inc in SNAP_STEPS if name == label)

    def _snap(self, s: float, event=None) -> float:
        """Snap a step position to the grid.

        Alt held during the drag (or snap Off) bypasses the grid:
        positions stay continuous, which the engine renders
        sample-accurately.
        """
        if event is not None and (event.state & ALT_MASK):
            # Alt held mid-drag: temporarily bypass snap (FL-style).
            return s
        return snap_step(s, self._snap_inc())

    def _nudge(self, direction: int) -> None:
        """Shift+mousewheel: move the selected note by one snap increment."""
        if self._selected is None or self._pattern is None:
            return
        n = self._notes[self._selected]
        inc = self._snap_inc() or MIN_NOTE_LEN
        n.start = max(0.0, min(self._pattern.steps - MIN_NOTE_LEN,
                               n.start + direction * inc))
        self._refresh_note_item(self._selected)
        self._commit_selection()

    def _nudge_wheel(self, event) -> None:
        self._nudge(-1 if event.delta > 0 else 1)

    def _commit_selection(self) -> None:
        """Commit the current local edits as one undo step."""
        if self._channel_id is not None:
            self._on_commit(self._channel_id,
                            [Note(start=n.start, length=n.length,
                                  pitch=n.pitch, vel=n.vel, pan=n.pan)
                             for n in self._notes])

    def _pos_at(self, event):
        """(step_float, pitch) under the cursor, or None."""
        x = self._canvas.canvasx(event.x)
        y = self._canvas.canvasy(event.y)
        s = (x - GUTTER) / COL_W
        row = int(y // ROW_H)
        pitch = PITCH_TOP - row
        if self._pattern is None or not (0 <= s < self._pattern.steps):
            return None
        if not (PITCH_BOTTOM <= pitch <= PITCH_TOP):
            return None
        return (s, pitch)

    def _vel_bar_at(self, event):
        """Index of the velocity bar under the cursor, or None."""
        x = self._canvas.canvasx(event.x)
        # Find the note whose bar is under x (bars centered on note.start).
        best, best_dist = None, float("inf")
        for i, n in enumerate(self._notes):
            cx = GUTTER + n.start * COL_W + COL_W / 2
            dist = abs(x - cx)
            if dist < COL_W / 2 and dist < best_dist:
                best, best_dist = i, dist
        return best

    def _drag_vel_lane(self, event) -> None:
        """Update velocity (Vel mode) or pan (Pan mode) from a lane drag."""
        if not self._grab or "idx" not in self._grab:
            return
        idx = self._grab["idx"]
        if idx >= len(self._notes):
            return
        y = self._canvas.canvasy(event.y)
        lane_top = PianoRollPainter.lane_y0()
        lane_bottom = lane_top + VEL_LANE_H - 8
        n = self._notes[idx]
        color = self._painter_color()
        if self._lane_mode() == "Pan":
            # Pan: 0.0 (hard left) at top, 0.5 center, 1.0 (hard right)
            # at bottom. Bar grows bidirectionally from the center line.
            pan = (y - lane_top - 12) / (lane_bottom - lane_top - 12)
            pan = max(0.0, min(1.0, pan))
            if abs(n.pan - pan) > 0.001:
                n.pan = pan
                self._painter.update_pan_bar(idx, n)
                self._vel_label.config(text=f"Pan {pan_label(pan)}")
        else:
            # Velocity: 1.0 at top, 0.0 at bottom.
            vel = 1.0 - (y - lane_top - 12) / (lane_bottom - lane_top - 12)
            vel = max(0.0, min(1.0, vel))
            if abs(n.vel - vel) > 0.001:
                n.vel = vel
                # Dirty-region: update the bar and the note shading.
                self._painter.update_vel_bar(idx, n, color)
                self._painter.update_note_shading(idx, n, color)
                self._vel_label.config(text=f"Vel {vel:.2f}")

    def _note_at(self, s: float, pitch: int):
        """Index of the note under (s, pitch), latest start wins."""
        best, best_start = None, -1.0
        for i, n in enumerate(self._notes):
            if n.pitch == pitch and n.start <= s < n.start + n.length:
                if n.start >= best_start:
                    best, best_start = i, n.start
        return best

    def _press(self, event) -> None:
        # Check velocity lane first (below the note grid).
        cy = self._canvas.canvasy(event.y)
        lane_top = PianoRollPainter.lane_y0()
        if cy >= lane_top:
            # Click/drag in velocity lane: find the bar under the cursor.
            idx = self._vel_bar_at(event)
            if idx is not None:
                self._gesture = "vel_lane"
                self._grab = {"idx": idx}
                self._set_selection(idx)
                # Set velocity from click Y immediately.
                self._drag_vel_lane(event)
            return
        pos = self._pos_at(event)
        if pos is None:
            return
        s, pitch = pos
        idx = self._note_at(s, pitch)
        x = self._canvas.canvasx(event.x)
        if idx is not None and (event.state & ALT_MASK):
            # Alt+drag on a note: adjust its velocity (Vel lane mode)
            # or its pan (Pan lane mode) - FL's Alt+wheel property edit.
            self._gesture = "velocity"
            self._grab = {"idx": idx}
            if self._lane_mode() == "Pan":
                self._vel_anchor = (event.y_root, self._notes[idx].pan)
                self._vel_label.config(
                    text=f"Pan {pan_label(self._notes[idx].pan)}")
            else:
                self._vel_anchor = (event.y_root, self._notes[idx].vel)
                self._vel_label.config(text=f"Vel {self._notes[idx].vel:.2f}")
        elif idx is not None:
            n = self._notes[idx]
            edge_x = GUTTER + (n.start + n.length) * COL_W
            self._set_selection(idx)
            if abs(x - edge_x) <= RESIZE_PX:
                self._gesture = "resize"
                self._grab = {"idx": idx}
            else:
                self._gesture = "move"
                self._grab = {"idx": idx, "ds": s - n.start,
                              "dpitch": pitch - n.pitch}
            self._draw()
        else:
            # Paint a 1-step note; dragging paints more.
            self._gesture = "paint"
            self._grab = {"painted": set()}
            self._paint_cell(s, pitch, event)
        self._canvas.focus_set()

    def _paint_cell(self, s: float, pitch: int, event=None) -> None:
        s = self._snap(s, event)
        if self._snap_inc() > 0.0:
            key = (round(s / self._snap_inc()), pitch)
            start = key[0] * self._snap_inc()
        else:
            # Free placement: dedup at 1/128-note granularity so a drag
            # paints a musical run, not a note per pixel.
            key = (round(s / MIN_NOTE_LEN), pitch)
            start = s
        if key in self._grab["painted"]:
            return
        self._grab["painted"].add(key)
        if self._note_at(start + MIN_NOTE_LEN / 2, pitch) is None:
            self._notes.append(Note(start=float(start), length=1.0,
                                    pitch=pitch, vel=0.8, pan=0.5))
            self._draw()

    def _motion(self, event) -> None:
        if self._gesture is None or self._pattern is None:
            return
        n_steps = self._pattern.steps
        if self._gesture == "velocity":
            idx = self._grab["idx"]
            y0, v0 = self._vel_anchor
            if self._lane_mode() == "Pan":
                pan = max(0.0, min(1.0, v0 + (event.y_root - y0) / 150.0))
                self._notes[idx].pan = pan
                self._vel_label.config(text=f"Pan {pan_label(pan)}")
            else:
                vel = max(0.0, min(1.0, v0 + (y0 - event.y_root) / 150.0))
                self._notes[idx].vel = vel
                self._vel_label.config(text=f"Vel {vel:.2f}")
            self._refresh_note_item(idx)
            return
        if self._gesture == "vel_lane":
            self._drag_vel_lane(event)
            return
        pos = self._pos_at(event)
        if pos is None:
            return
        s, pitch = pos
        if self._gesture == "paint":
            self._paint_cell(s, pitch, event)
            return
        idx = self._grab["idx"]
        n = self._notes[idx]
        if self._gesture == "move":
            raw = s - self._grab["ds"]
            n.start = float(max(0.0, min(n_steps - MIN_NOTE_LEN,
                                         self._snap(raw, event))))
            n.pitch = max(PITCH_BOTTOM, min(PITCH_TOP, pitch - self._grab["dpitch"]))
            self._refresh_note_item(idx)
        elif self._gesture == "resize":
            # Length snaps to the grid so the note covers the snapped
            # position under the cursor; Alt bypasses for free lengths.
            raw = s - n.start
            snapped = self._snap(raw, event)
            n.length = float(max(MIN_NOTE_LEN, snapped))
            self._refresh_note_item(idx)

    def _release(self, _event=None) -> None:
        if self._gesture is not None:
            self._commit_selection()
        self._gesture = None
        self._grab = None
        self._vel_anchor = None
        self._vel_label.config(text="")

    def _right_click(self, event) -> None:
        pos = self._pos_at(event)
        if pos is None:
            return
        s, pitch = pos
        idx = self._note_at(s, pitch)
        if idx is not None and self._channel_id is not None:
            del self._notes[idx]
            self._set_selection(None)
            self._draw()
            self._on_commit(self._channel_id,
                            [Note(start=n.start, length=n.length,
                                  pitch=n.pitch, vel=n.vel, pan=n.pan)
                             for n in self._notes])
