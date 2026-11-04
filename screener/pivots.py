"""Swing-point (pivot) detection.

Shared infrastructure for every pattern method. Head & Shoulders needs it now;
Elliott Wave will need the same pivots later, and the two must agree — a pattern
library where each detector finds its own swings will produce contradictory
readings of the same chart.

**Why sensitivity is user-configurable rather than hard-coded.** Research on
Head & Shoulders recognition found that adding a filter to the pattern-matching
algorithm "generally improves risk-adjusted excess returns" — i.e. published
results move depending on how pivots are identified. That makes sensitivity a
real parameter with real effect on outcomes, not a cosmetic tuning knob, so it
is exposed rather than buried. See `docs/research/evidence-review.md` §1.

The algorithm is an ATR-scaled zigzag: a swing is confirmed once price retraces
`sensitivity × ATR` from a running extreme. Scaling by ATR rather than a fixed
percentage means the same setting behaves consistently across a $20 stock and a
$600 one, and adapts as a ticker's volatility regime changes.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = ["Pivot", "average_true_range", "find_pivots"]


@dataclass(frozen=True)
class Pivot:
    """A confirmed swing high or low."""

    position: int             #: positional index into the price frame
    timestamp: pd.Timestamp
    price: float
    kind: str                 #: "high" | "low"

    @property
    def is_high(self) -> bool:
        return self.kind == "high"

    def to_dict(self) -> dict:
        return {
            "position": self.position,
            "date": self.timestamp.date().isoformat(),
            "price": round(self.price, 4),
            "kind": self.kind,
        }


def average_true_range(prices: pd.DataFrame, period: int = 14) -> pd.Series:
    """Wilder's ATR.

    True range is the greatest of: today's range, the gap up from yesterday's
    close, and the gap down from it. Using it rather than the high-low range
    means overnight gaps — which is where a lot of equity movement actually
    happens — are not invisible to the swing detector.
    """
    high, low, close = prices["high"], prices["low"], prices["close"]
    previous_close = close.shift(1)

    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)

    # Wilder smoothing, seeded with a simple mean — same convention as RSI.
    return true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def find_pivots(
    prices: pd.DataFrame,
    sensitivity: float = 1.5,
    atr_period: int = 14,
    max_pivots: int | None = None,
) -> list[Pivot]:
    """Detect alternating swing highs and lows.

    Args:
        prices: OHLCV frame indexed by date.
        sensitivity: retracement required to confirm a swing, in ATR multiples.
            Larger values yield fewer, more significant pivots.
        atr_period: lookback for the ATR used to scale the threshold.
        max_pivots: keep only this many most-recent pivots.

    Returns:
        Pivots in chronological order, strictly alternating high/low. Empty when
        there is not enough history to compute an ATR.

    The final swing is **not** included unless it has been confirmed by a
    retracement. An unconfirmed extreme is just the current price, and treating
    it as a pivot is how indicators end up repainting — a pattern that appears
    in hindsight but was never visible live. Everything this returns was real at
    the time.
    """
    if prices.empty or len(prices) < atr_period + 2:
        return []

    atr = average_true_range(prices, atr_period)
    highs = prices["high"].to_numpy(dtype="float64")
    lows = prices["low"].to_numpy(dtype="float64")
    timestamps = prices.index
    thresholds = (atr * sensitivity).to_numpy(dtype="float64")

    start = int(np.argmax(~np.isnan(thresholds))) if np.isnan(thresholds).any() else 0
    if start >= len(prices) - 1 or np.isnan(thresholds[start]):
        return []

    pivots: list[Pivot] = []
    direction: str | None = None          # the swing currently being tracked

    # The running high and the running low need independent positions. Sharing
    # one would let a new low overwrite the position of the standing high, and
    # the recorded pivot would then pair a high's price with a low's bar.
    high_price, high_position = highs[start], start
    low_price, low_position = lows[start], start

    def _record(position: int, price: float, kind: str) -> None:
        pivots.append(Pivot(position, timestamps[position], float(price), kind))

    for i in range(start + 1, len(prices)):
        threshold = thresholds[i]
        if np.isnan(threshold) or threshold <= 0:
            continue

        if direction is None:
            # Undecided: whichever side moves a full threshold first sets it.
            if highs[i] > high_price:
                high_price, high_position = highs[i], i
            if lows[i] < low_price:
                low_price, low_position = lows[i], i
            if high_price - lows[i] >= threshold:
                direction = "down"
                _record(high_position, high_price, "high")
                low_price, low_position = lows[i], i
            elif highs[i] - low_price >= threshold:
                direction = "up"
                _record(low_position, low_price, "low")
                high_price, high_position = highs[i], i

        elif direction == "up":
            if highs[i] >= high_price:
                high_price, high_position = highs[i], i
            elif high_price - lows[i] >= threshold:
                _record(high_position, high_price, "high")
                direction = "down"
                low_price, low_position = lows[i], i

        else:  # tracking a downswing
            if lows[i] <= low_price:
                low_price, low_position = lows[i], i
            elif highs[i] - low_price >= threshold:
                _record(low_position, low_price, "low")
                direction = "up"
                high_price, high_position = highs[i], i

    if max_pivots is not None and len(pivots) > max_pivots:
        pivots = pivots[-max_pivots:]
    return pivots
