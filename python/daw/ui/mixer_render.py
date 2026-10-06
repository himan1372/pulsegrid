"""Extracted Mixer meter renderer (research topic 29: Mixer painter extraction).

Architecture (brief sections 24/37/38):

    Audio core (Rust engine)
        |  peak data only -- no GUI knowledge
    Widget / controller (mixer.py, debug.py)
        |  interaction, layout, scheduling; builds presentation state
    MeterPresentation
        |  pure visual state: post-ballistics level, peak-hold position,
        |  clipping flag. Knows nothing about tkinter or the engine.
    MeterRenderer
        |  pure drawing: presentation -> tkinter Canvas. Never queries the
        |  engine or the widget tree. Owns geometry, colors, theme, and
        |  dirty-checking (redraws only on a visible change).

This is the "start with the Fader meter" extraction step (brief section 37).
The meter used to be drawn by Mixer._draw_meter with the ballistics inline
in Mixer.set_levels (widget-owned painting, brief section 5). Now:

  - ballistics (data -> visual value) live in MeterState.update,
  - geometry/colors (visual value -> pixels) live in MeterRenderer.render,
  - the Mixer only wires engine peaks to canvases.

The debug window's horizontal meters reuse the same renderer with a
different theme: one presentation model, multiple render targets (brief
section 38: MixerRendererQt / MixerRendererTest consuming the same model).
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass


@dataclass
class MeterTheme:
    """All visual constants for one meter style.

    `decay` is applied per update tick, so it belongs to the tick rate of
    the consumer (mixer ~33 ms polls use 0.85; the debug window ~66 ms
    polls use 0.92). `width=None` means "use the canvas's live width"
    (horizontal debug meters); otherwise the fixed pixel size.
    """

    orientation: str = "vertical"  # "vertical" | "horizontal"
    width: int | None = 10
    height: int = 110
    background: str = "#0d1117"
    ok_color: str = "#3fb950"
    warn_color: str = "#d29922"
    clip_color: str = "#f85149"
    warn_zone: float = 0.7
    clip_zone: float = 0.9
    peak_hold_color: str = "#e6edf3"
    decay: float = 0.85
    hold_ticks: int = 30  # peak-hold dwell before release (~1 s at 33 ms)

    def zone_color(self, level: float) -> str:
        if level >= self.clip_zone:
            return self.clip_color
        if level >= self.warn_zone:
            return self.warn_color
        return self.ok_color


@dataclass
class MeterPresentation:
    """Immutable-ish visual state for one meter (brief section 8's
    miniature presentation model: peak values already mapped to the
    0..1+ visual range, geometry left to the renderer)."""

    level: float = 0.0  # post-ballistics shown level (0..1+, 1.0 = 0 dBFS)
    peak_hold: float = 0.0  # persistent-peak marker position (0..1+)
    clipping: bool = False  # level >= 1.0


class MeterState:
    """Per-track meter presentation state (LMMS m_fPeakValue /
    m_persistentPeak equivalent).

    update() applies the ballistics -- instant attack, exponential decay,
    floor -- and the peak-hold dwell/release, returning a fresh
    MeterPresentation. Pure data transformation: no tkinter, no engine.
    """

    _FLOOR = 0.001

    def __init__(self, theme: MeterTheme | None = None) -> None:
        self.theme = theme or MeterTheme()
        self._shown = 0.0
        self._hold = 0.0
        self._hold_age = 0

    def update(self, raw: float) -> MeterPresentation:
        raw = max(0.0, float(raw))
        t = self.theme
        shown = raw if raw > self._shown else self._shown * t.decay
        if shown < self._FLOOR:
            shown = 0.0
        if raw >= self._hold:
            self._hold = raw
            self._hold_age = 0
        else:
            self._hold_age += 1
            if self._hold_age > t.hold_ticks:
                self._hold *= t.decay
                if self._hold < self._FLOOR:
                    self._hold = 0.0
        self._shown = shown
        return MeterPresentation(
            level=shown, peak_hold=self._hold, clipping=shown >= 1.0
        )

    def reset(self) -> None:
        self._shown = 0.0
        self._hold = 0.0
        self._hold_age = 0


class MeterRenderer:
    """Draws a MeterPresentation onto a tkinter Canvas.

    Owns geometry, colors, and dirty-checking. Never touches the audio
    engine, the project model, or widget state beyond the canvas it is
    given -- the renderer consumes presentation state only (brief
    section 28: no audio-engine queries inside the renderer).

    render() returns True when it redrew, False when the presentation
    was visually unchanged (dirty-flag optimization, brief sections
    35-36: redraw the meter, not the whole Mixer).
    """

    _EPS_DIGITS = 3  # ~0.1% -- below half a pixel on a 110 px meter

    def __init__(self, theme: MeterTheme | None = None) -> None:
        self.theme = theme or MeterTheme()
        # Canvas -> last rendered key. Weak keys so destroyed strips
        # don't pin canvases (strips are rebuilt on structural edits).
        self._last: weakref.WeakKeyDictionary = weakref.WeakKeyDictionary()

    def _key(self, pres: MeterPresentation) -> tuple:
        return (
            round(pres.level, self._EPS_DIGITS),
            round(pres.peak_hold, self._EPS_DIGITS),
            pres.clipping,
        )

    def render(self, canvas, pres: MeterPresentation) -> bool:
        key = self._key(pres)
        if self._last.get(canvas) == key:
            return False
        self._last[canvas] = key
        t = self.theme
        canvas.delete("all")
        if t.orientation == "vertical":
            self._render_vertical(canvas, pres)
        else:
            self._render_horizontal(canvas, pres)
        return True

    def _render_vertical(self, canvas, pres: MeterPresentation) -> None:
        t = self.theme
        w, h = t.width or 10, t.height
        canvas.create_rectangle(0, 0, w, h, fill=t.background, outline="")
        bar_h = int(h * min(1.0, pres.level))
        if bar_h > 0:
            y0 = h - bar_h
            canvas.create_rectangle(
                1, y0, w - 1, h, fill=t.zone_color(pres.level), outline=""
            )
        if pres.peak_hold > MeterState._FLOOR:
            y = h - int(h * min(1.0, pres.peak_hold))
            canvas.create_rectangle(
                0, y, w, y + 2, fill=t.peak_hold_color, outline=""
            )
        if pres.clipping:
            canvas.create_rectangle(0, 0, w, 3, fill=t.clip_color, outline="")

    def _render_horizontal(self, canvas, pres: MeterPresentation) -> None:
        t = self.theme
        try:
            w = canvas.winfo_width()
        except Exception:
            w = 0
        if w <= 1:
            w = 200
        h = t.height
        canvas.create_rectangle(0, 0, w, h, fill=t.background, outline="")
        bar_w = int(w * min(1.0, pres.level))
        if bar_w > 0:
            canvas.create_rectangle(
                0, 0, bar_w, h, fill=t.zone_color(pres.level), outline=""
            )
        if pres.peak_hold > MeterState._FLOOR:
            x = int(w * min(1.0, pres.peak_hold))
            canvas.create_rectangle(
                x, 0, x + 2, h, fill=t.peak_hold_color, outline=""
            )
        if pres.clipping:
            canvas.create_rectangle(
                w - 3, 0, w, h, fill=t.clip_color, outline=""
            )

