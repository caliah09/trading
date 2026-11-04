"""Individual parameter scorers.

Each scorer takes a :class:`TickerData` bundle and returns a
:class:`ParameterResult` carrying four things the UI needs:

* ``raw_value`` — what was actually measured (RSI 22.4, IV Rank 31, delta 0.53)
* ``signal``    — that value converted to a score
* ``explanation`` — a plain-language sentence explaining what it means
* ``status``    — whether the measurement succeeded at all

Signal conventions:

* **stock layer** — signed, [-1, +1]. +1 maximally bullish, -1 maximally
  bearish, 0 no directional edge.
* **options layer** — unsigned, [0, 1]. 1 is an ideal way to express the trade.

Scorers never raise on bad data. They return ``status="insufficient_data"`` with
an explanation, and the engine redistributes the weight.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

import pandas as pd

from screener import indicators
from screener.config import ScreenerConfig
from screener.market_calendar import trading_days_between
from screener.options_analysis import OptionsAssessment
from screener.param_specs import spec
from screener.patterns.head_shoulders import EXPECTED_HOLD_DAYS, detect_head_shoulders
from screener.pivots import find_pivots
from screener.scoring.ramps import ramp01, signed_ramp, soft_sign, window_fit

Status = Literal["ok", "insufficient_data", "not_applicable", "disabled"]

#: Trailing window for volatility ranking — one year of sessions.
VOL_RANK_WINDOW = 252


@dataclass
class ParameterResult:
    """One parameter's complete, auditable contribution to a ticker's score."""

    key: str
    label: str
    layer: str
    status: Status
    weight: float
    signal: float | None = None
    raw_value: float | None = None
    display_value: str = "—"
    explanation: str = ""
    #: Filled in by the engine once the weight denominator is known.
    effective_weight: float = 0.0
    contribution: float = 0.0
    #: Extra machine-readable context for the detail view.
    detail: dict = field(default_factory=dict)

    @property
    def counted(self) -> bool:
        """True when this parameter took part in the composite score."""
        return self.status == "ok" and self.signal is not None

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "layer": self.layer,
            "status": self.status,
            "weight": round(self.weight, 3),
            "signal": None if self.signal is None else round(self.signal, 4),
            "raw_value": None if self.raw_value is None else round(self.raw_value, 4),
            "display_value": self.display_value,
            "explanation": self.explanation,
            "effective_weight": round(self.effective_weight, 3),
            "contribution": round(self.contribution, 3),
            "detail": self.detail,
        }


@dataclass
class TickerData:
    """Everything gathered for one ticker before scoring."""

    symbol: str
    prices: pd.DataFrame
    today: date
    options: OptionsAssessment | None = None
    earnings_date: date | None = None
    is_etf: bool = False
    #: Historical at-the-money IV observations, date-indexed (decimals).
    iv_history: pd.Series = field(default_factory=lambda: pd.Series(dtype="float64"))

    @property
    def close(self) -> pd.Series:
        return self.prices["close"] if "close" in self.prices else pd.Series(dtype="float64")

    @property
    def spot(self) -> float | None:
        close = self.close.dropna()
        return float(close.iloc[-1]) if len(close) else None

    @property
    def bar_count(self) -> int:
        return len(self.prices)


def _missing(key: str, weight: float, why: str) -> ParameterResult:
    s = spec(key)
    return ParameterResult(
        key=key, label=s.label, layer=s.layer, status="insufficient_data",
        weight=weight, explanation=why,
    )


def _not_applicable(key: str, weight: float, why: str) -> ParameterResult:
    s = spec(key)
    return ParameterResult(
        key=key, label=s.label, layer=s.layer, status="not_applicable",
        weight=weight, explanation=why,
    )


def _lean(signal: float) -> str:
    """Plain-language direction word for a signed signal."""
    if signal > 0.35:
        return "Bullish lean."
    if signal > 0.08:
        return "Mildly bullish."
    if signal < -0.35:
        return "Bearish lean."
    if signal < -0.08:
        return "Mildly bearish."
    return "No directional edge."


# ==========================================================================
# Stock layer
# ==========================================================================

def score_rsi(data: TickerData, config: ScreenerConfig) -> ParameterResult:
    key = "rsi"
    weight = config.parameters[key].weight
    period = 14
    if data.bar_count < period + 1:
        return _missing(key, weight,
                        f"Only {data.bar_count} sessions of history; RSI(14) needs at least {period + 1}.")

    series = indicators.rsi(data.close, period).dropna()
    if series.empty:
        return _missing(key, weight, "RSI could not be computed from the available price history.")

    value = float(series.iloc[-1])
    oversold = config.threshold(key, "oversold")
    overbought = config.threshold(key, "overbought")
    width = config.threshold(key, "width")

    # Two one-sided ramps, so a mid-range RSI nets to zero rather than
    # accidentally leaning bullish or bearish.
    bullish = ramp01(oversold - value, 0.0, width)
    bearish = ramp01(value - overbought, 0.0, width)
    signal = bullish - bearish

    # These branches must agree with the sign of `signal`. Because the threshold
    # is a half-credit point rather than a hard gate, a value just short of it
    # still carries a real lean, and the wording has to say so — otherwise the
    # sentence reads as "not stretched in either direction. Bullish lean."
    if value <= oversold:
        note = (f"RSI {value:.1f} is at or below your oversold line of {oversold:.0f}, "
                "meaning the recent selling has been persistent enough that buyers often step in.")
    elif value >= overbought:
        note = (f"RSI {value:.1f} is at or above your overbought line of {overbought:.0f}, "
                "meaning the advance has been steep enough that it often stalls or retraces.")
    elif signal > 0.15:
        note = (f"RSI {value:.1f} is approaching your oversold line of {oversold:.0f} without "
                "having reached it — the selling is persistent but not yet at the level you flagged.")
    elif signal < -0.15:
        note = (f"RSI {value:.1f} is approaching your overbought line of {overbought:.0f} without "
                "having reached it — the advance is extended but not yet at the level you flagged.")
    else:
        note = (f"RSI {value:.1f} sits between your {oversold:.0f}/{overbought:.0f} lines — "
                "momentum is not stretched in either direction.")

    return ParameterResult(
        key=key, label=spec(key).label, layer="stock", status="ok", weight=weight,
        signal=signal, raw_value=value, display_value=f"{value:.1f}",
        explanation=f"{note} {_lean(signal)}",
        detail={"oversold": oversold, "overbought": overbought},
    )


def score_ma_cross(data: TickerData, config: ScreenerConfig) -> ParameterResult:
    key = "ma_cross"
    weight = config.parameters[key].weight
    if data.bar_count < 50:
        return _missing(key, weight,
                        f"Only {data.bar_count} sessions of history; the 50-day average needs 50.")

    ma20 = indicators.sma(data.close, 20)
    ma50 = indicators.sma(data.close, 50)
    if pd.isna(ma20.iloc[-1]) or pd.isna(ma50.iloc[-1]) or float(ma50.iloc[-1]) == 0:
        return _missing(key, weight, "Moving averages could not be computed from the available history.")

    fast, slow = float(ma20.iloc[-1]), float(ma50.iloc[-1])
    spread_pct = (fast - slow) / slow * 100.0
    width = config.threshold(key, "width")
    signal = signed_ramp(spread_pct, 0.0, width)

    bars_since = indicators.last_cross_index(ma20, ma50)
    fresh_limit = config.threshold(key, "fresh_cross_days")

    side = "above" if spread_pct >= 0 else "below"
    note = (f"The 20-day average ({fast:,.2f}) is {abs(spread_pct):.2f}% {side} "
            f"the 50-day ({slow:,.2f}).")
    if bars_since is not None and bars_since <= fresh_limit:
        cross = "golden cross" if spread_pct >= 0 else "death cross"
        when = "today" if bars_since == 0 else f"{bars_since} session{'s' if bars_since != 1 else ''} ago"
        note += f" That {cross} happened {when}, so the trend change is recent."
    elif bars_since is not None:
        note += f" The last crossover was {bars_since} sessions ago, so this trend is established."
    else:
        note += " No crossover occurred within the available history."

    return ParameterResult(
        key=key, label=spec(key).label, layer="stock", status="ok", weight=weight,
        signal=signal, raw_value=spread_pct, display_value=f"{spread_pct:+.2f}%",
        explanation=f"{note} {_lean(signal)}",
        detail={"ma20": round(fast, 4), "ma50": round(slow, 4), "bars_since_cross": bars_since},
    )


def score_macd(data: TickerData, config: ScreenerConfig) -> ParameterResult:
    key = "macd"
    weight = config.parameters[key].weight
    if data.bar_count < 35:  # 26-period EMA plus a 9-period signal needs room to settle
        return _missing(key, weight,
                        f"Only {data.bar_count} sessions of history; MACD(12,26,9) needs about 35.")

    result = indicators.macd(data.close)
    hist = result.histogram.dropna()
    spot = data.spot
    if hist.empty or not spot:
        return _missing(key, weight, "MACD could not be computed from the available price history.")

    hist_value = float(hist.iloc[-1])
    # Normalise by price so a $400 stock and a $20 stock are comparable.
    hist_pct = hist_value / spot * 100.0
    width = config.threshold(key, "width")
    signal = signed_ramp(hist_pct, 0.0, width)

    bars_since = indicators.last_cross_index(result.macd, result.signal)
    fresh_limit = config.threshold(key, "fresh_cross_days")

    state = "above" if hist_value >= 0 else "below"
    note = (f"The MACD line is {state} its signal line by {abs(hist_pct):.3f}% of price — "
            f"{'upward' if hist_value >= 0 else 'downward'} momentum is currently in control.")
    if bars_since is not None and bars_since <= fresh_limit:
        when = "today" if bars_since == 0 else f"{bars_since} session{'s' if bars_since != 1 else ''} ago"
        note += f" The lines crossed {when}, which is the fresh-signal window you configured."

    return ParameterResult(
        key=key, label=spec(key).label, layer="stock", status="ok", weight=weight,
        signal=signal, raw_value=hist_pct, display_value=f"{hist_pct:+.3f}%",
        explanation=f"{note} {_lean(signal)}",
        detail={"histogram": round(hist_value, 6), "bars_since_cross": bars_since},
    )


def score_ma200(data: TickerData, config: ScreenerConfig) -> ParameterResult:
    key = "ma200"
    weight = config.parameters[key].weight
    needed = config.min_bars_200ma
    if data.bar_count < needed:
        return _missing(key, weight,
                        f"Only {data.bar_count} sessions of history; the 200-day average needs {needed}.")

    ma200 = indicators.sma(data.close, 200)
    spot = data.spot
    if pd.isna(ma200.iloc[-1]) or not spot or float(ma200.iloc[-1]) == 0:
        return _missing(key, weight, "The 200-day average could not be computed.")

    level = float(ma200.iloc[-1])
    distance_pct = (spot - level) / level * 100.0
    width = config.threshold(key, "width")
    proximity = config.threshold(key, "proximity")
    signal = signed_ramp(distance_pct, 0.0, width)

    # Slope over the last month tells you whether the level itself is improving.
    slope_ref = ma200.dropna()
    slope_note = ""
    if len(slope_ref) > 21:
        earlier = float(slope_ref.iloc[-22])
        if earlier:
            slope_pct = (level - earlier) / earlier * 100.0
            trend = "rising" if slope_pct > 0.1 else ("falling" if slope_pct < -0.1 else "flat")
            slope_note = f" The 200-day itself is {trend} ({slope_pct:+.2f}% over the past month)."

    if abs(distance_pct) <= proximity:
        note = (f"Price ({spot:,.2f}) is within {proximity:.1f}% of the 200-day average "
                f"({level:,.2f}) — an active test of this level, which often resolves sharply.")
    elif distance_pct > 0:
        note = (f"Price ({spot:,.2f}) is {distance_pct:.2f}% above the 200-day average "
                f"({level:,.2f}), the long-term line institutions watch as support.")
    else:
        note = (f"Price ({spot:,.2f}) is {abs(distance_pct):.2f}% below the 200-day average "
                f"({level:,.2f}), which now sits overhead as resistance.")

    return ParameterResult(
        key=key, label=spec(key).label, layer="stock", status="ok", weight=weight,
        signal=signal, raw_value=distance_pct, display_value=f"{distance_pct:+.2f}%",
        explanation=f"{note}{slope_note} {_lean(signal)}",
        detail={"ma200": round(level, 4), "testing_level": abs(distance_pct) <= proximity},
    )


def score_volume(data: TickerData, config: ScreenerConfig) -> ParameterResult:
    key = "volume"
    weight = config.parameters[key].weight
    if "volume" not in data.prices or data.bar_count < 31:
        return _missing(key, weight,
                        f"Only {data.bar_count} sessions of history; a 30-day volume average needs 31.")

    volume = data.prices["volume"].dropna()
    if len(volume) < 31:
        return _missing(key, weight, "Not enough volume observations for a 30-day average.")

    latest = float(volume.iloc[-1])
    # Exclude the latest bar from its own baseline, otherwise a surge dilutes itself.
    average = float(volume.iloc[-31:-1].mean())
    if average <= 0:
        return _missing(key, weight, "The 30-day average volume is zero, so no comparison is possible.")

    ratio = latest / average
    surge = config.threshold(key, "surge_ratio")
    width = config.threshold(key, "width")
    lookback = int(config.threshold(key, "direction_lookback"))
    scale = config.threshold(key, "direction_scale")

    strength = ramp01(ratio, surge, width)
    move_pct = indicators.pct_change_over(data.close, lookback)
    if move_pct is None:
        return _missing(key, weight,
                        f"Not enough history to measure the {lookback}-session price move that "
                        "gives the volume its direction.")

    # Volume alone is directionless; it confirms whichever way price moved.
    # soft_sign fades through zero so a flat tape does not flip the signal on noise.
    direction = soft_sign(move_pct, scale)
    signal = strength * direction

    if ratio >= surge:
        vol_note = (f"Volume is {ratio:.2f}x its 30-day average — heavier than your "
                    f"{surge:.1f}x surge threshold.")
    else:
        vol_note = f"Volume is {ratio:.2f}x its 30-day average, below your {surge:.1f}x surge threshold."

    move_note = (f" Price moved {move_pct:+.2f}% over the last {lookback} sessions, so that "
                 f"participation is {'confirming buyers' if move_pct > 0 else 'confirming sellers'}.")
    if abs(move_pct) < scale * 0.2:
        move_note = (f" Price is close to flat ({move_pct:+.2f}%) over the last {lookback} sessions, "
                     "so the volume has no clear direction behind it.")

    return ParameterResult(
        key=key, label=spec(key).label, layer="stock", status="ok", weight=weight,
        signal=signal, raw_value=ratio, display_value=f"{ratio:.2f}x",
        explanation=f"{vol_note}{move_note} {_lean(signal)}",
        detail={"latest_volume": latest, "average_volume": average, "move_pct": round(move_pct, 3)},
    )


def score_head_shoulders(data: TickerData, config: ScreenerConfig) -> ParameterResult:
    key = "head_shoulders"
    weight = config.parameters[key].weight

    sensitivity = config.threshold(key, "sensitivity")
    retest_tolerance = config.threshold(key, "retest_tolerance")
    trend_lookback = int(config.threshold(key, "trend_lookback"))
    min_completion = config.threshold(key, "min_completion")

    if data.bar_count < 60:
        return _missing(key, weight,
                        f"Only {data.bar_count} sessions of history; pattern detection needs "
                        "at least 60 to find enough swing points.")

    pivots = find_pivots(data.prices, sensitivity=sensitivity)
    if len(pivots) < 5:
        return _missing(key, weight,
                        f"Only {len(pivots)} confirmed swing point(s) at your {sensitivity:.1f}x ATR "
                        "sensitivity — a Head & Shoulders needs five. Lower the sensitivity to "
                        "detect smaller swings.")

    pattern = detect_head_shoulders(
        data.prices, pivots,
        retest_tolerance_pct=retest_tolerance,
        trend_lookback=trend_lookback,
    )
    if pattern is None:
        return _missing(key, weight,
                        f"No valid Head & Shoulders in the last {len(pivots)} swing points. The "
                        "shape requires a middle extreme higher (or lower) than both shoulders; "
                        "nothing here qualifies.")

    if pattern.completion < min_completion:
        return ParameterResult(
            key=key, label=spec(key).label, layer="stock", status="ok", weight=weight,
            signal=0.0, raw_value=pattern.completion,
            display_value=f"{pattern.direction} · {pattern.state}",
            explanation=(
                f"A {pattern.direction} Head & Shoulders is {pattern.state}, which is below your "
                f"minimum completion of {min_completion:.1f}. It is shown for awareness but "
                "contributes nothing to the score."
            ),
            detail=pattern.to_dict(),
        )

    # Completion carries the price-action weight; quality only modulates it, so
    # an asymmetric but confirmed pattern still scores well.
    signal = pattern.sign * pattern.completion * (0.6 + 0.4 * pattern.quality)

    lean = "bearish" if pattern.direction == "bearish" else "bullish"
    structure = ("lower high followed by a lower low" if pattern.direction == "bearish"
                 else "higher low followed by a higher high")

    if pattern.completion >= 1.0:
        headline = (f"A {lean} Head & Shoulders is complete — the neckline broke and the "
                    f"confirming {structure.split(' followed by a ')[1]} printed.")
    elif pattern.completion >= 0.7:
        headline = f"A {lean} Head & Shoulders has broken its neckline but is not yet confirmed."
    elif pattern.completion >= 0.5:
        headline = (f"A {lean} Head & Shoulders has price back at the right-shoulder level, "
                    "the aggressive early-entry zone.")
    else:
        headline = f"A {lean} Head & Shoulders is forming; the neckline is untested."

    parts = [
        headline,
        f"The pattern rests on a {structure}, which is what makes it valid — "
        f"symmetry here is {pattern.symmetry * 100:.0f}% and is only a confidence factor.",
    ]
    parts.extend(pattern.notes)
    if pattern.target is not None:
        parts.append(
            f"The measured move projects roughly {pattern.target:,.2f}. Research puts this "
            f"pattern's cost-surviving edge near a {EXPECTED_HOLD_DAYS}-trading-day hold, so an "
            "expiry shorter than that is trading a horizon the evidence does not support."
        )

    return ParameterResult(
        key=key, label=spec(key).label, layer="stock", status="ok", weight=weight,
        signal=signal, raw_value=pattern.completion,
        display_value=f"{pattern.direction} · {pattern.state}",
        explanation=" ".join(parts),
        detail=pattern.to_dict(),
    )


# ==========================================================================
# Options layer — unsigned quality, posture-aware
# ==========================================================================

def score_vol_risk_premium(data: TickerData, config: ScreenerConfig) -> ParameterResult:
    key = "vol_risk_premium"
    weight = config.parameters[key].weight

    atm_iv = data.options.atm_iv if data.options else None
    if atm_iv is None:
        return _missing(key, weight,
                        "No at-the-money implied volatility was available, so there is nothing "
                        "to compare against realised volatility.")

    rv_window = int(config.threshold(key, "rv_window"))
    if data.bar_count < rv_window + 2:
        return _missing(key, weight,
                        f"Only {data.bar_count} sessions of history; a {rv_window}-day realised "
                        "volatility needs more.")

    realised = indicators.realised_volatility(data.close, rv_window).dropna()
    if realised.empty:
        return _missing(key, weight, "Realised volatility could not be computed.")

    realised_vol = float(realised.iloc[-1])      # already a percentage
    implied_vol = float(atm_iv) * 100.0
    spread = implied_vol - realised_vol          # vol points

    normal = config.threshold(key, "normal_premium")
    width = config.threshold(key, "width")
    buying = config.posture == "buy"

    # Measured against the historical premium, not against zero: collecting the
    # usual 4 points is par, and paying it is the normal cost of being long.
    signal = ramp01(spread, normal, width, rising=not buying)

    basis = (f"Implied volatility is {implied_vol:.1f}% against {realised_vol:.1f}% realised over "
             f"{rv_window} sessions — a spread of {spread:+.1f} points versus a typical {normal:.1f}.")

    if buying:
        if spread < normal:
            meaning = ("You are paying less than the usual premium for exposure to a stock that "
                       "has been moving more than its options imply. That is the favourable side "
                       "of this trade for a buyer.")
        else:
            meaning = ("You are paying above the usual premium relative to how much the stock has "
                       "actually been moving. The direction has to be right by more than normal "
                       "just to cover the volatility you are overpaying for.")
    else:
        if spread > normal:
            meaning = ("You are being paid more than the usual premium relative to how much the "
                       "stock has actually moved. This is the gap option sellers earn, and it is "
                       "wider than its historical norm here.")
        else:
            meaning = ("You are being paid less than the usual premium for the risk taken on. The "
                       "stock has been moving nearly as much as its options imply, so there is "
                       "little cushion.")

    return ParameterResult(
        key=key, label=spec(key).label, layer="options", status="ok", weight=weight,
        signal=signal, raw_value=spread, display_value=f"{spread:+.1f}pts",
        explanation=f"{basis} {meaning}",
        detail={
            "implied_vol": round(implied_vol, 2),
            "realised_vol": round(realised_vol, 2),
            "spread": round(spread, 2),
            "normal_premium": normal,
            "posture": config.posture,
        },
    )

def compute_iv_rank(data: TickerData, config: ScreenerConfig) -> tuple[float | None, str, int]:
    """Return ``(iv_rank, source, observation_count)``.

    ``source`` is ``"iv"`` once enough real implied-volatility snapshots have
    accumulated, otherwise ``"hv_proxy"`` — a realised-volatility percentile,
    which is computable from price history on day one. The UI always displays
    which one was used.
    """
    min_obs = int(config.threshold("iv_rank", "min_observations"))
    history = data.iv_history.dropna() if data.iv_history is not None else pd.Series(dtype="float64")
    observations = len(history)

    current_iv = data.options.atm_iv if data.options else None
    if observations >= min_obs and current_iv is not None:
        window = history.iloc[-VOL_RANK_WINDOW:]
        rank = indicators.percentile_rank(window, current_iv)
        if rank is not None:
            return rank, "iv", observations

    hv_window = int(config.threshold("iv_rank", "hv_window"))
    if data.bar_count >= hv_window + 2:
        realised = indicators.realised_volatility(data.close, hv_window).dropna()
        if not realised.empty:
            rank = indicators.percentile_rank(realised.iloc[-VOL_RANK_WINDOW:])
            if rank is not None:
                return rank, "hv_proxy", observations
    return None, "hv_proxy", observations


def score_iv_rank(data: TickerData, config: ScreenerConfig) -> ParameterResult:
    key = "iv_rank"
    weight = config.parameters[key].weight
    value, source, observations = compute_iv_rank(data, config)
    min_obs = int(config.threshold(key, "min_observations"))

    if value is None:
        return _missing(key, weight,
                        "Neither implied volatility nor enough price history was available to "
                        "rank current volatility against its past range.")

    midpoint = config.threshold(key, "midpoint")
    width = config.threshold(key, "width")
    buying = config.posture == "buy"
    # Buying wants cheap volatility; selling wants expensive volatility.
    signal = ramp01(value, midpoint, width, rising=not buying)

    if source == "iv":
        basis = (f"Based on {observations} stored implied-volatility observations at the "
                 f"{'~30-day' if data.options else 'reference'} tenor.")
        label_suffix = "IV Rank"
    else:
        basis = (f"Using realised volatility as a stand-in — {observations} of the {min_obs} "
                 "implied-volatility snapshots needed for a true IV Rank have been collected "
                 "so far. This switches over automatically.")
        label_suffix = "HV Rank (proxy)"

    if buying:
        if value <= midpoint:
            meaning = (f"{label_suffix} {value:.0f} is in the cheaper part of its one-year range. "
                       "Since you are buying premium, that means you are paying less for the same "
                       "exposure, and any volatility expansion works in your favour.")
        else:
            meaning = (f"{label_suffix} {value:.0f} is in the richer part of its one-year range. "
                       "Since you are buying premium, you are paying up, and a volatility contraction "
                       "would erode the position even if you get the direction right.")
    else:
        if value >= midpoint:
            meaning = (f"{label_suffix} {value:.0f} is in the richer part of its one-year range. "
                       "Since you are selling premium, that is well-paid risk, and any volatility "
                       "contraction works in your favour.")
        else:
            meaning = (f"{label_suffix} {value:.0f} is in the cheaper part of its one-year range. "
                       "Since you are selling premium, you are being paid comparatively little for "
                       "the risk you take on.")

    return ParameterResult(
        key=key, label=spec(key).label, layer="options", status="ok", weight=weight,
        signal=signal, raw_value=value, display_value=f"{value:.0f}",
        explanation=f"{meaning} {basis}",
        detail={
            "source": source,
            "observations": observations,
            "required_observations": min_obs,
            "atm_iv": None if not data.options or data.options.atm_iv is None
                     else round(data.options.atm_iv * 100, 2),
        },
    )


def score_dte_fit(data: TickerData, config: ScreenerConfig) -> ParameterResult:
    key = "dte_fit"
    weight = config.parameters[key].weight
    assessment = data.options
    if assessment is None or not assessment.has_chain:
        return _missing(key, weight,
                        "No listed options chain was found, so expiry fit cannot be assessed.")
    dte = assessment.selected.dte if assessment.selected else assessment.best_dte
    if dte is None:
        return _missing(key, weight, "No usable expiry was found in the chain.")

    target = config.target()
    tolerance = config.threshold(key, "tolerance")
    signal = window_fit(dte, target.dte_low, target.dte_high, tolerance)

    window = f"{target.dte_low}-{target.dte_high} days"
    if target.dte_low <= dte <= target.dte_high:
        note = (f"The best available expiry is {dte} days out, inside your {window} target window, "
                "so it gets full credit.")
    elif dte < target.dte_low:
        note = (f"The best available expiry is {dte} days out — {target.dte_low - dte} days shorter "
                f"than your {window} window. Less time for the thesis to play out, and time decay "
                "bites faster.")
    else:
        note = (f"The best available expiry is {dte} days out — {dte - target.dte_high} days longer "
                f"than your {window} window. More time, but more premium paid for it.")

    return ParameterResult(
        key=key, label=spec(key).label, layer="options", status="ok", weight=weight,
        signal=signal, raw_value=float(dte), display_value=f"{dte}d",
        explanation=note,
        detail={"target_low": target.dte_low, "target_high": target.dte_high,
                "expiry": assessment.best_expiry.isoformat() if assessment.best_expiry else None},
    )


def score_delta_fit(data: TickerData, config: ScreenerConfig) -> ParameterResult:
    key = "delta_fit"
    weight = config.parameters[key].weight
    assessment = data.options
    if assessment is None or not assessment.has_chain:
        return _missing(key, weight,
                        "No listed options chain was found, so no contract delta is available.")
    if assessment.selected is None:
        reason = assessment.notes[-1] if assessment.notes else "No contract passed the liquidity filters."
        return _missing(key, weight, reason)

    contract = assessment.selected
    target = config.target()
    tolerance = config.threshold(key, "tolerance")
    magnitude = abs(contract.delta)
    signal = window_fit(magnitude, target.delta_low, target.delta_high, tolerance)

    band = f"{target.delta_low:.2f}-{target.delta_high:.2f}"
    if target.delta_low <= magnitude <= target.delta_high:
        note = (f"The {contract.strike:,.2f} {contract.kind} has a delta of {magnitude:.2f}, inside "
                f"your {band} target band, so it gets full credit.")
    elif magnitude < target.delta_low:
        note = (f"The closest contract has a delta of {magnitude:.2f}, below your {band} band — "
                "further out of the money, so cheaper but needing a larger move to pay off.")
    else:
        note = (f"The closest contract has a delta of {magnitude:.2f}, above your {band} band — "
                "deeper in the money, so it tracks the stock more closely but costs more.")

    note += (f" Delta is computed with Black-Scholes from the chain's {contract.implied_volatility * 100:.1f}% "
             "implied volatility, because yfinance does not publish greeks.")

    return ParameterResult(
        key=key, label=spec(key).label, layer="options", status="ok", weight=weight,
        signal=signal, raw_value=magnitude, display_value=f"{contract.delta:+.2f}",
        explanation=note,
        detail={"target_low": target.delta_low, "target_high": target.delta_high,
                "contract": contract.contract_symbol},
    )


def score_earnings(data: TickerData, config: ScreenerConfig) -> ParameterResult:
    key = "earnings"
    weight = config.parameters[key].weight

    if data.earnings_date is None:
        if data.is_etf:
            return _not_applicable(
                key, weight,
                f"{data.symbol} is a fund, so it has no earnings report. This parameter's weight "
                "has been redistributed across the others.")
        return _missing(key, weight,
                        "No scheduled earnings date was available from the data source.")

    days = trading_days_between(data.today, data.earnings_date)
    if days < 0:
        return _missing(key, weight,
                        f"The stored earnings date ({data.earnings_date:%Y-%m-%d}) is in the past; "
                        "the next one has not been published yet.")

    buying = config.posture == "buy"
    when = f"{days} trading session{'s' if days != 1 else ''} away ({data.earnings_date:%d %b %Y})"

    if buying:
        sweet = config.threshold(key, "buy_sweet_spot")
        tolerance = config.threshold(key, "buy_tolerance")
        min_days = config.threshold(key, "buy_min_days")
        signal = window_fit(days, sweet, sweet, tolerance)
        if days <= min_days:
            # Inside a couple of sessions the report is a coin flip, not a vol play.
            signal *= 0.3
            note = (f"Earnings are {when} — close enough that the position becomes a bet on the "
                    "result itself rather than on volatility expansion, so the score is cut back.")
        elif abs(days - sweet) <= tolerance / 2:
            note = (f"Earnings are {when}, near your {sweet:.0f}-session sweet spot. Implied "
                    "volatility typically firms into a report, which helps a long option.")
        else:
            note = (f"Earnings are {when}, away from your {sweet:.0f}-session sweet spot, so the "
                    "volatility-expansion tailwind is weaker.")
    else:
        avoid = config.threshold(key, "sell_avoid_days")
        width = config.threshold(key, "sell_width")
        signal = ramp01(days, avoid, width, rising=True)
        if days <= avoid:
            note = (f"Earnings are {when}, inside the {avoid:.0f}-session window you asked to avoid "
                    "when selling premium. A report can gap the stock straight through a short "
                    "strike overnight.")
        else:
            note = (f"Earnings are {when}, clear of the {avoid:.0f}-session window you asked to "
                    "avoid, so the position should not have to carry a report.")

    return ParameterResult(
        key=key, label=spec(key).label, layer="options", status="ok", weight=weight,
        signal=signal, raw_value=float(days), display_value=f"{days}d",
        explanation=note,
        detail={"earnings_date": data.earnings_date.isoformat(), "posture": config.posture},
    )


#: Registry consumed by the engine. Order controls display order in the UI.
STOCK_SCORERS = {
    "rsi": score_rsi,
    "ma_cross": score_ma_cross,
    "macd": score_macd,
    "ma200": score_ma200,
    "volume": score_volume,
    "head_shoulders": score_head_shoulders,
}

OPTIONS_SCORERS = {
    "iv_rank": score_iv_rank,
    "vol_risk_premium": score_vol_risk_premium,
    "dte_fit": score_dte_fit,
    "delta_fit": score_delta_fit,
    "earnings": score_earnings,
}

ALL_SCORERS = {**STOCK_SCORERS, **OPTIONS_SCORERS}
