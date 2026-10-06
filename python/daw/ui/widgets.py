"""Reusable control grammar for the Pulsegrid UI.

One gesture language everywhere (the infographic's "repeated control
grammar"):
* Knob: drag vertically to change, Shift+drag for fine adjust,
  double-click to reset, mouse wheel to nudge. Commits on release.
* Tooltip: hover hints explaining any control.
"""

import math
import tkinter as tk


class Tooltip:
    """Hover hint for a widget. Usage: Tooltip(widget, "hint text")."""

    _active = None

    def __init__(self, widget, text: str, delay_ms: int = 500):
        self.widget = widget
        self.text = text
        self.delay_ms = delay_ms
        self._tip = None
        self._after = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")
        # If the widget dies while a tip is scheduled or visible, take
        # the tip with it -- an orphaned overrideredirect Toplevel is a
        # permanent on-screen artifact (Windows ghost-window reports).
        widget.bind("<Destroy>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._after = self.widget.after(self.delay_ms, self._show)

    def _cancel(self) -> None:
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except tk.TclError:
                pass
            self._after = None

    def _show(self) -> None:
        if Tooltip._active is not None:
            return
        try:
            # Widget gone (or never mapped): never create an orphan tip.
            if not self.widget.winfo_exists():
                return
            x = self.widget.winfo_rootx() + 12
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except tk.TclError:
            return
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(tip, text=self.text, justify="left",
                         background="#21262d", foreground="#e6edf3",
                         relief="solid", borderwidth=1,
                         font=("", 9), padx=8, pady=5)
        label.pack()
        self._tip = tip
        Tooltip._active = tip

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._tip is not None:
            try:
                self._tip.destroy()
            except tk.TclError:
                pass
            self._tip = None
            Tooltip._active = None


class Knob(tk.Canvas):
    """Rotary knob with one gesture language.

    Drag vertically to change; hold Shift for fine adjust; double-click
    resets to `default`; mouse wheel nudges. `on_change(value)` fires live
    while dragging, `on_commit(value)` once on release.
    """

    def __init__(self, master, min_value: float = -1.0, max_value: float = 1.0,
                 value: float = 0.0, default: float | None = None,
                 size: int = 48, on_change=None, on_commit=None,
                 format_value=None, **kwargs):
        super().__init__(master, width=size, height=size + 14,
                         highlightthickness=0, borderwidth=0, bg="#161b22", **kwargs)
        self.min_value = min_value
        self.max_value = max_value
        self.value = self._clamp(value)
        self.default = self._clamp(default if default is not None else value)
        self.size = size
        self.on_change = on_change
        self.on_commit = on_commit
        self.format_value = format_value or (lambda v: f"{v:.2f}")
        self._drag_y = None
        self._drag_value = 0.0
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._drag)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Double-Button-1>", self._reset)
        self.bind("<MouseWheel>", self._wheel)      # Windows/macOS
        self.bind("<Button-4>", lambda _e: self._nudge(1))   # Linux scroll up
        self.bind("<Button-5>", lambda _e: self._nudge(-1))  # Linux scroll down
        self._draw()

    def _clamp(self, v: float) -> float:
        return max(self.min_value, min(self.max_value, v))

    def set(self, value: float) -> None:
        self.value = self._clamp(value)
        self._draw()

    def get(self) -> float:
        return self.value

    # -- geometry ------------------------------------------------------

    def _angle(self) -> float:
        frac = ((self.value - self.min_value)
                / (self.max_value - self.min_value) if self.max_value > self.min_value else 0.5)
        return math.radians(135 + frac * 270)  # 7 o'clock -> 5 o'clock

    def _draw(self) -> None:
        self.delete("all")
        s = self.size
        cx, cy, r = s / 2, s / 2, s / 2 - 4
        # Track arc.
        self.create_arc(cx - r, cy - r, cx + r, cy + r,
                        start=135, extent=270, style="arc",
                        outline="#30363d", width=4)
        # Value arc.
        frac = ((self.value - self.min_value)
                / (self.max_value - self.min_value) if self.max_value > self.min_value else 0.5)
        self.create_arc(cx - r, cy - r, cx + r, cy + r,
                        start=135, extent=270 * frac, style="arc",
                        outline="#58a6ff", width=4)
        # Pointer.
        a = self._angle()
        x2, y2 = cx + math.cos(a) * (r - 2), cy - math.sin(a) * (r - 2)
        self.create_line(cx, cy, x2, y2, fill="#e6edf3", width=2)
        self.create_oval(cx - 3, cy - 3, cx + 3, cy + 3, fill="#e6edf3",
                         outline="")
        # Value readout.
        self.create_text(cx, s + 7, text=self.format_value(self.value),
                         fill="#8b949e", font=("", 8))

    # -- gestures -------------------------------------------------------

    def _press(self, event) -> None:
        self._drag_y = event.y
        self._drag_value = self.value
        self.focus_set()

    def _drag(self, event) -> None:
        if self._drag_y is None:
            return
        dy = self._drag_y - event.y
        span = self.max_value - self.min_value
        step = span / 150.0  # full range over ~150 px
        if event.state & 0x1:  # Shift held: fine adjust
            step /= 10.0
        self.value = self._clamp(self._drag_value + dy * step)
        self._draw()
        if self.on_change:
            self.on_change(self.value)

    def _release(self, _event=None) -> None:
        self._drag_y = None
        if self.on_commit:
            self.on_commit(self.value)

    def _reset(self, _event=None) -> None:
        self.value = self.default
        self._draw()
        if self.on_change:
            self.on_change(self.value)
        if self.on_commit:
            self.on_commit(self.value)

    def _nudge(self, direction: int) -> None:
        span = self.max_value - self.min_value
        self.value = self._clamp(self.value + direction * span / 100.0)
        self._draw()
        if self.on_change:
            self.on_change(self.value)
        if self.on_commit:
            self.on_commit(self.value)

    def _wheel(self, event) -> None:
        self._nudge(1 if event.delta > 0 else -1)
