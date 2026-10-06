"""Mouse-wheel scrolling shared by all Pulsegrid widgets.

FL Studio conventions (verified against the official manual):
- Mouse wheel = vertical scroll.
- Shift + mouse wheel = horizontal scroll.
- Middle-mouse drag = pan vertically and horizontally at once
  (documented for both the Piano roll and the Playlist).

Platform notes: Windows/macOS deliver <MouseWheel> with event.delta
(multiples of 120); Linux/X11 delivers <Button-4> (up) / <Button-5>
(down). Shift+wheel on Linux is <Shift-Button-4/5>.
"""

# How many canvas units one wheel notch scrolls.
_WHEEL_UNITS = 3


def _scroll_units(widget, xscroll, yscroll, dx_units, dy_units):
    if dx_units and xscroll is not None:
        xscroll(dx_units)
    if dy_units and yscroll is not None:
        yscroll(dy_units)
    return "break"


def bind_wheel(widget, *, xscroll=None, yscroll=None, wheel_is_horizontal=False):
    """Bind wheel gestures on `widget`.

    `xscroll`/`yscroll`: callables taking a signed unit count, e.g.
    `lambda n: canvas.xview_scroll(n, "units")`. Either may be None when
    the widget has no such axis.

    FL convention: wheel = vertical, Shift+wheel = horizontal. Set
    `wheel_is_horizontal` for timeline widgets whose only scrollable
    axis is horizontal (the plain wheel then scrolls that axis).
    """
    def wheel(event):
        steps = 1
        delta = getattr(event, "delta", 0)
        if delta:
            steps = max(1, abs(delta) // 120)
            if delta > 0:
                steps = -steps
        if wheel_is_horizontal:
            return _scroll_units(widget, xscroll, yscroll,
                                 steps * _WHEEL_UNITS, 0)
        # Plain wheel = vertical (FL), Shift+wheel = horizontal (FL).
        if event.state & 0x1:  # Shift mask
            return _scroll_units(widget, xscroll, yscroll, steps * _WHEEL_UNITS, 0)
        return _scroll_units(widget, xscroll, yscroll, 0, steps * _WHEEL_UNITS)

    def wheel_up(event):
        if wheel_is_horizontal or event.state & 0x1:
            return _scroll_units(widget, xscroll, yscroll, -_WHEEL_UNITS, 0)
        return _scroll_units(widget, xscroll, yscroll, 0, -_WHEEL_UNITS)

    def wheel_down(event):
        if wheel_is_horizontal or event.state & 0x1:
            return _scroll_units(widget, xscroll, yscroll, _WHEEL_UNITS, 0)
        return _scroll_units(widget, xscroll, yscroll, 0, _WHEEL_UNITS)

    widget.bind("<MouseWheel>", wheel, add="+")
    widget.bind("<Button-4>", wheel_up, add="+")
    widget.bind("<Button-5>", wheel_down, add="+")


def bind_middle_pan(canvas):
    """Middle-mouse drag pans a canvas on both axes (FL convention)."""
    state = {"x": 0, "y": 0}

    def press(event):
        state["x"], state["y"] = event.x, event.y

    def drag(event):
        canvas.xview_scroll(int((state["x"] - event.x) / 2), "units")
        canvas.yview_scroll(int((state["y"] - event.y) / 2), "units")
        state["x"], state["y"] = event.x, event.y

    canvas.bind("<Button-2>", press, add="+")
    canvas.bind("<B2-Motion>", drag, add="+")
