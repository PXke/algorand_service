"""Editorial-room `artifacts` store (2026-08-25, SHADOW MODE): insert/dedup, status transitions, priority reindexing, and the human-pin hook.

Uses the shared `fake_artifact_session` fixture / `FakeArtifactSession` from
conftest.py -- an in-memory emulation of the artifacts / artifacts_pending /
artifact_content / to_compose tables keyed by the exact CQL text of
`app.core.statements.ArtifactStmts` / `ToComposeStmts`, so these tests
exercise the real store code against its real prepared-statement call sites,
not just a call-capturing mock. Also used by test_artifact_priority.py and
test_to_compose_selection.py, which need the same tables wired together end
to end.
"""

from __future__ import annotations

import pytest
from conftest import FakeArtifactSession

# --------------------------------------------------------------------------- #
# insert_artifact / dedup
# --------------------------------------------------------------------------- #


def test_insert_artifact_creates_pending_row_and_content(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """A fresh insert lands in `artifacts`, `artifacts_pending`, and `artifact_content`."""
    from app.modules.newspaper.artifact_store import PENDING, insert_artifact

    artifact_id, created = insert_artifact(
        service_id="svc-1",
        url="https://example.com/a",
        channel="crawler",
        content="hello world " * 20,
        title="A diff",
    )
    assert created is True
    assert fake_artifact_session.artifacts[artifact_id]["status"] == PENDING
    assert len(fake_artifact_session.pending) == 1
    assert fake_artifact_session.content[artifact_id]["title"] == "A diff"


def test_second_diff_for_same_service_replaces_pending_row_but_concatenates_content(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """Dedup invariant: at most one PENDING artifact ROW per service_id -- a new diff for a service_id that already has one pending still REPLACES the row (delete-old + insert-new), mirroring publish_queue_store.enqueue_publish's identical rule for the row. 2026-08-26: unlike that rule, the CONTENT is no longer replaced outright -- it's concatenated (old + new), so the second insert's stored content contains BOTH versions, not just the latest."""
    from app.modules.newspaper.artifact_store import (
        DISCARDED,
        PENDING,
        get_artifact_content,
        insert_artifact,
    )

    first_id, _ = insert_artifact(
        service_id="svc-dup", url="https://x.io/", channel="crawler", content="first version"
    )
    second_id, _ = insert_artifact(
        service_id="svc-dup", url="https://x.io/", channel="crawler", content="second version"
    )

    assert first_id != second_id
    # Only ONE pending ROW for the service, and it's the new one.
    pending_for_service = [
        r for r in fake_artifact_session.pending.values() if r["service_id"] == "svc-dup"
    ]
    assert len(pending_for_service) == 1
    assert str(pending_for_service[0]["artifact_id"]) == second_id
    # The old artifact row still exists but is discarded, not deleted outright.
    assert fake_artifact_session.artifacts[first_id]["status"] == DISCARDED
    assert fake_artifact_session.artifacts[second_id]["status"] == PENDING
    # But the CONTENT is concatenated, not replaced -- both versions survive
    # in the new pending row's content.
    merged = get_artifact_content(second_id)
    assert merged is not None
    assert "first version" in merged.content
    assert "second version" in merged.content
    # The new content comes after the old content (old = "earlier", new =
    # "latest"), and the two are clearly separated, not run together.
    assert merged.content.index("first version") < merged.content.index("second version")
    assert "---" in merged.content


def test_artifacts_with_no_service_id_never_dedup_against_each_other(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """A service_id-less artifact (a brief, an unlinked mail message) must never be treated as a duplicate of another service_id-less artifact."""
    from app.modules.newspaper.artifact_store import insert_artifact

    id_a, _ = insert_artifact(service_id=None, url=None, channel="brief", content="brief one")
    id_b, _ = insert_artifact(service_id=None, url=None, channel="brief", content="brief two")

    assert id_a != id_b
    assert len(fake_artifact_session.pending) == 2


def test_dedup_scoped_to_matching_service_only(fake_artifact_session: FakeArtifactSession) -> None:
    """A diff for a DIFFERENT service_id must not disturb an unrelated service's pending artifact."""
    from app.modules.newspaper.artifact_store import insert_artifact

    id_a, _ = insert_artifact(service_id="svc-a", url="https://a.io/", channel="crawler", content="a")
    id_b, _ = insert_artifact(service_id="svc-b", url="https://b.io/", channel="crawler", content="b")

    assert len(fake_artifact_session.pending) == 2
    assert fake_artifact_session.artifacts[id_a]["status"] == "pending"
    assert fake_artifact_session.artifacts[id_b]["status"] == "pending"


# --------------------------------------------------------------------------- #
# list / get / status transitions
# --------------------------------------------------------------------------- #


def test_list_pending_artifacts_orders_by_priority_desc(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """Pending artifacts come back highest-priority first."""
    from app.modules.newspaper.artifact_store import (
        insert_artifact,
        list_pending_artifacts,
        update_artifact_priority,
    )

    low_id, _ = insert_artifact(service_id="svc-low", url=None, channel="brief", content="low")
    high_id, _ = insert_artifact(service_id="svc-high", url=None, channel="brief", content="high")
    update_artifact_priority(low_id, 1.0)
    update_artifact_priority(high_id, 9.0)

    ordered = list_pending_artifacts()
    assert [a.artifact_id for a in ordered] == [high_id, low_id]


def test_mark_artifact_status_removes_from_pending_index(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """Moving an artifact to a terminal status drops its pending-index row."""
    from app.modules.newspaper.artifact_store import (
        COMPOSED,
        insert_artifact,
        list_pending_artifacts,
        mark_artifact_status,
    )

    artifact_id, _ = insert_artifact(service_id="svc-1", url=None, channel="brief", content="x")
    mark_artifact_status(artifact_id, COMPOSED)

    assert fake_artifact_session.artifacts[artifact_id]["status"] == COMPOSED
    assert list_pending_artifacts() == []


def test_update_artifact_priority_reindexes_pending_row(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """Priority is part of artifacts_pending's clustering key -- a re-score must delete the old pending-index row and insert a fresh one, not silently desync it."""
    from app.modules.newspaper.artifact_store import insert_artifact, update_artifact_priority

    artifact_id, _ = insert_artifact(service_id="svc-1", url=None, channel="brief", content="x")
    assert len(fake_artifact_session.pending) == 1

    update_artifact_priority(artifact_id, 7.5)

    assert len(fake_artifact_session.pending) == 1  # re-keyed, not duplicated
    (row,) = fake_artifact_session.pending.values()
    assert row["priority"] == 7.5
    assert fake_artifact_session.artifacts[artifact_id]["priority"] == 7.5


def test_get_artifact_content_round_trips_metadata_json(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """Content metadata round-trips through JSON encode/decode intact."""
    from app.modules.newspaper.artifact_store import get_artifact_content, insert_artifact

    artifact_id, _ = insert_artifact(
        service_id=None,
        url=None,
        channel="mail",
        content="body text",
        title="subject",
        metadata={"from": "news@example.com"},
    )
    content = get_artifact_content(artifact_id)
    assert content is not None
    assert content.metadata == {"from": "news@example.com"}


# --------------------------------------------------------------------------- #
# human-pin hook
# --------------------------------------------------------------------------- #


def test_pin_artifact_for_day_sets_both_artifacts_and_pending_row(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """Pinning updates human_pick_day on both the artifacts row and its pending-index row."""
    from app.modules.newspaper.artifact_store import insert_artifact, pin_artifact_for_day

    artifact_id, _ = insert_artifact(service_id="svc-1", url=None, channel="brief", content="x")
    ok = pin_artifact_for_day(artifact_id, "2026-08-26")

    assert ok is True
    assert fake_artifact_session.artifacts[artifact_id]["human_pick_day"] == "2026-08-26"
    (row,) = fake_artifact_session.pending.values()
    assert row["human_pick_day"] == "2026-08-26"


def test_pin_artifact_for_day_unknown_id_returns_false(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """An artifact_id with no matching row returns False rather than raising."""
    import uuid

    from app.modules.newspaper.artifact_store import pin_artifact_for_day

    assert pin_artifact_for_day(str(uuid.uuid4()), "2026-08-26") is False


def test_clear_artifact_pin_clears_both_rows(fake_artifact_session: FakeArtifactSession) -> None:
    """Clearing a pin nulls human_pick_day on both the artifacts row and its pending-index row."""
    from app.modules.newspaper.artifact_store import (
        clear_artifact_pin,
        insert_artifact,
        pin_artifact_for_day,
    )

    artifact_id, _ = insert_artifact(service_id="svc-1", url=None, channel="brief", content="x")
    pin_artifact_for_day(artifact_id, "2026-08-26")
    clear_artifact_pin(artifact_id)

    assert fake_artifact_session.artifacts[artifact_id]["human_pick_day"] is None
    (row,) = fake_artifact_session.pending.values()
    assert row["human_pick_day"] is None


# --------------------------------------------------------------------------- #
# Per-service concatenation (2026-08-26) -- see insert_artifact's own
# docstring: a new artifact for a service_id that already has a pending
# artifact no longer replaces its content outright, it concatenates onto it.
# --------------------------------------------------------------------------- #


def test_concatenation_title_stays_the_latest_updates_own_title(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """Design decision: `title` is NOT merged/concatenated -- it stays the newest update's own title (the simplest option, and the right one for a compose step's headline anchor). The old title is still preserved inside the concatenated BODY's "Earlier update" heading, just not in the `title` field itself."""
    from app.modules.newspaper.artifact_store import get_artifact_content, insert_artifact

    insert_artifact(
        service_id="svc-t", url=None, channel="crawler", content="old body", title="Old Headline"
    )
    second_id, _ = insert_artifact(
        service_id="svc-t", url=None, channel="crawler", content="new body", title="New Headline"
    )

    merged = get_artifact_content(second_id)
    assert merged is not None
    assert merged.title == "New Headline"
    assert "Old Headline" in merged.content
    assert "New Headline" in merged.content


def test_concatenation_merges_metadata_into_a_segments_trail(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """The new artifact's OWN top-level metadata wins (so existing readers of e.g. metadata["dual_write_queue_id"] keep reading the LATEST signal's value), and the old artifact's full metadata is preserved verbatim as one entry in metadata["segments"] rather than being dropped."""
    from app.modules.newspaper.artifact_store import get_artifact_content, insert_artifact

    insert_artifact(
        service_id="svc-m",
        url="https://old.example/",
        channel="crawler",
        content="old body",
        title="old title",
        metadata={"dual_write_queue_id": "queue-old", "source_kind": "forum"},
    )
    second_id, _ = insert_artifact(
        service_id="svc-m",
        url="https://new.example/",
        channel="crawler",
        content="new body",
        title="new title",
        metadata={"dual_write_queue_id": "queue-new", "source_kind": "forum"},
    )

    merged = get_artifact_content(second_id)
    assert merged is not None
    # Top-level metadata is the NEW signal's own -- e.g. the dual-write
    # resolve step must mirror the latest, not a stale, queue_id.
    assert merged.metadata["dual_write_queue_id"] == "queue-new"
    # The old artifact's metadata (plus its own title/url, which live outside
    # the metadata blob as separate columns) is preserved in "segments".
    segments = merged.metadata["segments"]
    assert len(segments) == 1
    assert segments[0]["dual_write_queue_id"] == "queue-old"
    assert segments[0]["_title"] == "old title"
    assert segments[0]["_url"] == "https://old.example/"


def test_concatenation_chains_across_three_unaddressed_updates(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """A service updated three times with nobody composing in between accumulates ALL THREE versions in the final pending row's content, and the segments trail grows by one each cycle -- the owner's explicit "3 small unaddressed updates should read as more substantial" pressure-release."""
    from app.modules.newspaper.artifact_store import get_artifact_content, insert_artifact

    insert_artifact(service_id="svc-chain", url=None, channel="crawler", content="update one")
    insert_artifact(service_id="svc-chain", url=None, channel="crawler", content="update two")
    third_id, _ = insert_artifact(
        service_id="svc-chain", url=None, channel="crawler", content="update three"
    )

    merged = get_artifact_content(third_id)
    assert merged is not None
    assert "update one" in merged.content
    assert "update two" in merged.content
    assert "update three" in merged.content
    assert len(merged.metadata["segments"]) == 2
    # Only ONE pending row survives for the service at any point.
    pending_for_service = [
        r for r in fake_artifact_session.pending.values() if r["service_id"] == "svc-chain"
    ]
    assert len(pending_for_service) == 1


def test_concatenation_caps_accumulated_old_content_and_keeps_newest_intact(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """ARTIFACT_CONCAT_MAX_OLD_CHARS bounds the ACCUMULATED-OLD portion so a service updating constantly without ever composing can't grow the row without bound. The cap trims from the FRONT (oldest first) -- the newest content passed to insert_artifact is never trimmed."""
    from app.core import config as cfg
    from app.modules.newspaper.artifact_store import get_artifact_content, insert_artifact

    monkeypatch.setattr(cfg, "ARTIFACT_CONCAT_MAX_OLD_CHARS", 50)

    old_content = "x" * 500  # far past the 50-char cap
    insert_artifact(service_id="svc-cap", url=None, channel="crawler", content=old_content)
    new_id, _ = insert_artifact(
        service_id="svc-cap", url=None, channel="crawler", content="brand new content intact"
    )

    merged = get_artifact_content(new_id)
    assert merged is not None
    assert "brand new content intact" in merged.content
    # Old content was capped, not carried in full.
    assert merged.content.count("x") < 500
    assert "truncated" in merged.content


def test_concatenation_new_event_date_and_created_at_track_the_newest_update(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """Design decision: event_date/created_at are NOT widened into a range across concatenated segments -- the merged row simply carries THIS call's own (newest) event_date/created_at, so timeliness_score reflects how fresh the most recent activity is. word_count_score (over the now-longer concatenated content) is what reflects the accumulation instead."""
    import datetime as dt

    from app.modules.newspaper.artifact_store import get_artifact, insert_artifact

    old_event = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    new_event = dt.datetime(2026, 8, 20, tzinfo=dt.UTC)
    old_now = dt.datetime(2026, 1, 1, 12, 0, tzinfo=dt.UTC)
    new_now = dt.datetime(2026, 8, 20, 12, 0, tzinfo=dt.UTC)

    insert_artifact(
        service_id="svc-date",
        url=None,
        channel="crawler",
        content="old",
        event_date=old_event,
        now=old_now,
    )
    second_id, _ = insert_artifact(
        service_id="svc-date",
        url=None,
        channel="crawler",
        content="new",
        event_date=new_event,
        now=new_now,
    )

    artifact = get_artifact(second_id)
    assert artifact is not None
    assert artifact.event_date == new_event
    assert artifact.created_at == new_now


def test_no_service_id_artifacts_never_concatenate(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """service_id-less artifacts (a brief, an unlinked mail message) never dedup against each other (existing invariant), so they never trigger concatenation either -- each keeps its own standalone content untouched."""
    from app.modules.newspaper.artifact_store import get_artifact_content, insert_artifact

    id_a, _ = insert_artifact(service_id=None, url=None, channel="brief", content="brief one")
    id_b, _ = insert_artifact(service_id=None, url=None, channel="brief", content="brief two")

    content_a = get_artifact_content(id_a)
    content_b = get_artifact_content(id_b)
    assert content_a is not None
    assert content_b is not None
    assert content_a.content == "brief one"
    assert content_b.content == "brief two"


# --------------------------------------------------------------------------- #
# venue_service_id (bug-class-2 fix: per-item lanes carry a stable venue id
# distinct from their own per-item service_id)
# --------------------------------------------------------------------------- #


def test_insert_artifact_stores_venue_service_id(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """venue_service_id round-trips through both the artifacts row and its pending-index row -- both list_pending_artifacts and get_artifact must be able to read it back."""
    from app.modules.newspaper.artifact_store import get_artifact, insert_artifact, list_pending_artifacts

    artifact_id, _ = insert_artifact(
        service_id="forum-topic:15288",
        url="https://forum.algorand.co/t/wormhole-ntt/15288",
        channel="forum",
        content="hot topic",
        venue_service_id="algorand-forum",
    )

    assert fake_artifact_session.artifacts[artifact_id]["venue_service_id"] == "algorand-forum"
    (pending_row,) = fake_artifact_session.pending.values()
    assert pending_row["venue_service_id"] == "algorand-forum"

    fetched = get_artifact(artifact_id)
    assert fetched is not None
    assert fetched.venue_service_id == "algorand-forum"

    (listed,) = list_pending_artifacts()
    assert listed.venue_service_id == "algorand-forum"


def test_insert_artifact_venue_service_id_defaults_to_none(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """A plain web crawl diff (no venue distinct from its own service_id) leaves venue_service_id unset."""
    from app.modules.newspaper.artifact_store import insert_artifact, list_pending_artifacts

    insert_artifact(service_id="svc-1", url=None, channel="crawler", content="x")

    (listed,) = list_pending_artifacts()
    assert listed.venue_service_id is None


def test_update_artifact_priority_preserves_venue_service_id_on_reindex(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """Re-scoring an artifact deletes+reinserts its pending-index row (priority is part of that index's clustering key) -- venue_service_id must survive that round trip, not silently drop."""
    from app.modules.newspaper.artifact_store import (
        insert_artifact,
        list_pending_artifacts,
        update_artifact_priority,
    )

    artifact_id, _ = insert_artifact(
        service_id="youtube-chan:vid1",
        url=None,
        channel="youtube",
        content="video",
        venue_service_id="youtube-chan",
    )
    update_artifact_priority(artifact_id, 4.2)

    (listed,) = list_pending_artifacts()
    assert listed.venue_service_id == "youtube-chan"
    assert listed.priority == 4.2


def test_set_artifact_venue_service_id_backfills_existing_pending_artifact(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """The reconciliation safety net's write path: backfilling venue_service_id on an artifact that landed without one updates both the artifacts row and, while still pending, its pending-index row."""
    from app.modules.newspaper.artifact_store import (
        insert_artifact,
        list_pending_artifacts,
        set_artifact_venue_service_id,
    )

    artifact_id, _ = insert_artifact(
        service_id="xgov-proposal:101:voting", url=None, channel="crawler", content="proposal"
    )

    ok = set_artifact_venue_service_id(artifact_id, "xgov-algorand-co")

    assert ok is True
    assert fake_artifact_session.artifacts[artifact_id]["venue_service_id"] == "xgov-algorand-co"
    (listed,) = list_pending_artifacts()
    assert listed.venue_service_id == "xgov-algorand-co"


def test_set_artifact_venue_service_id_unknown_id_returns_false(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """An unknown/malformed artifact_id is a no-op, not an exception."""
    from app.modules.newspaper.artifact_store import set_artifact_venue_service_id

    assert set_artifact_venue_service_id("not-a-real-uuid", "algorand-forum") is False
