"""Tests for the decision journal and weight calibration.

The statistical honesty of this module matters more than its cleverness, so a
good share of these tests assert that it *declines* to draw conclusions.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from screener.config import ScreenerConfig
from screener.journal import MIN_SAMPLE, calibrate
from screener.scoring.engine import TickerScore
from screener.scoring.parameters import ParameterResult
from tests.conftest import make_frame

START = date(2026, 3, 2)


def score_for(symbol: str, rank: int, rsi_contribution: float) -> TickerScore:
    params = [
        ParameterResult(key="rsi", label="RSI (14)", layer="stock", status="ok",
                        weight=8.0, signal=0.5, contribution=rsi_contribution,
                        explanation="x"),
        ParameterResult(key="macd", label="MACD crossover", layer="stock", status="ok",
                        weight=7.0, signal=0.2, contribution=2.0, explanation="x"),
    ]
    score = TickerScore(
        symbol=symbol, composite=50.0 + rsi_contribution, direction="call",
        direction_label="Call lean", conviction=0.5, net_signal=0.5,
        parameters=params, spot=100.0,
    )
    score.rank = rank
    return score


def seed_run(db, scores, when: date = START) -> int:
    run_id = db.start_run("manual", ScreenerConfig(), "synthetic")
    db.save_ranking(run_id, scores)
    db.finish_run(run_id, "ok")
    stamp = datetime(when.year, when.month, when.day, 16, 10,
                     tzinfo=timezone.utc).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute("UPDATE runs SET started_at = ?, finished_at = ? WHERE id = ?",
                     (stamp, stamp, run_id))
    return run_id


def seed_outcome(db, run_id: int, symbol: str, directional_pct: float, horizon: int = 20) -> None:
    db.save_forward_returns([
        (run_id, symbol, horizon, 100.0, 100.0 * (1 + directional_pct / 100),
         directional_pct, directional_pct, "2026-04-01T00:00:00+00:00")
    ])


def build_sample(db, n_winners: int, n_losers: int, n_passed: int = 0,
                 winner_rsi: float = 9.0, loser_rsi: float = 2.0, seed: int = 7):
    """Create decided-and-resolved trades with a controllable RSI separation.

    Contributions carry a little noise on purpose. Identical values within a
    group give zero variance, which no real sample has and which the t-statistic
    correctly refuses to evaluate.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    index = 0
    for _ in range(n_winners):
        symbol = f"W{index}"
        run_id = seed_run(db, [score_for(symbol, 1, winner_rsi + rng.normal(0, 0.8))])
        db.record_decision(run_id, symbol, "taken", composite=59.0, direction="call")
        seed_outcome(db, run_id, symbol, float(4.0 + rng.normal(0, 0.5)))
        index += 1
    for _ in range(n_losers):
        symbol = f"L{index}"
        run_id = seed_run(db, [score_for(symbol, 1, loser_rsi + rng.normal(0, 0.8))])
        db.record_decision(run_id, symbol, "taken", composite=52.0, direction="call")
        seed_outcome(db, run_id, symbol, float(-3.0 + rng.normal(0, 0.5)))
        index += 1
    for _ in range(n_passed):
        symbol = f"P{index}"
        run_id = seed_run(db, [score_for(symbol, 1, 5.0 + rng.normal(0, 0.8))])
        db.record_decision(run_id, symbol, "passed", composite=55.0, direction="call")
        seed_outcome(db, run_id, symbol, float(0.2 + rng.normal(0, 0.5)))
        index += 1


class TestRecording:
    def test_round_trip(self, db):
        run_id = seed_run(db, [score_for("AAPL", 1, 5.0)])
        db.record_decision(run_id, "AAPL", "taken", note="liked the setup")
        stored = db.get_decisions(run_id)
        assert len(stored) == 1
        assert stored[0]["decision"] == "taken"
        assert stored[0]["note"] == "liked the setup"

    def test_re_recording_overwrites(self, db):
        run_id = seed_run(db, [score_for("AAPL", 1, 5.0)])
        db.record_decision(run_id, "AAPL", "watching")
        db.record_decision(run_id, "AAPL", "taken")
        stored = db.get_decisions(run_id)
        assert len(stored) == 1
        assert stored[0]["decision"] == "taken"

    def test_rejects_an_unknown_decision(self, db):
        run_id = seed_run(db, [score_for("AAPL", 1, 5.0)])
        with pytest.raises(ValueError, match="taken"):
            db.record_decision(run_id, "AAPL", "maybe")

    def test_delete(self, db):
        run_id = seed_run(db, [score_for("AAPL", 1, 5.0)])
        db.record_decision(run_id, "AAPL", "taken")
        assert db.delete_decision(run_id, "AAPL") is True
        assert db.get_decisions(run_id) == []

    def test_delete_missing_returns_false(self, db):
        assert db.delete_decision(999, "NOPE") is False

    def test_cascades_when_a_run_is_deleted(self, db):
        run_id = seed_run(db, [score_for("AAPL", 1, 5.0)])
        db.record_decision(run_id, "AAPL", "taken")
        with db.connect() as conn:
            conn.execute("DELETE FROM runs WHERE id = ?", (run_id,))
        assert db.get_decisions() == []


class TestRefusesToOverclaim:
    def test_no_decisions_says_so(self, db):
        report = calibrate(db)
        assert report.n_taken == 0
        assert "No decisions" in report.verdict
        assert report.actionable is False

    def test_small_sample_suggests_nothing(self, db):
        build_sample(db, n_winners=3, n_losers=2)
        report = calibrate(db)
        assert report.actionable is False
        assert all(p.suggested_weight is None for p in report.parameters)
        assert any(str(MIN_SAMPLE) in p.rationale for p in report.parameters)

    def test_small_sample_warns_about_noise(self, db):
        build_sample(db, n_winners=3, n_losers=2)
        report = calibrate(db)
        assert any("noise" in w for w in report.warnings)

    def test_decisions_without_outcomes_are_not_counted_as_resolved(self, db):
        run_id = seed_run(db, [score_for("AAPL", 1, 5.0)])
        db.record_decision(run_id, "AAPL", "taken")
        report = calibrate(db)
        assert report.n_taken == 1
        assert report.n_resolved_taken == 0

    def test_warns_when_nothing_was_passed(self, db):
        """Without a comparison group there is no selection edge to measure."""
        build_sample(db, n_winners=5, n_losers=5, n_passed=0)
        report = calibrate(db)
        assert any("Nothing is marked Passed" in w for w in report.warnings)

    def test_flags_t_statistics_as_indicative(self, db):
        build_sample(db, n_winners=10, n_losers=10)
        report = calibrate(db)
        assert any("indicative only" in w for w in report.warnings)


class TestSelectionEdge:
    def test_positive_edge_is_reported(self, db):
        build_sample(db, n_winners=10, n_losers=6, n_passed=6)
        report = calibrate(db)
        assert report.selection_edge is not None
        assert report.taken_mean > report.passed_mean
        assert "adding something" in report.verdict

    def test_negative_edge_is_reported_honestly(self, db):
        """If the passed candidates did better, say so plainly."""
        build_sample(db, n_winners=2, n_losers=14, n_passed=6)
        report = calibrate(db)
        assert report.selection_edge < 0
        assert "passed" in report.verdict

    def test_hit_rate_is_computed(self, db):
        build_sample(db, n_winners=8, n_losers=8)
        report = calibrate(db)
        assert report.hit_rate == pytest.approx(50.0, abs=0.1)


class TestParameterSeparation:
    def test_separating_parameter_is_detected(self, db):
        build_sample(db, n_winners=12, n_losers=12, winner_rsi=9.0, loser_rsi=1.0)
        report = calibrate(db)
        rsi = next(p for p in report.parameters if p.key == "rsi")
        assert rsi.separation > 5.0
        assert rsi.n_winners == 12
        assert rsi.n_losers == 12

    def test_non_separating_parameter_suggests_nothing(self, db):
        build_sample(db, n_winners=12, n_losers=12)
        report = calibrate(db)
        macd = next(p for p in report.parameters if p.key == "macd")
        assert macd.separation == pytest.approx(0.0, abs=1e-9)
        assert macd.suggested_weight is None

    def test_suggestion_moves_weight_up_for_a_good_parameter(self, db):
        build_sample(db, n_winners=12, n_losers=12, winner_rsi=9.0, loser_rsi=1.0)
        report = calibrate(db)
        rsi = next(p for p in report.parameters if p.key == "rsi")
        assert rsi.suggested_weight is not None
        assert rsi.suggested_weight > rsi.current_weight

    def test_suggestion_moves_weight_down_for_an_inverted_parameter(self, db):
        build_sample(db, n_winners=12, n_losers=12, winner_rsi=1.0, loser_rsi=9.0)
        report = calibrate(db)
        rsi = next(p for p in report.parameters if p.key == "rsi")
        assert rsi.suggested_weight < rsi.current_weight

    def test_suggestions_are_small_steps(self, db):
        build_sample(db, n_winners=15, n_losers=15, winner_rsi=10.0, loser_rsi=-10.0)
        report = calibrate(db)
        for param in report.parameters:
            if param.suggested_weight is not None:
                assert abs(param.suggested_weight - param.current_weight) <= 1.0

    def test_weights_stay_inside_the_valid_range(self, db):
        build_sample(db, n_winners=15, n_losers=15, winner_rsi=10.0, loser_rsi=-10.0)
        report = calibrate(db)
        for param in report.parameters:
            if param.suggested_weight is not None:
                assert 0.0 <= param.suggested_weight <= 10.0

    def test_ordered_by_separation(self, db):
        build_sample(db, n_winners=12, n_losers=12, winner_rsi=9.0, loser_rsi=1.0)
        report = calibrate(db)
        separations = [abs(p.separation or 0.0) for p in report.parameters]
        assert separations == sorted(separations, reverse=True)

    def test_every_registered_parameter_appears(self, db):
        from screener.param_specs import SPECS_BY_KEY

        build_sample(db, n_winners=5, n_losers=5)
        report = calibrate(db)
        assert {p.key for p in report.parameters} == set(SPECS_BY_KEY)


def test_serialises(db):
    build_sample(db, n_winners=12, n_losers=12, n_passed=5)
    payload = calibrate(db).to_dict()
    assert payload["sample"]["minimum_for_suggestions"] == MIN_SAMPLE
    assert "verdict" in payload
    assert isinstance(payload["parameters"], list)
