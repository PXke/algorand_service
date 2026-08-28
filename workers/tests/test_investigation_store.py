"""Percent-suffixing known numeric fields for gatekeeper grounding."""

from __future__ import annotations

import json

import pytest

from app.modules.newspaper.investigation_store import (
    _stringify_percent_fields,
    load_investigation_trace,
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


def test_store_investigation_findings_keeps_calls_past_the_old_25_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Root-caused 2026-08-28 (Lumi Rogue): investigation_findings used to hard-cap at the first 25 tool calls, so anything from the write stage onward (which runs AFTER research) contributed zero grounding anchors to the gatekeeper's numeric-entailment check -- a well-researched article could score a false-positive-fabrication purely from this. A compose can run up to LLM_MAX_TOOL_ROUNDS=48 rounds; 60 calls here must all persist."""
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
        {"tool": "fetch_url", "arguments": {"url": f"https://x.io/{i}"}, "result": {"n": i}}
        for i in range(60)
    ]
    n = store_investigation_findings(service_id="svc", source_url="https://x.io/", trace=trace)
    assert n == 60
    assert len(captured) == 60


def test_store_investigation_findings_result_cap_exceeds_the_old_8000_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A big fetch_url dump (real page text, likely to contain the actual figures a later article cites) must not be truncated back down to the old 8000-char cap."""
    captured: list[tuple] = []

    class _FakeSession:
        def prepare(self, cql: str) -> str:
            return cql

        def execute(self, _stmt: str, params: tuple) -> None:
            captured.append(params)

    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: _FakeSession())
    c.prepare_cached.cache_clear()

    big_text = "x" * 12_000
    trace = [{"tool": "fetch_url", "arguments": {}, "result": {"text": big_text}}]
    store_investigation_findings(service_id="svc", source_url="https://x.io/", trace=trace)
    stored = json.loads(captured[0][6])
    assert len(stored["text"]) == 12_000


def test_load_investigation_trace_reads_past_the_old_25_row_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The read side must request at least as many rows as the write side can now store, or fixing the write-side cap alone re-truncates on the way back out."""
    captured_limit: list[int] = []

    class _Row:
        def __init__(self, i: int) -> None:
            self.tool = "fetch_url"
            self.arguments = "{}"
            self.result_json = f'{{"n": {i}}}'

    class _FakeSession:
        def prepare(self, cql: str) -> str:
            return cql

        def execute(self, _stmt: object, params: tuple) -> list[_Row]:
            captured_limit.append(params[1])
            return [_Row(i) for i in range(params[1])]

    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: _FakeSession())
    c.prepare_cached.cache_clear()
    monkeypatch.setattr("app.core.config.INVESTIGATION_TRACE_MAX_ENTRIES", 200)

    trace = load_investigation_trace("svc")
    assert captured_limit == [200]
    assert trace.count("fetch_url(") == 200
