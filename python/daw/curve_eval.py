"""Automation curve evaluator (research topics 30, 36).

Pure-Python mirror of the Rust `AutoCurve::value_at` / `eval_segment`
plus `Lfo::apply` (engine/src/timeline.rs). The automation editor's
painter samples this to draw the true curve shape; it never reimplements
curve math itself (brief section 52: the renderer must not determine the
curve shape -- "Here are values. Draw them.").

Topic 36 additions:
- Per-node tangents (LMMS AutomationNode analog): a point's `in_tan` /
  `out_tan` (value per beat) may be None (AUTO -- derived from neighbors
  via Catmull-Rom, scaled by lane tension) or a float (LOCKED -- the
  user-defined tangent, used verbatim, never recalculated when neighbors
  move). Tangents shape only the "smooth" (cubic Hermite) evaluator.
- LFO layer (FL Automation Clip LFO analog): a separate modulation
  layer that never modifies the base spline. phase = (beat*speed) % 1,
  so realtime and offline render agree exactly.
  add:      final = base + level * wave(phase)
  multiply: final = base * (1 + level * wave(phase))

The two implementations must stay in sync: tests/test_curve_eval.py
checks this module against fixed vectors that match the Rust tests, so
a formula drift in either language fails loudly.
"""

from __future__ import annotations

import math

# Mode names must match Rust InterpMode::from_str exactly.
LINEAR = "linear"
SMOOTH = "smooth"
HOLD = "hold"
STAIRS = "stairs"
PULSE = "pulse"
WAVE = "wave"

MODES = (LINEAR, SMOOTH, HOLD, STAIRS, PULSE, WAVE)

# LFO shape/combine names must match Rust LfoShape/LfoCombine parsing.
LFO_SINE = "sine"
LFO_TRIANGLE = "triangle"
LFO_SAW = "saw"
LFO_PULSE = "pulse"
LFO_SHAPES = (LFO_SINE, LFO_TRIANGLE, LFO_SAW, LFO_PULSE)
LFO_ADD = "add"
LFO_MULTIPLY = "multiply"


def uses_tension(interp: str) -> bool:
    """Linear and Hold ignore tension (mirrors InterpMode::uses_tension)."""
    return interp not in (LINEAR, HOLD)


def _rust_round(x: float) -> int:
    """Rust f64::round(): half away from zero. Python's round() is
    banker's rounding, which diverges at .5 boundaries (e.g. 10.5 ->
    11 in Rust, 10 in Python). All our inputs are non-negative, so
    floor(x + 0.5) matches Rust exactly."""
    return int(math.floor(x + 0.5))


def norm_point(p):
    """(beat, value, in_tan, out_tan) from a tuple or an AutoPoint."""
    if hasattr(p, "beat"):
        return (float(p.beat), float(p.value), p.in_tan, p.out_tan)
    if len(p) == 2:
        return (float(p[0]), float(p[1]), None, None)
    return (float(p[0]), float(p[1]),
            None if p[2] is None else float(p[2]),
            None if p[3] is None else float(p[3]))


def _norm_point(p):
    return norm_point(p)


def auto_tangent(points, i: int, side: int, tension: float) -> float:
    """Automatic Catmull-Rom tangent (value per beat) for point `i`,
    scaled by lane tension.

    `side` 0 = outgoing tangent of point i, 1 = incoming tangent of
    point i. Mirrors the pre-topic-36 Smooth formula exactly, so lanes
    without locked tangents evaluate identically to before.

    Callers must respect the segment context: side 0 needs i+1 to
    exist (outgoing tangent of a segment start), side 1 needs i-1
    (incoming tangent of a segment end). The evaluator only ever asks
    for tangents that shape a real segment.
    """
    pts = [_norm_point(p) for p in points]
    b, v = pts[i][0], pts[i][1]
    n = len(pts)
    if side == 0:  # outgoing of i: uses i-1 (or i) and i+1
        pb, pv = (pts[i - 1][0], pts[i - 1][1]) if i > 0 else (b, v)
        nb, nv = pts[i + 1][0], pts[i + 1][1]
        return tension * (nv - pv) / max(1e-9, nb - pb)
    else:  # incoming of i: uses i-1 and i+1 (or i)
        pb, pv = pts[i - 1][0], pts[i - 1][1]
        nb, nv = (pts[i + 1][0], pts[i + 1][1]) if i + 1 < n else (b, v)
        return tension * (nv - pv) / max(1e-9, nb - pb)


def _auto_tangent(pts, i: int, side: int, tension: float) -> float:
    # pts already normalized here; delegate to the public helper.
    return auto_tangent(pts, i, side, tension)


def value_at(points, interp: str, tension: float, beat: float,
             base: float, lfo=None) -> float:
    """Value of the lane at `beat`. Mirrors Rust AutoCurve::value_at.

    `lfo` is an LfoSettings (or dict) or None; the LFO layer is applied
    after the base spline is evaluated (it never modifies the spline).
    """
    pts = [_norm_point(p) for p in points]
    if not pts or beat < pts[0][0]:
        val = base
    else:
        val = pts[-1][1]
        for i in range(len(pts) - 1):
            b0, v0 = pts[i][0], pts[i][1]
            b1 = pts[i + 1][0]
            if beat < b1:
                t = (beat - b0) / max(1e-9, b1 - b0)
                t = max(0.0, min(1.0, t))
                val = _eval(interp, pts, i, t, b0, v0,
                            pts[i + 1][0], pts[i + 1][1], tension)
                break
    return _apply_lfo(val, beat, lfo)


def _eval(interp: str, pts, i: int, t: float, b0: float, v0: float,
          b1: float, v1: float, tension: float) -> float:
    tension = max(0.0, min(1.0, tension))
    if interp == LINEAR:
        return v0 + (v1 - v0) * t
    if interp == HOLD:
        return v0
    if interp == SMOOTH:
        dt = max(1e-9, b1 - b0)
        # Locked tangents win; otherwise fall back to the automatic
        # Catmull-Rom tangent (LMMS locked-tangent semantics).
        out_tan = pts[i][3]
        in_tan = pts[i + 1][2]
        m0 = out_tan if out_tan is not None else _auto_tangent(
            pts, i, 0, tension)
        m1 = in_tan if in_tan is not None else _auto_tangent(
            pts, i + 1, 1, tension)
        t2 = t * t
        t3 = t2 * t
        return ((2.0 * t3 - 3.0 * t2 + 1.0) * v0
                + (t3 - 2.0 * t2 + t) * dt * m0
                + (-2.0 * t3 + 3.0 * t2) * v1
                + (t3 - t2) * dt * m1)
    if interp == STAIRS:
        steps = 2 + _rust_round(tension * 14.0)
        idx = min(int(math.floor(t * steps)), steps - 1)
        return v0 + (v1 - v0) * idx / (steps - 1)
    if interp == PULSE:
        cycles = 1.0 + _rust_round(tension * 7.0)
        return v0 if (t * cycles) % 1.0 < 0.5 else v1
    if interp == WAVE:
        cycles = 1.0 + _rust_round(tension * 7.0)
        ramp = v0 + (v1 - v0) * t
        return ramp + (v1 - v0) * 0.5 * math.sin(
            2.0 * math.pi * cycles * t)
    raise ValueError(f"unknown interp '{interp}'")


# -- LFO layer -----------------------------------------------------------


def _lfo_field(lfo, name: str, default):
    if lfo is None:
        return default
    if isinstance(lfo, dict):
        return lfo.get(name, default)
    return getattr(lfo, name, default)


def lfo_wave(shape: str, skew: float, pulse_width: float,
             phase: float) -> float:
    """Bipolar (-1..1) LFO waveform at `phase` in [0, 1).

    Mirrors Rust Lfo::wave exactly.
    """
    p = phase % 1.0
    if shape == LFO_SINE:
        return math.sin(2.0 * math.pi * p)
    if shape == LFO_SAW:
        return 2.0 * p - 1.0
    if shape == LFO_PULSE:
        return 1.0 if p < pulse_width else -1.0
    if shape == LFO_TRIANGLE:
        # Rise time morphs with skew: 0 -> symmetric triangle (peak at
        # phase 0.5); +1 -> rising saw; -1 -> falling saw.
        rise = 0.5 * (1.0 + max(-1.0, min(1.0, skew)))
        rise = max(0.01, min(0.99, rise))
        if p < rise:
            return -1.0 + 2.0 * p / rise
        return 1.0 - 2.0 * (p - rise) / (1.0 - rise)
    raise ValueError(f"unknown LFO shape '{shape}'")


def _apply_lfo(base: float, beat: float, lfo) -> float:
    """Apply the LFO modulation layer (or pass through when disabled)."""
    if lfo is None or not _lfo_field(lfo, "enabled", False):
        return base
    speed = _lfo_field(lfo, "speed", 1.0)
    shape = _lfo_field(lfo, "shape", LFO_SINE)
    skew = _lfo_field(lfo, "skew", 0.0)
    pulse_width = _lfo_field(lfo, "pulse_width", 0.5)
    level = _lfo_field(lfo, "level", 1.0)
    combine = _lfo_field(lfo, "combine", LFO_ADD)
    wave = lfo_wave(shape, skew, pulse_width, (beat * speed) % 1.0)
    if combine == LFO_MULTIPLY:
        return base * (1.0 + level * wave)
    return base + level * wave


def sample_curve(points, interp: str, tension: float,
                 samples_per_segment: int = 24, lfo=None,
                 ) -> list[tuple[float, float]]:
    """Sample (beat, value) pairs along the whole lane for the painter.

    Endpoints are shared between adjacent segments (no duplicates).
    A single point yields just itself; empty yields []. When `lfo` is
    given, the returned samples are the MODULATED curve (the preview
    overlay); pass lfo=None for the base spline.
    """
    pts = sorted((_norm_point(p) for p in points), key=lambda p: p[0])
    if len(pts) < 2:
        base = [(p[0], p[1]) for p in pts]
        return [(b, _apply_lfo(v, b, lfo)) for b, v in base]
    out: list[tuple[float, float]] = []
    for i in range(len(pts) - 1):
        b0, v0 = pts[i][0], pts[i][1]
        b1, v1 = pts[i + 1][0], pts[i + 1][1]
        n = max(2, samples_per_segment)
        for k in range(n):
            t = k / (n - 1)
            beat = b0 + (b1 - b0) * t
            val = _eval(interp, pts, i, t, b0, v0, b1, v1, tension)
            out.append((beat, _apply_lfo(val, beat, lfo)))
        # Drop the shared endpoint; the next segment re-adds it
        # (except after the final segment).
        if i < len(pts) - 2:
            out.pop()
    return out
