"""Percent-suffixing known numeric fields for gatekeeper grounding."""

from __future__ import annotations

import json

import pytest

from app.modules.newspaper.investigation_store import (
    _stringify_percent_fields,
    store_investigation_findings,
)


def test_stringify_percent_fields_adds_percent_suffix() -> None:
    """Suffixes a known percent field with '%' while leaving other fields untouched."""
    result = {"round": 123, "online_stake_algo": 1.0, "online_pct": 92.35}
    out = _stringify_percent_fields(result)
    assert out["online_pct"] == "92.35%"
    assert out["round"] == 123  # non-percent fields untouched
    assert out["online_stake_algo"] == 1.0


def test_stringify_percent_fields_covers_known_keys() -> None:
    """Suffixes every known percent-field key but leaves an unlisted lookalike key untouched."""
    result = {
        "change_24h_pct": 1.37,
        "week_change_pct": -3.5,
        "share_pct": 11.2112,
        "unrelated_pct_like_name": 5,  # not in the known-keys allowlist
    }
    out = _stringify_percent_fields(result)
    assert out["change_24h_pct"] == "1.37%"
    assert out["week_change_pct"] == "-3.5%"
    assert out["share_pct"] == "11.2112%"
    assert out["unrelated_pct_like_name"] == 5


def test_stringify_percent_fields_leaves_non_dict_untouched() -> None:
    """Returns non-dict input (string, None) unchanged instead of raising."""
    assert _stringify_percent_fields("not a dict") == "not a dict"
    assert _stringify_percent_fields(None) is None


def test_stringify_percent_fields_ignores_non_numeric_percent_value() -> None:
    """Leaves a None percent-field value as None instead of suffixing it."""
    result = {"online_pct": None}
    out = _stringify_percent_fields(result)
    assert out["online_pct"] is None


def test_store_investigation_findings_persists_percent_suffixed_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stored result_json must carry the '%' suffix — this is what lets the gatekeeper's numeric-entailment check later recognize a genuine server-computed percentage as a grounding anchor."""
    captured: list[tuple] = []

    class _FakeSession:
        def prepare(self, cql: str) -> str:
            return cql

        def execute(self, _stmt: str, params: tuple) -> None:
            captured.append(params)

    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: _FakeSession())
    c.prepare_cached.cache_clear()

    trace = [
        {
            "tool": "get_asset_holder_share",
            "arguments": {"asset_id": 1732165149, "address": "CREATOR"},
            "result": {"share_pct": 11.2112, "asset_id": 1732165149},
        }
    ]
    n = store_investigation_findings(
        service_id="compx-io", source_url="https://compx.io/", trace=trace
    )
    assert n == 1
    assert len(captured) == 1
    result_json = captured[0][6]
    stored = json.loads(result_json)
    assert stored["share_pct"] == "11.2112%"
