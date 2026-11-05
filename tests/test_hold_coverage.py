"""An option must outlast the thesis it is bought to express.

THE BUG THESE TESTS EXIST FOR
-----------------------------
The ticket builder already compared the contract's life against the pattern's
expected hold. It compared the wrong units:

    if int(contract["dte"]) < ticket.expected_hold_days:

``dte`` is calendar days. ``expected_hold_days`` held trading sessions. On a
71-day contract with a 60-session hold the check asked ``71 < 60``, answered
no, and stayed silent — while the contract covered 48 sessions, twelve short.

Nothing failed. The ticket printed "expected hold ~60 sessions" and "exp
2027-01-15 (71d)" two lines apart and let the reader reconcile them.

So these tests work in real dates against the real market calendar, and assert
on session counts rather than on the arithmetic that was wrong.
"""

from __future__ import annotations

from datetime import date

import pytest

from screener.market_calendar import trading_days_between
from screener.options_analysis import analyse_options
from screener.patterns.head_shoulders import EXPECTED_HOLD_SESSIONS
from screener.providers.base import ChainSnapshot, empty_chain_frame
from screener.tickets import build_ticket

#: The real case: early November, looking at January and February expiries.
TODAY = date(2026, 11, 5)
JAN = date(2027, 1, 15)   # 71 calendar days -> 48 sessions
FEB = date(2027, 2, 19)   # 106 calendar days -> 71 sessions


def test_the_calendar_disagrees_with_the_weekend_estimate():
    """Pins why calendar days are the wrong unit, with holidays included."""
    assert (JAN - TODAY).days == 71
    assert trading_days_between(TODAY, JAN) == 48
    # A naive weekdays-only estimate would say ~51. Thanksgiving, Christmas,
    # New Year and MLK account for the rest.
    assert trading_days_between(TODAY, JAN) < round(71 * 5 / 7)


def entry(expiry: date, dte: int, hold: int = EXPECTED_HOLD_SESSIONS) -> dict:
    return {
        "symbol": "AMD",
        "rank": 1,
        "composite": 50.3,
        "direction": "put",
        "direction_label": "Put lean",
        "spot": 404.40,
        "completeness": "10/11",
        "options_notes": [],
        "contract": {
            "contract_symbol": "AMD270115P00430000",
            "kind": "put", "strike": 430.0,
            "expiry": expiry.isoformat(), "dte": dte,
            "bid": 56.85, "ask": 59.25, "mid": 58.05, "spread_pct": 4.1,
            "open_interest": 4096, "volume": 800,
            "implied_volatility": 0.598, "delta": -0.526,
        },
        "parameters": [
            {
                "param_key": "head_shoulders", "label": "Head & Shoulders",
                "layer": "stock", "status": "ok", "contribution": -6.86,
                "weight": 5.0, "effective_weight": 8.8, "signal": -0.78,
                "display_value": "bearish · confirmed", "raw_value": -0.78,
                "explanation": "A bearish Head & Shoulders is complete.",
                "detail": {
                    "neckline_now": 450.67, "target": 357.07,
                    "expected_hold_sessions": hold,
                },
            },
        ],
    }


def _hold_warning(ticket) -> str | None:
    return next((w for w in ticket.warnings if "session" in w and "hold" in w), None)


class TestTicketWarning:
    def test_warns_when_the_contract_expires_first(self):
        """The exact case that was silent: 71 calendar days, 48 sessions, 60 needed."""
        ticket = build_ticket(entry(JAN, 71), today=TODAY)
        warning = _hold_warning(ticket)
        assert warning is not None, "a 48-session contract for a 60-session hold passed silently"
        assert "48" in warning
        assert "60" in warning

    def test_the_warning_says_how_far_short(self):
        warning = _hold_warning(build_ticket(entry(JAN, 71), today=TODAY))
        assert "12 short" in warning

    def test_does_not_warn_when_the_contract_covers_the_hold(self):
        """February covers 71 sessions against a 60-session hold."""
        assert _hold_warning(build_ticket(entry(FEB, 106), today=TODAY)) is None

    def test_comparing_calendar_days_would_have_missed_it(self):
        """Guards the specific regression rather than the general behaviour.

        If someone reinstates a calendar-day comparison, 71 > 60 passes and
        this fails — which is the whole point.
        """
        contract_days = 71
        assert contract_days > EXPECTED_HOLD_SESSIONS       # the old check's view
        assert trading_days_between(TODAY, JAN) < EXPECTED_HOLD_SESSIONS  # the truth
        assert _hold_warning(build_ticket(entry(JAN, 71), today=TODAY))

    def test_stays_silent_when_the_expiry_cannot_be_read(self):
        """No expiry means no honest comparison, so say nothing."""
        data = entry(JAN, 71)
        data["contract"]["expiry"] = None
        assert _hold_warning(build_ticket(data, today=TODAY)) is None

    def test_stays_silent_on_a_malformed_expiry(self):
        data = entry(JAN, 71)
        data["contract"]["expiry"] = "not-a-date"
        assert _hold_warning(build_ticket(data, today=TODAY)) is None

    def test_no_warning_without_an_expected_hold(self):
        data = entry(JAN, 71)
        data["parameters"][0]["detail"].pop("expected_hold_sessions")
        assert _hold_warning(build_ticket(data, today=TODAY)) is None


# --------------------------------------------------------------------------
# Selection
# --------------------------------------------------------------------------

class _Chain:
    """A provider stub offering a fixed set of expiries with a usable chain."""

    name = "stub"

    def __init__(self, expiries: list[date]):
        self._expiries = expiries
        self.chains_requested: list[date] = []

    def option_expiries(self, symbol: str) -> list[date]:
        return list(self._expiries)

    def option_chain(self, symbol: str, expiry: date) -> ChainSnapshot:
        self.chains_requested.append(expiry)
        import pandas as pd

        frame = empty_chain_frame()
        rows = []
        for strike in (400.0, 430.0, 460.0):
            rows.append({
                "strike": strike, "bid": 56.85, "ask": 59.25, "last": 58.0,
                "volume": 500.0, "open_interest": 4096.0,
                "implied_volatility": 0.60, "in_the_money": strike > 404.0,
                "contract_symbol": f"AMD{expiry:%y%m%d}P{int(strike * 1000):08d}",
            })
        frame = pd.DataFrame(rows, columns=frame.columns)
        return ChainSnapshot(symbol=symbol, expiry=expiry, calls=empty_chain_frame(), puts=frame)

    def price_history(self, symbol, lookback_days):  # pragma: no cover - unused
        raise NotImplementedError

    def next_earnings_date(self, symbol):  # pragma: no cover - unused
        return None

    def is_etf(self, symbol):  # pragma: no cover - unused
        return False


@pytest.fixture
def config():
    from screener.config import ScreenerConfig
    return ScreenerConfig(watchlist=["AMD"])


class TestSelection:
    def test_skips_expiries_that_cannot_cover_the_hold(self, config):
        provider = _Chain([JAN, FEB])
        result = analyse_options(
            provider=provider, symbol="AMD", spot=404.40, config=config,
            direction="put", today=TODAY, min_hold_sessions=60,
        )
        assert result.selected is not None
        assert result.selected.expiry == FEB, "picked an expiry that dies before the thesis"
        assert any("Skipped" in note for note in result.notes)

    def test_without_a_hold_the_nearer_expiry_still_wins(self, config):
        """January fits the 45-90 day target window; only the hold rules it out."""
        provider = _Chain([JAN, FEB])
        result = analyse_options(
            provider=provider, symbol="AMD", spot=404.40, config=config,
            direction="put", today=TODAY, min_hold_sessions=None,
        )
        assert result.selected is not None
        assert result.selected.expiry == JAN

    def test_falls_back_rather_than_returning_nothing(self, config):
        """A flagged short contract beats 'no contract found'."""
        provider = _Chain([JAN])
        result = analyse_options(
            provider=provider, symbol="AMD", spot=404.40, config=config,
            direction="put", today=TODAY, min_hold_sessions=60,
        )
        assert result.selected is not None
        assert result.selected.expiry == JAN
        assert any("No listed expiry covers" in note for note in result.notes)

    def test_no_note_when_every_expiry_already_covers_the_hold(self, config):
        provider = _Chain([FEB])
        result = analyse_options(
            provider=provider, symbol="AMD", spot=404.40, config=config,
            direction="put", today=TODAY, min_hold_sessions=60,
        )
        assert not any("Skipped" in n or "No listed expiry" in n for n in result.notes)

    def test_short_expiries_are_not_even_requested(self, config):
        """Excluded expiries should cost no chain requests."""
        provider = _Chain([JAN, FEB])
        analyse_options(
            provider=provider, symbol="AMD", spot=404.40, config=config,
            direction="put", today=TODAY, min_hold_sessions=60,
        )
        assert JAN not in provider.chains_requested or provider.chains_requested.count(JAN) <= 1


class TestPipelineWiring:
    def test_the_hold_reaches_selection(self):
        """The extractor must find the pattern's hold among the stock results."""
        from screener.pipeline import _expected_hold_sessions

        class R:
            def __init__(self, status, detail):
                self.status = status
                self.detail = detail

        assert _expected_hold_sessions([R("ok", {"expected_hold_sessions": 60})]) == 60

    def test_takes_the_longest_hold_not_the_first(self):
        from screener.pipeline import _expected_hold_sessions

        class R:
            def __init__(self, status, detail):
                self.status = status
                self.detail = detail

        results = [
            R("ok", {"expected_hold_sessions": 30}),
            R("ok", {"expected_hold_sessions": 90}),
        ]
        assert _expected_hold_sessions(results) == 90

    def test_ignores_parameters_that_did_not_compute(self):
        from screener.pipeline import _expected_hold_sessions

        class R:
            def __init__(self, status, detail):
                self.status = status
                self.detail = detail

        assert _expected_hold_sessions([
            R("insufficient_data", {"expected_hold_sessions": 60}),
        ]) is None

    def test_returns_none_when_no_parameter_reports_one(self):
        from screener.pipeline import _expected_hold_sessions

        class R:
            def __init__(self, status, detail):
                self.status = status
                self.detail = detail

        assert _expected_hold_sessions([R("ok", {"other": 1}), R("ok", None)]) is None
