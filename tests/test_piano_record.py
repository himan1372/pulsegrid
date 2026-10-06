"""Song-mode piano record / auto-place (v0.42.0 fixes).

Regression tests for the bug Philip hit on the Linux build (v0.19.0 -
v0.41.0): every recorded take was silently discarded. ``_auto_place_capture``
built the take channel with ``instrument="keys"``, which is not in
``INSTRUMENTS``, so ``Project.validate()`` raised inside ``_structural_edit``,
the pre-record project was restored, and an "Edit failed" dialog appeared.
The user pressed Stop, saw the error, then found Start did nothing useful.

Also covers the v0.42.0 transport-safety hardening in the same flow:
stop() cancels a pending count-in (a stale ``root.after`` must never start
playback after the user pressed Stop), and a take that fails to place can
never break stop() itself.

Widget tests need an X11 display (run under xvfb-run) and skip otherwise.
"""

import os
import time

import pytest

from daw.live_capture import CapturedNote

needs_display = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="record flow tests need an X11 display (run under xvfb-run)",
)


@pytest.fixture
def app():
    import tkinter as tk

    from daw.ui.app import PulsegridApp

    root = tk.Tk()
    try:
        a = PulsegridApp(root)
        root.update()
        yield a
    finally:
        root.destroy()


def _pump(root, seconds):
    deadline = time.time() + seconds
    while time.time() < deadline:
        root.update()
        time.sleep(0.05)


def _notes():
    return [
        CapturedNote(pitch=60, velocity=0.9, start_beat=4.0, end_beat=4.5),
        CapturedNote(pitch=64, velocity=0.8, start_beat=4.5, end_beat=5.0),
    ]


@needs_display
def test_auto_place_creates_audible_take(app):
    """Recorded notes become a new Pattern + Clip with a VALID instrument."""
    root = app.root
    app._capture.notes = _notes()
    before = len(app.project.patterns)

    app._auto_place_capture(4.0)
    root.update()

    assert len(app.project.patterns) == before + 1
    pat = app.project.patterns[-1]
    assert pat.name.startswith("Recorded take")
    ch = pat.channels[0]
    # The v0.41.0 bug: instrument="keys" is invalid, so validation failed
    # and the take was discarded. "lead" is the pitched voice -> audible.
    assert ch.instrument == "lead"
    assert len(ch.notes) == 2
    assert sorted(n.pitch for n in ch.notes) == [60, 64]
    # Clip placed at T0 on the first track (FL song-mode analog).
    clip = app.project.tracks[0].clips[-1]
    assert clip.pattern_id == pat.id
    assert clip.start_beat == 4
    # The whole project validates -- the old failure mode is gone.
    app.project.validate()
    # One undo step removes the take.
    app.undo.undo()
    root.update()
    assert len(app.project.patterns) == before


@needs_display
def test_auto_place_drops_notes_before_t0(app):
    """Notes played during the count-in (before T0) are not placed."""
    app._capture.notes = [
        CapturedNote(pitch=60, velocity=0.9, start_beat=3.5, end_beat=3.75),
    ]
    before = len(app.project.patterns)
    app._auto_place_capture(4.0)
    assert len(app.project.patterns) == before


@needs_display
def test_auto_place_empty_capture_is_noop(app):
    before = len(app.project.patterns)
    app._auto_place_capture(0.0)
    assert len(app.project.patterns) == before


@needs_display
def test_stop_cancels_pending_count_in(app):
    """Stop during the 1-bar count-in: no phantom playback starts later."""
    root = app.root
    app.transport.set_rec_armed(True)
    app._start_count_in()
    assert app._count_in_after_id is not None

    app.stop()
    root.update()
    assert app._count_in_after_id is None

    # Pump well past the count-in window (1 bar at 120bpm = 2s).
    _pump(root, 2.6)
    assert not app.engine.is_playing()


@needs_display
def test_failed_take_cannot_break_stop(app):
    """Even if auto-place blows up, stop() still stops cleanly."""
    root = app.root

    def boom(_t0):
        raise RuntimeError("simulated take failure")

    app._auto_place_capture = boom
    app._capture.notes = _notes()
    app._record_t0 = 0.0
    app.stop()  # must not raise
    root.update()
    assert app._record_t0 is None
    assert not app.engine.is_playing()


@needs_display
def test_record_flow_end_to_end(app):
    """Philip's scenario: record a take, stop, press Start -> plays."""
    root = app.root
    app.transport.set_rec_armed(True)

    app.toggle_play()
    # Count-in is 1 bar at 120bpm = 2s; pump past it.
    _pump(root, 2.6)
    assert app.engine.is_playing()

    # Perform on the piano.
    app._piano_note_on(60, 0.9)
    time.sleep(0.05)
    app._piano_note_off(60)
    app._piano_note_on(64, 0.9)
    time.sleep(0.05)
    app._piano_note_off(64)
    assert len(app._capture.notes) == 2

    # Stop -> take auto-placed as a new pattern + clip.
    before = len(app.project.patterns)
    app.stop()
    root.update()
    assert len(app.project.patterns) == before + 1
    assert app.project.patterns[-1].channels[0].instrument == "lead"

    # Disarm and press Start again: transport runs and progresses.
    app.transport.set_rec_armed(False)
    app.toggle_play()
    _pump(root, 0.6)
    assert app.engine.is_playing()
    p1 = app.engine.position_beats()
    time.sleep(0.4)
    p2 = app.engine.position_beats()
    assert p2 > p1

    app.stop()
    root.update()
    assert not app.engine.is_playing()
