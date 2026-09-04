"""Regression: AdminCassandraStore query-failure logging.

list_briefs / _pending_review_details must log a warning when the underlying
Cassandra query fails, instead of silently returning [] with zero trace
(CLAUDE.md section 3: a broad except logs at >= warning with context;
section 2 item 8's "empty is not none found" principle applies here too --
an admin operator must be able to tell "genuinely empty" from "the query
errored").
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from app.modules.admin.stores.cassandra import AdminCassandraStore


class _RaisingSession:
    """Stand-in Cassandra session whose every execute() call raises."""

    def execute(self, _stmt: object, _params: tuple = ()) -> object:
        raise RuntimeError("cassandra down")

    def prepare(self, cql: str) -> str:
        return cql


def test_list_briefs_logs_warning_on_query_failure(caplog: pytest.LogCaptureFixture) -> None:
    """list_briefs fails to [] but logs the Cassandra failure."""
    store = AdminCassandraStore()
    with (
        patch("app.core.cassandra.get_cassandra_session", return_value=_RaisingSession()),
        caplog.at_level(logging.WARNING, logger="app.modules.admin.stores.cassandra"),
    ):
        result = store.list_briefs()

    assert result == []
    assert any(
        "list_briefs" in r.message and "Cassandra query failed" in r.message for r in caplog.records
    )


def test_pending_review_details_logs_warning_on_query_failure(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_pending_review_details fails to [] but logs the Cassandra failure."""
    store = AdminCassandraStore()
    with (
        patch("app.core.cassandra.get_cassandra_session", return_value=_RaisingSession()),
        caplog.at_level(logging.WARNING, logger="app.modules.admin.stores.cassandra"),
    ):
        result = store._pending_review_details(50)

    assert result == []
    assert any(
        "_pending_review_details" in r.message and "Cassandra query failed" in r.message
        for r in caplog.records
    )
