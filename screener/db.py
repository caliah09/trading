"""SQLite persistence.

Design notes worth knowing:

* **A connection per unit of work.** The dashboard, the in-process scheduler and
  manual refreshes all touch the database from different threads. SQLite
  connections are not safely shared across threads, so every operation opens
  and closes its own. WAL mode keeps concurrent readers from blocking on the
  writer.
* **Configs are content-addressed.** Each run records the fingerprint of the
  config that produced it, and the config JSON is stored once per distinct
  fingerprint. History therefore always shows what you actually saw that day,
  even after you change your weights.
* **Chains are not stored wholesale.** A full options chain per ticker per run
  is tens of thousands of rows of mostly-noise. What gets persisted is the
  derived measurements the scoring engine used, plus the single suggested
  contract, which is what the detail and history views need.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator

import pandas as pd

from screener.config import ScreenerConfig

log = logging.getLogger(__name__)

SCHEMA_VERSION = 2

DEFAULT_DB_PATH = Path("data/screener.sqlite")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS prices (
    symbol      TEXT NOT NULL,
    date        TEXT NOT NULL,          -- ISO date
    open        REAL,
    high        REAL,
    low         REAL,
    close       REAL NOT NULL,
    volume      REAL,
    PRIMARY KEY (symbol, date)
);
CREATE INDEX IF NOT EXISTS idx_prices_symbol_date ON prices(symbol, date DESC);

-- One at-the-money implied volatility observation per symbol per day, always
-- sampled at the same reference tenor so the series is comparable over time.
CREATE TABLE IF NOT EXISTS iv_history (
    symbol         TEXT NOT NULL,
    date           TEXT NOT NULL,
    atm_iv         REAL NOT NULL,       -- decimal, e.g. 0.284
    reference_dte  INTEGER,
    PRIMARY KEY (symbol, date)
);

CREATE TABLE IF NOT EXISTS earnings (
    symbol       TEXT PRIMARY KEY,
    next_date    TEXT,                  -- NULL is meaningful: fund, or unknown
    is_etf       INTEGER NOT NULL DEFAULT 0,
    fetched_at   TEXT NOT NULL
);

-- Content-addressed config snapshots, so historical rankings stay interpretable.
CREATE TABLE IF NOT EXISTS configs (
    fingerprint  TEXT PRIMARY KEY,
    payload      TEXT NOT NULL,
    first_seen   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    trigger       TEXT NOT NULL,        -- 'market_open' | 'market_close' | 'manual' | 'startup_catchup' | 'cli'
    status        TEXT NOT NULL,        -- 'running' | 'ok' | 'partial' | 'failed'
    fingerprint   TEXT NOT NULL,
    provider      TEXT NOT NULL,
    posture       TEXT NOT NULL,
    message       TEXT,
    FOREIGN KEY (fingerprint) REFERENCES configs(fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC);

CREATE TABLE IF NOT EXISTS ranking_entries (
    run_id            INTEGER NOT NULL,
    symbol            TEXT NOT NULL,
    rank              INTEGER NOT NULL,
    composite         REAL NOT NULL,
    direction         TEXT NOT NULL,
    direction_label   TEXT NOT NULL,
    conviction        REAL NOT NULL,
    net_signal        REAL NOT NULL,
    spot              REAL,
    dollar_volume     REAL,
    completeness      TEXT,
    contract_json     TEXT,
    options_notes     TEXT,
    tie_break_note    TEXT,
    PRIMARY KEY (run_id, symbol),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_entries_run_rank ON ranking_entries(run_id, rank);

CREATE TABLE IF NOT EXISTS parameter_results (
    run_id            INTEGER NOT NULL,
    symbol            TEXT NOT NULL,
    param_key         TEXT NOT NULL,
    label             TEXT NOT NULL,
    layer             TEXT NOT NULL,
    status            TEXT NOT NULL,
    weight            REAL NOT NULL,
    effective_weight  REAL NOT NULL,
    signal            REAL,
    raw_value         REAL,
    display_value     TEXT,
    contribution      REAL NOT NULL,
    explanation       TEXT,
    detail_json       TEXT,
    PRIMARY KEY (run_id, symbol, param_key),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);

-- Forward performance of each ranking, filled in on later runs.
CREATE TABLE IF NOT EXISTS forward_returns (
    run_id            INTEGER NOT NULL,
    symbol            TEXT NOT NULL,
    horizon_days      INTEGER NOT NULL,
    start_price       REAL NOT NULL,
    end_price         REAL NOT NULL,
    return_pct        REAL NOT NULL,
    directional_pct   REAL NOT NULL,    -- sign-adjusted: a put lean that fell is positive
    computed_at       TEXT NOT NULL,
    PRIMARY KEY (run_id, symbol, horizon_days),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS app_settings (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);

-- What you actually did about each candidate. This is the one thing the
-- screener cannot derive for itself: it can compute what every ticker did
-- next, but not which ones you chose to act on. Without it every weight in
-- the config is an opinion; with it they can be measured.
CREATE TABLE IF NOT EXISTS decisions (
    run_id        INTEGER NOT NULL,
    symbol        TEXT NOT NULL,
    decision      TEXT NOT NULL,     -- 'taken' | 'passed' | 'watching'
    note          TEXT,
    instrument    TEXT,              -- 'option' | 'equity' | 'leveraged_etf'
    contract      TEXT,              -- contract identifier when instrument='option'
    entry_price   REAL,
    quantity      REAL,
    -- Snapshot of the score as it stood, so analysis survives later config changes.
    composite     REAL,
    direction     TEXT,
    decided_at    TEXT NOT NULL,
    PRIMARY KEY (run_id, symbol),
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_decisions_decided ON decisions(decided_at DESC);
"""


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Database:
    """Thin, explicit data-access layer. No ORM, no lazy loading."""

    def __init__(self, path: Path | str = DEFAULT_DB_PATH) -> None:
        self.path = Path(path)
        if self.path.parent and str(self.path.parent) not in ("", "."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        with self.connect() as conn:
            conn.executescript(_SCHEMA)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version < SCHEMA_VERSION:
                # Every schema change so far has been purely additive, and the
                # CREATE TABLE IF NOT EXISTS statements above have just applied
                # it. Nothing is dropped or rewritten, so existing data is safe.
                log.info("Upgraded database schema from version %s to %s",
                         version, SCHEMA_VERSION)
                conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            elif version > SCHEMA_VERSION:
                log.warning(
                    "Database at %s was written by a newer version of this app "
                    "(schema %s, this build expects %s). Back it up before continuing.",
                    self.path, version, SCHEMA_VERSION,
                )

    # -- prices ------------------------------------------------------------

    def upsert_prices(self, symbol: str, frame: pd.DataFrame) -> int:
        """Insert or replace daily bars. Returns the number of rows written."""
        if frame.empty:
            return 0
        rows = [
            (
                symbol,
                index.date().isoformat() if hasattr(index, "date") else str(index),
                _none_if_nan(row.get("open")),
                _none_if_nan(row.get("high")),
                _none_if_nan(row.get("low")),
                float(row["close"]),
                _none_if_nan(row.get("volume")),
            )
            for index, row in frame.iterrows()
            if pd.notna(row.get("close"))
        ]
        if not rows:
            return 0
        with self.connect() as conn:
            conn.executemany(
                "INSERT INTO prices (symbol, date, open, high, low, close, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(symbol, date) DO UPDATE SET "
                "open=excluded.open, high=excluded.high, low=excluded.low, "
                "close=excluded.close, volume=excluded.volume",
                rows,
            )
        return len(rows)

    def get_prices(self, symbol: str, limit: int | None = None) -> pd.DataFrame:
        """Daily bars for ``symbol``, oldest first, indexed by date."""
        query = "SELECT date, open, high, low, close, volume FROM prices WHERE symbol = ? ORDER BY date"
        params: tuple = (symbol,)
        if limit:
            # Take the most recent `limit` rows, then restore ascending order.
            query = (
                "SELECT * FROM (SELECT date, open, high, low, close, volume FROM prices "
                "WHERE symbol = ? ORDER BY date DESC LIMIT ?) ORDER BY date"
            )
            params = (symbol, limit)
        with self.connect() as conn:
            rows = conn.execute(query, params).fetchall()
        if not rows:
            from screener.providers.base import empty_price_frame
            return empty_price_frame()
        frame = pd.DataFrame([dict(r) for r in rows])
        frame.index = pd.DatetimeIndex(pd.to_datetime(frame.pop("date")), name="date")
        return frame

    def price_on_or_after(self, symbol: str, day: date) -> tuple[date, float] | None:
        """First available close on or after ``day`` — used for forward returns."""
        with self.connect() as conn:
            row = conn.execute(
                "SELECT date, close FROM prices WHERE symbol = ? AND date >= ? "
                "ORDER BY date LIMIT 1",
                (symbol, day.isoformat()),
            ).fetchone()
        if row is None:
            return None
        return date.fromisoformat(row["date"]), float(row["close"])

    # -- implied volatility history ---------------------------------------

    def record_iv(self, symbol: str, day: date, atm_iv: float, reference_dte: int | None) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO iv_history (symbol, date, atm_iv, reference_dte) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(symbol, date) DO UPDATE SET "
                "atm_iv=excluded.atm_iv, reference_dte=excluded.reference_dte",
                (symbol, day.isoformat(), float(atm_iv), reference_dte),
            )

    def get_iv_history(self, symbol: str, limit: int = 504) -> pd.Series:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM (SELECT date, atm_iv FROM iv_history WHERE symbol = ? "
                "ORDER BY date DESC LIMIT ?) ORDER BY date",
                (symbol, limit),
            ).fetchall()
        if not rows:
            return pd.Series(dtype="float64")
        return pd.Series(
            [float(r["atm_iv"]) for r in rows],
            index=pd.DatetimeIndex(pd.to_datetime([r["date"] for r in rows])),
            name="atm_iv",
        )

    # -- earnings ----------------------------------------------------------

    def save_earnings(self, symbol: str, next_date: date | None, is_etf: bool) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO earnings (symbol, next_date, is_etf, fetched_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(symbol) DO UPDATE SET next_date=excluded.next_date, "
                "is_etf=excluded.is_etf, fetched_at=excluded.fetched_at",
                (symbol, next_date.isoformat() if next_date else None, int(is_etf), _utcnow()),
            )

    def get_earnings(self, symbol: str) -> dict | None:
        """Cached earnings info, or None when the symbol has never been fetched.

        Note the distinction the caller relies on: a stored row with
        ``next_date=None`` means "we looked and there is none" (a fund, or an
        unpublished date), whereas ``None`` means "we have never looked".
        """
        with self.connect() as conn:
            row = conn.execute(
                "SELECT next_date, is_etf, fetched_at FROM earnings WHERE symbol = ?", (symbol,)
            ).fetchone()
        if row is None:
            return None
        return {
            "next_date": date.fromisoformat(row["next_date"]) if row["next_date"] else None,
            "is_etf": bool(row["is_etf"]),
            "fetched_at": datetime.fromisoformat(row["fetched_at"]),
        }

    # -- config snapshots --------------------------------------------------

    def save_config_snapshot(self, config: ScreenerConfig) -> str:
        fingerprint = config.fingerprint
        with self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO configs (fingerprint, payload, first_seen) VALUES (?, ?, ?)",
                (fingerprint, config.canonical_json(), _utcnow()),
            )
        return fingerprint

    def get_config_snapshot(self, fingerprint: str) -> dict[str, Any] | None:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT payload FROM configs WHERE fingerprint = ?", (fingerprint,)
            ).fetchone()
        return json.loads(row["payload"]) if row else None

    # -- active configuration (the one the dashboard edits) ---------------

    def save_active_config(self, config: ScreenerConfig) -> None:
        self.save_config_snapshot(config)
        self.set_setting("active_config", config.canonical_json())

    def load_active_config(self) -> ScreenerConfig:
        raw = self.get_setting("active_config")
        if not raw:
            return ScreenerConfig()
        try:
            return ScreenerConfig.from_dict(json.loads(raw))
        except Exception as exc:
            log.error("Stored config could not be loaded (%s); falling back to defaults.", exc)
            return ScreenerConfig()

    def set_setting(self, key: str, value: str) -> None:
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def get_setting(self, key: str) -> str | None:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    # -- runs --------------------------------------------------------------

    def start_run(self, trigger: str, config: ScreenerConfig, provider: str) -> int:
        fingerprint = self.save_config_snapshot(config)
        with self.connect() as conn:
            cursor = conn.execute(
                "INSERT INTO runs (started_at, trigger, status, fingerprint, provider, posture) "
                "VALUES (?, ?, 'running', ?, ?, ?)",
                (_utcnow(), trigger, fingerprint, provider, config.posture),
            )
            return int(cursor.lastrowid)

    def finish_run(self, run_id: int, status: str, message: str | None = None) -> None:
        with self.connect() as conn:
            conn.execute(
                "UPDATE runs SET finished_at = ?, status = ?, message = ? WHERE id = ?",
                (_utcnow(), status, message, run_id),
            )

    def save_ranking(self, run_id: int, scores: list) -> None:
        """Persist a complete ranking, including every parameter breakdown."""
        entries, params = [], []
        for score in scores:
            entries.append((
                run_id, score.symbol, score.rank or 0, score.composite,
                score.direction, score.direction_label, score.conviction, score.net_signal,
                score.spot, score.dollar_volume, score.completeness,
                json.dumps(score.contract) if score.contract else None,
                json.dumps(score.options_notes) if score.options_notes else None,
                score.tie_break_note,
            ))
            for result in score.parameters:
                params.append((
                    run_id, score.symbol, result.key, result.label, result.layer,
                    result.status, result.weight, result.effective_weight,
                    result.signal, result.raw_value, result.display_value,
                    result.contribution, result.explanation,
                    json.dumps(result.detail) if result.detail else None,
                ))
        with self.connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO ranking_entries (run_id, symbol, rank, composite, "
                "direction, direction_label, conviction, net_signal, spot, dollar_volume, "
                "completeness, contract_json, options_notes, tie_break_note) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                entries,
            )
            conn.executemany(
                "INSERT OR REPLACE INTO parameter_results (run_id, symbol, param_key, label, "
                "layer, status, weight, effective_weight, signal, raw_value, display_value, "
                "contribution, explanation, detail_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                params,
            )

    def latest_run(self, only_successful: bool = True) -> dict | None:
        clause = "WHERE status IN ('ok', 'partial')" if only_successful else ""
        with self.connect() as conn:
            row = conn.execute(
                f"SELECT * FROM runs {clause} ORDER BY started_at DESC, id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else None

    def get_run(self, run_id: int) -> dict | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return dict(row) if row else None

    def list_runs(self, limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT r.*, COUNT(e.symbol) AS entry_count FROM runs r "
                "LEFT JOIN ranking_entries e ON e.run_id = r.id "
                "WHERE r.status IN ('ok', 'partial') "
                "GROUP BY r.id ORDER BY r.started_at DESC, r.id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_ranking(self, run_id: int) -> list[dict]:
        """A full ranking with each entry's parameter breakdown attached."""
        with self.connect() as conn:
            entries = conn.execute(
                "SELECT * FROM ranking_entries WHERE run_id = ? ORDER BY rank", (run_id,)
            ).fetchall()
            params = conn.execute(
                "SELECT * FROM parameter_results WHERE run_id = ?", (run_id,)
            ).fetchall()
            returns = conn.execute(
                "SELECT * FROM forward_returns WHERE run_id = ?", (run_id,)
            ).fetchall()

        by_symbol: dict[str, list[dict]] = {}
        for row in params:
            item = dict(row)
            item["detail"] = json.loads(item.pop("detail_json") or "{}")
            by_symbol.setdefault(item["symbol"], []).append(item)

        returns_by_symbol: dict[str, list[dict]] = {}
        for row in returns:
            returns_by_symbol.setdefault(row["symbol"], []).append(dict(row))

        out = []
        for row in entries:
            entry = dict(row)
            entry["contract"] = json.loads(entry.pop("contract_json") or "null")
            entry["options_notes"] = json.loads(entry.pop("options_notes") or "[]")
            entry["parameters"] = _ordered_parameters(by_symbol.get(entry["symbol"], []))
            entry["forward_returns"] = sorted(
                returns_by_symbol.get(entry["symbol"], []), key=lambda r: r["horizon_days"]
            )
            out.append(entry)
        return out

    def runs_needing_forward_returns(self, horizons: tuple[int, ...]) -> list[dict]:
        """Past runs that do not yet have every forward-return horizon computed."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT r.id, r.started_at FROM runs r WHERE r.status IN ('ok', 'partial') "
                "ORDER BY r.started_at DESC LIMIT 400"
            ).fetchall()
            existing = conn.execute(
                "SELECT run_id, horizon_days, COUNT(*) AS n FROM forward_returns "
                "GROUP BY run_id, horizon_days"
            ).fetchall()
        have: dict[int, set[int]] = {}
        for row in existing:
            have.setdefault(row["run_id"], set()).add(row["horizon_days"])
        return [
            dict(r) for r in rows
            if not set(horizons).issubset(have.get(r["id"], set()))
        ]

    def save_forward_returns(self, rows: list[tuple]) -> None:
        if not rows:
            return
        with self.connect() as conn:
            conn.executemany(
                "INSERT OR REPLACE INTO forward_returns (run_id, symbol, horizon_days, "
                "start_price, end_price, return_pct, directional_pct, computed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                rows,
            )

    # -- decisions ---------------------------------------------------------

    def record_decision(self, run_id: int, symbol: str, decision: str, **fields) -> None:
        """Store what you did about one candidate. Re-recording overwrites."""
        if decision not in ("taken", "passed", "watching"):
            raise ValueError(
                f"decision must be 'taken', 'passed' or 'watching', got {decision!r}"
            )
        with self.connect() as conn:
            conn.execute(
                "INSERT INTO decisions (run_id, symbol, decision, note, instrument, "
                "contract, entry_price, quantity, composite, direction, decided_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(run_id, symbol) DO UPDATE SET "
                "decision=excluded.decision, note=excluded.note, "
                "instrument=excluded.instrument, contract=excluded.contract, "
                "entry_price=excluded.entry_price, quantity=excluded.quantity, "
                "decided_at=excluded.decided_at",
                (
                    run_id, symbol, decision, fields.get("note"),
                    fields.get("instrument"), fields.get("contract"),
                    fields.get("entry_price"), fields.get("quantity"),
                    fields.get("composite"), fields.get("direction"), _utcnow(),
                ),
            )

    def get_decisions(self, run_id: int | None = None, limit: int = 500) -> list[dict]:
        query = "SELECT * FROM decisions"
        params: tuple = ()
        if run_id is not None:
            query += " WHERE run_id = ?"
            params = (run_id,)
        query += " ORDER BY decided_at DESC LIMIT ?"
        params = params + (limit,)
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(query, params).fetchall()]

    def delete_decision(self, run_id: int, symbol: str) -> bool:
        with self.connect() as conn:
            cursor = conn.execute(
                "DELETE FROM decisions WHERE run_id = ? AND symbol = ?", (run_id, symbol)
            )
            return cursor.rowcount > 0

    def decisions_with_outcomes(self, horizon_days: int) -> list[dict]:
        """Every decision that now has a forward return at ``horizon_days``.

        Joins the decision to the forward return *and* to the parameter
        breakdown that produced the score, which is what makes per-parameter
        calibration possible.
        """
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT d.run_id, d.symbol, d.decision, d.composite, d.direction, "
                "       d.instrument, d.decided_at, "
                "       f.return_pct, f.directional_pct "
                "FROM decisions d "
                "JOIN forward_returns f "
                "  ON f.run_id = d.run_id AND f.symbol = d.symbol "
                "WHERE f.horizon_days = ? "
                "ORDER BY d.decided_at",
                (horizon_days,),
            ).fetchall()
            outcomes = [dict(r) for r in rows]

            for item in outcomes:
                params = conn.execute(
                    "SELECT param_key, label, status, contribution, signal, raw_value "
                    "FROM parameter_results WHERE run_id = ? AND symbol = ?",
                    (item["run_id"], item["symbol"]),
                ).fetchall()
                item["parameters"] = [dict(p) for p in params]
        return outcomes

    # -- maintenance -------------------------------------------------------

    def prune(self, keep_runs: int = 120, vacuum: bool = True) -> dict:
        """Drop old runs so the database stays a bounded size.

        Each run stores a parameter breakdown per ticker — explanations
        included — which is roughly 135 KB per run for a ten-ticker watchlist.
        Twice-daily refreshes would reach ~66 MB a year, so scheduled runs need
        this.

        **Runs carrying a decision are never pruned.** ``decisions`` has a
        cascading foreign key to ``runs``, so deleting an old run would
        silently take the journal entry with it — and those are the only
        records here that cannot be regenerated.

        Prices, implied-volatility history, earnings dates and config snapshots
        are all kept: they are small, and the IV series must accumulate for a
        year before IV Rank can stop using its proxy.
        """
        if keep_runs < 1:
            raise ValueError(f"keep_runs must be at least 1, got {keep_runs}")

        with self.connect() as conn:
            before = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            protected = conn.execute(
                "SELECT COUNT(DISTINCT run_id) FROM decisions"
            ).fetchone()[0]

            cursor = conn.execute(
                "DELETE FROM runs WHERE id NOT IN ("
                "    SELECT id FROM runs ORDER BY started_at DESC, id DESC LIMIT ?"
                ") AND id NOT IN (SELECT DISTINCT run_id FROM decisions)",
                (keep_runs,),
            )
            deleted = cursor.rowcount
            after = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]

        if vacuum and deleted:
            # VACUUM cannot run inside a transaction, so it gets its own
            # connection. Without it the file keeps the freed pages and the
            # committed blob never actually shrinks.
            conn = sqlite3.connect(self.path, timeout=60.0)
            try:
                conn.execute("VACUUM")
            finally:
                conn.close()

        result = {
            "runs_before": before,
            "runs_after": after,
            "deleted": deleted,
            "protected_by_decisions": protected,
            "size_bytes": self.path.stat().st_size if self.path.exists() else 0,
        }
        log.info("Pruned %d run(s); %d remain (%d protected by decisions)",
                 deleted, after, protected)
        return result

    def run_entries_for_returns(self, run_id: int) -> list[dict]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT symbol, rank, composite, direction, spot FROM ranking_entries "
                "WHERE run_id = ?", (run_id,)
            ).fetchall()
        return [dict(r) for r in rows]


def _ordered_parameters(items: list[dict]) -> list[dict]:
    """Return parameters in registry order so the UI is stable run to run."""
    from screener.param_specs import ALL_SPECS

    order = {s.key: i for i, s in enumerate(ALL_SPECS)}
    return sorted(items, key=lambda p: order.get(p["param_key"], 999))


def _none_if_nan(value: Any) -> float | None:
    if value is None or pd.isna(value):
        return None
    return float(value)
