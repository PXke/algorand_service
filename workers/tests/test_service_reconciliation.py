"""service_reconciliation.py: the daily automated safety net for the two service-duplication bug classes (literal domain-registry duplicates, and per-item lanes missing venue_service_id). Every test here is about the conservative/ambiguous boundary -- these auto-actions run unattended in prod, so what must NOT be touched matters as much as what should be."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from conftest import FakeArtifactSession

from app.modules.newspaper.service_reconciliation import (
    backfill_missing_venue_service_ids,
    find_domain_registry_duplicates,
    find_duplicate_pending_artifacts,
    reconcile_domain_duplicates,
    reconcile_duplicate_pending_artifacts,
)


def _registry_row(service_id: str, domain: str, *, enabled: bool = True, origin: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        service_id=service_id,
        match_kind="domain",
        match_value=domain,
        enabled=enabled,
        origin=origin,
        scrape_url=f"https://{domain}",
    )


# --------------------------------------------------------------------------- #
# find_domain_registry_duplicates
# --------------------------------------------------------------------------- #


def test_find_duplicates_flags_domain_owned_by_a_different_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """A service_registry row whose own domain is owned by a DIFFERENT service_id in the reverse index is a duplicate finding."""
    rows = [_registry_row("perawallet-app", "perawallet.app"), _registry_row("pera-wallet", "other.example")]
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: MagicMock(execute=lambda *_: rows),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.service_for_domain",
        lambda domain: "pera-wallet" if domain == "perawallet.app" else "",
    )
    findings = find_domain_registry_duplicates()
    assert findings == [
        {
            "service_id": "perawallet-app",
            "domain": "perawallet.app",
            "owner_service_id": "pera-wallet",
            "origin": "",
        }
    ]


def test_find_duplicates_ignores_self_owned_and_unclaimed_and_shared_platform(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Self-owned, unclaimed, shared-platform, and disabled rows are never duplicates."""
    rows = [
        _registry_row("nodely-io", "nodely.io"),  # owns itself
        _registry_row("some-svc", "unclaimed.example"),  # no owner yet
        _registry_row("stray-bsky", "bsky.app"),  # shared platform host, never a dup
        _registry_row("disabled-svc", "dup.example", enabled=False),  # not enabled, skipped
    ]
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: MagicMock(execute=lambda *_: rows),
    )

    def fake_owner(domain: str) -> str:
        return {"nodely.io": "nodely-io", "unclaimed.example": "", "bsky.app": "someone-else"}.get(
            domain, ""
        )

    monkeypatch.setattr("app.modules.newspaper.service_sources.service_for_domain", fake_owner)
    assert find_domain_registry_duplicates() == []


# --------------------------------------------------------------------------- #
# reconcile_domain_duplicates -- the three-outcome safety boundary
# --------------------------------------------------------------------------- #


def test_reconcile_self_heals_a_legacy_row_with_no_reverse_index_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No owner found at all -- a legacy/seeded row predating add_web_source -- gets INDEXED, never merged."""
    rows = [_registry_row("legacy-svc", "legacy.example")]
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session", lambda: MagicMock(execute=lambda *_: rows)
    )
    monkeypatch.setattr("app.modules.newspaper.service_sources.service_for_domain", lambda _d: "")
    add_calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.add_web_source",
        lambda service_id, *, domain, url: add_calls.append((service_id, domain, url)),
    )
    merge_calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.merge_services",
        lambda **kw: merge_calls.append(kw),
    )

    result = reconcile_domain_duplicates()

    assert result["indexed"] == ["legacy-svc"]
    assert result["merged"] == []
    assert result["flagged"] == []
    assert add_calls == [("legacy-svc", "legacy.example", "https://legacy.example")]
    assert merge_calls == []


def test_reconcile_merges_a_clear_cut_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-admin row whose domain is already owned by a different, ENABLED service is a clear-cut duplicate -- merged away."""
    rows = [
        _registry_row("perawallet-app", "perawallet.app", origin="domain"),
        _registry_row("pera-wallet", "pera-wallet-own.example"),
    ]
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session", lambda: MagicMock(execute=lambda *_: rows)
    )
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.service_for_domain",
        lambda domain: "pera-wallet" if domain == "perawallet.app" else "",
    )
    monkeypatch.setattr("app.modules.newspaper.service_sources.add_web_source", lambda *_a, **_kw: None)
    merge_calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.merge_services",
        lambda **kw: merge_calls.append(kw),
    )

    result = reconcile_domain_duplicates()

    assert result["merged"] == [
        {
            "service_id": "perawallet-app",
            "domain": "perawallet.app",
            "owner_service_id": "pera-wallet",
            "origin": "domain",
        }
    ]
    assert merge_calls == [{"target_service_id": "pera-wallet", "source_service_ids": ["perawallet-app"]}]
    assert result["flagged"] == []


def test_reconcile_flags_but_never_merges_an_admin_curated_row(monkeypatch: pytest.MonkeyPatch) -> None:
    """A human explicitly curated this row (origin=='admin') -- even a real domain collision is FLAGGED, never auto-merged, so a deliberate admin choice can't be silently erased."""
    rows = [
        _registry_row("admin-curated", "shared.example", origin="admin"),
        _registry_row("other-owner", "shared-own.example"),
    ]
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session", lambda: MagicMock(execute=lambda *_: rows)
    )
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.service_for_domain",
        lambda domain: "other-owner" if domain == "shared.example" else "",
    )
    monkeypatch.setattr("app.modules.newspaper.service_sources.add_web_source", lambda *_a, **_kw: None)
    merge_calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.merge_services",
        lambda **kw: merge_calls.append(kw),
    )

    result = reconcile_domain_duplicates()

    assert merge_calls == []
    assert result["merged"] == []
    assert len(result["flagged"]) == 1
    assert result["flagged"][0]["service_id"] == "admin-curated"


def test_reconcile_flags_when_the_reverse_index_owner_is_itself_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The by-domain index points at a service_id that isn't currently enabled -- a stale/inconsistent entry, not something to trust blindly. Flagged, not merged."""
    rows = [
        _registry_row("live-svc", "shared.example"),
        _registry_row("disabled-owner", "disabled-own.example", enabled=False),
    ]
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session", lambda: MagicMock(execute=lambda *_: rows)
    )
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.service_for_domain",
        lambda domain: "disabled-owner" if domain == "shared.example" else "",
    )
    monkeypatch.setattr("app.modules.newspaper.service_sources.add_web_source", lambda *_a, **_kw: None)
    merge_calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.merge_services",
        lambda **kw: merge_calls.append(kw),
    )

    result = reconcile_domain_duplicates()

    assert merge_calls == []
    assert result["merged"] == []
    assert len(result["flagged"]) == 1
    assert result["flagged"][0]["owner_service_id"] == "disabled-owner"


def test_reconcile_never_touches_bsky_app(monkeypatch: pytest.MonkeyPatch) -> None:
    """bsky.app is a shared platform host, never a real single-owner domain -- must never be indexed, merged, or flagged."""
    rows = [_registry_row("stray-bsky-row", "bsky.app")]
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session", lambda: MagicMock(execute=lambda *_: rows)
    )
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.service_for_domain", lambda _d: "someone-else"
    )
    add_calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.add_web_source",
        lambda *a, **kw: add_calls.append((a, kw)),
    )
    merge_calls = []
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.merge_services",
        lambda **kw: merge_calls.append(kw),
    )

    result = reconcile_domain_duplicates()

    assert result == {"indexed": [], "merged": [], "flagged": []}
    assert add_calls == []
    assert merge_calls == []


# --------------------------------------------------------------------------- #
# backfill_missing_venue_service_ids
# --------------------------------------------------------------------------- #


def _pending(artifact_id: str, service_id: str, channel: str, url: str = "", venue: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        artifact_id=artifact_id, service_id=service_id, channel=channel, url=url, venue_service_id=venue
    )


def _patch_backfill(
    monkeypatch: pytest.MonkeyPatch, *, pending: list, enabled_ids: set[str]
) -> list[tuple[str, str]]:
    monkeypatch.setattr(
        "app.modules.newspaper.artifact_store.list_pending_artifacts", lambda: pending
    )
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "app.modules.newspaper.artifact_store.set_artifact_venue_service_id",
        lambda artifact_id, venue: calls.append((artifact_id, venue)) or True,
    )
    monkeypatch.setattr(
        "app.modules.chain_tail.registry_cache.load_enabled_services",
        lambda: tuple(SimpleNamespace(service_id=sid) for sid in enabled_ids),
    )
    return calls


def test_backfill_forum_uses_the_fixed_venue_constant(monkeypatch: pytest.MonkeyPatch) -> None:
    """forum.algorand.co has exactly one venue -- always the fixed FORUM_VENUE_SERVICE_ID constant."""
    from app.core import config

    pending = [_pending("a1", "forum-topic:15288", "forum")]
    calls = _patch_backfill(monkeypatch, pending=pending, enabled_ids=set())

    result = backfill_missing_venue_service_ids()

    assert calls == [("a1", config.FORUM_VENUE_SERVICE_ID)]
    assert result["backfilled"] == [
        {"artifact_id": "a1", "service_id": "forum-topic:15288", "venue_service_id": config.FORUM_VENUE_SERVICE_ID}
    ]
    assert result["flagged"] == []


def test_backfill_xgov_matches_on_url_shape_not_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    """xgov_watch's artifacts land on the generic 'crawler' channel -- the proposals URL path is what identifies them, not the channel."""
    from app.core import config

    pending = [
        _pending("a1", "xgov-proposal:42:funded", "crawler", url="https://xgov.algorand.co/proposals/42"),
        _pending("a2", "some-other-crawler-service", "crawler", url="https://example.com/page"),
    ]
    calls = _patch_backfill(monkeypatch, pending=pending, enabled_ids=set())

    result = backfill_missing_venue_service_ids()

    assert calls == [("a1", config.XGOV_VENUE_SERVICE_ID)]
    assert [b["artifact_id"] for b in result["backfilled"]] == ["a1"]


def test_backfill_youtube_and_bluesky_split_on_venue_prefix_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The "<venue>:<item>" prefix becomes venue_service_id once verified against a real, enabled service_registry row."""
    pending = [
        _pending("a1", "algorand-foundation-bsky:3jt2x9y", "bluesky"),
        _pending("a2", "algorand-yt-channel:vid123", "youtube"),
    ]
    calls = _patch_backfill(
        monkeypatch,
        pending=pending,
        enabled_ids={"algorand-foundation-bsky", "algorand-yt-channel"},
    )

    result = backfill_missing_venue_service_ids()

    assert set(calls) == {
        ("a1", "algorand-foundation-bsky"),
        ("a2", "algorand-yt-channel"),
    }
    assert result["flagged"] == []


def test_backfill_flags_composite_id_when_venue_prefix_is_not_a_real_enabled_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The '<venue>:<item>' shape alone is never enough to act on -- the prefix must resolve to a real, currently-enabled service_registry row, or it's flagged for manual review instead of guessed at."""
    pending = [_pending("a1", "unknown-account:abc123", "bluesky")]
    calls = _patch_backfill(monkeypatch, pending=pending, enabled_ids={"some-other-service"})

    result = backfill_missing_venue_service_ids()

    assert calls == []
    assert result["backfilled"] == []
    assert result["flagged"] == [
        {"artifact_id": "a1", "service_id": "unknown-account:abc123", "channel": "bluesky"}
    ]


def test_backfill_skips_artifacts_that_already_have_a_venue_service_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An artifact that already carries a venue_service_id is left untouched -- never re-derived or overwritten."""
    pending = [_pending("a1", "forum-topic:1", "forum", venue="already-set")]
    calls = _patch_backfill(monkeypatch, pending=pending, enabled_ids=set())

    result = backfill_missing_venue_service_ids()

    assert calls == []
    assert result == {"backfilled": [], "flagged": []}


def test_backfill_leaves_plain_web_crawl_artifacts_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """A plain crawl diff has no venue distinct from its own service_id and no ':' in it -- not backfilled, not flagged."""
    pending = [_pending("a1", "algorand-co", "crawler", url="https://algorand.co/blog/post")]
    calls = _patch_backfill(monkeypatch, pending=pending, enabled_ids=set())

    result = backfill_missing_venue_service_ids()

    assert result == {"backfilled": [], "flagged": []}
    assert calls == []


# --------------------------------------------------------------------------- #
# find_duplicate_pending_artifacts / reconcile_duplicate_pending_artifacts
# --------------------------------------------------------------------------- #


def _seed_duplicate_pending(session: FakeArtifactSession) -> tuple[str, str]:
    """Two genuinely separate inserts, then a raw service_id repoint on the second -- exactly how the real pera-wallet incident happened (a direct Cassandra UPDATE bypassing insert_artifact's own dedup check), not something insert_artifact itself could ever produce on its own."""
    from app.modules.newspaper.artifact_store import insert_artifact

    older_id, _ = insert_artifact(
        service_id="pera-wallet", url="https://perawallet.app", channel="crawler",
        content="older crawl of the pera wallet homepage", title="Pera Wallet (older)",
    )
    newer_id, _ = insert_artifact(
        service_id="some-other-temp-id", url="https://perawallet.app/", channel="crawler",
        content="newer crawl of the pera wallet homepage", title="Pera Wallet (newer)",
    )
    # Simulate the raw repoint: directly mutate service_id, bypassing insert_artifact.
    session.artifacts[newer_id]["service_id"] = "pera-wallet"
    for row in session.pending.values():
        if str(row["artifact_id"]) == newer_id:
            row["service_id"] = "pera-wallet"
    return older_id, newer_id


def test_find_duplicate_pending_artifacts_detects_the_violation(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """Two pending artifacts sharing one service_id (via a raw repoint, not insert_artifact) are found as a duplicate."""
    older_id, newer_id = _seed_duplicate_pending(fake_artifact_session)

    dupes = find_duplicate_pending_artifacts()

    assert dupes.keys() == {"pera-wallet"}
    assert set(dupes["pera-wallet"]) == {older_id, newer_id}


def test_find_duplicate_pending_artifacts_ignores_healthy_services(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """Each service_id here has exactly one pending artifact -- no duplicates found."""
    from app.modules.newspaper.artifact_store import insert_artifact

    insert_artifact(service_id="svc-a", url=None, channel="crawler", content="x")
    insert_artifact(service_id="svc-b", url=None, channel="crawler", content="y")

    assert find_duplicate_pending_artifacts() == {}


def test_reconcile_folds_duplicates_to_exactly_one_pending_artifact(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """The fold uses insert_artifact's own concatenation path -- both discarded originals' content survive in the merged survivor, and only one PENDING artifact remains for the service_id afterward."""
    from app.modules.newspaper.artifact_store import (
        DISCARDED,
        PENDING,
        get_artifact_content,
        list_pending_artifacts,
    )

    older_id, newer_id = _seed_duplicate_pending(fake_artifact_session)

    result = reconcile_duplicate_pending_artifacts()

    assert result["merged"][0]["service_id"] == "pera-wallet"
    assert set(result["merged"][0]["artifact_ids"]) == {older_id, newer_id}
    survivor_id = result["merged"][0]["survivor"]

    # Both originals are gone from pending; exactly one pera-wallet artifact remains.
    still_pending = [a for a in list_pending_artifacts() if a.service_id == "pera-wallet"]
    assert [a.artifact_id for a in still_pending] == [survivor_id]

    assert fake_artifact_session.artifacts[older_id]["status"] == DISCARDED
    assert fake_artifact_session.artifacts[newer_id]["status"] == DISCARDED
    assert fake_artifact_session.artifacts[survivor_id]["status"] == PENDING

    merged_content = get_artifact_content(survivor_id)
    assert merged_content is not None
    assert "older crawl of the pera wallet homepage" in merged_content.content
    assert "newer crawl of the pera wallet homepage" in merged_content.content


def test_reconcile_is_a_noop_when_nothing_is_duplicated(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """No duplicates exist, so the sweep merges nothing."""
    from app.modules.newspaper.artifact_store import insert_artifact

    insert_artifact(service_id="svc-a", url=None, channel="crawler", content="x")

    assert reconcile_duplicate_pending_artifacts() == {"merged": []}
