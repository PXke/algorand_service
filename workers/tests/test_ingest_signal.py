"""Date-only content changes hash the same (must not trigger a re-publish)."""

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

import app.modules.newspaper.ingest_signal as ingest_signal_mod
from app.modules.newspaper.ingest_signal import (
    _insert_artifact_for_signal,
    _stable_content_hash,
    _strip_repeating_row_blocks,
    ingest_publish_signal,
)
from app.modules.newspaper.publish_policy import (
    PublishDecision,
    PublishIntent,
    PublishKind,
    PublishTier,
    PublishTopic,
)

_NFD_SALES_TABLE = """Recent sales
The latest primary and secondary NFD sales
View all
Name Price Seller Buyer Transaction
aigirlfriend.algo A 144.34
nfdomains.algo
gf.algo
Jul 24, 2026 12:13 AM
nunyafuckinbizness.algo A 72.42
nfdomains.algo
nunyafuckinbizness.algo
Jul 23, 2026 6:41 PM
agent402.algo A 71.1
nfdomains.algo
agent402.algo
Jul 23, 2026 3:45 AM
"""

_NFD_SALES_TABLE_DIFFERENT_ROWS = """Recent sales
The latest primary and secondary NFD sales
View all
Name Price Seller Buyer Transaction
twin.algo A 1215.6
nfdomains.algo
52NNOZ…PC6A
Aug 2, 2026 4:40 AM
plsg.algo A 151.57
nfdomains.algo
PXQ4SI…7N6Y
Jul 31, 2026 6:30 AM
espn2.algo A 75.78
nfdomains.algo
PXQ4SI…7N6Y
Jul 31, 2026 6:29 AM
"""


def test_date_only_change_hashes_the_same() -> None:
    """Two pages differing only in a long-form date produce the same stable content hash."""
    before = "ZK ColorSort daily puzzle. Last updated: June 18, 2026."
    after = "ZK ColorSort daily puzzle. Last updated: July 6, 2026."
    assert _stable_content_hash(before) == _stable_content_hash(after)


def test_iso_date_only_change_hashes_the_same() -> None:
    """Two pages differing only in an ISO-format date produce the same stable content hash."""
    before = "Snapshot generated 2026-06-18."
    after = "Snapshot generated 2026-07-06."
    assert _stable_content_hash(before) == _stable_content_hash(after)


def test_real_text_change_still_hashes_differently() -> None:
    """A genuine content change still produces a different hash even when the date stays the same."""
    before = "ZK ColorSort daily puzzle. Last updated: June 18, 2026."
    after = "ZK ColorSort now supports multiplayer mode. Last updated: June 18, 2026."
    assert _stable_content_hash(before) != _stable_content_hash(after)


def test_live_activity_table_churn_hashes_the_same() -> None:
    """NFDomains regression pin (2026-08-02): a marketplace 'recent sales' table flattened to a 4-line-per-row cycle (name+price / seller / buyer / date) churns its NAMES every poll -- not caught by the numeric/date stripping alone, since the names themselves aren't numbers. Two otherwise-identical pages differing only in which names sold must hash the same."""
    assert _stable_content_hash(_NFD_SALES_TABLE) == _stable_content_hash(
        _NFD_SALES_TABLE_DIFFERENT_ROWS
    )


def test_real_change_alongside_activity_table_still_hashes_differently() -> None:
    """A genuine content change elsewhere on the page still registers even when it sits next to a churning activity table -- the table is neutralized, not the whole page."""
    before = "# NFDomains\n\nMint your .algo name today.\n\n" + _NFD_SALES_TABLE
    after = "# NFDomains\n\nNow with vault support for every name.\n\n" + _NFD_SALES_TABLE_DIFFERENT_ROWS
    assert _stable_content_hash(before) != _stable_content_hash(after)


def test_strip_repeating_row_blocks_leaves_prose_and_short_tables_untouched() -> None:
    """A hand-written passage and a small markdown table with genuinely different row content (not just churning identifiers) are never touched -- this must not blank real article content."""
    prose = (
        "## A Decentralized Registry\n\n"
        "NFDomains is Algorand's name service: a permissionless registry that turns\n"
        "opaque addresses into memorable .algo names.\n\n"
        "| Concept | Real-World Implication |\n"
        "| --- | --- |\n"
        "| Forward resolution | Converts a name into an address |\n"
        "| Reverse resolution | Converts an address into a name |\n"
        "| Vaults | Auto opt-in to assets |\n"
    )
    assert _strip_repeating_row_blocks(prose) == prose


def test_strip_repeating_row_blocks_requires_minimum_cycles() -> None:
    """A single occurrence of a multi-line row shape is not enough evidence of a repeating live feed -- left alone.

    Two+ full repeats of this exact 4-line shape total 8+ short-shaped
    lines, which the independent short-run pass has enough evidence to
    catch on its own regardless of exact period -- see
    test_strip_repeating_row_blocks_handles_variable_period_screener for
    that pass's own coverage.
    """
    one_row = "aigirlfriend.algo A 144.34\nnfdomains.algo\ngf.algo\nJul 24, 2026 12:13 AM\n"
    assert _strip_repeating_row_blocks(one_row) == one_row


def test_strip_repeating_row_blocks_handles_variable_period_screener() -> None:
    """hay.app regression pin (2026-08-02): a dense TICKER/PERCENT screener where SOME rows also carry a rank number has no constant period, so the fixed-cycle pass alone misses the tail (found stopping short after the first 3 clean 2-line rows). The independent short-run pass catches the whole thing regardless of exact period."""
    screener = (
        "ORA\n+6%\nALPHA\n+5%\nGONNA\n+2%\nTINY\n-1%\n10\nHOG\n-1%\n11\nFOLKS\n-1%\n12\nHAY\n+3%\n"
    )
    other_values = screener.replace("+6%", "+9%").replace("ORA", "ZETA").replace("10\n", "20\n")
    assert _stable_content_hash(screener) == _stable_content_hash(other_values)


# --------------------------------------------------------------------------- #
# _insert_artifact_for_signal -- crawler-channel venue_service_id resolution
# --------------------------------------------------------------------------- #


def _patch_insert_artifact(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []
    monkeypatch.setattr(
        "algorand_shared.artifact_store.insert_artifact",
        lambda **kw: calls.append(kw) or ("new-artifact-id", True),
    )
    monkeypatch.setattr(
        "app.modules.gatekeeper.fact_align.event_anchor_date", lambda **_kw: None
    )
    return calls


def _insert_signal(**overrides: object) -> dict:
    kwargs = {
        "service_id": "forum-algorand-co",
        "source_url": "https://forum.algorand.co/latest",
        "page_title": "t",
        "page_text": "body",
        "source_kind": None,
        "display_name": "Algorand Forum",
        "match_kind": "domain",
        "match_value": "forum.algorand.co",
        "published_at": "",
        "queue_payload": {},
    }
    kwargs.update(overrides)
    _insert_artifact_for_signal(**kwargs)


def test_crawler_channel_resolves_venue_from_an_established_domain_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression pin: a plain crawler-channel artifact minted from a fresh crawl of forum.algorand.co (service_id 'forum-algorand-co') must get venue_service_id populated with the domain's REAL, differently-named, established owner ('algorand-forum') at insert time -- no source_kind maps to the default 'crawler' channel."""
    calls = _patch_insert_artifact(monkeypatch)
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.venue_owner_for_url",
        lambda url, *, own_service_id: "algorand-forum"
        if (url, own_service_id) == ("https://forum.algorand.co/latest", "forum-algorand-co")
        else "",
    )

    _insert_signal()

    assert len(calls) == 1
    assert calls[0]["service_id"] == "forum-algorand-co"
    assert calls[0]["venue_service_id"] == "algorand-forum"
    assert calls[0]["channel"] == "crawler"


def test_crawler_channel_stays_none_for_a_genuinely_new_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No regression: a domain with no established reverse-index owner (a real new-service discovery) still gets venue_service_id=None, exactly like before this fix."""
    calls = _patch_insert_artifact(monkeypatch)
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.venue_owner_for_url", lambda *_a, **_kw: ""
    )

    _insert_signal(service_id="brand-new-project-example", source_url="https://brand-new-project.example/")

    assert len(calls) == 1
    assert calls[0]["venue_service_id"] is None


def test_explicit_venue_service_id_is_never_overridden_by_the_domain_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lane that already passed its own venue_service_id (forum/xgov/youtube/bluesky) must win outright -- the generic domain-owner resolution only ever fills in an UNSET value, never second-guesses an explicit one."""
    calls = _patch_insert_artifact(monkeypatch)
    resolver_called = []
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.venue_owner_for_url",
        lambda *a, **kw: resolver_called.append((a, kw)) or "should-never-be-used",
    )

    _insert_signal(
        service_id="forum-topic:15288",
        source_kind="forum",
        venue_service_id="algorand-forum",
    )

    assert len(calls) == 1
    assert calls[0]["venue_service_id"] == "algorand-forum"
    assert resolver_called == []


def test_non_crawler_channel_never_triggers_the_domain_check(monkeypatch: pytest.MonkeyPatch) -> None:
    """The domain-owner resolution is scoped to the generic 'crawler' channel only -- a channel like 'mail' with no venue_service_id set must not even call the resolver."""
    calls = _patch_insert_artifact(monkeypatch)
    resolver_called = []
    monkeypatch.setattr(
        "app.modules.newspaper.service_sources.venue_owner_for_url",
        lambda *a, **kw: resolver_called.append((a, kw)) or "unexpected",
    )

    _insert_signal(service_id="some-mail-service", source_kind="mail", source_url="")

    assert len(calls) == 1
    assert calls[0]["channel"] == "mail"
    assert calls[0]["venue_service_id"] is None
    assert resolver_called == []


# --------------------------------------------------------------------------- #
# ingest_publish_signal -- artifact-before-snapshot ordering (W2-C)
# --------------------------------------------------------------------------- #
#
# Store-before-mark (CLAUDE.md sec. 2.2): the durable artifact write must
# land before insert_snapshot, the write that makes the NEXT poll's
# `previous[0] == content_hash` check at the top of ingest_publish_signal
# short-circuit to "unchanged". If the snapshot were written first (or
# unconditionally) and the artifact insert then raised, a retry of the exact
# same page would hash identically, see the already-written snapshot, and
# skip forever without ever producing the artifact that was actually paid
# for -- silently discarding a finished ingest.


def _fake_snapshot_store() -> tuple[dict[str, tuple[str, str, str]], Callable, Callable, Callable]:
    """An in-memory stand-in for snapshot_store, keyed like the real module."""
    rows: dict[str, tuple[str, str, str]] = {}

    def _source_id_for_service(service_id: str) -> str:
        return f"svc:{service_id}"

    def _get_latest_snapshot(source_id: str) -> tuple[str, str, str] | None:
        return rows.get(source_id)

    def _insert_snapshot(
        *, source_id: str, content_hash: str, title: str, body: str, **_kw: object
    ) -> None:
        rows[source_id] = (content_hash, title, body)

    return rows, _source_id_for_service, _get_latest_snapshot, _insert_snapshot


def _patch_ingest_publish_signal_seams(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, tuple[str, str, str]]:
    """Fake out every collaborator of ingest_publish_signal except the artifact/snapshot ordering under test."""
    rows, source_id_fn, get_latest_fn, insert_snapshot_fn = _fake_snapshot_store()
    monkeypatch.setattr(ingest_signal_mod, "source_id_for_service", source_id_fn)
    monkeypatch.setattr(ingest_signal_mod, "get_latest_snapshot", get_latest_fn)
    monkeypatch.setattr(ingest_signal_mod, "insert_snapshot", insert_snapshot_fn)

    # A non-None, non-stale updated_at so _resolve_stale_scale_signal returns
    # the stored score directly, without also needing to fake
    # resolve_service_scale (a network-touching resolver).
    monkeypatch.setattr(
        ingest_signal_mod, "get_stored_scale_signal", lambda _sid: (0.5, datetime.now(tz=UTC))
    )
    monkeypatch.setattr(ingest_signal_mod, "get_stored_service_weight", lambda _sid: 0)
    monkeypatch.setattr(ingest_signal_mod, "upsert_service_profile", lambda **_kw: None)
    monkeypatch.setattr(ingest_signal_mod, "upsert_service_scale", lambda **_kw: None)

    intent = PublishIntent(
        kind=PublishKind.SERVICE_DISCOVERY,
        topic=PublishTopic.GENERIC,
        tier=PublishTier.STANDARD,
        priority=1,
        priority_breakdown="test",
        event_id="",
        event_phase="",
    )
    monkeypatch.setattr(ingest_signal_mod, "build_publish_intent", lambda **_kw: intent)
    monkeypatch.setattr(
        ingest_signal_mod,
        "evaluate_enqueue",
        lambda *_a, **_kw: PublishDecision(kind=intent.kind, allowed=True, reason="ok"),
    )
    monkeypatch.setattr(
        ingest_signal_mod,
        "resolve_publish_mode",
        lambda **_kw: {"publish_mode": "new", "linked_article_id": ""},
    )

    from app.modules.ai.content_signals import ContentSignals

    fake_signals = ContentSignals(
        category="test",
        categories=("test",),
        relevance=1.0,
        publish_decision=True,
        confidence=1.0,
        storage_score=1.0,
    )
    monkeypatch.setattr(
        "app.modules.ai.content_signals.compute_content_signals",
        lambda *_a, **_kw: fake_signals,
    )
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.url_recently_rejected", lambda _url: False
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_store.record_service_event", lambda **_kw: None
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.recent_title_similarity",
        lambda *_a, **_kw: (0.0, None),
    )
    monkeypatch.setattr(
        "app.modules.newspaper.article_grader.recent_content_similarity",
        lambda *_a, **_kw: (0.0, None),
    )
    return rows


def test_artifact_failure_leaves_no_snapshot_and_retry_composes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """W2-C regression pin: a raising artifact write must not leave a snapshot behind, or a retry of the identical page would read back that snapshot's matching content_hash and skip as "unchanged" forever -- silently discarding a finished, paid-for ingest instead of composing it on retry."""
    rows = _patch_ingest_publish_signal_seams(monkeypatch)

    calls = {"n": 0}

    def _flaky_insert_artifact(**_kw: object) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("artifact store unavailable")

    monkeypatch.setattr(ingest_signal_mod, "_insert_artifact_for_signal", _flaky_insert_artifact)

    kwargs = {
        "service_id": "example-service",
        "display_name": "Example Service",
        "source_url": "https://example.test/",
        "page_title": "Example ships a new feature",
        "page_text": "Example now supports vaults.",
        "source_kind": None,
        "txid": "txid-1",
    }

    with pytest.raises(RuntimeError, match="artifact store unavailable"):
        ingest_publish_signal(**kwargs)

    # The failed attempt must leave no snapshot row -- otherwise the retry
    # below would hash identically, read back a matching content_hash, and
    # short-circuit to "unchanged" at the top of ingest_publish_signal
    # instead of ever reaching the artifact write again.
    assert rows == {}
    assert calls["n"] == 1

    result = ingest_publish_signal(**{**kwargs, "txid": "txid-2"})

    assert result == {"status": "enqueued", "txid": "txid-2"}
    assert calls["n"] == 2
    assert rows  # snapshot now recorded, only after the artifact write succeeded
