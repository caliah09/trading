# Evidence review — what the research actually says

Compiled 2026-11-04. The purpose is to decide **what weight each method deserves**
in the screener, based on published evidence rather than on how persuasive a
video was.

Read the "So what" lines if nothing else. They are where the research changes a
design decision.

**Source-quality warning:** search returned a mix of peer-reviewed work and
trading blogs. Peer-reviewed findings are marked **[peer-reviewed]**;
practitioner claims I could not verify against a primary source are marked
**[unverified]**. Several numbers below come from secondary summaries of papers
I could not fetch directly — this sandbox blocks most academic hosts — so treat
exact figures as indicative and check the originals before relying on them.

---

## 1. Head & Shoulders — predicts, but barely trades

This is the best-studied pattern in technical analysis, and the results split by
market in a way that matters enormously for you.

### The headline split

| Study | Market | Result |
|---|---|---|
| Lo, Mamaysky & Wang (2000), *Journal of Finance* **[peer-reviewed]** | US equities 1962–96 | Patterns carry **incremental information**; H&S appeared 1,611 times vs 577 in simulated data |
| Chang & Osler | FX, 1973–94 | **Profitable** in two major currencies |
| Osler, *Identifying Noise Traders* (NY Fed) | US equities | **Not profitable** — "uniformly" so |
| Savin, Weller & Zvingelis (2007), *J. Financial Econometrics* **[peer-reviewed]** | S&P 500 + Russell 2000, 1990–99 | **Strong predictive power** over 1–3 months; profitability only at longer holds |

So: the pattern contains real information, and that information is hard to
convert into money in US stocks.

### The finding that should drive the build

Savin et al. report predictive power for excess returns over **one to three
month** horizons — but net of transaction costs, *"only for the 60-day holding
period does it appear that raw excess returns net of transaction costs remain
positive."*

**So what:** this is the most directly actionable number in the whole review.
Sixty days is not an arbitrary figure — it sits inside the 45–90 DTE window you
already chose for buying premium. The pattern's documented edge and your
configured option tenor line up almost exactly. Short-dated expressions of an
H&S signal are trading a horizon at which the evidence says the edge does not
survive costs.

### Osler's volume finding, and why it's a warning

Osler found the pattern's lack of profitability **coexists with unusual trading
volume around pattern completion**. Her framing — it's in the paper's title — is
that H&S traders are *noise traders*.

**So what:** heavy volume on a neckline break is not confirmation that the move
is real. It may be evidence the crowd has arrived. The H&S video treats a
momentum candle on the break as a trigger; the research suggests that is where
the pattern is most crowded. I will still score volume expansion as a quality
factor, but I'm noting the tension rather than pretending it isn't there.

### Detection sensitivity is not a detail

Later work on a modified recognition algorithm found that adding a filter
*"generally improves risk-adjusted excess returns"* for H&S tops.

**So what:** the results move depending on how you identify the pattern. This
validates the Wise Trade video's core insight from the opposite direction —
rigid geometric matching is the wrong filter. It also means our swing-detection
sensitivity is a real parameter with real effect, not a cosmetic setting, and it
should be exposed in Settings rather than hard-coded.

### Verdict for the screener

`head_shoulders` ships at **moderate weight, not high**. It nominates
candidates; it does not decide trades. The 60-day finding gets wired into the
DTE proposal. This is roughly what I'd already spec'd, now with a reason
attached instead of an opinion.

---

## 2. Elliott Wave — the targets failed a direct test

The general criticism is what the video's own author concedes: counts are
subjective, two analysts produce two answers. David Aronson calls it a "story"
prone to subjective revision after the fact; Mandelbrot also cautioned against
its predictive validity.

But one finding is much more specific and much more damaging:

> **Batchelor & Ramyar (2005)** found no significant difference in the frequency
> of Fibonacci ratios in the Dow Jones Industrial Average compared to random
> occurrence.

**So what:** this hits the exact mechanism the video's indicator uses. Its price
targets come from *"standard Elliott Wave Fibonacci extensions anchored to the
correct waves."* If Fibonacci ratios appear no more often than chance in index
data, the targets are decoration on a validated count rather than a tested
forecast.

Note what this does *not* kill. The three cardinal rules are **logical
consistency checks** — they say a count is internally legal, which is a
different claim from saying the count predicts anything. The author's real
contribution is epistemic: refusing to label what cannot be validated. That
principle is excellent and it's already how the screener handles missing data.

One counterpoint, from a review of the literature: standalone Elliott Wave gives
inconsistent predictive accuracy, but combining it with other tools improved
reliability in selected conditions **[unverified]**. That is consistent with
using it as one weighted input among nine rather than as a system.

### Verdict for the screener

**Weight 0 confirmed, and now justified rather than cautious.** It computes, it
shows its reasoning, you watch it. If the Fibonacci targets have no edge, the
structure recognition may still have some — but it has to earn weight by
observation, not by assumption.

---

## 3. The volatility risk premium — the one with real support

This is the strongest-evidenced finding in the entire review, and it is not in
either of your videos.

| Study | Finding |
|---|---|
| Carr & Wu (2009) **[peer-reviewed]** | Negative variance risk premium across major equity indices, 1996–2003 |
| Coval & Shumway (2001) **[peer-reviewed]** | Selling option exposure earned returns standard risk models **could not explain** |
| Duarte & Jones | Premium **rises with market volatility**; large returns to index option sellers |

Practitioner figures, consistent but unverified: VIX has averaged ~19.6% since
1990 against realised volatility of ~15.5% **[unverified]**; Cboe research puts
implied ~4.2 points above realised over 1990–2018 **[unverified]**.

Implied volatility systematically exceeds subsequent realised volatility. Sellers
of options are paid for bearing that gap, and it is structural — options aren't
perfectly hedgeable, so the premium persists rather than being arbitraged away.

### The part that complicates your setup

Two findings sit awkwardly against choices already made.

**First:** retail traders are *"more likely to purchase options than write
them"*, and that demand is part of what sustains the premium. The academic case
says the paying side is the buying side.

**You configured the screener for buying premium.**

I'm not telling you to flip it — there are good reasons to buy. Defined risk,
no assignment, no margin, and convexity when a pattern resolves violently are
all real, and the research above measures *average* outcomes, not what a
selective trader achieves. But you should know you picked the side the evidence
says is structurally disadvantaged, and that this was not something either video
mentioned.

**Second, the crash caveat, which is not a footnote:** on rare days realised
volatility blows through implied, and the seller *"pays for years of collected
premium all at once."* Selling premium trades frequent small wins for rare large
losses. That is a different psychological and risk profile, not simply a better
one — and it is exactly where position sizing stops being optional.

### Verdict for the screener

The `iv_rank` parameter is already posture-aware, so no code change is forced.
But the weighting conversation should change: **IV Rank arguably deserves more
weight than any chart pattern**, because it is the input with the strongest
academic support. Worth revisiting your technicals-led default (RSI 8, IV Rank
6) once you've traded with it.

---

## 4. Leveraged ETFs — my earlier estimate was too kind

I previously modelled decay as `3σ²` per year from daily rebalancing. The
research says rebalancing is **the smaller half of the problem**.

**Bessembinder (2026)**, 235 levered single-stock ETFs listed since April 2024:

| Component | Drag per month |
|---|---|
| Daily rebalancing | ~0.42% |
| Frictions — fees, trading costs, excess loan margins | ~1.04% |
| **Total** | **~1.47%** |

An earlier version of the same work put total underperformance at ~0.79%/month
for long funds and ~1% for short funds across ~34 funds. Both figures are from
secondary summaries **[unverified]**.

Two further points from that work:
- Underperformance is attributed to **return reversals**, not volatility alone —
  chop specifically, not movement generally.
- A worked 2025 example: **TQQQ returned +33.2% while 3× QQQ's return was
  +60.5%.** A year in which the index rose substantially and the 3× fund
  captured roughly half of its nominal multiple.

**Important scope caveat:** Bessembinder studied *single-stock* levered ETFs,
which carry higher fees and worse borrow than broad index 3× funds like TQQQ.
Do not apply 1.47%/month directly to TQQQ. The structural lesson — frictions
exceed rebalancing drag — still holds.

**So what:** my earlier spec computed decay from volatility alone and would have
understated total cost by roughly half. The instrument doc needs correcting to
add an explicit friction term, not just the `3σ²` volatility term.

---

## 5. Position sizing — the part that isn't in either video

Neither video covers it. The research is unanimous that it dominates.

Kelly: `f* = (bp − q) / b`, where `b` is payoff ratio, `p` win probability.

- **Half-Kelly is the standard professional practice** — surrenders ~25% of
  growth to remove ~75% of ruin risk **[unverified, but consistent across
  sources]**.
- Kelly is **highly sensitive to overestimated inputs**. Overstate your win rate
  and it sizes you into ruin.
- Backtests inflate those inputs, particularly with repainting indicators.

**So what, and it's pointed:** the H&S video's advanced section says that when
three timeframes align, the author takes *"more aggressively sized entries."*
That is Kelly reasoning without Kelly's discipline — scaling up on a confluence
signal whose win rate has never been measured. The research says estimation
error in exactly that input is what causes ruin.

The screener will surface "3/3 timeframes aligned" as a conviction input. It
will not convert that into a position size, and the reasoning text should not
imply confluence justifies size. That remains your call, ideally against
measured win rates — which is why **your trade history is worth more to this
project than any further transcript.**

---

## 6. Professional vs. retail — what actually differs

| | Professionals | Retail |
|---|---|---|
| Unit of analysis | The whole **volatility surface** — skew, term structure | Single-position indicators |
| Greeks | Managed at **portfolio** level | Per-position, if at all |
| Primary inputs | Order flow, IV, greeks, macro | Technical indicators |
| Option posture | Market makers typically **net short**, hedging gamma | Net **buyers** |

Indicators lag by construction — they need history to confirm. That is not a
flaw to be engineered away; it is what they are.

**So what:** "Wall Street level" does not mean better pattern recognition. It
means (a) thinking in volatility rather than direction, (b) managing risk at the
portfolio level rather than per trade, and (c) knowing your edge's actual
measured win rate. Our screener does (a) partially via `iv_rank`, does not do
(b) at all, and cannot do (c) without your trade history.

For a serious next step on the professional side, Euan Sinclair's *Option
Trading: Pricing and Volatility Strategies and Techniques* was the one
substantive book recommendation to surface — it covers volatility measurement
and forecasting, which is the gap between the two columns above.

---

## 7. What this changes

| Decision | Before | After |
|---|---|---|
| `head_shoulders` weight | "moderate, TBD" | Moderate, with DTE proposals anchored to the 60-day finding |
| `elliott_wave` weight | 0, out of caution | 0, justified — Fibonacci targets failed a direct test |
| `iv_rank` weight | 6 (technicals-led default) | Candidate for the **highest** weight; strongest evidence of any input |
| Leveraged ETF drag model | `3σ²` volatility term only | Must add a friction term; rebalancing is the smaller half |
| Volume on neckline break | Confirmation bonus | Keep, but flag that Osler reads it as crowding |
| Pivot sensitivity | Hard-coded | Must be user-configurable; results are sensitive to it |
| Position sizing | Out of scope | Still out of scope, now deliberately and with a reason |

---

## Sources

- [Lo, Mamaysky & Wang — Foundations of Technical Analysis (Journal of Finance, 2000)](https://onlinelibrary.wiley.com/doi/full/10.1111/0022-1082.00265) · [NBER working paper](https://www.nber.org/papers/w7613) · [full PDF](https://www.cis.upenn.edu/~mkearns/teaching/cis700/lo.pdf)
- [Osler — Identifying Noise Traders: The Head-and-Shoulders Pattern in U.S. Equities (NY Fed)](https://www.newyorkfed.org/medialibrary/media/research/staff_reports/sr42.pdf)
- [Osler & Chang — Head and Shoulders: Not Just a Flaky Pattern (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=993938)
- [Savin, Weller & Zvingelis — The Predictive Power of "Head-and-Shoulders" Price Patterns in the U.S. Stock Market](http://technicalanalysis.org.uk/reversal-patterns/SaWZ03.pdf)
- [A New Recognition Algorithm for "Head-and-Shoulders" Price Patterns (MPRA)](https://mpra.ub.uni-muenchen.de/60825/1/MPRA_paper_60825.pdf)
- [Elliott wave principle — criticism overview (Wikipedia)](https://en.wikipedia.org/wiki/Elliott_wave_principle)
- [Efficacy of Elliott Wave Theory to Forecast Stock Price Movement: A Retrospective Review](https://www.researchgate.net/publication/403528355_Efficacy_of_Elliott_Wave_Theory_to_Forecast_Stock_Price_Movement_A_Retrospective_Review)
- [Volatility Risk Premium Effect — Quantpedia](https://quantpedia.com/strategies/volatility-risk-premium-effect)
- [The volatility risk premium: why selling volatility has historically been profitable](https://www.pfolio.io/academy/volatility-risk-premium)
- [Selling Volatility: The Most Seductive Backtest in Finance](https://www.quantt.co.uk/resources/selling-volatility-premium)
- [Bessembinder — Rebalancing, Frictional Costs, and Returns to Levered Single-stock ETFs (SSRN)](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=7383378)
- [Leveraged Single Stock ETFs: Understanding Volatility Decay](https://bhseclaw.com/blog/leveraged-single-stock-etfs-volatility-decay/)
- [Retail Option Traders and the Implied Volatility Surface](https://fmai.memberclicks.net/assets/docs/Derivatives2022/RetailOptions.pdf)
- [Risk of Ruin: The Math That Explains Why Most Traders Blow Up](https://dtsystems.dev/blog/risk-of-ruin-trading)
- [The Kelly Criterion: How to Size Positions](https://www.tradicted.com/learn/kelly-criterion/)
