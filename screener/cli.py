"""Command line entry points.

    python -m screener serve      # start the dashboard (schedules its own refreshes)
    python -m screener refresh    # run the pipeline once and exit
    python -m screener status     # print the last and next refresh

``serve`` is the normal way to use this tool. ``refresh`` exists to seed the
database before the first launch and for scripting; it is not required for
scheduling, which the running app handles itself.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from screener.config import ScreenerConfig
from screener.db import DEFAULT_DB_PATH, Database
from screener.pipeline import RefreshPipeline, build_provider
from screener.scheduler import next_scheduled, previous_scheduled

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    # yfinance is chatty about individual failures we already handle.
    logging.getLogger("yfinance").setLevel(logging.ERROR)
    logging.getLogger("peewee").setLevel(logging.ERROR)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="screener",
        description="Local-only personal stock/options screener.",
    )
    parser.add_argument("--db", default=str(DEFAULT_DB_PATH), type=Path,
                        help=f"SQLite database path (default: {DEFAULT_DB_PATH})")
    parser.add_argument("--provider", default="yfinance", choices=("yfinance", "synthetic"),
                        help="Data source. 'synthetic' generates deterministic fake data "
                             "for offline use and demos.")
    parser.add_argument("-v", "--verbose", action="store_true", help="Debug logging")

    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Start the dashboard and its internal scheduler")
    serve.add_argument("--host", default=DEFAULT_HOST,
                       help="Bind address. Defaults to loopback; this app has no "
                            "authentication, so do not bind it to a public interface.")
    serve.add_argument("--port", type=int, default=DEFAULT_PORT)
    serve.add_argument("--no-schedule", action="store_true",
                       help="Serve the dashboard without starting the internal scheduler")
    serve.add_argument("--no-catch-up", action="store_true",
                       help="Do not run a refresh at startup even if one was missed")

    refresh = sub.add_parser("refresh", help="Run the ranking pipeline once and exit")
    refresh.add_argument("--json", action="store_true", help="Print the run summary as JSON")

    sub.add_parser("status", help="Show the last and next scheduled refresh")

    doctor = sub.add_parser(
        "doctor",
        help="Check the data source end to end on one ticker and report what works",
    )
    doctor.add_argument("--symbol", default="AAPL",
                        help="Ticker to probe (default: AAPL)")

    watch = sub.add_parser("watchlist", help="Show or replace the watchlist")
    watch.add_argument("symbols", nargs="*", help="Replace the watchlist with these symbols")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.verbose)
    db = Database(args.db)

    if args.command == "serve":
        return _serve(args, db)
    if args.command == "refresh":
        return _refresh(args, db)
    if args.command == "status":
        return _status(db)
    if args.command == "doctor":
        return _doctor(args, db)
    if args.command == "watchlist":
        return _watchlist(args, db)
    return 1


def _serve(args, db: Database) -> int:
    import uvicorn

    from screener.api.app import create_app

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding to {args.host}. This app has no authentication — "
              "anyone who can reach that address can read and change your settings.",
              file=sys.stderr)

    app = create_app(
        db_path=args.db,
        provider_name=args.provider,
        start_scheduler=not args.no_schedule,
        catch_up=not args.no_catch_up,
    )
    print(f"\n  Screener dashboard: http://{args.host}:{args.port}\n"
          f"  Data source: {args.provider}   Database: {args.db}\n"
          f"  Automatic refreshes run at market open and close *while this process is running*.\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def _refresh(args, db: Database) -> int:
    config = db.load_active_config()
    provider = build_provider(args.provider)
    summary = RefreshPipeline(db, provider).run(config, trigger="cli")

    if args.json:
        print(json.dumps(summary.to_dict(), indent=2))
    else:
        print(f"\nRun {summary.run_id}: {summary.status} in {summary.duration_seconds:.1f}s")
        print(f"  Scored: {len(summary.scored)}  Failed: {len(summary.failed)}")
        for symbol, reason in summary.failed.items():
            print(f"    ! {symbol}: {reason}")
        entries = db.get_ranking(summary.run_id)
        if entries:
            print(f"\n  {'#':>2}  {'Ticker':<8}{'Score':>7}  {'Lean':<14}{'Data':>6}")
            for entry in entries[: config.top_n]:
                print(f"  {entry['rank']:>2}  {entry['symbol']:<8}"
                      f"{entry['composite']:>7.1f}  {entry['direction_label']:<14}"
                      f"{entry['completeness']:>6}")
        print()
    return 0 if summary.status in ("ok", "partial") else 1


def _status(db: Database) -> int:
    from datetime import datetime

    from screener.scheduler import MARKET_TZ

    now = datetime.now(MARKET_TZ)
    last = db.latest_run(only_successful=False)
    upcoming = next_scheduled(now)
    previous = previous_scheduled(now)

    print(f"\n  Now:      {now:%Y-%m-%d %H:%M %Z}")
    if last:
        print(f"  Last run: {last['started_at']}  ({last['status']}, {last['trigger']})")
    else:
        print("  Last run: never")
    if previous:
        print(f"  Previous scheduled slot: {previous[0]:%Y-%m-%d %H:%M %Z} ({previous[1]})")
    if upcoming:
        print(f"  Next scheduled slot:     {upcoming[0]:%Y-%m-%d %H:%M %Z} ({upcoming[1]})")
    print("\n  Note: scheduled refreshes only fire while `screener serve` is running.\n")
    return 0


def _doctor(args, db: Database) -> int:
    """Probe the data source step by step and say exactly what works.

    Exists because the failure modes of an unofficial scraper are many and
    look alike from the outside: a blocked host, a rate limit, a delisted
    ticker and a schema change all surface as "no data". This separates them.
    """
    from datetime import date

    symbol = args.symbol.strip().upper()
    print(f"\n  Probing {args.provider} with {symbol}\n  " + "-" * 52)
    failures = 0

    def check(label: str, fn):
        nonlocal failures
        try:
            ok, detail = fn()
        except Exception as exc:
            failures += 1
            print(f"  [FAIL] {label:<26} {type(exc).__name__}: {str(exc)[:90]}")
            return None
        mark = "ok  " if ok else "WARN"
        if not ok:
            failures += 1
        print(f"  [{mark}] {label:<26} {detail}")
        return detail

    if args.provider == "yfinance":
        def _version():
            import yfinance
            return True, f"yfinance {yfinance.__version__}"
        check("library", _version)

    provider = build_provider(args.provider)

    prices = None

    def _history():
        nonlocal prices
        prices = provider.price_history(symbol, 400)
        if prices.empty:
            return False, "no rows — ticker unknown, delisted, or the source is throttling"
        last = prices.index[-1].date()
        stale = (date.today() - last).days
        return True, (f"{len(prices)} bars, latest {last}"
                      + (f" ({stale}d old)" if stale > 4 else ""))
    check("price history", _history)

    expiries = check("option expiries", lambda: (
        (True, f"{len(provider.option_expiries(symbol))} listed")
        if provider.option_expiries(symbol) else (False, "none listed")
    ))

    def _chain():
        listed = provider.option_expiries(symbol)
        if not listed:
            return False, "skipped — no expiries"
        target = min(listed, key=lambda e: abs((e - date.today()).days - 30))
        chain = provider.option_chain(symbol, target)
        if chain.is_empty:
            return False, f"{target} returned nothing"
        usable = chain.calls["implied_volatility"].notna().sum()
        return True, (f"{target}: {len(chain.calls)} calls / {len(chain.puts)} puts, "
                      f"{usable} with usable IV")
    if expiries:
        check("option chain", _chain)

    # Once price history has failed, the source is almost certainly unreachable.
    # `is_etf` and `next_earnings_date` degrade to False/None by design, which is
    # right for the pipeline but would report a default here as if it were a
    # measurement — "equity" when nothing was actually checked.
    reachable = prices is not None and not prices.empty

    if reachable:
        check("fund check", lambda: (True, "ETF" if provider.is_etf(symbol) else "equity"))
        check("earnings date", lambda: (
            (True, str(provider.next_earnings_date(symbol)))
            if provider.next_earnings_date(symbol)
            else (False, "none published (normal for funds)")
        ))
    else:
        print(f"  [skip] {'fund check':<26} data source unreachable — not checked")
        print(f"  [skip] {'earnings date':<26} data source unreachable — not checked")

    def _score():
        config = db.load_active_config()
        pipeline = RefreshPipeline(db, provider)
        score = pipeline.score_ticker(symbol, config)
        if score is None:
            return False, "could not be scored"
        counted = len(score.counted)
        total = len(score.considered)
        return counted > 0, (f"{score.composite:.1f}/100, {score.direction_label}, "
                             f"{counted}/{total} parameters")
    check("end-to-end score", _score)

    print("  " + "-" * 52)
    if failures == 0:
        print(f"  All checks passed. `python -m screener refresh` should work.\n")
    else:
        print(f"  {failures} check(s) failed or warned.")
        print("  A 403 or connection error on every check usually means the data")
        print("  source is blocked by a network policy rather than broken.\n")
    return 0 if failures == 0 else 1


def _watchlist(args, db: Database) -> int:
    config = db.load_active_config()
    if args.symbols:
        from dataclasses import replace

        updated = replace(config, watchlist=list(args.symbols))
        db.save_active_config(updated)
        print(f"Watchlist set to: {', '.join(updated.watchlist)}")
    else:
        print(f"Watchlist ({len(config.watchlist)}): {', '.join(config.watchlist)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
