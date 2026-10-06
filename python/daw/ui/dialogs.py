"""Small dialogs: errors, about, shortcuts, audio clip properties."""

import tkinter as tk
from tkinter import ttk


def show_error(parent, title: str, message: str) -> None:
    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    win.grab_set()
    win.resizable(False, False)
    frame = ttk.Frame(win, padding=18)
    frame.pack(fill="both", expand=True)
    ttk.Label(frame, text="Something went wrong", font=("", 11, "bold")).pack(anchor="w")
    msg = tk.Text(frame, width=64, height=6, wrap="word", relief="flat")
    msg.pack(pady=(8, 12))
    msg.insert("1.0", message)
    msg.config(state="disabled")
    ttk.Button(frame, text="OK", command=win.destroy).pack(anchor="e")
    win.bind("<Escape>", lambda _e: win.destroy())
    win.bind("<Return>", lambda _e: win.destroy())


ABOUT_TEXT = """Pulsegrid 0.5.0 -- an original desktop music production app.

Python UI + Rust audio engine (PyO3 bridge).
Built as a learning project following the FL Studio Feature Atlas
as a product/architecture guide. All branding, UI, code, and
synthesis are original; no FL Studio assets or formats are used.
"""


def show_about(parent) -> None:
    win = tk.Toplevel(parent)
    win.title("About Pulsegrid")
    win.transient(parent)
    win.resizable(False, False)
    frame = ttk.Frame(win, padding=20)
    frame.pack()
    ttk.Label(frame, text="Pulsegrid", font=("", 16, "bold")).pack(anchor="w")
    ttk.Label(frame, text=ABOUT_TEXT, justify="left").pack(anchor="w", pady=(6, 12))
    ttk.Button(frame, text="Close", command=win.destroy).pack(anchor="e")


SHORTCUTS_TEXT = """Space            Play / Stop
Ctrl+N           New project
Ctrl+O           Open project
Ctrl+S           Save project
Ctrl+E           Export arrangement to WAV
Ctrl+B           Show/hide Browser
Ctrl+P           Show/hide Plugin Picker
Ctrl+M           Show/hide Mixer
Ctrl+Z           Undo
Ctrl+Y           Redo
Delete           Delete selected playlist clip
Click a step     Toggle note on/off (Channel Rack)
Double-click     Edit pattern (playlist clip / Browser)
Right-click      Context actions (clips, channels, patterns)
Drag             Move clips | assign instruments | place patterns | add effects
Alt+drag         Adjust note velocity (Piano Roll)
Shift+drag       Fine-adjust knob values
"""


def show_shortcuts(parent) -> None:
    win = tk.Toplevel(parent)
    win.title("Keyboard shortcuts")
    win.transient(parent)
    win.resizable(False, False)
    frame = ttk.Frame(win, padding=20)
    frame.pack()
    ttk.Label(frame, text="Keyboard shortcuts", font=("", 13, "bold")).pack(anchor="w")
    ttk.Label(frame, text=SHORTCUTS_TEXT, justify="left",
              font=("TkFixedFont", 10)).pack(anchor="w", pady=(6, 12))
    ttk.Button(frame, text="Close", command=win.destroy).pack(anchor="e")


QUICK_START_TEXT = """Make your first loop in two minutes:

1. FIND A SOUND -- The Browser (left) lists instruments,
   effects and patterns; the Plugin Picker tab shows them
   as a visual grid. Drag an instrument onto a channel in
   the Channel Rack to change its sound.

2. MAKE A BEAT -- Click steps in the Channel Rack, or open
   the Piano Roll tab to draw notes at exact pitches.
   The pitch box sets the default pitch for new notes.
   Alt+drag a note up or down to set its velocity
   (brighter notes play louder).

3. ARRANGE -- In the Playlist, click empty space to place
   clips of the current pattern (or drag patterns from
   the Browser). Drag clips to move them, right-click
   a clip for actions. The Snap selector quantizes
   placement to bars or individual beats.

4. MIX -- Open the Mixer (right): volume, pan, mute and
   insert effects (delay, drive, filter) per track.
   Drag effects from the Browser or Plugin Picker
   onto a mixer strip to add them.

5. EXPORT -- File > Export arrangement to WAV, or press
   Space to play your arrangement any time. Export shows
   a progress bar and stays cancellable; the UI keeps
   working while it renders.

Tips: hover any control for a hint, right-click for
context actions, and use View > UI scale if text looks
small. Your panel layout is remembered between sessions.
"""


def show_quick_start(parent) -> None:
    win = tk.Toplevel(parent)
    win.title("Quick start guide")
    win.transient(parent)
    win.resizable(False, False)
    frame = ttk.Frame(win, padding=20)
    frame.pack()
    ttk.Label(frame, text="Quick start guide", font=("", 13, "bold")).pack(anchor="w")
    text = tk.Text(frame, width=66, height=26, wrap="word", relief="flat",
                   font=("", 10))
    text.pack(pady=(6, 12))
    text.insert("1.0", QUICK_START_TEXT)
    text.config(state="disabled")
    ttk.Button(frame, text="Close", command=win.destroy).pack(anchor="e")
    win.bind("<Escape>", lambda _e: win.destroy())


class AudioClipDialog:
    """Per-instance audio clip properties (FL's Clip Properties panel).

    Modal dialog editing one AudioClip's playback properties. Returns a
    dict of the edited values on OK, or None on Cancel. All strings are
    ASCII (Ubuntu 20.04 Tk emoji/font crash lesson).
    """

    def __init__(self, parent, clip, sample_name: str):
        self._result = None
        win = tk.Toplevel(parent)
        self._win = win
        win.title(f"Audio clip: {sample_name}")
        win.transient(parent)
        win.grab_set()
        win.resizable(False, False)
        frame = ttk.Frame(win, padding=18)
        frame.pack(fill="both", expand=True)

        self._vars = {}
        row = 0

        def slider(label, key, lo, hi, fmt, default):
            nonlocal row
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w",
                                              pady=2)
            var = tk.DoubleVar(value=default)
            s = ttk.Scale(frame, from_=lo, to=hi, variable=var,
                          orient="horizontal", length=220)
            s.grid(row=row, column=1, padx=(8, 4), pady=2)
            val = ttk.Label(frame, text="", width=12)
            val.grid(row=row, column=2, sticky="w")
            def update(*_a, v=var, lbl=val, f=fmt):
                lbl.config(text=f(v.get()))
            var.trace_add("write", update)
            update()
            self._vars[key] = var
            row += 1

        def entry(label, key, default):
            nonlocal row
            ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w",
                                              pady=2)
            var = tk.StringVar(value=str(default))
            ttk.Entry(frame, textvariable=var, width=10).grid(
                row=row, column=1, sticky="w", padx=(8, 4), pady=2)
            ttk.Label(frame, text="beats").grid(row=row, column=2, sticky="w")
            self._vars[key] = var
            row += 1

        def check(label, key, default):
            nonlocal row
            var = tk.BooleanVar(value=default)
            ttk.Checkbutton(frame, text=label, variable=var).grid(
                row=row, column=0, columnspan=2, sticky="w", pady=2)
            self._vars[key] = var
            row += 1

        slider("Gain", "gain", 0.0, 2.0, lambda v: f"{v:.2f}", clip.gain)
        slider("Pan (L <- -> R)", "pan", 0.0, 1.0,
               lambda v: f"{'L' if v < 0.5 else 'R' if v > 0.5 else 'C'} {v:.2f}",
               clip.pan)
        slider("Pitch", "pitch_semitones", -48.0, 48.0,
               lambda v: f"{v:+.0f} st", clip.pitch_semitones)
        slider("Fine tune", "fine_cents", -100.0, 100.0,
               lambda v: f"{v:+.0f} ct", clip.fine_cents)
        entry("Start offset", "start_offset_beats", clip.start_offset_beats)
        entry("Length", "length_beats", clip.length_beats)
        check("Reverse", "reverse", clip.reverse)
        check("Mute clip", "muted", clip.muted)

        btns = ttk.Frame(frame)
        btns.grid(row=row, column=0, columnspan=3, pady=(12, 0), sticky="e")
        ttk.Button(btns, text="OK", command=self._ok).pack(side="left",
                                                          padx=(0, 8))
        ttk.Button(btns, text="Cancel",
                   command=win.destroy).pack(side="left")
        win.bind("<Escape>", lambda _e: win.destroy())
        win.bind("<Return>", lambda _e: self._ok())
        win.wait_window()

    def _ok(self):
        try:
            self._result = {
                "gain": float(self._vars["gain"].get()),
                "pan": float(self._vars["pan"].get()),
                "pitch_semitones": float(self._vars["pitch_semitones"].get()),
                "fine_cents": float(self._vars["fine_cents"].get()),
                "start_offset_beats": float(
                    self._vars["start_offset_beats"].get()),
                "length_beats": float(self._vars["length_beats"].get()),
                "reverse": bool(self._vars["reverse"].get()),
                "muted": bool(self._vars["muted"].get()),
            }
        except (ValueError, tk.TclError):
            self._result = None
        self._win.destroy()

    @property
    def result(self):
        return self._result


def edit_audio_clip(parent, clip, sample_name: str):
    """Show the clip properties dialog. Returns dict of values or None."""
    return AudioClipDialog(parent, clip, sample_name).result
