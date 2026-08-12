"""Tests for Black-Scholes greeks.

Delta matters here because yfinance publishes no greeks, so every delta the
screener scores is computed by this module.
"""

from __future__ import annotations

import pytest

from screener.blackscholes import d1, delta


class TestDelta:
    def test_atm_call_is_about_half(self):
        value = delta(spot=100, strike=100, time_years=0.25, vol=0.30, kind="call")
        assert 0.50 < value < 0.60

    def test_atm_put_is_about_minus_half(self):
        value = delta(spot=100, strike=100, time_years=0.25, vol=0.30, kind="put")
        assert -0.50 < value < -0.40

    def test_put_call_parity(self):
        """call delta - put delta = e^(-qT); with q=0 that is exactly 1."""
        args = dict(spot=100, strike=95, time_years=0.5, vol=0.25)
        call = delta(kind="call", **args)
        put = delta(kind="put", **args)
        assert call - put == pytest.approx(1.0)

    def test_deep_itm_call_approaches_one(self):
        value = delta(spot=200, strike=100, time_years=0.25, vol=0.20, kind="call")
        assert value > 0.98

    def test_deep_otm_call_approaches_zero(self):
        value = delta(spot=50, strike=100, time_years=0.25, vol=0.20, kind="call")
        assert value < 0.02

    def test_call_delta_bounded(self):
        for strike in (10, 50, 100, 150, 400):
            value = delta(spot=100, strike=strike, time_years=1.0, vol=0.4, kind="call")
            assert 0.0 <= value <= 1.0

    def test_put_delta_bounded(self):
        for strike in (10, 50, 100, 150, 400):
            value = delta(spot=100, strike=strike, time_years=1.0, vol=0.4, kind="put")
            assert -1.0 <= value <= 0.0

    def test_higher_vol_pushes_otm_delta_up(self):
        low = delta(spot=100, strike=120, time_years=0.25, vol=0.15, kind="call")
        high = delta(spot=100, strike=120, time_years=0.25, vol=0.60, kind="call")
        assert high > low

    def test_dividend_yield_reduces_call_delta(self):
        plain = delta(spot=100, strike=100, time_years=1.0, vol=0.3, kind="call")
        with_dividend = delta(spot=100, strike=100, time_years=1.0, vol=0.3,
                              kind="call", dividend=0.05)
        assert with_dividend < plain

    @pytest.mark.parametrize("bad", [
        dict(time_years=0.0),      # expires today
        dict(vol=0.0),             # no quote
        dict(spot=0.0),            # missing price
        dict(strike=0.0),
        dict(time_years=-1.0),
    ])
    def test_degenerate_inputs_return_none(self, bad):
        """None rather than a 0/1 step, so the caller treats it as missing data."""
        args = dict(spot=100.0, strike=100.0, time_years=0.25, vol=0.3, kind="call")
        args.update(bad)
        assert delta(**args) is None

    def test_rejects_unknown_kind(self):
        with pytest.raises(ValueError, match="call.*put"):
            delta(spot=100, strike=100, time_years=0.25, vol=0.3, kind="straddle")


def test_d1_none_on_degenerate_input():
    assert d1(spot=100, strike=100, time_years=0, vol=0.3) is None
    assert d1(spot=100, strike=100, time_years=1, vol=0) is None
