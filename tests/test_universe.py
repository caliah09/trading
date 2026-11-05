"""Universe resolution, the wide-scan screen, and batch price fetching.

These cover the paths a wide scan adds. The batch fetch itself is never
exercised against the network — ``split_batch_frame`` is tested directly
against hand-built frames in each of the shapes yfinance actually returns,
which is the part that can be wrong without a network to tell you.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from screener.config import ConfigError, ScreenerConfig
from screener.pipeline import RefreshPipeline
from screener.providers.base import (
    PRICE_COLUMNS,
    empty_price_frame,
    fetch_price_history,
    sequential_price_history,
)
from screener.providers.yfinance_provider import split_batch_frame
from screener.universe import (
    UNIVERSE_NAMES,
    UNIVERSES,
    WATCHLIST_UNIVERSE,
    UnknownUniverse,
    describe,
    resolve,
)
from tests.conftest import TEST_TODAY


class TestResolve:
    def test_watchlist_universe_uses_the_watchlist(self):
        assert resolve(WATCHLIST_UNIVERSE, ["aapl", "msft"]) == ["AAPL", "MSFT"]

    def test_named_universe_ignores_the_watchlist(self):
        out = resolve("sectors", ["ZZZZ"])
        assert "ZZZZ" not in out
        assert "SPY" in out

    def test_extras_are_appended_to_any_universe(self):
        out = resolve("sectors", [], ["pltr"])
        assert out[-1] == "PLTR"

    def test_symbols_are_upper_cased_and_stripped(self):
        assert resolve(WATCHLIST_UNIVERSE, [" nvda ", "amd"]) == ["NVDA", "AMD"]

    def test_blank_entries_are_dropped(self):
        assert resolve(WATCHLIST_UNIVERSE, ["AAPL", "", "   "]) == ["AAPL"]

    def test_broad_deduplicates_across_constituent_lists(self):
        """AAPL is in the S&P and the Nasdaq-100; it must be scored once."""
        out = resolve("broad")
        assert out.count("AAPL") == 1
        assert len(out) == len(set(out))

    def test_order_is_stable(self):
        assert resolve("sp500") == resolve("sp500")

    def test_extras_do_not_duplicate_an_existing_member(self):
        out = resolve("sectors", [], ["SPY"])
        assert out.count("SPY") == 1

    def test_unknown_universe_names_the_alternatives(self):
        with pytest.raises(UnknownUniverse) as excinfo:
            resolve("everything")
        for name in UNIVERSE_NAMES:
            assert name in str(excinfo.value)

    @pytest.mark.parametrize("name", UNIVERSE_NAMES)
    def test_every_universe_resolves(self, name):
        resolve(name, ["AAPL"])

    @pytest.mark.parametrize("name", [n for n in UNIVERSE_NAMES if n != WATCHLIST_UNIVERSE])
    def test_static_universes_are_non_empty_and_clean(self, name):
        symbols = UNIVERSES[name].symbols
        assert symbols
        for sym in symbols:
            assert sym == sym.strip().upper()
            assert sym

    def test_describe_reports_the_resolved_size(self):
        meta = describe("sectors", [], ["PLTR"])
        assert meta["size"] == len(resolve("sectors", [], ["PLTR"]))
        assert meta["label"]
        assert meta["note"]


class TestConfigIntegration:
    def test_candidates_defaults_to_the_watchlist(self):
        config = ScreenerConfig(watchlist=["AAPL", "MSFT"])
        assert config.candidates() == ["AAPL", "MSFT"]

    def test_candidates_follows_the_selected_universe(self):
        config = ScreenerConfig(watchlist=["AAPL"], universe="sectors")
        assert len(config.candidates()) > 1
        assert "SPY" in config.candidates()

    def test_is_wide_scan_is_false_for_a_small_watchlist(self):
        assert ScreenerConfig(watchlist=["AAPL", "MSFT"]).is_wide_scan is False

    def test_is_wide_scan_is_true_for_a_broad_universe(self):
        assert ScreenerConfig(universe="broad", scan_depth=25).is_wide_scan is True

    def test_unknown_universe_is_rejected_at_construction(self):
        with pytest.raises(ConfigError):
            ScreenerConfig(universe="nonsense")

    def test_scan_depth_must_be_positive(self):
        with pytest.raises(ConfigError):
            ScreenerConfig(scan_depth=0)

    def test_negative_liquidity_floor_is_rejected(self):
        with pytest.raises(ConfigError):
            ScreenerConfig(min_scan_dollar_volume=-1.0)

    def test_empty_watchlist_universe_is_rejected_with_a_useful_message(self):
        with pytest.raises(ConfigError) as excinfo:
            ScreenerConfig(watchlist=[])
        assert "wider universe" in str(excinfo.value)

    def test_empty_watchlist_is_fine_when_a_named_universe_is_selected(self):
        config = ScreenerConfig(watchlist=[], universe="sectors")
        assert config.candidates()

    def test_extra_symbols_are_normalised(self):
        config = ScreenerConfig(watchlist=["AAPL"], extra_symbols=[" pltr ", "PLTR"])
        assert config.extra_symbols == ["PLTR"]

    def test_universe_settings_round_trip(self):
        config = ScreenerConfig(
            watchlist=["AAPL"], universe="nasdaq100",
            extra_symbols=["PLTR"], scan_depth=12, min_scan_dollar_volume=2e6,
        )
        restored = ScreenerConfig.from_dict(config.to_dict())
        assert restored.universe == "nasdaq100"
        assert restored.extra_symbols == ["PLTR"]
        assert restored.scan_depth == 12
        assert restored.min_scan_dollar_volume == 2e6
        assert restored.fingerprint == config.fingerprint

    def test_widening_the_universe_changes_the_fingerprint(self):
        """Runs under different candidate pools must not look interchangeable."""
        narrow = ScreenerConfig(watchlist=["AAPL"])
        wide = ScreenerConfig(watchlist=["AAPL"], universe="broad")
        assert narrow.fingerprint != wide.fingerprint


# --------------------------------------------------------------------------
# Batch fetching
# --------------------------------------------------------------------------

def _frame(n: int = 5, base: float = 100.0) -> pd.DataFrame:
    index = pd.date_range(end=pd.Timestamp(TEST_TODAY), periods=n, freq="D")
    close = np.linspace(base, base * 1.05, n)
    return pd.DataFrame(
        {
            "Open": close, "High": close * 1.01, "Low": close * 0.99,
            "Close": close, "Volume": np.full(n, 1_000_000.0),
        },
        index=index,
    )


def _multi(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """Build the MultiIndex shape yf.download returns for several tickers."""
    return pd.concat(frames, axis=1)


class TestSplitBatchFrame:
    def test_splits_a_multiindex_response(self):
        raw = _multi({"AAPL": _frame(), "MSFT": _frame(base=200.0)})
        out = split_batch_frame(raw, ["AAPL", "MSFT"])
        assert set(out) == {"AAPL", "MSFT"}
        for frame in out.values():
            assert list(frame.columns) == list(PRICE_COLUMNS)
            assert not frame.empty

    def test_attributes_rows_to_the_right_symbol(self):
        """A transposed split would be silently catastrophic, so pin it."""
        raw = _multi({"AAPL": _frame(base=100.0), "MSFT": _frame(base=200.0)})
        out = split_batch_frame(raw, ["AAPL", "MSFT"])
        assert out["AAPL"]["close"].iloc[0] == pytest.approx(100.0)
        assert out["MSFT"]["close"].iloc[0] == pytest.approx(200.0)

    def test_missing_symbol_maps_to_an_empty_frame(self):
        raw = _multi({"AAPL": _frame()})
        out = split_batch_frame(raw, ["AAPL", "DELISTED"])
        assert not out["AAPL"].empty
        assert out["DELISTED"].empty

    def test_all_nan_rows_become_an_empty_frame(self):
        """Yahoo accepts some tickers and returns nothing but NaN for them."""
        blank = _frame()
        blank[:] = np.nan
        raw = _multi({"AAPL": _frame(), "GHOST": blank})
        out = split_batch_frame(raw, ["AAPL", "GHOST"])
        assert out["GHOST"].empty

    def test_single_ticker_flat_columns(self):
        """One surviving ticker loses the ticker level entirely."""
        out = split_batch_frame(_frame(), ["AAPL"])
        assert list(out["AAPL"].columns) == list(PRICE_COLUMNS)
        assert not out["AAPL"].empty

    def test_flat_columns_for_many_symbols_is_refused_not_guessed(self):
        """Rows that cannot be attributed must not be handed to an arbitrary symbol."""
        out = split_batch_frame(_frame(), ["AAPL", "MSFT"])
        assert out["AAPL"].empty
        assert out["MSFT"].empty

    def test_every_requested_symbol_gets_an_entry(self):
        raw = _multi({"AAPL": _frame()})
        out = split_batch_frame(raw, ["AAPL", "X", "Y", "Z"])
        assert set(out) == {"AAPL", "X", "Y", "Z"}


class _Recorder:
    """A provider stub that records how it was called."""

    name = "recorder"

    def __init__(self, batch=None):
        self.singles: list[str] = []
        self.batch_calls = 0
        self._batch = batch
        if batch is not None:
            self.price_history_batch = self._run_batch

    def price_history(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        self.singles.append(symbol)
        if symbol == "BOOM":
            raise RuntimeError("provider exploded")
        return empty_price_frame()

    def _run_batch(self, symbols: list[str], lookback_days: int):
        self.batch_calls += 1
        return self._batch(symbols)


class TestFetchPriceHistory:
    def test_uses_the_batch_path_when_available(self):
        provider = _Recorder(batch=lambda syms: {s: empty_price_frame() for s in syms})
        fetch_price_history(provider, ["AAPL", "MSFT"], 400)
        assert provider.batch_calls == 1
        assert provider.singles == []

    def test_falls_back_to_sequential_without_a_batch_method(self):
        provider = _Recorder()
        out = fetch_price_history(provider, ["AAPL", "MSFT"], 400)
        assert provider.singles == ["AAPL", "MSFT"]
        assert set(out) == {"AAPL", "MSFT"}

    def test_a_raising_batch_falls_back_rather_than_failing_the_run(self):
        def explode(symbols):
            raise RuntimeError("upstream threw")

        provider = _Recorder(batch=explode)
        out = fetch_price_history(provider, ["AAPL", "MSFT"], 400)
        assert provider.batch_calls == 1
        assert provider.singles == ["AAPL", "MSFT"]
        assert set(out) == {"AAPL", "MSFT"}

    def test_a_partial_batch_is_topped_up_individually(self):
        provider = _Recorder(batch=lambda syms: {"AAPL": empty_price_frame()})
        out = fetch_price_history(provider, ["AAPL", "MSFT"], 400)
        assert provider.singles == ["MSFT"]
        assert set(out) == {"AAPL", "MSFT"}

    def test_one_exploding_symbol_does_not_take_down_the_others(self):
        provider = _Recorder()
        out = sequential_price_history(provider, ["AAPL", "BOOM", "MSFT"], 400)
        assert set(out) == {"AAPL", "BOOM", "MSFT"}
        assert out["BOOM"].empty

    def test_empty_request_short_circuits(self):
        provider = _Recorder()
        assert fetch_price_history(provider, [], 400) == {}
        assert provider.singles == []


# --------------------------------------------------------------------------
# The screen
# --------------------------------------------------------------------------

@pytest.fixture
def pipeline(db, provider) -> RefreshPipeline:
    return RefreshPipeline(db, provider, today=TEST_TODAY)


class TestScreen:
    def test_a_watchlist_smaller_than_scan_depth_is_not_screened(self, pipeline, db):
        config = ScreenerConfig(watchlist=["AAPL", "MSFT", "NVDA"], scan_depth=25)
        summary = pipeline.run(config)
        assert summary.screened == 3
        assert len(summary.scored) == 3
        assert summary.filtered == {}

    def test_a_wide_universe_is_narrowed_to_scan_depth(self, pipeline, db):
        config = ScreenerConfig(
            watchlist=[], universe="sectors", scan_depth=5, min_scan_dollar_volume=0.0,
        )
        summary = pipeline.run(config)
        assert summary.screened > 5
        assert len(summary.scored) == 5
        assert len(db.get_ranking(summary.run_id)) == 5

    def test_screened_out_names_are_reported_with_a_reason(self, pipeline):
        config = ScreenerConfig(
            watchlist=[], universe="sectors", scan_depth=5, min_scan_dollar_volume=0.0,
        )
        summary = pipeline.run(config)
        assert summary.filtered
        for reason in summary.filtered.values():
            assert reason.strip()

    def test_the_liquidity_floor_excludes_thin_names(self, pipeline):
        """An unreachable floor must drop everything rather than pass it through."""
        config = ScreenerConfig(
            watchlist=[], universe="sectors", scan_depth=5,
            min_scan_dollar_volume=1e15,
        )
        summary = pipeline.run(config)
        assert summary.status == "failed"
        assert len(summary.filtered) == summary.screened
        for reason in summary.filtered.values():
            assert "scan floor" in reason

    def test_no_ranked_entry_is_stock_layer_only(self, pipeline, db):
        """The point of filtering rather than partially scoring.

        A name that only went through the screen is composed with
        ``options_results=[]``, so it carries no options-layer parameters at
        all. If one of those reached the ranking it would sit beside fully
        scored entries with its stock weights redistributed to 100% — a
        number that looks comparable and is not.
        """
        config = ScreenerConfig(
            watchlist=[], universe="sectors", scan_depth=4, min_scan_dollar_volume=0.0,
        )
        summary = pipeline.run(config)
        ranking = db.get_ranking(summary.run_id)
        assert ranking

        for entry in ranking:
            layers = {p["layer"] for p in entry["parameters"]}
            assert "options" in layers, (
                f"{entry['symbol']} reached the ranking without the options layer"
            )
            assert "stock" in layers

    def test_screened_out_names_never_reach_the_ranking(self, pipeline, db):
        config = ScreenerConfig(
            watchlist=[], universe="sectors", scan_depth=4, min_scan_dollar_volume=0.0,
        )
        summary = pipeline.run(config)
        ranked = {e["symbol"] for e in db.get_ranking(summary.run_id)}
        assert ranked.isdisjoint(set(summary.filtered))

    def test_the_screen_is_deterministic(self, db, provider):
        config = ScreenerConfig(
            watchlist=[], universe="sectors", scan_depth=5, min_scan_dollar_volume=0.0,
        )
        first = RefreshPipeline(db, provider, today=TEST_TODAY).run(config)
        second = RefreshPipeline(db, provider, today=TEST_TODAY).run(config)
        assert first.scored == second.scored

    def test_the_run_message_reports_the_breadth(self, pipeline, db):
        config = ScreenerConfig(
            watchlist=[], universe="sectors", scan_depth=5, min_scan_dollar_volume=0.0,
        )
        summary = pipeline.run(config)
        run = db.get_run(summary.run_id)
        assert "Screened" in (run["message"] or "")

    def test_summary_serialises_the_scan_accounting(self, pipeline):
        config = ScreenerConfig(
            watchlist=[], universe="sectors", scan_depth=5, min_scan_dollar_volume=0.0,
        )
        payload = pipeline.run(config).to_dict()
        assert payload["screened"] > 5
        assert isinstance(payload["filtered"], dict)
