# 02 — Head & Shoulders, including imperfect patterns

**Source:** Wise Trade — Head & Shoulders masterclass.

**Core thesis:** the pattern's validity rests on **price action**, not geometry.
A textbook-symmetric H&S is rare; what actually matters is a **lower high
followed by a lower low** (bearish) or a **higher low followed by a higher
high** (bullish). Everything else is cosmetic.

The author is blunt about why this matters: *"if you just sit on the sidelines
and wait for the picture-perfect by-the-book patterns, you'll miss out on 95% of
the best trades."* **[T]**

---

## 1. Anatomy

### Bearish (standard H&S) — reversal from uptrend to downtrend

| Element | Definition |
|---|---|
| Left shoulder | Swing high |
| Head | Higher swing high |
| Right shoulder | **Lower** high |
| Neckline | Line through the two intervening swing lows |
| Completion | Break of neckline **+ a lower low** |

### Bullish (inverse H&S) — reversal from downtrend to uptrend

Mirror: left shoulder swing low, head lower swing low, right shoulder **higher**
low, neckline through the two intervening swing highs, completed by a neckline
break **+ a higher high**.

### What it means in price-action terms **[T]**

Bearish: buyers lost momentum — price **failed to make a higher high** and made
a lower high instead. Once a lower low follows, lower high + lower low together
constitute a moving downtrend. The pattern is just a readable picture of that
transition.

---

## 2. Imperfect patterns — the central lesson

> "Because it is imperfect does not mean the pattern is invalid."

**Tolerated deviations** (each named in the transcript, with a worked example):

| Deviation | Example given |
|---|---|
| Neckline points not level — one swing low well below the other | DraftKings, Delta Airlines |
| Right shoulder slightly **higher** than the left shoulder | Bill.com |
| Right shoulder higher **and** the second neckline point higher | Block (Square) |
| Head is a **double top** rather than a single peak | Broadcom |

**The invariant that must hold:** lower high + lower low (bearish), or higher
low + higher high (bullish). The author's test is literally *"none of this was
jeopardised through the imperfection"* — if the core trend structure survives,
the pattern stands. **[T]**

### Implementation consequence **[D]**

This is the single most important thing to get right, and it inverts the usual
approach. Do **not** score shoulder symmetry and neckline levelness as
requirements. Instead:

```
valid  = lower_high_confirmed AND lower_low_confirmed      # hard gate
quality = f(symmetry, neckline slope, volume, level confluence)   # confidence only
```

A pattern with 40% shoulder asymmetry and a valid lower-high/lower-low sequence
is **tradeable and valid** — merely lower-confidence. A pattern with perfect
symmetry but no lower low is **not complete**.

This maps cleanly onto the screener's continuous-ramp model: the invariant is
the gate, the cosmetics scale the signal.

### Supporting confluence **[T]**
- A trendline drawn across the swing lows, broken at the same time as the
  neckline, is an **additional** trend-change confirmation (not a requirement).

---

## 3. Variants

### Slanted neckline — explicitly **higher quality** **[T]**

When the neckline slopes down (bearish), price has **already made a lower low**
before the neckline even breaks. The downtrend is in motion; the break merely
confirms momentum already visible.

Scoring consequence: a downward-slanted neckline should **raise** confidence,
not lower it. Treating "neckline not horizontal" as a defect inverts the
method. **[D]**

Inverse mirror: upward-slanted neckline means the higher high is already in.

### H&S inside an existing downtrend — continuation, not reversal **[T]**

Worked through on the Block chart. If the larger trend is already down, an H&S
forming within it is a **pullback**, and the neckline break signals trend
**continuation**, not reversal.

Consequence: the parameter must read the prevailing trend before deciding
whether the pattern means "reversal" or "continuation." The direction of the
resulting signal is the same (bearish), but the narrative text and the
confidence differ — continuation in the direction of the dominant trend is the
higher-probability case.

---

## 4. Entry models

Four distinct entries, all **[T]**. They differ in aggression and in how early
they commit.

| # | Entry | Trigger |
|---|---|---|
| E1 | **Breakout** | Close beyond the neckline with a momentum candle, plus the lower low (bearish) / higher high (bullish) |
| E2 | **Pullback** | Price retests the broken neckline — now flipped S→R or R→S — and prints a reaction candle, then an intraday trend-change confirmation |
| E3 | **Right-shoulder (aggressive)** | The right shoulder reaches the left shoulder's level, which is a technical S/R level; take a reaction candle plus a trend-change confirmation there |
| E4 | **Right-shoulder, multi-timeframe** | As E3, but drop to a lower timeframe and look *inside* the right shoulder for a trend-change pattern, for an earlier and tighter entry |

### Reaction-candle vocabulary **[T]**

What counts as "a reaction to the level," named explicitly across the examples:

- Long-wick candle (wick sticking out, body rejected)
- Shrinking candles
- Candle colour change
- Inside bar

### Critical caveat, repeated twice **[T]**

> "A reaction does not equal a trade — price can stall at the level, form
> candles, react to the level, but still push right through."

A reaction candle alone is never the trigger. It must be followed by a
**trend-change confirmation**: either a momentum candle off the level, or a
trendline break, or a confirmed structure break on a lower timeframe.

### Note on E1 **[T]**
After a breakout, the neckline retest (E2) is still available, and the original
breakout entry remains valid for **scaling into** the position.

---

## 5. The seven strategies

| # | Strategy |
|---|---|
| 1 | H&S forming **at a key resistance level** (inverse at key support) |
| 2 | Key level → candlestick reaction at the level → drop a timeframe → H&S **inside** that reaction |
| 3 | Moving trend → pullback → candle PA at the level → H&S **inside** on a lower timeframe |
| 4 | H&S pullback entry → look **inside the pullback** on a lower TF for an intraday trend change |
| 5 | Slanted-neckline H&S → pullback → look inside the pullback on a lower TF |
| 6 | Aggressive early entry at the right shoulder (= left shoulder's level) |
| 7 | Right-shoulder entry + multi-timeframe: look inside the right shoulder on a lower TF |

Strategies 2, 3, 4, 5 and 7 are all the same shape: **a higher timeframe
identifies the zone, a lower timeframe provides the trigger.**

Stated to apply identically across all asset classes and all timeframes. **[T]**

---

## 6. Top-down multi-timeframe confluence (the advanced method)

Three timeframes, each with a distinct job. **[T]**

| Timeframe | Role | Worked example (Plug Power) |
|---|---|---|
| Weekly | Directional **bias** from a key level | Wicks rejecting a major weekly resistance |
| Daily | **Confirm** the bias with matching price action | RSI divergence at the weekly level + lower highs |
| 4-hour | **Entry** | Double top / descending triangle inside the right shoulder |

> "All your three core time frames are confluent because they're giving you the
> same directional bias."

**Position sizing:** the author states that when all three align, he takes
*"more aggressively sized entries."* **[T]**

### Why this is a modifier, not a parameter **[D]**

Confluence measures **agreement among signals already being scored**. Adding it
as a tenth weighted parameter would count the weekly level once as `key_level`
and again as `mtf_confluence`. It belongs where the existing conviction gate
sits — scaling conviction, not contributing its own points.

### Sizing — deliberately out of scope **[D]**

The screener ranks and explains; it does not size positions or manage risk. It
can surface "3/3 timeframes aligned" as a conviction input. Converting that into
an aggressive position size is a decision for the person reading the dashboard,
and the tool should not imply otherwise.

---

## 7. Mapping to screener parameters

### `head_shoulders` · stock layer · signed

```
if not (lower_high_confirmed and lower_low_confirmed):   # hard gate, section 2
    status = "insufficient_data"   # or "forming", if shoulders present but incomplete
signal = -1 × completion × quality        # bearish H&S
       = +1 × completion × quality        # inverse H&S
```

`quality` factors, each scaled, none able to invalidate:
- Neckline slope in the confirming direction → **bonus** (section 3)
- Shoulder symmetry → mild bonus; asymmetry is **not** a penalty to zero
- Volume expansion on the neckline break → bonus
- Coincident trendline break → bonus
- Pattern sits at a higher-timeframe key level → bonus (overlaps `key_level`;
  avoid double counting)

`completion` as a staged value rather than a boolean, so the ranked list can
surface setups *before* they trigger: **[D]**

| State | completion |
|---|---|
| Shoulders + head present, neckline not broken | 0.3 |
| Right shoulder at the left-shoulder level (E3 zone) | 0.5 |
| Neckline broken, no confirming lower low yet | 0.7 |
| Broken + lower low confirmed | 1.0 |
| Broken, retested, holding (E2 zone) | 1.0, flagged as pullback entry |

### `key_level` · stock layer · signed

Swing highs/lows from the **weekly** series, extended forward. Signal reflects
proximity to a level plus whether price is reacting to it (section 4
vocabulary). Underpins strategies 1–3.

### `mtf_confluence` · conviction modifier

Weekly bias, daily bias, entry-timeframe bias → count agreement, scale
conviction. Not a weighted parameter.

---

## 8. Open questions

1. **Entry timeframe.** Weekly and daily come free from stored bars. Strategies
   2–5 and 7 all need a lower timeframe for the trigger — is 4h (hourly fetch +
   resample) worth the storage cost, or is daily-only acceptable with the
   understanding that entries will be looser?
2. **Does the screener need to distinguish the four entry models**, or is it
   enough to report the pattern state and let you pick the entry?
3. **Pivot sensitivity** — same question as Elliott Wave; the two should share
   one swing-detection implementation so their pivots agree.
