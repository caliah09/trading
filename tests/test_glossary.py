"""The Learn tab's content must stay complete and well-formed.

A glossary is the kind of thing that rots quietly: an entry loses its
``seen_in``, a category empties out, two entries end up with the same term and
one silently shadows the other in search. None of that breaks the app, so
nothing would catch it except a test.
"""

from __future__ import annotations

import pytest

from screener.glossary import CATEGORIES, GLOSSARY, by_category, entry

CATEGORY_KEYS = {key for key, _, _ in CATEGORIES}


class TestStructure:
    def test_every_entry_belongs_to_a_declared_category(self):
        unknown = {e.category for e in GLOSSARY} - CATEGORY_KEYS
        assert not unknown, f"entries in undeclared categories: {unknown}"

    def test_no_category_is_empty(self):
        """An empty section renders as a heading with nothing under it."""
        for group in by_category():
            assert group["entries"], f"category {group['key']!r} has no entries"

    def test_terms_are_unique(self):
        terms = [e.term for e in GLOSSARY]
        duplicates = {t for t in terms if terms.count(t) > 1}
        assert not duplicates, f"duplicate terms: {duplicates}"

    def test_categories_render_in_declared_order(self):
        assert [g["key"] for g in by_category()] == [k for k, _, _ in CATEGORIES]

    def test_lookup_finds_an_entry(self):
        assert entry("Delta") is not None

    def test_lookup_returns_none_for_an_unknown_term(self):
        assert entry("Gamma Squeeze Supreme") is None


@pytest.mark.parametrize("item", GLOSSARY, ids=lambda e: e.term)
class TestEachEntry:
    def test_has_a_plain_definition(self, item):
        assert item.plain.strip()
        # Long enough to be an explanation rather than a restatement.
        assert len(item.plain) > 60, f"{item.term}: plain text looks like a stub"

    def test_says_where_it_appears(self, item):
        """An explanation you cannot connect to the screen is trivia."""
        assert item.seen_in.strip(), f"{item.term}: no seen_in"

    def test_detail_does_not_merely_repeat_plain(self, item):
        if item.detail:
            assert item.detail.strip() != item.plain.strip()

    def test_serialises_completely(self, item):
        payload = item.to_dict()
        assert set(payload) == {
            "term", "category", "plain", "detail", "aka", "seen_in", "caution",
        }
        assert isinstance(payload["aka"], list)


class TestTheContentItself:
    """A few claims the app depends on being stated correctly."""

    def test_the_expiry_entry_leads_with_the_correction(self):
        """The costliest misunderstanding, so it must not be buried."""
        item = entry("Expiration is a deadline, not a payday")
        assert item is not None
        assert item.category == "start"
        assert "not" in item.plain.lower()

    def test_defined_risk_does_not_imply_small_risk(self):
        item = entry("Defined risk")
        assert item.caution, "capped loss must not be presented as a safe loss"
        assert "100%" in item.caution

    def test_expected_hold_warns_about_the_dte_mismatch(self):
        item = entry("Expected hold")
        assert item.caution
        assert "session" in item.caution.lower()


def test_the_api_serves_the_glossary(tmp_path):
    from fastapi.testclient import TestClient

    from screener.api.app import create_app

    app = create_app(db_path=tmp_path / "t.sqlite", provider_name="synthetic",
                     start_scheduler=False)
    payload = TestClient(app).get("/api/glossary").json()

    assert [g["key"] for g in payload["categories"]] == [k for k, _, _ in CATEGORIES]
    served = sum(len(g["entries"]) for g in payload["categories"])
    assert served == len(GLOSSARY)
    for group in payload["categories"]:
        assert group["label"] and group["blurb"]
