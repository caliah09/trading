# 01 — Elliott Wave, validated counts only

**Source:** "The Good, the Bad, and the Bitcoin" — *Elliott Wave Advanced V3*
(open-source Pine Script indicator).

**Core thesis, in the author's words:** most Elliott Wave indicators detect swing
points and then label them `1 2 3 4 5 A B C` forever, without ever checking
whether the count is legal. The result is a chart covered in confident labels
that violate the theory they claim to follow. His fix: **don't label anything
you cannot validate.** When no legal count exists, draw neutral grey dots and
say so.

That principle is already how this screener handles missing data, so the method
drops into the existing architecture rather than fighting it.

---

## 1. The three cardinal rules — hard, invalidating

These are not guidelines. A count that breaks any one of them is wrong, full
stop. All three are **[T]**.

Let an impulse run `0 → 1 → 2 → 3 → 4 → 5`, where `0` is the start of wave 1.

| # | Rule (bullish impulse) | Bearish mirror |
|---|---|---|
| R1 | `low(wave 2) > price(0)` — wave 2 never retraces beyond the start of wave 1 | `high(wave 2) < price(0)` |
| R2 | `len(w3) ≠ min(len(w1), len(w3), len(w5))` — wave 3 is never the shortest of 1, 3, 5 | same |
| R3 | `low(wave 4) > high(wave 1)` — wave 4 cannot enter wave 1's price territory | `high(wave 4) < low(wave 1)` |

Notes:

- R2 says *shortest*, not *shorter than both*. Wave 3 may be shorter than wave 1
  **or** shorter than wave 5, but not shorter than both. Measure length as
  absolute price travel, not bar count. **[D]**
- R3 has a documented exception: **diagonals** permit wave 4 to overlap wave 1.
  The transcript mentions this ("it might diagonal, but it's not an irregular
  impulse wave"). Implementation decision: if R1 and R2 hold but R3 fails,
  classify as a *candidate diagonal* at reduced confidence rather than
  discarding it outright. **[D]**

### Implementation note

The author flags why most indicators skip this: validation requires holding
several pivots in memory at once, computing ratios between non-adjacent waves,
and *retracting* counts that later fail. In Pine Script that means arrays of
user-defined types under a bar-by-bar execution model, which is awkward. **[T]**

In Python over a complete pandas series this constraint does not exist — we can
enumerate candidate pivot groupings and filter. The cost moves from awkwardness
to combinatorics: with `n` pivots there are many possible 6-pivot groupings, so
the search needs bounding. **[D]**

---

## 2. Corrective patterns

All three descriptions are **[T]**.

### Zigzag (A-B-C)
- Sharp, impulsive move.
- Wave B retraces **38%–79%** of wave A.
- Wave C usually extends **beyond** wave A's end.

### Flat (A-B-C)
- "The tricky ones that trap traders."
- Wave B returns all the way to the **start of wave A**, or beyond it.
- Wave B beyond the start of A → **expanded flat**.
- Distinguished from zigzag because the two have different implications.

### Contracting triangle (A-B-C-D-E)
- Converging boundaries; each wave shorter than the one before.
- Highs declining, lows rising.
- **Require at least 3 of the 4 contraction relationships to hold** — the author
  deliberately does not demand all four, "because real markets aren't perfectly
  geometric." **[T]**

That 3-of-4 tolerance is the same philosophy as the Head & Shoulders method's
treatment of imperfect patterns, and both should use the same tolerance style.

---

## 3. Guidelines (probability, not validity)

The transcript explicitly **does not enumerate these** — it says they exist,
calls them the point where most people give up, and moves on.

Everything in this section is therefore **[S]**: standard published Elliott Wave
theory, added by me to make the confidence score implementable. **Review before
weighting.** None of it can invalidate a count; it only adjusts confidence.

- Wave 2 commonly retraces 50%–61.8% of wave 1. **[S]**
- Wave 3 commonly extends to 1.618× wave 1, and is frequently the longest. **[S]**
- Wave 4 commonly retraces ~38.2% of wave 3. **[S]**
- **Alternation:** if wave 2 is a sharp correction, wave 4 tends to be sideways,
  and vice versa. **[S]**
- Wave 5 commonly equals wave 1, or reaches 0.618× the span of waves 1–3. **[S]**

---

## 4. Output model

Mirrors the indicator's status panel. All fields **[T]**.

| Field | Values | Example from transcript |
|---|---|---|
| `trend` | bullish / bearish | bullish |
| `phase` | impulse / correction | correction |
| `scenario` | free text | "correction complete, bearish trend resuming" |
| `action` | buy zone / sell zone / none | sell zone |
| `target` | price | 644 |
| `stop` | price | 738 |
| `confidence` | 0–100% | 40% |
| `rating` | 0–5 stars | 2/5 |
| `pattern` | impulse / zigzag / flat / expanded flat / triangle / diagonal | regular flat |
| `substructure` | optional, with its own confidence | diagonal @ 65% |

### The gating rule — the most important design point

**Forecast is gated behind pattern confirmation.** **[T]**

- Pattern validated → `confirmed`. Emit structure-aware targets from Fibonacci
  extensions anchored to the correct waves. After a completed correction, project
  the next impulse; after a completed impulse, anticipate the correction.
- Pattern not validated → `unconfirmed`. **No wave labels, no directional call,
  no buy/sell zone.** Only cautious observations about price relative to recent
  pivots.

The author's framing: *"the forecast earns the right to exist by being backed by
a validated count."* In screener terms this maps directly onto
`status="insufficient_data"` with the reason surfaced — not a neutral 0 score,
which would imply we looked and found balance rather than that we could not
look at all.

---

## 5. Stated limitations

Straight from the transcript, worth preserving because they bound where this
parameter should carry weight. All **[T]**.

- **Elliott Wave is inherently subjective.** Two experienced analysts can produce
  different *valid* counts on the same chart. The indicator picks the
  highest-confidence pattern; that does not make it the only legal reading.
- **Higher timeframes only** — 4h, daily, weekly. On a 1-minute chart you will
  mostly see dots, "and that's the indicator doing its job correctly."
- **The author is not an Elliott Wave expert** — self-described quant
  implementing the framework faithfully, open-sourced specifically so
  practitioners can correct it.

---

## 6. Mapping to a screener parameter

**Key:** `elliott_wave` · **Layer:** stock · **Signed:** yes

```
signal = direction_sign × confidence
  where direction_sign = +1 if the validated scenario implies upside, −1 if downside
        confidence     ∈ [0, 1] from rule compliance + guideline fit
```

| Count state | Parameter result |
|---|---|
| Valid impulse or correction, scenario implies a direction | `status="ok"`, signed signal |
| Valid count but scenario is mid-pattern with no directional implication | `status="ok"`, signal ≈ 0 |
| No legal count | `status="insufficient_data"`, reason names the rule that failed |
| Fewer pivots than a count needs | `status="insufficient_data"`, reason names the shortfall |

The reason string should name **which cardinal rule broke**, e.g. *"No valid
count: the best candidate puts wave 4 inside wave 1's range (R3), and the next
best retraces wave 2 past the start of wave 1 (R1)."* That is far more useful
than "no pattern found," and it is the whole point of the method.

### Proposed default weight

Start at **0**, i.e. shipped switched off, and raise it deliberately after
reviewing counts against charts you trust. **[D]**

Rationale: the method is subjective by its author's own admission, the guideline
set driving confidence is **[S]** rather than sourced, and a parameter at weight
0 still computes and displays its reasoning in the breakdown — so it can be
evaluated for weeks before it is allowed to move a ranking.

---

## 7. Open questions

1. **Timeframe.** Daily and weekly are available from stored bars. Is 4h needed,
   given it requires hourly fetching and a large storage increase?
2. **Pivot sensitivity.** Wave counts are extremely sensitive to the swing
   detection threshold. One ATR-scaled setting, or user-configurable?
3. **Should the Fibonacci targets drive the options contract selection** (expiry
   chosen to cover the projected move) or only display as context?
