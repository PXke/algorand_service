"""_parse_relevance_components: best-effort JSON-decode of content_relevance_components.

Same shape of guard as the rest of the domain-list assembly in
_admin_domains_full_list (see test_admin_domain_sort.py) -- a single
malformed or pre-existing row must never crash the whole admin Domains
list, so this must fail open to {} rather than raise.
"""

from __future__ import annotations

from app.modules.admin.api.routes import _parse_relevance_components


def test_empty_string_yields_empty_dict() -> None:
    """A domain scored before this field existed has no content_relevance_components at all -- meta.get(...) returns ""."""
    assert _parse_relevance_components("") == {}


def test_valid_json_dict_round_trips() -> None:
    """A real score_page()-produced components dict decodes with float values."""
    raw = '{"algorand_keywords": 0.2, "exact_mention": 0.15}'
    assert _parse_relevance_components(raw) == {"algorand_keywords": 0.2, "exact_mention": 0.15}


def test_malformed_json_fails_open_to_empty_dict() -> None:
    """Truncated/corrupt JSON must not raise -- {} instead."""
    assert _parse_relevance_components("{not valid json") == {}


def test_non_dict_json_fails_open_to_empty_dict() -> None:
    """A JSON value that parses but isn't an object (e.g. a bare list or number) isn't a components mapping."""
    assert _parse_relevance_components("[1, 2, 3]") == {}
    assert _parse_relevance_components("42") == {}


def test_non_numeric_values_are_dropped_not_fatal() -> None:
    """A dict with one bad value drops just that key instead of failing the whole parse."""
    raw = '{"algorand_keywords": 0.2, "bad_key": "not-a-number"}'
    assert _parse_relevance_components(raw) == {"algorand_keywords": 0.2}
