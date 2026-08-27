"""prompt_version must round-trip through the write and read paths so stored articles can be correlated with the compose prompt that produced them."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

from app.modules.newspaper.article_store import get_article, insert_stored_article

# `articles` table INSERT's column order (see ArticlesStmts.INSERT / the
# shared _ARTICLES_COLUMNS in algorand_shared.article_transitions): status,
# year, published_at, article_id, service_id, title, summary, body,
# image_url, tags, source_url, trigger_txid, trigger_round, slug,
# translations, first_published_at, updated_at, prompt_version, ...
_PROMPT_VERSION_INDEX = 17
# The ArticlesStmts.INSERT call binds all 23 columns -- comfortably more than
# any other statement insert_stored_article issues (GET_BY_ID's lookup binds
# 1, the stale-partition DELETE binds 4), so param-tuple length reliably
# picks it out. Identity comparison (`stmt is ArticlesStmts.INSERT`) is NOT
# reliable here: `fake_cassandra_session` doesn't patch prepare_cached, so
# every prepared statement resolves through a bare MagicMock session whose
# .prepare(...) return value is the same mock object for any cql string,
# making every statement `is`-equal to every other.
_INSERT_PARAM_COUNT = 24  # +1 for `views` (migration 084)


def _article_insert_params(fake_cassandra_session: MagicMock) -> tuple:
    """The bind params of the ArticlesStmts.INSERT call specifically -- insert_stored_article also makes an (unrelated, best-effort) glossary lookup and an ArticlesStmts.GET_BY_ID/DELETE pair to clear any stale partition, so param count (not position or identity) reliably finds the article insert among execute() calls."""
    for args, _kwargs in fake_cassandra_session.execute.call_args_list:
        if len(args) != 2:
            continue  # e.g. the glossary lookup's no-params LIST_ALL call
        _stmt, params = args
        if len(params) == _INSERT_PARAM_COUNT:
            return params
    raise AssertionError("ArticlesStmts.INSERT was never called")


def test_insert_stored_article_passes_prompt_version_last_positional(
    fake_cassandra_session: MagicMock,
) -> None:
    """prompt_version is passed as the expected positional bind param on insert."""
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
    assert params[_PROMPT_VERSION_INDEX] == "2026-07-02"


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
    assert params[_PROMPT_VERSION_INDEX] is None


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
