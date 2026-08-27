"""is_source_parked_or_expired: detects a crawled source whose domain has since gone dead (parked/expired-registration page).

The shape neither domain_probe (advisory, fooled by a normal 200/HTTPS) nor
defunct_entity_gate (DNS-only) catches.

Root-caused 2026-08-27: arima.io was crawled with real content, then its
registration expired -- DNS still resolves, HTTP still returns 200, only the
page BODY (a Namecheap parking template) gives it away.
"""

from __future__ import annotations

import pytest

from app.modules.newspaper import source_liveness


class _FakeResponse:
    def __init__(self, status_code: int, text: str) -> None:
        self.status_code = status_code
        self.text = text


def _mock_get(monkeypatch: pytest.MonkeyPatch, response: _FakeResponse | Exception) -> None:
    def fake_get(*_a: object, **_k: object) -> _FakeResponse:
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(source_liveness.httpx, "get", fake_get)
    monkeypatch.setattr(source_liveness, "assert_public_url", lambda url: url)


def test_true_on_known_parking_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real arima.io body: a Namecheap parking template with 'Domain registration has expired'."""
    _mock_get(
        monkeypatch,
        _FakeResponse(200, "<html><body>Domain registration has expired. Renew now.</body></html>"),
    )
    assert source_liveness.is_source_parked_or_expired("https://arima.io/") is True


def test_true_on_parking_service_script_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    """Matches a known parking-service script tag even without the expiry phrase."""
    _mock_get(monkeypatch, _FakeResponse(200, '<script src="https://lander.parity.domains/js/x.js"></script>'))
    assert source_liveness.is_source_parked_or_expired("https://example.com/") is True


def test_false_on_normal_live_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """A genuine, unrelated page never matches -- no false positive on ordinary content."""
    _mock_get(monkeypatch, _FakeResponse(200, "<html><body>Welcome to our NFT platform.</body></html>"))
    assert source_liveness.is_source_parked_or_expired("https://example.com/") is False


def test_false_on_fetch_error_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """A network error must never be treated as 'dead' -- fails open (alive)."""
    _mock_get(monkeypatch, RuntimeError("connection reset"))
    assert source_liveness.is_source_parked_or_expired("https://example.com/") is False


def test_false_on_http_error_status(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-2xx/3xx status is treated as alive, not dead -- this module only detects the parked-page SHAPE, not general unreachability."""
    _mock_get(monkeypatch, _FakeResponse(404, "Not Found"))
    assert source_liveness.is_source_parked_or_expired("https://example.com/") is False


def test_false_for_non_http_url() -> None:
    """A non-http(s) URL (e.g. a per-item lane's synthetic identifier) never triggers a fetch."""
    assert source_liveness.is_source_parked_or_expired("editorial://brief/b1") is False


def test_false_for_empty_url() -> None:
    """An empty URL never triggers a fetch."""
    assert source_liveness.is_source_parked_or_expired("") is False


def test_unsafe_url_fails_open(monkeypatch: pytest.MonkeyPatch) -> None:
    """assert_public_url raising (an internal/private target) is caught and reads as alive, never propagates."""

    def _boom(_url: str) -> str:
        raise ValueError("blocked: private host")

    monkeypatch.setattr(source_liveness, "assert_public_url", _boom, raising=False)
    assert source_liveness.is_source_parked_or_expired("http://169.254.169.254/") is False


class _FakePendingArtifact:
    def __init__(self, artifact_id: str, service_id: str, url: str, channel: str) -> None:
        self.artifact_id = artifact_id
        self.service_id = service_id
        self.url = url
        self.channel = channel


def test_find_dead_pending_artifacts_scopes_to_crawler_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only channel=='crawler' rows are checked -- a forum/youtube/bluesky per-item lane has no 'site' of its own to go dead."""
    artifacts = [
        _FakePendingArtifact("a1", "svc-a", "https://dead.example/", "crawler"),
        _FakePendingArtifact("a2", "forum-topic:1", "", "forum"),
        _FakePendingArtifact("a3", "svc-b", "https://alive.example/", "crawler"),
    ]
    monkeypatch.setattr(
        "algorand_shared.artifact_store.list_pending_artifacts", lambda *, limit=200: artifacts  # noqa: ARG005 -- name must match the real callee's keyword arg
    )
    monkeypatch.setattr(
        source_liveness,
        "is_source_parked_or_expired",
        lambda url: url == "https://dead.example/",
    )

    found = source_liveness.find_dead_pending_artifacts()

    assert [f["artifact_id"] for f in found] == ["a1"]


def test_discard_dead_pending_artifacts_dry_run_makes_no_writes(
    fake_artifact_session,  # noqa: ANN001 -- conftest.FakeArtifactSession, untyped to avoid an import cycle in this file
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dry_run=True (the default) reports what WOULD be discarded without touching any status."""
    from algorand_shared.artifact_store import PENDING, insert_artifact

    artifact_id, _ = insert_artifact(
        service_id="svc-dead", url="https://dead.example/", channel="crawler", content="x"
    )
    monkeypatch.setattr(source_liveness, "is_source_parked_or_expired", lambda _url: True)

    result = source_liveness.discard_dead_pending_artifacts()

    assert result["status"] == "dry_run"
    assert [e["artifact_id"] for e in result["would_discard"]] == [artifact_id]
    assert fake_artifact_session.artifacts[artifact_id]["status"] == PENDING


def test_discard_dead_pending_artifacts_real_run_discards(
    fake_artifact_session,  # noqa: ANN001 -- conftest.FakeArtifactSession, untyped to avoid an import cycle in this file
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dry_run=False actually marks the matched artifacts DISCARDED."""
    from algorand_shared.artifact_store import DISCARDED, insert_artifact

    artifact_id, _ = insert_artifact(
        service_id="svc-dead", url="https://dead.example/", channel="crawler", content="x"
    )
    monkeypatch.setattr(source_liveness, "is_source_parked_or_expired", lambda _url: True)

    result = source_liveness.discard_dead_pending_artifacts(dry_run=False)

    assert result["status"] == "ok"
    assert result["discarded"] == [artifact_id]
    assert fake_artifact_session.artifacts[artifact_id]["status"] == DISCARDED


def test_beat_task_delegates_with_a_small_throttled_scan_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Celery task body is a thin, real (dry_run=False) delegation, capped to a small scan_limit -- each check is a live network fetch, so this must stay a slow trickle, never a one-shot sweep of the whole pending pool."""
    from app.modules.newspaper.tasks import queue_drain_tasks as qdt

    captured: dict[str, object] = {}

    def _fake_discard(*, scan_limit: int, dry_run: bool) -> dict[str, object]:
        captured["scan_limit"] = scan_limit
        captured["dry_run"] = dry_run
        return {"status": "ok", "discarded": [], "count": 0}

    monkeypatch.setattr(source_liveness, "discard_dead_pending_artifacts", _fake_discard)

    result = qdt.discard_dead_pending_sources_task.run()

    assert captured == {"scan_limit": 15, "dry_run": False}
    assert result == {"status": "ok", "discarded": [], "count": 0}
