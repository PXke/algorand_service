from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from app.core import config
from app.modules.newspaper.scam_enrichment import (
    extract_algorand_addresses,
    extract_domains_and_urls,
)

_KEYWORD_RE = re.compile(r"\$([A-Za-z][A-Za-z0-9]{1,15})\b")


def build_match_keys(
    *,
    service_id: str,
    page_text: str,
    source_url: str = "",
    extra_keywords: tuple[str, ...] = (),
    match_kind: str = "",
    match_value: str = "",
    topic: str = "",
) -> list[tuple[str, str]]:
    """Normalized (key_type, key_value) pairs for article lookup."""
    keys: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(key_type: str, value: str) -> None:
        val = value.strip().lower()[:512]
        if not val:
            return
        pair = (key_type, val)
        if pair in seen:
            return
        seen.add(pair)
        keys.append(pair)

    add("service_id", service_id)
    kind = match_kind.strip().lower()
    if kind == "domain":
        registry_domain = match_value.strip().lower()
        if registry_domain:
            add("domain", registry_domain)
        elif source_url.strip().startswith(("http://", "https://")):
            from app.modules.crawler.domain_tracker import domain_from_url

            add("domain", domain_from_url(source_url))
    elif source_url.strip():
        add("source_url", _normalize_source_url(source_url))

    # Domains MENTIONED in the body text (as opposed to the source's own
    # domain above) are only a meaningful "this belongs to that story" signal
    # for scam/incident continuity — a scam alert about algoblow.com should
    # attach a LATER report about algoblow.com to the same article. For
    # ordinary content this is dangerously broad: nearly every article cites
    # algorand.co/forum.algorand.co/github.com in its own Sources section,
    # so extracting those as match keys turned the most-cited article into a
    # magnet for every unrelated future update mentioning any of them — a
    # real runaway loop (2026-07-17): six unrelated sources (the Algorand
    # blog, forum, Nodely, Haystack, a GitHub repo) all got routed to "edit"
    # the same live article, which then got re-edited on every ~2-minute
    # beat forever (see publish_queue_store.TERMINAL_OUTCOMES) — 165 edits /
    # 330 versions in under 4 hours before this was caught by hand.
    # Cashtags get the same topic gate as body domains, for the same reason:
    # $ALGO/$USDC appear in ordinary market coverage constantly, so an
    # ungated "keyword" key routes unrelated future updates into editing
    # whatever article happened to mention the ticker last ($SCAMTOKEN
    # continuity on an alert is the signal this key type exists for).
    if topic in ("scam_alert", "network_incident"):
        _urls, domains = extract_domains_and_urls(page_text)
        for domain in domains:
            add("domain", domain)
        for match in _KEYWORD_RE.finditer(page_text):
            add("keyword", match.group(1).lower())

    # Addresses stay ungated: 58-char checksummed strings are high-precision
    # "same story" signals in any topic, unlike domains/tickers.
    for addr in extract_algorand_addresses(page_text):
        add("algo_address", addr.upper())

    for kw in extra_keywords:
        add("keyword", kw.lower())

    return keys


def _normalize_source_url(url: str) -> str:
    raw = url.strip().split("?")[0].rstrip("/")
    return raw.lower()


def edit_window_closes_at(*, from_time: datetime | None = None) -> datetime:
    hours = getattr(config, "ARTICLE_EDIT_WINDOW_HOURS", 24)
    start = from_time or datetime.now(tz=UTC)
    return start + timedelta(hours=hours)


def service_has_article(service_id: str) -> bool:
    """Whether this service has EVER had a real published article. Match keys
    are registered only on the publish and edit paths (never for held/review
    drafts) and are deleted with the article, so a hit here means readers have
    genuinely been introduced to the service. Fails open (True) on store
    errors: the safe default is the normal update framing, not re-introducing
    a service we may already have covered."""
    sid = (service_id or "").strip().lower()
    if not sid:
        return True
    try:
        from app.core.cassandra import get_cassandra_session
        from app.core.statements import ArticleMatchStmts

        row = get_cassandra_session().execute(
            ArticleMatchStmts.FIND_BY_KEY, ("service_id", sid)
        ).one()
        return row is not None
    except Exception:
        return True


def find_article_for_followup(
    keys: list[tuple[str, str]],
    *,
    now: datetime | None = None,
) -> str | None:
    """
    If any key still has an open edit window, return article_id to update.
    """
    if not keys:
        return None
    moment = now or datetime.now(tz=UTC)
    try:
        from app.core.cassandra import execute_parallel_with_args
        from app.core.statements import ArticleMatchStmts
    except Exception:
        return None

    try:
        # Look every key up concurrently; results align with `keys` (input order),
        # so we keep the first-key-wins precedence the sequential loop had.
        results = execute_parallel_with_args(
            ArticleMatchStmts.FIND_BY_KEY, [(kt, kv) for kt, kv in keys]
        )
    except Exception:
        return None
    for ok, rows in results:
        if not ok:
            continue
        for row in rows:
            closes = row.edit_window_closes_at
            if closes and closes.replace(tzinfo=UTC) > moment:
                return str(row.article_id)
    return None


def register_article_match_keys(
    *,
    article_id: str,
    keys: list[tuple[str, str]],
    closes_at: datetime | None = None,
) -> int:
    """After publish, index keys so follow-up ingest can attach edits."""
    if not keys:
        return 0
    from app.core.cassandra import get_cassandra_session
    from app.core.statements import ArticleMatchStmts

    session = get_cassandra_session()
    aid = UUID(article_id)
    linked = datetime.now(tz=UTC)
    closes = closes_at or edit_window_closes_at(from_time=linked)
    count = 0
    for key_type, key_value in keys:
        session.execute(
            ArticleMatchStmts.INSERT_KEY,
            (key_type, key_value, aid, linked, closes),
        )
        session.execute(
            ArticleMatchStmts.INSERT_KEY_BY_ARTICLE,
            (aid, key_type, key_value, linked),
        )
        count += 1
    return count


def is_edit_window_open(article_id: str, *, now: datetime | None = None) -> bool:
    """Explicitly linked article is editable while within ARTICLE_EDIT_WINDOW_HOURS of publish."""
    from app.modules.newspaper.article_store import get_article

    try:
        article = get_article(article_id)
    except Exception:
        return False
    if article is None or not article.published_at_epoch:
        return False
    moment = now or datetime.now(tz=UTC)
    published = datetime.fromtimestamp(article.published_at_epoch, tz=UTC)
    return edit_window_closes_at(from_time=published) > moment


def resolve_publish_mode(
    *,
    service_id: str,
    page_text: str,
    source_url: str = "",
    topic: str = "",
    requested_mode: str = "",
    requested_article_id: str = "",
    match_kind: str = "",
    match_value: str = "",
) -> dict[str, Any]:
    """
    Decide new article vs edit follow-up.
    An explicitly requested edit (`requested_mode="edit"` + article id) wins when
    the edit window is still open; otherwise fall back to match-key lookup.
    Returns { publish_mode, linked_article_id?, match_keys, edit_window_open? }.
    """
    extra: tuple[str, ...] = ()
    if topic == "scam_alert":
        extra = ("scam", "rekey")
    keys = build_match_keys(
        service_id=service_id,
        page_text=page_text,
        source_url=source_url,
        extra_keywords=extra,
        match_kind=match_kind,
        match_value=match_value,
        topic=topic,
    )
    if (
        requested_mode == "edit"
        and requested_article_id
        and is_edit_window_open(requested_article_id)
    ):
        return {
            "publish_mode": "edit",
            "linked_article_id": requested_article_id,
            "match_keys": keys,
            "edit_window_open": True,
        }

    linked = find_article_for_followup(keys)
    if linked:
        return {
            "publish_mode": "edit",
            "linked_article_id": linked,
            "match_keys": keys,
            "edit_window_open": True,
        }
    return {
        "publish_mode": "create",
        "linked_article_id": None,
        "match_keys": keys,
        "edit_window_open": False,
    }
