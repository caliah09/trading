"""Black-Scholes greeks.

**Why this module exists:** yfinance's ``.option_chain()`` returns strike, bid,
ask, volume, open interest and implied volatility — but *no greeks*. Delta is a
scoring parameter, so it has to be computed locally from the chain's own IV.

Assumptions, all of which are approximations worth knowing about:

* European exercise. US equity options are American; for the near-the-money,
  non-dividend-heavy contracts this screener targets, the delta difference is
  small, but it is not zero for deep ITM puts.
* A flat risk-free rate and continuous dividend yield taken from config rather
  than a live curve.
* The chain's published implied volatility is taken at face value. Yahoo's IV
  is unreliable for illiquid strikes, which is one reason chain hygiene filters
  (open interest, spread width) are applied before selection.
"""

from __future__ import annotations

import math

__all__ = ["d1", "delta", "OptionKind"]

OptionKind = str  # "call" | "put"

_SQRT_2 = math.sqrt(2.0)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF via the error function (no SciPy dependency)."""
    return 0.5 * (1.0 + math.erf(x / _SQRT_2))


def d1(spot: float, strike: float, time_years: float, vol: float,
       rate: float = 0.0, dividend: float = 0.0) -> float | None:
    """The Black-Scholes ``d1`` term, or None when inputs are degenerate."""
    if spot <= 0 or strike <= 0 or time_years <= 0 or vol <= 0:
        return None
    numerator = math.log(spot / strike) + (rate - dividend + 0.5 * vol * vol) * time_years
    return numerator / (vol * math.sqrt(time_years))


def delta(spot: float, strike: float, time_years: float, vol: float,
          kind: OptionKind, rate: float = 0.0, dividend: float = 0.0) -> float | None:
    """Black-Scholes delta.

    Args:
        spot: underlying price.
        strike: contract strike.
        time_years: time to expiry in years (e.g. 45/365).
        vol: implied volatility as a decimal (0.32 for 32%).
        kind: "call" or "put".
        rate: continuously compounded risk-free rate.
        dividend: continuous dividend yield.

    Returns:
        Call delta in (0, 1), put delta in (-1, 0), or None if the inputs cannot
        support a calculation (expired, zero vol, missing price).

    Expiry-day and zero-vol contracts return None rather than a degenerate 0/1
    step, because a delta of exactly 1.0 would otherwise score as a real
    measurement rather than as missing data.
    """
    if kind not in ("call", "put"):
        raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")
    d_1 = d1(spot, strike, time_years, vol, rate, dividend)
    if d_1 is None:
        return None
    discount = math.exp(-dividend * time_years)
    if kind == "call":
        return discount * _norm_cdf(d_1)
    return discount * (_norm_cdf(d_1) - 1.0)
