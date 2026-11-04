# Trading methods — source index

Rules extracted from video transcripts, written as implementable specifications
rather than prose, so they can become screener parameters without re-reading the
source each time.

| # | Method | Source | Status |
|---|---|---|---|
| 01 | Elliott Wave (validated counts only) | "The Good, the Bad, and the Bitcoin" — Elliott Wave Advanced V3 | Spec'd, not implemented |
| 02 | Head & Shoulders (+ inverse, 7 strategies) | Wise Trade — patterns masterclass | Spec'd, not implemented |

## Provenance convention

Each spec marks every rule with where it came from:

- **[T]** — stated explicitly in the transcript.
- **[S]** — standard published theory, added to fill a gap the transcript left
  open. Flagged because it was *not* in the source and is therefore an
  assumption to confirm, not something the video endorsed.
- **[D]** — a decision made during implementation, with the tradeoff recorded.

This matters: the Elliott Wave transcript explicitly declines to enumerate the
guidelines ("boy oh boy, these are really confusing"), so anything in that
section marked **[S]** is my addition and should be reviewed before it is given
weight in the scoring engine.

## How these map onto the existing screener

The screener already has the structure these methods need:

- **Signed stock-layer signals** in `[-1, +1]` — both methods produce a
  direction plus a conviction, which is exactly this shape.
- **`insufficient_data` with a reason** — this *is* the Elliott Wave author's
  central principle ("don't label anything you cannot validate"). The indicator
  he describes draws grey dots when no valid count exists; the screener drops
  the parameter, redistributes its weight, and states why. Same idea, already
  built.
- **Plain-language reasoning per parameter** — both methods are explanatory by
  nature, so the breakdown text is where the pattern narrative lives.

Planned parameters:

| Key | Layer | Source method | Signed? |
|---|---|---|---|
| `head_shoulders` | stock | 02 | yes |
| `elliott_wave` | stock | 01 | yes |
| `key_level` | stock | 02 (strategies 1–3) | yes |
| `mtf_confluence` | *modifier, not a parameter* | 02 (advanced) | n/a |

`mtf_confluence` is deliberately **not** a parameter. It measures agreement
*between* timeframes, so expressing it as one more weighted signal would
double-count the signals it is agreeing about. It belongs as a multiplier on
conviction, in the same slot the existing conviction gate occupies.

## Open architecture gap

Both methods are multi-timeframe. The screener currently stores **daily bars
only**.

- **Weekly** — free. Resample stored daily bars; exact, no new fetching.
- **4-hour** — not free. yfinance serves 60-minute bars for roughly the last 730
  days and has no native 4h interval, so 4h means fetching hourly and
  resampling, with a large increase in stored rows per ticker.

This is unresolved and needs a decision before implementation. See the open
questions at the bottom of each spec.
