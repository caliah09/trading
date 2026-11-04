"""Tests for swing-point detection."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from screener.pivots import Pivot, average_true_range, find_pivots
from tests.conftest import TEST_TODAY, make_frame


def build_closes(waypoints: list[tuple[float, int]]) -> list[float]:
    """Piecewise-linear price path through (price, bars_to_get_there) waypoints."""
    closes = [float(waypoints[0][0])]
    for price, bars in waypoints[1:]:
        closes.extend(np.linspace(closes[-1], float(price), bars + 1)[1:])
    return closes


ZIGZAG = [(100, 0), (130, 25), (100, 25), (140, 25), (95, 25)]


class TestAverageTrueRange:
    def test_positive_and_finite(self):
        frame = make_frame(build_closes(ZIGZAG))
        atr = average_true_range(frame).dropna()
        assert len(atr) > 0
        assert (atr > 0).all()

    def test_needs_full_period(self):
        frame = make_frame([100.0] * 20)
        assert average_true_range(frame, 14).iloc[:13].isna().all()

    def test_accounts_for_gaps(self):
        """True range must see an overnight gap, not just the bar's own range."""
        frame = make_frame([100.0] * 20 + [130.0] * 20)
        atr = average_true_range(frame, 14).dropna()
        # The 30-point gap dwarfs the 2% synthetic intrabar range.
        assert atr.max() > 2.0


class TestFindPivots:
    def test_detects_alternating_swings(self):
        pivots = find_pivots(make_frame(build_closes(ZIGZAG)))
        assert len(pivots) >= 3
        kinds = [p.kind for p in pivots]
        assert all(a != b for a, b in zip(kinds, kinds[1:])), "pivots must alternate"

    def test_finds_the_major_extremes(self):
        pivots = find_pivots(make_frame(build_closes(ZIGZAG)))
        highs = [p.price for p in pivots if p.is_high]
        lows = [p.price for p in pivots if not p.is_high]
        assert any(abs(h - 130) < 6 or abs(h - 140) < 6 for h in highs)
        assert any(abs(low - 100) < 6 for low in lows)

    def test_higher_sensitivity_finds_fewer_pivots(self):
        """The research-relevant property: sensitivity genuinely changes output."""
        frame = make_frame(build_closes(ZIGZAG))
        fine = find_pivots(frame, sensitivity=0.6)
        coarse = find_pivots(frame, sensitivity=4.0)
        assert len(fine) >= len(coarse)

    def test_flat_series_has_no_pivots(self):
        assert find_pivots(make_frame([100.0] * 120)) == []

    def test_short_history_returns_empty(self):
        assert find_pivots(make_frame([100.0, 101.0, 102.0])) == []

    def test_empty_frame_returns_empty(self):
        assert find_pivots(pd.DataFrame()) == []

    def test_does_not_repaint_the_final_leg(self):
        """An unconfirmed extreme is just today's price and must not be a pivot.

        This is the property that separates an honest detector from one whose
        patterns only appear in hindsight.
        """
        rising = build_closes([(100, 0), (130, 25), (100, 25), (180, 30)])
        pivots = find_pivots(make_frame(rising))
        assert all(p.price < 175 for p in pivots), "the unconfirmed final high leaked in"

    def test_pivot_positions_are_in_range_and_ordered(self):
        frame = make_frame(build_closes(ZIGZAG))
        pivots = find_pivots(frame)
        positions = [p.position for p in pivots]
        assert positions == sorted(positions)
        assert all(0 <= p < len(frame) for p in positions)

    def test_max_pivots_keeps_most_recent(self):
        frame = make_frame(build_closes(ZIGZAG * 2))
        everything = find_pivots(frame)
        limited = find_pivots(frame, max_pivots=2)
        if len(everything) > 2:
            assert len(limited) == 2
            assert limited == everything[-2:]

    def test_prices_match_the_bar_extremes(self):
        frame = make_frame(build_closes(ZIGZAG))
        for pivot in find_pivots(frame):
            bar_price = (frame["high"] if pivot.is_high else frame["low"]).iloc[pivot.position]
            assert pivot.price == pytest.approx(float(bar_price))

    def test_deterministic(self):
        frame = make_frame(build_closes(ZIGZAG))
        assert find_pivots(frame) == find_pivots(frame)


def test_pivot_serialises():
    pivot = Pivot(position=5, timestamp=pd.Timestamp(TEST_TODAY), price=123.456, kind="high")
    payload = pivot.to_dict()
    assert payload["kind"] == "high"
    assert payload["price"] == 123.456
