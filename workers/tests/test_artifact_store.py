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


def test_second_diff_for_same_service_replaces_pending_artifact(
    fake_artifact_session: FakeArtifactSession,
) -> None:
    """Dedup invariant: at most one PENDING artifact per service_id. A new diff for a service_id that already has one pending REPLACES it (delete-old + insert-new), mirroring publish_queue_store.enqueue_publish's identical rule -- it does not accumulate a second pending row."""
    from app.modules.newspaper.artifact_store import DISCARDED, PENDING, insert_artifact

    first_id, _ = insert_artifact(
        service_id="svc-dup", url="https://x.io/", channel="crawler", content="first version"
    )
    second_id, _ = insert_artifact(
        service_id="svc-dup", url="https://x.io/", channel="crawler", content="second version"
    )

    assert first_id != second_id
    # Only ONE pending row for the service, and it's the new one.
    pending_for_service = [
        r for r in fake_artifact_session.pending.values() if r["service_id"] == "svc-dup"
    ]
    assert len(pending_for_service) == 1
    assert str(pending_for_service[0]["artifact_id"]) == second_id
    # The old artifact row still exists but is discarded, not deleted outright.
    assert fake_artifact_session.artifacts[first_id]["status"] == DISCARDED
    assert fake_artifact_session.artifacts[second_id]["status"] == PENDING


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
