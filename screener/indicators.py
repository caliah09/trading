"""Technical indicator maths.

Pure functions over pandas Series. No I/O, no config, no network — everything
here is deterministic and directly unit-testable.

All functions return ``None`` (or a result carrying ``None``) when there is not
enough history, rather than raising or silently returning a wrong number. The
scoring engine turns those into "insufficient data" and redistributes weight.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

__all__ = [
    "rsi", "ema", "sma", "macd", "MacdResult",
    "realised_volatility", "percentile_rank",
    "last_cross_index", "pct_change_over",
]


def sma(series: pd.Series, window: int) -> pd.Series:
    """Simple moving average. Values before ``window`` observations are NaN."""
    return series.rolling(window=window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """Exponential moving average.

    ``adjust=False`` gives the recursive form used by charting platforms, so the
    numbers match what you would see in a broker terminal.
    """
    return series.ewm(span=span, adjust=False).mean()


def _wilder_smooth(values: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing, seeded with a simple average of the first window.

    This seeding is not a detail. Running an EMA from the very first bar
    instead — which ``ewm(alpha=1/period)`` does — converges to the same place
    but differs materially over the first few hundred bars, and would put this
    tool's RSI visibly out of line with the value a broker chart shows for the
    same ticker.
    """
    raw = values.to_numpy(dtype="float64")
    out = np.full(len(raw), np.nan)
    # raw[0] is NaN because it comes from .diff(); the seed spans raw[1:period+1].
    if len(raw) <= period:
        return pd.Series(out, index=values.index)

    window = raw[1: period + 1]
    if np.isnan(window).any():
        return pd.Series(out, index=values.index)

    out[period] = window.mean()
    for i in range(period + 1, len(raw)):
        current = 0.0 if np.isnan(raw[i]) else raw[i]
        out[i] = (out[i - 1] * (period - 1) + current) / period
    return pd.Series(out, index=values.index)


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI.

    Uses Wilder's smoothing seeded with the simple average of the first window,
    which is the canonical definition and the one charting platforms implement.

    Rising-only series produce RSI 100; falling-only produce RSI 0; a perfectly
    flat series produces the neutral 50.
    """
    if period < 1:
        raise ValueError(f"period must be >= 1, got {period}")
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)

    avg_gain = _wilder_smooth(gain, period)
    avg_loss = _wilder_smooth(loss, period)

    rs = avg_gain / avg_loss
    out = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 means no down moves in the window -> RSI 100 by definition.
    out = out.where(avg_loss != 0, 100.0)
    # Both zero means a perfectly flat series; 50 is the neutral convention.
    out = out.where(~((avg_loss == 0) & (avg_gain == 0)), 50.0)
    return out


@dataclass(frozen=True)
class MacdResult:
    macd: pd.Series
    signal: pd.Series
    histogram: pd.Series


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> MacdResult:
    """MACD line, signal line, and histogram."""
    if not fast < slow:
        raise ValueError(f"fast ({fast}) must be less than slow ({slow})")
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    return MacdResult(macd=macd_line, signal=signal_line, histogram=macd_line - signal_line)


def last_cross_index(a: pd.Series, b: pd.Series) -> int | None:
    """Bars since ``a`` last crossed ``b``, or None if no crossover is in range.

    Returns 0 when the crossover happened on the most recent bar. Used purely
    for the plain-language reasoning ("golden cross 4 sessions ago"); the score
    itself comes from the size of the spread, not the recency of the event.
    """
    diff = (a - b).dropna()
    if len(diff) < 2:
        return None
    sign = np.sign(diff.to_numpy())
    # Ignore exact zeros by carrying the previous sign forward.
    for i in range(1, len(sign)):
        if sign[i] == 0:
            sign[i] = sign[i - 1]
    flips = np.nonzero(sign[1:] != sign[:-1])[0]
    if len(flips) == 0:
        return None
    # flips[-1] indexes into the diff array; distance from the final bar.
    return int(len(sign) - 1 - (flips[-1] + 1))


def pct_change_over(close: pd.Series, lookback: int) -> float | None:
    """Percentage change over the last ``lookback`` bars, or None if too short."""
    clean = close.dropna()
    if len(clean) < lookback + 1:
        return None
    past = float(clean.iloc[-(lookback + 1)])
    if past == 0:
        return None
    return (float(clean.iloc[-1]) / past - 1.0) * 100.0


def realised_volatility(close: pd.Series, window: int = 21, annualise: bool = True) -> pd.Series:
    """Rolling realised (historical) volatility from log returns.

    Annualised with 252 trading days so the scale is comparable to the implied
    volatility quoted on option chains.
    """
    if window < 2:
        raise ValueError(f"window must be >= 2, got {window}")
    log_ret = np.log(close / close.shift(1))
    vol = log_ret.rolling(window=window, min_periods=window).std(ddof=1)
    if annualise:
        vol = vol * np.sqrt(252.0)
    return vol * 100.0  # express as a percentage, matching chain IV units


def percentile_rank(series: pd.Series, value: float | None = None) -> float | None:
    """Percentile (0-100) of ``value`` within ``series``.

    Defaults to ranking the series' own final observation, which is what both
    IV Rank (against stored IV history) and the realised-volatility proxy need.

    Returns None when there is nothing to rank against. A series where every
    observation is identical returns 50.0 — the value is neither high nor low
    in its own range, and returning 0 or 100 there would be misleading.
    """
    clean = series.dropna()
    if clean.empty:
        return None
    if value is None:
        value = float(clean.iloc[-1])
    if len(clean) == 1:
        return 50.0
    lo, hi = float(clean.min()), float(clean.max())
    if hi == lo:
        return 50.0
    # Rank-based percentile: robust to outliers in a way min/max scaling is not.
    below = float((clean < value).sum())
    equal = float((clean == value).sum())
    return clamp_pct(100.0 * (below + 0.5 * equal) / len(clean))


def clamp_pct(value: float) -> float:
    return max(0.0, min(100.0, value))
