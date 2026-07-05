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
