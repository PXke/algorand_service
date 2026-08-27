"""insert_stored_article must never create an "unlisted" row with status='published'.

`articles.status` IS public-feed membership since the 2026-08-24 table
consolidation, so publish_to_feed=False combined with the status parameter's
"published" default is a contradiction that puts a slug-less draft row
straight onto the live feed as a duplicate of the real article. Exactly this
bit live three times (HesabPay 2026-08-22, AlgoRank 2026-08-26, Al Goanna
2026-08-27): recompose_published stored its unlisted draft without an
explicit status. The guard coerces the contradiction to 'on_hold' (fail SAFE:
a wrongly-held article is recoverable, a stray live draft is an incident).
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.modules.newspaper.article_store import insert_stored_article

# `articles` table INSERT's column order (see ArticlesStmts.INSERT / the
# shared _ARTICLES_COLUMNS in algorand_shared.article_transitions): status is
# the first bind param.
_STATUS_INDEX = 0
# See test_article_store_prompt_version.py for why param-tuple length (23
# bound columns), not statement identity, picks the article INSERT out of the
# fake session's calls.
_INSERT_PARAM_COUNT = 24  # +1 for `views` (migration 084)


def _article_insert_params(fake_cassandra_session: MagicMock) -> tuple:
    """The bind params of the ArticlesStmts.INSERT call specifically."""
    for args, _kwargs in fake_cassandra_session.execute.call_args_list:
        if len(args) != 2:
            continue  # e.g. the glossary lookup's no-params LIST_ALL call
        _stmt, params = args
        if len(params) == _INSERT_PARAM_COUNT:
            return params
    raise AssertionError("ArticlesStmts.INSERT was never called")


def _insert(fake_cassandra_session: MagicMock, **overrides: object) -> tuple:
    kwargs: dict[str, object] = {
        "service_id": "svc",
        "title": "T",
        "summary": "S",
        "body": "B",
        "trigger_txid": "tx",
        "trigger_round": 1,
        "source_url": "https://example.com",
    }
    kwargs.update(overrides)
    insert_stored_article(**kwargs)  # type: ignore[arg-type]
    return _article_insert_params(fake_cassandra_session)


def test_unlisted_insert_with_default_status_is_coerced_to_on_hold(
    fake_cassandra_session: MagicMock,
) -> None:
    """publish_to_feed=False relying on the 'published' default must not create a live row -- this exact reliance was recompose_published's bug."""
    params = _insert(fake_cassandra_session, publish_to_feed=False)
    assert params[_STATUS_INDEX] == "on_hold"


def test_unlisted_insert_with_explicit_published_is_coerced_to_on_hold(
    fake_cassandra_session: MagicMock,
) -> None:
    """Even an EXPLICIT status='published' with publish_to_feed=False is the same contradiction, coerced the same way."""
    params = _insert(fake_cassandra_session, publish_to_feed=False, status="published")
    assert params[_STATUS_INDEX] == "on_hold"


def test_unlisted_insert_with_real_destination_status_is_untouched(
    fake_cassandra_session: MagicMock,
) -> None:
    """An unlisted draft that names its real destination (backlog/on_hold) passes through unchanged."""
    params = _insert(fake_cassandra_session, publish_to_feed=False, status="backlog")
    assert params[_STATUS_INDEX] == "backlog"


def test_feed_publish_keeps_status_published(fake_cassandra_session: MagicMock) -> None:
    """The real publish path (publish_to_feed=True) still inserts status='published'."""
    params = _insert(fake_cassandra_session, publish_to_feed=True)
    assert params[_STATUS_INDEX] == "published"
