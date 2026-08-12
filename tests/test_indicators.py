"""Tests for indicator maths."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from screener import indicators
from tests.conftest import make_series, trending


class TestRSI:
    def test_all_gains_is_100(self):
        series = make_series(trending(40, daily_pct=1.0))
        assert indicators.rsi(series).iloc[-1] == pytest.approx(100.0)

    def test_all_losses_is_zero(self):
        series = make_series(trending(40, daily_pct=-1.0))
        assert indicators.rsi(series).iloc[-1] == pytest.approx(0.0, abs=1e-6)

    def test_flat_series_is_neutral(self):
        series = make_series([100.0] * 40)
        assert indicators.rsi(series).iloc[-1] == pytest.approx(50.0)

    def test_bounded(self):
        rng = np.random.default_rng(7)
        series = make_series(list(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))))
        values = indicators.rsi(series).dropna()
        assert values.between(0, 100).all()

    def test_insufficient_history_is_nan(self):
        series = make_series([100.0, 101.0, 102.0])
        assert indicators.rsi(series, 14).isna().all()

    def test_matches_wilder_worked_example(self):
        """Guards the SMA-seeded smoothing against an EMA-from-bar-one variant.

        This is the standard worked series; Wilder's method puts the first RSI
        reading around 70. Seeding from the first bar instead lands near 47,
        which is what a broker chart would not show.
        """
        closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42,
                  45.84, 46.08, 45.89, 46.03, 45.61, 46.28, 46.28]
        value = indicators.rsi(make_series(closes), 14).iloc[-1]
        assert value == pytest.approx(70.46, abs=1.0)

    def test_seed_appears_exactly_at_period(self):
        """The first defined value sits at index `period`, not earlier."""
        series = make_series(trending(30, daily_pct=0.4))
        result = indicators.rsi(series, 14)
        assert result.iloc[:14].isna().all()
        assert not pd.isna(result.iloc[14])

    def test_rejects_bad_period(self):
        with pytest.raises(ValueError):
            indicators.rsi(make_series([1.0, 2.0]), 0)


class TestMovingAverages:
    def test_sma_needs_full_window(self):
        series = make_series([1.0, 2.0, 3.0, 4.0])
        result = indicators.sma(series, 3)
        assert result.isna().sum() == 2
        assert result.iloc[-1] == pytest.approx(3.0)

    def test_ema_recursive_form(self):
        """adjust=False is the charting-platform convention; verify the first steps."""
        series = make_series([10.0, 20.0, 30.0])
        result = indicators.ema(series, span=2)
        alpha = 2 / 3
        expected = 10.0
        for value in (20.0, 30.0):
            expected = alpha * value + (1 - alpha) * expected
        assert result.iloc[-1] == pytest.approx(expected)


class TestMACD:
    def test_uptrend_has_positive_macd_line(self):
        series = make_series(trending(120, daily_pct=0.6))
        assert indicators.macd(series).macd.iloc[-1] > 0

    def test_downtrend_has_negative_macd_line(self):
        series = make_series(trending(120, daily_pct=-0.6))
        assert indicators.macd(series).macd.iloc[-1] < 0

    def test_steady_trend_drives_histogram_toward_zero(self):
        """The histogram measures acceleration, not direction.

        A constant-rate trend has no momentum *change*, so MACD and its signal
        line converge and the histogram decays to ~0 even though the trend is
        strong. This is why the MACD scorer reports 'no fresh momentum' rather
        than a strong signal on a long, steady grind.
        """
        series = make_series(trending(200, daily_pct=0.6))
        result = indicators.macd(series)
        assert abs(result.histogram.iloc[-1]) < abs(result.macd.iloc[-1]) * 0.1

    def test_accelerating_move_has_positive_histogram(self):
        flat = [100.0] * 80
        surge = [100.0 * (1.03 ** i) for i in range(1, 21)]
        assert indicators.macd(make_series(flat + surge)).histogram.iloc[-1] > 0

    def test_histogram_is_macd_minus_signal(self):
        series = make_series(trending(120, daily_pct=0.3))
        result = indicators.macd(series)
        assert result.histogram.iloc[-1] == pytest.approx(
            result.macd.iloc[-1] - result.signal.iloc[-1]
        )

    def test_rejects_fast_above_slow(self):
        with pytest.raises(ValueError, match="must be less than"):
            indicators.macd(make_series(trending(50)), fast=26, slow=12)


class TestLastCrossIndex:
    def test_detects_recent_cross(self):
        a = make_series([1.0, 1.0, 1.0, 2.0, 3.0])
        b = make_series([2.0, 2.0, 2.0, 1.0, 1.0])
        assert indicators.last_cross_index(a, b) == 1

    def test_cross_on_final_bar_is_zero(self):
        a = make_series([1.0, 1.0, 3.0])
        b = make_series([2.0, 2.0, 2.0])
        assert indicators.last_cross_index(a, b) == 0

    def test_no_cross_returns_none(self):
        a = make_series([3.0, 4.0, 5.0])
        b = make_series([1.0, 1.0, 1.0])
        assert indicators.last_cross_index(a, b) is None

    def test_too_short_returns_none(self):
        assert indicators.last_cross_index(make_series([1.0]), make_series([2.0])) is None

    def test_exact_equality_does_not_count_as_two_crosses(self):
        """A touch that returns to the same side is not a crossover."""
        a = make_series([1.0, 2.0, 2.0, 1.0])
        b = make_series([2.0, 2.0, 2.0, 2.0])
        # Series never actually crosses to the other side.
        assert indicators.last_cross_index(a, b) is None


class TestRealisedVolatility:
    def test_flat_series_has_zero_volatility(self):
        series = make_series([100.0] * 60)
        assert indicators.realised_volatility(series, 21).iloc[-1] == pytest.approx(0.0)

    def test_noisier_series_scores_higher(self):
        rng = np.random.default_rng(3)
        calm = make_series(list(100 * np.exp(np.cumsum(rng.normal(0, 0.005, 100)))))
        wild = make_series(list(100 * np.exp(np.cumsum(rng.normal(0, 0.05, 100)))))
        calm_vol = indicators.realised_volatility(calm, 21).iloc[-1]
        wild_vol = indicators.realised_volatility(wild, 21).iloc[-1]
        assert wild_vol > calm_vol

    def test_annualised_percentage_scale(self):
        """A 1% daily standard deviation annualises to roughly 16%."""
        rng = np.random.default_rng(11)
        returns = rng.normal(0, 0.01, 400)
        series = make_series(list(100 * np.exp(np.cumsum(returns))))
        value = indicators.realised_volatility(series, 252).iloc[-1]
        assert 12.0 < value < 20.0

    def test_rejects_tiny_window(self):
        with pytest.raises(ValueError):
            indicators.realised_volatility(make_series([1.0, 2.0]), 1)


class TestPercentileRank:
    def test_maximum_ranks_high(self):
        series = pd.Series([1.0, 2.0, 3.0, 4.0, 10.0])
        assert indicators.percentile_rank(series) > 85.0

    def test_minimum_ranks_low(self):
        series = pd.Series([10.0, 9.0, 8.0, 7.0, 1.0])
        assert indicators.percentile_rank(series) < 15.0

    def test_constant_series_is_midpoint(self):
        """No range means neither high nor low; 0 or 100 would be misleading."""
        assert indicators.percentile_rank(pd.Series([5.0] * 10)) == 50.0

    def test_single_observation_is_midpoint(self):
        assert indicators.percentile_rank(pd.Series([5.0])) == 50.0

    def test_empty_returns_none(self):
        assert indicators.percentile_rank(pd.Series(dtype="float64")) is None

    def test_explicit_value_ranked_against_history(self):
        history = pd.Series([10.0, 20.0, 30.0, 40.0])
        assert indicators.percentile_rank(history, value=35.0) == pytest.approx(75.0)

    def test_ignores_nans(self):
        series = pd.Series([1.0, np.nan, 2.0, 3.0])
        assert indicators.percentile_rank(series, value=3.0) is not None


class TestPctChangeOver:
    def test_simple_change(self):
        series = make_series([100.0, 105.0, 110.0])
        assert indicators.pct_change_over(series, 2) == pytest.approx(10.0)

    def test_too_short_returns_none(self):
        assert indicators.pct_change_over(make_series([100.0]), 5) is None

    def test_zero_base_returns_none(self):
        assert indicators.pct_change_over(make_series([0.0, 5.0]), 1) is None
