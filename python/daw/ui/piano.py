"""Visual piano keyboard for live play + capture.

A tkinter Canvas piano (2 octaves, C4-C6) that highlights keys as they are
played via the typing keyboard. Also shows the capture buffer status and
provides Dump/Clear controls.
"""

import tkinter as tk
from tkinter import ttk

from ..live_capture import KEY_TO_SEMITONE, BASE_MIDI, QUANTIZE_GRIDS
from .widgets import Tooltip

WHITE_KEYS = [0, 2, 4, 5, 7, 9, 11]  # semitone offsets that are white keys
BLACK_KEYS = [1, 3, 6, 8, 10]


class PianoKeyboard(ttk.Frame):
    """Two-octave visual piano with capture controls."""

    def __init__(self, master, on_note_on, on_note_off, on_dump, on_clear,
                 get_quantize, on_quantize):
        """on_note_on(pitch, velocity)/on_note_off(pitch): live play callbacks.
        on_dump()/on_clear(): capture buffer controls.
        get_quantize()/on_quantize(grid): input quantize preference.
        """
        super().__init__(master)
        self._on_note_on = on_note_on
        self._on_note_off = on_note_off
        self._base_midi = BASE_MIDI
        self._held_keys = {}  # pitch -> canvas item id

        # Piano canvas: 2 octaves = 14 white keys.
        self._canvas = tk.Canvas(self, height=120, bg="#0d1117",
                                 highlightthickness=0, borderwidth=0)
        self._canvas.pack(fill="x", padx=8, pady=(8, 4))
        self._white_ids = {}  # pitch -> item id
        self._black_ids = {}
        self._draw_keys()
        self._canvas.bind("<Configure>", lambda _e: self._draw_keys())

        # Octave shift.
        oct_row = ttk.Frame(self)
        oct_row.pack(fill="x", padx=8, pady=2)
        ttk.Button(oct_row, text="< Oct",
                   command=lambda: self._shift_octave(-1)).pack(side="left")
        self._oct_label = ttk.Label(oct_row, text="C4-C6", width=10)
        self._oct_label.pack(side="left", padx=8)
        ttk.Button(oct_row, text="Oct >",
                   command=lambda: self._shift_octave(1)).pack(side="left")
        Tooltip(oct_row, "Shift the typing-keyboard octave (or press Z/X)")

        # Capture controls.
        cap = ttk.LabelFrame(self, text="Capture (score logger)", padding=8)
        cap.pack(fill="x", padx=8, pady=4)
        self._count_label = ttk.Label(cap, text="0 notes captured")
        self._count_label.pack(anchor="w")
        btn_row = ttk.Frame(cap)
        btn_row.pack(fill="x", pady=(4, 0))
        ttk.Button(btn_row, text="Dump to pattern",
                   command=on_dump).pack(side="left", padx=(0, 6))
        Tooltip(btn_row.winfo_children()[0],
                "Write captured notes into the current pattern")
        ttk.Button(btn_row, text="Clear",
                   command=on_clear).pack(side="left")
        # Quantize.
        q_row = ttk.Frame(cap)
        q_row.pack(fill="x", pady=(6, 0))
        ttk.Label(q_row, text="Input quantize:").pack(side="left")
        self._quant_var = tk.StringVar(value=get_quantize())
        q_combo = ttk.Combobox(q_row, textvariable=self._quant_var,
                               values=[label for label, _ in QUANTIZE_GRIDS],
                               width=8, state="readonly")
        q_combo.pack(side="left", padx=(6, 0))
        q_combo.bind("<<ComboboxSelected>>",
                     lambda _e: on_quantize(self._quant_var.get()))
        Tooltip(q_combo, "Snap captured notes to the grid on dump")

        hint = ttk.Label(
            self,
            text="Play: Z-M row = white keys, S/D/G/H/J = black keys\n"
                 "Q-U row = next octave. Click keys or use the keyboard.\n"
                 "Song-mode: arm REC + Play, perform, then Stop --\n"
                 "captured notes auto-place as a new clip at the start.",
            foreground="#8b949e", font=("", 8), justify="left")
        hint.pack(anchor="w", padx=8, pady=(0, 8))

    def _draw_keys(self):
        cv = self._canvas
        cv.delete("all")
        self._white_ids.clear()
        self._black_ids.clear()
        w = cv.winfo_width()
        if w < 50:
            w = 400
        h = 120
        n_white = 14
        ww = w / n_white
        # White keys.
        wi = 0
        for octv in range(2):
            for st in WHITE_KEYS:
                pitch = self._base_midi + octv * 12 + st
                x0 = wi * ww
                iid = cv.create_rectangle(x0 + 1, 2, x0 + ww - 1, h - 2,
                                          fill="#e6edf3", outline="#0d1117",
                                          tags=f"key-{pitch}")
                cv.tag_bind(iid, "<ButtonPress-1>",
                            lambda e, p=pitch: self._press(p))
                cv.tag_bind(iid, "<ButtonRelease-1>",
                            lambda e, p=pitch: self._release(p))
                self._white_ids[pitch] = iid
                # Label C keys.
                if st == 0:
                    cv.create_text(x0 + ww / 2, h - 12,
                                   text=f"C{4 + octv}", font=("", 8),
                                   fill="#0d1117")
                wi += 1
        # Black keys (drawn on top).
        wi = 0
        for octv in range(2):
            for st in WHITE_KEYS:
                pitch_w = self._base_midi + octv * 12 + st
                # Black key after this white key (except E/B).
                if st in (0, 2, 5, 7, 9):
                    pitch = pitch_w + 1
                    x = (wi + 1) * ww
                    bw = ww * 0.6
                    iid = cv.create_rectangle(
                        x - bw / 2, 2, x + bw / 2, h * 0.62,
                        fill="#161b22", outline="#0d1117",
                        tags=f"key-{pitch}")
                    cv.tag_bind(iid, "<ButtonPress-1>",
                                lambda e, p=pitch: self._press(p))
                    cv.tag_bind(iid, "<ButtonRelease-1>",
                                lambda e, p=pitch: self._release(p))
                    self._black_ids[pitch] = iid
                wi += 1

    def _shift_octave(self, delta: int):
        self._base_midi = max(12, min(96, self._base_midi + delta * 12))
        octv = self._base_midi // 12 - 1
        self._oct_label.configure(text=f"C{octv}-C{octv + 2}")
        self._draw_keys()

    def _press(self, pitch: int):
        self.highlight(pitch, True)
        self._on_note_on(pitch, 0.9)

    def _release(self, pitch: int):
        self.highlight(pitch, False)
        self._on_note_off(pitch)

    def highlight(self, pitch: int, on: bool):
        """Light up a key (called for typing-keyboard presses too)."""
        iid = self._white_ids.get(pitch) or self._black_ids.get(pitch)
        if iid is None:
            return
        is_white = pitch in self._white_ids
        if on:
            self._canvas.itemconfig(iid, fill="#f778ba")
            self._held_keys[pitch] = iid
        else:
            self._canvas.itemconfig(
                iid, fill="#e6edf3" if is_white else "#161b22")
            self._held_keys.pop(pitch, None)

    def set_count(self, n: int):
        self._count_label.configure(text=f"{n} note{'s' if n != 1 else ''} captured")

    def sync_quantize(self, label: str):
        self._quant_var.set(label)
