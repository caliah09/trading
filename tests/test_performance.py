"""Tests for forward-return tracking."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from screener.config import ScreenerConfig
from screener.performance import (
    _add_trading_days,
    compute_forward_returns,
    summarise_run_performance,
)
from screener.scoring.engine import TickerScore
from tests.conftest import TEST_TODAY, make_frame


def seed_prices(db, symbol: str, closes: list[float], start: date) -> None:
    """Store consecutive daily bars beginning at ``start``."""
    frame = make_frame(closes, end=start + timedelta(days=len(closes) - 1))
    db.upsert_prices(symbol, frame)


def make_entry(symbol: str, direction: str, rank: int, composite: float = 50.0) -> TickerScore:
    score = TickerScore(
        symbol=symbol, composite=composite, direction=direction,
        direction_label=f"{direction} lean", conviction=0.6, net_signal=0.6,
        parameters=[], spot=100.0,
    )
    score.rank = rank
    return score


def create_run_at(db, when: date, scores: list[TickerScore]) -> int:
    """Create a completed run back-dated to ``when``."""
    run_id = db.start_run("manual", ScreenerConfig(), "synthetic")
    db.save_ranking(run_id, scores)
    db.finish_run(run_id, "ok")
    stamp = datetime(when.year, when.month, when.day, 16, 10,
                     tzinfo=timezone.utc).isoformat(timespec="seconds")
    with db.connect() as conn:
        conn.execute("UPDATE runs SET started_at = ?, finished_at = ? WHERE id = ?",
                     (stamp, stamp, run_id))
    return run_id


class TestAddTradingDays:
    def test_skips_the_weekend(self):
        # Friday 13 Mar 2026 + 1 session = Monday 16 Mar.
        assert _add_trading_days(date(2026, 3, 13), 1) == date(2026, 3, 16)

    def test_counts_consecutive_weekdays(self):
        assert _add_trading_days(date(2026, 3, 9), 4) == date(2026, 3, 13)

    def test_zero_is_a_no_op(self):
        assert _add_trading_days(date(2026, 3, 11), 0) == date(2026, 3, 11)


class TestComputeForwardReturns:
    def test_computes_a_call_lean_correctly(self, db):
        start = date(2026, 3, 2)
        seed_prices(db, "AAPL", [100.0 + i for i in range(30)], start)
        run_id = create_run_at(db, start, [make_entry("AAPL", "call", 1)])

        written = compute_forward_returns(db, horizons=(5,), today=date(2026, 4, 30))
        assert written == 1
        result = db.get_ranking(run_id)[0]["forward_returns"][0]
        assert result["return_pct"] > 0
        # A call lean that rose is correct, so the directional figure matches.
        assert result["directional_pct"] == pytest.approx(result["return_pct"])

    def test_put_lean_is_rewarded_for_a_decline(self, db):
        """The screener was right; the sign must reflect that."""
        start = date(2026, 3, 2)
        seed_prices(db, "AAPL", [130.0 - i for i in range(30)], start)
        run_id = create_run_at(db, start, [make_entry("AAPL", "put", 1)])

        compute_forward_returns(db, horizons=(5,), today=date(2026, 4, 30))
        result = db.get_ranking(run_id)[0]["forward_returns"][0]
        assert result["return_pct"] < 0
        assert result["directional_pct"] > 0

    def test_put_lean_is_penalised_for_a_rally(self, db):
        start = date(2026, 3, 2)
        seed_prices(db, "AAPL", [100.0 + i for i in range(30)], start)
        run_id = create_run_at(db, start, [make_entry("AAPL", "put", 1)])

        compute_forward_returns(db, horizons=(5,), today=date(2026, 4, 30))
        result = db.get_ranking(run_id)[0]["forward_returns"][0]
        assert result["directional_pct"] < 0

    def test_neutral_lean_claims_nothing(self, db):
        """No direction was called, so there is nothing to be right about."""
        start = date(2026, 3, 2)
        seed_prices(db, "AAPL", [100.0 + i for i in range(30)], start)
        run_id = create_run_at(db, start, [make_entry("AAPL", "neutral", 1)])

        compute_forward_returns(db, horizons=(5,), today=date(2026, 4, 30))
        result = db.get_ranking(run_id)[0]["forward_returns"][0]
        assert result["directional_pct"] == 0.0
        assert result["return_pct"] != 0.0

    def test_future_horizons_are_skipped(self, db):
        """A horizon that has not elapsed must not be invented."""
        start = date(2026, 3, 2)
        seed_prices(db, "AAPL", [100.0 + i for i in range(30)], start)
        create_run_at(db, start, [make_entry("AAPL", "call", 1)])
        assert compute_forward_returns(db, horizons=(20,), today=date(2026, 3, 4)) == 0

    def test_is_idempotent(self, db):
        start = date(2026, 3, 2)
        seed_prices(db, "AAPL", [100.0 + i for i in range(30)], start)
        create_run_at(db, start, [make_entry("AAPL", "call", 1)])
        first = compute_forward_returns(db, horizons=(5,), today=date(2026, 4, 30))
        second = compute_forward_returns(db, horizons=(5,), today=date(2026, 4, 30))
        assert first == 1
        assert second == 0

    def test_missing_prices_are_skipped_silently(self, db):
        start = date(2026, 3, 2)
        create_run_at(db, start, [make_entry("GHOST", "call", 1)])
        assert compute_forward_returns(db, horizons=(5,), today=date(2026, 4, 30)) == 0

    def test_no_runs_is_a_no_op(self, db):
        assert compute_forward_returns(db, horizons=(1,), today=TEST_TODAY) == 0


class TestSummarise:
    def test_compares_top_against_the_rest(self, db):
        start = date(2026, 3, 2)
        # WINNER climbs, LOSER falls; both called as call leans.
        seed_prices(db, "WINNER", [100.0 + 2 * i for i in range(30)], start)
        seed_prices(db, "LOSER", [100.0 - 2 * i for i in range(30)], start)
        run_id = create_run_at(db, start, [
            make_entry("WINNER", "call", 1, composite=90.0),
            make_entry("LOSER", "call", 2, composite=10.0),
        ])
        compute_forward_returns(db, horizons=(5,), today=date(2026, 4, 30))

        summary = summarise_run_performance(db, run_id, top_n=1, horizons=(5,))
        assert summary["available"]
        horizon = summary["horizons"][0]
        assert horizon["top_avg"] > horizon["rest_avg"]
        assert horizon["edge"] > 0
        assert horizon["hit_rate"] == 100.0

    def test_reports_hit_rate_for_directional_picks(self, db):
        start = date(2026, 3, 2)
        seed_prices(db, "AAPL", [100.0 - i for i in range(30)], start)
        run_id = create_run_at(db, start, [make_entry("AAPL", "call", 1)])
        compute_forward_returns(db, horizons=(5,), today=date(2026, 4, 30))

        summary = summarise_run_performance(db, run_id, top_n=1, horizons=(5,))
        assert summary["horizons"][0]["hit_rate"] == 0.0  # called up, went down

    def test_unavailable_before_returns_exist(self, db):
        run_id = create_run_at(db, date(2026, 3, 2), [make_entry("AAPL", "call", 1)])
        assert summarise_run_performance(db, run_id, top_n=1)["available"] is False

    def test_unknown_run(self, db):
        assert summarise_run_performance(db, 999, top_n=5)["available"] is False
