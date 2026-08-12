"""Tests for individual parameter scorers.

These assert *directional meaning*, not exact numbers: an oversold RSI must
lean bullish, an overbought one bearish, and a mid-range one neither. Pinning
exact scores would make the tests brittle against threshold tuning without
testing anything a user cares about.
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from screener.config import ScreenerConfig
from screener.options_analysis import ContractCandidate, OptionsAssessment
from screener.scoring import parameters as P
from screener.scoring.parameters import TickerData
from tests.conftest import TEST_TODAY, make_frame, trending


def data_for(closes, volumes=None, **kwargs) -> TickerData:
    return TickerData(
        symbol=kwargs.pop("symbol", "TEST"),
        prices=make_frame(closes, volumes),
        today=kwargs.pop("today", TEST_TODAY),
        **kwargs,
    )


@pytest.fixture
def config() -> ScreenerConfig:
    return ScreenerConfig()


# ==========================================================================
# RSI
# ==========================================================================

class TestRSI:
    def test_oversold_leans_bullish(self, config):
        result = P.score_rsi(data_for(trending(60, daily_pct=-1.2)), config)
        assert result.status == "ok"
        assert result.signal > 0.5
        assert "oversold" in result.explanation.lower()

    def test_overbought_leans_bearish(self, config):
        result = P.score_rsi(data_for(trending(60, daily_pct=1.2)), config)
        assert result.signal < -0.5
        assert "overbought" in result.explanation.lower()

    def test_midrange_is_neutral(self, config):
        """Alternating moves keep RSI near 50, which must net to no edge."""
        closes = [100.0 + (1.0 if i % 2 else -1.0) for i in range(60)]
        result = P.score_rsi(data_for(closes), config)
        assert abs(result.signal) < 0.1

    def test_signal_bounded(self, config):
        for pct in (-3.0, -0.5, 0.5, 3.0):
            result = P.score_rsi(data_for(trending(80, daily_pct=pct)), config)
            assert -1.0 <= result.signal <= 1.0

    def test_short_history_is_insufficient(self, config):
        result = P.score_rsi(data_for([100.0] * 5), config)
        assert result.status == "insufficient_data"
        assert "sessions" in result.explanation
        assert result.signal is None

    def test_reports_raw_value(self, config):
        result = P.score_rsi(data_for(trending(60, daily_pct=-1.0)), config)
        assert 0 <= result.raw_value <= 100
        assert result.display_value.replace(".", "").isdigit()


# ==========================================================================
# Moving averages
# ==========================================================================

class TestMACross:
    def test_uptrend_is_bullish(self, config):
        result = P.score_ma_cross(data_for(trending(120, daily_pct=0.5)), config)
        assert result.signal > 0.5

    def test_downtrend_is_bearish(self, config):
        result = P.score_ma_cross(data_for(trending(120, daily_pct=-0.5)), config)
        assert result.signal < -0.5

    def test_flat_is_neutral(self, config):
        result = P.score_ma_cross(data_for([100.0] * 120), config)
        assert abs(result.signal) < 0.05

    def test_needs_fifty_bars(self, config):
        result = P.score_ma_cross(data_for(trending(30)), config)
        assert result.status == "insufficient_data"

    def test_mentions_recent_cross(self, config):
        """A fresh golden cross should be called out in the reasoning."""
        closes = [100.0] * 70 + list(trending(25, start=100.0, daily_pct=2.0))
        result = P.score_ma_cross(data_for(closes), config)
        assert "cross" in result.explanation.lower()


class TestMA200:
    def test_above_the_line_is_bullish(self, config):
        result = P.score_ma200(data_for(trending(250, daily_pct=0.3)), config)
        assert result.signal > 0.5
        assert "above" in result.explanation

    def test_below_the_line_is_bearish(self, config):
        result = P.score_ma200(data_for(trending(250, daily_pct=-0.3)), config)
        assert result.signal < -0.5
        assert "below" in result.explanation

    def test_flags_an_active_test_of_the_level(self, config):
        result = P.score_ma200(data_for([100.0] * 250), config)
        assert result.detail["testing_level"] is True
        assert "test" in result.explanation.lower()

    def test_short_history_is_insufficient(self, config):
        result = P.score_ma200(data_for(trending(150)), config)
        assert result.status == "insufficient_data"
        assert "200" in result.explanation


# ==========================================================================
# MACD
# ==========================================================================

class TestMACD:
    def test_accelerating_advance_is_bullish(self, config):
        closes = [100.0] * 80 + [100.0 * (1.03 ** i) for i in range(1, 21)]
        assert P.score_macd(data_for(closes), config).signal > 0.3

    def test_accelerating_decline_is_bearish(self, config):
        closes = [100.0] * 80 + [100.0 * (0.97 ** i) for i in range(1, 21)]
        assert P.score_macd(data_for(closes), config).signal < -0.3

    def test_normalised_by_price(self, config):
        """A $20 stock and a $400 stock with the same shape score alike."""
        shape = [1.0] * 80 + [1.03 ** i for i in range(1, 21)]
        cheap = P.score_macd(data_for([20.0 * x for x in shape]), config)
        pricey = P.score_macd(data_for([400.0 * x for x in shape]), config)
        assert cheap.signal == pytest.approx(pricey.signal, abs=0.02)

    def test_short_history_is_insufficient(self, config):
        assert P.score_macd(data_for(trending(20)), config).status == "insufficient_data"


# ==========================================================================
# Volume
# ==========================================================================

class TestVolume:
    def test_surge_on_an_advance_is_bullish(self, config):
        closes = trending(60, daily_pct=1.0)
        volumes = [1_000_000.0] * 59 + [5_000_000.0]
        assert P.score_volume(data_for(closes, volumes), config).signal > 0.4

    def test_surge_on_a_decline_is_bearish(self, config):
        closes = trending(60, daily_pct=-1.0)
        volumes = [1_000_000.0] * 59 + [5_000_000.0]
        assert P.score_volume(data_for(closes, volumes), config).signal < -0.4

    def test_surge_on_a_flat_tape_has_no_direction(self, config):
        """Volume alone is directionless; it must not invent a lean."""
        closes = [100.0] * 60
        volumes = [1_000_000.0] * 59 + [5_000_000.0]
        assert abs(P.score_volume(data_for(closes, volumes), config).signal) < 0.05

    def test_latest_bar_excluded_from_its_own_baseline(self, config):
        """A surge must not dilute the average it is measured against."""
        closes = trending(60, daily_pct=1.0)
        volumes = [1_000_000.0] * 59 + [3_000_000.0]
        result = P.score_volume(data_for(closes, volumes), config)
        assert result.raw_value == pytest.approx(3.0, abs=0.01)

    def test_zero_average_volume_is_insufficient(self, config):
        result = P.score_volume(data_for(trending(60), [0.0] * 60), config)
        assert result.status == "insufficient_data"

    def test_short_history_is_insufficient(self, config):
        assert P.score_volume(data_for(trending(10)), config).status == "insufficient_data"


# ==========================================================================
# IV Rank
# ==========================================================================

def assessment_with(atm_iv=0.30, selected=None, expiry_count=3, **kwargs) -> OptionsAssessment:
    return OptionsAssessment(
        symbol="TEST", spot=100.0, atm_iv=atm_iv, expiry_count=expiry_count,
        selected=selected, **kwargs,
    )


def contract(delta_value=0.52, dte=60, kind="call") -> ContractCandidate:
    return ContractCandidate(
        contract_symbol="TEST260101C00100000", kind=kind, strike=100.0,
        expiry=TEST_TODAY + timedelta(days=dte), dte=dte, bid=5.0, ask=5.2,
        mid=5.1, spread_pct=3.9, open_interest=1200, volume=300,
        implied_volatility=0.30, delta=delta_value,
    )


class TestIVRank:
    def test_falls_back_to_hv_proxy_without_iv_history(self, config):
        data = data_for(trending(300, daily_pct=0.2), options=assessment_with())
        result = P.score_iv_rank(data, config)
        assert result.status == "ok"
        assert result.detail["source"] == "hv_proxy"
        assert "realised volatility" in result.explanation

    def test_switches_to_real_iv_once_enough_observations(self, config):
        history = pd.Series(
            [0.20 + 0.001 * i for i in range(80)],
            index=pd.date_range(end="2026-03-10", periods=80, freq="D"),
        )
        data = data_for(trending(300, daily_pct=0.2),
                        options=assessment_with(atm_iv=0.40), iv_history=history)
        result = P.score_iv_rank(data, config)
        assert result.detail["source"] == "iv"
        assert result.detail["observations"] == 80

    def test_buying_prefers_cheap_volatility(self, config):
        """Same IV Rank must score oppositely under the two postures."""
        history = pd.Series([0.20 + 0.002 * i for i in range(80)],
                            index=pd.date_range(end="2026-03-10", periods=80, freq="D"))
        low = data_for(trending(300, daily_pct=0.2),
                       options=assessment_with(atm_iv=0.19), iv_history=history)
        buying = P.score_iv_rank(low, ScreenerConfig(posture="buy"))
        selling = P.score_iv_rank(low, ScreenerConfig(posture="sell"))
        assert buying.signal > 0.5
        assert selling.signal < 0.5
        assert "buying premium" in buying.explanation
        assert "selling premium" in selling.explanation

    def test_selling_prefers_rich_volatility(self, config):
        history = pd.Series([0.20 + 0.002 * i for i in range(80)],
                            index=pd.date_range(end="2026-03-10", periods=80, freq="D"))
        high = data_for(trending(300, daily_pct=0.2),
                        options=assessment_with(atm_iv=0.60), iv_history=history)
        assert P.score_iv_rank(high, ScreenerConfig(posture="sell")).signal > 0.5
        assert P.score_iv_rank(high, ScreenerConfig(posture="buy")).signal < 0.5

    def test_no_data_at_all_is_insufficient(self, config):
        data = data_for([100.0] * 5, options=assessment_with(atm_iv=None))
        assert P.score_iv_rank(data, config).status == "insufficient_data"

    def test_signal_is_unsigned(self, config):
        data = data_for(trending(300, daily_pct=0.2), options=assessment_with())
        assert 0.0 <= P.score_iv_rank(data, config).signal <= 1.0


# ==========================================================================
# Options fit parameters
# ==========================================================================

class TestDTEFit:
    def test_inside_window_is_full_credit(self, config):
        data = data_for(trending(60), options=assessment_with(selected=contract(dte=60)))
        result = P.score_dte_fit(data, config)
        assert result.signal == 1.0
        assert "inside your" in result.explanation

    def test_outside_window_decays(self, config):
        data = data_for(trending(60), options=assessment_with(selected=contract(dte=7)))
        result = P.score_dte_fit(data, config)
        assert result.signal < 0.2
        assert "shorter" in result.explanation

    def test_no_chain_is_insufficient(self, config):
        data = data_for(trending(60), options=assessment_with(expiry_count=0))
        assert P.score_dte_fit(data, config).status == "insufficient_data"


class TestDeltaFit:
    def test_inside_band_is_full_credit(self, config):
        data = data_for(trending(60), options=assessment_with(selected=contract(0.52)))
        assert P.score_delta_fit(data, config).signal == 1.0

    def test_far_from_band_decays(self, config):
        data = data_for(trending(60), options=assessment_with(selected=contract(0.05)))
        assert P.score_delta_fit(data, config).signal < 0.05

    def test_uses_absolute_delta_for_puts(self, config):
        """A put delta of -0.52 is as good a fit as a call delta of +0.52."""
        put = data_for(trending(60), options=assessment_with(selected=contract(-0.52, kind="put")))
        assert P.score_delta_fit(put, config).signal == 1.0

    def test_no_contract_explains_why(self, config):
        assessment = assessment_with(selected=None)
        assessment.notes.append("No contract passed the liquidity filters.")
        data = data_for(trending(60), options=assessment)
        result = P.score_delta_fit(data, config)
        assert result.status == "insufficient_data"
        assert "liquidity" in result.explanation


# ==========================================================================
# Earnings
# ==========================================================================

class TestEarnings:
    def test_etf_is_not_applicable(self, config):
        data = data_for(trending(60), symbol="SPY", is_etf=True, earnings_date=None)
        result = P.score_earnings(data, config)
        assert result.status == "not_applicable"
        assert "fund" in result.explanation
        assert "redistributed" in result.explanation

    def test_missing_date_on_a_stock_is_insufficient(self, config):
        data = data_for(trending(60), is_etf=False, earnings_date=None)
        assert P.score_earnings(data, config).status == "insufficient_data"

    def test_buying_rewards_an_approaching_report(self, config):
        buying = ScreenerConfig(posture="buy")
        near = data_for(trending(60), earnings_date=TEST_TODAY + timedelta(days=14))
        far = data_for(trending(60), earnings_date=TEST_TODAY + timedelta(days=120))
        assert P.score_earnings(near, buying).signal > P.score_earnings(far, buying).signal

    def test_selling_penalises_an_approaching_report(self, config):
        selling = ScreenerConfig(posture="sell")
        near = data_for(trending(60), earnings_date=TEST_TODAY + timedelta(days=3))
        far = data_for(trending(60), earnings_date=TEST_TODAY + timedelta(days=90))
        assert P.score_earnings(near, selling).signal < P.score_earnings(far, selling).signal
        assert "gap" in P.score_earnings(near, selling).explanation

    def test_buying_suppresses_a_report_that_is_imminent(self, config):
        """Inside a session or two it is a coin flip, not a volatility play."""
        buying = ScreenerConfig(posture="buy")
        imminent = data_for(trending(60), earnings_date=TEST_TODAY + timedelta(days=1))
        result = P.score_earnings(imminent, buying)
        assert result.signal < 0.4
        assert "coin" in result.explanation or "bet on the result" in result.explanation

    def test_past_date_is_insufficient(self, config):
        stale = data_for(trending(60), earnings_date=TEST_TODAY - timedelta(days=10))
        result = P.score_earnings(stale, config)
        assert result.status == "insufficient_data"
        assert "past" in result.explanation

    def test_uses_trading_days_not_calendar_days(self, config):
        """A report 'four days away' across a weekend is two sessions away."""
        friday = date(2026, 3, 13)
        data = TickerData(symbol="T", prices=make_frame(trending(60)), today=friday,
                          earnings_date=date(2026, 3, 17))
        assert P.score_earnings(data, config).raw_value == 2.0


# ==========================================================================
# Registry integrity
# ==========================================================================

def test_every_registered_parameter_has_a_scorer():
    from screener.param_specs import ALL_SPECS

    assert {s.key for s in ALL_SPECS} == set(P.ALL_SCORERS)


def test_scorers_are_split_by_layer():
    from screener.param_specs import OPTIONS_KEYS, STOCK_KEYS

    assert set(P.STOCK_SCORERS) == set(STOCK_KEYS)
    assert set(P.OPTIONS_SCORERS) == set(OPTIONS_KEYS)


def test_every_result_carries_an_explanation(config):
    """The UI promises reasoning on every parameter, including failures."""
    data = data_for(trending(300, daily_pct=0.3), options=assessment_with(selected=contract()),
                    earnings_date=TEST_TODAY + timedelta(days=20))
    for scorer in P.ALL_SCORERS.values():
        result = scorer(data, config)
        assert result.explanation, f"{result.key} produced no explanation"
        assert len(result.explanation) > 20
