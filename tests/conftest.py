"""Shared fixtures.

Every test runs against :class:`SyntheticProvider`. No test touches the network:
network tests are slow, flaky, and would fail on a market holiday or a Yahoo
outage, none of which says anything about whether this code is correct.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from screener.config import ScreenerConfig
from screener.db import Database
from screener.providers.synthetic import SyntheticProvider

#: A fixed Wednesday, so the market calendar behaves predictably in tests.
TEST_TODAY = date(2026, 3, 11)


@pytest.fixture
def today() -> date:
    return TEST_TODAY


@pytest.fixture
def provider(today) -> SyntheticProvider:
    return SyntheticProvider(today=today)


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "test.sqlite")


@pytest.fixture
def config() -> ScreenerConfig:
    return ScreenerConfig(watchlist=["AAPL", "MSFT", "SPY"])


def make_series(values: list[float], end: date = TEST_TODAY) -> pd.Series:
    """A close-price series ending on ``end``, one bar per calendar day."""
    index = pd.date_range(end=pd.Timestamp(end), periods=len(values), freq="D")
    return pd.Series(values, index=index, dtype="float64")


def make_frame(closes: list[float], volumes: list[float] | None = None,
               end: date = TEST_TODAY) -> pd.DataFrame:
    """A minimal OHLCV frame for indicator and scorer tests."""
    close = make_series(closes, end)
    if volumes is None:
        volumes = [1_000_000.0] * len(closes)
    return pd.DataFrame(
        {
            "open": close.values,
            "high": close.values * 1.01,
            "low": close.values * 0.99,
            "close": close.values,
            "volume": np.array(volumes, dtype="float64"),
        },
        index=close.index,
    )


def trending(n: int, start: float = 100.0, daily_pct: float = 0.5) -> list[float]:
    """A clean geometric trend — deterministic, no noise."""
    return [start * ((1.0 + daily_pct / 100.0) ** i) for i in range(n)]
