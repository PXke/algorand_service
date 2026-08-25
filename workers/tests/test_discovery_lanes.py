"""Discovery-lane widening (2026-07-09): link-following + curated lists miss services nobody links or curates. Four new inputs: (1) on-chain asset-config url params (the chain declares domains — HAFN carries hesab.af), (2) DefiLlama / Pera-verified API registries, (3) GitHub-topic + Medium-tag + Bluesky-link mentions, (4) a retro-pass promoting pending domains on crawled-content score."""

import json
from types import SimpleNamespace

import pytest

import app.modules.crawler.ecosystem_sync as es
import app.modules.crawler.mention_discovery as md
from app.modules.chain_tail.chain_reader import RoundTransaction
from app.modules.chain_tail.discovery import extract_urls_from_tx
from app.modules.scraper.core.bluesky_scraper import _record_links


def _acfg_tx(au: str) -> RoundTransaction:
    return RoundTransaction(
        txid="T",
        round=1,
        sender="SND",
        txn_type="acfg",
        txn_json=json.dumps({"txn": {"type": "acfg", "apar": {"au": au, "un": "TOK"}}}),
    )


# --- lane 1: on-chain asset-config urls -------------------------------------


def test_asset_config_url_is_extracted() -> None:
    """Extracts a plain http(s) URL declared in an acfg asset's `au` field."""
    urls = extract_urls_from_tx(_acfg_tx("https://hesab.af"))
    assert "https://hesab.af" in urls


def test_asset_config_skips_content_pointers() -> None:
    """Skips ipfs/arweave/template content pointers and empty `au` values."""
    for au in (
        "ipfs://QmSomeCid",
        "template-ipfs://{ipfscid:1:raw:reserve:sha2-256}",
        "https://gateway.pinata.cloud/ipfs/QmX",
        "https://arweave.net/abc",
        "",
    ):
        assert extract_urls_from_tx(_acfg_tx(au)) == [], au


def test_non_acfg_txn_unaffected() -> None:
    """Extracts no URLs from a non-acfg transaction type."""
    tx = RoundTransaction(
        txid="T",
        round=1,
        sender="SND",
        txn_type="pay",
        txn_json=json.dumps({"txn": {"type": "pay", "note": ""}}),
    )
    assert extract_urls_from_tx(tx) == []


# --- lane 2: API registries ---------------------------------------------------


def test_defillama_filters_to_algorand_non_cex(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keeps only Algorand-chain, non-CEX protocols from the DefiLlama registry."""
    protocols = [
        {
            "name": "Binance CEX",
            "category": "CEX",
            "chains": ["Algorand"],
            "url": "https://www.binance.com",
            "slug": "binance",
        },
        {
            "name": "Folks Finance",
            "category": "Lending",
            "chains": ["Algorand", "Avalanche"],
            "url": "https://folks.finance",
            "slug": "folks-finance",
        },
        {
            "name": "Uniswap",
            "category": "Dexes",
            "chains": ["Ethereum"],
            "url": "https://uniswap.org",
            "slug": "uniswap",
        },
    ]
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda _url, **_kw: SimpleNamespace(json=lambda: protocols, raise_for_status=lambda: None),
    )
    domains = es._domains_from_defillama()
    assert domains == {"folks.finance": "defillama:folks-finance"}


def test_pera_verified_resolves_asset_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolves verified Pera assets to their linked URL, skipping unverified and non-URL assets."""
    monkeypatch.setattr(
        "app.core.net_guard.guarded_get",
        lambda _url, **_kw: SimpleNamespace(
            json=lambda: {
                "results": [
                    {"asset_id": 849191641, "verification_tier": "verified"},
                    {"asset_id": 2, "verification_tier": "unverified"},
                    {"asset_id": 3, "verification_tier": "verified"},
                ]
            },
            raise_for_status=lambda: None,
        ),
    )
    lookups = {
        849191641: {"url": "https://hesab.af"},
        3: {"url": "ipfs://QmX"},
    }
    monkeypatch.setattr(
        "app.modules.ai.chain_tools._tool_lookup_asset",
        lambda aid: lookups.get(aid, {}),
    )
    domains = es._domains_from_pera_verified(asset_cap=10)
    assert domains == {"hesab.af": "pera-verified:849191641"}


# --- lane 3: mentions ---------------------------------------------------------


def test_github_topic_homepages() -> None:
    """Extracts repo homepages, excluding null, GitHub Pages, and social-media URLs."""
    payload = {
        "items": [
            {"full_name": "org/wallet", "homepage": "https://methodwallet.app"},
            {"full_name": "org/none", "homepage": None},
            {"full_name": "org/pages", "homepage": "https://someuser.github.io/x"},
            {"full_name": "org/social", "homepage": "https://twitter.com/x"},
        ]
    }
    urls = md.homepages_from_github_topic(payload)
    assert urls == {"https://methodwallet.app": "github:org/wallet"}


def test_feed_html_external_links_one_per_domain() -> None:
    """Extracts one external link per distinct domain, excluding the self-host and duplicate domains."""
    xml = (
        '<a href="https://medium.com/@author/post">self</a>'
        '<a href="https://compx.io/launch">product</a>'
        '<a href="https://compx.io/docs">same domain again</a>'
        '<a href="https://github.com/org/repo">forge</a>'
    )
    urls = md.urls_from_feed_html(xml, self_host="medium.com")
    assert urls == {"https://compx.io/launch": "feed:medium.com"}


def test_bluesky_record_links_from_facets_and_embed() -> None:
    """Extracts the embed URL and facet link URL, ignoring mention facets."""
    record = {
        "embed": {"external": {"uri": "https://rug.ninja/launch"}},
        "facets": [
            {
                "features": [
                    {"$type": "app.bsky.richtext.facet#link", "uri": "https://compx.io/"},
                    {"$type": "app.bsky.richtext.facet#mention", "did": "did:plc:x"},
                ]
            },
        ],
    }
    assert _record_links(record) == ("https://rug.ninja/launch", "https://compx.io/")


# --- lane 4: pending retro-pass -------------------------------------------------


def test_reevaluate_promotes_only_scored_pending_above_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Promotes only the pending, relevant domain whose scored content_relevance clears the threshold."""
    import app.modules.crawler.tasks.url_queue_tasks as uq

    rows = [
        SimpleNamespace(
            domain="good.fi",
            frontier_status="pending",
            is_relevant=True,
            metadata={"content_relevance": "0.61"},
        ),
        SimpleNamespace(
            domain="lowscore.io",
            frontier_status="pending",
            is_relevant=True,
            metadata={"content_relevance": "0.12"},
        ),
        SimpleNamespace(
            domain="unscored.dev", frontier_status="pending", is_relevant=True, metadata={}
        ),
        SimpleNamespace(
            domain="already.app",
            frontier_status="approved",
            is_relevant=True,
            metadata={"content_relevance": "0.9"},
        ),
        SimpleNamespace(
            domain="rejected.com",
            frontier_status="pending",
            is_relevant=False,
            metadata={"content_relevance": "0.9"},
        ),
    ]
    monkeypatch.setattr(
        "app.core.cassandra.get_cassandra_session",
        lambda: SimpleNamespace(
            execute=lambda _stmt, _params: rows,
            prepare=lambda cql: cql,  # _Stmt descriptor prepares on first access
        ),
    )
    monkeypatch.setattr(
        uq,
        "classify_pending_domains",
        lambda **_kw: {"scored": 0, "errors": 0, "unreadable": 0},
    )
    promoted = []
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.update_domain_status",
        lambda d, **kw: promoted.append((d, kw)),
    )
    enqueued = []
    monkeypatch.setattr(
        "app.modules.crawler.url_queue.enqueue_url",
        lambda url, **_kw: enqueued.append(url) or ("", True),
    )
    service_calls = []
    monkeypatch.setattr(
        "app.modules.crawler.domain_tracker.ensure_monitored_service",
        lambda domain, **kw: service_calls.append((domain, kw)) or True,
    )
    out = uq.reevaluate_pending_domains(limit=10)
    assert out["promoted"] == 1
    assert out["promoted_domains"] == ["good.fi"]
    assert promoted[0][0] == "good.fi"
    assert promoted[0][1]["frontier_status_override"] == "approved"
    assert enqueued == ["https://good.fi"]
    # A retro-promote is a full automated approve, same as the discovery-time
    # auto-approve and deep_classify_domain — it must also register the
    # monitored source, or the domain gets crawled forever without ever
    # reaching the publish queue (root-caused 2026-08-25: this task predates
    # neither ensure_monitored_service nor the discovery-time fix that wired
    # it in, but was never itself wired to it).
    assert service_calls == [("good.fi", {"scrape_url": "https://good.fi"})]
