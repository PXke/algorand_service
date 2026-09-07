"""Percent-suffixing known numeric fields for gatekeeper grounding."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.modules.newspaper.investigation_store import (
    _stringify_percent_fields,
    format_prior_search_x_block,
    load_investigation_trace,
    load_prior_search_x_findings,
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


class _Row:
    """A fake investigation_findings row with settable tool/arguments/result_json/created_at."""

    def __init__(
        self, tool: str, arguments: dict, result: dict, created_at: datetime | None = None
    ) -> None:
        self.tool = tool
        self.arguments = json.dumps(arguments)
        self.result_json = json.dumps(result)
        self.created_at = created_at


class _FakeSession:
    def __init__(self, rows: list[_Row]) -> None:
        self._rows = rows

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, _stmt: object, _params: tuple) -> list[_Row]:
        return self._rows


def _patch_session(monkeypatch: pytest.MonkeyPatch, rows: list[_Row]) -> None:
    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: _FakeSession(rows))
    c.prepare_cached.cache_clear()


def test_load_prior_search_x_findings_filters_to_search_x_tool_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only tool == "search_x" rows are surfaced -- every other tool call in the shared investigation_findings evidence trail is ignored."""
    rows = [
        _Row("fetch_url", {"url": "https://x.io"}, {"text": "page"}),
        _Row(
            "search_x",
            {"query": "algorand quantum"},
            {"query": "algorand quantum", "posts": [{"text": "hit", "likes": 1}]},
        ),
    ]
    _patch_session(monkeypatch, rows)

    findings = load_prior_search_x_findings("svc")
    assert len(findings) == 1
    assert findings[0]["query"] == "algorand quantum"


def test_load_prior_search_x_findings_dedupes_by_normalized_query_keeping_newest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rows arrive newest-first (created_at DESC clustering); a later (older) row for the same normalized query is dropped, keeping only the most recent result."""
    rows = [
        _Row(  # newest
            "search_x",
            {"query": "Algorand  Quantum"},
            {"posts": [{"text": "newest result"}]},
        ),
        _Row(  # older, same query modulo case/whitespace
            "search_x",
            {"query": "algorand quantum"},
            {"posts": [{"text": "stale result"}]},
        ),
    ]
    _patch_session(monkeypatch, rows)

    findings = load_prior_search_x_findings("svc")
    assert len(findings) == 1
    assert findings[0]["result"]["posts"][0]["text"] == "newest result"


def test_load_prior_search_x_findings_skips_error_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An error result is not a reusable finding (CLAUDE.md invariant 2.8) -- a row whose stored result carries an "error" key is skipped, not surfaced as if it were a real answer."""
    rows = [_Row("search_x", {"query": "algorand quantum"}, {"error": "X search not configured"})]
    _patch_session(monkeypatch, rows)

    assert load_prior_search_x_findings("svc") == []


def test_load_prior_search_x_findings_fails_open_on_cassandra_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A Cassandra read failure degrades to "nothing to reinject", never raises into the recompose it's meant to help."""

    def _raise() -> None:
        raise ConnectionError("cassandra down")

    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", _raise)

    assert load_prior_search_x_findings("svc") == []


def test_load_prior_search_x_findings_empty_service_id_short_circuits() -> None:
    """An empty service_id is a usage no-op, not a query -- matches load_investigation_trace's own guard."""
    assert load_prior_search_x_findings("") == []


def test_format_prior_search_x_block_renders_query_and_posts() -> None:
    """The rendered block names the query, includes post text/engagement, and carries a clear "this may be stale" label so the writer treats it appropriately."""
    findings = [
        {
            "query": "algorand quantum",
            "result": {
                "posts": [
                    {"text": "Algorand ships v5.0.0", "likes": 42, "reposts": 7, "replies": 3}
                ]
            },
        }
    ]
    block = format_prior_search_x_block(findings)
    assert "algorand quantum" in block
    assert "Algorand ships v5.0.0" in block
    assert "42" in block
    assert "PRIOR X (TWITTER) RESEARCH" in block
    assert "may be DAYS OR WEEKS OLD" in block


def test_format_prior_search_x_block_empty_findings_returns_empty_string() -> None:
    """No prior findings -> empty block, so callers can pass it straight through as enrichment_block with no conditional."""
    assert format_prior_search_x_block([]) == ""


def test_load_prior_search_x_findings_carries_created_at_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The row's own clustering timestamp reaches the returned finding — the block's "judge this against today's date" instruction depends on it, and until 2026-09-05 the LIST statement never selected it, so the instruction's one required input could never be supplied."""
    fetched = datetime(2026, 8, 30, 14, 5, tzinfo=UTC)
    rows = [
        _Row(
            "search_x",
            {"query": "algorand quantum"},
            {"posts": [{"text": "hit"}]},
            created_at=fetched,
        )
    ]
    _patch_session(monkeypatch, rows)

    findings = load_prior_search_x_findings("svc")
    assert len(findings) == 1
    assert findings[0]["created_at"] == fetched


def test_format_prior_search_x_block_renders_per_query_fetch_date() -> None:
    """Each query section is labeled with the UTC date its result was actually fetched, so the writer can judge the data's age instead of being told to judge an age it was never given."""
    findings = [
        {
            "query": "algorand quantum",
            "result": {"posts": [{"text": "hit", "likes": 1, "reposts": 0, "replies": 0}]},
            "created_at": datetime(2026, 8, 30, 14, 5, tzinfo=UTC),
        }
    ]
    block = format_prior_search_x_block(findings)
    assert '### X search: "algorand quantum" (fetched 2026-08-30 UTC)' in block
    assert "labeled with the UTC date it was actually fetched" in block


def test_format_prior_search_x_block_omits_date_when_created_at_missing() -> None:
    """A finding with no created_at (an old caller/test double) renders without a fetched label rather than fabricating a date or raising."""
    findings = [
        {
            "query": "algorand quantum",
            "result": {"posts": [{"text": "hit", "likes": 1, "reposts": 0, "replies": 0}]},
        }
    ]
    block = format_prior_search_x_block(findings)
    assert '### X search: "algorand quantum"\n' in block
    assert "fetched" not in block.split("\n\n", 2)[2]  # no per-query label in the sections
