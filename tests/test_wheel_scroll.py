"""Tests for wheel scrolling (FL Studio conventions).

- Wheel = vertical scroll, Shift+wheel = horizontal scroll.
- Timeline widgets (playlist) use wheel_is_horizontal: the plain wheel
  scrolls the only scrollable axis.
- Middle-drag pans both axes.
"""
import os

import pytest

needs_display = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="scroll tests need an X11 display (run under xvfb-run)",
)


class FakeEvent:
    def __init__(self, delta=0, state=0):
        self.delta = delta
        self.state = state


@needs_display
def test_bind_wheel_plain_is_vertical_shift_is_horizontal():
    import tkinter as tk

    from daw.ui.scroll import bind_wheel

    root = tk.Tk()
    try:
        calls = []
        w = tk.Frame(root)
        w.pack()
        bind_wheel(w,
                   xscroll=lambda n: calls.append(("x", n)),
                   yscroll=lambda n: calls.append(("y", n)))
        root.update()
        # Linux path: Button-5 (down) -> vertical scroll down.
        w.event_generate("<Button-5>")
        root.update()
        assert ("y", 3) in calls, calls
        # Shift+Button-5 -> horizontal scroll.
        calls.clear()
        w.event_generate("<Shift-Button-5>")
        root.update()
        assert ("x", 3) in calls, calls
        # Windows path: <MouseWheel> with delta.
        calls.clear()
        w.event_generate("<MouseWheel>", delta=-120)
        root.update()
        assert ("y", 3) in calls, calls
    finally:
        root.destroy()


@needs_display
def test_bind_wheel_horizontal_primary():
    import tkinter as tk

    from daw.ui.scroll import bind_wheel

    root = tk.Tk()
    try:
        calls = []
        w = tk.Frame(root)
        w.pack()
        bind_wheel(w, xscroll=lambda n: calls.append(("x", n)),
                   wheel_is_horizontal=True)
        root.update()
        w.event_generate("<Button-5>")
        root.update()
        assert ("x", 3) in calls, calls
    finally:
        root.destroy()


@needs_display
def test_widgets_have_wheel_bindings():
    import tkinter as tk

    from daw.ui.app import PulsegridApp

    root = tk.Tk()
    try:
        app = PulsegridApp(root)
        root.update()
        # Every scrollable widget exposes the wheel bindings.
        for cv in list(app.playlist._canvases.values())[:1]:
            assert cv.bind("<MouseWheel>"), "playlist lane"
            assert cv.bind("<Button-4>"), "playlist lane"
        assert app.pianoroll._canvas.bind("<MouseWheel>"), "piano roll"
        assert app.mixer._canvas.bind("<MouseWheel>"), "mixer"
        assert app.browser._body_canvas.bind("<MouseWheel>"), "browser"
        assert app.sequencer.canvas.bind("<MouseWheel>"), "channel rack"
        # Piano roll: Alt+wheel nudges (Shift+wheel is now h-scroll).
        assert app.pianoroll._canvas.bind("<Alt-MouseWheel>"), "nudge"
    finally:
        root.destroy()
