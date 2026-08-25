"""Forum hot-topic + xGov proposal lanes (2026-07-09): community debates and governance phases as publish signals. xGov has no REST API — proposals are apps created by the registry escrow (registry 3147789458), enumerated in one algod account call; the forum lane reads Discourse /latest.json."""

import base64
from types import SimpleNamespace

import pytest

import app.modules.chain_tail.xgov_watch as xw
import app.modules.scraper.tasks.forum_poll_tasks as fp
from app.modules.chain_tail.xgov_watch import (
    decode_global_state,
    proposal_facts,
    registry_escrow_address,
)
from app.modules.scraper.tasks.forum_poll_tasks import topic_is_hot


def _b64(s: bytes) -> str:
    return base64.b64encode(s).decode()


def _state(status: int, title: str = "Test Proposal", *, age_days: float = 1.0) -> list[dict]:
    import time

    ts = int(time.time() - age_days * 86400)
    return [
        {"key": _b64(b"status"), "value": {"type": 2, "uint": status}},
        {"key": _b64(b"title"), "value": {"type": 1, "bytes": _b64(title.encode())}},
        {"key": _b64(b"requested_amount"), "value": {"type": 2, "uint": 20_000_000_000}},
        {"key": _b64(b"approvals"), "value": {"type": 2, "uint": 4067}},
        {"key": _b64(b"rejections"), "value": {"type": 2, "uint": 709}},
        {"key": _b64(b"proposer"), "value": {"type": 1, "bytes": _b64(b"\x01" * 32)}},
        {"key": _b64(b"funding_category"), "value": {"type": 2, "uint": 10}},
        {"key": _b64(b"submission_timestamp"), "value": {"type": 2, "uint": ts}},
        {"key": _b64(b"vote_opening_timestamp"), "value": {"type": 2, "uint": ts}},
        {"key": _b64(b"voting_duration"), "value": {"type": 2, "uint": 3600}},
    ]


def test_registry_escrow_address_derivation() -> None:
    """Derives the registry escrow address that holds the created proposal apps."""
    # Verified against algod: this account holds the created proposal apps.
    assert registry_escrow_address(3147789458) == (
        "GR7UPYPKVCT7EIYFAGIJYT3LLHZZK3NRMWBXNZ7SXAAC2OPNXQTJVXV52A"
    )


def test_proposal_facts_decode() -> None:
    """Decodes an xGov proposal's global state into phase, title, and summary text."""
    state = decode_global_state(_state(25))
    facts = proposal_facts(3599298458, state)
    assert facts["phase"] == "voting"
    assert facts["title"] == "Test Proposal"
    assert "20,000 ALGO" in facts["text"]
    assert "4067 approvals" in facts["text"]
    assert "xgov.algorand.co/proposals/3599298458" in facts["text"]


def test_poll_signals_new_phases_and_skips_drafts_and_seen(monkeypatch: pytest.MonkeyPatch) -> None:
    """Signals only new-phase, non-draft, non-stale xGov proposals; skips already-seen and ancient ones."""
    account = {
        "created-apps": [
            {"id": 101, "params": {"global-state": _state(25, "Voting one")}},
            {"id": 102, "params": {"global-state": _state(10, "Still a draft")}},
            {"id": 103, "params": {"global-state": _state(30, "Approved one")}},
            {"id": 104, "params": {"global-state": _state(45, "Reviewed internal")}},
            # First-run backfill guard: a proposal that finished months ago must
            # never signal as news.
            {"id": 105, "params": {"global-state": _state(30, "Ancient history", age_days=120)}},
        ]
    }
    monkeypatch.setattr("app.modules.ai.chain_tools._algod_get", lambda _path: account)
    # 103's approved phase already signaled.
    monkeypatch.setattr(
        "app.modules.newspaper.snapshot_store.get_latest_snapshot",
        lambda sid: ("h", "t", "b") if "103" in sid else None,
    )
    monkeypatch.setattr("app.modules.newspaper.snapshot_store.source_id_for_service", lambda s: s)
    signals = []
    monkeypatch.setattr(
        "app.modules.newspaper.ingest_signal.ingest_publish_signal",
        lambda **kw: signals.append(kw) or {"status": "enqueued"},
    )
    out = xw.poll_xgov_proposals()
    assert out["new_signals"] == 1
    assert out["stale_skipped"] == 1
    assert [s["service_id"] for s in signals] == ["xgov-proposal:101:voting"]
    assert signals[0]["source_kind"] == "xgov"
    assert signals[0]["is_first_override"] is False
    # Bug-class-2 fix: every proposal phase mints its own per-item
    # service_id, which can never literal-match a prior published article
    # even though the xGov program itself is a well-covered venue -- the
    # lane must pass the stable venue id through so the editorial-room
    # artifact pool reads this as routine coverage, not a new discovery.
    from app.core import config

    assert signals[0]["venue_service_id"] == config.XGOV_VENUE_SERVICE_ID


def test_topic_is_hot_thresholds_and_pinned() -> None:
    """Applies posts/likes thresholds and never counts a pinned topic as hot."""
    assert topic_is_hot({"posts_count": 21, "like_count": 0}, min_posts=8, min_likes=10)
    assert topic_is_hot({"posts_count": 1, "like_count": 12}, min_posts=8, min_likes=10)
    assert not topic_is_hot({"posts_count": 3, "like_count": 4}, min_posts=8, min_likes=10)
    # The pinned scam-warning banner outscores everything, forever — never news.
    assert not topic_is_hot(
        {"posts_count": 50, "like_count": 90, "pinned": True}, min_posts=8, min_likes=10
    )


def test_forum_poll_signals_hot_unseen_topics(monkeypatch: pytest.MonkeyPatch) -> None:
    """Signals only hot, not-already-covered forum topics with post excerpts and published_at."""
    latest = {
        "topic_list": {
            "topics": [
                {
                    "id": 15288,
                    "title": "Wormhole NTT Contracts",
                    "slug": "wormhole-ntt",
                    "posts_count": 21,
                    "like_count": 18,
                },
                {
                    "id": 15362,
                    "title": "Quiet topic",
                    "slug": "quiet",
                    "posts_count": 1,
                    "like_count": 0,
                },
                {
                    "id": 15309,
                    "title": "Already covered",
                    "slug": "covered",
                    "posts_count": 22,
                    "like_count": 17,
                },
            ]
        }
    }
    topic_json = {
        "post_stream": {
            "posts": [
                {
                    "username": "dev1",
                    "cooked": "<p>Proposal to deploy <b>NTT</b>.</p>",
                    "created_at": "2026-07-01T10:00:00Z",
                },
                {"username": "dev2", "cooked": "<p>Concerns about fees.</p>"},
            ]
        }
    }

    def fake_get(url: str, **_kw: object) -> SimpleNamespace:
        payload = latest if url.endswith("/latest.json") else topic_json
        return SimpleNamespace(json=lambda: payload, raise_for_status=lambda: None)

    monkeypatch.setattr("app.core.net_guard.guarded_get", fake_get)
    monkeypatch.setattr(
        "app.modules.newspaper.snapshot_store.get_latest_snapshot",
        lambda sid: ("h", "t", "b") if "15309" in sid else None,
    )
    monkeypatch.setattr("app.modules.newspaper.snapshot_store.source_id_for_service", lambda s: s)
    signals = []
    monkeypatch.setattr(
        "app.modules.newspaper.ingest_signal.ingest_publish_signal",
        lambda **kw: signals.append(kw) or {"status": "enqueued"},
    )
    out = fp.poll_forum_topics()
    assert out["new_signals"] == 1
    assert signals[0]["service_id"] == "forum-topic:15288"
    assert signals[0]["source_url"] == "https://forum.algorand.co/t/wormhole-ntt/15288"
    assert "@dev1: Proposal to deploy NTT" in signals[0]["page_text"]
    assert signals[0]["published_at"] == "2026-07-01T10:00:00Z"
