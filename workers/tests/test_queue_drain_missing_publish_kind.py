"""_artifact_to_queued_row must default a missing publish_kind, not crash.

Root-caused 2026-08-31: the 2026-08-25 publish_queue->artifacts migration
backfilled artifacts with no "payload" in their content metadata at all, so
publish_kind read back as "" -- and PublishKind("") raises in
_publish_standard_row, silently failing (and permanently stalling) every
drain_to_compose run that drew one of them. 49 of a 50-artifact live PENDING
sample were affected. See queue_drain_tasks._artifact_to_queued_row's own
comment for why SERVICE_DISCOVERY (not CONTENT_UPDATE) is the correct
default.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import pytest
from algorand_shared.artifact_store import Artifact, ArtifactContent

from app.modules.newspaper.publish_policy import PublishKind
from app.modules.newspaper.tasks import queue_drain_tasks as qdt


def _artifact(**overrides: object) -> Artifact:
    base: dict[str, object] = {
        "artifact_id": "a1",
        "service_id": "pera-wallet",
        "url": "https://pera.com",
        "channel": "crawler",
        "created_at": datetime(2026, 8, 25, 16, 46, tzinfo=UTC),
        "event_date": None,
        "priority": 24.4566,
        "priority_computed_at": None,
        "status": "selected",
    }
    base.update(overrides)
    return Artifact(**base)  # type: ignore[arg-type]


def test_a_missing_payload_defaults_publish_kind_to_service_discovery(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The exact migration-gap shape: metadata carries no "payload" key at all."""
    content = ArtifactContent(
        artifact_id="a1", title="Pera Wallet update", content="body text", metadata={}
    )

    with caplog.at_level(logging.WARNING):
        row = qdt._artifact_to_queued_row(_artifact(), content)

    assert row.publish_kind == PublishKind.SERVICE_DISCOVERY.value
    assert any("defaulting to service_discovery" in rec.message for rec in caplog.records)


def test_an_empty_string_publish_kind_also_defaults() -> None:
    """Same fix covers a payload that explicitly stored "" (not just a missing key)."""
    content = ArtifactContent(
        artifact_id="a1",
        title="t",
        content="body",
        metadata={"payload": {"publish_kind": ""}},
    )

    row = qdt._artifact_to_queued_row(_artifact(), content)

    assert row.publish_kind == PublishKind.SERVICE_DISCOVERY.value


def test_a_real_publish_kind_is_never_overridden() -> None:
    """Sanity counterpart: a normal, post-cutover artifact keeps its own kind untouched."""
    content = ArtifactContent(
        artifact_id="a1",
        title="t",
        content="body",
        metadata={"payload": {"publish_kind": PublishKind.CONTENT_UPDATE.value, "diff": "+line"}},
    )

    row = qdt._artifact_to_queued_row(_artifact(), content)

    assert row.publish_kind == PublishKind.CONTENT_UPDATE.value


def test_no_content_row_at_all_still_defaults_publish_kind() -> None:
    """content=None (a stashed-content read miss) must not crash the row build either."""
    row = qdt._artifact_to_queued_row(_artifact(), None)

    assert row.publish_kind == PublishKind.SERVICE_DISCOVERY.value
