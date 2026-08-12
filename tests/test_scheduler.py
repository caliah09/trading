"""Tests for the in-process scheduler.

The schedule arithmetic is computed rather than delegated to APScheduler, so
that holidays are respected. These tests pin that arithmetic and the
concurrency guarantee that two refreshes never overlap.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta

import pytest

from screener.config import ScreenerConfig
from screener.scheduler import (
    CLOSE_TIME,
    MARKET_TZ,
    OPEN_TIME,
    RefreshBusy,
    RefreshService,
    next_scheduled,
    previous_scheduled,
)


def et(year, month, day, hour, minute) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=MARKET_TZ)


class TestNextScheduled:
    def test_before_the_open_returns_the_open(self):
        when, trigger = next_scheduled(et(2026, 3, 11, 8, 0))
        assert trigger == "market_open"
        assert when == et(2026, 3, 11, OPEN_TIME.hour, OPEN_TIME.minute)

    def test_midday_returns_the_close(self):
        when, trigger = next_scheduled(et(2026, 3, 11, 12, 0))
        assert trigger == "market_close"
        assert when.date() == et(2026, 3, 11, 0, 0).date()

    def test_after_the_close_rolls_to_the_next_day(self):
        when, trigger = next_scheduled(et(2026, 3, 11, 20, 0))
        assert trigger == "market_open"
        assert when.date() == et(2026, 3, 12, 0, 0).date()

    def test_friday_evening_rolls_to_monday(self):
        when, _ = next_scheduled(et(2026, 3, 13, 20, 0))
        assert when.date() == et(2026, 3, 16, 0, 0).date()

    def test_skips_a_holiday(self):
        """Thanksgiving 2026 is 26 Nov; the next session is the 27th."""
        when, _ = next_scheduled(et(2026, 11, 25, 20, 0))
        assert when.date() == et(2026, 11, 27, 0, 0).date()

    def test_saturday_returns_monday_open(self):
        when, trigger = next_scheduled(et(2026, 3, 14, 10, 0))
        assert trigger == "market_open"
        assert when.date() == et(2026, 3, 16, 0, 0).date()


class TestPreviousScheduled:
    def test_midday_returns_todays_open(self):
        when, trigger = previous_scheduled(et(2026, 3, 11, 12, 0))
        assert trigger == "market_open"
        assert when == et(2026, 3, 11, OPEN_TIME.hour, OPEN_TIME.minute)

    def test_evening_returns_todays_close(self):
        when, trigger = previous_scheduled(et(2026, 3, 11, 20, 0))
        assert trigger == "market_close"
        assert when == et(2026, 3, 11, CLOSE_TIME.hour, CLOSE_TIME.minute)

    def test_before_the_open_returns_the_prior_session(self):
        when, trigger = previous_scheduled(et(2026, 3, 11, 6, 0))
        assert trigger == "market_close"
        assert when.date() == et(2026, 3, 10, 0, 0).date()

    def test_monday_morning_returns_friday_close(self):
        when, _ = previous_scheduled(et(2026, 3, 16, 6, 0))
        assert when.date() == et(2026, 3, 13, 0, 0).date()


class TestMissedRefresh:
    def test_reports_a_miss_when_nothing_has_run(self, db):
        service = RefreshService(db, provider_name="synthetic", catch_up=False)
        assert service.missed_refresh() is not None

    def test_no_miss_after_a_recent_run(self, db):
        service = RefreshService(db, provider_name="synthetic", catch_up=False)
        service.refresh(trigger="manual")
        assert service.missed_refresh() is None

    def test_reports_a_miss_after_an_old_run(self, db):
        service = RefreshService(db, provider_name="synthetic", catch_up=False)
        service.refresh(trigger="manual")
        stale = (datetime.now(MARKET_TZ) - timedelta(days=14)).isoformat(timespec="seconds")
        with db.connect() as conn:
            conn.execute("UPDATE runs SET started_at = ?", (stale,))
        assert service.missed_refresh() is not None


class TestRefreshSerialisation:
    def test_a_second_refresh_is_rejected_while_one_runs(self, db):
        """A double-click must not start two overlapping writers."""
        service = RefreshService(db, provider_name="synthetic", catch_up=False)
        service._lock.acquire()
        try:
            with pytest.raises(RefreshBusy):
                service.refresh(trigger="manual", blocking=False)
        finally:
            service._lock.release()

    def test_refresh_releases_the_lock_on_failure(self, db, monkeypatch):
        service = RefreshService(db, provider_name="synthetic", catch_up=False)

        def explode(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr("screener.scheduler.build_provider", explode)
        with pytest.raises(RuntimeError):
            service.refresh(trigger="manual")
        # The lock must be free, otherwise the app deadlocks after one error.
        assert service._lock.acquire(blocking=False)
        service._lock.release()

    def test_concurrent_refreshes_do_not_interleave(self, db):
        service = RefreshService(db, provider_name="synthetic", catch_up=False)
        db.save_active_config(ScreenerConfig(watchlist=["AAPL", "MSFT"]))
        results = []

        def worker():
            try:
                results.append(service.refresh(trigger="manual", blocking=True).status)
            except Exception as exc:  # pragma: no cover - should not happen
                results.append(f"error: {exc}")

        threads = [threading.Thread(target=worker) for _ in range(3)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=60)
        assert results == ["ok", "ok", "ok"]


class TestStatus:
    def test_reports_provider_and_schedule(self, db):
        service = RefreshService(db, provider_name="synthetic", catch_up=False)
        status = service.status().to_dict()
        assert status["provider"] == "synthetic"
        assert status["scheduler_running"] is False
        assert status["last_run"] is None
        assert len(status["schedule"]) == 2

    def test_reports_the_last_run(self, db):
        service = RefreshService(db, provider_name="synthetic", catch_up=False)
        db.save_active_config(ScreenerConfig(watchlist=["AAPL"]))
        service.refresh(trigger="manual")
        assert service.status().last_run["status"] == "ok"

    def test_next_run_is_populated(self, db):
        service = RefreshService(db, provider_name="synthetic", catch_up=False)
        assert service.status().next_run_at is not None


class TestLifecycle:
    def test_start_and_shutdown(self, db):
        service = RefreshService(db, provider_name="synthetic", catch_up=False)
        service.start()
        try:
            assert service.status().running is True
            assert {job.id for job in service._scheduler.get_jobs()} == \
                   {"market_open", "market_close"}
        finally:
            service.shutdown()
        assert service.status().running is False

    def test_scheduled_job_skips_a_non_session(self, db, monkeypatch):
        """The holiday guard is why the schedule is not left to APScheduler alone."""
        service = RefreshService(db, provider_name="synthetic", catch_up=False)
        called = []
        monkeypatch.setattr(service, "refresh", lambda **kw: called.append(kw))
        monkeypatch.setattr("screener.scheduler.is_trading_day", lambda day: False)
        service._scheduled_refresh("market_open")
        assert called == []

    def test_scheduled_job_survives_a_failure(self, db, monkeypatch):
        """One bad refresh must not kill the timer for every future run."""
        service = RefreshService(db, provider_name="synthetic", catch_up=False)

        def explode(**kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(service, "refresh", explode)
        monkeypatch.setattr("screener.scheduler.is_trading_day", lambda day: True)
        service._scheduled_refresh("market_open")  # must not raise
