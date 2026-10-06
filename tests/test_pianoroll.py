"""Sub-step timing (v0.28.0): snap math + Piano Roll gesture probes.

Pure snap math runs anywhere; widget gesture probes need an X11 display
(run under xvfb-run) and skip otherwise.
"""

import os

import pytest

from daw.ui.pianoroll import snap_step, SNAP_STEPS, MIN_NOTE_LEN

needs_display = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="piano roll gesture probes need an X11 display (run under xvfb-run)",
)


# -- pure snap math -----------------------------------------------------------


def test_snap_step_round_half_up():
    # Python's round() is banker's; snap must be musician-friendly.
    assert snap_step(2.5, 1.0) == 3.0
    assert snap_step(3.5, 1.0) == 4.0
    assert snap_step(2.3, 1.0) == 2.0
    assert snap_step(2.74, 0.5) == 2.5
    assert snap_step(2.76, 0.5) == 3.0


def test_snap_step_off_is_identity():
    assert snap_step(2.345678, 0.0) == 2.345678
    assert snap_step(0.0, 0.0) == 0.0


def test_snap_step_fine_grids():
    assert snap_step(0.06, 0.125) == 0.0
    assert snap_step(0.07, 0.125) == 0.125
    assert snap_step(1.13, 0.25) == 1.25


def test_snap_steps_cover_fl_style_values():
    labels = [label for label, _ in SNAP_STEPS]
    assert labels == ["1/16", "1/32", "1/64", "1/128", "Off"]
    incs = [inc for _, inc in SNAP_STEPS]
    assert incs == [1.0, 0.5, 0.25, 0.125, 0.0]
    assert MIN_NOTE_LEN == 0.125


# -- fractional notes validate and serialize (no format change) --------------


def test_fractional_note_validates_and_round_trips():
    from daw.project import Note
    n = Note(start=2.5, length=0.5, pitch=69, vel=0.9)
    n.validate(16)
    d = n.to_dict()
    n2 = Note.from_dict(d)
    n2.validate(16)
    assert n2.start == 2.5 and n2.length == 0.5


# -- headless gesture probes --------------------------------------------------


@needs_display
def test_paint_honors_snap_grid():
    """Click at step 2.7 with 1/32 snap -> note lands on 2.5."""
    import tkinter as tk
    from types import SimpleNamespace
    from daw.project import Pattern, Channel
    from daw.ui.pianoroll import PianoRoll, GUTTER, COL_W
    from daw.ui.pianoroll_painter import ROW_H, PITCH_TOP
    root = tk.Tk()
    root.withdraw()
    try:
        pat = Pattern(id="p1", name="P", steps=16,
                      channels=[Channel(id="c1", name="C", instrument="lead",
                                        pitch=69, notes=[])])
        roll = PianoRoll(root, on_commit=lambda cid, notes: None)
        roll.set_pattern(pat, channel_id="c1", names=["C"])
        roll._snap_var.set("1/32")  # 0.5-step grid

        def ev_at(step):
            return SimpleNamespace(x=GUTTER + step * COL_W + 1,
                                   y=(PITCH_TOP - 69) * ROW_H + ROW_H // 2,
                                   state=0, x_root=0, y_root=0)
        roll._press(ev_at(2.7))
        roll._release(ev_at(2.7))
        assert len(roll._notes) == 1
        assert roll._notes[0].start == 2.5
    finally:
        root.destroy()


@needs_display
def test_paint_snap_off_places_freely():
    """Click at step 2.7 with snap Off -> note keeps its free position."""
    import tkinter as tk
    from types import SimpleNamespace
    from daw.project import Pattern, Channel
    from daw.ui.pianoroll import PianoRoll, GUTTER, COL_W
    from daw.ui.pianoroll_painter import ROW_H, PITCH_TOP
    root = tk.Tk()
    root.withdraw()
    try:
        pat = Pattern(id="p1", name="P", steps=16,
                      channels=[Channel(id="c1", name="C", instrument="lead",
                                        pitch=69, notes=[])])
        roll = PianoRoll(root, on_commit=lambda cid, notes: None)
        roll.set_pattern(pat, channel_id="c1", names=["C"])
        roll._snap_var.set("Off")

        def ev_at(step):
            return SimpleNamespace(x=GUTTER + step * COL_W + 1,
                                   y=(PITCH_TOP - 69) * ROW_H + ROW_H // 2,
                                   state=0, x_root=0, y_root=0)
        roll._press(ev_at(2.7))
        roll._release(ev_at(2.7))
        assert len(roll._notes) == 1
        assert roll._notes[0].start == pytest.approx(2.7, abs=0.05)
        assert roll._notes[0].start != 2.5
    finally:
        root.destroy()


@needs_display
def test_alt_mid_drag_bypasses_snap():
    """FL-style: hold Alt during a move drag for free positioning."""
    import tkinter as tk
    from types import SimpleNamespace
    from daw.project import Pattern, Channel, Note
    from daw.ui.pianoroll import PianoRoll, GUTTER, COL_W, ALT_MASK
    from daw.ui.pianoroll_painter import ROW_H, PITCH_TOP
    root = tk.Tk()
    root.withdraw()
    try:
        pat = Pattern(id="p1", name="P", steps=16,
                      channels=[Channel(id="c1", name="C", instrument="lead",
                                        pitch=69,
                                        notes=[Note(start=4.0, length=1.0,
                                                    pitch=69, vel=0.9)])])
        roll = PianoRoll(root, on_commit=lambda cid, notes: None)
        roll.set_pattern(pat, channel_id="c1", names=["C"])
        roll._snap_var.set("1/16")  # whole-step grid

        def ev_at(step, state=0):
            return SimpleNamespace(x=GUTTER + step * COL_W + 1,
                                   y=(PITCH_TOP - 69) * ROW_H + ROW_H // 2,
                                   state=state, x_root=0, y_root=0)
        # Press on the note body (no Alt -> move gesture, not velocity).
        roll._press(ev_at(4.5))
        assert roll._gesture == "move"
        # Drag to 6.7 without Alt -> snaps to 6.0 (grab offset ~0.5).
        roll._motion(ev_at(6.7))
        assert roll._notes[0].start == 6.0
        # Same drag with Alt held -> free (unsnapped) position. Tk
        # rounds event coords to integer pixels, so compute the
        # expected raw value the same way the widget sees it.
        px_press = round(GUTTER + 4.5 * COL_W + 1)
        px_motion = round(GUTTER + 6.7 * COL_W + 1)
        s_press = (px_press - GUTTER) / COL_W
        s_motion = (px_motion - GUTTER) / COL_W
        expected = s_motion - (s_press - 4.0)
        roll._motion(ev_at(6.7, ALT_MASK))
        assert roll._notes[0].start == pytest.approx(expected, abs=1e-9)
        # ...and it is genuinely off-grid, not snapped to 6.0.
        assert abs(roll._notes[0].start - 6.0) > 0.05
        roll._release(ev_at(6.7, ALT_MASK))
    finally:
        root.destroy()


@needs_display
def test_nudge_moves_selected_note_by_snap():
    import tkinter as tk
    from daw.project import Pattern, Channel, Note
    from daw.ui.pianoroll import PianoRoll
    root = tk.Tk()
    root.withdraw()
    try:
        committed = []
        pat = Pattern(id="p1", name="P", steps=16,
                      channels=[Channel(id="c1", name="C", instrument="lead",
                                        pitch=69,
                                        notes=[Note(start=4.0, length=1.0,
                                                    pitch=69, vel=0.9)])])
        roll = PianoRoll(root,
                         on_commit=lambda cid, notes: committed.append(notes))
        roll.pack()
        roll.set_pattern(pat, channel_id="c1", names=["C"])
        root.update()
        roll._snap_var.set("1/32")
        roll._set_selection(0)
        roll._nudge(1)
        assert roll._notes[0].start == 4.5
        roll._nudge(-1)
        assert roll._notes[0].start == 4.0
        assert len(committed) == 2  # each nudge is one undo step
    finally:
        root.destroy()


# -- v0.29.0 per-note pan lane -----------------------------------------------

def test_pan_label():
    from daw.ui.pianoroll import pan_label
    assert pan_label(0.5) == "C"
    assert pan_label(0.51) == "C"
    assert pan_label(0.0) == "L100"
    assert pan_label(1.0) == "R100"
    assert pan_label(0.25) == "L50"
    assert pan_label(0.75) == "R50"


def test_pan_bar_coords_bidirectional():
    from daw.project import Note
    from daw.ui.pianoroll_painter import PianoRollPainter, VEL_LANE_H
    lane_y0 = 1000.0
    cy = lane_y0 + VEL_LANE_H / 2
    # Center: minimal 2px tick at the center line.
    x0, y0, x1, y1 = PianoRollPainter.pan_bar_coords(
        Note(start=0, length=1, pitch=60, pan=0.5), lane_y0)
    assert y0 == cy and y1 - y0 == 2
    # Hard left: bar extends upward from center.
    x0, y0, x1, y1 = PianoRollPainter.pan_bar_coords(
        Note(start=0, length=1, pitch=60, pan=0.0), lane_y0)
    assert y1 == cy and y0 < cy
    # Hard right: bar extends downward from center.
    x0, y0, x1, y1 = PianoRollPainter.pan_bar_coords(
        Note(start=0, length=1, pitch=60, pan=1.0), lane_y0)
    assert y0 == cy and y1 > cy


@needs_display
def test_lane_toggle_and_pan_drag():
    """Vel/Pan toggle exists; dragging in the lane in Pan mode sets pan."""
    import tkinter as tk
    from types import SimpleNamespace
    from daw.project import Pattern, Channel, Note
    from daw.ui.pianoroll import PianoRoll, GUTTER, COL_W
    from daw.ui.pianoroll_painter import (ROW_H, PITCH_TOP, VEL_LANE_H,
                                          PianoRollPainter)
    root = tk.Tk()
    root.withdraw()
    try:
        pat = Pattern(id="p1", name="P", steps=16,
                      channels=[Channel(id="c1", name="C", instrument="lead",
                                        pitch=69,
                                        notes=[Note(start=4.0, length=1.0,
                                                    pitch=69, vel=0.9,
                                                    pan=0.5)])])
        committed = []
        roll = PianoRoll(root,
                         on_commit=lambda cid, notes: committed.append(notes))
        roll.set_pattern(pat, channel_id="c1", names=["C"])
        # Toggle exists with both modes.
        assert set(roll._lane_var.get() for _ in [0]) == {"Vel"}
        roll._lane_var.set("Pan")
        roll._on_lane_mode()
        assert roll._lane_mode() == "Pan"

        # Drag in the lane at the note's x: top of lane -> hard left.
        lane_top = PianoRollPainter.lane_y0()
        cx = GUTTER + 4.0 * COL_W + COL_W / 2
        cy = lane_top + VEL_LANE_H / 2
        ev = SimpleNamespace(x=cx, y=cy, state=0, x_root=0, y_root=0)
        roll._press(ev)  # press in lane -> vel_lane gesture
        assert roll._gesture == "vel_lane"
        top_ev = SimpleNamespace(x=cx, y=lane_top + 13, state=0,
                                 x_root=0, y_root=0)
        roll._motion(top_ev)
        assert roll._notes[0].pan == pytest.approx(0.0, abs=0.05)
        bot_ev = SimpleNamespace(x=cx, y=lane_top + VEL_LANE_H - 9, state=0,
                                 x_root=0, y_root=0)
        roll._motion(bot_ev)
        assert roll._notes[0].pan == pytest.approx(1.0, abs=0.05)
        roll._release(bot_ev)
        # One gesture = one undo step; pan survived the commit.
        assert len(committed) == 1
        assert committed[0][0].pan == pytest.approx(1.0, abs=0.05)
    finally:
        root.destroy()


@needs_display
def test_alt_drag_in_pan_mode_adjusts_pan():
    """Alt+drag on a note in Pan lane mode adjusts pan, not velocity."""
    import tkinter as tk
    from types import SimpleNamespace
    from daw.project import Pattern, Channel, Note
    from daw.ui.pianoroll import PianoRoll, GUTTER, COL_W, ALT_MASK
    from daw.ui.pianoroll_painter import ROW_H, PITCH_TOP
    root = tk.Tk()
    root.withdraw()
    try:
        pat = Pattern(id="p1", name="P", steps=16,
                      channels=[Channel(id="c1", name="C", instrument="lead",
                                        pitch=69,
                                        notes=[Note(start=4.0, length=1.0,
                                                    pitch=69, vel=0.9,
                                                    pan=0.5)])])
        roll = PianoRoll(root, on_commit=lambda cid, notes: None)
        roll.set_pattern(pat, channel_id="c1", names=["C"])
        roll._lane_var.set("Pan")

        def ev_at(step, state=0, y_root=500):
            return SimpleNamespace(x=GUTTER + step * COL_W + 1,
                                   y=(PITCH_TOP - 69) * ROW_H + ROW_H // 2,
                                   state=state, x_root=0, y_root=y_root)
        roll._press(ev_at(4.5, ALT_MASK))
        assert roll._gesture == "velocity"
        # Drag downward (y_root increases) -> pan increases (right).
        roll._motion(ev_at(4.5, ALT_MASK, y_root=650))
        assert roll._notes[0].pan == pytest.approx(1.0, abs=0.01)
        assert roll._notes[0].vel == 0.9  # velocity untouched
        roll._release(ev_at(4.5))
    finally:
        root.destroy()
