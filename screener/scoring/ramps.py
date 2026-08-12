"""Continuous scoring primitives.

The screener converts raw indicator values into scores with smooth ramps rather
than hard pass/fail tests. The reason is that this tool *ranks* — with a hard
threshold, RSI 29.9 and RSI 30.1 land at opposite extremes while being
practically identical, and deeply oversold RSI 12 is indistinguishable from
barely oversold RSI 29. Smooth ramps keep the ordering meaningful.

Convention: a threshold is the **half-credit point** (score 0.5), and ``width``
is the distance past it that reaches ~88% strength. Setting a small width
approximates hard pass/fail behaviour, so the ramp generalises both styles.
"""

from __future__ import annotations

import math

__all__ = ["logistic", "ramp01", "signed_ramp", "window_fit", "clamp"]


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def logistic(z: float) -> float:
    """Numerically stable logistic function.

    ``math.exp`` overflows around z = -745; the branch below keeps extreme
    inputs (which do occur, e.g. a MACD histogram far from zero) finite.
    """
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def ramp01(value: float, midpoint: float, width: float, rising: bool = True) -> float:
    """Map a value to [0, 1] with 0.5 at ``midpoint``.

    Args:
        value: the raw measurement.
        midpoint: the user's threshold — scores exactly 0.5.
        width: distance past the midpoint scoring ~0.88 (two logistic units).
        rising: True when larger values are better; False to invert.

    Returns:
        A score in (0, 1).

    >>> round(ramp01(30, midpoint=30, width=10), 3)
    0.5
    >>> round(ramp01(40, midpoint=30, width=10), 3)
    0.881
    """
    if width <= 0:
        raise ValueError(f"width must be positive, got {width}")
    z = 2.0 * (value - midpoint) / width
    if not rising:
        z = -z
    return logistic(z)


def signed_ramp(value: float, midpoint: float, width: float) -> float:
    """Map a value to [-1, +1] with 0.0 at ``midpoint``.

    Used for directional stock-layer parameters where the midpoint is the
    neutral crossover point (e.g. an MA spread of zero).

    >>> signed_ramp(0, midpoint=0, width=1)
    0.0
    """
    return 2.0 * ramp01(value, midpoint, width) - 1.0


def window_fit(value: float, low: float, high: float, tolerance: float) -> float:
    """Score membership of a target band: 1.0 inside, Gaussian decay outside.

    Used for "fit" parameters (days-to-expiration, delta proximity) where
    anything inside the band you specified is equally acceptable and the
    penalty should grow with distance outside it.

    >>> window_fit(60, low=45, high=90, tolerance=15)
    1.0
    >>> round(window_fit(30, low=45, high=90, tolerance=15), 3)
    0.368
    """
    if tolerance <= 0:
        raise ValueError(f"tolerance must be positive, got {tolerance}")
    if low > high:
        raise ValueError(f"low ({low}) must not exceed high ({high})")
    if low <= value <= high:
        return 1.0
    distance = low - value if value < low else value - high
    return math.exp(-((distance / tolerance) ** 2))


def soft_sign(value: float, scale: float) -> float:
    """Continuous replacement for ``sign()`` in [-1, +1].

    A hard ``sign()`` on a near-zero measurement flips the whole signal on
    noise. ``soft_sign`` fades through zero instead: a move of ``scale``
    reaches about +0.76, while a flat tape stays near 0.

    >>> soft_sign(0.0, 3.0)
    0.0
    """
    if scale <= 0:
        raise ValueError(f"scale must be positive, got {scale}")
    return math.tanh(value / scale)
