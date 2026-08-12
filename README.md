# Personal Stock & Options Screener

A local-only dashboard that ranks your watchlist by how well each ticker matches
parameters *you* define, with a weighted composite score and a full,
parameter-by-parameter explanation of how every score was built.

> **This is a personal research tool, not financial advice.** Nothing it produces
> is a recommendation to buy or sell any security. Scores reflect the parameters
> and weights you configured, computed from delayed public data that may be
> incomplete or wrong. Options carry substantial risk of loss. Verify everything
> independently before risking money.

---

## Quick start

```bash
pip install -r requirements.txt

# Seed the database (optional — the app can also do this itself on first launch)
python -m screener refresh

# Start the dashboard. It schedules its own refreshes.
python -m screener serve
# → http://127.0.0.1:8765
```

To try it with no network access at all — no Yahoo, no live data:

```bash
python -m screener --provider synthetic refresh
python -m screener --provider synthetic serve
```

### Other commands

```bash
python -m screener status                      # last and next scheduled refresh
python -m screener watchlist                   # show the watchlist
python -m screener watchlist AAPL MSFT NVDA    # replace it
python -m screener refresh --json              # run once, machine-readable output
python -m screener --help
```

---

## How the score is built

The scoring logic is never hidden — this section is the same model the UI shows
you per ticker.

### Step 1 — direction, from the stock layer

Each **stock-layer** parameter produces a *signed* signal in `[-1, +1]`:
`+1` maximally bullish, `-1` maximally bearish, `0` no directional edge. Their
weighted mean is the **net directional signal**:

```
net = Σ(wᵢ · sᵢ) / Σ wᵢ
```

Its **sign** picks the call/put lean. Its **magnitude** is *conviction*, in
`[0, 1]`. A ticker whose indicators disagree nets toward zero and is reported as
having no edge, rather than being scored on whichever indicator shouts loudest.

This is a **bidirectional** screener: a strong bearish setup scores exactly as
highly as an equally strong bullish one, and is surfaced as a put candidate.

### Step 2 — contributions

Every parameter's contribution is expressed in the same units as the final
score, so the breakdown the UI shows literally adds up to the composite:

```
scale                 = 100 / Σ(all counted weights)
stock contributionᵢ   = scale · wᵢ · sᵢ · dir
options contributionⱼ = scale · wⱼ · qⱼ · gate
```

`dir` is `+1` for a call lean and `-1` for a put lean. A stock parameter that
**disagrees** with the chosen direction therefore contributes *negatively*, and
the UI shows it in red with an explanation.

**Options-layer** parameters produce an *unsigned* quality score in `[0, 1]`:
they measure how well the options market lets you express the trade, not which
way to trade. They are evaluated *for* the direction the stock layer chose.

### Step 3 — the conviction gate

`gate` is the conviction from step 1 (or `1.0` if you switch the gate off in
Settings).

**Why:** without it, a ticker with no directional signal but a cheap,
perfectly-sized contract would score respectably — surfacing a well-priced way
to bet on nothing.

**The cost:** it couples the two layers, so a strong options setup cannot rescue
a flat chart. That is intended for a directional screener. Turn the gate off in
Settings if you would rather score the layers independently.

### Continuous ramps, not pass/fail

A threshold is the **half-credit point** (score `0.5`), and each parameter's
`width` is the distance past it that reaches ~88% strength.

```
RSI oversold threshold = 30, width = 12
RSI 31 → 0.48      RSI 29 → 0.52
RSI 18 → 0.88      RSI  6 → 0.98
```

With a hard threshold, RSI 29.9 and 30.1 land at opposite extremes while being
practically identical, and deeply oversold RSI 12 is indistinguishable from
barely-oversold RSI 29. Since this tool *ranks*, smooth ramps keep the ordering
meaningful. **Setting a small width approximates hard pass/fail**, so the ramp
generalises both styles.

### Missing data

Parameters that cannot be computed are dropped from **both** the numerator and
the denominator, so scores stay comparable across tickers with different data
coverage. The UI shows a completeness badge (`7/9`) and the reason for every
omission.

`not_applicable` (SPY has no earnings date because it is a fund) is
distinguished from `insufficient_data` (the fetch failed, or history is too
short).

### Tie-breaking

Deterministic cascade, so the ordering never shuffles between refreshes:

1. Composite score
2. Contribution of your **highest-weighted** parameter
3. 30-day average dollar volume (prefer the more tradeable name)
4. Ticker, alphabetically

The tie-break reason is shown in the ticker's detail view.

---

## The parameters

| Parameter | Layer | Default weight | Measures |
|---|---|---|---|
| RSI (14) | stock | 8 | Wilder's RSI; oversold → bullish, overbought → bearish |
| MA 20/50 crossover | stock | 7 | 20-day vs 50-day spread, as % of the 50-day |
| MACD crossover | stock | 7 | MACD(12,26,9) histogram, normalised by price |
| 200-day MA | stock | 6 | Distance from the 200-day, as support/resistance |
| Volume vs 30-day avg | stock | 5 | Volume multiple, signed by recent price direction |
| IV Rank / Percentile | options | 6 | Where IV sits in its 1-year range (posture-aware) |
| Days-to-expiration fit | options | 4 | Best expiry vs your target window |
| Delta proximity | options | 5 | Selected contract's delta vs your target band |
| Earnings proximity | options | 4 | Trading days to the next report (posture-aware) |

Every weight, threshold and target is editable in **Settings**. Weight `0`
switches a parameter off entirely.

### Posture

Four parameters invert with your **posture**, set in Settings:

| | Buying premium | Selling premium |
|---|---|---|
| **IV Rank** | low is good (options are cheap) | high is good (premium is rich) |
| **Earnings** | approaching is a bonus (vol expansion) | approaching is a hazard (gap risk) |
| **Delta target** | 0.45–0.60 | 0.16–0.30 |
| **DTE target** | 45–90 days | 30–45 days |

---

## Scheduling

**The scheduler runs inside the app.** Starting `screener serve` starts it —
there is no cron or Task Scheduler entry to create.

- **Market open** — 09:35 ET
- **Market close** — 16:10 ET

Both are anchored to `America/New_York`, so they follow daylight saving, and
both are guarded by a market calendar that skips weekends and holidays. The
dashboard header always shows the last refresh and the next scheduled one, and
**Refresh Now** runs the same pipeline on demand.

> ### ⚠️ The tradeoff
>
> **Automatic refreshes only happen while the app is running.** Close the
> terminal, reboot, or let the machine sleep through the close, and that refresh
> does not happen. An OS-level scheduler would have run it regardless.
>
> What softens this: on startup the app checks whether a scheduled refresh was
> missed since the last stored run, and if so runs one immediately — so
> launching on Monday morning gives you current data rather than Friday's. It
> cannot reconstruct a ranking *as of* a moment that has passed, because the
> market data for that moment is gone. Disable with `serve --no-catch-up`.

Refreshes are serialised behind a lock. A second request while one is running
gets `409 Conflict` rather than starting an overlapping writer.

---

## Data source and its limits

`yfinance` is an unofficial scraper of Yahoo Finance. Free, no API key — and
unreliable in specific ways, all absorbed at the provider boundary
(`screener/providers/yfinance_provider.py`):

- Transient rate limiting and connection resets → retried with backoff
- Schema drift between versions → normalised to a fixed schema
- Silent empties for delisted/mistyped tickers → returned as empty frames
- Implied volatility of `0.0` or absurd values on illiquid strikes → coerced to
  missing, so they are never scored as if real

Above that boundary, everything can assume clean data.

### yfinance publishes no greeks

Delta is a scoring parameter, so it is computed locally with Black-Scholes from
the chain's own implied volatility (`screener/blackscholes.py`). Assumptions
worth knowing:

- **European exercise.** US equity options are American. For the near-the-money
  contracts this screener targets the difference is small, but it is not zero
  for deep ITM puts.
- **Flat risk-free rate and dividend yield** from config, not a live curve.
- **The chain's published IV is taken at face value**, which is why the hygiene
  filters (open interest, spread width) run before contract selection.

### IV Rank cannot be computed on day one

Yahoo gives you *current* IV only — there is no historical IV series, so
"where does today's IV sit in its 52-week range?" has nothing to rank against
until you have collected some.

The screener therefore:

1. Uses a **252-day realised-volatility percentile** as the proxy immediately,
   computable from price history on day one, and
2. Snapshots real at-the-money IV on every run, then
3. **Switches automatically** to true IV Rank once 60 observations exist
   (configurable).

The UI always badges which source a score used, so you are never misled about
what you are looking at.

Every IV snapshot is taken at the **same reference tenor** (~30 DTE), regardless
of which contract the screener suggests. Sampling a 45-day expiry today and a
7-day expiry next week would measure the term structure rather than a change in
volatility, making the whole rank meaningless.

---

## Design decisions worth arguing with

These are judgement calls where a different answer is defensible. They are
called out rather than buried.

1. **Redistribution rewards absent data.** Weight from parameters that could not
   be computed is redistributed across the ones that did. This keeps scores
   comparable across tickers — but it means a ticker whose *weak* parameters
   happen to be missing can outrank a complete one. In the synthetic demo,
   `NOCHAIN` (no options chain, 7/9 parameters) ranks first partly for that
   reason. The completeness badge is shown on every row so you can see it. If
   this bothers you, the alternatives are scoring missing parameters as zero
   (penalises thin/new names) or excluding the ticker entirely.

2. **Forward returns measure the underlying, not option P&L.** Simulating
   contract P&L honestly needs historical marks for a specific option, which
   yfinance does not provide reliably. A fabricated fill would make the history
   view look authoritative while being fiction. Underlying returns, adjusted for
   the direction called, are a real measurement of whether the read was correct.

3. **The MA/MACD scores come from spread size, not crossover recency.** A fresh
   golden cross arguably deserves more weight than a six-month-old one. The
   crossover age *is* computed and shown in the reasoning ("golden cross 4
   sessions ago"), but it does not alter the score — adding a recency multiplier
   would be another tunable knob with little evidence behind its shape.

4. **Volume is signed by price direction using a soft sign.** Volume alone is
   directionless. A hard `sign()` on a near-flat tape would flip the whole
   signal on noise, so `tanh(move / scale)` fades through zero instead.

5. **The market calendar is hand-rolled.** `pandas_market_calendars` is more
   complete (early closes, historical rule changes) but is a heavy dependency
   for the one question asked here: *is today a session?* Ad-hoc closures
   (national days of mourning, weather) are not modelled; worst case is one
   wasted fetch.

6. **No authentication.** The app binds to `127.0.0.1` and has no login. Do not
   bind it to a public interface — `serve --host` warns if you try.

---

## Architecture

```
screener/
  param_specs.py         Declarative registry — drives defaults AND the Settings UI
  config.py              User config, validation, content-addressed fingerprinting
  indicators.py          Pure indicator maths (RSI, MACD, MAs, realised vol)
  blackscholes.py        Greeks, because yfinance has none
  market_calendar.py     Dependency-free US session calendar
  options_analysis.py    IV sampling, chain hygiene, contract selection
  scoring/
    ramps.py             Continuous scoring primitives
    parameters.py        The nine scorers — value, signal, and plain-language reason
    engine.py            Composition, conviction gate, redistribution, ranking
  providers/
    base.py              The provider interface — every network call goes through it
    yfinance_provider.py Live data + all the normalisation
    synthetic.py         Deterministic fake data for tests and offline use
  db.py                  SQLite persistence
  pipeline.py            Fetch → score → rank → persist
  performance.py         Forward returns for the history view
  scheduler.py           In-process market open/close scheduling
  api/
    app.py               FastAPI
    web/                 The dashboard (vanilla JS, no build step)
  cli.py                 Entry points
```

The scoring engine never knows where its numbers came from, which is why the
whole test suite runs without a network.

### Database

SQLite, at `data/screener.sqlite` by default (`--db` to change).

Every run stores the **fingerprint of the config that produced it**, and the
config JSON is stored once per distinct fingerprint. History therefore always
shows what you actually saw that day, and the UI flags runs made under settings
different from your current ones.

Full option chains are **not** stored — that is tens of thousands of rows of
mostly-noise per run. What is persisted is the derived measurements the engine
used, plus the single suggested contract.

---

## Tests

```bash
python -m pytest              # 362 tests, ~10s, no network required
python -m pytest -v tests/test_engine.py
```

No test touches the network. Network tests are slow, flaky, and would fail on a
market holiday or a Yahoo outage — none of which says anything about whether
this code is correct. `SyntheticProvider` generates deterministic data seeded
from the ticker symbol, including deliberately awkward cases:

| Symbol | Exercises |
|---|---|
| `BADTICK`, `DELISTED` | Unknown ticker — empty frames, skipped with a reason |
| `NOCHAIN` | No options chain — options parameters redistribute |
| `NEWIPO` | 60 bars only — no 200-day MA |
| `SPY`, `QQQ` | Funds — earnings is `not_applicable`, not missing |

---

## Known limitations

- Quotes outside market hours fall back to last traded price, which is staler
  than a live mid.
- The 200-day MA needs 200 sessions; recent IPOs score on fewer parameters.
- Earnings dates come from Yahoo's calendar and are sometimes wrong or absent.
  They are cached for 18 hours.
- American-exercise and dividend effects on delta are approximated (see above).
- There is no migration path defined for schema changes; the app warns and asks
  you to back up if it sees an unexpected `user_version`.
