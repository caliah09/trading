# 00 — Expressing pattern signals as options and leveraged ETFs

Both methods in this folder output the same shape:

```
direction · entry zone · invalidation (stop) · target · expected hold duration
```

The screener's job is to turn that into an instrument. This note records how,
and flags one structural problem with leveraged ETFs that affects which signals
should be routed to them at all.

---

## 1. Options

The existing options layer already scores `delta_fit`, `dte_fit`, `iv_rank` and
`earnings`. The pattern methods add three inputs it does not currently have:

| Pattern output | Should drive |
|---|---|
| Target price | Strike selection — a strike beyond the projected target is paying for a move the method does not predict |
| Invalidation (stop) | Expiry choice and position sizing; also a clean exit rule |
| Expected hold duration | **DTE selection** — expiry must outlast the move, with margin |

### Changes this implies **[D]**

1. **DTE should follow the pattern, not a fixed preference.** The current target
   window (45–90 DTE buying) is a static setting. A daily-chart H&S that
   historically resolves over 3–6 weeks and a weekly Elliott Wave impulse
   projecting three months are different trades. Proposal: keep the configured
   window as the default, but let a confirmed pattern *propose* a DTE and show
   the gap when they disagree.

2. **Theta vs. pattern duration is the real risk.** An option bought on a
   correct pattern read still loses if the move takes longer than the expiry.
   The usual guidance is to buy roughly 2× the expected hold. Worth surfacing as
   an explicit line in the contract card rather than leaving implied.

3. **Defined risk changes what a "stop" means.** On stock, pattern invalidation
   is a stop-loss. On a long option, maximum loss is already the premium — so
   invalidation is an *exit signal*, not a risk cap. The reasoning text should
   not describe an option stop as if it bounds risk the way a stock stop does.

---

## 2. Leveraged ETFs — the structural problem

**This is the item most likely to cost real money, so it is stated plainly
rather than buried.**

Leveraged ETFs (TQQQ, SOXL, SPXL, SQQQ, …) target their multiple of the
underlying's **daily** return and rebalance every day. Over more than one day,
returns are **path-dependent**: the fund does not deliver 3× the period return,
and in a choppy market it loses value even when the underlying ends flat.

### The approximate size of it

For a fund with leverage `L` and underlying annualised volatility `σ`, the
expected drag on log return is roughly:

```
drag ≈ ((L² − L) / 2) × σ²     per year
```

For `L = 3` that is `3σ²` per year. Concretely:

| Underlying annualised vol | Expected drag per year | Per month |
|---|---|---|
| 20% | ~12% | ~1.0% |
| 30% | ~27% | ~2.3% |
| 50% | ~75% | ~6.3% |
| 60% | ~108% | ~9.0% |

*(Continuous-rebalancing approximation. Excludes the ~0.9–1.0% expense ratio
most 3× funds charge, and excludes embedded financing cost, both of which add
to it.)*

### Why this collides with these two methods specifically

Both methods are explicitly **higher-timeframe**:

- The Elliott Wave author: 4h, daily, weekly — *"on a one minute chart you'll
  mostly see dots."*
- The H&S method's strongest setup is weekly bias → daily confirmation → 4h
  entry.

So both generate signals with **multi-day to multi-week expected holds**, which
is precisely the window where leveraged decay compounds. A 3× product held six
weeks on a 50%-vol underlying gives up roughly 9% to drag before the directional
call is even scored.

**This does not mean don't.** It means drag is a cost that has to be in the
expected value, the same way option premium is. A pattern projecting a 4% move
on the underlying over six weeks is a bad leveraged-ETF trade and a fine options
trade; one projecting 15% over eight days is the reverse.

### What the screener should do about it **[D]**

1. Compute expected drag from the underlying's **realised volatility** — already
   available, it backs the IV Rank proxy — and the pattern's expected hold.
2. Show it as a line on the instrument card: *"~3.1% expected decay over the
   ~4-week projected hold at current 42% realised vol."*
3. Compare projected move against drag, and say so when drag eats most of the
   edge.
4. Never present a leveraged ETF as a plain 3× expression of the signal.

---

## 3. Leveraged ETF routing

To suggest a leveraged instrument, the screener needs a mapping from each
watchlist ticker to its bull/bear pair. Needs confirming — this is a starting
sketch, not a researched list **[D]**:

| Underlying | 3× bull | 3× bear |
|---|---|---|
| QQQ / NDX | TQQQ | SQQQ |
| SPY / SPX | UPRO or SPXL | SPXU or SPXS |
| Semis (SOXX) | SOXL | SOXS |
| Russell 2000 | TNA | TZA |
| Single names | *mostly unavailable* | — |

**The gap that matters:** the current watchlist is nine single names plus SPY and
QQQ. Leveraged ETFs exist for **indices and sectors**, not for AAPL or MSFT.
Single-stock leveraged products do exist (2× NVDA, 2× TSLA) but are thin,
expensive, and a different risk profile.

So leveraged-ETF routing only applies to a small part of the current watchlist
unless the watchlist changes. Worth deciding before building the routing.

### Two further points

- **IV Rank must be computed on the leveraged ticker itself**, never inherited
  from the underlying. TQQQ's implied volatility is roughly 3× QQQ's, and its
  own one-year range is the only meaningful comparison.
- **Options on leveraged ETFs stack leverage on leverage** — a 3× fund's option
  is a leveraged bet on a leveraged bet, with decay underneath. Treat as a
  separate instrument class, not a variant.

---

## 4. Open questions

1. Is leveraged-ETF routing wanted for **indices/sectors only**, or should the
   watchlist expand to sector ETFs so there is something to route to?
2. Should the screener ever suggest **options on leveraged ETFs**, or keep those
   two expressions separate?
3. Should pattern-derived expected hold **override** the configured DTE window,
   or only be displayed alongside it?
