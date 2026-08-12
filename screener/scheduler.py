"""In-process refresh scheduling.

The dashboard schedules its own refreshes; there is no cron or Task Scheduler
entry to create. Starting the app starts the timer.

**The tradeoff, stated plainly:** an in-process scheduler only fires while the
app is running. Close the terminal, reboot, or let the machine sleep through
the close, and that refresh does not happen. An OS-level scheduler would have
run it regardless. What this module does to soften that is check on startup
whether a scheduled refresh was missed since the last stored run, and if so run
one immediately — so launching the app on Monday morning gives you current data
rather than Friday's. It cannot backfill a ranking *as of* a moment that has
passed, because the market data for that moment is gone.

Times are anchored to America/New_York and guarded by the market calendar, so
they follow daylight saving and skip weekends and holidays automatically.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from screener.config import ScreenerConfig
from screener.db import Database
from screener.market_calendar import is_trading_day
from screener.pipeline import RefreshPipeline, RunSummary, build_provider

log = logging.getLogger(__name__)

MARKET_TZ = ZoneInfo("America/New_York")

#: Five minutes after the opening bell — the opening auction and the first
#: prints settle by then, and yfinance's snapshot is less likely to be stale.
OPEN_TIME = time(9, 35)

#: Ten minutes after the close, so the closing print is reflected.
CLOSE_TIME = time(16, 10)

SCHEDULE = (("market_open", OPEN_TIME), ("market_close", CLOSE_TIME))


class RefreshBusy(RuntimeError):
    """A refresh was requested while one was already running."""


@dataclass
class SchedulerStatus:
    running: bool
    refresh_in_progress: bool
    next_run_at: datetime | None
    next_run_trigger: str | None
    last_run: dict | None
    provider: str

    def to_dict(self) -> dict:
        return {
            "scheduler_running": self.running,
            "refresh_in_progress": self.refresh_in_progress,
            "next_run_at": self.next_run_at.isoformat() if self.next_run_at else None,
            "next_run_trigger": self.next_run_trigger,
            "last_run": self.last_run,
            "provider": self.provider,
            "market_timezone": str(MARKET_TZ),
            "schedule": [
                {"trigger": name, "time_et": t.strftime("%H:%M")} for name, t in SCHEDULE
            ],
        }


class RefreshService:
    """Owns the schedule, and serialises every refresh through one lock."""

    def __init__(self, db: Database, provider_name: str = "yfinance",
                 config_loader: Callable[[], ScreenerConfig] | None = None,
                 catch_up: bool = True) -> None:
        self.db = db
        self.provider_name = provider_name
        self._config_loader = config_loader or db.load_active_config
        self._catch_up = catch_up
        self._scheduler = BackgroundScheduler(timezone=MARKET_TZ)
        # Non-reentrant: a manual refresh during a scheduled one must wait or
        # be rejected, never interleave two writers on the same run tables.
        self._lock = threading.Lock()
        self._in_progress = False
        self._last_summary: RunSummary | None = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        """Register jobs and start the background timer."""
        for name, at in SCHEDULE:
            self._scheduler.add_job(
                self._scheduled_refresh,
                trigger=CronTrigger(day_of_week="mon-fri", hour=at.hour,
                                    minute=at.minute, timezone=MARKET_TZ),
                args=[name],
                id=name,
                # If the app was asleep, run once on wake rather than firing
                # every missed occurrence in a burst.
                coalesce=True,
                misfire_grace_time=3600,
                max_instances=1,
                replace_existing=True,
            )
        self._scheduler.start()
        log.info("Refresh scheduler started (%s)",
                 ", ".join(f"{n} at {t:%H:%M} ET" for n, t in SCHEDULE))

        if self._catch_up:
            threading.Thread(target=self._startup_catch_up, name="startup-catchup",
                             daemon=True).start()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            log.info("Refresh scheduler stopped")

    # -- refreshing --------------------------------------------------------

    def refresh(self, trigger: str = "manual", blocking: bool = True) -> RunSummary:
        """Run the pipeline once.

        Raises :class:`RefreshBusy` if another refresh holds the lock and
        ``blocking`` is False, so the UI can report "already running" instead of
        queueing a duplicate behind the user's button press.
        """
        acquired = self._lock.acquire(blocking=blocking)
        if not acquired:
            raise RefreshBusy("A refresh is already in progress.")
        try:
            self._in_progress = True
            config = self._config_loader()
            provider = build_provider(self.provider_name)
            pipeline = RefreshPipeline(self.db, provider)
            summary = pipeline.run(config, trigger=trigger)
            self._last_summary = summary
            return summary
        finally:
            self._in_progress = False
            self._lock.release()

    def _scheduled_refresh(self, trigger: str) -> None:
        """Job entry point. Swallows errors so a failure cannot kill the timer."""
        today = datetime.now(MARKET_TZ).date()
        if not is_trading_day(today):
            log.info("Skipping %s refresh: %s is not a trading session", trigger, today)
            return
        try:
            self.refresh(trigger=trigger, blocking=True)
        except Exception:
            log.exception("Scheduled %s refresh failed", trigger)

    def _startup_catch_up(self) -> None:
        """Run once at launch if a scheduled refresh was missed."""
        try:
            missed = self.missed_refresh()
            if missed is None:
                log.info("No missed refresh at startup")
                return
            log.info("Missed the %s refresh at %s; catching up now", missed[1], missed[0])
            self.refresh(trigger="startup_catchup", blocking=True)
        except Exception:
            log.exception("Startup catch-up failed")

    def missed_refresh(self) -> tuple[datetime, str] | None:
        """The most recent scheduled fire time that has no run after it."""
        now = datetime.now(MARKET_TZ)
        previous = previous_scheduled(now)
        if previous is None:
            return None
        fire_time, trigger = previous

        last = self.db.latest_run(only_successful=True)
        if last is None:
            return previous
        started = datetime.fromisoformat(last["started_at"])
        if started.tzinfo is None:
            started = started.replace(tzinfo=ZoneInfo("UTC"))
        return None if started.astimezone(MARKET_TZ) >= fire_time else previous

    # -- status ------------------------------------------------------------

    def status(self) -> SchedulerStatus:
        upcoming = next_scheduled(datetime.now(MARKET_TZ))
        return SchedulerStatus(
            running=self._scheduler.running,
            refresh_in_progress=self._in_progress,
            next_run_at=upcoming[0] if upcoming else None,
            next_run_trigger=upcoming[1] if upcoming else None,
            last_run=self.db.latest_run(only_successful=False),
            provider=self.provider_name,
        )


# --------------------------------------------------------------------------
# Schedule arithmetic
#
# Computed here rather than read from APScheduler's next_run_time so that the
# holiday guard is reflected: APScheduler would happily report Thanksgiving as
# the next fire time, and the job would then skip itself.
# --------------------------------------------------------------------------

def _fire_times(day: date) -> list[tuple[datetime, str]]:
    if not is_trading_day(day):
        return []
    return [
        (datetime.combine(day, at, tzinfo=MARKET_TZ), name)
        for name, at in SCHEDULE
    ]


def next_scheduled(now: datetime, horizon_days: int = 10) -> tuple[datetime, str] | None:
    """The next scheduled refresh at or after ``now``, skipping non-sessions."""
    now = now.astimezone(MARKET_TZ)
    for offset in range(horizon_days):
        day = now.date() + timedelta(days=offset)
        for fire_time, trigger in _fire_times(day):
            if fire_time > now:
                return fire_time, trigger
    return None


def previous_scheduled(now: datetime, horizon_days: int = 10) -> tuple[datetime, str] | None:
    """The most recent scheduled refresh strictly before ``now``."""
    now = now.astimezone(MARKET_TZ)
    for offset in range(horizon_days):
        day = now.date() - timedelta(days=offset)
        for fire_time, trigger in reversed(_fire_times(day)):
            if fire_time < now:
                return fire_time, trigger
    return None
