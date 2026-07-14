from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

from app.modules.admin.stores.cassandra import AdminCassandraStore


class _FakeSession:
    """Mirrors the pattern in test_reject_domain_source.py: prepare() returns
    the raw CQL so execute() can branch on query text."""

    def __init__(self, rows: list[SimpleNamespace]) -> None:
        self._rows = rows
        self.resolve_calls: list[tuple] = []

    def prepare(self, cql):
        return cql

    def execute(self, query, params=()):
        q = " ".join(str(query).split())
        if q.startswith("SELECT") and "tool_suggestions" in q:
            return list(self._rows)
        if q.startswith("UPDATE") and "tool_suggestions" in q:
            self.resolve_calls.append(tuple(params))
            for r in self._rows:
                if r.suggestion_id == params[3]:
                    r.resolved = params[0]
        return None


def _row(capability: str, *, resolved: bool = False) -> SimpleNamespace:
    return SimpleNamespace(
        suggestion_id=uuid4(),
        created_at=datetime(2026, 7, 14, tzinfo=UTC),
        capability=capability,
        reason="a reason",
        service_id="svc",
        source_url="https://example.com/",
        model="mistral-large-latest",
        resolved=resolved,
    )


def _patch(monkeypatch, fake) -> None:
    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)
    c.prepare_cached.cache_clear()


def test_list_tool_suggestions_hides_resolved_by_default(monkeypatch) -> None:
    fake = _FakeSession([
        _row("reddit_api_post_history"),
        _row("search_token_listings", resolved=True),
    ])
    _patch(monkeypatch, fake)

    store = AdminCassandraStore()
    items = store.list_tool_suggestions()

    assert len(items) == 1
    assert items[0]["capability"] == "reddit_api_post_history"
    assert items[0]["resolved"] is False


def test_list_tool_suggestions_include_resolved_shows_everything(monkeypatch) -> None:
    fake = _FakeSession([
        _row("reddit_api_post_history"),
        _row("search_token_listings", resolved=True),
    ])
    _patch(monkeypatch, fake)

    store = AdminCassandraStore()
    items = store.list_tool_suggestions(include_resolved=True)

    assert len(items) == 2


def test_resolve_tool_suggestions_marks_matching_capability_only(monkeypatch) -> None:
    """Bulk-resolve targets every unresolved row for the exact capability —
    this is what clears an already-implemented tool (e.g. search_token_listings,
    github_repository_search, shipped 2026-07-14) off the growing Tool gaps
    list without deleting the request-count history."""
    fake = _FakeSession([
        _row("search_token_listings"),
        _row("search_token_listings"),
        _row("reddit_api_post_history"),
    ])
    _patch(monkeypatch, fake)

    store = AdminCassandraStore()
    n = store.resolve_tool_suggestions("search_token_listings")

    assert n == 2
    assert len(fake.resolve_calls) == 2
    remaining = store.list_tool_suggestions()
    assert len(remaining) == 1
    assert remaining[0]["capability"] == "reddit_api_post_history"


def test_resolve_tool_suggestions_is_case_insensitive(monkeypatch) -> None:
    fake = _FakeSession([_row("Search_Token_Listings")])
    _patch(monkeypatch, fake)

    store = AdminCassandraStore()
    n = store.resolve_tool_suggestions("search_token_listings")

    assert n == 1


def test_resolve_tool_suggestions_skips_already_resolved(monkeypatch) -> None:
    fake = _FakeSession([_row("search_token_listings", resolved=True)])
    _patch(monkeypatch, fake)

    store = AdminCassandraStore()
    n = store.resolve_tool_suggestions("search_token_listings")

    assert n == 0
    assert fake.resolve_calls == []
