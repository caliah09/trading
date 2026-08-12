"""Tests for configuration handling, validation and fingerprinting."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from screener.config import (
    ConfigError,
    ParameterConfig,
    ScreenerConfig,
    normalise_watchlist,
)
from screener.param_specs import ALL_SPECS


class TestDefaults:
    def test_every_parameter_is_seeded(self):
        config = ScreenerConfig()
        assert set(config.parameters) == {s.key for s in ALL_SPECS}

    def test_default_weights_match_registry(self):
        config = ScreenerConfig()
        for spec in ALL_SPECS:
            assert config.parameters[spec.key].weight == spec.default_weight

    def test_thresholds_seeded_from_registry(self):
        config = ScreenerConfig()
        assert config.threshold("rsi", "oversold") == 30.0
        assert config.threshold("rsi", "overbought") == 70.0

    def test_missing_threshold_falls_back_to_default(self):
        """A config written before a threshold existed must still load."""
        config = ScreenerConfig()
        config.parameters["rsi"] = ParameterConfig(weight=8.0, thresholds={})
        assert config.threshold("rsi", "oversold") == 30.0

    def test_unknown_threshold_raises(self):
        config = ScreenerConfig()
        with pytest.raises(KeyError):
            config.threshold("rsi", "not_a_threshold")


class TestValidation:
    def test_rejects_bad_posture(self):
        with pytest.raises(ConfigError, match="posture"):
            ScreenerConfig(posture="hedge")

    def test_rejects_zero_top_n(self):
        with pytest.raises(ConfigError, match="top_n"):
            ScreenerConfig(top_n=0)

    def test_rejects_out_of_range_weight(self):
        config = ScreenerConfig()
        with pytest.raises(ConfigError, match="0-10"):
            ScreenerConfig(parameters={**config.parameters,
                                       "rsi": ParameterConfig(weight=11.0)})

    def test_rejects_all_zero_weights(self):
        """A config with nothing enabled cannot produce a ranking."""
        params = {s.key: ParameterConfig(weight=0.0) for s in ALL_SPECS}
        with pytest.raises(ConfigError, match="at least one parameter|At least one"):
            ScreenerConfig(parameters=params)

    def test_rejects_inverted_delta_band(self):
        config = ScreenerConfig()
        bad = replace(config.buy_target, delta_low=0.6, delta_high=0.4)
        with pytest.raises(ConfigError, match="delta_low"):
            ScreenerConfig(buy_target=bad)

    def test_rejects_inverted_dte_band(self):
        config = ScreenerConfig()
        bad = replace(config.buy_target, dte_low=90, dte_high=45)
        with pytest.raises(ConfigError, match="dte_low"):
            ScreenerConfig(buy_target=bad)

    def test_rejects_delta_of_one(self):
        config = ScreenerConfig()
        bad = replace(config.buy_target, delta_high=1.0)
        with pytest.raises(ConfigError):
            ScreenerConfig(buy_target=bad)


class TestWatchlist:
    def test_normalises_case_and_whitespace(self):
        assert normalise_watchlist([" aapl ", "Msft"]) == ["AAPL", "MSFT"]

    def test_deduplicates_preserving_order(self):
        assert normalise_watchlist(["AAPL", "aapl", "MSFT"]) == ["AAPL", "MSFT"]

    def test_drops_empty_entries(self):
        assert normalise_watchlist(["AAPL", "", "  "]) == ["AAPL"]

    def test_applied_on_construction(self):
        assert ScreenerConfig(watchlist=["aapl", "AAPL", "spy"]).watchlist == ["AAPL", "SPY"]


class TestActiveWeights:
    def test_zero_weight_is_inactive(self):
        config = ScreenerConfig()
        config.parameters["rsi"].weight = 0.0
        assert not config.active("rsi")

    def test_disabled_flag_is_inactive(self):
        config = ScreenerConfig()
        config.parameters["rsi"].enabled = False
        assert not config.active("rsi")

    def test_total_excludes_inactive(self):
        config = ScreenerConfig()
        before = config.active_weight_total()
        config.parameters["rsi"].enabled = False
        assert config.active_weight_total() == before - 8.0


class TestFingerprint:
    def test_stable_across_identical_configs(self):
        assert ScreenerConfig().fingerprint == ScreenerConfig().fingerprint

    def test_changes_with_weight(self):
        a = ScreenerConfig()
        b = ScreenerConfig()
        b.parameters["rsi"].weight = 3.0
        assert a.fingerprint != b.fingerprint

    def test_changes_with_watchlist(self):
        assert ScreenerConfig(watchlist=["AAPL"]).fingerprint != \
               ScreenerConfig(watchlist=["MSFT"]).fingerprint

    def test_insensitive_to_key_order(self):
        config = ScreenerConfig()
        shuffled = json.loads(config.canonical_json())
        shuffled["parameters"] = dict(reversed(list(shuffled["parameters"].items())))
        assert ScreenerConfig.from_dict(shuffled).fingerprint == config.fingerprint


class TestRoundTrip:
    def test_dict_round_trip_preserves_fingerprint(self):
        config = ScreenerConfig(watchlist=["AAPL", "TSLA"], posture="sell", top_n=7)
        assert ScreenerConfig.from_dict(config.to_dict()).fingerprint == config.fingerprint

    def test_file_round_trip(self, tmp_path):
        config = ScreenerConfig(watchlist=["NVDA"], top_n=3)
        path = tmp_path / "cfg.json"
        config.save(path)
        assert ScreenerConfig.load(path).fingerprint == config.fingerprint

    def test_load_missing_file_returns_defaults(self, tmp_path):
        assert ScreenerConfig.load(tmp_path / "absent.json").watchlist == \
               ScreenerConfig().watchlist

    def test_unknown_parameters_are_dropped(self):
        """A config from a newer version must not crash an older one."""
        data = ScreenerConfig().to_dict()
        data["parameters"]["quantum_flux"] = {"weight": 5.0, "enabled": True, "thresholds": {}}
        assert "quantum_flux" not in ScreenerConfig.from_dict(data).parameters

    def test_target_selection_follows_posture(self):
        buying = ScreenerConfig(posture="buy")
        selling = ScreenerConfig(posture="sell")
        assert buying.target().delta_low == buying.buy_target.delta_low
        assert selling.target().delta_low == selling.sell_target.delta_low
