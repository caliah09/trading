"""The explanatory registry must stay in step with the parameter registry.

A parameter added without an explanation would surface in the UI as a row that
expands into nothing — the one thing the drill-down exists to prevent.
"""

from __future__ import annotations

import pytest

from screener.param_docs import DEEP_DIVE, deep_dive, missing_keys
from screener.param_specs import ALL_SPECS, SPECS_BY_KEY


def test_every_parameter_has_an_explanation():
    assert missing_keys() == (), (
        f"parameters without a deep-dive entry: {missing_keys()}"
    )


def test_no_explanation_describes_a_parameter_that_does_not_exist():
    assert set(DEEP_DIVE) <= set(SPECS_BY_KEY)


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.key)
def test_each_entry_fills_every_field(spec):
    entry = deep_dive(spec.key)
    assert entry is not None
    for field in ("measures", "changes", "evidence"):
        text = getattr(entry, field)
        assert text and text.strip(), f"{spec.key}.{field} is empty"
        # Short enough to be a placeholder rather than an explanation.
        assert len(text) > 80, f"{spec.key}.{field} looks like a stub"


@pytest.mark.parametrize("spec", ALL_SPECS, ids=lambda s: s.key)
def test_to_dict_is_json_ready(spec):
    payload = deep_dive(spec.key).to_dict()
    assert set(payload) == {"measures", "changes", "evidence"}
    assert all(isinstance(v, str) for v in payload.values())


def test_deep_dive_returns_none_for_an_unknown_key():
    assert deep_dive("not_a_parameter") is None


def test_the_api_ships_the_explanations(tmp_path):
    """A client that cannot see the prose cannot render the drill-down."""
    from fastapi.testclient import TestClient

    from screener.api.app import create_app

    app = create_app(db_path=tmp_path / "t.sqlite", provider_name="synthetic",
                     start_scheduler=False)
    payload = TestClient(app).get("/api/meta").json()

    for param in payload["parameters"]:
        assert param["deep_dive"], f"{param['key']} shipped without its explanation"
        assert set(param["deep_dive"]) == {"measures", "changes", "evidence"}


def test_the_api_ships_the_universe_list(tmp_path):
    from fastapi.testclient import TestClient

    from screener.api.app import create_app
    from screener.universe import UNIVERSE_NAMES

    app = create_app(db_path=tmp_path / "t.sqlite", provider_name="synthetic",
                     start_scheduler=False)
    payload = TestClient(app).get("/api/meta").json()

    assert [u["key"] for u in payload["universes"]] == list(UNIVERSE_NAMES)
    for entry in payload["universes"]:
        assert entry["size"] >= 0
        assert entry["label"]
        assert entry["note"]
