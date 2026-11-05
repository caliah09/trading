"""Contract tests against the installed yfinance.

**Why these exist.** Everything else in this suite runs against the synthetic
provider, which proves the scoring engine is correct but says nothing about
whether the *real* provider still speaks the library's language. yfinance is an
unofficial scraper whose API moves between versions — this project was written
against 0.2.x and later ran against 1.8.0, a major version jump.

These tests assert the specific surface `YFinanceProvider` depends on, so an
upgrade that moves it fails here, loudly, instead of at the first live fetch
against a real watchlist.

**No network.** They inspect the installed package and exercise the
normalisation functions against frames shaped like yfinance's real output.
Nothing here contacts Yahoo.
"""

from __future__ import annotations

import inspect

import numpy as np
import pandas as pd
import pytest

yfinance = pytest.importorskip("yfinance", reason="yfinance is not installed")

from screener.providers.yfinance_provider import (  # noqa: E402
    _iter_calendar_dates,
    _normalise_chain,
    _normalise_prices,
)

#: Columns yfinance's option chain is documented to return.
CHAIN_COLUMNS = [
    "contractSymbol", "lastTradeDate", "strike", "lastPrice", "bid", "ask",
    "change", "percentChange", "volume", "openInterest", "impliedVolatility",
    "inTheMoney", "contractSize", "currency",
]

#: Columns `Ticker.history()` returns, capitalised as yfinance emits them.
PRICE_COLUMNS = ["Open", "High", "Low", "Close", "Volume"]


class TestApiSurface:
    def test_ticker_has_the_methods_the_provider_calls(self):
        for name in ("history", "option_chain"):
            assert callable(getattr(yfinance.Ticker, name, None)), f"Ticker.{name} is gone"

    def test_ticker_has_the_properties_the_provider_reads(self):
        for name in ("options", "calendar", "earnings_dates", "info"):
            assert hasattr(yfinance.Ticker, name), f"Ticker.{name} is gone"

    def test_history_still_accepts_every_keyword_we_pass(self):
        """A dropped kwarg would silently change what data we store."""
        from yfinance.scrapers.history import PriceHistory

        accepted = set(inspect.signature(PriceHistory.history).parameters)
        for keyword in ("period", "interval", "auto_adjust", "actions", "raise_errors"):
            assert keyword in accepted, f"history() no longer accepts {keyword!r}"

    def test_option_chain_signature_takes_a_date(self):
        signature = inspect.signature(yfinance.Ticker.option_chain)
        assert "date" in signature.parameters

    def test_option_chain_result_exposes_calls_and_puts(self):
        """The provider reads .calls/.puts off whatever comes back."""
        source = inspect.getsource(yfinance.Ticker.option_chain)
        assert "calls" in source and "puts" in source

    def test_auto_adjust_is_available(self):
        """Indicators need a split-adjusted continuous series."""
        from yfinance.scrapers.history import PriceHistory

        assert "auto_adjust" in inspect.signature(PriceHistory.history).parameters


class TestPriceNormalisation:
    def _frame(self, rows: int = 5) -> pd.DataFrame:
        index = pd.date_range("2026-01-02", periods=rows, freq="D", tz="America/New_York")
        return pd.DataFrame(
            {c: np.linspace(100, 110, rows) for c in PRICE_COLUMNS}, index=index
        )

    def test_capitalised_columns_are_lowercased(self):
        result = _normalise_prices(self._frame())
        assert list(result.columns) == ["open", "high", "low", "close", "volume"]

    def test_timezone_is_stripped(self):
        """SQLite stores dates as strings; a tz-aware index breaks comparisons."""
        result = _normalise_prices(self._frame())
        assert result.index.tz is None

    def test_multiindex_columns_are_flattened(self):
        """Some versions return MultiIndex columns even for a single ticker."""
        frame = self._frame()
        frame.columns = pd.MultiIndex.from_product([frame.columns, ["AAPL"]])
        result = _normalise_prices(frame)
        assert list(result.columns) == ["open", "high", "low", "close", "volume"]

    def test_duplicate_dates_keep_the_last(self):
        frame = pd.concat([self._frame(2), self._frame(2)])
        assert len(_normalise_prices(frame)) == 2

    def test_rows_without_a_close_are_dropped(self):
        frame = self._frame()
        frame.loc[frame.index[0], "Close"] = np.nan
        assert len(_normalise_prices(frame)) == 4

    def test_index_is_sorted(self):
        frame = self._frame().iloc[::-1]
        assert _normalise_prices(frame).index.is_monotonic_increasing

    def test_unexpected_schema_raises_rather_than_guessing(self):
        from screener.providers.base import ProviderError

        with pytest.raises(ProviderError, match="unexpected price schema"):
            _normalise_prices(pd.DataFrame({"Close": [1.0], "Nonsense": [2.0]}))


class TestChainNormalisation:
    def _chain(self, rows: int = 3) -> pd.DataFrame:
        return pd.DataFrame({
            "contractSymbol": [f"AAPL260116C0020000{i}" for i in range(rows)],
            "lastTradeDate": pd.to_datetime(["2026-01-02"] * rows),
            "strike": np.linspace(195, 205, rows),
            "lastPrice": np.linspace(5, 6, rows),
            "bid": np.linspace(4.9, 5.9, rows),
            "ask": np.linspace(5.1, 6.1, rows),
            "change": [0.1] * rows,
            "percentChange": [1.0] * rows,
            "volume": [100.0] * rows,
            "openInterest": [1000.0] * rows,
            "impliedVolatility": [0.31] * rows,
            "inTheMoney": [False] * rows,
            "contractSize": ["REGULAR"] * rows,
            "currency": ["USD"] * rows,
        })

    def test_real_schema_maps_onto_our_columns(self):
        """The rename map must match the columns yfinance actually emits."""
        result = _normalise_chain(self._chain())
        assert "contract_symbol" in result.columns
        assert "implied_volatility" in result.columns
        assert "open_interest" in result.columns
        assert "last_price" in result.columns
        assert len(result) == 3

    def test_none_chain_yields_an_empty_frame(self):
        """yfinance returns calls=None when a ticker has no chain at all."""
        assert _normalise_chain(None).empty

    def test_empty_chain_yields_an_empty_frame(self):
        assert _normalise_chain(pd.DataFrame()).empty

    def test_zero_implied_volatility_becomes_missing(self):
        """A stale strike quoting 0.0 IV is 'no quote', not 'volatility is zero'."""
        frame = self._chain()
        frame.loc[0, "impliedVolatility"] = 0.0
        result = _normalise_chain(frame)
        assert pd.isna(result.loc[0, "implied_volatility"])

    def test_absurd_implied_volatility_becomes_missing(self):
        frame = self._chain()
        frame.loc[0, "impliedVolatility"] = 99.0
        assert pd.isna(_normalise_chain(frame).loc[0, "implied_volatility"])

    def test_missing_volume_becomes_zero_not_nan(self):
        """Overnight, volume is absent; zero is the truthful value."""
        frame = self._chain()
        frame.loc[0, "volume"] = np.nan
        assert _normalise_chain(frame).loc[0, "volume"] == 0.0

    def test_rows_without_a_strike_are_dropped(self):
        frame = self._chain()
        frame.loc[0, "strike"] = np.nan
        assert len(_normalise_chain(frame)) == 2

    def test_missing_columns_are_tolerated(self):
        """An older or trimmed schema must degrade, not explode."""
        frame = self._chain()[["contractSymbol", "strike", "bid", "ask"]]
        result = _normalise_chain(frame)
        assert len(result) == 3
        assert result["implied_volatility"].isna().all()


class TestCalendarParsing:
    def test_parses_the_dict_shape(self):
        """yfinance >= 1.0 returns calendar as a dict."""
        import datetime

        found = _iter_calendar_dates({"Earnings Date": [datetime.date(2026, 4, 30)]})
        assert found == [datetime.date(2026, 4, 30)]

    def test_parses_a_bare_value(self):
        import datetime

        assert _iter_calendar_dates({"Earnings Date": datetime.date(2026, 4, 30)})

    def test_parses_the_dataframe_shape(self):
        """Older versions returned a DataFrame."""
        frame = pd.DataFrame({"Value": [pd.Timestamp("2026-04-30")]}, index=["Earnings Date"])
        assert _iter_calendar_dates(frame)

    def test_unknown_shape_returns_empty(self):
        assert _iter_calendar_dates(None) == []
        assert _iter_calendar_dates("nonsense") == []

    def test_missing_key_returns_empty(self):
        assert _iter_calendar_dates({"Dividend Date": ["2026-04-30"]}) == []
