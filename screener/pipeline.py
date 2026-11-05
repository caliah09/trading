"""The refresh pipeline: fetch, score, rank, persist.

One pass over the watchlist produces one immutable *run*. A run is never
partially visible: entries are written after every ticker has been scored, so
the dashboard either shows the previous complete ranking or the new one.

Per-ticker failures are contained. A delisted symbol, an empty chain or a
scorer raising degrades that one row — recorded with a reason — rather than
aborting the refresh.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import pandas as pd

from screener.config import ScreenerConfig
from screener.db import Database
from screener.options_analysis import analyse_options
from screener.performance import compute_forward_returns
from screener.providers.base import MarketDataProvider, fetch_price_history
from screener.scoring.engine import (
    TickerScore,
    average_dollar_volume,
    compose,
    derive_direction,
    rank,
    score_options_layer,
    score_stock_layer,
)
from screener.scoring.parameters import TickerData

log = logging.getLogger(__name__)

#: Calendar days of history requested on a cold fetch. The longest lookback the
#: scorers need is a 252-session volatility percentile over a 21-day rolling
#: window (~273 sessions), which is roughly 400 calendar days; 730 leaves room.
COLD_FETCH_DAYS = 730

#: Once enough history is stored, only recent bars are re-fetched.
WARM_FETCH_DAYS = 45

#: Sessions of stored history below which a cold fetch is forced.
WARM_THRESHOLD_BARS = 300

#: How long a cached earnings date is trusted before being re-fetched.
EARNINGS_TTL = timedelta(hours=18)


@dataclass
class RunSummary:
    """Outcome of one refresh."""

    run_id: int
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    scored: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    #: Wide-scan accounting. ``screened`` is how many candidates the stock
    #: layer looked at; ``scored`` is the subset that survived into the
    #: ranking. Equal for a plain watchlist run.
    screened: int = 0
    #: Dropped before the options layer, by reason. Reported rather than
    #: silently discarded — "why is my ticker not in the list" needs an answer.
    filtered: dict[str, str] = field(default_factory=dict)

    @property
    def duration_seconds(self) -> float:
        end = self.finished_at or datetime.now(timezone.utc)
        return (end - self.started_at).total_seconds()

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "trigger": self.trigger,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "duration_seconds": round(self.duration_seconds, 2),
            "scored": self.scored,
            "failed": self.failed,
            "warnings": self.warnings,
            "screened": self.screened,
            "filtered": self.filtered,
        }


class RefreshPipeline:
    """Runs the screener end to end and stores the result."""

    def __init__(self, db: Database, provider: MarketDataProvider,
                 today: date | None = None) -> None:
        self.db = db
        self.provider = provider
        self._today_override = today

    @property
    def today(self) -> date:
        return self._today_override or date.today()

    # -- public API --------------------------------------------------------

    def run(self, config: ScreenerConfig, trigger: str = "manual") -> RunSummary:
        """Screen the candidate universe and persist a new ranking.

        Two stages when the universe is wider than ``scan_depth``:

        1. Fetch prices in bulk and score the *stock layer* for every
           candidate. Cheap — one batched request covers hundreds of names.
        2. Take the best ``scan_depth`` survivors and score them in full,
           options layer included. Expensive — roughly four chain requests
           each — so it is bought only for names that earned it.

        Only stage-2 names enter the ranking. See ``ScreenerConfig.scan_depth``
        for why this is a filter rather than partial scoring.
        """
        started = datetime.now(timezone.utc)
        run_id = self.db.start_run(trigger, config, self.provider.name)
        summary = RunSummary(run_id=run_id, trigger=trigger, status="running",
                             started_at=started)

        candidates = config.candidates()
        summary.screened = len(candidates)
        log.info("Run %s started (trigger=%s, universe=%s, %d candidates)",
                 run_id, trigger, config.universe, len(candidates))

        finalists = self._screen(candidates, config, summary)

        scores: list[TickerScore] = []
        for symbol in finalists:
            try:
                score = self.score_ticker(symbol, config)
            except Exception as exc:
                log.exception("Scoring %s failed", symbol)
                summary.failed[symbol] = f"{type(exc).__name__}: {exc}"
                continue
            if score is None:
                summary.failed[symbol] = (
                    "No price history available — the ticker may be delisted, mistyped, "
                    "or the data source may be throttling."
                )
                continue
            scores.append(score)
            summary.scored.append(symbol)

        if not scores:
            summary.status = "failed"
            summary.finished_at = datetime.now(timezone.utc)
            message = "No ticker could be scored. " + "; ".join(
                f"{s}: {r}" for s, r in list(summary.failed.items())[:5]
            )
            self.db.finish_run(run_id, "failed", message)
            log.error("Run %s failed: %s", run_id, message)
            return summary

        ordered = rank(scores, config)
        self.db.save_ranking(run_id, ordered)

        summary.status = "partial" if summary.failed else "ok"
        summary.finished_at = datetime.now(timezone.utc)

        parts: list[str] = []
        if summary.screened > len(summary.scored):
            parts.append(
                f"Screened {summary.screened} candidates, fully scored "
                f"{len(summary.scored)}."
            )
        if summary.failed:
            parts.append(
                "Skipped: " + "; ".join(f"{s} ({r})" for s, r in summary.failed.items())
            )
        self.db.finish_run(run_id, summary.status, " ".join(parts) or None)

        # Fill in performance for older runs now that fresh prices are stored.
        try:
            updated = compute_forward_returns(self.db)
            if updated:
                summary.warnings.append(f"Computed forward returns for {updated} past entries.")
        except Exception as exc:
            log.warning("Forward-return computation failed: %s", exc)

        log.info("Run %s finished: %s (%d scored, %d failed, %.1fs)",
                 run_id, summary.status, len(summary.scored), len(summary.failed),
                 summary.duration_seconds)
        return summary

    # -- stage 1: the screen -----------------------------------------------

    def _screen(self, candidates: list[str], config: ScreenerConfig,
                summary: RunSummary) -> list[str]:
        """Narrow the candidate pool to the names worth buying options data for.

        Returns the candidates unchanged when the pool already fits inside
        ``scan_depth``: a ten-name watchlist should not pay for a screening
        pass that can only return all ten.
        """
        if len(candidates) <= config.scan_depth:
            return candidates

        log.info("Screening %d candidates down to %d", len(candidates), config.scan_depth)
        prices = fetch_price_history(self.provider, candidates, COLD_FETCH_DAYS)

        provisional: list[tuple[float, float, str]] = []
        for symbol in candidates:
            frame = prices.get(symbol)
            if frame is None or frame.empty:
                summary.filtered[symbol] = "No price history."
                continue

            self.db.upsert_prices(symbol, frame)
            stored = self.db.get_prices(symbol)

            turnover = average_dollar_volume(stored)
            if config.min_scan_dollar_volume > 0 and (
                turnover is None or turnover < config.min_scan_dollar_volume
            ):
                # Not a judgement about the company. Options on a thin name
                # fail the chain hygiene filters anyway, so a chain request
                # here buys a "no contract passed" message at full price.
                summary.filtered[symbol] = (
                    f"Average turnover {_money(turnover)} is below the "
                    f"{_money(config.min_scan_dollar_volume)} scan floor."
                )
                continue

            try:
                score = self._stock_only_score(symbol, stored, config)
            except Exception as exc:
                log.warning("Screening %s failed: %s", symbol, exc)
                summary.filtered[symbol] = f"Screen failed: {type(exc).__name__}: {exc}"
                continue
            if score is None:
                summary.filtered[symbol] = "No directional signal could be computed."
                continue

            provisional.append((score, turnover or 0.0, symbol))

        # Highest provisional score first; turnover breaks ties so the more
        # tradeable of two equal candidates wins, and the symbol breaks the
        # rest so the screen is deterministic across runs.
        provisional.sort(key=lambda row: (-row[0], -row[1], row[2]))
        finalists = [symbol for _, _, symbol in provisional[:config.scan_depth]]

        for _, _, symbol in provisional[config.scan_depth:]:
            summary.filtered[symbol] = "Screened out — outside the top candidates."

        log.info("Screen kept %d of %d candidates", len(finalists), len(candidates))
        return finalists

    def _stock_only_score(self, symbol: str, prices: pd.DataFrame,
                          config: ScreenerConfig) -> float | None:
        """Composite from the stock layer alone, used to rank the screen.

        Deliberately not stored or shown. It is computed on a different weight
        basis from a full score — six parameters scaled to 100% instead of
        eleven — so surfacing it next to real composites would invite exactly
        the comparison it cannot support. It exists only to sort.
        """
        data = TickerData(
            symbol=symbol,
            prices=prices,
            today=self.today,
            earnings_date=None,
            is_etf=False,
            iv_history=[],
        )
        stock_results = score_stock_layer(data, config)
        read = derive_direction(stock_results)
        composed = compose(
            symbol=symbol,
            stock_results=stock_results,
            options_results=[],
            read=read,
            config=config,
            spot=data.spot,
            dollar_volume=average_dollar_volume(prices),
            options=None,
        )
        return composed.composite

    # -- per-ticker --------------------------------------------------------

    def score_ticker(self, symbol: str, config: ScreenerConfig) -> TickerScore | None:
        """Fetch, score and compose one ticker. Returns None if it has no data."""
        prices = self._refresh_prices(symbol)
        if prices.empty:
            return None

        earnings_date, is_etf = self._earnings(symbol)

        data = TickerData(
            symbol=symbol,
            prices=prices,
            today=self.today,
            earnings_date=earnings_date,
            is_etf=is_etf,
            iv_history=self.db.get_iv_history(symbol),
        )

        # Pass 1: the stock layer decides direction and conviction.
        stock_results = score_stock_layer(data, config)
        read = derive_direction(stock_results)

        # Pass 2: the options layer is evaluated for that direction. A neutral
        # read still gets a call-side look so the detail view can show what the
        # chain offers, but the conviction gate keeps it from inflating the score.
        chain_side = "put" if read.direction == "put" else "call"

        # The stock layer is what knows how long its setup takes, and it has
        # already run — so the expected hold can steer contract selection
        # rather than only being reported afterwards.
        assessment = analyse_options(
            provider=self.provider,
            symbol=symbol,
            spot=data.spot,
            config=config,
            direction=chain_side,
            today=self.today,
            min_hold_sessions=_expected_hold_sessions(stock_results),
        )
        data.options = assessment

        if assessment.atm_iv is not None:
            # Stored for IV Rank. Sampled at a fixed reference tenor so the
            # series stays comparable — see options_analysis.IV_REFERENCE_DTE.
            self.db.record_iv(symbol, self.today, assessment.atm_iv,
                              assessment.iv_reference_dte)

        options_results = score_options_layer(data, config)

        return compose(
            symbol=symbol,
            stock_results=stock_results,
            options_results=options_results,
            read=read,
            config=config,
            spot=data.spot,
            dollar_volume=average_dollar_volume(prices),
            options=assessment,
        )

    # -- data acquisition --------------------------------------------------

    def _refresh_prices(self, symbol: str) -> pd.DataFrame:
        """Fetch new bars, merge them into storage, and return the full series.

        Uses a short window once enough history is banked, which keeps a
        ten-ticker refresh to a handful of seconds instead of re-downloading two
        years of data twice a day.
        """
        stored = self.db.get_prices(symbol)
        needs_cold_fetch = len(stored) < WARM_THRESHOLD_BARS
        lookback = COLD_FETCH_DAYS if needs_cold_fetch else WARM_FETCH_DAYS

        fetched = self.provider.price_history(symbol, lookback)
        if fetched.empty:
            if stored.empty:
                log.warning("No price data at all for %s", symbol)
                return stored
            log.warning("Price fetch for %s returned nothing; using %d stored bars.",
                        symbol, len(stored))
            return stored

        self.db.upsert_prices(symbol, fetched)
        return self.db.get_prices(symbol)

    def _earnings(self, symbol: str) -> tuple[date | None, bool]:
        """Next earnings date and fund status, cached to limit network calls."""
        cached = self.db.get_earnings(symbol)
        if cached is not None:
            age = datetime.now(timezone.utc) - _as_utc(cached["fetched_at"])
            still_future = cached["next_date"] is None or cached["next_date"] >= self.today
            if age < EARNINGS_TTL and still_future:
                return cached["next_date"], cached["is_etf"]

        try:
            is_etf = self.provider.is_etf(symbol)
            next_date = None if is_etf else self.provider.next_earnings_date(symbol)
        except Exception as exc:
            log.warning("Earnings lookup for %s failed: %s", symbol, exc)
            if cached is not None:
                return cached["next_date"], cached["is_etf"]
            return None, False

        self.db.save_earnings(symbol, next_date, is_etf)
        return next_date, is_etf


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _money(value: float | None) -> str:
    """Compact dollar figure for operator-facing messages."""
    if value is None:
        return "unknown"
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if abs(value) >= limit:
            return f"${value / limit:.1f}{suffix}"
    return f"${value:.0f}"


def build_provider(name: str, today: date | None = None) -> MarketDataProvider:
    """Construct a provider by name.

    ``synthetic`` exists so the dashboard and the test suite can run with no
    network access at all.
    """
    if name == "yfinance":
        import os

        from screener.providers.yfinance_provider import YFinanceProvider

        # Shared-IP environments such as CI runners get throttled by Yahoo far
        # harder than a home connection, and throttling surfaces as empty
        # frames rather than an error. SCREENER_REQUEST_PAUSE spaces the calls
        # out without needing a code change.
        pause = float(os.environ.get("SCREENER_REQUEST_PAUSE", "0") or 0)
        return YFinanceProvider(request_pause=pause)
    if name == "synthetic":
        from screener.providers.synthetic import SyntheticProvider
        return SyntheticProvider(today=today)
    raise ValueError(f"Unknown provider {name!r}. Expected 'yfinance' or 'synthetic'.")


def _expected_hold_sessions(stock_results) -> int | None:
    """Longest expected hold any stock-layer parameter reports, in sessions.

    Only pattern parameters carry one today — Head & Shoulders, whose research
    puts the cost-surviving edge near a 60-session hold. Taking the longest
    rather than the first means adding a slower pattern later widens the
    requirement instead of being silently ignored by declaration order.
    """
    holds = [
        int(r.detail["expected_hold_sessions"])
        for r in stock_results
        if r.status == "ok" and isinstance(getattr(r, "detail", None), dict)
        and r.detail.get("expected_hold_sessions")
    ]
    return max(holds) if holds else None
