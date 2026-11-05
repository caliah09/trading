"""Tests for SQLite persistence."""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from screener.config import ScreenerConfig
from screener.db import Database
from screener.scoring.engine import TickerScore
from screener.scoring.parameters import ParameterResult
from tests.conftest import TEST_TODAY, make_frame, trending


class TestPrices:
    def test_round_trip(self, db):
        frame = make_frame(trending(10))
        assert db.upsert_prices("AAPL", frame) == 10
        stored = db.get_prices("AAPL")
        assert len(stored) == 10
        assert stored["close"].iloc[-1] == pytest.approx(frame["close"].iloc[-1])

    def test_upsert_is_idempotent(self, db):
        frame = make_frame(trending(10))
        db.upsert_prices("AAPL", frame)
        db.upsert_prices("AAPL", frame)
        assert len(db.get_prices("AAPL")) == 10

    def test_upsert_updates_existing_bars(self, db):
        """A same-day re-run must correct the bar, not duplicate it."""
        db.upsert_prices("AAPL", make_frame([100.0]))
        db.upsert_prices("AAPL", make_frame([123.0]))
        stored = db.get_prices("AAPL")
        assert len(stored) == 1
        assert stored["close"].iloc[0] == pytest.approx(123.0)

    def test_empty_frame_writes_nothing(self, db):
        assert db.upsert_prices("AAPL", pd.DataFrame()) == 0

    def test_rows_without_close_are_skipped(self, db):
        frame = make_frame([100.0, 101.0])
        frame.loc[frame.index[0], "close"] = None
        assert db.upsert_prices("AAPL", frame) == 1

    def test_unknown_symbol_returns_empty_frame(self, db):
        assert db.get_prices("NOPE").empty

    def test_limit_returns_most_recent_ascending(self, db):
        db.upsert_prices("AAPL", make_frame(trending(50)))
        recent = db.get_prices("AAPL", limit=5)
        assert len(recent) == 5
        assert recent.index.is_monotonic_increasing
        assert recent["close"].iloc[-1] == pytest.approx(
            db.get_prices("AAPL")["close"].iloc[-1])

    def test_symbols_are_isolated(self, db):
        db.upsert_prices("AAPL", make_frame(trending(5)))
        db.upsert_prices("MSFT", make_frame(trending(9)))
        assert len(db.get_prices("AAPL")) == 5
        assert len(db.get_prices("MSFT")) == 9

    def test_price_on_or_after(self, db):
        db.upsert_prices("AAPL", make_frame(trending(10)))
        first = db.get_prices("AAPL").index[0].date()
        found = db.price_on_or_after("AAPL", first)
        assert found is not None and found[0] == first

    def test_price_on_or_after_beyond_range_is_none(self, db):
        db.upsert_prices("AAPL", make_frame(trending(10)))
        assert db.price_on_or_after("AAPL", TEST_TODAY + timedelta(days=365)) is None


class TestIVHistory:
    def test_round_trip(self, db):
        db.record_iv("AAPL", date(2026, 3, 10), 0.28, 30)
        db.record_iv("AAPL", date(2026, 3, 11), 0.31, 30)
        history = db.get_iv_history("AAPL")
        assert len(history) == 2
        assert history.iloc[-1] == pytest.approx(0.31)

    def test_same_day_overwrites(self, db):
        db.record_iv("AAPL", date(2026, 3, 11), 0.28, 30)
        db.record_iv("AAPL", date(2026, 3, 11), 0.35, 30)
        history = db.get_iv_history("AAPL")
        assert len(history) == 1
        assert history.iloc[0] == pytest.approx(0.35)

    def test_absent_symbol_returns_empty_series(self, db):
        assert db.get_iv_history("NOPE").empty

    def test_returned_in_ascending_date_order(self, db):
        for offset in (3, 1, 2):
            db.record_iv("AAPL", date(2026, 3, offset), 0.2 + offset / 100, 30)
        assert db.get_iv_history("AAPL").index.is_monotonic_increasing


class TestEarnings:
    def test_round_trip(self, db):
        db.save_earnings("AAPL", date(2026, 4, 30), False)
        record = db.get_earnings("AAPL")
        assert record["next_date"] == date(2026, 4, 30)
        assert record["is_etf"] is False

    def test_never_fetched_returns_none(self, db):
        assert db.get_earnings("AAPL") is None

    def test_fetched_with_no_date_is_distinct_from_never_fetched(self, db):
        """A fund has no earnings date; that is a fact, not a gap."""
        db.save_earnings("SPY", None, True)
        record = db.get_earnings("SPY")
        assert record is not None
        assert record["next_date"] is None
        assert record["is_etf"] is True

    def test_overwrites_on_refetch(self, db):
        db.save_earnings("AAPL", date(2026, 4, 30), False)
        db.save_earnings("AAPL", date(2026, 7, 30), False)
        assert db.get_earnings("AAPL")["next_date"] == date(2026, 7, 30)


class TestConfigSnapshots:
    def test_snapshot_is_content_addressed(self, db):
        config = ScreenerConfig()
        first = db.save_config_snapshot(config)
        second = db.save_config_snapshot(ScreenerConfig())
        assert first == second

    def test_snapshot_round_trip(self, db):
        config = ScreenerConfig(watchlist=["NVDA"], top_n=3)
        fingerprint = db.save_config_snapshot(config)
        assert db.get_config_snapshot(fingerprint)["top_n"] == 3

    def test_unknown_fingerprint_is_none(self, db):
        assert db.get_config_snapshot("deadbeef") is None

    def test_active_config_round_trip(self, db):
        config = ScreenerConfig(watchlist=["TSLA"], posture="sell", top_n=8)
        db.save_active_config(config)
        assert db.load_active_config().fingerprint == config.fingerprint

    def test_active_config_defaults_when_unset(self, db):
        assert db.load_active_config().watchlist == ScreenerConfig().watchlist

    def test_corrupt_active_config_falls_back(self, db):
        """A damaged stored config must not make the dashboard unusable."""
        db.set_setting("active_config", "{not json")
        assert db.load_active_config().fingerprint == ScreenerConfig().fingerprint


def make_score(symbol="AAPL", rank=1) -> TickerScore:
    param = ParameterResult(
        key="rsi", label="RSI (14)", layer="stock", status="ok", weight=8.0,
        signal=0.6, raw_value=28.4, display_value="28.4", contribution=7.2,
        effective_weight=25.0, explanation="Oversold.", detail={"oversold": 30.0},
    )
    score = TickerScore(
        symbol=symbol, composite=72.5, direction="call", direction_label="Call lean",
        conviction=0.62, net_signal=0.62, parameters=[param], spot=180.5,
        dollar_volume=1.2e9, contract={"strike": 180.0, "kind": "call"},
        options_notes=["note"], tie_break_note=None,
    )
    score.rank = rank
    return score


class TestRuns:
    def test_full_run_round_trip(self, db):
        config = ScreenerConfig()
        run_id = db.start_run("manual", config, "synthetic")
        db.save_ranking(run_id, [make_score()])
        db.finish_run(run_id, "ok")

        ranking = db.get_ranking(run_id)
        assert len(ranking) == 1
        entry = ranking[0]
        assert entry["symbol"] == "AAPL"
        assert entry["composite"] == pytest.approx(72.5)
        assert entry["contract"]["strike"] == 180.0
        assert entry["options_notes"] == ["note"]

    def test_parameter_breakdown_is_preserved(self, db):
        """The reasoning must survive storage — it is the point of the tool."""
        run_id = db.start_run("manual", ScreenerConfig(), "synthetic")
        db.save_ranking(run_id, [make_score()])
        db.finish_run(run_id, "ok")
        param = db.get_ranking(run_id)[0]["parameters"][0]
        assert param["explanation"] == "Oversold."
        assert param["contribution"] == pytest.approx(7.2)
        assert param["display_value"] == "28.4"
        assert param["detail"]["oversold"] == 30.0

    def test_running_runs_are_hidden_from_latest(self, db):
        db.start_run("manual", ScreenerConfig(), "synthetic")
        assert db.latest_run(only_successful=True) is None

    def test_failed_runs_are_hidden_from_latest(self, db):
        run_id = db.start_run("manual", ScreenerConfig(), "synthetic")
        db.finish_run(run_id, "failed", "nothing scored")
        assert db.latest_run(only_successful=True) is None
        assert db.latest_run(only_successful=False)["status"] == "failed"

    def test_latest_returns_the_newest_successful_run(self, db):
        config = ScreenerConfig()
        first = db.start_run("manual", config, "synthetic")
        db.finish_run(first, "ok")
        second = db.start_run("market_close", config, "synthetic")
        db.finish_run(second, "ok")
        assert db.latest_run()["id"] == second

    def test_list_runs_includes_entry_counts(self, db):
        run_id = db.start_run("manual", ScreenerConfig(), "synthetic")
        db.save_ranking(run_id, [make_score("AAPL", 1), make_score("MSFT", 2)])
        db.finish_run(run_id, "ok")
        assert db.list_runs()[0]["entry_count"] == 2

    def test_run_records_its_config_fingerprint(self, db):
        config = ScreenerConfig(top_n=9)
        run_id = db.start_run("manual", config, "synthetic")
        assert db.get_run(run_id)["fingerprint"] == config.fingerprint

    def test_unknown_run_is_none(self, db):
        assert db.get_run(999) is None

    def test_parameters_come_back_in_registry_order(self, db):
        from screener.param_specs import ALL_SPECS

        score = make_score()
        score.parameters = [
            ParameterResult(key=spec.key, label=spec.label, layer=spec.layer,
                            status="ok", weight=1.0, signal=0.5, contribution=1.0,
                            explanation="x")
            for spec in reversed(ALL_SPECS)
        ]
        run_id = db.start_run("manual", ScreenerConfig(), "synthetic")
        db.save_ranking(run_id, [score])
        db.finish_run(run_id, "ok")
        keys = [p["param_key"] for p in db.get_ranking(run_id)[0]["parameters"]]
        assert keys == [s.key for s in ALL_SPECS]


class TestForwardReturns:
    def test_saved_returns_attach_to_entries(self, db):
        run_id = db.start_run("manual", ScreenerConfig(), "synthetic")
        db.save_ranking(run_id, [make_score()])
        db.finish_run(run_id, "ok")
        db.save_forward_returns([(run_id, "AAPL", 5, 100.0, 105.0, 5.0, 5.0, "2026-03-16")])
        assert db.get_ranking(run_id)[0]["forward_returns"][0]["return_pct"] == 5.0

    def test_runs_needing_returns_excludes_complete_ones(self, db):
        run_id = db.start_run("manual", ScreenerConfig(), "synthetic")
        db.save_ranking(run_id, [make_score()])
        db.finish_run(run_id, "ok")
        assert any(r["id"] == run_id for r in db.runs_needing_forward_returns((1,)))
        db.save_forward_returns([(run_id, "AAPL", 1, 100.0, 101.0, 1.0, 1.0, "2026-03-12")])
        assert not any(r["id"] == run_id for r in db.runs_needing_forward_returns((1,)))


def test_schema_survives_reopening(tmp_path):
    path = tmp_path / "reopen.sqlite"
    first = Database(path)
    first.upsert_prices("AAPL", make_frame(trending(5)))
    reopened = Database(path)
    assert len(reopened.get_prices("AAPL")) == 5


def test_creates_parent_directory(tmp_path):
    db = Database(tmp_path / "nested" / "deep" / "screener.sqlite")
    assert db.path.parent.exists()


class TestUnwritableDatabase:
    """A mounted volume the process does not own is the common deploy failure.

    SQLite reports it as a bare "unable to open database file", naming neither
    the path nor the cause, so it is translated into something diagnosable.
    """

    def test_missing_directory_is_reported_as_missing(self):
        from screener.db import DatabaseUnwritable

        message = str(DatabaseUnwritable(Path("/nonexistent-xyz/a.sqlite"), OSError("x")))
        assert "does not exist" in message
        assert "/nonexistent-xyz/a.sqlite" in message

    def test_message_names_the_running_user_and_the_cause(self):
        from screener.db import DatabaseUnwritable

        message = str(DatabaseUnwritable(Path("/nonexistent-xyz/a.sqlite"),
                                         OSError("Permission denied")))
        assert "Running as" in message
        assert "Permission denied" in message

    def test_message_explains_the_volume_case(self):
        from screener.db import DatabaseUnwritable

        message = str(DatabaseUnwritable(Path("/data/a.sqlite"), OSError("x")))
        assert "volume" in message
        assert "SCREENER_DB" in message

    def test_does_not_claim_missing_when_merely_unreadable(self, tmp_path, monkeypatch):
        """Reporting 'does not exist' for an unreadable directory would send
        you hunting the wrong problem."""
        from screener.db import DatabaseUnwritable

        target = tmp_path / "vol"
        target.mkdir()

        def deny(self):
            raise PermissionError("denied")

        monkeypatch.setattr(Path, "stat", deny)
        message = str(DatabaseUnwritable(target / "a.sqlite", OSError("x")))
        assert "does not exist" not in message
        assert "denies access" in message

    def test_a_writable_path_still_opens(self, tmp_path):
        assert Database(tmp_path / "nested" / "ok.sqlite").path.exists()
