"""Tests for chain hygiene, IV sampling and contract selection."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from screener.config import ScreenerConfig
from screener.options_analysis import (
    IV_REFERENCE_DTE,
    analyse_options,
    atm_implied_volatility,
    select_contract,
)
from screener.providers.base import ChainSnapshot, empty_chain_frame
from screener.providers.synthetic import SyntheticProvider
from tests.conftest import TEST_TODAY


@pytest.fixture
def config() -> ScreenerConfig:
    return ScreenerConfig()


def chain(strikes, iv=0.3, open_interest=1000, bid=4.9, ask=5.1,
          expiry=None) -> ChainSnapshot:
    expiry = expiry or TEST_TODAY + timedelta(days=60)
    frame = pd.DataFrame({
        "contract_symbol": [f"T{i}" for i in range(len(strikes))],
        "strike": strikes,
        "bid": [bid] * len(strikes),
        "ask": [ask] * len(strikes),
        "last_price": [(bid + ask) / 2] * len(strikes),
        "volume": [100.0] * len(strikes),
        "open_interest": [float(open_interest)] * len(strikes),
        "implied_volatility": [iv] * len(strikes),
    })
    return ChainSnapshot(symbol="T", expiry=expiry, calls=frame, puts=frame.copy())


class TestATMImpliedVolatility:
    def test_picks_the_strike_nearest_spot(self):
        frame = pd.DataFrame({
            "strike": [90.0, 100.0, 110.0],
            "implied_volatility": [0.50, 0.30, 0.55],
        })
        snapshot = ChainSnapshot(symbol="T", expiry=TEST_TODAY, calls=frame, puts=empty_chain_frame())
        assert atm_implied_volatility(snapshot, 101.0) == pytest.approx(0.30)

    def test_averages_calls_and_puts(self):
        calls = pd.DataFrame({"strike": [100.0], "implied_volatility": [0.30]})
        puts = pd.DataFrame({"strike": [100.0], "implied_volatility": [0.40]})
        snapshot = ChainSnapshot(symbol="T", expiry=TEST_TODAY, calls=calls, puts=puts)
        assert atm_implied_volatility(snapshot, 100.0) == pytest.approx(0.35)

    def test_empty_chain_returns_none(self):
        snapshot = ChainSnapshot(symbol="T", expiry=TEST_TODAY)
        assert atm_implied_volatility(snapshot, 100.0) is None

    def test_all_nan_implied_volatility_returns_none(self):
        frame = pd.DataFrame({"strike": [100.0], "implied_volatility": [np.nan]})
        snapshot = ChainSnapshot(symbol="T", expiry=TEST_TODAY, calls=frame,
                                 puts=empty_chain_frame())
        assert atm_implied_volatility(snapshot, 100.0) is None


class TestSelectContract:
    def test_picks_delta_closest_to_target(self, config):
        strikes = [80.0, 90.0, 100.0, 110.0, 120.0]
        result = select_contract(chain(strikes), 100.0, "call", config, TEST_TODAY)
        assert result is not None
        # Buying posture targets |delta| 0.45-0.60.
        assert 0.35 <= abs(result.delta) <= 0.70

    def test_returns_none_for_an_empty_chain(self, config):
        snapshot = ChainSnapshot(symbol="T", expiry=TEST_TODAY + timedelta(days=60))
        assert select_contract(snapshot, 100.0, "call", config, TEST_TODAY) is None

    def test_rejects_thin_open_interest(self, config):
        """A chain nobody trades should produce no suggestion at all."""
        thin = chain([95.0, 100.0, 105.0], open_interest=1)
        assert select_contract(thin, 100.0, "call", config, TEST_TODAY) is None

    def test_rejects_wide_spreads(self, config):
        wide = chain([95.0, 100.0, 105.0], bid=1.0, ask=9.0)
        assert select_contract(wide, 100.0, "call", config, TEST_TODAY) is None

    def test_rejects_expired_chain(self, config):
        expired = chain([100.0], expiry=TEST_TODAY - timedelta(days=1))
        assert select_contract(expired, 100.0, "call", config, TEST_TODAY) is None

    def test_missing_implied_volatility_is_skipped(self, config):
        snapshot = chain([95.0, 100.0, 105.0])
        snapshot.calls.loc[:, "implied_volatility"] = np.nan
        assert select_contract(snapshot, 100.0, "call", config, TEST_TODAY) is None

    def test_falls_back_to_last_price_when_quotes_are_absent(self, config):
        """Outside market hours Yahoo returns zero bid/ask; last price keeps it usable."""
        snapshot = chain([95.0, 100.0, 105.0], bid=0.0, ask=0.0)
        snapshot.calls.loc[:, "last_price"] = 5.0
        result = select_contract(snapshot, 100.0, "call", config, TEST_TODAY)
        assert result is not None
        assert result.mid == pytest.approx(5.0)

    def test_selects_from_the_requested_side(self, config):
        result = select_contract(chain([95.0, 100.0, 105.0]), 100.0, "put", config, TEST_TODAY)
        assert result.kind == "put"
        assert result.delta < 0

    def test_ties_prefer_more_open_interest(self, config):
        snapshot = chain([100.0, 100.0])
        snapshot.calls.loc[0, "open_interest"] = 50.0
        snapshot.calls.loc[1, "open_interest"] = 9000.0
        result = select_contract(snapshot, 100.0, "call", config, TEST_TODAY)
        assert result.open_interest == 9000.0

    def test_reports_spread_percentage(self, config):
        # bid 4.75 / ask 5.25 -> mid 5.00, spread 0.50 = 10%, inside the 25% limit.
        result = select_contract(chain([100.0], bid=4.75, ask=5.25), 100.0, "call",
                                 config, TEST_TODAY)
        assert result.spread_pct == pytest.approx(10.0)

    def test_spread_limit_is_configurable(self):
        """A 40% spread passes only if the user raises their own limit."""
        strict = ScreenerConfig(max_spread_pct=25.0)
        lenient = ScreenerConfig(max_spread_pct=50.0)
        wide = chain([100.0], bid=4.0, ask=6.0)
        assert select_contract(wide, 100.0, "call", strict, TEST_TODAY) is None
        result = select_contract(wide, 100.0, "call", lenient, TEST_TODAY)
        assert result is not None
        assert result.spread_pct == pytest.approx(40.0)


class TestAnalyseOptions:
    def test_samples_iv_at_the_reference_tenor(self, config, provider):
        """IV Rank is only meaningful if every sample uses the same tenor."""
        result = analyse_options(provider, "AAPL", 150.0, config, "call", TEST_TODAY)
        assert result.atm_iv is not None
        assert abs(result.iv_reference_dte - IV_REFERENCE_DTE) <= 10

    def test_no_chain_is_reported_with_a_reason(self, config, provider):
        result = analyse_options(provider, "NOCHAIN", 100.0, config, "call", TEST_TODAY)
        assert not result.has_chain
        assert result.selected is None
        assert any("no listed options" in note.lower() for note in result.notes)

    def test_missing_spot_short_circuits(self, config, provider):
        result = analyse_options(provider, "AAPL", None, config, "call", TEST_TODAY)
        assert result.selected is None
        assert any("underlying price" in note for note in result.notes)

    def test_selects_a_contract_on_a_healthy_chain(self, config, provider):
        result = analyse_options(provider, "AAPL", 150.0, config, "call", TEST_TODAY)
        assert result.selected is not None
        assert result.selected.kind == "call"
        assert result.best_dte is not None

    def test_respects_the_requested_direction(self, config, provider):
        result = analyse_options(provider, "AAPL", 150.0, config, "put", TEST_TODAY)
        assert result.selected.kind == "put"

    def test_prefers_an_expiry_inside_the_target_window(self, config, provider):
        result = analyse_options(provider, "MSFT", 200.0, config, "call", TEST_TODAY)
        target = config.target()
        assert target.dte_low - 20 <= result.best_dte <= target.dte_high + 20

    def test_posture_changes_the_selected_contract(self, provider):
        """Selling targets a much lower delta than buying."""
        buying = analyse_options(provider, "AAPL", 150.0, ScreenerConfig(posture="buy"),
                                 "call", TEST_TODAY)
        selling = analyse_options(provider, "AAPL", 150.0, ScreenerConfig(posture="sell"),
                                  "call", TEST_TODAY)
        assert abs(buying.selected.delta) > abs(selling.selected.delta)

    def test_expired_expiries_are_excluded(self, config):
        class PastExpiryProvider(SyntheticProvider):
            def option_expiries(self, symbol):
                return [TEST_TODAY - timedelta(days=5), TEST_TODAY]

        result = analyse_options(PastExpiryProvider(today=TEST_TODAY), "AAPL", 150.0,
                                 config, "call", TEST_TODAY)
        assert result.expiry_count == 0
