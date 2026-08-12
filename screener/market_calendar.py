"""Minimal US equity market calendar.

Deliberately dependency-free. ``pandas_market_calendars`` / ``exchange_calendars``
are more complete (early closes, historical rule changes), but they are heavy
dependencies for the one question this tool actually asks: *is today a session?*
The rules below cover NYSE/Nasdaq regular holidays from 2022 onward, which is
the range that matters for a tool ranking current data.

Known limitation: ad-hoc closures (national days of mourning, weather) are not
modelled. The scheduled refresh treats a non-session day as "skip", so the worst
case is one unnecessary fetch that returns stale data and is deduplicated.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

__all__ = ["is_trading_day", "previous_trading_day", "trading_days_between"]


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The nth (1-based) ``weekday`` of a month. weekday: Monday=0."""
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    """The last ``weekday`` of a month."""
    if month == 12:
        d = date(year, 12, 31)
    else:
        d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """Gregorian Easter Sunday (Anonymous Gregorian algorithm)."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    m = (32 + 2 * e + 2 * i - h - k) % 7
    n = (a + 11 * h + 22 * m) // 451
    month, day = divmod(h + m - 7 * n + 114, 31)
    return date(year, month, day + 1)


def _observed(holiday: date) -> date:
    """US markets observe Saturday holidays on Friday, Sunday on Monday."""
    if holiday.weekday() == 5:
        return holiday - timedelta(days=1)
    if holiday.weekday() == 6:
        return holiday + timedelta(days=1)
    return holiday


@lru_cache(maxsize=32)
def market_holidays(year: int) -> frozenset[date]:
    """Full-day NYSE/Nasdaq closures for a calendar year."""
    holidays = {
        _observed(date(year, 1, 1)),                  # New Year's Day
        _nth_weekday(year, 1, 0, 3),                  # MLK Jr Day
        _nth_weekday(year, 2, 0, 3),                  # Presidents' Day
        _easter(year) - timedelta(days=2),            # Good Friday
        _last_weekday(year, 5, 0),                    # Memorial Day
        _observed(date(year, 6, 19)),                 # Juneteenth (from 2022)
        _observed(date(year, 7, 4)),                  # Independence Day
        _nth_weekday(year, 9, 0, 1),                  # Labor Day
        _nth_weekday(year, 11, 3, 4),                 # Thanksgiving
        _observed(date(year, 12, 25)),                # Christmas
    }
    if year < 2022:
        holidays.discard(_observed(date(year, 6, 19)))
    return frozenset(holidays)


def is_trading_day(day: date) -> bool:
    """True when US equity markets hold a regular session on ``day``."""
    if day.weekday() >= 5:
        return False
    return day not in market_holidays(day.year)


def previous_trading_day(day: date) -> date:
    """The most recent session strictly before ``day``."""
    cursor = day - timedelta(days=1)
    for _ in range(30):  # bounded: no real closure streak approaches this
        if is_trading_day(cursor):
            return cursor
        cursor -= timedelta(days=1)
    raise RuntimeError(f"No trading day found within 30 days before {day}")


def trading_days_between(start: date, end: date) -> int:
    """Count sessions in ``(start, end]``. Negative when ``end`` precedes ``start``.

    Used for earnings proximity, where calendar days overstate how much time you
    actually have — a report 4 calendar days out across a weekend is 2 sessions.
    """
    if end == start:
        return 0
    sign = 1 if end > start else -1
    lo, hi = (start, end) if end > start else (end, start)
    count = 0
    cursor = lo + timedelta(days=1)
    while cursor <= hi:
        if is_trading_day(cursor):
            count += 1
        cursor += timedelta(days=1)
    return sign * count
