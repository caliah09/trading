#!/usr/bin/env python3
"""Review order tickets and, if you confirm each one, place them.

    # See what the latest ranking proposes — places nothing
    python scripts/execute.py --top 5

    # Same, against a paper account, still placing nothing
    python scripts/execute.py --top 5 --broker alpaca

    # Actually place, one confirmation per order
    python scripts/execute.py --top 5 --broker alpaca --execute --qty 1

Safety, by construction:

* **Dry run is the default.** Without ``--execute`` nothing is sent anywhere.
* **Paper is the default.** Live money needs ``--live`` *and* typing LIVE.
* **No unattended live trading.** ``--live`` with ``--yes`` is refused.
* **Credentials come only from the environment.** Never an argument, never a
  file in this repo, never written to the log.
* **You set the size.** The screener does not size positions, so ``--qty`` is
  required before anything can be placed.

Every run appends to a log file so there is a record of what was sent.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from screener.brokers.base import BrokerError, OrderRequest  # noqa: E402
from screener.brokers.dryrun import DryRunBroker  # noqa: E402
from screener.db import DEFAULT_DB_PATH, Database  # noqa: E402
from screener.tickets import build_ticket  # noqa: E402

log = logging.getLogger("execute")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="execute",
        description="Review and optionally place order tickets from a ranking.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--run-id", type=int,
                        help="Ranking run to build tickets from (default: latest).")
    parser.add_argument("--top", type=int, default=5,
                        help="How many ranked candidates to turn into tickets.")
    parser.add_argument("--symbol", action="append", dest="symbols",
                        help="Limit to specific tickers. Repeatable.")
    parser.add_argument("--instrument", default="auto",
                        choices=("auto", "option", "equity", "leveraged_etf"))
    parser.add_argument("--broker", default="dryrun", choices=("dryrun", "alpaca"))
    parser.add_argument("--qty", type=float,
                        help="Contracts or shares per order. Required with --execute.")
    parser.add_argument("--execute", action="store_true",
                        help="Actually place orders. Without this, nothing is sent.")
    parser.add_argument("--live", action="store_true",
                        help="Use the live endpoint instead of paper. Requires typing LIVE.")
    parser.add_argument("--yes", action="store_true",
                        help="Skip per-order prompts. Paper only; refused with --live.")
    parser.add_argument("--json", action="store_true", help="Emit tickets as JSON and exit.")
    parser.add_argument("--log-file", type=Path, default=Path("data/execution.log"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_logging(args.log_file)

    tickets = _load_tickets(args)
    if not tickets:
        print("No tickets to review.", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps([t.to_dict() for t in tickets], indent=2))
        return 0

    for ticket in tickets:
        print()
        print(ticket.to_text())
    print()

    if not args.execute:
        print("=" * 60)
        print("DRY RUN — nothing was placed. Add --execute to place these.")
        print("=" * 60)
        return 0

    if not _preflight(args):
        return 2

    broker = _build_broker(args)
    return _place(tickets, broker, args)


# --------------------------------------------------------------------------
# Safety gates
# --------------------------------------------------------------------------


def _preflight(args) -> bool:
    """Every check that must pass before a single order can be sent."""
    if args.qty is None or args.qty <= 0:
        print("ERROR: --qty is required with --execute. The screener does not size "
              "positions, so you have to state the size yourself.", file=sys.stderr)
        return False

    if args.live and args.yes:
        print("ERROR: --live cannot be combined with --yes. Unattended live trading is "
              "not supported by this tool.", file=sys.stderr)
        return False

    if args.live:
        print()
        print("!" * 60)
        print("  LIVE TRADING — these orders risk real money.")
        print("  Every order will still be confirmed individually.")
        print("!" * 60)
        try:
            typed = input('  Type LIVE to continue, anything else to abort: ').strip()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return False
        if typed != "LIVE":
            print("Aborted.")
            return False
    return True


def _build_broker(args):
    if args.broker == "dryrun":
        return DryRunBroker()

    from screener.brokers.alpaca import AlpacaBroker

    broker = AlpacaBroker(paper=not args.live)
    try:
        account = broker.account()
    except BrokerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    print("-" * 60)
    print(f"  Broker:  {broker.name}  ({'PAPER' if broker.is_paper else 'LIVE'})")
    print(f"  Account: {account.get('account_number')}  status={account.get('status')}")
    print(f"  Equity:  {account.get('equity')}   Buying power: {account.get('buying_power')}")
    print("-" * 60)
    return broker


def _place(tickets, broker, args) -> int:
    placed = skipped = failed = 0

    for ticket in tickets:
        print()
        print(f"--- {ticket.tradable_symbol} ---")
        print(f"    {ticket.side} {args.qty:g} @ "
              + (f"limit {ticket.limit_price:,.2f}" if ticket.limit_price else "market"))
        for warning in ticket.warnings:
            print(f"    ! {warning}")

        if not args.yes:
            try:
                answer = input("    Place this order? [y/N] ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n    Aborted.")
                break
            if answer != "y":
                print("    Skipped.")
                skipped += 1
                continue

        request = OrderRequest(
            symbol=ticket.tradable_symbol,
            side=ticket.side,
            quantity=args.qty,
            order_type="limit" if ticket.limit_price else "market",
            limit_price=ticket.limit_price,
            asset_class="option" if ticket.instrument == "option" else "equity",
        )
        try:
            result = broker.submit(request)
        except (BrokerError, ValueError) as exc:
            print(f"    FAILED: {exc}")
            log.error("Order failed for %s: %s", ticket.tradable_symbol, exc)
            failed += 1
            continue

        print(f"    {result.status}: {result.message}")
        log.info("Order result for %s: %s", ticket.tradable_symbol,
                 json.dumps(result.to_dict()))
        placed += 1 if result.placed else 0
        skipped += 0 if result.placed else 1

    print()
    print(f"Placed {placed}, skipped {skipped}, failed {failed}.")
    print(f"Log: {args.log_file}")
    return 0 if failed == 0 else 1


# --------------------------------------------------------------------------


def _load_tickets(args) -> list:
    db = Database(args.db)
    run = db.get_run(args.run_id) if args.run_id else db.latest_run(only_successful=True)
    if run is None:
        print("No ranking found. Run `python -m screener refresh` first.", file=sys.stderr)
        return []

    entries = db.get_ranking(run["id"])
    if args.symbols:
        wanted = {s.strip().upper() for s in args.symbols}
        entries = [e for e in entries if e["symbol"] in wanted]
    else:
        entries = [e for e in entries if e["rank"] <= args.top]

    config = db.load_active_config()
    print(f"Run {run['id']} — {run['started_at']} — {len(entries)} ticket(s)")
    return [
        build_ticket(entry, instrument=args.instrument,
                     risk_free_rate=config.risk_free_rate, run_id=run["id"])
        for entry in entries
    ]


def _configure_logging(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.FileHandler(path), logging.StreamHandler(sys.stderr)],
    )
    logging.getLogger().handlers[1].setLevel(logging.WARNING)  # keep stderr quiet
    log.info("=== execute.py started at %s ===",
             datetime.now(timezone.utc).isoformat(timespec="seconds"))


if __name__ == "__main__":
    raise SystemExit(main())
