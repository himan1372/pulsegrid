"""Tests for the automation curve evaluator (research topic 30).

curve_eval.py is a pure-Python mirror of Rust AutoCurve::value_at /
eval_segment. These tests use the SAME fixed vectors as the Rust tests
in engine/src/timeline.rs, so a formula drift in either language fails
loudly. The painter samples this module; it never reimplements curves.
"""

import math

import pytest

from daw.curve_eval import (
    HOLD,
    LINEAR,
    MODES,
    PULSE,
    SMOOTH,
    STAIRS,
    WAVE,
    sample_curve,
    uses_tension,
    value_at,
)

PTS = [(0.0, 0.0), (4.0, 8.0)]


def test_modes_match_rust_names():
    assert MODES == ("linear", "smooth", "hold", "stairs", "pulse", "wave")


def test_uses_tension_matches_rust():
    assert not uses_tension(LINEAR)
    assert not uses_tension(HOLD)
    for m in (SMOOTH, STAIRS, PULSE, WAVE):
        assert uses_tension(m)


def test_linear_parity():
    assert value_at(PTS, LINEAR, 0.5, 2.0, 0.0) == pytest.approx(4.0)
    assert value_at(PTS, LINEAR, 0.5, -1.0, 9.0) == pytest.approx(9.0)
    assert value_at(PTS, LINEAR, 0.5, 100.0, 0.0) == pytest.approx(8.0)


def test_hold_parity():
    assert value_at(PTS, HOLD, 0.5, 0.0, 9.0) == pytest.approx(0.0)
    assert value_at(PTS, HOLD, 0.5, 2.0, 9.0) == pytest.approx(0.0)
    assert value_at(PTS, HOLD, 0.5, 3.999, 9.0) == pytest.approx(0.0)
    assert value_at(PTS, HOLD, 0.5, 4.0, 9.0) == pytest.approx(8.0)
    assert value_at(PTS, HOLD, 0.5, -1.0, 9.0) == pytest.approx(9.0)


def test_smooth_parity():
    # Symmetric segment: midpoint exact for any tension.
    assert value_at(PTS, SMOOTH, 1.0, 2.0, 0.0) == pytest.approx(4.0)
    assert value_at(PTS, SMOOTH, 0.5, 2.0, 0.0) == pytest.approx(4.0)
    # Tension 0 = smoothstep: 0.15625 * 8 = 1.25 at t = 0.25.
    assert value_at(PTS, SMOOTH, 0.0, 1.0, 0.0) == pytest.approx(1.25)
    assert value_at(PTS, SMOOTH, 1.0, 0.0, 0.0) == pytest.approx(0.0)


def test_stairs_parity():
    # tension 0.5 -> 9 steps.
    assert value_at(PTS, STAIRS, 0.5, 0.5, 0.0) == pytest.approx(1.0)
    assert value_at(PTS, STAIRS, 0.5, 2.0, 0.0) == pytest.approx(4.0)
    assert value_at(PTS, STAIRS, 0.5, 3.999, 0.0) == pytest.approx(8.0)


def test_pulse_parity():
    assert value_at(PTS, PULSE, 0.0, 1.0, 0.0) == pytest.approx(0.0)
    assert value_at(PTS, PULSE, 0.0, 3.0, 0.0) == pytest.approx(8.0)
    assert value_at(PTS, PULSE, 1.0, 0.2, 0.0) == pytest.approx(0.0)
    assert value_at(PTS, PULSE, 1.0, 0.4, 0.0) == pytest.approx(8.0)


def test_wave_parity():
    assert value_at(PTS, WAVE, 0.0, 1.0, 0.0) == pytest.approx(6.0)
    assert value_at(PTS, WAVE, 0.0, 3.0, 0.0) == pytest.approx(2.0)
    assert value_at(PTS, WAVE, 0.0, 0.0, 0.0) == pytest.approx(0.0)


def test_rounding_matches_rust_at_half_boundaries():
    # Rust f64::round() is half-away-from-zero; Python round() is
    # banker's. tension 0.75 -> 10.5 must give 11 steps (13 total),
    # not 10.
    v = value_at(PTS, STAIRS, 0.75, 2.0, 0.0)
    # 13 steps: idx floor(0.5*13)=6 -> 8*6/12 = 4.0.
    assert v == pytest.approx(4.0)
    # Banker's rounding would give 12 steps -> idx 6 -> 8*6/11 = 4.3636.
    assert abs(v - 4.3636) > 0.01


def test_unknown_interp_raises():
    with pytest.raises(ValueError):
        value_at(PTS, "bogus", 0.5, 2.0, 0.0)


def test_sample_curve_endpoints_shared():
    pts = [(0.0, 0.0), (2.0, 2.0), (4.0, 0.0)]
    s = sample_curve(pts, LINEAR, 0.5, samples_per_segment=8)
    # 8 per segment, shared endpoints: 8 + 7 = 15.
    assert len(s) == 15
    assert s[0] == (0.0, 0.0)
    assert s[-1] == (4.0, 0.0)
    # No duplicate beats.
    beats = [b for b, _ in s]
    assert len(set(beats)) == len(beats)
    # Linear samples lie on the segments: k=4 -> t=4/7.
    assert s[4] == pytest.approx((8 / 7, 8 / 7))
    # Shared midpoint is exact and unduplicated.
    assert s[7] == (2.0, 2.0)


def test_sample_curve_degenerate():
    assert sample_curve([], LINEAR, 0.5) == []
    assert sample_curve([(3.0, 7.0)], LINEAR, 0.5) == [(3.0, 7.0)]


def test_sample_curve_follows_mode():
    # Hold sampling must show the step, not a glide. The step holds
    # through the endpoint (t=1 -> v0); the jump AT the point is shown
    # by the next segment / the point oval itself (like LMMS's
    # inValue/outValue distinction for discontinuities).
    s = sample_curve(PTS, HOLD, 0.5, samples_per_segment=8)
    assert all(v == 0.0 for _, v in s)
