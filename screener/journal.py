"""Decision journal and weight calibration.

The screener can compute what every ticker did after a ranking. What it cannot
know is **which candidates you acted on** — and that is precisely the
information needed to tell whether its scores are any good *for you*.

Every weight in the default config is an opinion: mine, a preset's, or a
literature average. This module is how they stop being opinions.

Two questions it answers:

1. **Selection edge** — did the candidates you took outperform the ones you
   passed? If not, the screener is not helping you choose, however good its
   scores look.
2. **Per-parameter separation** — among the trades you took, which parameters
   were actually larger in the winners than in the losers? Those are the ones
   that deserve weight.

**On statistical honesty:** trading samples are small and noisy, and it is very
easy to read a pattern into thirty trades that is not there. This module
therefore reports sample sizes with every number, declines to suggest weight
changes below a minimum sample, and caps suggested changes to small steps.
The t-statistics are indicative only — returns are not normally distributed and
the samples are not independent across overlapping holds.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

from screener.db import Database
from screener.param_specs import SPECS_BY_KEY

log = logging.getLogger(__name__)

__all__ = ["CalibrationReport", "calibrate", "DEFAULT_HORIZON", "MIN_SAMPLE"]

#: Horizon used for calibration. 20 trading days ≈ one month, long enough for a
#: swing signal to resolve and short enough to accumulate samples.
DEFAULT_HORIZON = 20

#: Below this many decided-and-resolved trades, numbers are reported but no
#: weight change is suggested. Thirty is already thin; fifteen is the floor at
#: which anything is worth looking at at all.
MIN_SAMPLE = 15

#: Largest weight change suggested in one pass. Small steps, because the
#: estimate underneath is noisy.
MAX_WEIGHT_STEP = 1.0


@dataclass
class ParameterCalibration:
    """How well one parameter separated winners from losers."""

    key: str
    label: str
    winners_mean: float | None
    losers_mean: float | None
    n_winners: int
    n_losers: int
    separation: float | None       #: winners_mean − losers_mean
    t_stat: float | None
    current_weight: float
    suggested_weight: float | None
    rationale: str

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "label": self.label,
            "winners_mean": _round(self.winners_mean),
            "losers_mean": _round(self.losers_mean),
            "n_winners": self.n_winners,
            "n_losers": self.n_losers,
            "separation": _round(self.separation),
            "t_stat": _round(self.t_stat),
            "current_weight": self.current_weight,
            "suggested_weight": self.suggested_weight,
            "rationale": self.rationale,
        }


@dataclass
class CalibrationReport:
    """What the journal says about the screener's usefulness."""

    horizon_days: int
    n_taken: int
    n_passed: int
    n_watching: int
    n_resolved_taken: int
    n_resolved_passed: int
    taken_mean: float | None = None
    passed_mean: float | None = None
    selection_edge: float | None = None
    hit_rate: float | None = None
    parameters: list[ParameterCalibration] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    verdict: str = ""

    @property
    def actionable(self) -> bool:
        return self.n_resolved_taken >= MIN_SAMPLE

    def to_dict(self) -> dict:
        return {
            "horizon_days": self.horizon_days,
            "sample": {
                "taken": self.n_taken,
                "passed": self.n_passed,
                "watching": self.n_watching,
                "resolved_taken": self.n_resolved_taken,
                "resolved_passed": self.n_resolved_passed,
                "minimum_for_suggestions": MIN_SAMPLE,
            },
            "actionable": self.actionable,
            "taken_mean": _round(self.taken_mean),
            "passed_mean": _round(self.passed_mean),
            "selection_edge": _round(self.selection_edge),
            "hit_rate": _round(self.hit_rate),
            "verdict": self.verdict,
            "parameters": [p.to_dict() for p in self.parameters],
            "warnings": self.warnings,
        }


def calibrate(db: Database, horizon_days: int = DEFAULT_HORIZON,
              min_sample: int = MIN_SAMPLE) -> CalibrationReport:
    """Measure the screener against the decisions actually recorded against it."""
    all_decisions = db.get_decisions(limit=5000)
    counts = {"taken": 0, "passed": 0, "watching": 0}
    for item in all_decisions:
        counts[item["decision"]] = counts.get(item["decision"], 0) + 1

    resolved = db.decisions_with_outcomes(horizon_days)
    taken = [r for r in resolved if r["decision"] == "taken"]
    passed = [r for r in resolved if r["decision"] == "passed"]

    report = CalibrationReport(
        horizon_days=horizon_days,
        n_taken=counts.get("taken", 0),
        n_passed=counts.get("passed", 0),
        n_watching=counts.get("watching", 0),
        n_resolved_taken=len(taken),
        n_resolved_passed=len(passed),
    )

    if not resolved:
        report.verdict = (
            "No decisions have resolved yet. Record Taken/Passed on candidates and "
            f"this fills in once {horizon_days} trading days have elapsed."
        )
        return report

    # -- 1. Selection edge -------------------------------------------------
    taken_returns = [r["directional_pct"] for r in taken]
    passed_returns = [r["directional_pct"] for r in passed]

    report.taken_mean = _mean(taken_returns)
    report.passed_mean = _mean(passed_returns)
    if report.taken_mean is not None and report.passed_mean is not None:
        report.selection_edge = report.taken_mean - report.passed_mean

    if taken_returns:
        report.hit_rate = 100.0 * sum(1 for r in taken_returns if r > 0) / len(taken_returns)

    # -- 2. Per-parameter separation among taken trades --------------------
    config = db.load_active_config()
    winners = [r for r in taken if r["directional_pct"] > 0]
    losers = [r for r in taken if r["directional_pct"] <= 0]

    for key, spec_item in SPECS_BY_KEY.items():
        current_weight = config.parameters[key].weight if key in config.parameters else 0.0
        winner_values = _contributions(winners, key)
        loser_values = _contributions(losers, key)

        winners_mean = _mean(winner_values)
        losers_mean = _mean(loser_values)
        separation = (None if winners_mean is None or losers_mean is None
                      else winners_mean - losers_mean)
        t_stat = _welch_t(winner_values, loser_values)

        suggested, rationale = _suggest(
            separation, t_stat, current_weight, len(taken), min_sample,
            len(winner_values), len(loser_values),
        )
        report.parameters.append(ParameterCalibration(
            key=key, label=spec_item.label,
            winners_mean=winners_mean, losers_mean=losers_mean,
            n_winners=len(winner_values), n_losers=len(loser_values),
            separation=separation, t_stat=t_stat,
            current_weight=current_weight,
            suggested_weight=suggested, rationale=rationale,
        ))

    # Strongest separation first — that is the ordering worth reading.
    report.parameters.sort(key=lambda p: abs(p.separation or 0.0), reverse=True)

    report.verdict = _verdict(report, min_sample)
    report.warnings = _warnings(report, min_sample)
    return report


# --------------------------------------------------------------------------


def _contributions(rows: list[dict], key: str) -> list[float]:
    """Contributions of one parameter across a set of resolved decisions.

    Rows where the parameter could not be computed are skipped rather than
    counted as zero — "no data" is not the same measurement as "no effect".
    """
    values: list[float] = []
    for row in rows:
        for param in row.get("parameters", []):
            if param["param_key"] == key and param["status"] == "ok":
                values.append(float(param["contribution"]))
    return values


def _suggest(separation: float | None, t_stat: float | None, current: float,
             sample: int, min_sample: int,
             n_winners: int = 0, n_losers: int = 0) -> tuple[float | None, str]:
    """Propose a small weight change, or decline and say why.

    The "why" matters as much as the number: declining because the sample is
    small is a different situation from declining because the parameter simply
    did not separate winners from losers, and conflating them would hide which
    one you are looking at.
    """
    if sample < min_sample:
        return None, (
            f"Only {sample} resolved trade(s); {min_sample} needed before a weight "
            "change is worth suggesting."
        )
    if separation is None:
        return None, "This parameter did not compute on enough of these trades to compare."
    if t_stat is None:
        if min(n_winners, n_losers) < 2:
            return None, (
                f"Needs at least two winners and two losers; have {n_winners} and {n_losers}."
            )
        return None, (
            f"Separation is {separation:+.2f}, but every observation within each group is "
            "identical, so there is no variation to test it against."
        )
    if abs(t_stat) < 1.5:
        return None, (
            "Winners and losers are not meaningfully separated on this parameter "
            f"(separation {separation:+.2f}, t≈{t_stat:.2f} — noise)."
        )

    step = min(MAX_WEIGHT_STEP, max(0.5, abs(separation) / 4.0))
    direction = 1.0 if separation > 0 else -1.0
    suggested = float(max(0.0, min(10.0, current + direction * step)))
    if suggested == current:
        return None, "Already at the end of the 0–10 range."

    movement = "higher" if separation > 0 else "lower"
    return suggested, (
        f"This parameter contributed {abs(separation):.2f} points {movement} in your "
        f"winners than your losers (t≈{t_stat:.2f} over {sample} trades). "
        f"Suggest {current:.1f} → {suggested:.1f}."
    )


def _verdict(report: CalibrationReport, min_sample: int) -> str:
    if report.n_resolved_taken < min_sample:
        return (
            f"{report.n_resolved_taken} of {min_sample} resolved trades needed before "
            "these numbers mean much. Keep recording decisions."
        )
    if report.selection_edge is None:
        return (
            f"{report.n_resolved_taken} trades recorded, but nothing was marked Passed, "
            "so there is no comparison group. Recording what you skip is what makes "
            "the selection edge measurable."
        )
    if report.selection_edge > 0.5:
        return (
            f"Over {report.n_resolved_taken} trades, the candidates you took beat the ones "
            f"you passed by {report.selection_edge:+.2f}% at {report.horizon_days} days. "
            "The screener plus your judgement is adding something."
        )
    if report.selection_edge < -0.5:
        return (
            f"Over {report.n_resolved_taken} trades, the candidates you *passed* outperformed "
            f"the ones you took by {abs(report.selection_edge):.2f}%. Worth examining what "
            "made you skip them — the screener may be scoring something your judgement "
            "is correctly overriding, or the reverse."
        )
    return (
        f"Over {report.n_resolved_taken} trades, taken and passed candidates performed about "
        f"the same ({report.selection_edge:+.2f}%). No measurable selection edge yet."
    )


def _warnings(report: CalibrationReport, min_sample: int) -> list[str]:
    messages: list[str] = []
    if report.n_resolved_passed == 0 and report.n_resolved_taken > 0:
        messages.append(
            "Nothing is marked Passed. Without a comparison group the selection edge "
            "cannot be measured — recording the candidates you skip matters as much as "
            "the ones you take."
        )
    if 0 < report.n_resolved_taken < min_sample:
        messages.append(
            f"Sample is {report.n_resolved_taken}. Treat every number here as indicative; "
            "patterns in samples this small are usually noise."
        )
    if report.n_resolved_taken >= min_sample:
        messages.append(
            "The t-statistics are indicative only. Returns are not normally distributed "
            "and overlapping holding periods make these samples non-independent, so they "
            "overstate significance."
        )
    return messages


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _welch_t(a: list[float], b: list[float]) -> float | None:
    """Welch's t-statistic. Indicative only — see the module docstring."""
    if len(a) < 2 or len(b) < 2:
        return None
    mean_a, mean_b = _mean(a), _mean(b)
    var_a = sum((x - mean_a) ** 2 for x in a) / (len(a) - 1)
    var_b = sum((x - mean_b) ** 2 for x in b) / (len(b) - 1)
    denominator = math.sqrt(var_a / len(a) + var_b / len(b))
    if denominator == 0:
        return None
    return (mean_a - mean_b) / denominator


def _round(value: float | None, places: int = 3) -> float | None:
    return None if value is None else round(value, places)
