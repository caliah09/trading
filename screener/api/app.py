"""Local FastAPI application.

Binds to 127.0.0.1 only. There is no authentication because there is no remote
access — do not expose this to a network interface without adding some.

The API is deliberately thin: it reads and writes the database and delegates
everything else to the pipeline and scheduler. All scoring logic lives in
``screener.scoring`` so it can be tested without an HTTP layer.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from screener.config import (
    PRESET_DESCRIPTIONS,
    WEIGHT_PRESETS,
    ConfigError,
    ScreenerConfig,
    normalise_watchlist,
)
from screener.db import DEFAULT_DB_PATH, Database
from screener.journal import DEFAULT_HORIZON, calibrate
from screener.param_specs import ALL_SPECS
from screener.performance import HORIZONS, summarise_run_performance
from screener.portfolio import DEFAULT_WINDOW, analyse_concentration
from screener.scheduler import RefreshBusy, RefreshService
from screener.tickets import build_ticket

log = logging.getLogger(__name__)

WEB_DIR = Path(__file__).parent / "web"

DISCLAIMER = (
    "This is a personal research tool, not financial advice. Nothing here is a "
    "recommendation to buy or sell any security. Scores reflect the parameters and "
    "weights you configured, computed from delayed public data that may be "
    "incomplete or wrong. Options carry substantial risk of loss. Verify everything "
    "independently before risking money."
)


def create_app(
    db_path: str | Path = DEFAULT_DB_PATH,
    provider_name: str = "yfinance",
    start_scheduler: bool = True,
    catch_up: bool = True,
) -> FastAPI:
    """Build the application.

    Args:
        provider_name: ``yfinance`` for live data, ``synthetic`` for offline use.
        start_scheduler: disabled by tests so no background timer runs.
        catch_up: run a refresh at startup if a scheduled one was missed.
    """
    db = Database(db_path)
    service = RefreshService(db, provider_name=provider_name, catch_up=catch_up)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_scheduler:
            service.start()
        try:
            yield
        finally:
            if start_scheduler:
                service.shutdown()

    app = FastAPI(
        title="Personal Stock/Options Screener",
        description=DISCLAIMER,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.db = db
    app.state.service = service

    # ---------------------------------------------------------------- meta

    @app.get("/api/meta")
    def meta() -> dict:
        """Disclaimer, parameter registry, and horizons — everything the UI needs to render."""
        return {
            "disclaimer": DISCLAIMER,
            "horizons": list(HORIZONS),
            "parameters": [
                {
                    "key": s.key,
                    "label": s.label,
                    "layer": s.layer,
                    "summary": s.summary,
                    "default_weight": s.default_weight,
                    "posture_aware": s.posture_aware,
                    "thresholds": [asdict(t) for t in s.thresholds],
                }
                for s in ALL_SPECS
            ],
        }

    @app.get("/api/status")
    def status() -> dict:
        """Scheduler state, last refresh, and next scheduled refresh."""
        return service.status().to_dict()

    # ------------------------------------------------------------ refresh

    @app.post("/api/refresh")
    async def refresh(trigger: str = Query("manual", pattern="^(manual|cli)$")) -> dict:
        """Run the ranking pipeline now.

        Returns 409 rather than queueing when a refresh is already running, so a
        double-click cannot start two overlapping runs.
        """
        try:
            summary = await run_in_threadpool(service.refresh, trigger, False)
        except RefreshBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except Exception as exc:
            log.exception("Manual refresh failed")
            raise HTTPException(status_code=500, detail=f"Refresh failed: {exc}") from exc

        if summary.status == "failed":
            # The run completed but produced nothing usable; surface why.
            raise HTTPException(
                status_code=502,
                detail={
                    "message": "The refresh completed but no ticker could be scored.",
                    "summary": summary.to_dict(),
                },
            )
        return summary.to_dict()

    # ----------------------------------------------------------- rankings

    @app.get("/api/rankings/latest")
    def latest_ranking() -> dict:
        run = db.latest_run(only_successful=True)
        if run is None:
            return {
                "available": False,
                "message": "No ranking has been computed yet. Use Refresh Now to build one.",
                "run": None,
                "entries": [],
            }
        return _ranking_payload(db, run)

    @app.get("/api/rankings/{run_id}")
    def ranking(run_id: int) -> dict:
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
        return _ranking_payload(db, run)

    # NOTE: this must stay registered BEFORE /api/rankings/{run_id}/{symbol}.
    # FastAPI matches routes in declaration order, so the ticker route would
    # otherwise swallow "concentration" as a symbol name and return 404.
    @app.get("/api/rankings/{run_id}/concentration")
    def concentration(run_id: int, top_n: int | None = Query(None, ge=1, le=50)) -> dict:
        """How many independent bets the top candidates actually represent.

        The ranking scores each ticker in isolation, so it cannot see that its
        leaders move together. This is the portfolio-level view that gap leaves
        out — see ``screener.portfolio``.
        """
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No run with id {run_id}")

        entries = db.get_ranking(run_id)
        if not entries:
            return {"available": False, "message": "This run stored no ranking."}

        stored = db.get_config_snapshot(run["fingerprint"]) or {}
        limit = top_n or int(stored.get("top_n", db.load_active_config().top_n))
        candidates = [e for e in entries if e["rank"] <= limit]
        symbols = [e["symbol"] for e in candidates]
        directions = {e["symbol"]: e["direction"] for e in candidates}

        prices = {s: db.get_prices(s, limit=DEFAULT_WINDOW * 3) for s in symbols}
        report = analyse_concentration(prices, symbols, directions=directions)
        payload = report.to_dict()
        payload["available"] = True
        payload["run_id"] = run_id
        return payload

    @app.get("/api/rankings/{run_id}/{symbol}")
    def ticker_detail(run_id: int, symbol: str) -> dict:
        """Full parameter-by-parameter breakdown for one ticker in one run."""
        entries = db.get_ranking(run_id)
        wanted = symbol.strip().upper()
        for entry in entries:
            if entry["symbol"] == wanted:
                run = db.get_run(run_id) or {}
                return {
                    "run": _run_payload(db, run),
                    "entry": entry,
                    "disclaimer": DISCLAIMER,
                }
        raise HTTPException(status_code=404, detail=f"{wanted} is not in run {run_id}")

    # ------------------------------------------------------------ history

    @app.get("/api/runs")
    def runs(limit: int = Query(50, ge=1, le=400)) -> dict:
        config = db.load_active_config()
        items = db.list_runs(limit)
        for item in items:
            item["is_current_config"] = item["fingerprint"] == config.fingerprint
        return {
            "runs": items,
            "current_fingerprint": config.fingerprint,
        }

    @app.get("/api/runs/{run_id}/performance")
    def performance(run_id: int) -> dict:
        run = db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail=f"No run with id {run_id}")
        config = db.load_active_config()
        stored = db.get_config_snapshot(run["fingerprint"]) or {}
        top_n = int(stored.get("top_n", config.top_n))
        return summarise_run_performance(db, run_id, top_n)

    @app.get("/api/config/{fingerprint}")
    def config_snapshot(fingerprint: str) -> dict:
        payload = db.get_config_snapshot(fingerprint)
        if payload is None:
            raise HTTPException(status_code=404, detail=f"No stored config {fingerprint}")
        return {"fingerprint": fingerprint, "config": payload}

    # ------------------------------------------------------------- config

    @app.get("/api/config")
    def get_config() -> dict:
        config = db.load_active_config()
        return {"config": config.to_dict(), "fingerprint": config.fingerprint}

    @app.put("/api/config")
    def put_config(payload: dict[str, Any] = Body(...)) -> dict:
        """Replace the active configuration.

        Validation happens in ``ScreenerConfig``; a rejected config leaves the
        stored one untouched so the dashboard can never be left unusable.
        """
        try:
            if "watchlist" in payload:
                payload["watchlist"] = normalise_watchlist(list(payload["watchlist"]))
            config = ScreenerConfig.from_dict(payload)
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail=f"Malformed configuration: {exc}") from exc

        db.save_active_config(config)
        return {
            "config": config.to_dict(),
            "fingerprint": config.fingerprint,
            "note": ("Saved. The new weights apply to the next refresh; rankings already "
                     "stored keep the configuration that produced them."),
        }

    @app.get("/api/presets")
    def presets() -> dict:
        """Named weight sets, with the one currently in use marked."""
        config = db.load_active_config()
        return {
            "current": config.matching_preset(),
            "presets": [
                {
                    "name": name,
                    "description": PRESET_DESCRIPTIONS.get(name, ""),
                    "weights": weights,
                }
                for name, weights in WEIGHT_PRESETS.items()
            ],
        }

    @app.post("/api/presets/{name}")
    def apply_preset(name: str) -> dict:
        """Replace the active weights with a preset, leaving everything else alone."""
        try:
            updated = db.load_active_config().apply_preset(name)
        except ConfigError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        db.save_active_config(updated)
        return {
            "config": updated.to_dict(),
            "fingerprint": updated.fingerprint,
            "preset": name,
            "note": f"Weights set to '{name}'. Refresh to re-rank under them.",
        }

    # ------------------------------------------------------------ tickets

    @app.get("/api/rankings/{run_id}/{symbol}/ticket")
    def ticket(run_id: int, symbol: str,
               instrument: str = Query("auto",
                                       pattern="^(auto|option|equity|leveraged_etf)$")) -> dict:
        """A reviewable order ticket for one candidate. Places nothing."""
        wanted = symbol.strip().upper()
        entry = next((e for e in db.get_ranking(run_id) if e["symbol"] == wanted), None)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"{wanted} is not in run {run_id}")

        config = db.load_active_config()
        built = build_ticket(entry, instrument=instrument,
                             risk_free_rate=config.risk_free_rate, run_id=run_id)
        return {
            "ticket": built.to_dict(),
            "text": built.to_text(),
            "disclaimer": DISCLAIMER,
        }

    # ----------------------------------------------------------- decisions

    @app.get("/api/decisions")
    def list_decisions(run_id: int | None = None,
                       limit: int = Query(500, ge=1, le=5000)) -> dict:
        return {"decisions": db.get_decisions(run_id=run_id, limit=limit)}

    @app.post("/api/decisions")
    def record_decision(payload: dict[str, Any] = Body(...)) -> dict:
        """Record what you did about a candidate: taken, passed, or watching.

        This is the only input the screener cannot generate for itself, and it
        is what lets the weights be measured rather than assumed.
        """
        try:
            run_id = int(payload["run_id"])
            symbol = str(payload["symbol"]).strip().upper()
            decision = str(payload["decision"])
        except (KeyError, TypeError, ValueError) as exc:
            raise HTTPException(
                status_code=400,
                detail=f"run_id, symbol and decision are required: {exc}",
            ) from exc

        if db.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail=f"No run with id {run_id}")

        entry = next((e for e in db.get_ranking(run_id) if e["symbol"] == symbol), None)
        if entry is None:
            raise HTTPException(status_code=404, detail=f"{symbol} is not in run {run_id}")

        try:
            db.record_decision(
                run_id, symbol, decision,
                note=payload.get("note"),
                instrument=payload.get("instrument"),
                contract=payload.get("contract"),
                entry_price=payload.get("entry_price"),
                quantity=payload.get("quantity"),
                composite=entry["composite"],
                direction=entry["direction"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {"ok": True, "run_id": run_id, "symbol": symbol, "decision": decision}

    @app.delete("/api/decisions/{run_id}/{symbol}")
    def delete_decision(run_id: int, symbol: str) -> dict:
        removed = db.delete_decision(run_id, symbol.strip().upper())
        if not removed:
            raise HTTPException(status_code=404, detail="No such decision")
        return {"ok": True}

    @app.get("/api/calibration")
    def calibration(horizon_days: int = Query(DEFAULT_HORIZON, ge=1, le=250)) -> dict:
        """Measure the screener against the decisions recorded against it."""
        return calibrate(db, horizon_days=horizon_days).to_dict()

    # ---------------------------------------------------------------- web

    if WEB_DIR.exists():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/")
        def index() -> FileResponse:
            return FileResponse(WEB_DIR / "index.html")

    return app


# --------------------------------------------------------------------------


def _run_payload(db: Database, run: dict) -> dict:
    if not run:
        return {}
    current = db.load_active_config()
    payload = dict(run)
    payload["is_current_config"] = run.get("fingerprint") == current.fingerprint
    return payload


def _ranking_payload(db: Database, run: dict) -> dict:
    entries = db.get_ranking(run["id"])
    stored = db.get_config_snapshot(run["fingerprint"]) or {}
    top_n = int(stored.get("top_n", db.load_active_config().top_n))
    return {
        "available": bool(entries),
        "run": _run_payload(db, run),
        "top_n": top_n,
        "entries": entries,
        "disclaimer": DISCLAIMER,
    }
