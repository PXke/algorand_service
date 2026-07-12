"""merge_services (workers twin of the backend admin store's version): fold a
source service's sources into a target service and disable the source."""

from unittest.mock import MagicMock

from app.modules.newspaper.service_sources import merge_services


def test_merge_moves_sources_and_repoints_domain(fake_cassandra_session):
    source_row = MagicMock(
        source_id="web:algonode.io", source_type="web", url="https://algonode.io/",
        domain="algonode.io", enabled=True,
    )
    fake_cassandra_session.execute.return_value = [source_row]

    result = merge_services(target_service_id="nodely-io", source_service_ids=["algonode-io"])

    assert result == {"target": "nodely-io", "moved_sources": ["web:algonode.io"]}
    executed_stmts = [c.args[0] for c in fake_cassandra_session.execute.call_args_list]
    # UPSERT (move source) + UPSERT_BY_DOMAIN (repoint) + DELETE_FOR_SERVICE + SET_ENABLED
    assert len(executed_stmts) >= 4


def test_merge_skips_target_service_id_in_source_list(fake_cassandra_session):
    result = merge_services(target_service_id="nodely-io", source_service_ids=["nodely-io"])
    assert result == {"target": "nodely-io", "moved_sources": []}
    fake_cassandra_session.execute.assert_not_called()


class _FakeSnapshotStmts:
    """Plain sentinels standing in for the real `_Stmt` descriptors, so these
    tests don't depend on `prepare_cached`'s process-wide lru_cache resolving
    consistently against a mocked session."""

    GET_LATEST = object()
    INSERT = object()


def _patch_snapshot_stmts(monkeypatch):
    monkeypatch.setattr("app.core.statements.SnapshotStmts", _FakeSnapshotStmts)


def test_merge_carries_over_snapshot_when_target_has_none(fake_cassandra_session, monkeypatch):
    """A merged-away service's poll history must survive the fold — otherwise
    the canonical id's snapshot lineage starts empty and its next poll looks
    like a brand-new discovery (the nf.domains incident)."""
    _patch_snapshot_stmts(monkeypatch)
    src_snapshot = MagicMock(content_hash="abc123", title="NFD Docs", body="body text")

    def execute(stmt, args=None):
        if stmt is _FakeSnapshotStmts.GET_LATEST:
            result = MagicMock()
            result.one.return_value = src_snapshot if args[0] == "svc:docs-nf-domains" else None
            return result
        no_row = MagicMock()
        no_row.one.return_value = None
        return no_row

    fake_cassandra_session.execute.side_effect = execute

    merge_services(target_service_id="nf-domains", source_service_ids=["docs-nf-domains"])

    insert_calls = [
        c for c in fake_cassandra_session.execute.call_args_list
        if c.args[0] is _FakeSnapshotStmts.INSERT
    ]
    assert len(insert_calls) == 1
    inserted_args = insert_calls[0].args[1]
    assert inserted_args[0] == "svc:nf-domains"
    assert inserted_args[2:] == ("abc123", "NFD Docs", "body text")


def test_merge_does_not_clobber_target_snapshot_that_already_exists(
    fake_cassandra_session, monkeypatch
):
    _patch_snapshot_stmts(monkeypatch)
    src_snapshot = MagicMock(content_hash="old-hash", title="old", body="old body")
    tgt_snapshot = MagicMock(content_hash="fresher-hash", title="new", body="new body")

    def execute(stmt, args=None):
        if stmt is _FakeSnapshotStmts.GET_LATEST:
            result = MagicMock()
            result.one.return_value = (
                src_snapshot if args[0] == "svc:docs-nf-domains" else tgt_snapshot
            )
            return result
        no_row = MagicMock()
        no_row.one.return_value = None
        return no_row

    fake_cassandra_session.execute.side_effect = execute

    merge_services(target_service_id="nf-domains", source_service_ids=["docs-nf-domains"])

    insert_calls = [
        c for c in fake_cassandra_session.execute.call_args_list
        if c.args[0] is _FakeSnapshotStmts.INSERT
    ]
    assert insert_calls == []
