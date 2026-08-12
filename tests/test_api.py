"""API tests.

The scheduler is disabled in these tests so no background timer runs; the
refresh endpoint is exercised directly instead.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from screener.config import ScreenerConfig
from screener.db import Database


@pytest.fixture
def client(tmp_path):
    from screener.api.app import create_app

    app = create_app(
        db_path=tmp_path / "api.sqlite",
        provider_name="synthetic",
        start_scheduler=False,
        catch_up=False,
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def seeded_client(client, tmp_path):
    """A client whose database already contains one ranking."""
    db = Database(tmp_path / "api.sqlite")
    db.save_active_config(ScreenerConfig(watchlist=["AAPL", "MSFT", "SPY"]))
    response = client.post("/api/refresh")
    assert response.status_code == 200
    return client


class TestMeta:
    def test_exposes_the_disclaimer(self, client):
        body = client.get("/api/meta").json()
        assert "not financial advice" in body["disclaimer"].lower()

    def test_lists_every_parameter_for_the_settings_ui(self, client):
        from screener.param_specs import ALL_SPECS

        body = client.get("/api/meta").json()
        assert {p["key"] for p in body["parameters"]} == {s.key for s in ALL_SPECS}

    def test_parameters_carry_editable_thresholds(self, client):
        body = client.get("/api/meta").json()
        rsi = next(p for p in body["parameters"] if p["key"] == "rsi")
        assert {t["key"] for t in rsi["thresholds"]} >= {"oversold", "overbought"}
        assert rsi["summary"]


class TestStatus:
    def test_reports_scheduler_state(self, client):
        body = client.get("/api/status").json()
        assert body["scheduler_running"] is False
        assert body["provider"] == "synthetic"
        assert body["last_run"] is None

    def test_next_run_and_schedule_are_exposed(self, client):
        body = client.get("/api/status").json()
        assert body["next_run_at"]
        assert [s["trigger"] for s in body["schedule"]] == ["market_open", "market_close"]


class TestRankings:
    def test_latest_is_empty_before_any_run(self, client):
        body = client.get("/api/rankings/latest").json()
        assert body["available"] is False
        assert "Refresh Now" in body["message"]

    def test_refresh_then_latest(self, seeded_client):
        body = seeded_client.get("/api/rankings/latest").json()
        assert body["available"] is True
        assert len(body["entries"]) == 3
        assert [e["rank"] for e in body["entries"]] == [1, 2, 3]

    def test_entries_include_full_reasoning(self, seeded_client):
        body = seeded_client.get("/api/rankings/latest").json()
        for entry in body["entries"]:
            assert entry["parameters"]
            for param in entry["parameters"]:
                assert param["explanation"]
                assert "contribution" in param

    def test_top_n_is_returned_for_the_ui(self, seeded_client):
        assert seeded_client.get("/api/rankings/latest").json()["top_n"] >= 1

    def test_ticker_detail(self, seeded_client):
        body = seeded_client.get("/api/rankings/latest").json()
        run_id = body["run"]["id"]
        detail = seeded_client.get(f"/api/rankings/{run_id}/aapl").json()
        assert detail["entry"]["symbol"] == "AAPL"
        assert detail["entry"]["parameters"]
        assert detail["disclaimer"]

    def test_ticker_detail_404(self, seeded_client):
        run_id = seeded_client.get("/api/rankings/latest").json()["run"]["id"]
        assert seeded_client.get(f"/api/rankings/{run_id}/NOSUCH").status_code == 404

    def test_unknown_run_404(self, client):
        assert client.get("/api/rankings/999").status_code == 404


class TestRefresh:
    def test_returns_a_summary(self, client):
        body = client.post("/api/refresh").json()
        assert body["status"] in ("ok", "partial")
        assert body["scored"]
        assert body["duration_seconds"] >= 0

    def test_rejects_an_unknown_trigger(self, client):
        assert client.post("/api/refresh?trigger=hack").status_code == 422

    def test_conflict_when_already_running(self, client, monkeypatch):
        from screener.scheduler import RefreshBusy

        def busy(*args, **kwargs):
            raise RefreshBusy("A refresh is already in progress.")

        monkeypatch.setattr(client.app.state.service, "refresh", busy)
        response = client.post("/api/refresh")
        assert response.status_code == 409
        assert "already in progress" in response.json()["detail"]

    def test_reports_a_run_that_scored_nothing(self, client, tmp_path):
        db = Database(tmp_path / "api.sqlite")
        db.save_active_config(ScreenerConfig(watchlist=["BADTICK", "DELISTED"]))
        response = client.post("/api/refresh")
        assert response.status_code == 502
        assert "no ticker could be scored" in response.json()["detail"]["message"].lower()


class TestHistory:
    def test_lists_runs(self, seeded_client):
        body = seeded_client.get("/api/runs").json()
        assert len(body["runs"]) == 1
        assert body["runs"][0]["entry_count"] == 3

    def test_flags_runs_made_under_a_different_config(self, seeded_client):
        assert seeded_client.get("/api/runs").json()["runs"][0]["is_current_config"] is True

        config = seeded_client.get("/api/config").json()["config"]
        config["parameters"]["rsi"]["weight"] = 1.0
        seeded_client.put("/api/config", json=config)

        body = seeded_client.get("/api/runs").json()
        assert body["runs"][0]["is_current_config"] is False

    def test_performance_endpoint(self, seeded_client):
        run_id = seeded_client.get("/api/rankings/latest").json()["run"]["id"]
        body = seeded_client.get(f"/api/runs/{run_id}/performance").json()
        # Too soon for forward returns, but the endpoint must answer cleanly.
        assert body["available"] is False

    def test_performance_404(self, client):
        assert client.get("/api/runs/999/performance").status_code == 404

    def test_config_snapshot_retrievable(self, seeded_client):
        run = seeded_client.get("/api/rankings/latest").json()["run"]
        body = seeded_client.get(f"/api/config/{run['fingerprint']}").json()
        assert body["config"]["watchlist"] == ["AAPL", "MSFT", "SPY"]

    def test_config_snapshot_404(self, client):
        assert client.get("/api/config/deadbeef").status_code == 404


class TestConfig:
    def test_get_returns_defaults(self, client):
        body = client.get("/api/config").json()
        assert body["config"]["watchlist"]
        assert body["fingerprint"]

    def test_update_round_trip(self, client):
        config = client.get("/api/config").json()["config"]
        config["watchlist"] = ["nvda", "tsla"]
        config["top_n"] = 3
        config["posture"] = "sell"

        response = client.put("/api/config", json=config)
        assert response.status_code == 200
        assert response.json()["config"]["watchlist"] == ["NVDA", "TSLA"]
        assert client.get("/api/config").json()["config"]["top_n"] == 3

    def test_weights_are_persisted(self, client):
        config = client.get("/api/config").json()["config"]
        config["parameters"]["rsi"]["weight"] = 2.5
        client.put("/api/config", json=config)
        assert client.get("/api/config").json()["config"]["parameters"]["rsi"]["weight"] == 2.5

    def test_thresholds_are_persisted(self, client):
        config = client.get("/api/config").json()["config"]
        config["parameters"]["rsi"]["thresholds"]["oversold"] = 25.0
        client.put("/api/config", json=config)
        stored = client.get("/api/config").json()["config"]
        assert stored["parameters"]["rsi"]["thresholds"]["oversold"] == 25.0

    def test_rejects_an_invalid_weight(self, client):
        config = client.get("/api/config").json()["config"]
        config["parameters"]["rsi"]["weight"] = 99.0
        assert client.put("/api/config", json=config).status_code == 400

    def test_rejects_an_invalid_posture(self, client):
        config = client.get("/api/config").json()["config"]
        config["posture"] = "sideways"
        assert client.put("/api/config", json=config).status_code == 400

    def test_rejects_all_zero_weights(self, client):
        config = client.get("/api/config").json()["config"]
        for param in config["parameters"].values():
            param["weight"] = 0.0
        assert client.put("/api/config", json=config).status_code == 400

    def test_rejected_update_leaves_stored_config_intact(self, client):
        """A bad save must never leave the dashboard unusable."""
        before = client.get("/api/config").json()["fingerprint"]
        config = client.get("/api/config").json()["config"]
        config["top_n"] = 0
        assert client.put("/api/config", json=config).status_code == 400
        assert client.get("/api/config").json()["fingerprint"] == before

    def test_rejects_malformed_body(self, client):
        assert client.put("/api/config", json={"buy_target": {"delta_low": "abc"}}).status_code == 400

    def test_new_weights_change_the_next_ranking(self, client, tmp_path):
        db = Database(tmp_path / "api.sqlite")
        db.save_active_config(ScreenerConfig(watchlist=["AAPL", "MSFT", "NVDA", "TSLA"]))
        first = client.post("/api/refresh").json()

        config = client.get("/api/config").json()["config"]
        for key, param in config["parameters"].items():
            param["weight"] = 10.0 if key == "volume" else 1.0
        client.put("/api/config", json=config)
        second = client.post("/api/refresh").json()

        assert first["run_id"] != second["run_id"]
        scores_a = {e["symbol"]: e["composite"]
                    for e in client.get(f"/api/rankings/{first['run_id']}").json()["entries"]}
        scores_b = {e["symbol"]: e["composite"]
                    for e in client.get(f"/api/rankings/{second['run_id']}").json()["entries"]}
        assert scores_a != scores_b


class TestWeb:
    def test_serves_the_dashboard(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "Stock &amp; Options Screener" in response.text

    def test_serves_static_assets(self, client):
        assert client.get("/static/app.js").status_code == 200
        assert client.get("/static/styles.css").status_code == 200
