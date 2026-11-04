"""End-to-end pipeline tests against the synthetic provider."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from screener.config import ScreenerConfig
from screener.pipeline import RefreshPipeline, build_provider
from screener.providers.base import empty_price_frame
from screener.providers.synthetic import SyntheticProvider
from tests.conftest import TEST_TODAY


@pytest.fixture
def pipeline(db, provider) -> RefreshPipeline:
    return RefreshPipeline(db, provider, today=TEST_TODAY)


class TestRun:
    def test_produces_a_complete_ranking(self, pipeline, db):
        config = ScreenerConfig(watchlist=["AAPL", "MSFT", "NVDA"])
        summary = pipeline.run(config, trigger="cli")
        assert summary.status == "ok"
        assert len(summary.scored) == 3

        ranking = db.get_ranking(summary.run_id)
        assert [e["rank"] for e in ranking] == [1, 2, 3]
        assert ranking[0]["composite"] >= ranking[-1]["composite"]

    def test_every_entry_carries_full_reasoning(self, pipeline, db):
        """The whole ranked list must be explainable, not just the leaders."""
        summary = pipeline.run(ScreenerConfig(watchlist=["AAPL", "MSFT", "NVDA"]))
        for entry in db.get_ranking(summary.run_id):
            assert entry["parameters"], f"{entry['symbol']} has no breakdown"
            for param in entry["parameters"]:
                assert param["explanation"]
                assert param["status"] in {"ok", "insufficient_data", "not_applicable"}

    def test_scores_are_bounded(self, pipeline, db):
        summary = pipeline.run(ScreenerConfig(watchlist=["AAPL", "MSFT", "NVDA", "TSLA"]))
        for entry in db.get_ranking(summary.run_id):
            assert 0.0 <= entry["composite"] <= 100.0

    def test_contributions_sum_to_the_composite(self, pipeline, db):
        summary = pipeline.run(ScreenerConfig(watchlist=["AAPL", "MSFT"]))
        for entry in db.get_ranking(summary.run_id):
            total = sum(p["contribution"] for p in entry["parameters"])
            assert total == pytest.approx(entry["composite"], abs=0.05)

    def test_unknown_ticker_is_skipped_not_fatal(self, pipeline, db):
        config = ScreenerConfig(watchlist=["AAPL", "BADTICK", "MSFT"])
        summary = pipeline.run(config)
        assert summary.status == "partial"
        assert "BADTICK" in summary.failed
        assert len(summary.scored) == 2
        assert db.get_run(summary.run_id)["message"]

    def test_run_of_only_bad_tickers_fails_cleanly(self, pipeline, db):
        summary = pipeline.run(ScreenerConfig(watchlist=["BADTICK", "DELISTED"]))
        assert summary.status == "failed"
        assert db.latest_run(only_successful=True) is None

    def test_ticker_without_a_chain_still_ranks(self, pipeline, db):
        """Missing options data must degrade one ticker, not exclude it."""
        summary = pipeline.run(ScreenerConfig(watchlist=["AAPL", "NOCHAIN"]))
        entry = next(e for e in db.get_ranking(summary.run_id) if e["symbol"] == "NOCHAIN")
        # Asserting the missing set rather than a "7/9" fraction, so adding a
        # parameter later does not fail this test for the wrong reason.
        # No chain kills expiry fit, delta fit, and the volatility risk premium
        # (which needs implied volatility to compare against). IV Rank still
        # computes from the realised-volatility proxy; earnings is unaffected.
        missing = {p["param_key"] for p in entry["parameters"] if p["status"] != "ok"}
        assert missing == {"dte_fit", "delta_fit", "vol_risk_premium"}

        counted, considered = (int(x) for x in entry["completeness"].split("/"))
        assert considered - counted == len(missing)
        for param in entry["parameters"]:
            if param["status"] != "ok":
                assert param["explanation"]

    def test_short_history_ticker_reports_which_parameters_failed(self, pipeline, db):
        summary = pipeline.run(ScreenerConfig(watchlist=["AAPL", "NEWIPO"]))
        entry = next(e for e in db.get_ranking(summary.run_id) if e["symbol"] == "NEWIPO")
        ma200 = next(p for p in entry["parameters"] if p["param_key"] == "ma200")
        assert ma200["status"] == "insufficient_data"
        assert "200" in ma200["explanation"]

    def test_etf_earnings_are_not_applicable(self, pipeline, db):
        summary = pipeline.run(ScreenerConfig(watchlist=["SPY"]))
        entry = db.get_ranking(summary.run_id)[0]
        earnings = next(p for p in entry["parameters"] if p["param_key"] == "earnings")
        assert earnings["status"] == "not_applicable"

    def test_disabled_parameters_are_absent(self, pipeline, db):
        config = ScreenerConfig(watchlist=["AAPL"])
        config.parameters["volume"].weight = 0.0
        summary = pipeline.run(config)
        keys = {p["param_key"] for p in db.get_ranking(summary.run_id)[0]["parameters"]}
        assert "volume" not in keys

    def test_run_records_trigger_and_provider(self, pipeline, db):
        summary = pipeline.run(ScreenerConfig(watchlist=["AAPL"]), trigger="market_open")
        run = db.get_run(summary.run_id)
        assert run["trigger"] == "market_open"
        assert run["provider"] == "synthetic"

    def test_posture_changes_the_ranking(self, db, provider):
        """Buy and sell postures must genuinely score differently."""
        watchlist = ["AAPL", "MSFT", "NVDA", "TSLA", "AMD"]
        buying = RefreshPipeline(db, provider, today=TEST_TODAY).run(
            ScreenerConfig(watchlist=watchlist, posture="buy"))
        selling = RefreshPipeline(db, provider, today=TEST_TODAY).run(
            ScreenerConfig(watchlist=watchlist, posture="sell"))
        buy_scores = {e["symbol"]: e["composite"] for e in db.get_ranking(buying.run_id)}
        sell_scores = {e["symbol"]: e["composite"] for e in db.get_ranking(selling.run_id)}
        assert buy_scores != sell_scores

    def test_repeated_runs_are_deterministic(self, db, provider):
        config = ScreenerConfig(watchlist=["AAPL", "MSFT", "NVDA"])
        first = RefreshPipeline(db, provider, today=TEST_TODAY).run(config)
        second = RefreshPipeline(db, provider, today=TEST_TODAY).run(config)
        order_a = [e["symbol"] for e in db.get_ranking(first.run_id)]
        order_b = [e["symbol"] for e in db.get_ranking(second.run_id)]
        assert order_a == order_b


class TestIVAccumulation:
    def test_iv_is_recorded_for_rank_history(self, pipeline, db):
        pipeline.run(ScreenerConfig(watchlist=["AAPL"]))
        assert len(db.get_iv_history("AAPL")) == 1

    def test_iv_rank_starts_on_the_hv_proxy(self, pipeline, db):
        summary = pipeline.run(ScreenerConfig(watchlist=["AAPL"]))
        entry = db.get_ranking(summary.run_id)[0]
        iv = next(p for p in entry["parameters"] if p["param_key"] == "iv_rank")
        assert iv["detail"]["source"] == "hv_proxy"
        assert "realised volatility" in iv["explanation"]

    def test_switches_to_real_iv_once_history_accumulates(self, db, provider):
        """Seed 60 prior observations and confirm the source flips."""
        for offset in range(70, 5, -1):
            db.record_iv("AAPL", TEST_TODAY - timedelta(days=offset), 0.25 + offset / 1000, 30)
        summary = RefreshPipeline(db, provider, today=TEST_TODAY).run(
            ScreenerConfig(watchlist=["AAPL"]))
        entry = db.get_ranking(summary.run_id)[0]
        iv = next(p for p in entry["parameters"] if p["param_key"] == "iv_rank")
        assert iv["detail"]["source"] == "iv"


class TestPriceCaching:
    def test_prices_are_persisted(self, pipeline, db):
        pipeline.run(ScreenerConfig(watchlist=["AAPL"]))
        assert len(db.get_prices("AAPL")) > 200

    def test_warm_refresh_keeps_existing_history(self, db, provider):
        """A short warm fetch must not truncate the stored series."""
        config = ScreenerConfig(watchlist=["AAPL"])
        RefreshPipeline(db, provider, today=TEST_TODAY).run(config)
        first_count = len(db.get_prices("AAPL"))
        RefreshPipeline(db, provider, today=TEST_TODAY).run(config)
        assert len(db.get_prices("AAPL")) >= first_count

    def test_stored_history_survives_a_failed_fetch(self, db, provider):
        """A throttled fetch must fall back to storage, not blank the ticker."""
        config = ScreenerConfig(watchlist=["AAPL"])
        RefreshPipeline(db, provider, today=TEST_TODAY).run(config)

        class ThrottledProvider(SyntheticProvider):
            def price_history(self, symbol, lookback_days):
                return empty_price_frame()

        pipeline = RefreshPipeline(db, ThrottledProvider(today=TEST_TODAY), today=TEST_TODAY)
        summary = pipeline.run(config)
        assert summary.status == "ok"
        assert len(summary.scored) == 1


class TestEarningsCaching:
    def test_earnings_are_cached_after_first_fetch(self, pipeline, db):
        pipeline.run(ScreenerConfig(watchlist=["AAPL"]))
        assert db.get_earnings("AAPL") is not None

    def test_lookup_failure_falls_back_to_cache(self, db, provider):
        db.save_earnings("AAPL", date(2026, 5, 1), False)

        class BrokenProvider(SyntheticProvider):
            def is_etf(self, symbol):
                raise RuntimeError("network down")

        pipeline = RefreshPipeline(db, BrokenProvider(today=TEST_TODAY), today=TEST_TODAY)
        earnings_date, is_etf = pipeline._earnings("AAPL")
        assert earnings_date == date(2026, 5, 1)
        assert is_etf is False

    def test_lookup_failure_without_cache_is_survivable(self, db):
        class BrokenProvider(SyntheticProvider):
            def is_etf(self, symbol):
                raise RuntimeError("network down")

        pipeline = RefreshPipeline(db, BrokenProvider(today=TEST_TODAY), today=TEST_TODAY)
        assert pipeline._earnings("AAPL") == (None, False)


class TestScorerIsolation:
    def test_a_broken_scorer_does_not_lose_the_run(self, pipeline, db, monkeypatch):
        """One faulty parameter must degrade to 'no data', not abort scoring."""
        import screener.scoring.engine as engine_module

        def explode(data, config):
            raise ValueError("synthetic failure")

        monkeypatch.setitem(engine_module.STOCK_SCORERS, "rsi", explode)
        summary = pipeline.run(ScreenerConfig(watchlist=["AAPL"]))
        assert summary.status == "ok"
        entry = db.get_ranking(summary.run_id)[0]
        rsi = next(p for p in entry["parameters"] if p["param_key"] == "rsi")
        assert rsi["status"] == "insufficient_data"
        assert "synthetic failure" in rsi["explanation"]


class TestBuildProvider:
    def test_builds_synthetic(self):
        assert build_provider("synthetic").name == "synthetic"

    def test_rejects_unknown(self):
        with pytest.raises(ValueError, match="Unknown provider"):
            build_provider("bloomberg")
