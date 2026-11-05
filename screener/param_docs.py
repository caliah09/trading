"""The explanatory layer: what each parameter measures, and what would change it.

WHY THIS IS SEPARATE FROM param_specs
-------------------------------------
``param_specs`` describes parameters to the *machine*: keys, weights,
thresholds, the metadata the Settings controls are generated from. This module
describes them to a *person* who has just clicked a row and wants to know why a
number is what it is.

Keeping them apart matters because they change for different reasons. A
threshold's bounds move when the scorer changes; this prose moves when the
explanation turns out to be unclear. Mixing them would mean editing scoring
configuration to fix a wording problem.

WHAT EACH FIELD IS FOR
----------------------
``measures``  The mechanism. What is actually computed, in enough detail that
              the number stops being a black box.
``changes``   What would move it, and roughly how far. This is the field that
              turns a dashboard into something you can learn from — a reading
              you cannot imagine changing is a reading you cannot act on.
``evidence``  What the research supports, and where it does not. Stated plainly
              enough to argue with. Where a default weight is an opinion rather
              than a finding, it says so.

The honesty rule: no field here claims more than the implementation does. If a
parameter uses a proxy, the text says it is a proxy.
"""

from __future__ import annotations

from dataclasses import dataclass

from screener.param_specs import ALL_SPECS

__all__ = ["DeepDive", "DEEP_DIVE", "deep_dive"]


@dataclass(frozen=True)
class DeepDive:
    """Human-facing explanation of one parameter."""

    measures: str
    changes: str
    evidence: str

    def to_dict(self) -> dict[str, str]:
        return {
            "measures": self.measures,
            "changes": self.changes,
            "evidence": self.evidence,
        }


DEEP_DIVE: dict[str, DeepDive] = {
    "rsi": DeepDive(
        measures=(
            "Wilder's 14-period Relative Strength Index: the ratio of average gains to "
            "average losses, rescaled to 0-100. The smoothing is seeded with a simple "
            "average of the first 14 bars rather than running an EMA from bar one. That "
            "distinction is not pedantry — an unseeded EMA returns readings several "
            "points adrift of the canonical value on the same data."
        ),
        changes=(
            "RSI moves fastest when recent bars are one-sided. A few strong sessions "
            "lift it several points; a drift sideways decays it toward 50. Note the "
            "score does not keep rising as RSI falls — past your oversold threshold the "
            "contribution shrinks again, because deeply oversold is bounce risk, not a "
            "bigger edge."
        ),
        evidence=(
            "Short-horizon mean reversion in equities has reasonable empirical support, "
            "but the effect is small and concentrated in less liquid names — which is "
            "precisely where you cannot trade options. Treat RSI as a tiebreaker, not a "
            "thesis."
        ),
    ),
    "ma_cross": DeepDive(
        measures=(
            "Percentage distance between the 20- and 50-session simple moving averages, "
            "expressed relative to the 50-day. The sign gives direction; the magnitude "
            "gives how committed the move is. A cross that has just happened scores "
            "lower than one that has opened a gap, which is the point — the signal is "
            "the separation, not the crossing moment."
        ),
        changes=(
            "Moving averages are slow by construction, so this is one of the stickier "
            "parameters. Reversing it takes sustained movement, not a single session. "
            "The reasoning text names the price level where the two averages would meet."
        ),
        evidence=(
            "Moving-average rules have been studied since the 1990s with mixed results. "
            "They survive in some samples and vanish in others once transaction costs "
            "and data-snooping corrections are applied. The honest summary: weak "
            "evidence of a real effect, strong evidence that it is widely watched, and "
            "those are not the same claim."
        ),
    ),
    "macd": DeepDive(
        measures=(
            "The gap between the MACD line (12-period EMA minus 26-period EMA) and its "
            "own 9-period signal EMA, divided by price. Dividing by price is what makes "
            "the figure comparable between a $200 stock and a $40 one — a raw MACD gap "
            "is not. Comparing against the signal line rather than zero means the "
            "question is whether momentum is changing, not merely whether it is negative."
        ),
        changes=(
            "This is the most responsive of the trend parameters. A handful of sessions "
            "moving the other way will narrow the gap and can flip the sign. Because the "
            "scoring curve flattens, a reading already far past the threshold gains "
            "little from more of the same — the sensitivity is all behind it."
        ),
        evidence=(
            "Time-series momentum is one of the better-documented anomalies and survives "
            "out of sample across decades and asset classes. The caveat that matters "
            "here: most of that work uses horizons of months, not the days this "
            "crossover reacts to, and costs eat a large share of the measured edge at "
            "shorter horizons."
        ),
    ),
    "ma200": DeepDive(
        measures=(
            "Distance from the 200-session simple moving average as a percentage of "
            "price. Above it the long-term trend line acts as support; below it, as "
            "resistance."
        ),
        changes=(
            "The 200-day moves slowly, so what changes is price. The reasoning names the "
            "level — when spot is within a percent or two of it, this parameter is close "
            "to flipping and should be weighted accordingly in your own head."
        ),
        evidence=(
            "Much of why this works is that many participants watch it. That is a real "
            "mechanism, not a dismissal: a level a large number of people act on becomes "
            "a level worth knowing. It also means the effect can crowd out, and there is "
            "no guarantee it persists."
        ),
    ),
    "volume": DeepDive(
        measures=(
            "Today's volume against the trailing 30-session average, as a multiple. "
            "Volume is treated as confirmation rather than direction: it amplifies "
            "whatever the price action is saying rather than arguing for a side of its own."
        ),
        changes=(
            "Reverts quickly. A single heavy session spikes the multiple and it decays "
            "within days as the average catches up. Expect this contribution to be "
            "unstable between runs — that is the parameter behaving correctly, not noise "
            "in the pipeline."
        ),
        evidence=(
            "Volume confirmation is near-universal in practitioner literature and thinly "
            "supported in academic work. It is in the model at a deliberately modest "
            "weight for that reason."
        ),
    ),
    "head_shoulders": DeepDive(
        measures=(
            "ATR-scaled zigzag pivots identify the shoulders and head. The hard gate is "
            "structural — a lower high followed by a lower low for the bearish form. "
            "Geometry such as shoulder symmetry and neckline slope only adjusts "
            "confidence; it can never manufacture a pattern the structure does not "
            "support. The final unconfirmed leg is excluded, so a pattern never appears "
            "and then disappears as new bars arrive."
        ),
        changes=(
            "Price back through the neckline invalidates the pattern outright. That is "
            "the same number the order ticket uses as its stop — not a coincidence. The "
            "level where the evidence dies is the level where the position should."
        ),
        evidence=(
            "The best-known study of this pattern found returns that largely disappeared "
            "once risk and costs were accounted for. It is in the model because the "
            "invalidation level it produces is genuinely useful for structuring a trade, "
            "not because the pattern itself is a reliable money-maker. Weight it with "
            "that in mind."
        ),
    ),
    "iv_rank": DeepDive(
        measures=(
            "Where today's implied volatility sits within its own trailing range, as a "
            "percentile. Sampled at a fixed reference tenor near 30 days to expiry so "
            "the series stays comparable as contracts roll — comparing a 7-day IV against "
            "a 90-day one would measure the term structure, not the level."
        ),
        changes=(
            "Needs roughly 60 stored snapshots before a percentile means anything. Until "
            "then it is excluded and its weight is redistributed across the parameters "
            "that could be computed. Nothing to do but let the series accumulate — this "
            "is the one parameter that genuinely improves by waiting."
        ),
        evidence=(
            "That implied volatility mean-reverts is well established. That its "
            "percentile rank is the best way to exploit it is a practitioner convention "
            "rather than a research finding."
        ),
    ),
    "vol_risk_premium": DeepDive(
        measures=(
            "Implied volatility minus realised volatility. The long-run average gap in "
            "US equity index options is roughly four points, so the parameter measures "
            "today's gap against that norm rather than against zero."
        ),
        changes=(
            "Widens into uncertainty and collapses after it resolves — an earnings date "
            "or a macro print will move this more than anything in the price chart. "
            "Worth reading alongside earnings proximity rather than on its own."
        ),
        evidence=(
            "The volatility risk premium is one of the most robust findings in options "
            "research. Note which way it points: the evidence favours *selling* premium "
            "when the gap is wide. If your posture is set to buying, this parameter is "
            "working against the weight of the research, and you should know that you "
            "are taking the other side of it deliberately."
        ),
    ),
    "dte_fit": DeepDive(
        measures=(
            "How close the available expiries sit to your target days-to-expiry window. "
            "Scored on the best-fitting expiry actually listed, not on a hypothetical "
            "one — a target no contract can satisfy scores poorly rather than silently "
            "rounding to the nearest available date."
        ),
        changes=(
            "Moves as expiries roll. A target in the middle of a monthly gap will score "
            "well one week and badly the next, which is a property of the listing "
            "calendar rather than of the underlying."
        ),
        evidence=(
            "No research claim. This is a fit measure against your stated preference, "
            "and its weight reflects how much you care about hitting that window."
        ),
    ),
    "delta_fit": DeepDive(
        measures=(
            "How close the best available contract's delta sits to your target band. "
            "Delta is computed locally with Black-Scholes, because the data source "
            "publishes no greeks. That means it inherits the assumptions on the Settings "
            "page — risk-free rate and dividend yield — and will differ slightly from "
            "your broker's figure."
        ),
        changes=(
            "Delta moves with price, time and implied volatility, so this shifts "
            "continuously. A contract that fits your band today may not next week "
            "without the underlying moving at all."
        ),
        evidence=(
            "No research claim. A fit measure against your stated preference. The "
            "Black-Scholes assumption is a known simplification: it misprices deep "
            "out-of-the-money contracts, which is where the volatility smile lives."
        ),
    ),
    "earnings": DeepDive(
        measures=(
            "Calendar days until the next scheduled earnings date, scored against your "
            "avoid window. Not applicable to funds, which have no earnings — for an ETF "
            "this parameter is excluded and its weight redistributed rather than scored "
            "as a zero, which would quietly penalise every fund in the list."
        ),
        changes=(
            "Ticks down a day at a time and resets after the event. A date moving — "
            "companies do reschedule — will shift this without any price action."
        ),
        evidence=(
            "The earnings announcement premium is well documented: implied volatility "
            "rises into the event and collapses after it. Which direction that argues "
            "for depends entirely on whether you are buying or selling premium, which is "
            "why this parameter is posture-aware."
        ),
    ),
}


def deep_dive(key: str) -> DeepDive | None:
    """Explanation for one parameter, or None when it has none yet."""
    return DEEP_DIVE.get(key)


def missing_keys() -> tuple[str, ...]:
    """Parameters with no explanation. Kept honest by a test."""
    return tuple(s.key for s in ALL_SPECS if s.key not in DEEP_DIVE)
