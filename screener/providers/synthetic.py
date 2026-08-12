"""Deterministic synthetic market data.

Two jobs:

1. **Tests.** The suite must never touch the network — network tests are slow,
   flaky, and would fail on a market holiday or a Yahoo outage. Every scoring
   test runs against data generated here.
2. **Offline demo.** ``--provider synthetic`` drives the whole dashboard end to
   end without a live data source, which is also how you can exercise the UI in
   a sandbox where Yahoo is unreachable.

The generator is seeded from the ticker symbol, so a given symbol always
produces the same series. Regimes are deliberately varied across symbols so the
ranked list exercises bullish, bearish and no-signal paths, plus the degenerate
cases (no chain, short history, absent earnings date).
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta

import numpy as np
import pandas as pd

from screener.market_calendar import is_trading_day
from screener.providers.base import ChainSnapshot, empty_chain_frame, empty_price_frame

#: Symbols given deliberately awkward data so edge-case paths get exercised.
NO_CHAIN_SYMBOLS = frozenset({"NOCHAIN"})
SHORT_HISTORY_SYMBOLS = frozenset({"NEWIPO"})
UNKNOWN_SYMBOLS = frozenset({"BADTICK", "DELISTED"})
ETF_SYMBOLS = frozenset({"SPY", "QQQ", "IWM", "DIA", "VOO", "ARKK"})

#: Calendar span of the one canonical series generated per symbol. Every
#: ``price_history`` call slices this, so results are consistent across
#: different lookback windows.
CANONICAL_DAYS = 900


def _seed(symbol: str) -> int:
    return int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)


class SyntheticProvider:
    """Reproducible fake market data with configurable "today"."""

    name = "synthetic"

    def __init__(self, today: date | None = None) -> None:
        self.today = today or date.today()
        self._series_cache: dict[str, pd.DataFrame] = {}

    # -- price history -----------------------------------------------------

    def price_history(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        """Bars for the requested window, sliced from one canonical series.

        The series is generated once per symbol over a fixed long window and
        then sliced, so asking for 45 days and asking for 730 return *the same
        bars* for the overlapping dates. Generating per-request would make a
        warm refresh silently rewrite history that a cold refresh had stored,
        which is not how any real data source behaves.
        """
        if symbol in UNKNOWN_SYMBOLS:
            return empty_price_frame()

        full = self._canonical_series(symbol)
        if full.empty:
            return full
        cutoff = pd.Timestamp(self.today - timedelta(days=max(lookback_days, 30)))
        return full[full.index >= cutoff].copy()

    def _canonical_series(self, symbol: str) -> pd.DataFrame:
        """The symbol's full generated history, cached per instance."""
        cached = self._series_cache.get(symbol)
        if cached is not None:
            return cached

        rng = np.random.default_rng(_seed(symbol))
        sessions = _sessions_ending(self.today, CANONICAL_DAYS)
        if symbol in SHORT_HISTORY_SYMBOLS:
            sessions = sessions[-60:]  # too short for a 200-day MA, by design

        n = len(sessions)
        if n == 0:
            return empty_price_frame()

        # Give each symbol a persistent regime so the ranking has real spread.
        regime = _seed(symbol) % 4
        drift = {0: 0.0011, 1: -0.0011, 2: 0.0002, 3: 0.0}[regime]
        vol = 0.010 + (_seed(symbol) % 17) / 1000.0

        returns = rng.normal(loc=drift, scale=vol, size=n)
        # A late-window momentum kick makes recent crossovers non-trivial.
        if regime in (0, 1):
            returns[-25:] += drift * 3.0

        base = 40.0 + (_seed(symbol) % 400)
        close = base * np.exp(np.cumsum(returns))

        intraday = np.abs(rng.normal(0.0, vol / 2.0, size=n))
        high = close * (1.0 + intraday)
        low = close * (1.0 - intraday)
        open_ = np.concatenate([[close[0]], close[:-1]])

        volume = rng.lognormal(mean=15.0, sigma=0.35, size=n)
        volume[-1] *= 1.0 + (_seed(symbol) % 5) / 2.0  # occasional surge on the last bar

        frame = pd.DataFrame(
            {
                "open": open_,
                "high": np.maximum.reduce([high, open_, close]),
                "low": np.minimum.reduce([low, open_, close]),
                "close": close,
                "volume": np.round(volume),
            },
            index=pd.DatetimeIndex(pd.to_datetime(sessions), name="date"),
        )
        self._series_cache[symbol] = frame
        return frame

    # -- options -----------------------------------------------------------

    def option_expiries(self, symbol: str) -> list[date]:
        if symbol in NO_CHAIN_SYMBOLS or symbol in UNKNOWN_SYMBOLS:
            return []
        offsets = (7, 14, 21, 35, 49, 63, 91, 126, 182, 273)
        return [self.today + timedelta(days=d) for d in offsets]

    def option_chain(self, symbol: str, expiry: date) -> ChainSnapshot:
        if symbol in NO_CHAIN_SYMBOLS or symbol in UNKNOWN_SYMBOLS:
            return ChainSnapshot(symbol=symbol, expiry=expiry)

        history = self.price_history(symbol, 30)
        if history.empty:
            return ChainSnapshot(symbol=symbol, expiry=expiry)

        spot = float(history["close"].iloc[-1])
        rng = np.random.default_rng(_seed(symbol) + expiry.toordinal())
        dte = max((expiry - self.today).days, 1)

        strikes = np.round(np.arange(spot * 0.70, spot * 1.30, max(spot * 0.025, 0.5)), 2)
        base_iv = 0.18 + (_seed(symbol) % 30) / 100.0

        def _side(kind: str) -> pd.DataFrame:
            moneyness = (strikes - spot) / spot
            # A volatility smile: wings price above the at-the-money level.
            iv = base_iv * (1.0 + 1.2 * moneyness ** 2) + rng.normal(0, 0.004, len(strikes))
            intrinsic = (np.maximum(spot - strikes, 0.0) if kind == "put"
                         else np.maximum(strikes - spot, 0.0))
            time_value = spot * base_iv * np.sqrt(dte / 365.0) * np.exp(-8.0 * moneyness ** 2)
            mid = np.maximum(intrinsic + time_value, 0.01)
            spread = np.maximum(mid * 0.02, 0.01)
            open_interest = np.round(
                5000.0 * np.exp(-12.0 * moneyness ** 2) + rng.integers(0, 40, len(strikes))
            )
            return pd.DataFrame(
                {
                    "contract_symbol": [
                        f"{symbol}{expiry:%y%m%d}{kind[0].upper()}{int(s * 1000):08d}"
                        for s in strikes
                    ],
                    "strike": strikes,
                    "bid": np.round(mid - spread / 2.0, 2),
                    "ask": np.round(mid + spread / 2.0, 2),
                    "last_price": np.round(mid, 2),
                    "volume": np.round(open_interest / 10.0),
                    "open_interest": open_interest,
                    "implied_volatility": np.clip(iv, 0.02, 3.0),
                }
            )

        return ChainSnapshot(symbol=symbol, expiry=expiry,
                             calls=_side("call"), puts=_side("put"))

    # -- fundamentals ------------------------------------------------------

    def next_earnings_date(self, symbol: str) -> date | None:
        if self.is_etf(symbol) or symbol in UNKNOWN_SYMBOLS:
            return None
        offset = 3 + (_seed(symbol) % 70)
        return self.today + timedelta(days=offset)

    def is_etf(self, symbol: str) -> bool:
        return symbol in ETF_SYMBOLS


def _sessions_ending(end: date, calendar_days: int) -> list[date]:
    """Trading sessions in the ``calendar_days`` window ending at ``end``."""
    start = end - timedelta(days=calendar_days)
    out: list[date] = []
    cursor = start
    while cursor <= end:
        if is_trading_day(cursor):
            out.append(cursor)
        cursor += timedelta(days=1)
    return out
