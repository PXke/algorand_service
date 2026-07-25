"""prompt_version must round-trip through the write and read paths so stored articles can be correlated with the compose prompt that produced them."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from app.modules.newspaper.article_store import get_article, insert_stored_article


def test_insert_stored_article_passes_prompt_version_last_positional(
    fake_cassandra_session: MagicMock,
) -> None:
    insert_stored_article(
        service_id="svc",
        title="T",
        summary="S",
        body="B",
        trigger_txid="tx",
        trigger_round=1,
        source_url="https://example.com",
        publish_to_feed=False,
        prompt_version="2026-07-02",
    )

    args, _ = fake_cassandra_session.execute.call_args_list[0]
    _stmt, params = args
    assert params[-1] == "2026-07-02"


def test_insert_stored_article_defaults_prompt_version_to_none(
    fake_cassandra_session: MagicMock,
) -> None:
    insert_stored_article(
        service_id="svc",
        title="T",
        summary="S",
        body="B",
        trigger_txid="tx",
        trigger_round=1,
        source_url="https://example.com",
        publish_to_feed=False,
    )

    args, _ = fake_cassandra_session.execute.call_args_list[0]
    _stmt, params = args
    assert params[-1] is None


def test_get_article_reads_prompt_version(fake_cassandra_session: MagicMock) -> None:
    aid = uuid4()
    row = MagicMock()
    row.article_id = aid
    row.service_id = "svc"
    row.title = "T"
    row.summary = "S"
    row.body = "B"
    row.published_at = None
    row.trigger_txid = "tx"
    row.trigger_round = 1
    row.source_url = "https://example.com"
    row.prompt_version = "2026-07-02"

    fake_cassandra_session.execute.return_value.one.return_value = row

    detail = get_article(str(aid))

    assert detail is not None
    assert detail.prompt_version == "2026-07-02"
