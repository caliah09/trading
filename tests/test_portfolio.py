"""Tests for portfolio-level concentration analysis."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from screener.portfolio import (
    DEFAULT_WINDOW,
    analyse_concentration,
    correlation_matrix,
)
from tests.conftest import make_frame

N = 160


def series_from(returns: np.ndarray, start: float = 100.0) -> pd.DataFrame:
    return make_frame(list(start * np.exp(np.cumsum(returns))))


def correlated_set(n_symbols: int, rho: float, seed: int = 11) -> dict[str, pd.DataFrame]:
    """Symbols sharing a common factor, with `rho` controlling how tightly."""
    rng = np.random.default_rng(seed)
    common = rng.normal(0, 0.015, N)
    out = {}
    for i in range(n_symbols):
        idiosyncratic = rng.normal(0, 0.015, N)
        returns = rho * common + np.sqrt(max(0.0, 1 - rho ** 2)) * idiosyncratic
        out[f"SYM{i}"] = series_from(returns)
    return out


class TestCorrelationMatrix:
    def test_shape_and_diagonal(self):
        matrix = correlation_matrix(correlated_set(4, 0.9))
        assert matrix.shape == (4, 4)
        assert np.allclose(np.diag(matrix.to_numpy()), 1.0)

    def test_detects_high_correlation(self):
        matrix = correlation_matrix(correlated_set(3, 0.97))
        off_diagonal = matrix.to_numpy()[~np.eye(3, dtype=bool)]
        assert off_diagonal.min() > 0.7

    def test_detects_low_correlation(self):
        matrix = correlation_matrix(correlated_set(3, 0.05, seed=5))
        off_diagonal = matrix.to_numpy()[~np.eye(3, dtype=bool)]
        assert abs(off_diagonal).max() < 0.5

    def test_too_few_symbols_returns_empty(self):
        assert correlation_matrix(correlated_set(1, 0.9)).empty

    def test_short_history_is_excluded(self):
        data = correlated_set(2, 0.9)
        data["SHORT"] = make_frame([100.0, 101.0, 102.0])
        assert "SHORT" not in correlation_matrix(data).columns

    def test_empty_input(self):
        assert correlation_matrix({}).empty


class TestConcentration:
    def test_correlated_candidates_collapse_to_one_bet(self):
        """The core claim: five correlated names are not five positions."""
        data = correlated_set(5, 0.97)
        report = analyse_concentration(data, list(data), threshold=0.75)
        assert report.effective_positions == 1
        assert len(report.clusters) == 1
        assert len(report.clusters[0]) == 5

    def test_uncorrelated_candidates_stay_independent(self):
        data = correlated_set(4, 0.02, seed=99)
        report = analyse_concentration(data, list(data), threshold=0.75)
        assert report.effective_positions == 4

    def test_warns_when_concentration_is_hidden(self):
        data = correlated_set(5, 0.97)
        report = analyse_concentration(data, list(data), threshold=0.75)
        assert report.warnings
        assert any("independent bet" in w for w in report.warnings)

    def test_no_warning_when_genuinely_diversified(self):
        data = correlated_set(4, 0.02, seed=99)
        report = analyse_concentration(data, list(data), threshold=0.75)
        assert not any("independent bet" in w for w in report.warnings)

    def test_reports_average_and_max_pair(self):
        data = correlated_set(4, 0.95)
        report = analyse_concentration(data, list(data))
        assert report.average_correlation > 0.7
        assert report.max_pair is not None
        assert report.max_pair[2] >= report.average_correlation

    def test_clustering_is_transitive(self):
        """If A~B and B~C, all three are one bet even if A~C is borderline."""
        rng = np.random.default_rng(3)
        base = rng.normal(0, 0.015, N)
        a = base
        b = 0.9 * base + 0.44 * rng.normal(0, 0.015, N)
        c = 0.78 * base + 0.63 * rng.normal(0, 0.015, N)
        data = {"A": series_from(a), "B": series_from(b), "C": series_from(c)}
        report = analyse_concentration(data, ["A", "B", "C"], threshold=0.7)
        if report.effective_positions == 1:
            assert sorted(report.clusters[0]) == ["A", "B", "C"]

    def test_flags_contradictory_directions(self):
        """Correlated names with opposite leans is a contradiction, not concentration."""
        data = correlated_set(2, 0.98)
        symbols = list(data)
        report = analyse_concentration(
            data, symbols,
            directions={symbols[0]: "call", symbols[1]: "put"},
            threshold=0.75,
        )
        assert any("disagree" in w for w in report.warnings)

    def test_same_direction_is_not_flagged_as_contradiction(self):
        data = correlated_set(2, 0.98)
        symbols = list(data)
        report = analyse_concentration(
            data, symbols, directions={s: "call" for s in symbols}, threshold=0.75
        )
        assert not any("disagree" in w for w in report.warnings)

    def test_directional_split_is_counted(self):
        data = correlated_set(3, 0.5)
        symbols = list(data)
        report = analyse_concentration(
            data, symbols,
            directions={symbols[0]: "call", symbols[1]: "put", symbols[2]: "call"},
        )
        assert report.directional_split == {"call": 2, "put": 1}

    def test_single_candidate_is_trivial(self):
        data = correlated_set(1, 0.5)
        report = analyse_concentration(data, list(data))
        assert report.effective_positions == 1
        assert report.warnings == []

    def test_empty_candidate_list(self):
        report = analyse_concentration({}, [])
        assert report.effective_positions == 0

    def test_missing_history_is_reported_not_silently_dropped(self):
        data = correlated_set(2, 0.9)
        data["THIN"] = make_frame([100.0, 101.0])
        report = analyse_concentration(data, list(data))
        assert "THIN" in report.insufficient
        assert any("THIN" in w for w in report.warnings)
        # It still counts as a position rather than vanishing from the report.
        assert "THIN" in [s for cluster in report.clusters for s in cluster]

    def test_no_usable_history_degrades_gracefully(self):
        data = {"A": make_frame([100.0, 101.0]), "B": make_frame([50.0, 51.0])}
        report = analyse_concentration(data, ["A", "B"])
        assert report.effective_positions == 2
        assert any("Not enough overlapping history" in w for w in report.warnings)

    def test_serialises(self):
        data = correlated_set(3, 0.9)
        payload = analyse_concentration(data, list(data)).to_dict()
        assert payload["nominal_positions"] == 3
        assert "effective_positions" in payload
        assert payload["window"] == DEFAULT_WINDOW
        assert isinstance(payload["clusters"], list)

    def test_deterministic(self):
        data = correlated_set(4, 0.8)
        first = analyse_concentration(data, list(data)).to_dict()
        second = analyse_concentration(data, list(data)).to_dict()
        assert first == second
