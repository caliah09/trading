"""Tests for Head & Shoulders detection.

The central property under test is the one that defines the method: **price
action is the hard gate, geometry is only confidence.** An ugly pattern with a
valid lower-high/lower-low sequence must be detected; a beautiful one without
the confirming break must not be marked complete.
"""

from __future__ import annotations

import pytest

from screener.patterns.head_shoulders import (
    EXPECTED_HOLD_DAYS,
    detect_head_shoulders,
)
from screener.pivots import find_pivots
from tests.conftest import make_frame
from tests.test_pivots import build_closes

# left shoulder 122 · trough 104 · head 142 · trough 102 · right shoulder 119 · break to 86
BEARISH = [(100, 0), (122, 25), (104, 20), (142, 25), (102, 22), (119, 20), (86, 25)]
BULLISH = [(140, 0), (118, 25), (136, 20), (98, 25), (138, 22), (121, 20), (154, 25)]

#: Same shape as BEARISH, but the pullback from the right shoulder stalls at 109
#: — above the ~103 neckline. The right shoulder is confirmed (price retraced
#: from it) while the neckline is untouched, which is what "forming" means.
#:
#: Note it cannot simply stop *at* the right shoulder: an extreme with no
#: retracement after it is unconfirmed, and treating it as a pivot would be
#: repainting. A pattern is only visible once its right shoulder is real.
BEARISH_FORMING = BEARISH[:-1] + [(109, 18)]


def detect(waypoints, volumes=None, **kwargs):
    frame = make_frame(build_closes(waypoints), volumes)
    pivots = find_pivots(frame)
    return detect_head_shoulders(frame, pivots, **kwargs), frame, pivots


class TestBearishDetection:
    def test_detects_a_clean_pattern(self):
        pattern, _, _ = detect(BEARISH)
        assert pattern is not None
        assert pattern.direction == "bearish"
        assert pattern.sign == -1

    def test_head_is_the_highest_pivot(self):
        pattern, _, _ = detect(BEARISH)
        assert pattern.head.price > pattern.left_shoulder.price
        assert pattern.head.price > pattern.right_shoulder.price

    def test_completed_pattern_confirms_both_gates(self):
        pattern, _, _ = detect(BEARISH)
        assert pattern.broke_neckline is True
        assert pattern.extreme_confirmed is True
        assert pattern.completion == 1.0
        assert pattern.state == "confirmed"

    def test_forming_pattern_is_not_complete(self):
        """Shape without a break must never read as confirmed."""
        pattern, _, _ = detect(BEARISH_FORMING)
        assert pattern is not None
        assert pattern.broke_neckline is False
        assert pattern.completion < 0.7
        assert pattern.state in ("forming", "right shoulder at level")

    def test_measured_move_projects_downward(self):
        pattern, _, _ = detect(BEARISH)
        assert pattern.target is not None
        assert pattern.target < pattern.neckline_now

    def test_carries_the_research_backed_hold_period(self):
        pattern, _, _ = detect(BEARISH)
        assert pattern.to_dict()["expected_hold_days"] == EXPECTED_HOLD_DAYS == 60


class TestBullishDetection:
    def test_detects_inverse_pattern(self):
        pattern, _, _ = detect(BULLISH)
        assert pattern is not None
        assert pattern.direction == "bullish"
        assert pattern.sign == 1

    def test_head_is_the_lowest_pivot(self):
        pattern, _, _ = detect(BULLISH)
        assert pattern.head.price < pattern.left_shoulder.price
        assert pattern.head.price < pattern.right_shoulder.price

    def test_measured_move_projects_upward(self):
        pattern, _, _ = detect(BULLISH)
        assert pattern.target is not None
        assert pattern.target > pattern.neckline_now


class TestImperfectPatternsStayValid:
    """The method's core claim — imperfection must not invalidate."""

    def test_right_shoulder_higher_than_left_is_still_valid(self):
        # Right shoulder at 131 vs left at 118; head still highest at 145.
        waypoints = [(100, 0), (118, 25), (104, 20), (145, 25), (102, 22), (131, 20), (86, 25)]
        pattern, _, _ = detect(waypoints)
        assert pattern is not None
        assert pattern.right_shoulder.price > pattern.left_shoulder.price
        assert pattern.completion == 1.0, "asymmetry must not block completion"

    def test_uneven_neckline_is_still_valid(self):
        # Troughs at 110 and 98 — the neckline slopes, which the method calls a
        # higher-quality setup rather than a defect.
        waypoints = [(100, 0), (124, 25), (110, 20), (145, 25), (98, 22), (120, 20), (70, 25)]
        pattern, _, _ = detect(waypoints)
        assert pattern is not None
        assert pattern.neckline_slope < 0
        assert pattern.completion == 1.0

    def test_neckline_extension_is_capped(self):
        """A steep neckline must not run away and make the break unreachable."""
        waypoints = [(100, 0), (124, 25), (112, 20), (145, 25), (92, 22), (120, 20), (82, 40)]
        pattern, frame, _ = detect(waypoints)
        assert pattern is not None
        # Held flat past the cap, the line stays within the pattern's own range
        # rather than extrapolating below the lowest trough indefinitely.
        assert pattern.neckline_now > frame["close"].min() - 20

    def test_asymmetry_lowers_confidence_without_invalidating(self):
        clean, _, _ = detect(BEARISH)
        waypoints = [(100, 0), (112, 25), (104, 20), (150, 25), (102, 22), (134, 20), (86, 25)]
        ugly, _, _ = detect(waypoints)
        assert ugly is not None
        assert ugly.completion == clean.completion == 1.0
        assert ugly.symmetry < clean.symmetry, "symmetry should differentiate confidence"

    def test_low_symmetry_is_explained_not_hidden(self):
        waypoints = [(100, 0), (110, 25), (104, 20), (155, 25), (102, 22), (140, 20), (86, 25)]
        pattern, _, _ = detect(waypoints)
        assert pattern is not None
        if pattern.symmetry < 0.6:
            assert any("symmetry" in note.lower() for note in pattern.notes)


class TestQualityFactors:
    def test_favourable_neckline_slope_is_noted(self):
        # Second trough well below the first -> neckline slopes down into a bearish break.
        waypoints = [(100, 0), (124, 25), (116, 20), (146, 25), (96, 22), (120, 20), (78, 25)]
        pattern, _, _ = detect(waypoints)
        assert pattern is not None
        assert pattern.neckline_slope < 0
        assert any("slopes in the direction" in note for note in pattern.notes)

    def test_quality_is_bounded(self):
        for waypoints in (BEARISH, BULLISH, BEARISH_FORMING):
            pattern, _, _ = detect(waypoints)
            if pattern is not None:
                assert 0.0 <= pattern.quality <= 1.0
                assert 0.0 <= pattern.symmetry <= 1.0

    def test_volume_note_flags_crowding_not_confirmation(self):
        """Osler reads the completion volume spike as crowding; the copy must too."""
        closes = build_closes(BEARISH)
        volumes = [1_000_000.0] * len(closes)
        volumes[-20:] = [6_000_000.0] * 20
        pattern, _, _ = detect(BEARISH, volumes)
        assert pattern is not None
        volume_notes = [n for n in pattern.notes if "Volume on the break" in n]
        if volume_notes:
            assert "crowding" in volume_notes[0]


class TestNoPattern:
    def test_flat_series_yields_nothing(self):
        pattern, _, _ = detect([(100, 0), (100, 120)])
        assert pattern is None

    def test_steady_uptrend_yields_nothing(self):
        pattern, _, _ = detect([(100, 0), (200, 140)])
        assert pattern is None

    def test_too_few_pivots_yields_nothing(self):
        frame = make_frame(build_closes([(100, 0), (130, 30), (105, 30)]))
        assert detect_head_shoulders(frame, find_pivots(frame)) is None

    def test_empty_pivots_yields_nothing(self):
        frame = make_frame(build_closes(BEARISH))
        assert detect_head_shoulders(frame, []) is None


class TestSerialisation:
    def test_to_dict_carries_every_pivot_and_the_reasoning(self):
        pattern, _, _ = detect(BEARISH)
        payload = pattern.to_dict()
        assert set(payload["pivots"]) == {
            "left_shoulder", "trough_1", "head", "trough_2", "right_shoulder"
        }
        assert payload["direction"] == "bearish"
        assert payload["state"] == "confirmed"
        assert isinstance(payload["notes"], list)

    def test_deterministic(self):
        first, frame, pivots = detect(BEARISH)
        second = detect_head_shoulders(frame, pivots)
        assert first.to_dict() == second.to_dict()
