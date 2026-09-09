"""Every article write path in article_store.py must sanitize the body before it lands in Cassandra (W1-B).

insert_stored_article backs insert_article (a brand-new article); replace_article_content
backs apply_recomposed_article (an approved recompose applied onto a live/drafted
article_id). The writer's body is LLM-composed markdown and could carry a raw
<script>/onerror= payload that the frontend's `marked` renderer would otherwise
pass straight through to `{@html}` -- see backend/app/core/sanitize.py for the
mirrored allowlist used server-side on the admin-edit path.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

from app.modules.newspaper.article_store import insert_stored_article, replace_article_content

# `articles` table INSERT's column order (see ArticlesStmts.INSERT / the
# shared _ARTICLES_COLUMNS in algorand_shared.article_transitions): status,
# year, published_at, article_id, service_id, title, summary, body, ...
_INSERT_BODY_INDEX = 7
_INSERT_PARAM_COUNT = 25  # see test_article_store_prompt_version.py


def _article_insert_params(fake_cassandra_session: MagicMock) -> tuple:
    """The bind params of the ArticlesStmts.INSERT call specifically (same lookup as test_article_store_prompt_version.py)."""
    for args, _kwargs in fake_cassandra_session.execute.call_args_list:
        if len(args) != 2:
            continue
        _stmt, params = args
        if len(params) == _INSERT_PARAM_COUNT:
            return params
    raise AssertionError("ArticlesStmts.INSERT was never called")


def test_insert_stored_article_strips_script_tag_from_body(
    fake_cassandra_session: MagicMock,
) -> None:
    """insert_article's underlying write never stores a live <script> tag."""
    insert_stored_article(
        service_id="svc",
        title="T",
        summary="S",
        body="Hello<script>alert(1)</script> world",
        trigger_txid="tx",
        trigger_round=1,
        source_url="https://example.com",
        publish_to_feed=False,
    )

    body = _article_insert_params(fake_cassandra_session)[_INSERT_BODY_INDEX]
    assert "<script" not in body
    assert "alert(1)" not in body
    assert "Hello" in body
    assert "world" in body


def test_insert_stored_article_strips_onerror_attribute_from_body(
    fake_cassandra_session: MagicMock,
) -> None:
    """A disallowed event-handler attribute is still stripped.

    The nh3.is_html() fast-path added to skip corrupting plain prose (see
    _sanitize_body) must not skip sanitizing real embedded HTML: is_html()
    detects tag syntax with the same tokenizer nh3.clean() itself uses, so
    this body (containing a real `<img ...>` tag) is not eligible for the
    skip and is still fully cleaned.
    """
    insert_stored_article(
        service_id="svc",
        title="T",
        summary="S",
        body='Hello <img src=x onerror="alert(1)"> world',
        trigger_txid="tx",
        trigger_round=1,
        source_url="https://example.com",
        publish_to_feed=False,
    )

    body = _article_insert_params(fake_cassandra_session)[_INSERT_BODY_INDEX]
    assert "onerror" not in body
    assert "alert(1)" not in body
    assert "Hello" in body
    assert "world" in body


def test_insert_stored_article_preserves_bare_markdown_prose_characters(
    fake_cassandra_session: MagicMock,
) -> None:
    """Regression: a body with no real HTML tags must survive verbatim.

    Plain Markdown prose containing bare `>`/`&`/`<` (e.g. a quoted on-chain
    transaction note or a URL query string) must not get entity-encoded into
    `&gt;`/`&amp;`/`&lt;` by nh3.clean() treating the Markdown source as HTML
    to be re-serialized. Confirmed root cause: nh3.clean() on
    "r=1748>1643,1666>1727" (no `<tag>` anywhere) returns
    "r=1748&gt;1643,1666&gt;1727".
    """
    body = (
        "The transaction note read `r=1748>1643,1666>1727` and the source "
        "link was https://example.com/x?a=1&b=2. Compare: 3 < 5 and 5 > 3."
    )
    insert_stored_article(
        service_id="svc",
        title="T",
        summary="S",
        body=body,
        trigger_txid="tx",
        trigger_round=1,
        source_url="https://example.com",
        publish_to_feed=False,
    )

    stored = _article_insert_params(fake_cassandra_session)[_INSERT_BODY_INDEX]
    assert stored == body
    assert "&gt;" not in stored
    assert "&lt;" not in stored
    assert "&amp;" not in stored


def _draft_row(aid: object) -> MagicMock:
    row = MagicMock()
    row.article_id = aid
    row.service_id = "svc"
    row.title = "Old title"
    row.summary = "Old summary"
    row.body = "Old body"
    row.published_at = datetime.now(tz=UTC)
    row.first_published_at = None
    row.status = "draft"
    row.tags = []
    row.slug = None
    row.trigger_txid = "tx"
    row.trigger_round = 1
    row.source_url = "https://example.com"
    row.prompt_version = ""
    row.translations = None
    return row


def test_replace_article_content_strips_script_tag_from_body(
    fake_cassandra_session: MagicMock,
) -> None:
    """apply_recomposed_article's underlying write (replace_article_content) never stores a live <script> tag, on the drafted-article branch."""
    aid = uuid4()
    row = _draft_row(aid)
    fake_cassandra_session.execute.return_value.one.return_value = row

    result = replace_article_content(
        article_id=str(aid),
        title="New title",
        summary="New summary",
        body="Hello<script>alert(1)</script> world",
        tags=["updated"],
        image_url="",
    )

    assert result == row.published_at

    # `_dual_write_draft_content`'s UPDATE_CONTENT call: (title, summary,
    # body, tags, image, now, status, year, published_at, aid) -- 10 params,
    # a length no other execute() call in this path shares (lookups bind 1,
    # CLEAR_TRANSLATIONS binds 4).
    update_params = None
    for args, _kwargs in fake_cassandra_session.execute.call_args_list:
        if len(args) != 2:
            continue
        _stmt, params = args
        if len(params) == 10:
            update_params = params
            break
    assert update_params is not None
    body = update_params[2]
    assert "<script" not in body
    assert "alert(1)" not in body
    assert "Hello" in body
    assert "world" in body
