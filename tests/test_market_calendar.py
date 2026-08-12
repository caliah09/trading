"""Tests for the dependency-free US market calendar."""

from __future__ import annotations

from datetime import date

import pytest

from screener.market_calendar import (
    is_trading_day,
    market_holidays,
    previous_trading_day,
    trading_days_between,
)


class TestTradingDay:
    def test_ordinary_weekday(self):
        assert is_trading_day(date(2026, 3, 11))  # Wednesday

    @pytest.mark.parametrize("day", [date(2026, 3, 14), date(2026, 3, 15)])
    def test_weekends_are_closed(self, day):
        assert not is_trading_day(day)

    @pytest.mark.parametrize("day,name", [
        (date(2026, 1, 1), "New Year's Day"),
        (date(2026, 1, 19), "MLK Jr Day"),
        (date(2026, 2, 16), "Presidents' Day"),
        (date(2026, 4, 3), "Good Friday"),
        (date(2026, 5, 25), "Memorial Day"),
        (date(2026, 6, 19), "Juneteenth"),
        (date(2026, 7, 3), "Independence Day observed (4th is a Saturday)"),
        (date(2026, 9, 7), "Labor Day"),
        (date(2026, 11, 26), "Thanksgiving"),
        (date(2026, 12, 25), "Christmas"),
    ])
    def test_holidays_are_closed(self, day, name):
        assert not is_trading_day(day), f"{name} ({day}) should be a closure"

    def test_saturday_holiday_observed_on_friday(self):
        """4 July 2026 falls on a Saturday, so the 3rd is the closure."""
        assert date(2026, 7, 3) in market_holidays(2026)
        assert date(2026, 7, 4) not in market_holidays(2026)

    def test_sunday_holiday_observed_on_monday(self):
        """Christmas 2027 falls on a Saturday; 2022's New Year moved to Monday."""
        assert date(2022, 6, 20) in market_holidays(2022)  # Juneteenth on a Sunday

    def test_juneteenth_absent_before_2022(self):
        assert date(2021, 6, 18) not in market_holidays(2021)
        assert date(2021, 6, 19) not in market_holidays(2021)

    def test_good_friday_tracks_easter(self):
        assert date(2027, 3, 26) in market_holidays(2027)


class TestPreviousTradingDay:
    def test_monday_goes_back_to_friday(self):
        assert previous_trading_day(date(2026, 3, 16)) == date(2026, 3, 13)

    def test_skips_a_holiday(self):
        # 26 Nov 2026 is Thanksgiving; the day before it is the 25th.
        assert previous_trading_day(date(2026, 11, 27)) == date(2026, 11, 25)


class TestTradingDaysBetween:
    def test_same_day_is_zero(self):
        assert trading_days_between(date(2026, 3, 11), date(2026, 3, 11)) == 0

    def test_consecutive_weekdays(self):
        assert trading_days_between(date(2026, 3, 11), date(2026, 3, 12)) == 1

    def test_weekend_does_not_count(self):
        """Friday to Monday is four calendar days but one session."""
        assert trading_days_between(date(2026, 3, 13), date(2026, 3, 16)) == 1

    def test_holiday_does_not_count(self):
        # Wed 25 Nov -> Fri 27 Nov 2026 spans Thanksgiving.
        assert trading_days_between(date(2026, 11, 25), date(2026, 11, 27)) == 1

    def test_negative_when_reversed(self):
        assert trading_days_between(date(2026, 3, 12), date(2026, 3, 11)) == -1
