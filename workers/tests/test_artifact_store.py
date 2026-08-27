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
    from algorand_shared.artifact_store import PENDING, insert_artifact

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
    from algorand_shared.artifact_store import (
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
    from algorand_shared.artifact_store import insert_artifact

    id_a, _ = insert_artifact(service_id=None, url=None, channel="brief", content="brief one")
    id_b, _ = insert_artifact(service_id=None, url=None, channel="brief", content="brief two")

    assert id_a != id_b
    assert len(fake_artifact_session.pending) == 2


def test_supersede_carries_forward_the_old_rows_human_pin(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """A pin on a pending artifact must survive a routine re-crawl that supersedes it (2026-08-27 fix).

    Before this fix, insert_artifact's dedup-supersede path hardcoded the
    successor's human_pick_day to None, so a pin died silently the moment
    the same service was re-crawled before the day's select beat ran --
    with no error, since selection only scans PENDING rows and the pinned
    row was gone.
    """
    from algorand_shared.artifact_store import insert_artifact, pin_artifact_for_day

    first_id, _ = insert_artifact(service_id="svc-pin", url="https://x.io/", channel="crawler", content="v1")
    pin_artifact_for_day(first_id, "2026-08-28")

    second_id, _ = insert_artifact(service_id="svc-pin", url="https://x.io/", channel="crawler", content="v2")

    assert second_id != first_id
    assert fake_artifact_session.artifacts[second_id]["human_pick_day"] == "2026-08-28"
    (row,) = fake_artifact_session.pending.values()
    assert str(row["artifact_id"]) == second_id
    assert row["human_pick_day"] == "2026-08-28"


def test_dedup_scoped_to_matching_service_only(fake_artifact_session: FakeArtifactSession) -> None:
    """A diff for a DIFFERENT service_id must not disturb an unrelated service's pending artifact."""
    from algorand_shared.artifact_store import insert_artifact

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
    from algorand_shared.artifact_store import (
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
    from algorand_shared.artifact_store import (
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
    from algorand_shared.artifact_store import insert_artifact, update_artifact_priority

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
    from algorand_shared.artifact_store import get_artifact_content, insert_artifact

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
    from algorand_shared.artifact_store import insert_artifact, pin_artifact_for_day

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

    from algorand_shared.artifact_store import pin_artifact_for_day

    assert pin_artifact_for_day(str(uuid.uuid4()), "2026-08-26") is False


def test_pin_artifact_for_day_rejects_a_non_pending_artifact(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """Pinning anything but a PENDING artifact must fail, not silently no-op (2026-08-27 fix).

    Selection only ever scans PENDING artifacts, so a pin on a
    selected/composed/discarded row could never be honored -- previously
    this returned True anyway, leaving an admin believing a pick was set
    when it never would take effect.
    """
    from algorand_shared.artifact_store import (
        SELECTED,
        insert_artifact,
        mark_artifact_status,
        pin_artifact_for_day,
    )

    artifact_id, _ = insert_artifact(service_id="svc-1", url=None, channel="brief", content="x")
    mark_artifact_status(artifact_id, SELECTED)

    assert pin_artifact_for_day(artifact_id, "2026-08-28") is False
    assert fake_artifact_session.artifacts[artifact_id]["human_pick_day"] is None


def test_pin_artifact_for_day_clears_a_prior_pin_for_the_same_day(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """At most one live pin per day (2026-08-27 fix).

    Pinning a second artifact for a day that already has a pin clears the
    first one, rather than leaving two pending artifacts both claiming the
    same day (selection's "first pending match wins" used to silently pick
    between them, and the loser's pin lingered forever, unexplained).
    """
    from algorand_shared.artifact_store import insert_artifact, pin_artifact_for_day

    first_id, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    second_id, _ = insert_artifact(service_id="svc-b", url=None, channel="brief", content="b")
    pin_artifact_for_day(first_id, "2026-08-28")

    ok = pin_artifact_for_day(second_id, "2026-08-28")

    assert ok is True
    assert fake_artifact_session.artifacts[first_id]["human_pick_day"] is None
    assert fake_artifact_session.artifacts[second_id]["human_pick_day"] == "2026-08-28"


def test_clear_artifact_pin_clears_both_rows(fake_artifact_session: FakeArtifactSession) -> None:
    """Clearing a pin nulls human_pick_day on both the artifacts row and its pending-index row."""
    from algorand_shared.artifact_store import (
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
    from algorand_shared.artifact_store import get_artifact_content, insert_artifact

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
    from algorand_shared.artifact_store import get_artifact_content, insert_artifact

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
    from algorand_shared.artifact_store import get_artifact_content, insert_artifact

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
    from algorand_shared.artifact_store import get_artifact_content, insert_artifact
    from app.core import config as cfg

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

    from algorand_shared.artifact_store import get_artifact, insert_artifact

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
    from algorand_shared.artifact_store import get_artifact_content, insert_artifact

    id_a, _ = insert_artifact(service_id=None, url=None, channel="brief", content="brief one")
    id_b, _ = insert_artifact(service_id=None, url=None, channel="brief", content="brief two")

    content_a = get_artifact_content(id_a)
    content_b = get_artifact_content(id_b)
    assert content_a is not None
    assert content_b is not None
    assert content_a.content == "brief one"
    assert content_b.content == "brief two"


def test_concatenation_venue_service_id_survives_repeated_cycles(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """A real per-item lane (forum/xgov/youtube/bluesky) passes the same venue_service_id on every insert for a given service_id -- confirm it's still correctly carried through 3 concatenation cycles in a row, ending on the latest call's own value."""
    from algorand_shared.artifact_store import get_artifact, insert_artifact

    insert_artifact(
        service_id="svc-venue", url=None, channel="youtube", content="v1", venue_service_id="algorand-yt"
    )
    insert_artifact(
        service_id="svc-venue", url=None, channel="youtube", content="v2", venue_service_id="algorand-yt"
    )
    third_id, _ = insert_artifact(
        service_id="svc-venue", url=None, channel="youtube", content="v3", venue_service_id="algorand-yt"
    )

    artifact = get_artifact(third_id)
    assert artifact is not None
    assert artifact.venue_service_id == "algorand-yt"


def test_concatenation_preserves_venue_service_id_when_a_later_call_omits_it(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """venue_service_id identifies the SERVICE, not one update event, so it must never regress from set to unset just because a later call in the concatenation chain doesn't pass one -- unlike title (which deliberately always takes the latest). This is exactly the shape reconcile_duplicate_pending_artifacts's fold can hit when only one of several duplicates ever got backfilled (see test_service_reconciliation.py)."""
    from algorand_shared.artifact_store import get_artifact, insert_artifact

    insert_artifact(
        service_id="svc-venue2", url=None, channel="bluesky", content="v1", venue_service_id="acct-bsky"
    )
    second_id, _ = insert_artifact(
        service_id="svc-venue2", url=None, channel="bluesky", content="v2", venue_service_id=None
    )

    artifact = get_artifact(second_id)
    assert artifact is not None
    assert artifact.venue_service_id == "acct-bsky"


# --------------------------------------------------------------------------- #
# revert_artifact_to_pending (2026-08-26) -- the reverse of mark_artifact_status's
# pending -> non-pending move, used by to_compose_selection.reset_to_compose_for_day
# to undo a SELECTED artifact's status flip when an admin redoes a day's picks.
# --------------------------------------------------------------------------- #


def test_revert_artifact_to_pending_moves_selected_back_and_reindexes(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """A SELECTED artifact reverts to PENDING status and gets a fresh artifacts_pending row (it was removed when mark_artifact_status moved it to SELECTED in the first place)."""
    from algorand_shared.artifact_store import (
        PENDING,
        SELECTED,
        insert_artifact,
        mark_artifact_status,
        revert_artifact_to_pending,
    )

    artifact_id, _ = insert_artifact(
        service_id="svc-1", url="https://x.io/", channel="crawler", content="x"
    )
    mark_artifact_status(artifact_id, SELECTED)
    assert fake_artifact_session.pending == {}

    ok = revert_artifact_to_pending(artifact_id)

    assert ok is True
    assert fake_artifact_session.artifacts[artifact_id]["status"] == PENDING
    (row,) = fake_artifact_session.pending.values()
    assert str(row["artifact_id"]) == artifact_id
    assert row["service_id"] == "svc-1"
    assert row["status"] == PENDING


def test_revert_artifact_to_pending_is_a_noop_for_composed(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """A COMPOSED artifact (already turned into a real article) must never be silently resurrected back to pending -- returns False and leaves it untouched."""
    from algorand_shared.artifact_store import (
        COMPOSED,
        insert_artifact,
        mark_artifact_status,
        revert_artifact_to_pending,
    )

    artifact_id, _ = insert_artifact(service_id="svc-1", url=None, channel="brief", content="x")
    mark_artifact_status(artifact_id, COMPOSED)

    ok = revert_artifact_to_pending(artifact_id)

    assert ok is False
    assert fake_artifact_session.artifacts[artifact_id]["status"] == COMPOSED
    assert fake_artifact_session.pending == {}


def test_revert_artifact_to_pending_is_a_noop_for_discarded(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """A DISCARDED artifact (a pre-compose gate permanently dropped it) must also never be silently resurrected -- same guard as the composed case."""
    from algorand_shared.artifact_store import (
        DISCARDED,
        insert_artifact,
        mark_artifact_status,
        revert_artifact_to_pending,
    )

    artifact_id, _ = insert_artifact(service_id="svc-1", url=None, channel="brief", content="x")
    mark_artifact_status(artifact_id, DISCARDED)

    ok = revert_artifact_to_pending(artifact_id)

    assert ok is False
    assert fake_artifact_session.artifacts[artifact_id]["status"] == DISCARDED


def test_revert_artifact_to_pending_is_a_noop_for_already_pending(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """An artifact that's already PENDING (never selected) is left alone -- no duplicate pending-index row."""
    from algorand_shared.artifact_store import insert_artifact, revert_artifact_to_pending

    artifact_id, _ = insert_artifact(service_id="svc-1", url=None, channel="brief", content="x")

    ok = revert_artifact_to_pending(artifact_id)

    assert ok is False
    assert len(fake_artifact_session.pending) == 1


def test_revert_artifact_to_pending_unknown_id_returns_false(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """An artifact_id with no matching row returns False rather than raising."""
    import uuid

    from algorand_shared.artifact_store import revert_artifact_to_pending

    assert revert_artifact_to_pending(str(uuid.uuid4())) is False


def test_revert_artifact_to_pending_malformed_id_returns_false(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """A non-UUID artifact_id fails closed to False rather than raising, matching pin_artifact_for_day's own contract for a bad id."""
    from algorand_shared.artifact_store import revert_artifact_to_pending

    assert revert_artifact_to_pending("not-a-uuid") is False


# --------------------------------------------------------------------------- #
# venue_service_id (bug-class-2 fix: per-item lanes carry a stable venue id
# distinct from their own per-item service_id)
# --------------------------------------------------------------------------- #


def test_insert_artifact_stores_venue_service_id(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """venue_service_id round-trips through both the artifacts row and its pending-index row -- both list_pending_artifacts and get_artifact must be able to read it back."""
    from algorand_shared.artifact_store import get_artifact, insert_artifact, list_pending_artifacts

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
    from algorand_shared.artifact_store import insert_artifact, list_pending_artifacts

    insert_artifact(service_id="svc-1", url=None, channel="crawler", content="x")

    (listed,) = list_pending_artifacts()
    assert listed.venue_service_id is None


def test_update_artifact_priority_preserves_venue_service_id_on_reindex(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """Re-scoring an artifact deletes+reinserts its pending-index row (priority is part of that index's clustering key) -- venue_service_id must survive that round trip, not silently drop."""
    from algorand_shared.artifact_store import (
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
    from algorand_shared.artifact_store import (
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
    from algorand_shared.artifact_store import set_artifact_venue_service_id

    assert set_artifact_venue_service_id("not-a-real-uuid", "algorand-forum") is False


# --------------------------------------------------------------------------- #
# Artifact instance methods (2026-08-27) -- thin delegates to this module's
# own free functions, for one obvious way to act on an artifact you already
# hold instead of every caller re-importing mark_artifact_status/etc.
# --------------------------------------------------------------------------- #


def test_artifact_load_is_an_alias_for_get_artifact(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """Artifact.load is a classmethod alias for the module-level get_artifact."""
    from algorand_shared.artifact_store import Artifact, insert_artifact

    artifact_id, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")

    loaded = Artifact.load(artifact_id)

    assert loaded is not None
    assert loaded.artifact_id == artifact_id
    assert fake_artifact_session.artifacts[artifact_id]["status"] == loaded.status


def test_artifact_mark_composed(fake_artifact_session: FakeArtifactSession) -> None:
    """.mark_composed() transitions the artifact to COMPOSED."""
    from algorand_shared.artifact_store import COMPOSED, Artifact, insert_artifact

    artifact_id, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    artifact = Artifact.load(artifact_id)

    artifact.mark_composed()

    assert fake_artifact_session.artifacts[artifact_id]["status"] == COMPOSED


def test_artifact_mark_discarded(fake_artifact_session: FakeArtifactSession) -> None:
    """.mark_discarded() transitions the artifact to DISCARDED."""
    from algorand_shared.artifact_store import DISCARDED, Artifact, insert_artifact

    artifact_id, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    artifact = Artifact.load(artifact_id)

    artifact.mark_discarded()

    assert fake_artifact_session.artifacts[artifact_id]["status"] == DISCARDED


def test_artifact_revert_to_pending_only_takes_effect_when_selected(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """.revert_to_pending() moves a SELECTED artifact back to PENDING and returns True."""
    from algorand_shared.artifact_store import (
        PENDING,
        SELECTED,
        Artifact,
        insert_artifact,
        mark_artifact_status,
    )

    artifact_id, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    mark_artifact_status(artifact_id, SELECTED)
    artifact = Artifact.load(artifact_id)

    reverted = artifact.revert_to_pending()

    assert reverted is True
    assert fake_artifact_session.artifacts[artifact_id]["status"] == PENDING


def test_artifact_pin_for_day_refuses_a_non_pending_artifact(
    fake_artifact_session: FakeArtifactSession,  # noqa: ARG001 -- activates the fixture's monkeypatch
) -> None:
    """.pin_for_day() refuses a non-PENDING artifact -- a pin on it could never be honored."""
    from algorand_shared.artifact_store import (
        SELECTED,
        Artifact,
        insert_artifact,
        mark_artifact_status,
    )

    artifact_id, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    mark_artifact_status(artifact_id, SELECTED)
    artifact = Artifact.load(artifact_id)

    assert artifact.pin_for_day("2026-08-28") is False


def test_artifact_pin_and_clear_pin(fake_artifact_session: FakeArtifactSession) -> None:
    """.pin_for_day() sets human_pick_day; .clear_pin() nulls it again."""
    from algorand_shared.artifact_store import Artifact, insert_artifact

    artifact_id, _ = insert_artifact(service_id="svc-a", url=None, channel="brief", content="a")
    artifact = Artifact.load(artifact_id)

    assert artifact.pin_for_day("2026-08-28") is True
    assert fake_artifact_session.artifacts[artifact_id]["human_pick_day"] == "2026-08-28"

    artifact.clear_pin()
    assert fake_artifact_session.artifacts[artifact_id]["human_pick_day"] is None


def test_artifact_set_venue_service_id(fake_artifact_session: FakeArtifactSession) -> None:
    """.set_venue_service_id() backfills venue_service_id on the artifacts row."""
    from algorand_shared.artifact_store import Artifact, insert_artifact

    artifact_id, _ = insert_artifact(
        service_id="xgov-proposal:101:voting", url=None, channel="crawler", content="proposal"
    )
    artifact = Artifact.load(artifact_id)

    assert artifact.set_venue_service_id("xgov-algorand-co") is True
    assert fake_artifact_session.artifacts[artifact_id]["venue_service_id"] == "xgov-algorand-co"
