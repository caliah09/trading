"""Declarative registry of every scoring parameter.

This module is the single source of truth for:

  * which parameters exist,
  * their default weights and thresholds,
  * the metadata the Settings UI needs to render editable controls.

Nothing here computes anything. Keeping the *description* of the parameters
separate from their *implementation* (``screener.scoring.parameters``) means the
Settings page can be generated generically, and adding a parameter is a matter of
registering a spec plus a scorer rather than touching the UI.

Layers
------
``stock``   Directional. Produces a **signed** signal in [-1, +1] where +1 is
            maximally bullish and -1 maximally bearish. These decide *whether*
            there is a setup and in *which* direction.
``options`` Non-directional quality. Produces an **unsigned** signal in [0, 1]
            describing how well the options market lets you express that setup.
            These are evaluated *given* the direction chosen by the stock layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

Layer = Literal["stock", "options"]


@dataclass(frozen=True)
class ThresholdSpec:
    """One user-editable numeric knob belonging to a parameter."""

    key: str
    label: str
    default: float
    minimum: float
    maximum: float
    step: float
    help: str


@dataclass(frozen=True)
class ParamSpec:
    """Static description of a scoring parameter."""

    key: str
    label: str
    layer: Layer
    default_weight: float
    summary: str
    thresholds: tuple[ThresholdSpec, ...] = field(default_factory=tuple)
    #: True when the parameter's meaning flips with buy/sell posture.
    posture_aware: bool = False

    def default_thresholds(self) -> dict[str, float]:
        return {t.key: t.default for t in self.thresholds}


# --------------------------------------------------------------------------
# Stock layer — signed, directional
# --------------------------------------------------------------------------

RSI = ParamSpec(
    key="rsi",
    label="RSI (14)",
    layer="stock",
    default_weight=8.0,
    summary=(
        "Relative Strength Index over 14 sessions. Low readings suggest the stock is "
        "oversold (bullish lean); high readings suggest overbought (bearish lean)."
    ),
    thresholds=(
        ThresholdSpec("oversold", "Oversold below", 30.0, 5.0, 50.0, 1.0,
                      "RSI level at which the bullish read is half strength."),
        ThresholdSpec("overbought", "Overbought above", 70.0, 50.0, 95.0, 1.0,
                      "RSI level at which the bearish read is half strength."),
        ThresholdSpec("width", "Transition width", 12.0, 2.0, 30.0, 1.0,
                      "How many RSI points past the threshold reach ~88% strength. "
                      "Smaller = sharper, more pass/fail-like."),
    ),
)

MA_CROSS = ParamSpec(
    key="ma_cross",
    label="MA 20/50 crossover",
    layer="stock",
    default_weight=7.0,
    summary=(
        "Spread between the 20-day and 50-day moving averages, as a percentage of the "
        "50-day. Positive means short-term trend is above long-term (golden-cross side)."
    ),
    thresholds=(
        ThresholdSpec("width", "Transition width (%)", 1.5, 0.1, 10.0, 0.1,
                      "MA spread, in percent, that reaches ~88% signal strength."),
        ThresholdSpec("fresh_cross_days", "Flag crosses newer than (days)", 15.0, 1.0, 90.0, 1.0,
                      "Crossovers more recent than this are called out in the reasoning."),
    ),
)

MACD = ParamSpec(
    key="macd",
    label="MACD crossover",
    layer="stock",
    default_weight=7.0,
    summary=(
        "MACD(12,26,9) histogram — the gap between the MACD line and its signal line, "
        "normalised by price so it is comparable across tickers. Positive is bullish momentum."
    ),
    thresholds=(
        ThresholdSpec("width", "Transition width (% of price)", 0.15, 0.01, 2.0, 0.01,
                      "Histogram size, as a percent of price, reaching ~88% strength."),
        ThresholdSpec("fresh_cross_days", "Flag crosses newer than (days)", 10.0, 1.0, 60.0, 1.0,
                      "MACD/signal crossovers newer than this are called out."),
    ),
)

MA200 = ParamSpec(
    key="ma200",
    label="200-day MA support/resistance",
    layer="stock",
    default_weight=6.0,
    summary=(
        "Distance of price from the 200-day moving average, in percent. Above is a bullish "
        "regime (the 200-day acts as support); below is bearish (it acts as resistance)."
    ),
    thresholds=(
        ThresholdSpec("width", "Transition width (%)", 3.0, 0.25, 15.0, 0.25,
                      "Percent distance from the 200-day reaching ~88% strength."),
        ThresholdSpec("proximity", "'Testing the line' within (%)", 1.5, 0.1, 10.0, 0.1,
                      "Inside this distance the reasoning flags an active support/resistance test."),
    ),
)

VOLUME = ParamSpec(
    key="volume",
    label="Volume vs 30-day average",
    layer="stock",
    default_weight=5.0,
    summary=(
        "Latest session volume as a multiple of its 30-day average, signed by recent price "
        "direction. Heavy volume on an advance is bullish confirmation; heavy volume on a "
        "decline is bearish confirmation."
    ),
    thresholds=(
        ThresholdSpec("surge_ratio", "Surge multiple", 1.5, 1.0, 5.0, 0.1,
                      "Volume multiple counted as a half-strength surge."),
        ThresholdSpec("width", "Transition width (x)", 0.75, 0.1, 3.0, 0.05,
                      "Additional volume multiple reaching ~88% strength."),
        ThresholdSpec("direction_lookback", "Price direction lookback (days)", 5.0, 1.0, 30.0, 1.0,
                      "Window whose return decides whether the surge is bullish or bearish."),
        ThresholdSpec("direction_scale", "Full-conviction move (%)", 3.0, 0.5, 20.0, 0.5,
                      "Return over the lookback that counts as an unambiguous direction. "
                      "Smaller moves scale the signal down smoothly instead of flipping sign."),
    ),
)

# --------------------------------------------------------------------------
# Options layer — unsigned quality, posture-aware
# --------------------------------------------------------------------------

IV_RANK = ParamSpec(
    key="iv_rank",
    label="IV Rank / Percentile",
    layer="options",
    default_weight=6.0,
    summary=(
        "Where current implied volatility sits within its trailing one-year range (0-100). "
        "When buying premium, low IV Rank is good (options are cheap). When selling premium, "
        "high IV Rank is good (premium is rich)."
    ),
    posture_aware=True,
    thresholds=(
        ThresholdSpec("midpoint", "Half-credit at IV Rank", 50.0, 0.0, 100.0, 1.0,
                      "IV Rank scoring 0.5. Buying posture rewards values below it; "
                      "selling posture rewards values above it."),
        ThresholdSpec("width", "Transition width", 25.0, 5.0, 50.0, 1.0,
                      "IV Rank points past the midpoint reaching ~88% strength."),
        ThresholdSpec("min_observations", "Real-IV switchover (observations)", 60.0, 20.0, 252.0, 1.0,
                      "Stored IV snapshots required before true IV Rank replaces the "
                      "realised-volatility proxy."),
        ThresholdSpec("hv_window", "Realised-vol window (days)", 21.0, 5.0, 63.0, 1.0,
                      "Lookback used to compute realised volatility for the proxy."),
    ),
)

DTE_FIT = ParamSpec(
    key="dte_fit",
    label="Days-to-expiration fit",
    layer="options",
    default_weight=4.0,
    summary=(
        "How well the best available expiry matches your target holding window. Full credit "
        "inside the window, decaying smoothly outside it."
    ),
    posture_aware=True,
    thresholds=(
        ThresholdSpec("tolerance", "Decay tolerance (days)", 15.0, 1.0, 90.0, 1.0,
                      "Days outside the target window at which the score falls to ~37%."),
    ),
)

DELTA_FIT = ParamSpec(
    key="delta_fit",
    label="Delta proximity to target",
    layer="options",
    default_weight=5.0,
    summary=(
        "How close the selected contract's delta is to your target. Delta is computed with "
        "Black-Scholes from the chain's implied volatility, because yfinance does not "
        "publish greeks."
    ),
    posture_aware=True,
    thresholds=(
        ThresholdSpec("tolerance", "Decay tolerance (delta)", 0.10, 0.01, 0.50, 0.01,
                      "Delta distance outside the target band at which the score falls to ~37%."),
    ),
)

EARNINGS = ParamSpec(
    key="earnings",
    label="Earnings-date proximity",
    layer="options",
    default_weight=4.0,
    summary=(
        "Trading days until the next scheduled earnings report. When buying premium, an "
        "approaching report is an opportunity (volatility expansion). When selling premium, "
        "it is a hazard (overnight gap risk)."
    ),
    posture_aware=True,
    thresholds=(
        ThresholdSpec("buy_sweet_spot", "Buying: ideal days out", 10.0, 1.0, 60.0, 1.0,
                      "Days before earnings that scores best when buying premium."),
        ThresholdSpec("buy_tolerance", "Buying: tolerance (days)", 12.0, 1.0, 60.0, 1.0,
                      "How quickly the buying score decays away from the sweet spot."),
        ThresholdSpec("buy_min_days", "Buying: too-close cutoff (days)", 2.0, 0.0, 10.0, 1.0,
                      "Inside this many days the report is a coin-flip; score is suppressed."),
        ThresholdSpec("sell_avoid_days", "Selling: avoid within (days)", 30.0, 0.0, 90.0, 1.0,
                      "Earnings inside this window are penalised when selling premium."),
        ThresholdSpec("sell_width", "Selling: transition width (days)", 10.0, 1.0, 45.0, 1.0,
                      "Days past the avoid window reaching ~88% strength."),
    ),
)


ALL_SPECS: tuple[ParamSpec, ...] = (
    RSI, MA_CROSS, MACD, MA200, VOLUME,
    IV_RANK, DTE_FIT, DELTA_FIT, EARNINGS,
)

SPECS_BY_KEY: dict[str, ParamSpec] = {s.key: s for s in ALL_SPECS}

STOCK_KEYS: tuple[str, ...] = tuple(s.key for s in ALL_SPECS if s.layer == "stock")
OPTIONS_KEYS: tuple[str, ...] = tuple(s.key for s in ALL_SPECS if s.layer == "options")


def spec(key: str) -> ParamSpec:
    """Look up a spec, raising a clear error for unknown keys."""
    try:
        return SPECS_BY_KEY[key]
    except KeyError:
        raise KeyError(
            f"Unknown parameter {key!r}. Known parameters: {sorted(SPECS_BY_KEY)}"
        ) from None
