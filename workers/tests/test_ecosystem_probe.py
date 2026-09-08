"""Algorand Open Registry liveness-probe beat and blurb-draft helper: cadence gate, per-entry isolation, SSRF-guarded fetch, grounded-only blurb drafting.

Fully offline: the HTTP fetch is faked at guarded_get's own seam, storage at
the RegistryRepository seam / a fake Cassandra session, and the LLM call at
the MistralProvider seam -- nothing here reaches a real network, a real
Cassandra, or a real DeepSeek call.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from celery.exceptions import SoftTimeLimitExceeded

from app.core import redis_lock as redis_lock_module
from app.modules.ecosystem_probe import blurb as blurb_module
from app.modules.ecosystem_probe.service import (
    RegistryEntry,
    RegistryRepository,
    check_reachable,
    run_liveness_sweep,
)
from app.modules.ecosystem_probe.tasks import probe_tasks

NOW = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


class _FakeRepo(RegistryRepository):
    def __init__(self, entries: list[RegistryEntry]) -> None:
        self.entries = entries
        self.recorded: list[tuple] = []
        self._explode_for: set[str] = set()

    def list_recheckable(self, *, limit: int) -> list[RegistryEntry]:
        return self.entries[:limit]

    def record_liveness(
        self, slug: str, *, reachable: bool, http_status: int, at: datetime
    ) -> None:
        if slug in self._explode_for:
            raise ConnectionError("cassandra hiccup")
        self.recorded.append((slug, reachable, http_status, at))


def _entry(slug: str, url: str = "https://example.test/") -> RegistryEntry:
    return RegistryEntry(slug=slug, url=url, status="approved")


# --------------------------------------------------------------------------- #
# check_reachable
# --------------------------------------------------------------------------- #
@pytest.fixture(autouse=True)
def _not_parked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Default the parking-page check to "not parked" for every test in this module except the ones specifically exercising it below -- fails the same way is_source_parked_or_expired itself fails open (False), and keeps every pre-existing test in this file from making a second, unmocked real fetch."""
    monkeypatch.setattr(
        "app.modules.ecosystem_probe.service.is_source_parked_or_expired", lambda _url: False
    )


def test_check_reachable_true_on_2xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real 2xx response counts as reachable."""
    monkeypatch.setattr(
        "app.modules.ecosystem_probe.service.guarded_get",
        lambda *_a, **_kw: SimpleNamespace(status_code=200),
    )
    reachable, status = check_reachable("https://example.test/")
    assert reachable is True
    assert status == 200


def test_check_reachable_false_on_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 5xx response counts as unreachable."""
    monkeypatch.setattr(
        "app.modules.ecosystem_probe.service.guarded_get",
        lambda *_a, **_kw: SimpleNamespace(status_code=503),
    )
    reachable, status = check_reachable("https://example.test/")
    assert reachable is False
    assert status == 503


def test_check_reachable_false_on_404(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test (2026-09-08, owner ask): a 404 used to pass the old '< 500' bar as 'alive' -- only 2xx counts now."""
    monkeypatch.setattr(
        "app.modules.ecosystem_probe.service.guarded_get",
        lambda *_a, **_kw: SimpleNamespace(status_code=404),
    )
    reachable, status = check_reachable("https://example.test/")
    assert reachable is False
    assert status == 404


def test_check_reachable_false_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 401 response counts as unreachable, same as 404."""
    monkeypatch.setattr(
        "app.modules.ecosystem_probe.service.guarded_get",
        lambda *_a, **_kw: SimpleNamespace(status_code=401),
    )
    reachable, status = check_reachable("https://example.test/")
    assert reachable is False
    assert status == 401


def test_check_reachable_false_when_the_body_matches_a_parking_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test (2026-09-08, the 'downbad' problem): a 200 response whose body matches a known parking-page signature reads as unreachable, not alive -- the transport-only check alone would have called this reachable."""
    monkeypatch.setattr(
        "app.modules.ecosystem_probe.service.guarded_get",
        lambda *_a, **_kw: SimpleNamespace(status_code=200),
    )
    monkeypatch.setattr(
        "app.modules.ecosystem_probe.service.is_source_parked_or_expired", lambda _url: True
    )
    reachable, status = check_reachable("https://example.test/")
    assert reachable is False
    assert status == 200  # the real transport status is still reported, just not "alive"


def test_check_reachable_never_checks_parking_when_already_unreachable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The parking check is skipped (no second fetch) once the transport check has already failed -- nothing left to narrow."""

    def _must_not_be_called(_url: str) -> bool:
        raise AssertionError("must not check for parking on an already-unreachable entry")

    monkeypatch.setattr(
        "app.modules.ecosystem_probe.service.guarded_get",
        lambda *_a, **_kw: SimpleNamespace(status_code=503),
    )
    monkeypatch.setattr(
        "app.modules.ecosystem_probe.service.is_source_parked_or_expired", _must_not_be_called
    )
    reachable, _status = check_reachable("https://example.test/")
    assert reachable is False


def test_check_reachable_never_raises_on_network_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport failure is reported as unreachable, never propagated."""

    def boom(*_a: object, **_kw: object) -> None:
        raise ConnectionError("dns failure")

    monkeypatch.setattr("app.modules.ecosystem_probe.service.guarded_get", boom)
    reachable, status = check_reachable("https://example.test/")
    assert reachable is False
    assert status == 0


def test_check_reachable_propagates_soft_time_limit_from_the_fetch_itself(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression test (2026-09-07): a SoftTimeLimitExceeded raised mid-fetch (inside guarded_get) must propagate out of check_reachable, not be swallowed as a false "unreachable" result.

    This is deliberately NOT the same thing test_sweep_reraises_soft_time_limit below checks --
    that test patches check_reachable itself to raise, which proves run_liveness_sweep's own
    handler works but says nothing about whether check_reachable's own `except Exception` (which
    a bare except would also catch SoftTimeLimitExceeded through) actually lets a real mid-fetch
    interrupt through. Before this fix, this exact scenario returned (False, 0) instead of
    raising -- the sweep read its own timeout as "this site is down" and wrote that to a real
    registry row, then kept looping on a growing false-unreachable set until hard-killed.
    """

    def boom(*_a: object, **_kw: object) -> None:
        raise SoftTimeLimitExceeded

    monkeypatch.setattr("app.modules.ecosystem_probe.service.guarded_get", boom)
    with pytest.raises(SoftTimeLimitExceeded):
        check_reachable("https://example.test/")


# --------------------------------------------------------------------------- #
# run_liveness_sweep
# --------------------------------------------------------------------------- #
def test_sweep_records_every_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every recheckable entry gets one liveness record."""
    monkeypatch.setattr(
        "app.modules.ecosystem_probe.service.check_reachable", lambda _url: (True, 200)
    )
    repo = _FakeRepo([_entry("a"), _entry("b"), _entry("c")])
    result = run_liveness_sweep(repo=repo, limit=10)
    assert result["entries"] == 3
    assert result["checked"] == 3
    assert result["reachable"] == 3
    assert [slug for slug, *_ in repo.recorded] == ["a", "b", "c"]


def test_sweep_one_failure_does_not_abort_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    """A per-entry storage failure is isolated -- the sweep continues to the next entry."""
    monkeypatch.setattr(
        "app.modules.ecosystem_probe.service.check_reachable", lambda _url: (True, 200)
    )
    repo = _FakeRepo([_entry("a"), _entry("b")])
    repo._explode_for.add("a")
    result = run_liveness_sweep(repo=repo, limit=10)
    assert result["failed"] == 1
    assert [slug for slug, *_ in repo.recorded] == ["b"]


def test_sweep_reraises_soft_time_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """SoftTimeLimitExceeded propagates immediately (CLAUDE.md invariant 6), unlike an ordinary per-entry failure."""

    def boom(_url: str) -> None:
        raise SoftTimeLimitExceeded

    monkeypatch.setattr("app.modules.ecosystem_probe.service.check_reachable", boom)
    repo = _FakeRepo([_entry("a")])
    with pytest.raises(SoftTimeLimitExceeded):
        run_liveness_sweep(repo=repo, limit=10)


# --------------------------------------------------------------------------- #
# Beat gate
# --------------------------------------------------------------------------- #
def test_probe_task_skips_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    """The task re-checks ECOSYSTEM_PROBE_ENABLED itself, so a manual trigger honours the flag too, and never touches the sweep."""
    monkeypatch.setattr(probe_tasks, "ECOSYSTEM_PROBE_ENABLED", False)
    monkeypatch.setattr(
        probe_tasks, "run_liveness_sweep", lambda: pytest.fail("must not sweep when disabled")
    )

    class _Redis:
        def set(self, key: str, _v: str, *, nx: bool, ex: int) -> bool:  # noqa: ARG002 -- name must match the real callee's keyword arg
            return True

        def eval(self, *_a: object) -> int:
            return 1

    monkeypatch.setattr(redis_lock_module, "_client", lambda: _Redis())
    result = probe_tasks.probe_registry_entries()
    assert result == {"status": "skipped", "reason": "ecosystem_probe_disabled"}


def test_probe_task_lock_ttl_covers_soft_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The single_flight lock TTL is >= the celery-wide soft time limit (CLAUDE.md invariant 5)."""
    monkeypatch.setattr(probe_tasks, "ECOSYSTEM_PROBE_ENABLED", True)
    monkeypatch.setattr(probe_tasks, "run_liveness_sweep", lambda: {"status": "ok"})
    acquired: list[tuple[str, int]] = []

    class _Redis:
        def set(self, key: str, _v: str, *, nx: bool, ex: int) -> bool:  # noqa: ARG002 -- name must match the real callee's keyword arg
            acquired.append((key, ex))
            return True

        def eval(self, *_a: object) -> int:
            return 1

    monkeypatch.setattr(redis_lock_module, "_client", lambda: _Redis())
    assert probe_tasks.probe_registry_entries() == {"status": "ok"}
    assert acquired[0][0] == "lock:ecosystem_probe:sweep"
    assert acquired[0][1] >= probe_tasks.celery_app.conf.task_soft_time_limit


# --------------------------------------------------------------------------- #
# Blurb drafting: grounded-only, never invents
# --------------------------------------------------------------------------- #
class _FakeSession:
    """Fake Cassandra session with an identity `prepare()` so `execute()` can dispatch on raw CQL text.

    Same convention as conftest.py's FakeArtifactSession: `_Stmt.__get__`
    calls `prepare_cached(cql)`, which calls `get_cassandra_session().prepare(cql)`;
    this fake's `.prepare` just hands the CQL straight back.
    """

    def __init__(self, listing_rows: list, body_by_page_id: dict) -> None:
        self.listing_rows = listing_rows
        self.body_by_page_id = body_by_page_id

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, stmt: str, params: tuple) -> object:
        if "crawled_pages_by_domain" in stmt:
            return self.listing_rows if params[0] == "example.test" else []
        assert "crawled_pages_by_id" in stmt
        row = self.body_by_page_id.get(params[0])
        return SimpleNamespace(one=lambda: row)


def test_grounding_text_empty_when_nothing_crawled(monkeypatch: pytest.MonkeyPatch) -> None:
    """No harvested pages for the domain -> empty grounding text, never fabricated."""
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: _FakeSession([], {}))
    assert blurb_module._grounding_text("example.test") == ""


def test_grounding_text_joins_title_and_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """Grounding text is assembled from already-crawled title+body, nothing else."""
    listing_rows = [SimpleNamespace(page_id="p1", url="https://example.test/", title="Home")]
    body_by_page_id = {
        "p1": SimpleNamespace(
            title="Home", body="Example is a wallet for Algorand.", crawled_at=NOW
        )
    }
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: _FakeSession(listing_rows, body_by_page_id),
    )
    text = blurb_module._grounding_text("example.test")
    assert "Example is a wallet for Algorand." in text


def test_draft_blurb_returns_empty_without_grounding(monkeypatch: pytest.MonkeyPatch) -> None:
    """draft_blurb never calls the LLM at all when there is nothing to ground it in -- it returns before ever building a client."""
    monkeypatch.setattr(blurb_module, "_grounding_text", lambda _domain: "")

    def _must_not_be_called() -> None:
        raise AssertionError("must not call the LLM with no grounding")

    monkeypatch.setattr(blurb_module, "get_llm_translate_client", _must_not_be_called)
    result = blurb_module.draft_blurb(slug="s", url="https://example.test/", domain="example.test")
    assert result == ""
