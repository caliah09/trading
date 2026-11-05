"""Tests for the CLI commands the scheduled refresh depends on.

`gate`, `prune` and `export` exist to be run by CI, where a failure is a silent
stale ranking rather than a visible error, so they are tested directly.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

import pytest

from screener.cli import main
from screener.config import ScreenerConfig
from screener.pipeline import RefreshPipeline, build_provider

ET = ZoneInfo("America/New_York")


def seed(db, runs: int = 1, watchlist=("AAPL", "MSFT")) -> None:
    config = ScreenerConfig(watchlist=list(watchlist))
    db.save_active_config(config)
    provider = build_provider("synthetic")
    for _ in range(runs):
        RefreshPipeline(db, provider).run(config, trigger="cli")


class TestGate:
    def _run_at(self, moment: datetime, window: str) -> int:
        with mock.patch("screener.cli.datetime") as clock:
            clock.now.return_value = moment
            return main(["gate", "--window", window])

    def test_runs_inside_the_open_window(self):
        assert self._run_at(datetime(2026, 11, 5, 9, 40, tzinfo=ET), "open") == 0

    def test_runs_inside_the_close_window(self):
        assert self._run_at(datetime(2026, 11, 5, 16, 15, tzinfo=ET), "close") == 0

    def test_tolerates_a_late_cron(self):
        """GitHub Actions cron is routinely minutes late and sometimes worse."""
        assert self._run_at(datetime(2026, 11, 5, 11, 0, tzinfo=ET), "open") == 0

    def test_skips_before_the_open(self):
        assert self._run_at(datetime(2026, 11, 5, 8, 35, tzinfo=ET), "open") == 1

    def test_skips_the_wrong_daylight_saving_slot(self):
        """14:35 UTC is 09:35 ET in winter but 10:35 ET in summer.

        Both UTC slots fire year-round; the window is what makes only one of
        them count.
        """
        summer_wrong = datetime(2026, 7, 15, 14, 35, tzinfo=timezone.utc).astimezone(ET)
        assert summer_wrong.hour == 10
        assert self._run_at(summer_wrong, "open") == 0  # still inside 09:30-11:30

        winter_wrong = datetime(2026, 1, 15, 13, 35, tzinfo=timezone.utc).astimezone(ET)
        assert winter_wrong.hour == 8
        assert self._run_at(winter_wrong, "open") == 1  # outside the window

    def test_skips_a_weekend(self):
        saturday = datetime(2026, 11, 7, 9, 40, tzinfo=ET)
        assert saturday.weekday() == 5
        assert self._run_at(saturday, "open") == 1

    def test_skips_a_market_holiday(self):
        thanksgiving = datetime(2026, 11, 26, 9, 40, tzinfo=ET)
        assert self._run_at(thanksgiving, "open") == 1

    def test_creates_no_database(self, tmp_path, monkeypatch):
        """It runs before the database is restored in CI, so it must not make one."""
        monkeypatch.chdir(tmp_path)
        with mock.patch("screener.cli.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 7, 9, 40, tzinfo=ET)
            main(["gate", "--window", "open"])
        assert not (tmp_path / "data").exists()


class TestPrune:
    def test_keeps_the_requested_number(self, db):
        seed(db, runs=5)
        result = db.prune(keep_runs=2)
        assert result["runs_after"] == 2
        assert result["deleted"] == 3

    def test_keeps_the_most_recent(self, db):
        seed(db, runs=4)
        newest = db.latest_run()["id"]
        db.prune(keep_runs=1)
        assert db.latest_run()["id"] == newest

    def test_never_prunes_a_run_carrying_a_decision(self, db):
        """Decisions cascade with their run and cannot be regenerated."""
        seed(db, runs=5)
        runs = db.list_runs()
        oldest = runs[-1]["id"]
        db.record_decision(oldest, "AAPL", "taken", note="keep me")

        db.prune(keep_runs=1)
        remaining = {r["id"] for r in db.list_runs()}
        assert oldest in remaining
        assert db.get_decisions(oldest)[0]["note"] == "keep me"

    def test_reports_how_many_were_protected(self, db):
        seed(db, runs=3)
        db.record_decision(db.list_runs()[-1]["id"], "AAPL", "passed")
        assert db.prune(keep_runs=1)["protected_by_decisions"] == 1

    def test_cascades_to_parameter_rows(self, db):
        seed(db, runs=3)
        db.prune(keep_runs=1)
        with db.connect() as conn:
            orphans = conn.execute(
                "SELECT COUNT(*) FROM parameter_results WHERE run_id NOT IN "
                "(SELECT id FROM runs)"
            ).fetchone()[0]
        assert orphans == 0

    def test_keeps_prices_and_iv_history(self, db):
        """Those accumulate for a year before IV Rank stops using its proxy."""
        seed(db, runs=3)
        bars = len(db.get_prices("AAPL"))
        iv = len(db.get_iv_history("AAPL"))
        db.prune(keep_runs=1)
        assert len(db.get_prices("AAPL")) == bars
        assert len(db.get_iv_history("AAPL")) == iv

    def test_shrinks_the_file(self, db):
        seed(db, runs=8)
        before = db.path.stat().st_size
        db.prune(keep_runs=1, vacuum=True)
        assert db.path.stat().st_size < before

    def test_no_op_when_under_the_limit(self, db):
        seed(db, runs=2)
        assert db.prune(keep_runs=50)["deleted"] == 0

    def test_rejects_a_nonsense_limit(self, db):
        with pytest.raises(ValueError, match="at least 1"):
            db.prune(keep_runs=0)

    def test_via_cli(self, db, capsys):
        seed(db, runs=4)
        assert main(["--db", str(db.path), "prune", "--keep", "1"]) == 0
        assert "Runs 4 -> 1" in capsys.readouterr().out


class TestExport:
    def test_markdown_contains_the_ranking(self, db, capsys):
        seed(db)
        assert main(["--db", str(db.path), "export", "--format", "markdown"]) == 0
        output = capsys.readouterr().out
        assert "# Rankings" in output
        assert "AAPL" in output
        assert "| # | Ticker |" in output

    def test_markdown_carries_the_disclaimer(self, db, capsys):
        seed(db)
        main(["--db", str(db.path), "export", "--format", "markdown"])
        assert "not financial advice" in capsys.readouterr().out.lower()

    def test_markdown_explains_the_top_candidates(self, db, capsys):
        seed(db)
        main(["--db", str(db.path), "export", "--format", "markdown"])
        output = capsys.readouterr().out
        assert "— why" in output
        # Reasoning, not just numbers.
        assert "→" in output

    def test_json_round_trips(self, db, capsys):
        seed(db)
        assert main(["--db", str(db.path), "export", "--format", "json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["entries"]
        assert payload["entries"][0]["parameters"]

    def test_writes_to_a_file(self, db, tmp_path):
        seed(db)
        target = tmp_path / "nested" / "ranking.md"
        assert main(["--db", str(db.path), "export", "--output", str(target)]) == 0
        assert "# Rankings" in target.read_text()

    def test_fails_cleanly_with_no_ranking(self, db):
        assert main(["--db", str(db.path), "export"]) == 1
