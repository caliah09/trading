"""Portfolio-level risk across ranked candidates.

**Why this exists.** The screener ranks tickers independently, which is how
every retail screener works and is exactly the gap between retail and
institutional practice: professionals manage greeks and exposure at the
*portfolio* level, retail manages them per position.

A ranked list is not a portfolio. If the top five candidates are AAPL, MSFT,
NVDA, GOOGL and QQQ, that is not five positions — it is one bet on large-cap
US tech, sized five times. The ranking has no way to know that, because each
score was computed in isolation.

This module answers the question the ranking cannot: *given what is at the top
of the list, how many independent bets am I actually looking at?*

It deliberately does **not** size positions or tell you what to hold. It
reports concentration and leaves the decision where it belongs.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

__all__ = ["ConcentrationReport", "correlation_matrix", "analyse_concentration"]

#: Sessions of daily returns used for the correlation estimate. ~3 months is
#: long enough to be stable and short enough to reflect the current regime.
DEFAULT_WINDOW = 60

#: Pairwise correlation at or above which two names are treated as one bet.
DEFAULT_CLUSTER_THRESHOLD = 0.75


@dataclass
class ConcentrationReport:
    """How concentrated a set of candidates really is."""

    symbols: list[str]
    window: int
    threshold: float
    clusters: list[list[str]] = field(default_factory=list)
    average_correlation: float | None = None
    max_pair: tuple[str, str, float] | None = None
    directional_split: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    insufficient: list[str] = field(default_factory=list)

    @property
    def effective_positions(self) -> int:
        """Independent bets, counting each correlated cluster once."""
        return len(self.clusters)

    def to_dict(self) -> dict:
        return {
            "symbols": self.symbols,
            "window": self.window,
            "threshold": self.threshold,
            "clusters": self.clusters,
            "effective_positions": self.effective_positions,
            "nominal_positions": len(self.symbols),
            "average_correlation": (None if self.average_correlation is None
                                    else round(self.average_correlation, 3)),
            "max_pair": (None if self.max_pair is None
                         else {"a": self.max_pair[0], "b": self.max_pair[1],
                               "correlation": round(self.max_pair[2], 3)}),
            "directional_split": self.directional_split,
            "warnings": self.warnings,
            "insufficient": self.insufficient,
        }


def correlation_matrix(prices_by_symbol: dict[str, pd.DataFrame],
                       window: int = DEFAULT_WINDOW) -> pd.DataFrame:
    """Pairwise correlation of daily returns over the trailing ``window``.

    Returns are aligned on date before correlating, so tickers with different
    history lengths — or a holiday one exchange observed and another did not —
    do not silently produce a correlation over mismatched days.
    """
    series: dict[str, pd.Series] = {}
    for symbol, frame in prices_by_symbol.items():
        if frame is None or frame.empty or "close" not in frame:
            continue
        closes = frame["close"].dropna()
        if len(closes) < window + 1:
            continue
        returns = closes.pct_change().dropna().iloc[-window:]
        if len(returns) >= max(10, window // 2):
            series[symbol] = returns

    if len(series) < 2:
        return pd.DataFrame()
    return pd.DataFrame(series).dropna(how="any").corr()


def analyse_concentration(
    prices_by_symbol: dict[str, pd.DataFrame],
    symbols: list[str],
    directions: dict[str, str] | None = None,
    window: int = DEFAULT_WINDOW,
    threshold: float = DEFAULT_CLUSTER_THRESHOLD,
) -> ConcentrationReport:
    """Report how many independent bets a candidate list represents.

    Args:
        symbols: the candidates to analyse — normally the top N of a ranking.
        directions: optional per-symbol "call"/"put"/"neutral" lean. Two highly
            correlated names with *opposite* leans are flagged separately,
            because that is a contradiction rather than a concentration.
    """
    report = ConcentrationReport(symbols=list(symbols), window=window, threshold=threshold)
    directions = directions or {}

    if len(symbols) < 2:
        report.clusters = [[s] for s in symbols]
        return report

    available = {s: prices_by_symbol.get(s) for s in symbols}
    report.insufficient = [
        s for s, frame in available.items()
        if frame is None or frame.empty or len(frame["close"].dropna()) < window + 1
    ]

    matrix = correlation_matrix(
        {s: f for s, f in available.items() if f is not None and not f.empty}, window
    )
    if matrix.empty:
        report.clusters = [[s] for s in symbols]
        report.warnings.append(
            f"Not enough overlapping history to measure correlation across these "
            f"{len(symbols)} candidates, so each is counted as an independent position."
        )
        return report

    present = [s for s in symbols if s in matrix.columns]
    report.clusters = _cluster(matrix, present, threshold)
    for symbol in symbols:
        if symbol not in present:
            report.clusters.append([symbol])

    # Summary statistics over the upper triangle only, so each pair counts once.
    pairs: list[tuple[str, str, float]] = []
    for i, a in enumerate(present):
        for b in present[i + 1:]:
            value = float(matrix.loc[a, b])
            if not np.isnan(value):
                pairs.append((a, b, value))

    if pairs:
        report.average_correlation = float(np.mean([p[2] for p in pairs]))
        report.max_pair = max(pairs, key=lambda p: p[2])

    for symbol in symbols:
        lean = directions.get(symbol, "neutral")
        report.directional_split[lean] = report.directional_split.get(lean, 0) + 1

    report.warnings.extend(_warnings(report, pairs, directions))
    return report


def _cluster(matrix: pd.DataFrame, symbols: list[str], threshold: float) -> list[list[str]]:
    """Group symbols into clusters by correlation, using union-find.

    Transitive by design: if A correlates with B and B with C, all three are one
    cluster even when A and C fall just under the threshold. For position
    concentration that is the behaviour you want — the risk travels through B.
    """
    parent = {s: s for s in symbols}

    def find(x: str) -> str:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    for i, a in enumerate(symbols):
        for b in symbols[i + 1:]:
            value = matrix.loc[a, b]
            if not np.isnan(value) and float(value) >= threshold:
                union(a, b)

    groups: dict[str, list[str]] = {}
    for symbol in symbols:
        groups.setdefault(find(symbol), []).append(symbol)
    # Largest clusters first — those are the ones worth reading.
    return sorted(groups.values(), key=lambda g: (-len(g), g[0]))


def _warnings(report: ConcentrationReport, pairs: list[tuple[str, str, float]],
              directions: dict[str, str]) -> list[str]:
    messages: list[str] = []

    nominal = len(report.symbols)
    effective = report.effective_positions
    if effective < nominal:
        biggest = max(report.clusters, key=len)
        if len(biggest) > 1:
            messages.append(
                f"These {nominal} candidates are really about {effective} independent "
                f"bet{'s' if effective != 1 else ''}. {', '.join(biggest)} move together "
                f"(correlation ≥ {report.threshold:.2f}), so taking all of them is one "
                "position sized several times over, not diversification."
            )

    if report.average_correlation is not None and report.average_correlation >= 0.6:
        messages.append(
            f"Average pairwise correlation across the list is "
            f"{report.average_correlation:.2f}. A single market-wide move will hit most of "
            "these at once, in the same direction."
        )

    # Highly correlated names with opposite leans is a contradiction, not concentration.
    for a, b, value in pairs:
        lean_a, lean_b = directions.get(a), directions.get(b)
        if (value >= report.threshold and lean_a and lean_b
                and lean_a != lean_b and "neutral" not in (lean_a, lean_b)):
            messages.append(
                f"{a} and {b} are {value:.2f} correlated but the screener leans {lean_a} on "
                f"{a} and {lean_b} on {b}. Those two readings disagree — at least one is "
                "probably reading noise."
            )

    if report.insufficient:
        messages.append(
            f"No correlation estimate for {', '.join(report.insufficient)} — not enough "
            f"overlapping history in the {report.window}-session window."
        )
    return messages
