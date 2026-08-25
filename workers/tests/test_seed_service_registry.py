"""deploy/scripts/seed_service_registry.py: closing the bug-class-1 gap.

Historically this script inserted `service_registry` rows via raw CQL and
never called `service_sources.add_web_source`, so a seeded/legacy domain-kind
service was permanently invisible to `service_for_domain`'s by-domain reverse
index -- the exact condition that let `domain_tracker.ensure_monitored_service`
spawn a genuine duplicate `service_registry` row for the same real-world
domain later on, since its own "does someone already own this domain?" guard
found nothing. `_maybe_seed_web_source` is the fix: called once per seeded
entry, right after the raw INSERT.

The script itself isn't part of the `app` package (it lives under
deploy/scripts/), so it's loaded here via importlib from its file path --
the same module the real `python deploy/scripts/seed_service_registry.py`
entry point runs, not a reimplementation.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from conftest import _artifact_cql, _artifact_rows

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "deploy" / "scripts" / "seed_service_registry.py"


def _load_seed_module() -> object:
    """Import deploy/scripts/seed_service_registry.py by file path -- it isn't part of the `app` package."""
    spec = importlib.util.spec_from_file_location("seed_service_registry_under_test", _SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed_mod = _load_seed_module()


class _FakeServiceSourceSession:
    """In-memory service_sources/service_by_domain, keyed by the exact CQL text of `ServiceSourceStmts` -- same dispatch shape as conftest's FakeArtifactSession, scoped to just the handful of statements add_web_source/service_for_domain use."""

    def __init__(self) -> None:
        from app.core.statements import ServiceSourceStmts

        self.sources: dict[tuple[str, str], dict] = {}
        self.by_domain: dict[str, str] = {}
        self._handlers = {
            _artifact_cql(ServiceSourceStmts, "UPSERT"): self._upsert,
            _artifact_cql(ServiceSourceStmts, "UPSERT_BY_DOMAIN"): self._upsert_by_domain,
            _artifact_cql(ServiceSourceStmts, "GET_BY_DOMAIN"): self._get_by_domain,
        }

    def prepare(self, cql: str) -> str:
        return cql

    def execute(self, cql: str, params: tuple = ()) -> object:
        handler = self._handlers.get(cql)
        if handler is None:
            raise AssertionError(f"_FakeServiceSourceSession: no handler wired for CQL: {cql!r}")
        return handler(tuple(params))

    def _upsert(self, p: tuple) -> None:
        service_id, source_id, source_type, url, domain, enabled, _added_at = p
        self.sources[(service_id, source_id)] = {
            "source_type": source_type,
            "url": url,
            "domain": domain,
            "enabled": enabled,
        }

    def _upsert_by_domain(self, p: tuple) -> None:
        domain, service_id = p
        self.by_domain[domain] = service_id

    def _get_by_domain(self, p: tuple) -> object:
        (domain,) = p
        service_id = self.by_domain.get(domain)
        row = SimpleNamespace(service_id=service_id) if service_id else None
        return _artifact_rows([row] if row else [])


@pytest.fixture
def fake_service_source_session(monkeypatch: pytest.MonkeyPatch) -> _FakeServiceSourceSession:
    """An in-memory service_sources/service_by_domain double, swapped in for get_cassandra_session."""
    import app.core.cassandra as c

    session = _FakeServiceSourceSession()
    monkeypatch.setattr(c, "get_cassandra_session", lambda: session)
    c.prepare_cached.cache_clear()
    return session


def test_maybe_seed_web_source_indexes_a_domain_entry(
    fake_service_source_session: _FakeServiceSourceSession,
) -> None:
    """A domain-matched seed entry gets claimed in the by-domain reverse index -- the fix itself."""
    entry = {
        "service_id": "algorand-forum",
        "display_name": "Algorand Forum — Latest",
        "match_kind": "domain",
        "match_value": "forum.algorand.co",
        "scrape_url": "https://forum.algorand.co/latest",
        "enabled": True,
    }

    seed_mod._maybe_seed_web_source(entry)

    assert fake_service_source_session.by_domain["forum.algorand.co"] == "algorand-forum"


def test_seeded_service_is_now_visible_to_service_for_domain(
    fake_service_source_session: _FakeServiceSourceSession,  # noqa: ARG001 -- activates the monkeypatch
) -> None:
    """End to end: after seeding, service_for_domain (the exact function ensure_monitored_service consults before spawning a new service) resolves the seeded service_id -- proving the gap that let a legacy/seeded service go permanently invisible is closed."""
    from app.modules.newspaper.service_sources import service_for_domain

    entry = {
        "service_id": "algorand-forum",
        "display_name": "Algorand Forum — Latest",
        "match_kind": "domain",
        "match_value": "forum.algorand.co",
        "scrape_url": "https://forum.algorand.co/latest",
        "enabled": True,
    }

    # Before seeding: exactly the pre-fix bug -- unclaimed, invisible.
    assert service_for_domain("forum.algorand.co") == ""

    seed_mod._maybe_seed_web_source(entry)

    assert service_for_domain("forum.algorand.co") == "algorand-forum"


def test_maybe_seed_web_source_skips_non_domain_match_kinds(
    fake_service_source_session: _FakeServiceSourceSession,
) -> None:
    """A subreddit/address-matched entry has no registrable domain to claim -- never calls add_web_source."""
    entry = {
        "service_id": "reddit-algorand",
        "display_name": "Reddit — r/algorand",
        "match_kind": "subreddit",
        "match_value": "algorand",
        "scrape_url": "reddit://r/algorand/new",
        "enabled": True,
    }

    seed_mod._maybe_seed_web_source(entry)

    assert fake_service_source_session.by_domain == {}


def test_maybe_seed_web_source_never_raises_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Cassandra/import failure inside add_web_source must not abort the whole seed run -- best-effort, matching _maybe_enqueue_seed_url's own established pattern in this script."""

    def _boom(*_a: object, **_kw: object) -> None:
        raise RuntimeError("cassandra unreachable")

    monkeypatch.setattr("app.modules.newspaper.service_sources.add_web_source", _boom)

    entry = {
        "service_id": "algorand-forum",
        "match_kind": "domain",
        "match_value": "forum.algorand.co",
        "scrape_url": "https://forum.algorand.co/latest",
    }

    seed_mod._maybe_seed_web_source(entry)  # must not raise
