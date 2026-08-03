"""prompt_version must round-trip through the write and read paths so stored articles can be correlated with the compose prompt that produced them."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from app.core.statements import ArticleStmts
from app.modules.newspaper.article_store import get_article, insert_stored_article


def _article_insert_params(fake_cassandra_session: MagicMock) -> tuple:
    """The bind params of the ArticleStmts.INSERT call specifically -- insert_stored_article also makes an (unrelated, best-effort) glossary lookup, so position alone isn't a reliable way to find the article insert among execute() calls."""
    for args, _kwargs in fake_cassandra_session.execute.call_args_list:
        if len(args) != 2:
            continue  # e.g. the glossary lookup's no-params LIST_ALL call
        stmt, params = args
        if stmt is ArticleStmts.INSERT:
            return params
    raise AssertionError("ArticleStmts.INSERT was never called")


def test_insert_stored_article_passes_prompt_version_last_positional(
    fake_cassandra_session: MagicMock,
) -> None:
    """prompt_version is passed as the last positional bind param on insert."""
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

    params = _article_insert_params(fake_cassandra_session)
    assert params[-1] == "2026-07-02"


def test_insert_stored_article_defaults_prompt_version_to_none(
    fake_cassandra_session: MagicMock,
) -> None:
    """prompt_version defaults to None when not supplied on insert."""
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

    params = _article_insert_params(fake_cassandra_session)
    assert params[-1] is None


def test_get_article_reads_prompt_version(fake_cassandra_session: MagicMock) -> None:
    """get_article reads prompt_version back off the stored row."""
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
