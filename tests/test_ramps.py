"""Tests for the continuous scoring primitives."""

from __future__ import annotations

import math

import pytest

from screener.scoring.ramps import (
    clamp,
    logistic,
    ramp01,
    signed_ramp,
    soft_sign,
    window_fit,
)


class TestLogistic:
    def test_midpoint(self):
        assert logistic(0.0) == pytest.approx(0.5)

    def test_symmetry(self):
        assert logistic(2.0) + logistic(-2.0) == pytest.approx(1.0)

    @pytest.mark.parametrize("z", [-10_000.0, -800.0, 800.0, 10_000.0])
    def test_extremes_do_not_overflow(self, z):
        """math.exp overflows near z=-745; the stable branch must not."""
        value = logistic(z)
        assert 0.0 <= value <= 1.0
        assert math.isfinite(value)


class TestRamp01:
    def test_threshold_is_half_credit(self):
        assert ramp01(30.0, midpoint=30.0, width=10.0) == pytest.approx(0.5)

    def test_one_width_past_threshold(self):
        """The documented contract: one width past the midpoint is ~0.88."""
        assert ramp01(40.0, midpoint=30.0, width=10.0) == pytest.approx(0.881, abs=0.002)

    def test_monotonic_rising(self):
        values = [ramp01(x, 30.0, 10.0) for x in range(0, 60, 5)]
        assert values == sorted(values)

    def test_falling_inverts(self):
        assert ramp01(40.0, 30.0, 10.0, rising=False) == pytest.approx(
            1.0 - ramp01(40.0, 30.0, 10.0)
        )

    def test_narrow_width_approximates_pass_fail(self):
        """A small width should behave almost like a hard threshold."""
        assert ramp01(31.0, 30.0, 0.1) > 0.99
        assert ramp01(29.0, 30.0, 0.1) < 0.01

    def test_rejects_nonpositive_width(self):
        with pytest.raises(ValueError, match="width must be positive"):
            ramp01(1.0, 0.0, 0.0)


class TestSignedRamp:
    def test_zero_at_midpoint(self):
        assert signed_ramp(0.0, 0.0, 1.5) == pytest.approx(0.0)

    def test_bounded(self):
        for x in (-1000.0, -5.0, 0.0, 5.0, 1000.0):
            assert -1.0 <= signed_ramp(x, 0.0, 1.5) <= 1.0

    def test_antisymmetric(self):
        assert signed_ramp(3.0, 0.0, 2.0) == pytest.approx(-signed_ramp(-3.0, 0.0, 2.0))


class TestWindowFit:
    def test_full_credit_inside_window(self):
        assert window_fit(60, 45, 90, 15) == 1.0
        assert window_fit(45, 45, 90, 15) == 1.0
        assert window_fit(90, 45, 90, 15) == 1.0

    def test_gaussian_decay_outside(self):
        # One tolerance outside the band is exp(-1) ~ 0.368.
        assert window_fit(30, 45, 90, 15) == pytest.approx(math.exp(-1), abs=0.001)
        assert window_fit(105, 45, 90, 15) == pytest.approx(math.exp(-1), abs=0.001)

    def test_symmetric_around_band(self):
        assert window_fit(35, 45, 90, 15) == pytest.approx(window_fit(100, 45, 90, 15))

    def test_far_outside_approaches_zero(self):
        assert window_fit(500, 45, 90, 15) < 1e-6

    def test_rejects_inverted_band(self):
        with pytest.raises(ValueError, match="must not exceed"):
            window_fit(50, 90, 45, 15)

    def test_rejects_nonpositive_tolerance(self):
        with pytest.raises(ValueError, match="tolerance must be positive"):
            window_fit(50, 45, 90, 0)


class TestSoftSign:
    def test_zero_stays_zero(self):
        assert soft_sign(0.0, 3.0) == 0.0

    def test_does_not_flip_hard_on_noise(self):
        """A tiny move must not produce a full-strength direction."""
        assert abs(soft_sign(0.05, 3.0)) < 0.02

    def test_saturates(self):
        assert soft_sign(100.0, 3.0) == pytest.approx(1.0, abs=1e-6)
        assert soft_sign(-100.0, 3.0) == pytest.approx(-1.0, abs=1e-6)

    def test_rejects_nonpositive_scale(self):
        with pytest.raises(ValueError):
            soft_sign(1.0, 0.0)


def test_clamp():
    assert clamp(-0.5) == 0.0
    assert clamp(1.5) == 1.0
    assert clamp(0.5) == 0.5
