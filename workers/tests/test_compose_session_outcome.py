"""compose_sessions' status='ok' is ambiguous -- it only means the compose produced a JSON payload without crashing, not what happened to that draft afterward. publish_from_queued_row now finalizes it into the real publish decision (published/on_hold/rejected:<reason>); every other terminal status (aborted_by_writer, error, credit_insufficient) is already self-explanatory and left untouched."""

from __future__ import annotations

import pytest

from app.modules.ai import tool_insights_store as tis
from app.modules.newspaper.tasks import publish_tasks as pt


@pytest.mark.parametrize(
    ("result", "expected"),
    [
        ({"status": "published"}, "published"),
        ({"status": "approved_backlog"}, "published"),
        ({"status": "auto_applied"}, "published"),
        ({"status": "edited"}, "published"),
        ({"status": "review"}, "on_hold"),
        (
            {"status": "duplicate", "reason": "same_facts_as_own_recent_article"},
            "rejected:same_facts_as_own_recent_article",
        ),
        ({"status": "duplicate"}, "rejected"),
        ({"status": "failed", "reason": "update_failed"}, "rejected:update_failed"),
        ({"status": "failed"}, "rejected:failed"),
    ],
)
def test_classify_publish_outcome_maps_known_terminal_statuses(
    result: dict, expected: str
) -> None:
    """A draft-was-produced-then-decided outcome maps to its compose_sessions terminal string."""
    assert pt._classify_publish_outcome(result) == expected


@pytest.mark.parametrize(
    "result",
    [
        {"status": "aborted_by_writer", "reason": "dead_project: site is defunct"},
        {"status": "mistral_failed", "detail": "timeout"},
        {"status": "mistral_credit_insufficient"},
        {"status": "already_running"},
        {"status": "skipped", "reason": "no_scrape_url"},
        {"status": "domain_capped"},
        {"status": "rate_limited", "reason": "daily cap"},
    ],
)
def test_classify_publish_outcome_leaves_self_explanatory_statuses_alone(
    result: dict,
) -> None:
    """These either never reached a status='ok' compose session (nothing to overwrite) or are already a complete answer on their own -- must not be reclassified."""
    assert pt._classify_publish_outcome(result) is None


class _Row:
    queue_id = "q1"
    scrape_url = "https://example.com/"


def test_wrapper_finalizes_the_session_when_outcome_is_classified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """publish_from_queued_row calls finalize_compose_session_outcome with the row's scrape_url and the classified outcome after a real decision was made."""
    monkeypatch.setattr(
        pt, "_publish_from_queued_row_impl", lambda _row, **_kw: {"status": "published"}
    )
    captured: dict = {}

    def _fake_finalize(source_url: str, outcome: str) -> bool:
        captured["source_url"] = source_url
        captured["outcome"] = outcome
        return True

    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.finalize_compose_session_outcome", _fake_finalize
    )

    result = pt.publish_from_queued_row(_Row())

    assert result == {"status": "published"}
    assert captured == {"source_url": "https://example.com/", "outcome": "published"}


def test_wrapper_skips_finalize_for_unclassified_outcomes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No finalize call at all when the outcome doesn't need translating (e.g. a pre-compose veto) -- must not touch compose_sessions needlessly."""
    monkeypatch.setattr(
        pt, "_publish_from_queued_row_impl", lambda _row, **_kw: {"status": "already_running"}
    )

    def _must_not_be_called(*_a: object, **_kw: object) -> None:
        raise AssertionError("finalize_compose_session_outcome must not be called")

    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.finalize_compose_session_outcome",
        _must_not_be_called,
    )

    assert pt.publish_from_queued_row(_Row()) == {"status": "already_running"}


def test_wrapper_swallows_finalize_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A finalize failure (e.g. a Cassandra blip) must never surface as a publish failure -- the actual outcome dict is still returned."""
    monkeypatch.setattr(
        pt, "_publish_from_queued_row_impl", lambda _row, **_kw: {"status": "published"}
    )

    def _boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("cassandra blip")

    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.finalize_compose_session_outcome", _boom
    )

    assert pt.publish_from_queued_row(_Row()) == {"status": "published"}


class _FakeSession:
    def __init__(self, rows: list) -> None:
        self._rows = rows
        self.updates: list[tuple] = []

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, _stmt: object, params: tuple) -> list | None:
        # LIST_ALL_SUMMARY has one bind param, MARK_STALE has four.
        if len(params) == 1:
            return self._rows
        self.updates.append(params)
        return None


class _Row2:
    def __init__(self, created_at: int, session_id: str, status: str, source_url: str) -> None:
        self.created_at = created_at
        self.session_id = session_id
        self.status = status
        self.source_url = source_url


def _patch_cassandra(monkeypatch: pytest.MonkeyPatch, fake: object) -> None:
    import app.core.cassandra as c

    monkeypatch.setattr(c, "get_cassandra_session", lambda: fake)
    c.prepare_cached.cache_clear()


def test_finalize_only_touches_the_newest_matching_ok_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two 'ok' rows exist for the same source_url (a rare double-compose) -- only the newest is updated."""
    rows = [
        _Row2(10, "old-session", "ok", "https://example.com/"),
        _Row2(20, "new-session", "ok", "https://example.com/"),
        _Row2(30, "unrelated-session", "ok", "https://other.com/"),
        _Row2(40, "aborted-session", "aborted_by_writer", "https://example.com/"),
    ]
    fake = _FakeSession(rows)
    _patch_cassandra(monkeypatch, fake)

    ok = tis.finalize_compose_session_outcome("https://example.com/", "published")

    assert ok is True
    assert len(fake.updates) == 1
    outcome, bucket, created_at, session_id = fake.updates[0]
    assert outcome == "published"
    assert bucket == "all"
    assert created_at == 20
    assert session_id == "new-session"


def test_finalize_returns_false_when_no_ok_row_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    """No status='ok' row for this source_url at all -- nothing to overwrite."""
    fake = _FakeSession([_Row2(10, "s1", "error", "https://example.com/")])
    _patch_cassandra(monkeypatch, fake)

    assert tis.finalize_compose_session_outcome("https://example.com/", "published") is False
    assert fake.updates == []


def test_finalize_is_a_noop_for_blank_inputs() -> None:
    """A blank source_url or outcome short-circuits before touching Cassandra at all."""
    assert tis.finalize_compose_session_outcome("", "published") is False
    assert tis.finalize_compose_session_outcome("https://example.com/", "") is False


def test_stamp_service_recompose_cooldown_marks_scraped_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful compose stamps the full re-scrape window, regardless of what triggered it.

    Regression for a real gap (found live 2026-08-09): admin "Recompose now" never
    stamped this cooldown, so the beat could recompose the same service days later.
    """
    calls = []
    monkeypatch.setattr(
        "app.modules.scraper.core.scrape_cooldown.mark_scraped",
        lambda service_id, *, ok: calls.append((service_id, ok)),
    )
    pt._stamp_service_recompose_cooldown("algoseas-io", ok=True)
    assert calls == [("algoseas-io", True)]


def test_stamp_service_recompose_cooldown_marks_short_backoff_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed compose still stamps a cooldown -- just the short failure backoff.

    Not the full 30-day window, so a transient error doesn't block a legitimate
    retry for a month.
    """
    calls = []
    monkeypatch.setattr(
        "app.modules.scraper.core.scrape_cooldown.mark_scraped",
        lambda service_id, *, ok: calls.append((service_id, ok)),
    )
    pt._stamp_service_recompose_cooldown("treefund-io", ok=False)
    assert calls == [("treefund-io", False)]


def test_stamp_service_recompose_cooldown_swallows_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis being unavailable must never break a compose over cooldown bookkeeping."""

    def _boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("redis down")

    monkeypatch.setattr("app.modules.scraper.core.scrape_cooldown.mark_scraped", _boom)
    pt._stamp_service_recompose_cooldown("algoseas-io", ok=True)  # must not raise
