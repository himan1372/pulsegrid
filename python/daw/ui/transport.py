"""Transport bar: play/stop, tempo, position readout, backend status."""

import tkinter as tk
from tkinter import ttk
from typing import Callable


class TransportBar(ttk.Frame):
    def __init__(self, parent, on_play: Callable[[], None], on_stop: Callable[[], None],
                 on_tempo: Callable[[float], None],
                 on_toggle_browser: Callable[[], None] | None = None,
                 on_toggle_mixer: Callable[[], None] | None = None,
                 on_record: Callable[[], None] | None = None):
        super().__init__(parent, padding=(10, 8))
        self._on_play = on_play
        self._on_stop = on_stop
        self._on_tempo = on_tempo
        self._on_record = on_record

        self.play_btn = ttk.Button(self, text=">  Play", command=self._on_play, width=10)
        self.play_btn.pack(side="left", padx=(0, 6))
        self.stop_btn = ttk.Button(self, text="[]  Stop", command=self._on_stop,
                                   width=10, state="disabled")
        self.stop_btn.pack(side="left", padx=(0, 6))
        # Record arm (toggles capture-armed state; count-in before capture).
        self._rec_armed = False
        self.rec_btn = ttk.Button(self, text="O  Rec", width=10,
                                  command=self._toggle_rec)
        self.rec_btn.pack(side="left", padx=(0, 16))

        ttk.Label(self, text="Tempo").pack(side="left")
        self.tempo_var = tk.DoubleVar(value=128.0)
        self.tempo_spin = ttk.Spinbox(self, from_=40, to=240, increment=1,
                                      textvariable=self.tempo_var, width=6,
                                      command=self._tempo_changed)
        self.tempo_spin.pack(side="left", padx=(6, 4))
        ttk.Label(self, text="BPM").pack(side="left", padx=(0, 16))
        self.tempo_spin.bind("<Return>", lambda _e: self._tempo_changed())
        self.tempo_spin.bind("<FocusOut>", lambda _e: self._tempo_changed())

        ttk.Label(self, text="Position").pack(side="left")
        self.pos_var = tk.StringVar(value="Bar 1 | Beat 1.0")
        ttk.Label(self, textvariable=self.pos_var, width=20,
                  font=("TkFixedFont", 10)).pack(side="left", padx=(6, 16))

        ttk.Label(self, text="Audio").pack(side="left")
        self.backend_var = tk.StringVar(value="starting...")
        ttk.Label(self, textvariable=self.backend_var,
                  font=("", 9), foreground="#8b949e").pack(side="left", padx=(6, 0))

        # Window toggles: keep the main work areas one click away.
        if on_toggle_browser is not None:
            ttk.Button(self, text="Browser", width=9,
                       command=on_toggle_browser).pack(side="right", padx=(6, 0))
        if on_toggle_mixer is not None:
            ttk.Button(self, text="Mixer", width=9,
                       command=on_toggle_mixer).pack(side="right")

    def _tempo_changed(self) -> None:
        try:
            bpm = float(self.tempo_var.get())
        except (tk.TclError, ValueError):
            return
        self._on_tempo(bpm)

    def set_tempo(self, bpm: float) -> None:
        self.tempo_var.set(round(bpm, 1))

    def set_playing(self, playing: bool) -> None:
        self.play_btn.config(state="disabled" if playing else "normal")
        self.stop_btn.config(state="normal" if playing else "disabled")

    def _toggle_rec(self) -> None:
        self._rec_armed = not self._rec_armed
        self.rec_btn.configure(
            text="O  REC" if self._rec_armed else "O  Rec")
        if self._on_record is not None:
            self._on_record()

    def is_rec_armed(self) -> bool:
        return self._rec_armed

    def set_rec_armed(self, armed: bool) -> None:
        self._rec_armed = armed
        self.rec_btn.configure(text="O  REC" if armed else "O  Rec")

    def set_position(self, text: str) -> None:
        self.pos_var.set(text)

    def set_backend(self, text: str) -> None:
        self.backend_var.set(text)
