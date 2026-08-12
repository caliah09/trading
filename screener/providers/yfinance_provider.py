"""Live market data via yfinance.

yfinance is an unofficial scraper of Yahoo Finance. It is free and needs no API
key, which is why it was chosen, but it is also *unreliable in specific ways*
that this module is responsible for absorbing:

* Transient rate limiting and connection resets — retried with backoff.
* Schema drift between versions (column names, MultiIndex columns on some
  calls) — normalised to :data:`screener.providers.base.PRICE_COLUMNS`.
* Silent empties for delisted or mistyped tickers — returned as empty frames so
  the caller's missing-data path handles them.
* Implied volatility of exactly 0.0 or absurd values (>500%) on illiquid
  strikes — coerced to NaN so they are treated as missing rather than scored.

Everything above the provider boundary is free to assume clean data.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd

from screener.providers.base import (
    CHAIN_COLUMNS,
    PRICE_COLUMNS,
    ChainSnapshot,
    ProviderError,
    empty_chain_frame,
    empty_price_frame,
)

log = logging.getLogger(__name__)

#: Implied volatility outside this range is treated as a bad quote, not a value.
_IV_MIN, _IV_MAX = 0.01, 5.0


class YFinanceProvider:
    """Market data backed by Yahoo Finance."""

    name = "yfinance"

    def __init__(self, max_retries: int = 3, backoff_seconds: float = 1.0,
                 request_pause: float = 0.0) -> None:
        """
        Args:
            max_retries: attempts per call before giving up.
            backoff_seconds: base delay, doubled each retry.
            request_pause: optional fixed sleep between calls. Yahoo throttles
                aggressively on large watchlists; set this to ~0.5 if you see
                intermittent empty responses.
        """
        self.max_retries = max_retries
        self.backoff_seconds = backoff_seconds
        self.request_pause = request_pause
        self._ticker_cache: dict[str, object] = {}

    # -- internals ---------------------------------------------------------

    def _ticker(self, symbol: str):
        import yfinance as yf  # imported lazily so tests need not have it installed

        if symbol not in self._ticker_cache:
            self._ticker_cache[symbol] = yf.Ticker(symbol)
        return self._ticker_cache[symbol]

    def _retry(self, what: str, fn):
        """Run ``fn`` with bounded exponential backoff.

        Returns None after exhausting retries. Callers decide whether that is
        fatal (raise) or simply absent data (empty frame) — this helper does not
        make that judgement.
        """
        delay = self.backoff_seconds
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                if self.request_pause:
                    time.sleep(self.request_pause)
                return fn()
            except Exception as exc:  # yfinance raises a wide variety of types
                last_error = exc
                log.warning("%s failed (attempt %d/%d): %s",
                            what, attempt, self.max_retries, exc)
                if attempt < self.max_retries:
                    time.sleep(delay)
                    delay *= 2
        log.error("%s failed after %d attempts: %s", what, self.max_retries, last_error)
        return None

    # -- price history -----------------------------------------------------

    def price_history(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        raw = self._retry(
            f"price_history({symbol})",
            lambda: self._ticker(symbol).history(
                period=f"{max(lookback_days, 5)}d",
                interval="1d",
                auto_adjust=True,   # split/dividend adjusted: indicators need a continuous series
                actions=False,
                raise_errors=False,
            ),
        )
        if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
            log.info("No price history for %s (unknown, delisted, or throttled)", symbol)
            return empty_price_frame()
        return _normalise_prices(raw)

    # -- options -----------------------------------------------------------

    def option_expiries(self, symbol: str) -> list[date]:
        raw = self._retry(f"option_expiries({symbol})", lambda: self._ticker(symbol).options)
        if not raw:
            return []
        out: list[date] = []
        for item in raw:
            try:
                out.append(datetime.strptime(str(item), "%Y-%m-%d").date())
            except ValueError:
                log.warning("Unparseable expiry %r for %s", item, symbol)
        return sorted(out)

    def option_chain(self, symbol: str, expiry: date) -> ChainSnapshot:
        raw = self._retry(
            f"option_chain({symbol}, {expiry})",
            lambda: self._ticker(symbol).option_chain(expiry.strftime("%Y-%m-%d")),
        )
        if raw is None:
            return ChainSnapshot(symbol=symbol, expiry=expiry)
        return ChainSnapshot(
            symbol=symbol,
            expiry=expiry,
            calls=_normalise_chain(getattr(raw, "calls", None)),
            puts=_normalise_chain(getattr(raw, "puts", None)),
        )

    # -- fundamentals ------------------------------------------------------

    def next_earnings_date(self, symbol: str) -> date | None:
        """Next earnings date, preferring the calendar over the estimates table."""
        cal = self._retry(f"calendar({symbol})", lambda: self._ticker(symbol).calendar)
        for value in _iter_calendar_dates(cal):
            if value >= date.today():
                return value

        # Fallback: the earnings_dates frame is indexed by datetime, future first.
        frame = self._retry(f"earnings_dates({symbol})",
                            lambda: self._ticker(symbol).earnings_dates)
        if isinstance(frame, pd.DataFrame) and not frame.empty:
            future = [ts.date() for ts in pd.to_datetime(frame.index, errors="coerce")
                      if pd.notna(ts) and ts.date() >= date.today()]
            if future:
                return min(future)
        return None

    def is_etf(self, symbol: str) -> bool:
        info = self._retry(f"info({symbol})", lambda: self._ticker(symbol).info)
        if not isinstance(info, dict):
            return False
        quote_type = str(info.get("quoteType", "")).upper()
        return quote_type in {"ETF", "MUTUALFUND", "INDEX"}


# --------------------------------------------------------------------------
# Normalisation helpers — the whole point of this module
# --------------------------------------------------------------------------

def _normalise_prices(raw: pd.DataFrame) -> pd.DataFrame:
    """Coerce a yfinance history frame into the documented schema."""
    frame = raw.copy()

    # Some yfinance versions return MultiIndex columns even for a single ticker.
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)

    frame.columns = [str(c).strip().lower().replace(" ", "_") for c in frame.columns]
    missing = [c for c in PRICE_COLUMNS if c not in frame.columns]
    if missing:
        raise ProviderError(
            f"yfinance returned an unexpected price schema; missing {missing}. "
            f"Got columns: {list(frame.columns)}"
        )

    frame = frame[list(PRICE_COLUMNS)]

    # Drop timezone so dates compare cleanly against SQLite date strings.
    index = pd.to_datetime(frame.index, errors="coerce")
    if getattr(index, "tz", None) is not None:
        index = index.tz_localize(None)
    frame.index = pd.DatetimeIndex(index.normalize(), name="date")

    frame = frame[~frame.index.isna()]
    frame = frame[~frame.index.duplicated(keep="last")].sort_index()

    for column in PRICE_COLUMNS:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    # A row without a close is unusable; volume of 0 is legitimate (halts).
    return frame.dropna(subset=["close"])


def _normalise_chain(raw: pd.DataFrame | None) -> pd.DataFrame:
    """Coerce a yfinance option chain side into the documented schema."""
    if raw is None or not isinstance(raw, pd.DataFrame) or raw.empty:
        return empty_chain_frame()

    rename = {
        "contractSymbol": "contract_symbol",
        "lastPrice": "last_price",
        "openInterest": "open_interest",
        "impliedVolatility": "implied_volatility",
    }
    frame = raw.rename(columns=rename).copy()

    for column in CHAIN_COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan
    frame = frame[list(CHAIN_COLUMNS)]

    for column in ("strike", "bid", "ask", "last_price", "volume",
                   "open_interest", "implied_volatility"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")

    # Yahoo reports 0.0 IV for stale strikes and occasionally absurd values.
    # Both are "no quote", not "volatility is zero".
    iv = frame["implied_volatility"]
    frame["implied_volatility"] = iv.where((iv >= _IV_MIN) & (iv <= _IV_MAX))

    # Volume and open interest are frequently NaN overnight; zero is the truth.
    frame["volume"] = frame["volume"].fillna(0.0)
    frame["open_interest"] = frame["open_interest"].fillna(0.0)

    return frame.dropna(subset=["strike"]).reset_index(drop=True)


def _iter_calendar_dates(cal) -> list[date]:
    """Extract earnings dates from the several shapes ``Ticker.calendar`` takes."""
    found: list[date] = []
    if isinstance(cal, dict):
        values = cal.get("Earnings Date") or cal.get("earningsDate") or []
        if not isinstance(values, (list, tuple)):
            values = [values]
    elif isinstance(cal, pd.DataFrame) and not cal.empty:
        try:
            values = list(cal.loc["Earnings Date"].values)
        except KeyError:
            values = []
    else:
        return found

    for value in values:
        if isinstance(value, date) and not isinstance(value, datetime):
            found.append(value)
            continue
        ts = pd.to_datetime(value, errors="coerce")
        if pd.notna(ts):
            found.append(ts.date())
    return sorted(found)
