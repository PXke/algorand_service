"""x_search_store: Cassandra persistence for the weekly per-service X search cache (x_search_weekly, migration 074)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock

from app.modules.newspaper.x_search_store import list_snapshots, save_snapshot


def test_save_snapshot_upserts_json_encoded_posts(fake_cassandra_session: MagicMock) -> None:
    """A saved snapshot upserts one row keyed on service_id, with posts JSON-encoded."""
    save_snapshot(
        service_id="folks-finance",
        display_name="Folks Finance",
        query="Folks Finance",
        posts=[{"text": "hello", "likes": 1, "reposts": 0, "replies": 0, "url": ""}],
        error="",
    )
    fake_cassandra_session.execute.assert_called_once()
    args = fake_cassandra_session.execute.call_args.args[1]
    service_id, display_name, query, posts_json, post_count, swept_at, error = args
    assert service_id == "folks-finance"
    assert display_name == "Folks Finance"
    assert query == "Folks Finance"
    assert json.loads(posts_json) == [{"text": "hello", "likes": 1, "reposts": 0, "replies": 0, "url": ""}]
    assert post_count == 1
    assert isinstance(swept_at, datetime)
    assert error == ""


def test_save_snapshot_records_a_sweep_error_with_zero_posts(
    fake_cassandra_session: MagicMock,
) -> None:
    """A failed sweep call for a service still writes a row, empty posts + a truncated error."""
    save_snapshot(
        service_id="tinyman", display_name="Tinyman", query="Tinyman", posts=[], error="boom" * 100
    )
    args = fake_cassandra_session.execute.call_args.args[1]
    posts_json, post_count, _swept_at, error = args[3], args[4], args[5], args[6]
    assert posts_json == "[]"
    assert post_count == 0
    # Errors are truncated to 200 chars, same convention as every other tool error field.
    assert len(error) == 200


def test_list_snapshots_decodes_every_row(fake_cassandra_session: MagicMock) -> None:
    """Every stored row decodes into an XSearchSnapshot, posts JSON round-tripped to a tuple."""
    rows = [
        MagicMock(
            service_id="folks-finance",
            display_name="Folks Finance",
            query="Folks Finance",
            posts_json=json.dumps([{"text": "hi"}]),
            post_count=1,
            swept_at=datetime(2026, 8, 24, tzinfo=UTC),
            error="",
        ),
        MagicMock(
            service_id="tinyman",
            display_name="Tinyman",
            query="Tinyman",
            posts_json="",
            post_count=0,
            swept_at=datetime(2026, 8, 24, tzinfo=UTC),
            error="ConnectError: boom",
        ),
    ]
    fake_cassandra_session.execute.return_value = rows

    snapshots = list_snapshots()

    assert len(snapshots) == 2
    assert snapshots[0].service_id == "folks-finance"
    assert snapshots[0].posts == ({"text": "hi"},)
    assert snapshots[1].posts == ()
    assert snapshots[1].error == "ConnectError: boom"


def test_list_snapshots_tolerates_malformed_json(fake_cassandra_session: MagicMock) -> None:
    """A corrupted posts_json row degrades to an empty post list instead of raising."""
    rows = [
        MagicMock(
            service_id="broken",
            display_name="Broken",
            query="Broken",
            posts_json="{not valid json",
            post_count=0,
            swept_at=None,
            error="",
        )
    ]
    fake_cassandra_session.execute.return_value = rows

    snapshots = list_snapshots()

    assert snapshots[0].posts == ()
    assert snapshots[0].swept_at is None
