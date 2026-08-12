"""Composite scoring and ranking.

The model, stated once, plainly — this is the logic the UI promises never to hide.

**Step 1 — direction, from the stock layer.**
Each stock parameter yields a signed signal in [-1, +1]. Their weighted mean is
the *net directional signal*::

    net = Σ(wᵢ · sᵢ) / Σ wᵢ

Its sign chooses the call/put lean; its magnitude is *conviction*, in [0, 1].
A ticker whose indicators disagree nets toward zero and is correctly reported as
having no edge, rather than being scored on whichever indicator happens to be
loudest.

**Step 2 — contributions.**
Every parameter's contribution is expressed in the same units as the final
score, so the breakdown literally adds up::

    scale                  = 100 / Σ(all counted weights)
    stock contributionᵢ    = scale · wᵢ · sᵢ · dir
    options contributionⱼ  = scale · wⱼ · qⱼ · gate

``dir`` is +1 for a call lean and -1 for a put lean, so a stock parameter that
*disagrees* with the chosen direction contributes negatively and says so.

**Step 3 — the conviction gate.**
``gate`` is the conviction from step 1 (or 1.0 if you switch the gate off in
Settings). The reasoning: options-layer parameters measure how well you can
*express* a trade, not whether a trade exists. Without the gate, a ticker with
no directional signal but a cheap, perfectly-sized contract would score
respectably — surfacing a well-priced way to bet on nothing. The cost of the
gate is that it couples the two layers, so a strong options setup cannot rescue
a flat chart. That is the intended behaviour for a directional screener; turn it
off if you want the layers scored independently.

**Missing data.** Parameters that cannot be computed are dropped from both the
numerator and the denominator, so scores stay comparable across tickers with
different data coverage. The UI shows a completeness badge and the reason for
every omission.

Composite scores are bounded to [0, 100] by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Iterable

import pandas as pd

from screener.config import ScreenerConfig
from screener.options_analysis import OptionsAssessment
from screener.scoring.parameters import (
    OPTIONS_SCORERS,
    STOCK_SCORERS,
    ParameterResult,
    TickerData,
)

__all__ = [
    "DirectionRead", "TickerScore", "derive_direction",
    "score_stock_layer", "score_options_layer", "compose", "rank",
]

#: Below this conviction the ticker is reported as having no directional edge.
NEUTRAL_CONVICTION = 0.05


@dataclass
class DirectionRead:
    """The stock layer's verdict on direction and conviction."""

    direction: str          # "call" | "put" | "neutral"
    conviction: float       # 0..1
    net_signal: float       # -1..+1

    @property
    def multiplier(self) -> int:
        """+1 when scoring a call lean, -1 for a put lean."""
        return -1 if self.direction == "put" else 1

    @property
    def label(self) -> str:
        return {"call": "Call lean", "put": "Put lean"}.get(self.direction, "No clear edge")


@dataclass
class TickerScore:
    """A fully-explained score for one ticker."""

    symbol: str
    composite: float
    direction: str
    direction_label: str
    conviction: float
    net_signal: float
    parameters: list[ParameterResult]
    spot: float | None = None
    dollar_volume: float = 0.0
    contract: dict | None = None
    options_notes: list[str] = field(default_factory=list)
    #: Populated by :func:`rank` when this ticker's score tied with another.
    tie_break_note: str | None = None
    rank: int | None = None

    @property
    def counted(self) -> list[ParameterResult]:
        return [p for p in self.parameters if p.counted]

    @property
    def considered(self) -> list[ParameterResult]:
        """Parameters the user has switched on, whether or not they computed."""
        return [p for p in self.parameters if p.status != "disabled"]

    @property
    def completeness(self) -> str:
        return f"{len(self.counted)}/{len(self.considered)}"

    @property
    def has_edge(self) -> bool:
        return self.conviction >= NEUTRAL_CONVICTION

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "composite": round(self.composite, 2),
            "direction": self.direction,
            "direction_label": self.direction_label,
            "conviction": round(self.conviction, 4),
            "net_signal": round(self.net_signal, 4),
            "spot": None if self.spot is None else round(self.spot, 2),
            "dollar_volume": round(self.dollar_volume, 2),
            "completeness": self.completeness,
            "counted_parameters": len(self.counted),
            "considered_parameters": len(self.considered),
            "contract": self.contract,
            "options_notes": self.options_notes,
            "tie_break_note": self.tie_break_note,
            "rank": self.rank,
            "parameters": [p.to_dict() for p in self.parameters],
        }


# --------------------------------------------------------------------------
# Layer scoring
# --------------------------------------------------------------------------

def score_stock_layer(data: TickerData, config: ScreenerConfig) -> list[ParameterResult]:
    """Run every enabled stock-layer scorer."""
    return _run(STOCK_SCORERS, data, config)


def score_options_layer(data: TickerData, config: ScreenerConfig) -> list[ParameterResult]:
    """Run every enabled options-layer scorer.

    Call this only after ``data.options`` has been populated, which in turn
    requires a direction from :func:`derive_direction`.
    """
    return _run(OPTIONS_SCORERS, data, config)


def _run(scorers: dict, data: TickerData, config: ScreenerConfig) -> list[ParameterResult]:
    results: list[ParameterResult] = []
    for key, scorer in scorers.items():
        if not config.active(key):
            continue  # disabled or zero-weighted parameters are simply absent
        try:
            results.append(scorer(data, config))
        except Exception as exc:  # a bug in one scorer must not lose the whole run
            from screener.param_specs import spec
            results.append(ParameterResult(
                key=key, label=spec(key).label, layer=spec(key).layer,
                status="insufficient_data",
                weight=config.parameters[key].weight,
                explanation=f"This parameter failed to compute ({type(exc).__name__}: {exc}).",
            ))
    return results


def derive_direction(stock_results: Iterable[ParameterResult]) -> DirectionRead:
    """Combine signed stock signals into a direction and a conviction."""
    counted = [r for r in stock_results if r.counted]
    total_weight = sum(r.weight for r in counted)
    if not counted or total_weight <= 0:
        return DirectionRead(direction="neutral", conviction=0.0, net_signal=0.0)

    net = sum(r.weight * float(r.signal) for r in counted) / total_weight
    if abs(net) < NEUTRAL_CONVICTION:
        # Still record the sign in net_signal; the label reports no edge.
        return DirectionRead(direction="neutral", conviction=abs(net), net_signal=net)
    return DirectionRead(
        direction="call" if net > 0 else "put",
        conviction=min(abs(net), 1.0),
        net_signal=net,
    )


# --------------------------------------------------------------------------
# Composition
# --------------------------------------------------------------------------

def compose(
    symbol: str,
    stock_results: list[ParameterResult],
    options_results: list[ParameterResult],
    read: DirectionRead,
    config: ScreenerConfig,
    *,
    spot: float | None = None,
    dollar_volume: float = 0.0,
    options: OptionsAssessment | None = None,
) -> TickerScore:
    """Turn scored parameters into a composite score with per-parameter attribution."""
    all_results = list(stock_results) + list(options_results)
    counted = [r for r in all_results if r.counted]
    total_weight = sum(r.weight for r in counted)

    if total_weight <= 0:
        for result in all_results:
            result.effective_weight = 0.0
            result.contribution = 0.0
        return TickerScore(
            symbol=symbol, composite=0.0, direction="neutral",
            direction_label="No data", conviction=0.0, net_signal=0.0,
            parameters=all_results, spot=spot, dollar_volume=dollar_volume,
            options_notes=list(options.notes) if options else [],
        )

    scale = 100.0 / total_weight
    gate = read.conviction if config.conviction_gate else 1.0
    multiplier = read.multiplier

    for result in all_results:
        if not result.counted:
            result.effective_weight = 0.0
            result.contribution = 0.0
            continue
        # Share of the score this parameter can move, after redistribution.
        result.effective_weight = result.weight / total_weight * 100.0
        if result.layer == "stock":
            result.contribution = scale * result.weight * float(result.signal) * multiplier
        else:
            result.contribution = scale * result.weight * float(result.signal) * gate

    composite = sum(r.contribution for r in all_results)
    composite = max(0.0, min(100.0, composite))

    contract = None
    if options and options.selected:
        contract = options.selected.to_dict()

    return TickerScore(
        symbol=symbol,
        composite=composite,
        direction=read.direction,
        direction_label=read.label,
        conviction=read.conviction,
        net_signal=read.net_signal,
        parameters=all_results,
        spot=spot,
        dollar_volume=dollar_volume,
        contract=contract,
        options_notes=list(options.notes) if options else [],
    )


# --------------------------------------------------------------------------
# Ranking and tie-breaking
# --------------------------------------------------------------------------

def _heaviest_key(config: ScreenerConfig) -> str | None:
    """The active parameter carrying the most weight.

    Ties on weight resolve alphabetically so the tie-break rule is itself
    deterministic — otherwise the ordering could depend on dict iteration.
    """
    active = [(k, pc.weight) for k, pc in config.parameters.items() if config.active(k)]
    if not active:
        return None
    return sorted(active, key=lambda item: (-item[1], item[0]))[0][0]


def _contribution_of(score: TickerScore, key: str | None) -> float:
    if key is None:
        return 0.0
    for result in score.parameters:
        if result.key == key:
            return result.contribution
    return 0.0


def rank(scores: list[TickerScore], config: ScreenerConfig) -> list[TickerScore]:
    """Sort best-to-worst, breaking ties deterministically.

    Cascade, as configured: composite score, then the contribution of the
    highest-weighted parameter, then 30-day average dollar volume (prefer the
    more tradeable name), then ticker alphabetically. The final key guarantees a
    total order, so identical inputs always produce identical output.
    """
    heaviest = _heaviest_key(config)

    ordered = sorted(
        scores,
        key=lambda s: (
            -round(s.composite, 6),
            -round(_contribution_of(s, heaviest), 6),
            -round(s.dollar_volume, 2),
            s.symbol,
        ),
    )

    label = None
    if heaviest is not None:
        from screener.param_specs import spec
        label = spec(heaviest).label

    for index, score in enumerate(ordered):
        score.rank = index + 1
        score.tie_break_note = None

    # Annotate only genuine ties, so the UI can explain a surprising ordering.
    for index, score in enumerate(ordered):
        neighbours = []
        if index > 0:
            neighbours.append(ordered[index - 1])
        if index + 1 < len(ordered):
            neighbours.append(ordered[index + 1])
        tied = [n for n in neighbours if round(n.composite, 2) == round(score.composite, 2)]
        if not tied:
            continue
        other = tied[0]
        mine = _contribution_of(score, heaviest)
        theirs = _contribution_of(other, heaviest)
        if round(mine, 2) != round(theirs, 2):
            score.tie_break_note = (
                f"Tied with {other.symbol} on composite score; separated by {label} "
                f"({mine:+.2f} vs {theirs:+.2f})."
            )
        elif round(score.dollar_volume, 2) != round(other.dollar_volume, 2):
            score.tie_break_note = (
                f"Tied with {other.symbol} on score and {label}; separated by average "
                f"dollar volume (${score.dollar_volume:,.0f} vs ${other.dollar_volume:,.0f})."
            )
        else:
            score.tie_break_note = (
                f"Tied with {other.symbol} on every tie-break rule; ordered alphabetically."
            )
    return ordered


def average_dollar_volume(prices: pd.DataFrame, window: int = 30) -> float:
    """Mean daily dollar volume over ``window`` sessions — the liquidity tie-break."""
    if prices.empty or "close" not in prices or "volume" not in prices:
        return 0.0
    tail = prices.tail(window)
    product = (tail["close"] * tail["volume"]).dropna()
    return float(product.mean()) if len(product) else 0.0
