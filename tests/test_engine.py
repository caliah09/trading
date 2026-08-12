"""Tests for composite scoring, the conviction gate, redistribution and ranking.

This is the module that decides what outranks what, so the invariants it must
hold are asserted directly: scores stay in [0, 100], contributions sum to the
composite, missing data is redistributed rather than scored, and the ordering is
deterministic.
"""

from __future__ import annotations

import pytest

from screener.config import ScreenerConfig
from screener.scoring.engine import (
    DirectionRead,
    TickerScore,
    compose,
    derive_direction,
    rank,
)
from screener.scoring.parameters import ParameterResult


def stock_param(key, signal, weight=5.0, status="ok") -> ParameterResult:
    return ParameterResult(
        key=key, label=key.upper(), layer="stock", status=status,
        weight=weight, signal=signal if status == "ok" else None,
        raw_value=0.0, explanation="test",
    )


def options_param(key, signal, weight=5.0, status="ok") -> ParameterResult:
    return ParameterResult(
        key=key, label=key.upper(), layer="options", status=status,
        weight=weight, signal=signal if status == "ok" else None,
        raw_value=0.0, explanation="test",
    )


@pytest.fixture
def config() -> ScreenerConfig:
    return ScreenerConfig()


# ==========================================================================
# Direction
# ==========================================================================

class TestDeriveDirection:
    def test_all_bullish_is_a_call_lean(self):
        read = derive_direction([stock_param("rsi", 0.8), stock_param("macd", 0.9)])
        assert read.direction == "call"
        assert read.conviction > 0.8
        assert read.multiplier == 1

    def test_all_bearish_is_a_put_lean(self):
        read = derive_direction([stock_param("rsi", -0.8), stock_param("macd", -0.9)])
        assert read.direction == "put"
        assert read.conviction > 0.8
        assert read.multiplier == -1

    def test_conflicting_signals_cancel_to_no_edge(self):
        """Disagreement must read as 'no edge', not as the loudest indicator."""
        read = derive_direction([stock_param("rsi", 0.9), stock_param("macd", -0.9)])
        assert read.direction == "neutral"
        assert read.conviction < 0.05

    def test_weights_decide_the_direction(self):
        read = derive_direction([
            stock_param("rsi", -0.9, weight=10.0),
            stock_param("macd", 0.5, weight=1.0),
        ])
        assert read.direction == "put"

    def test_missing_parameters_are_ignored(self):
        read = derive_direction([
            stock_param("rsi", 0.8),
            stock_param("macd", None, status="insufficient_data"),
        ])
        assert read.direction == "call"
        assert read.conviction == pytest.approx(0.8)

    def test_no_usable_parameters_is_neutral(self):
        read = derive_direction([stock_param("rsi", None, status="insufficient_data")])
        assert read.direction == "neutral"
        assert read.conviction == 0.0

    def test_conviction_bounded(self):
        read = derive_direction([stock_param("rsi", 1.0), stock_param("macd", 1.0)])
        assert 0.0 <= read.conviction <= 1.0


# ==========================================================================
# Composition
# ==========================================================================

class TestCompose:
    def test_contributions_sum_to_composite(self, config):
        """The breakdown the UI shows must actually add up."""
        stock = [stock_param("rsi", 0.6, 8), stock_param("macd", 0.4, 7)]
        options = [options_param("iv_rank", 0.8, 6)]
        read = derive_direction(stock)
        score = compose("T", stock, options, read, config)
        total = sum(p.contribution for p in score.parameters)
        assert total == pytest.approx(score.composite, abs=1e-9)

    def test_perfect_alignment_scores_one_hundred(self, config):
        stock = [stock_param("rsi", 1.0, 8), stock_param("macd", 1.0, 7)]
        options = [options_param("iv_rank", 1.0, 6), options_param("dte_fit", 1.0, 4)]
        read = derive_direction(stock)
        score = compose("T", stock, options, read, config)
        assert score.composite == pytest.approx(100.0)

    def test_score_is_bounded(self, config):
        for signal in (-1.0, -0.5, 0.0, 0.5, 1.0):
            stock = [stock_param("rsi", signal, 8)]
            options = [options_param("iv_rank", 1.0, 6)]
            score = compose("T", stock, options, derive_direction(stock), config)
            assert 0.0 <= score.composite <= 100.0

    def test_bearish_setup_scores_as_high_as_bullish(self, config):
        """Bidirectional by design: a strong bear setup is a strong candidate."""
        bull = [stock_param("rsi", 0.9, 8), stock_param("macd", 0.9, 7)]
        bear = [stock_param("rsi", -0.9, 8), stock_param("macd", -0.9, 7)]
        bull_score = compose("B", bull, [], derive_direction(bull), config)
        bear_score = compose("R", bear, [], derive_direction(bear), config)
        assert bull_score.composite == pytest.approx(bear_score.composite)
        assert bull_score.direction == "call"
        assert bear_score.direction == "put"

    def test_disagreeing_parameter_contributes_negatively(self, config):
        """A dissenting indicator must be visible as a negative contribution."""
        stock = [stock_param("rsi", 0.9, 8), stock_param("macd", -0.5, 4)]
        score = compose("T", stock, [], derive_direction(stock), config)
        by_key = {p.key: p for p in score.parameters}
        assert score.direction == "call"
        assert by_key["rsi"].contribution > 0
        assert by_key["macd"].contribution < 0

    def test_options_contributions_are_never_negative(self, config):
        """Options parameters measure expression quality, so they only add."""
        stock = [stock_param("rsi", -0.9, 8)]
        options = [options_param("iv_rank", 0.2, 6)]
        score = compose("T", stock, options, derive_direction(stock), config)
        by_key = {p.key: p for p in score.parameters}
        assert by_key["iv_rank"].contribution >= 0


class TestConvictionGate:
    def test_gate_suppresses_options_on_a_flat_chart(self):
        """A perfect contract on a no-signal stock must not manufacture a score."""
        config = ScreenerConfig(conviction_gate=True)
        stock = [stock_param("rsi", 0.0, 8), stock_param("macd", 0.0, 7)]
        options = [options_param("iv_rank", 1.0, 6), options_param("dte_fit", 1.0, 4)]
        score = compose("T", stock, options, derive_direction(stock), config)
        assert score.composite < 1.0

    def test_gate_off_lets_options_stand_alone(self):
        config = ScreenerConfig(conviction_gate=False)
        stock = [stock_param("rsi", 0.0, 8), stock_param("macd", 0.0, 7)]
        options = [options_param("iv_rank", 1.0, 6), options_param("dte_fit", 1.0, 4)]
        score = compose("T", stock, options, derive_direction(stock), config)
        assert score.composite > 30.0

    def test_gate_scales_proportionally(self):
        config = ScreenerConfig(conviction_gate=True)
        strong = [stock_param("rsi", 1.0, 8)]
        weak = [stock_param("rsi", 0.3, 8)]
        options = [options_param("iv_rank", 1.0, 6)]
        strong_score = compose("A", strong, list(options), derive_direction(strong), config)
        weak_score = compose("B", weak, [options_param("iv_rank", 1.0, 6)],
                             derive_direction(weak), config)
        strong_iv = next(p for p in strong_score.parameters if p.key == "iv_rank")
        weak_iv = next(p for p in weak_score.parameters if p.key == "iv_rank")
        assert strong_iv.contribution > weak_iv.contribution


class TestRedistribution:
    def test_missing_weight_is_redistributed(self, config):
        """Dropping a parameter must not shrink the achievable score."""
        complete = [stock_param("rsi", 1.0, 8), stock_param("macd", 1.0, 7)]
        partial = [stock_param("rsi", 1.0, 8),
                   stock_param("macd", None, 7, status="insufficient_data")]
        full = compose("A", complete, [], derive_direction(complete), config)
        reduced = compose("B", partial, [], derive_direction(partial), config)
        assert full.composite == pytest.approx(100.0)
        assert reduced.composite == pytest.approx(100.0)

    def test_missing_parameters_contribute_nothing(self, config):
        params = [stock_param("rsi", 1.0, 8),
                  stock_param("macd", None, 7, status="insufficient_data")]
        score = compose("T", params, [], derive_direction(params), config)
        missing = next(p for p in score.parameters if p.key == "macd")
        assert missing.contribution == 0.0
        assert missing.effective_weight == 0.0

    def test_completeness_is_reported(self, config):
        params = [stock_param("rsi", 1.0, 8),
                  stock_param("macd", None, 7, status="insufficient_data")]
        score = compose("T", params, [], derive_direction(params), config)
        assert score.completeness == "1/2"

    def test_not_applicable_also_redistributes(self, config):
        params = [stock_param("rsi", 1.0, 8)]
        options = [options_param("earnings", None, 4, status="not_applicable")]
        score = compose("SPY", params, options, derive_direction(params), config)
        assert score.composite == pytest.approx(100.0)
        assert score.completeness == "1/2"

    def test_nothing_computable_scores_zero(self, config):
        params = [stock_param("rsi", None, 8, status="insufficient_data")]
        score = compose("T", params, [], derive_direction(params), config)
        assert score.composite == 0.0
        assert score.direction == "neutral"
        assert score.direction_label == "No data"


# ==========================================================================
# Ranking
# ==========================================================================

def make_score(symbol, composite, dollar_volume=1e6, params=None) -> TickerScore:
    return TickerScore(
        symbol=symbol, composite=composite, direction="call",
        direction_label="Call lean", conviction=0.5, net_signal=0.5,
        parameters=params or [], dollar_volume=dollar_volume,
    )


class TestRank:
    def test_orders_best_to_worst(self, config):
        ordered = rank([make_score("A", 10), make_score("B", 90), make_score("C", 50)], config)
        assert [s.symbol for s in ordered] == ["B", "C", "A"]
        assert [s.rank for s in ordered] == [1, 2, 3]

    def test_tie_broken_by_heaviest_parameter(self, config):
        """RSI carries the top default weight, so its contribution decides."""
        strong = ParameterResult(key="rsi", label="RSI", layer="stock", status="ok",
                                 weight=8.0, signal=0.9, contribution=8.0, explanation="")
        weak = ParameterResult(key="rsi", label="RSI", layer="stock", status="ok",
                               weight=8.0, signal=0.3, contribution=3.0, explanation="")
        ordered = rank([make_score("LOW", 50.0, params=[weak]),
                        make_score("HIGH", 50.0, params=[strong])], config)
        assert [s.symbol for s in ordered] == ["HIGH", "LOW"]
        assert "RSI" in ordered[0].tie_break_note

    def test_tie_broken_by_liquidity(self, config):
        same = ParameterResult(key="rsi", label="RSI", layer="stock", status="ok",
                               weight=8.0, signal=0.5, contribution=5.0, explanation="")
        ordered = rank([
            make_score("THIN", 50.0, dollar_volume=1e5, params=[same]),
            make_score("DEEP", 50.0, dollar_volume=9e9, params=[dict_copy(same)]),
        ], config)
        assert [s.symbol for s in ordered] == ["DEEP", "THIN"]
        assert "dollar volume" in ordered[0].tie_break_note

    def test_total_tie_falls_back_to_alphabetical(self, config):
        ordered = rank([make_score("ZZZ", 50.0), make_score("AAA", 50.0)], config)
        assert [s.symbol for s in ordered] == ["AAA", "ZZZ"]
        assert "alphabetically" in ordered[0].tie_break_note

    def test_ordering_is_deterministic(self, config):
        scores = [make_score(sym, 50.0) for sym in ("D", "A", "C", "B")]
        first = [s.symbol for s in rank(list(scores), config)]
        second = [s.symbol for s in rank(list(reversed(scores)), config)]
        assert first == second

    def test_no_tie_note_when_scores_differ(self, config):
        ordered = rank([make_score("A", 90.0), make_score("B", 10.0)], config)
        assert all(s.tie_break_note is None for s in ordered)

    def test_empty_input(self, config):
        assert rank([], config) == []


def dict_copy(param: ParameterResult) -> ParameterResult:
    return ParameterResult(**{**param.__dict__})


def test_score_serialises_for_the_api(config):
    stock = [stock_param("rsi", 0.6, 8)]
    score = compose("T", stock, [], derive_direction(stock), config)
    payload = score.to_dict()
    assert payload["symbol"] == "T"
    assert "parameters" in payload
    assert payload["parameters"][0]["explanation"] == "test"
    assert isinstance(payload["composite"], float)
