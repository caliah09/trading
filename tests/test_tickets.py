"""Tests for order ticket generation."""

from __future__ import annotations

import pytest

from screener.tickets import (
    LEVERAGED_PAIRS,
    build_ticket,
    leveraged_drag,
)


def param(key, contribution=5.0, status="ok", detail=None, label=None, explanation="because"):
    return {
        "param_key": key, "label": label or key, "status": status,
        "contribution": contribution, "explanation": explanation,
        "detail": detail or {},
    }


def entry(**overrides) -> dict:
    base = {
        "symbol": "AAPL",
        "rank": 1,
        "composite": 72.4,
        "direction": "put",
        "direction_label": "Put lean",
        "spot": 180.0,
        "completeness": "11/11",
        "options_notes": [],
        "contract": {
            "contract_symbol": "AAPL260116P00175000",
            "kind": "put", "strike": 175.0, "expiry": "2026-01-16", "dte": 73,
            "bid": 4.80, "ask": 4.90, "mid": 4.85, "spread_pct": 2.1,
            "open_interest": 2400, "volume": 300,
            "implied_volatility": 0.31, "delta": -0.52,
        },
        "parameters": [
            param("head_shoulders", -8.5, detail={
                "neckline_now": 186.2, "target": 168.0, "expected_hold_days": 60,
            }),
            param("vol_risk_premium", 6.0, detail={"realised_vol": 28.0, "implied_vol": 31.0}),
        ],
    }
    base.update(overrides)
    return base


class TestInstrumentChoice:
    def test_auto_picks_the_option_when_one_exists(self):
        assert build_ticket(entry()).instrument == "option"

    def test_auto_falls_back_to_equity_without_a_contract(self):
        assert build_ticket(entry(contract=None)).instrument == "equity"

    def test_explicit_equity_is_honoured(self):
        assert build_ticket(entry(), instrument="equity").instrument == "equity"

    def test_leveraged_requires_a_listed_pair(self):
        ticket = build_ticket(entry(symbol="AAPL"), instrument="leveraged_etf")
        assert ticket.instrument == "equity"
        assert any("No leveraged ETF exists" in w for w in ticket.warnings)

    def test_leveraged_routes_a_listed_underlying(self):
        ticket = build_ticket(entry(symbol="QQQ"), instrument="leveraged_etf")
        assert ticket.instrument == "leveraged_etf"
        assert ticket.tradable_symbol == LEVERAGED_PAIRS["QQQ"].bear  # put lean

    def test_leveraged_picks_the_bull_fund_for_a_call_lean(self):
        ticket = build_ticket(entry(symbol="QQQ", direction="call"),
                              instrument="leveraged_etf")
        assert ticket.tradable_symbol == LEVERAGED_PAIRS["QQQ"].bull
        assert ticket.side == "buy"


class TestOptionTicket:
    def test_uses_the_contract_symbol(self):
        assert build_ticket(entry()).tradable_symbol == "AAPL260116P00175000"

    def test_limit_sits_above_mid_by_the_cap(self):
        ticket = build_ticket(entry(), price_cap_pct=2.0)
        assert ticket.limit_price == pytest.approx(4.85 * 1.02, abs=0.01)
        assert "mid" in ticket.price_basis

    def test_max_loss_is_defined_risk(self):
        assert "defined risk" in build_ticket(entry()).max_loss

    def test_notes_theta(self):
        assert any("Theta" in note for note in build_ticket(entry()).cost_notes)

    def test_warns_on_a_wide_spread(self):
        data = entry()
        data["contract"]["spread_pct"] = 22.0
        assert any("spread" in w.lower() for w in build_ticket(data).warnings)

    def test_warns_on_thin_open_interest(self):
        data = entry()
        data["contract"]["open_interest"] = 12
        assert any("Open interest" in w for w in build_ticket(data).warnings)

    def test_warns_when_expiry_precedes_the_expected_hold(self):
        """A 60-day edge expressed in a 10-day option is the wrong horizon."""
        data = entry()
        data["contract"]["dte"] = 10
        warnings = build_ticket(data).warnings
        assert any("expires before" in w for w in warnings)


class TestPatternContext:
    def test_pulls_invalidation_target_and_hold(self):
        ticket = build_ticket(entry())
        assert ticket.invalidation == pytest.approx(186.2)
        assert ticket.target == pytest.approx(168.0)
        assert ticket.expected_hold_days == 60
        assert "neckline" in ticket.invalidation_reason

    def test_absent_pattern_leaves_them_empty(self):
        data = entry(parameters=[param("rsi", 4.0)])
        ticket = build_ticket(data)
        assert ticket.invalidation is None
        assert ticket.target is None

    def test_ignores_an_uncomputed_pattern(self):
        data = entry(parameters=[param("head_shoulders", 0.0, status="insufficient_data")])
        assert build_ticket(data).invalidation is None


class TestReasoningAndWarnings:
    def test_carries_the_largest_contributors(self):
        reasoning = build_ticket(entry()).reasoning
        assert reasoning
        assert any("head_shoulders" in r for r in reasoning)

    def test_never_sizes_the_position(self):
        """Sizing needs a measured win rate; the ticket must not invent one."""
        ticket = build_ticket(entry())
        assert "Size this yourself" in ticket.sizing_note
        assert not hasattr(ticket, "quantity")

    def test_warns_on_a_neutral_direction(self):
        ticket = build_ticket(entry(direction="neutral"))
        assert any("no directional edge" in w for w in ticket.warnings)

    def test_warns_on_incomplete_scoring(self):
        ticket = build_ticket(entry(completeness="8/11"))
        assert any("8/11" in w for w in ticket.warnings)

    def test_passes_through_options_notes(self):
        ticket = build_ticket(entry(options_notes=["Chain was thin."]))
        assert "Chain was thin." in ticket.warnings

    def test_text_rendering_includes_the_disclaimer(self):
        text = build_ticket(entry()).to_text()
        assert "not advice" in text
        assert "Nothing has been placed" in text

    def test_serialises(self):
        payload = build_ticket(entry()).to_dict()
        assert payload["instrument"] == "option"
        assert payload["underlying"] == "AAPL"
        assert "warnings" in payload


class TestLeveragedDrag:
    def test_cost_rises_with_volatility(self):
        calm = leveraged_drag(20.0, 3.0, 60, 0.0095)
        wild = leveraged_drag(60.0, 3.0, 60, 0.0095)
        assert wild["expected_cost_pct"] > calm["expected_cost_pct"]

    def test_cost_rises_with_holding_period(self):
        short = leveraged_drag(30.0, 3.0, 10, 0.0095)
        long = leveraged_drag(30.0, 3.0, 120, 0.0095)
        assert long["expected_cost_pct"] > short["expected_cost_pct"]

    def test_carry_dominates_at_low_volatility(self):
        """Fees and financing are the whole cost on a quiet underlying."""
        drag = leveraged_drag(15.0, 3.0, 60, 0.0095)
        assert drag["carry_annual_pct"] > drag["rebalancing_annual_pct"]

    def test_rebalancing_dominates_at_high_volatility(self):
        drag = leveraged_drag(50.0, 3.0, 60, 0.0095)
        assert drag["rebalancing_annual_pct"] > drag["carry_annual_pct"]

    def test_carry_is_never_zero(self):
        """Even at zero volatility you still pay fees and financing."""
        drag = leveraged_drag(0.0, 3.0, 252, 0.0095)
        assert drag["rebalancing_annual_pct"] == 0.0
        assert drag["carry_annual_pct"] > 5.0

    def test_rebalancing_matches_the_closed_form(self):
        # ((9-3)/2) * 0.30^2 = 0.27 -> 27% per year
        drag = leveraged_drag(30.0, 3.0, 252, 0.0)
        assert drag["rebalancing_annual_pct"] == pytest.approx(27.0, abs=0.1)

    def test_zero_leverage_has_no_rebalancing_cost(self):
        assert leveraged_drag(30.0, 1.0, 60, 0.0)["rebalancing_annual_pct"] == 0.0

    def test_zero_hold_costs_nothing(self):
        assert leveraged_drag(30.0, 3.0, 0, 0.0095)["expected_cost_pct"] == 0.0


class TestLeveragedTicket:
    def test_reports_expected_decay(self):
        ticket = build_ticket(entry(symbol="QQQ"), instrument="leveraged_etf")
        assert any("Expected decay" in note for note in ticket.cost_notes)

    def test_explains_that_decay_is_paid_regardless_of_direction(self):
        ticket = build_ticket(entry(symbol="QQQ"), instrument="leveraged_etf")
        assert any("whether or not the direction is right" in n for n in ticket.cost_notes)

    def test_warns_when_decay_eats_the_projected_move(self):
        """A small projected move over a long hold is a bad leveraged trade."""
        data = entry(symbol="QQQ", spot=180.0, parameters=[
            param("head_shoulders", -8.5, detail={
                "neckline_now": 182.0, "target": 179.0, "expected_hold_days": 120,
            }),
            param("vol_risk_premium", 6.0, detail={"realised_vol": 55.0}),
        ])
        ticket = build_ticket(data, instrument="leveraged_etf")
        assert any("Decay eats most of the edge" in w for w in ticket.warnings)

    def test_flags_missing_volatility_rather_than_guessing(self):
        data = entry(symbol="QQQ", parameters=[param("rsi", 3.0)])
        ticket = build_ticket(data, instrument="leveraged_etf")
        assert any("could not be estimated" in w for w in ticket.warnings)
