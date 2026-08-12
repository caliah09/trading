"""Did the high scores actually work?

For every stored ranking, this computes the underlying's forward return at a
set of horizons and adjusts it for the direction that was called at the time.
A put-lean pick that subsequently fell 3% counts as +3% "directional" — the
screener was right — while the same fall on a call-lean pick counts as -3%.

What this deliberately does *not* do is simulate option P&L. Doing that
honestly needs historical marks for a specific contract, which yfinance does
not provide reliably, and a fabricated fill would make the history view look
authoritative while being fiction. Underlying returns are a real measurement of
whether the directional read was correct, which is the question the ranking is
actually answering.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from screener.db import Database
from screener.market_calendar import is_trading_day

log = logging.getLogger(__name__)

#: Trading-day horizons measured after each ranking.
HORIZONS: tuple[int, ...] = (1, 5, 20)


def compute_forward_returns(db: Database, horizons: tuple[int, ...] = HORIZONS,
                            today: date | None = None) -> int:
    """Fill in any missing forward returns that enough time has now elapsed for.

    Safe to call on every refresh: runs already complete are skipped, and a
    horizon whose end date has not yet arrived is simply left for next time.

    Returns the number of (run, symbol, horizon) rows written.
    """
    today = today or date.today()
    pending = db.runs_needing_forward_returns(horizons)
    if not pending:
        return 0

    rows: list[tuple] = []
    computed_at = datetime.now(timezone.utc).isoformat(timespec="seconds")

    for run in pending:
        run_started = _run_date(run["started_at"])
        entries = db.run_entries_for_returns(run["id"])
        if not entries:
            continue

        for horizon in horizons:
            target_day = _add_trading_days(run_started, horizon)
            if target_day > today:
                continue  # not enough time has passed yet

            for entry in entries:
                start = db.price_on_or_after(entry["symbol"], run_started)
                end = db.price_on_or_after(entry["symbol"], target_day)
                if start is None or end is None or start[1] <= 0:
                    continue
                # Guard against the horizon resolving to the same bar, which
                # would record a meaningless 0% return.
                if end[0] <= start[0]:
                    continue

                return_pct = (end[1] / start[1] - 1.0) * 100.0
                sign = -1.0 if entry["direction"] == "put" else 1.0
                if entry["direction"] == "neutral":
                    # No direction was called, so there is nothing to be right
                    # about; record the raw move without a directional claim.
                    directional = 0.0
                else:
                    directional = return_pct * sign

                rows.append((
                    run["id"], entry["symbol"], horizon,
                    start[1], end[1], return_pct, directional, computed_at,
                ))

    db.save_forward_returns(rows)
    if rows:
        log.info("Wrote %d forward-return rows", len(rows))
    return len(rows)


def summarise_run_performance(db: Database, run_id: int, top_n: int,
                              horizons: tuple[int, ...] = HORIZONS) -> dict:
    """Compare the top N against the rest of the watchlist for one run.

    This is the question the history view exists to answer: did the tickers the
    screener ranked highest actually outperform the ones it ranked lower?
    """
    ranking = db.get_ranking(run_id)
    if not ranking:
        return {"run_id": run_id, "horizons": [], "available": False}

    top_symbols = {e["symbol"] for e in ranking if e["rank"] <= top_n}
    returns: dict[int, dict[str, list[float]]] = {
        h: {"top": [], "rest": [], "top_hits": []} for h in horizons
    }

    for entry in ranking:
        bucket = "top" if entry["symbol"] in top_symbols else "rest"
        for fr in entry.get("forward_returns", []):
            horizon = fr["horizon_days"]
            if horizon not in returns:
                continue
            returns[horizon][bucket].append(fr["directional_pct"])
            if bucket == "top" and entry["direction"] != "neutral":
                returns[horizon]["top_hits"].append(1.0 if fr["directional_pct"] > 0 else 0.0)

    out = []
    for horizon in horizons:
        data = returns[horizon]
        if not data["top"] and not data["rest"]:
            continue
        hits = data["top_hits"]
        out.append({
            "horizon_days": horizon,
            "top_avg": _mean(data["top"]),
            "rest_avg": _mean(data["rest"]),
            "edge": (None if _mean(data["top"]) is None or _mean(data["rest"]) is None
                     else round(_mean(data["top"]) - _mean(data["rest"]), 3)),
            "top_count": len(data["top"]),
            "rest_count": len(data["rest"]),
            "hit_rate": (None if not hits else round(100.0 * sum(hits) / len(hits), 1)),
            "hits": int(sum(hits)),
            "directional_picks": len(hits),
        })

    return {"run_id": run_id, "horizons": out, "available": bool(out), "top_n": top_n}


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def _run_date(started_at: str) -> date:
    """Date portion of a stored ISO timestamp."""
    return datetime.fromisoformat(started_at).date()


def _add_trading_days(start: date, count: int) -> date:
    """The date ``count`` trading sessions after ``start``."""
    cursor = start
    remaining = count
    # Bounded so a pathological input cannot spin: 10 calendar days per session
    # is far beyond any real holiday run.
    limit = count * 10 + 30
    steps = 0
    while remaining > 0 and steps < limit:
        cursor += timedelta(days=1)
        steps += 1
        if is_trading_day(cursor):
            remaining -= 1
    return cursor
