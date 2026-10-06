"""Tests for the extracted Mixer meter renderer (research topic 29).

Ballistics/presentation tests are pure (no display). Renderer drawing
tests need an X11 display (run under xvfb-run), like the piano-roll
gesture probes.
"""

import os

import pytest

from daw.ui.mixer_render import (
    MeterPresentation,
    MeterRenderer,
    MeterState,
    MeterTheme,
)

needs_display = pytest.mark.skipif(
    not os.environ.get("DISPLAY"),
    reason="renderer drawing probes need an X11 display (run under xvfb-run)",
)


# -- presentation state (pure, no display) -----------------------------------

def test_meter_state_attack_is_instant():
    s = MeterState()
    pres = s.update(0.8)
    assert pres.level == pytest.approx(0.8)
    assert pres.peak_hold == pytest.approx(0.8)
    assert not pres.clipping


def test_meter_state_decay_is_exponential():
    s = MeterState()
    s.update(0.8)
    pres = s.update(0.0)
    assert pres.level == pytest.approx(0.8 * 0.85)


def test_meter_state_floor_snaps_to_zero():
    s = MeterState()
    s.update(0.0005)
    pres = s.update(0.0)
    assert pres.level == 0.0


def test_meter_state_clipping_flag():
    s = MeterState()
    pres = s.update(1.2)
    assert pres.level == pytest.approx(1.2)
    assert pres.clipping
    # Still over 0 dBFS after one decay tick (1.2 * 0.85 = 1.02).
    pres2 = s.update(0.0)
    assert pres2.clipping
    # Decays out of clipping after a few more ticks.
    for _ in range(5):
        pres2 = s.update(0.0)
    assert not pres2.clipping


def test_meter_state_peak_hold_dwell_then_release():
    theme = MeterTheme(hold_ticks=3, decay=0.5)
    s = MeterState(theme)
    s.update(0.8)
    # Within the dwell: hold stays pinned even as the level decays.
    for _ in range(3):
        pres = s.update(0.0)
        assert pres.peak_hold == pytest.approx(0.8)
    # Past the dwell: hold releases with the decay factor.
    pres = s.update(0.0)
    assert pres.peak_hold == pytest.approx(0.4)
    # A new louder peak re-pins the hold and resets the dwell.
    pres = s.update(0.9)
    assert pres.peak_hold == pytest.approx(0.9)


def test_meter_state_reset():
    s = MeterState()
    s.update(0.8)
    s.reset()
    pres = s.update(0.0)
    assert (pres.level, pres.peak_hold) == (0.0, 0.0)
    assert not pres.clipping


def test_meter_state_ignores_negative_peaks():
    s = MeterState()
    pres = s.update(-0.5)
    assert pres.level == 0.0


def test_theme_zone_colors():
    t = MeterTheme()
    assert t.zone_color(0.0) == "#3fb950"
    assert t.zone_color(0.69) == "#3fb950"
    assert t.zone_color(0.7) == "#d29922"
    assert t.zone_color(0.89) == "#d29922"
    assert t.zone_color(0.9) == "#f85149"
    assert t.zone_color(1.5) == "#f85149"


def test_presentation_defaults():
    p = MeterPresentation()
    assert (p.level, p.peak_hold, p.clipping) == (0.0, 0.0, False)


# -- renderer drawing (needs display) ----------------------------------------

import tkinter as tk  # noqa: E402


def _items_by_fill(canvas):
    out = {}
    for i in canvas.find_all():
        out.setdefault(canvas.itemcget(i, "fill"), []).append(
            tuple(canvas.coords(i)))
    return out


@needs_display
def test_renderer_vertical_geometry_and_zone_color():
    root = tk.Tk()
    root.withdraw()
    try:
        c = tk.Canvas(root, width=10, height=110)
        c.pack()
        r = MeterRenderer()
        assert r.render(c, MeterPresentation(level=0.5)) is True
        items = _items_by_fill(c)
        # Background + green bar + peak-hold marker (hold == level).
        assert "#0d1117" in items
        assert "#3fb950" in items
        bar = items["#3fb950"][0]
        assert bar[0] == pytest.approx(1) and bar[2] == pytest.approx(9)
        assert bar[1] == pytest.approx(110 - 55) and bar[3] == pytest.approx(110)
    finally:
        root.destroy()


@needs_display
def test_renderer_zone_colors_drawn():
    root = tk.Tk()
    root.withdraw()
    try:
        c = tk.Canvas(root, width=10, height=110)
        c.pack()
        r = MeterRenderer()
        r.render(c, MeterPresentation(level=0.8, peak_hold=0.0))
        assert "#d29922" in _items_by_fill(c)
        r.render(c, MeterPresentation(level=0.95, peak_hold=0.0))
        assert "#f85149" in _items_by_fill(c)
    finally:
        root.destroy()


@needs_display
def test_renderer_clip_indicator():
    root = tk.Tk()
    root.withdraw()
    try:
        c = tk.Canvas(root, width=10, height=110)
        c.pack()
        r = MeterRenderer()
        r.render(c, MeterPresentation(level=1.2, peak_hold=0.0,
                                      clipping=True))
        items = _items_by_fill(c)
        clips = [box for box in items["#f85149"]
                 if box[1] == pytest.approx(0)
                 and box[3] == pytest.approx(3)]
        assert clips, "clip LED expected at the top of the meter"
    finally:
        root.destroy()


@needs_display
def test_renderer_peak_hold_marker_position():
    root = tk.Tk()
    root.withdraw()
    try:
        c = tk.Canvas(root, width=10, height=110)
        c.pack()
        r = MeterRenderer()
        r.render(c, MeterPresentation(level=0.2, peak_hold=0.6))
        items = _items_by_fill(c)
        markers = items["#e6edf3"]
        assert len(markers) == 1
        y = 110 - int(110 * 0.6)
        assert markers[0][1] == pytest.approx(y)
        assert markers[0][3] == pytest.approx(y + 2)
    finally:
        root.destroy()


@needs_display
def test_renderer_dirty_skip_and_redraw():
    root = tk.Tk()
    root.withdraw()
    try:
        c = tk.Canvas(root, width=10, height=110)
        c.pack()
        r = MeterRenderer()
        pres = MeterPresentation(level=0.5, peak_hold=0.5)
        assert r.render(c, pres) is True
        before = c.find_all()
        # Identical presentation: skipped, canvas untouched.
        assert r.render(c, MeterPresentation(level=0.5,
                                             peak_hold=0.5)) is False
        assert c.find_all() == before
        # Visible change: redrawn.
        assert r.render(c, MeterPresentation(level=0.6,
                                             peak_hold=0.5)) is True
        # Sub-pixel change: skipped (rounds to the same dirty key).
        assert r.render(c, MeterPresentation(level=0.6004,
                                             peak_hold=0.5)) is False
    finally:
        root.destroy()


@needs_display
def test_renderer_horizontal_theme():
    root = tk.Tk()
    root.withdraw()
    try:
        c = tk.Canvas(root, width=200, height=14)
        c.pack()
        root.update_idletasks()
        theme = MeterTheme(orientation="horizontal", width=None, height=14)
        r = MeterRenderer(theme)
        r.render(c, MeterPresentation(level=0.5, peak_hold=0.0))
        items = _items_by_fill(c)
        bar = items["#3fb950"][0]
        # Bar extends in x from the left edge.
        assert bar[0] == pytest.approx(0)
        assert bar[2] == pytest.approx(100, abs=2)
        assert bar[3] == pytest.approx(14)
    finally:
        root.destroy()


@needs_display
def test_mixer_wires_peaks_through_extracted_renderer():
    """The Mixer owns interaction/scheduling; drawing goes through the
    extracted renderer and presentation state (no inline painting)."""
    from daw.ui.mixer import Mixer
    from daw.project import PlaylistTrack

    root = tk.Tk()
    root.withdraw()
    try:
        m = Mixer(root, on_volume=lambda *a: None, on_pan=lambda *a: None,
                  on_mute=lambda *a: None, on_add_effect=lambda *a: None,
                  on_effect_param=lambda *a: None,
                  on_remove_effect=lambda *a: None,
                  on_add_plugin=lambda *a: None,
                  on_edit_plugin=lambda *a: None,
                  on_open_gui=lambda *a: None,
                  on_set_generator=lambda *a: None,
                  on_edit_generator=lambda *a: None,
                  on_clear_generator=lambda *a: None,
                  on_open_generator_gui=lambda *a: None)
        m.pack()
        t = PlaylistTrack(id="t1", name="T1")
        m.set_project(type("P", (), {"tracks": [t]})())
        root.update_idletasks()
        assert isinstance(m._meter_renderer, MeterRenderer)
        w = m._strip_widgets["t1"]
        assert isinstance(w["meter_state"], MeterState)
        assert not hasattr(m, "_draw_meter"), \
            "inline meter painting must be gone from the Mixer"
        m.set_levels([0.75])
        root.update_idletasks()
        meter = w["meter"]
        fills = {meter.itemcget(i, "fill") for i in meter.find_all()}
        # 0.75 is in the warn zone.
        assert "#d29922" in fills
        # Silence decays the meter on the next ticks.
        for _ in range(40):
            m.set_levels([0.0])
        fills = {meter.itemcget(i, "fill") for i in meter.find_all()}
        assert "#d29922" not in fills
    finally:
        root.destroy()
