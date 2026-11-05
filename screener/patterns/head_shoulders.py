"""Head & Shoulders detection.

Two sources shaped this, and they agree from opposite directions.

**From the method video:** the pattern's validity rests on price action, not
geometry. A textbook-symmetric H&S is rare; what matters is a **lower high
followed by a lower low** (bearish) or a **higher low followed by a higher
high** (bullish). Shoulders at unequal heights, a sloping neckline, or a
double-top head are all explicitly tolerated.

**From the research:** Savin, Weller & Zvingelis found the pattern predicts
excess returns in US equities over one to three months, but survives
transaction costs only around a **60-day hold**. Osler found it unprofitable in
US equities as a standalone rule, while reporting unusual volume at pattern
completion — which she reads as crowding, not confirmation.

Taken together, the implementation rule is:

    hard gate   = lower high AND lower low        (price action — binary)
    confidence  = symmetry, neckline slope, volume, context   (geometry — scaled)

A pattern with 40% shoulder asymmetry and a valid lower-high/lower-low sequence
is valid and merely lower-confidence. A perfectly symmetric pattern with no
lower low is **not complete**. Scoring symmetry as a requirement would invert
the method.

See `docs/methods/02-head-and-shoulders.md` and
`docs/research/evidence-review.md` §1.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from screener.pivots import Pivot

__all__ = ["HeadShoulders", "detect_head_shoulders", "EXPECTED_HOLD_SESSIONS"]

#: TRADING SESSIONS -- not calendar days -- that the research associates with
#: the pattern's cost-surviving edge. The unit is in the name because it was
#: not, and a comparison against an option's calendar-day DTE read 71 > 60 and
#: stayed silent while the contract covered only 48 sessions.
#: Savin et al.: net of transaction costs, excess returns remain positive only
#: at the 60-day holding period. Used to propose an option expiry.
EXPECTED_HOLD_SESSIONS = 60


@dataclass
class HeadShoulders:
    """A detected Head & Shoulders (or inverse) formation."""

    direction: str                 #: "bearish" (H&S) | "bullish" (inverse H&S)
    left_shoulder: Pivot
    trough_1: Pivot
    head: Pivot
    trough_2: Pivot
    right_shoulder: Pivot

    neckline_slope: float          #: price per bar
    neckline_now: float            #: neckline extended to the latest bar
    completion: float              #: 0.3 / 0.5 / 0.7 / 1.0 — see module docs
    quality: float                 #: 0..1, confidence only — never invalidates
    symmetry: float                #: 0..1
    broke_neckline: bool
    extreme_confirmed: bool        #: the lower low / higher high
    is_continuation: bool          #: pattern sits inside a trend of the same direction
    in_pullback: bool              #: broke, then retested the neckline
    target: float | None           #: measured move projection
    notes: list[str] = field(default_factory=list)

    @property
    def sign(self) -> int:
        return -1 if self.direction == "bearish" else 1

    @property
    def state(self) -> str:
        if self.completion >= 1.0:
            return "pullback" if self.in_pullback else "confirmed"
        if self.completion >= 0.7:
            return "neckline broken"
        if self.completion >= 0.5:
            return "right shoulder at level"
        return "forming"

    def to_dict(self) -> dict:
        return {
            "direction": self.direction,
            "state": self.state,
            "completion": round(self.completion, 3),
            "quality": round(self.quality, 3),
            "symmetry": round(self.symmetry, 3),
            "neckline_now": round(self.neckline_now, 4),
            "neckline_slope": round(self.neckline_slope, 6),
            "is_continuation": self.is_continuation,
            "in_pullback": self.in_pullback,
            "target": None if self.target is None else round(self.target, 4),
            "expected_hold_sessions": EXPECTED_HOLD_SESSIONS,
            "pivots": {
                "left_shoulder": self.left_shoulder.to_dict(),
                "trough_1": self.trough_1.to_dict(),
                "head": self.head.to_dict(),
                "trough_2": self.trough_2.to_dict(),
                "right_shoulder": self.right_shoulder.to_dict(),
            },
            "notes": self.notes,
        }


def detect_head_shoulders(
    prices: pd.DataFrame,
    pivots: list[Pivot],
    *,
    retest_tolerance_pct: float = 1.5,
    trend_lookback: int = 120,
) -> HeadShoulders | None:
    """Find the most recent valid H&S or inverse H&S, or None.

    Scans five-pivot windows newest-first and returns the first that passes the
    hard gate, preferring more-complete patterns over merely more-recent ones.
    """
    if len(pivots) < 5 or prices.empty:
        return None

    candidates: list[HeadShoulders] = []
    # Newest window first; cap the scan so an old pattern cannot outrank a live one.
    for start in range(len(pivots) - 5, max(-1, len(pivots) - 25), -1):
        window = pivots[start:start + 5]
        pattern = _evaluate_window(
            prices, window, retest_tolerance_pct, trend_lookback
        )
        if pattern is not None:
            candidates.append(pattern)

    if not candidates:
        return None
    # Prefer the furthest-progressed pattern; break ties toward the most recent.
    return max(candidates, key=lambda p: (p.completion, p.right_shoulder.position))


def _evaluate_window(
    prices: pd.DataFrame,
    window: list[Pivot],
    retest_tolerance_pct: float,
    trend_lookback: int,
) -> HeadShoulders | None:
    left, trough_1, head, trough_2, right = window

    kinds = [p.kind for p in window]
    if kinds == ["high", "low", "high", "low", "high"]:
        direction = "bearish"
    elif kinds == ["low", "high", "low", "high", "low"]:
        direction = "bullish"
    else:
        return None  # pivots must alternate in the shoulder-head-shoulder shape

    # --- hard gate, part 1: the head is the extreme, so the right shoulder is
    # a lower high (bearish) or higher low (bullish). This IS the price-action
    # requirement; nothing about shoulder symmetry is checked here deliberately.
    if direction == "bearish":
        if not (head.price > left.price and head.price > right.price):
            return None
    else:
        if not (head.price < left.price and head.price < right.price):
            return None

    if trough_2.position <= trough_1.position:
        return None

    slope = (trough_2.price - trough_1.price) / (trough_2.position - trough_1.position)

    # A steeply sloped neckline extrapolated indefinitely runs off to absurd
    # levels — far enough that a falling price can chase it down without ever
    # crossing, so the pattern silently never completes. Traders extend a
    # neckline modestly, not forever, so the line is held flat beyond half a
    # pattern-width past the right shoulder. Scaling the limit to the pattern's
    # own width keeps it meaningful on any timeframe.
    pattern_width = max(1, trough_2.position - trough_1.position)
    extension_limit = right.position + max(1, pattern_width // 2)

    def neckline_at(position: int) -> float:
        capped = min(position, extension_limit)
        return trough_1.price + slope * (capped - trough_1.position)

    last_position = len(prices) - 1
    neckline_now = neckline_at(last_position)

    closes = prices["close"].to_numpy(dtype="float64")
    lows = prices["low"].to_numpy(dtype="float64")
    highs = prices["high"].to_numpy(dtype="float64")

    # --- hard gate, part 2: did price break the neckline, and then confirm?
    after = range(right.position + 1, len(prices))
    break_position: int | None = None
    for i in after:
        level = neckline_at(i)
        if direction == "bearish" and closes[i] < level:
            break_position = i
            break
        if direction == "bullish" and closes[i] > level:
            break_position = i
            break

    broke = break_position is not None
    extreme_confirmed = False
    if broke:
        if direction == "bearish":
            prior_low = min(trough_1.price, trough_2.price)
            extreme_confirmed = bool(np.nanmin(lows[break_position:]) < prior_low)
        else:
            prior_high = max(trough_1.price, trough_2.price)
            extreme_confirmed = bool(np.nanmax(highs[break_position:]) > prior_high)

    # --- staged completion, so setups are visible before they trigger
    notes: list[str] = []
    shoulder_gap_pct = abs(closes[-1] - right.price) / max(abs(right.price), 1e-9) * 100
    if broke and extreme_confirmed:
        completion = 1.0
    elif broke:
        completion = 0.7
        notes.append(
            "Neckline is broken but the confirming "
            f"{'lower low' if direction == 'bearish' else 'higher high'} has not printed yet."
        )
    elif shoulder_gap_pct <= retest_tolerance_pct * 2:
        completion = 0.5
        notes.append(
            "Price is back at the right-shoulder level, which is the aggressive "
            "early-entry zone — the neckline has not broken."
        )
    else:
        completion = 0.3
        notes.append("Shape is present but the neckline has not been tested.")

    # --- pullback / retest of a broken neckline
    in_pullback = False
    if broke and completion >= 0.7:
        distance_pct = abs(closes[-1] - neckline_now) / max(abs(neckline_now), 1e-9) * 100
        beyond = (closes[-1] < neckline_now) if direction == "bearish" else (closes[-1] > neckline_now)
        if distance_pct <= retest_tolerance_pct and beyond:
            in_pullback = True
            notes.append(
                "Price has returned to the broken neckline, which now acts as "
                f"{'resistance' if direction == 'bearish' else 'support'} — the pullback entry zone."
            )

    # --- confidence factors. None of these can invalidate the pattern.
    head_height = abs(head.price - (trough_1.price + trough_2.price) / 2.0)
    if head_height <= 0:
        return None

    symmetry = float(np.clip(1.0 - abs(left.price - right.price) / head_height, 0.0, 1.0))
    if symmetry < 0.6:
        notes.append(
            f"Shoulders are uneven ({symmetry * 100:.0f}% symmetry). That lowers confidence "
            "but does not invalidate the pattern — the lower-high/lower-low structure is what matters."
        )

    # A neckline sloping in the trade's direction means the confirming extreme
    # is already partly in place before the break, which the method explicitly
    # calls higher quality.
    slope_pct_per_bar = slope / max(abs(head.price), 1e-9) * 100
    favourable_slope = (slope_pct_per_bar < 0) if direction == "bearish" else (slope_pct_per_bar > 0)
    slope_score = float(np.clip(abs(slope_pct_per_bar) / 0.25, 0.0, 1.0)) if favourable_slope else 0.0
    if favourable_slope and slope_score > 0.3:
        notes.append(
            "The neckline slopes in the direction of the break, so the trend was "
            "already turning before the pattern completed — a higher-quality setup."
        )

    # Trend context: the same shape inside an established trend is a
    # continuation (pullback) rather than a reversal, and reads stronger.
    is_continuation = _is_continuation(prices, direction, trend_lookback)
    if is_continuation:
        notes.append(
            f"This formed inside an existing {'down' if direction == 'bearish' else 'up'}trend, "
            "so it reads as a continuation rather than a reversal."
        )

    volume_score, volume_note = _volume_confirmation(prices, break_position)
    if volume_note:
        notes.append(volume_note)

    quality = float(np.clip(
        0.40 * symmetry
        + 0.25 * slope_score
        + 0.20 * (1.0 if is_continuation else 0.4)
        + 0.15 * volume_score,
        0.0, 1.0,
    ))

    target = _measured_move(head.price, neckline_at(head.position), neckline_now, direction)

    return HeadShoulders(
        direction=direction,
        left_shoulder=left, trough_1=trough_1, head=head,
        trough_2=trough_2, right_shoulder=right,
        neckline_slope=slope,
        neckline_now=neckline_now,
        completion=completion,
        quality=quality,
        symmetry=symmetry,
        broke_neckline=broke,
        extreme_confirmed=extreme_confirmed,
        is_continuation=is_continuation,
        in_pullback=in_pullback,
        target=target,
        notes=notes,
    )


def _is_continuation(prices: pd.DataFrame, direction: str, lookback: int) -> bool:
    """True when the prevailing trend already runs the pattern's way."""
    closes = prices["close"].dropna()
    if len(closes) < lookback:
        return False
    window = closes.iloc[-lookback:]
    change = (float(window.iloc[-1]) / float(window.iloc[0]) - 1.0) * 100
    return change < -5.0 if direction == "bearish" else change > 5.0


def _volume_confirmation(prices: pd.DataFrame, break_position: int | None) -> tuple[float, str | None]:
    """Score volume expansion on the neckline break.

    Deliberately capped. Osler found that H&S completion coincides with unusual
    volume *and* with the pattern's lack of standalone profitability, reading
    the participants as noise traders. Heavy volume on the break is therefore
    treated as weak supporting evidence, not as the confirmation the method
    video presents it as.
    """
    if break_position is None or "volume" not in prices:
        return 0.4, None
    volume = prices["volume"].dropna()
    if len(volume) < 31 or break_position >= len(prices):
        return 0.4, None

    baseline = float(volume.iloc[max(0, break_position - 30):break_position].mean())
    if baseline <= 0:
        return 0.4, None
    ratio = float(prices["volume"].iloc[break_position]) / baseline
    score = float(np.clip((ratio - 1.0) / 1.0, 0.0, 1.0))
    if ratio >= 1.5:
        return score, (
            f"Volume on the break ran {ratio:.1f}x its 30-day average. Treat that as mild "
            "support only — research associates the volume spike at pattern completion with "
            "crowding rather than confirmation."
        )
    return score, None


def _measured_move(head_price: float, neckline_at_head: float,
                   neckline_now: float, direction: str) -> float | None:
    """Classic projection: neckline minus the head-to-neckline height."""
    height = abs(head_price - neckline_at_head)
    if height <= 0:
        return None
    return neckline_now - height if direction == "bearish" else neckline_now + height
