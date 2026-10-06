"""Plugin Picker: visual grid of built-in instruments and effects.

Built-in instruments and effects can be dragged onto tracks. Third-party
CLAP audio effects are hosted too -- add them from the Mixer's "Plug"
button, which scans your CLAP folders.
"""

import tkinter as tk
from tkinter import ttk

from ..project import FX_NAMES, INSTRUMENTS
from .browser import DRAG_THRESHOLD, INSTRUMENT_COLORS, INSTRUMENT_LABELS
from .widgets import Tooltip

EFFECT_COLORS = {
    "delay": "#79c0ff",
    "drive": "#ffa657",
    "filter": "#7ee787",
}


class PluginPicker(ttk.Frame):
    def __init__(self, master, dnd_start):
        """dnd_start(kind, payload, label, event): begin a drag."""
        super().__init__(master)
        self._dnd_start = dnd_start
        self._press = None  # (kind, payload, label, x, y)

        ttk.Label(self, text="Plugin Picker", font=("", 10, "bold"),
                  padding=(8, 4)).pack(anchor="w")
        ttk.Label(self, text="Built-in sounds -- drag onto the Channel Rack or Mixer.",
                  foreground="#8b949e", font=("", 9),
                  padding=(8, 0)).pack(anchor="w")
        ttk.Label(self, text="Third-party CLAP effects: Mixer -> Plug.",
                  foreground="#8b949e", font=("", 9),
                  padding=(8, 0)).pack(anchor="w")

        body = ttk.Frame(self)
        body.pack(fill="both", expand=True)

        self._grid(
            body, "Instruments",
            [(INSTRUMENT_LABELS[i], INSTRUMENT_COLORS[i], "instrument", i)
             for i in INSTRUMENTS],
            "Drag onto a Channel Rack row to change its sound",
        )
        self._grid(
            body, "Effects",
            [(FX_NAMES[k], EFFECT_COLORS.get(k, "#8b949e"), "effect", k)
             for k in FX_NAMES],
            "Drag onto a mixer strip to add this effect",
        )

    def _grid(self, parent, title, items, hint):
        frame = ttk.LabelFrame(parent, text=title, padding=6)
        frame.pack(fill="x", padx=8, pady=4)
        for col, (label, color, kind, payload) in enumerate(items):
            cell = ttk.Frame(frame, padding=8, relief="groove", borderwidth=1)
            cell.grid(row=0, column=col, padx=4, pady=2, sticky="nsew")
            frame.columnconfigure(col, weight=1)
            dot = tk.Canvas(cell, width=14, height=14, bg="#161b22",
                            highlightthickness=0)
            dot.pack(pady=(2, 4))
            dot.create_oval(1, 1, 13, 13, fill=color, outline="")
            ttk.Label(cell, text=label, font=("", 9)).pack()
            for target in [cell] + list(cell.winfo_children()):
                Tooltip(target, hint)
                target.bind(
                    "<ButtonPress-1>",
                    lambda e, k=kind, p=payload, t=label:
                        self._press_begin(k, p, t, e),
                    add="+")
                target.bind("<B1-Motion>", self._press_motion, add="+")

    # -- drag initiation (same press-then-drag grammar as the Browser) -----

    def _press_begin(self, kind, payload, label, event) -> None:
        self._press = (kind, payload, label, event.x_root, event.y_root)

    def _press_motion(self, event) -> None:
        if self._press is None:
            return
        kind, payload, label, x0, y0 = self._press
        if (abs(event.x_root - x0) < DRAG_THRESHOLD
                and abs(event.y_root - y0) < DRAG_THRESHOLD):
            return
        self._press = None
        self._dnd_start(kind, payload, label, event)
