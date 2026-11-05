"""Market data provider interface.

Every network call in this application goes through a ``MarketDataProvider``.
Two implementations exist:

* :class:`screener.providers.yfinance_provider.YFinanceProvider` — the real one.
* :class:`screener.providers.synthetic.SyntheticProvider` — deterministic fake
  data for tests and for exercising the dashboard offline.

Keeping the boundary this narrow means the scoring engine never knows where its
numbers came from, and the test suite never touches the network.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd

log = logging.getLogger(__name__)

__all__ = [
    "PRICE_COLUMNS", "CHAIN_COLUMNS",
    "ChainSnapshot", "ProviderError", "MarketDataProvider",
    "empty_price_frame", "empty_chain_frame",
    "fetch_price_history", "sequential_price_history",
]

#: Normalised price history schema. Providers must return exactly these columns,
#: indexed by a timezone-naive ``DatetimeIndex`` of session dates.
PRICE_COLUMNS = ("open", "high", "low", "close", "volume")

#: Normalised option chain schema.
CHAIN_COLUMNS = (
    "contract_symbol", "strike", "bid", "ask", "last_price",
    "volume", "open_interest", "implied_volatility",
)


class ProviderError(RuntimeError):
    """A data source failed in a way the caller should surface, not swallow.

    Distinct from *absent* data: an unknown ticker returning an empty frame is
    normal and handled by the scoring engine's missing-data path. This is for
    genuine failures — network down, rate limited, malformed response.
    """


def empty_price_frame() -> pd.DataFrame:
    frame = pd.DataFrame(columns=list(PRICE_COLUMNS))
    frame.index = pd.DatetimeIndex([], name="date")
    return frame.astype({c: "float64" for c in PRICE_COLUMNS})


def empty_chain_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=list(CHAIN_COLUMNS))


@dataclass
class ChainSnapshot:
    """One expiry's calls and puts, normalised to :data:`CHAIN_COLUMNS`."""

    symbol: str
    expiry: date
    calls: pd.DataFrame = field(default_factory=empty_chain_frame)
    puts: pd.DataFrame = field(default_factory=empty_chain_frame)

    @property
    def is_empty(self) -> bool:
        return self.calls.empty and self.puts.empty

    def side(self, kind: str) -> pd.DataFrame:
        """Return the ``"call"`` or ``"put"`` frame."""
        if kind == "call":
            return self.calls
        if kind == "put":
            return self.puts
        raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")


@runtime_checkable
class MarketDataProvider(Protocol):
    """The complete data surface the screener depends on."""

    name: str

    def price_history(self, symbol: str, lookback_days: int) -> pd.DataFrame:
        """Daily OHLCV for roughly ``lookback_days`` calendar days back.

        Returns an empty frame (not an exception) for unknown or delisted
        tickers, so a bad symbol degrades one row rather than the whole run.
        """
        ...

    def price_history_batch(self, symbols: list[str],
                            lookback_days: int) -> dict[str, pd.DataFrame]:
        """Daily OHLCV for many symbols at once.

        Optional. A provider that cannot batch should not implement this —
        ``sequential_price_history`` below is the fallback, and the pipeline
        uses it automatically. Implementing it matters only for wide scans,
        where one request per ticker is the difference between three minutes
        and an hour.

        Must return one entry per requested symbol. A symbol with no data maps
        to an empty frame rather than being omitted, so callers never have to
        distinguish "absent" from "failed".
        """
        ...

    def option_expiries(self, symbol: str) -> list[date]:
        """Available expiration dates, ascending. Empty when no chain exists."""
        ...

    def option_chain(self, symbol: str, expiry: date) -> ChainSnapshot:
        """Normalised chain for one expiry."""
        ...

    def next_earnings_date(self, symbol: str) -> date | None:
        """Next scheduled earnings date.

        ``None`` legitimately means "not applicable" for ETFs such as SPY and
        QQQ, as well as "unknown". The pipeline distinguishes the two by
        checking whether the instrument has an equity profile at all.
        """
        ...

    def is_etf(self, symbol: str) -> bool:
        """True when the symbol is a fund rather than a single company.

        Used so a missing earnings date on SPY is reported as "not applicable"
        rather than as a data gap.
        """
        ...


def sequential_price_history(provider: MarketDataProvider, symbols: list[str],
                             lookback_days: int) -> dict[str, pd.DataFrame]:
    """Fallback batch fetch: one request per symbol, failures contained.

    Used for any provider that does not implement ``price_history_batch``, and
    as the recovery path when a provider's batch call raises. A batch request
    is all-or-nothing at the transport level, so one malformed symbol can take
    down the whole response — dropping back to sequential turns that into a
    handful of empty frames instead of a failed run.
    """
    out: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        try:
            out[symbol] = provider.price_history(symbol, lookback_days)
        except Exception:
            log.warning("Price fetch failed for %s", symbol, exc_info=True)
            out[symbol] = empty_price_frame()
    return out


def fetch_price_history(provider: MarketDataProvider, symbols: list[str],
                        lookback_days: int) -> dict[str, pd.DataFrame]:
    """Batch-fetch if the provider supports it, otherwise fetch one at a time.

    Always returns an entry for every requested symbol. A provider whose batch
    call succeeds but omits symbols has those filled in sequentially, because a
    partial batch is a common shape of upstream throttling and is not worth
    failing over.
    """
    if not symbols:
        return {}

    batch = getattr(provider, "price_history_batch", None)
    if batch is None:
        return sequential_price_history(provider, symbols, lookback_days)

    try:
        fetched = batch(list(symbols), lookback_days)
    except Exception:
        log.warning("Batch price fetch failed; falling back to sequential.", exc_info=True)
        return sequential_price_history(provider, symbols, lookback_days)

    missing = [s for s in symbols if s not in fetched]
    if missing:
        log.info("Batch fetch omitted %d symbol(s); fetching those individually.", len(missing))
        fetched.update(sequential_price_history(provider, missing, lookback_days))
    return fetched
