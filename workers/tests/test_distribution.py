"""Social auto-post on publish (owner decision 2026-07-12): Bluesky first,
Telegram second, in that priority order. Each channel is independently
"enabled" by having credentials configured — no separate flag. These test
the HTTP call shapes against mocked httpx clients (no real network) and the
dispatcher's per-channel failure isolation.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.modules.distribution.base import (
    ArticleShare,
    DistributionResult,
    SocialDistributor,
    compose_caption,
    hashtags_for,
)
from app.modules.distribution.bluesky import BlueskyDistributor
from app.modules.distribution.dispatcher import distribute
from app.modules.distribution.mastodon import MastodonDistributor
from app.modules.distribution.telegram import TelegramDistributor

_SHARE = ArticleShare(
    title="Nodely Expands Infrastructure with Voi Support and Enterprise Tiers",
    summary="Nodely launches Voi mainnet endpoints and premium tiers.",
    url="https://algorand.pxke.me/news/articles/abc123",
    image_url="https://algorand.pxke.me/og/article/abc123.png",
    tags=("infrastructure", "layer-1"),
)


def test_article_share_is_plain_data() -> None:
    # No article_id/DB coupling — a distributor is testable with just strings.
    assert _SHARE.title and _SHARE.url


# ── Hashtags / caption composition ──────────────────────────────────────


def test_hashtags_for_always_leads_with_algorand() -> None:
    assert hashtags_for(()) == ["#Algorand"]


def test_hashtags_for_dedupes_case_insensitively() -> None:
    # Article already tagged "algorand" shouldn't produce #Algorand twice.
    assert hashtags_for(("Algorand", "defi")) == ["#Algorand", "#DeFi"]


def test_hashtags_for_applies_known_casing_overrides() -> None:
    tags = hashtags_for(("nft", "dao", "api", "kyc"), limit=10)
    assert tags == ["#Algorand", "#NFT", "#DAO", "#API", "#KYC"]


def test_hashtags_for_camel_cases_multiword_slugs() -> None:
    assert hashtags_for(("layer-1",), limit=10) == ["#Algorand", "#Layer1"]


def test_hashtags_for_respects_limit() -> None:
    tags = hashtags_for(("a", "b", "c", "d", "e"), limit=3)
    assert len(tags) == 3
    assert tags[0] == "#Algorand"


def test_compose_caption_appends_hashtag_line() -> None:
    text = compose_caption(parts=["Title"], tags=("defi",), max_chars=100)
    assert text == "Title\n\n#Algorand #DeFi"


def test_compose_caption_drops_hashtags_before_truncating_content() -> None:
    # A tight budget should shed the (least important) hashtag line before
    # it ever chops into the real content with an ellipsis.
    title = "A" * 20
    text = compose_caption(parts=[title], tags=("defi",), max_chars=22)
    assert text == title
    assert "#" not in text


def test_compose_caption_truncates_with_ellipsis_when_content_alone_overflows() -> None:
    title = "A" * 50
    text = compose_caption(parts=[title], tags=(), max_chars=10)
    assert text == "A" * 9 + "…"
    assert len(text) == 10


# ── Bluesky ──────────────────────────────────────────────────────────────


def test_bluesky_disabled_without_credentials() -> None:
    assert not BlueskyDistributor(handle="", app_password="").enabled
    assert not BlueskyDistributor(handle="x.bsky.social", app_password="").enabled
    assert BlueskyDistributor(handle="x.bsky.social", app_password="app-pw").enabled


def test_bluesky_post_success() -> None:
    with patch("app.modules.distribution.bluesky.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value

        def post_side_effect(path, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = lambda: None
            if path == "/xrpc/com.atproto.server.createSession":
                resp.json.return_value = {"did": "did:plc:abc", "accessJwt": "jwt-token"}
            elif path == "/xrpc/com.atproto.repo.uploadBlob":
                resp.json.return_value = {"blob": {"$type": "blob", "ref": {"$link": "cid"}}}
            elif path == "/xrpc/com.atproto.repo.createRecord":
                resp.json.return_value = {"uri": "at://did:plc:abc/app.bsky.feed.post/xyz"}
            return resp

        client.post.side_effect = post_side_effect
        client.get.return_value.raise_for_status = lambda: None
        client.get.return_value.content = b"fake-image-bytes"
        client.get.return_value.headers = {"content-type": "image/png"}

        result = BlueskyDistributor(handle="x.bsky.social", app_password="pw").post_article(
            _SHARE
        )

    assert result.ok
    assert result.channel == "bluesky"
    # 3 calls: createSession, uploadBlob (via GET+POST -> 1 post call), createRecord
    post_paths = [c.args[0] for c in client.post.call_args_list]
    assert "/xrpc/com.atproto.server.createSession" in post_paths
    assert "/xrpc/com.atproto.repo.uploadBlob" in post_paths
    assert "/xrpc/com.atproto.repo.createRecord" in post_paths
    # The createRecord call must carry the external embed with our URL.
    create_record_call = next(
        c for c in client.post.call_args_list
        if c.args[0] == "/xrpc/com.atproto.repo.createRecord"
    )
    record = create_record_call.kwargs["json"]["record"]
    assert record["embed"]["external"]["uri"] == _SHARE.url
    assert record["embed"]["external"]["thumb"]["ref"]["$link"] == "cid"
    assert record["text"] == (
        "Nodely Expands Infrastructure with Voi Support and Enterprise Tiers"
        "\n\n#Algorand #Infrastructure #Layer1"
    )


def test_bluesky_post_survives_thumb_upload_failure() -> None:
    # A share-card image is a nice-to-have; the post should still go out
    # text/link-only if the image fetch or blob upload fails.
    with patch("app.modules.distribution.bluesky.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value

        def post_side_effect(path, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = lambda: None
            if path == "/xrpc/com.atproto.server.createSession":
                resp.json.return_value = {"did": "did:plc:abc", "accessJwt": "jwt-token"}
            elif path == "/xrpc/com.atproto.repo.createRecord":
                resp.json.return_value = {"uri": "at://did:plc:abc/app.bsky.feed.post/xyz"}
            return resp

        client.post.side_effect = post_side_effect
        client.get.side_effect = ConnectionError("image host down")

        result = BlueskyDistributor(handle="x.bsky.social", app_password="pw").post_article(
            _SHARE
        )

    assert result.ok
    create_record_call = next(
        c for c in client.post.call_args_list
        if c.args[0] == "/xrpc/com.atproto.repo.createRecord"
    )
    assert "thumb" not in create_record_call.kwargs["json"]["record"]["embed"]["external"]


def test_bluesky_post_failure_does_not_raise() -> None:
    with patch("app.modules.distribution.bluesky.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.side_effect = ConnectionError("auth service down")

        result = BlueskyDistributor(handle="x.bsky.social", app_password="pw").post_article(
            _SHARE
        )

    assert not result.ok
    assert result.channel == "bluesky"
    assert "auth service down" in result.detail


# ── Telegram ─────────────────────────────────────────────────────────────


def test_telegram_disabled_without_credentials() -> None:
    assert not TelegramDistributor(bot_token="", chat_id="").enabled
    assert not TelegramDistributor(bot_token="tok", chat_id="").enabled
    assert TelegramDistributor(bot_token="tok", chat_id="@chan").enabled


def test_telegram_sends_photo_when_image_present() -> None:
    with patch("app.modules.distribution.telegram.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        resp.json.return_value = {"ok": True}
        client.post.return_value = resp

        result = TelegramDistributor(bot_token="tok", chat_id="@chan").post_article(_SHARE)

    assert result.ok
    call = client.post.call_args
    assert call.args[0].endswith("/sendPhoto")
    assert call.kwargs["json"]["photo"] == _SHARE.image_url
    assert _SHARE.url in call.kwargs["json"]["caption"]
    assert "#Algorand #Infrastructure #Layer1" in call.kwargs["json"]["caption"]


def test_telegram_falls_back_to_send_message_without_image() -> None:
    share = ArticleShare(title="T", summary="S", url="https://x.test/a", image_url="")
    with patch("app.modules.distribution.telegram.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        resp.json.return_value = {"ok": True}
        client.post.return_value = resp

        result = TelegramDistributor(bot_token="tok", chat_id="@chan").post_article(share)

    assert result.ok
    call = client.post.call_args
    assert call.args[0].endswith("/sendMessage")


def test_telegram_reports_api_level_failure() -> None:
    # Telegram returns HTTP 200 with {"ok": false} on API-level errors (e.g.
    # bot not admin of the channel) — not an HTTP error, must still surface.
    with patch("app.modules.distribution.telegram.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        resp.json.return_value = {"ok": False, "description": "bot is not a member"}
        client.post.return_value = resp

        result = TelegramDistributor(bot_token="tok", chat_id="@chan").post_article(_SHARE)

    assert not result.ok
    assert "not a member" in result.detail


# ── Mastodon ─────────────────────────────────────────────────────────────


def test_mastodon_disabled_without_credentials() -> None:
    assert not MastodonDistributor(instance_url="", access_token="").enabled
    assert not MastodonDistributor(
        instance_url="https://mastodon.social", access_token=""
    ).enabled
    assert MastodonDistributor(
        instance_url="https://mastodon.social", access_token="tok"
    ).enabled


def test_mastodon_post_success_with_media() -> None:
    with patch("app.modules.distribution.mastodon.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.return_value.raise_for_status = lambda: None
        client.get.return_value.content = b"fake-image-bytes"
        client.get.return_value.headers = {"content-type": "image/png"}

        def post_side_effect(path, **kwargs):
            resp = MagicMock()
            resp.raise_for_status = lambda: None
            if path == "/api/v1/media":
                resp.json.return_value = {"id": "media-42"}
            return resp

        client.post.side_effect = post_side_effect

        result = MastodonDistributor(
            instance_url="https://mastodon.social", access_token="tok"
        ).post_article(_SHARE)

    assert result.ok
    status_call = next(
        c for c in client.post.call_args_list if c.args[0] == "/api/v1/statuses"
    )
    assert status_call.kwargs["data"]["media_ids"] == ["media-42"]
    assert _SHARE.url in status_call.kwargs["data"]["status"]
    assert "#Algorand #Infrastructure #Layer1" in status_call.kwargs["data"]["status"]


def test_mastodon_post_survives_media_upload_failure() -> None:
    with patch("app.modules.distribution.mastodon.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.side_effect = ConnectionError("image host down")
        resp = MagicMock()
        resp.raise_for_status = lambda: None
        client.post.return_value = resp

        result = MastodonDistributor(
            instance_url="https://mastodon.social", access_token="tok"
        ).post_article(_SHARE)

    assert result.ok
    status_call = client.post.call_args
    assert "media_ids" not in status_call.kwargs["data"]


def test_mastodon_post_failure_does_not_raise() -> None:
    with patch("app.modules.distribution.mastodon.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.return_value.raise_for_status = lambda: None
        client.get.return_value.content = b"x"
        client.get.return_value.headers = {"content-type": "image/png"}
        client.post.side_effect = ConnectionError("instance down")

        result = MastodonDistributor(
            instance_url="https://mastodon.social", access_token="tok"
        ).post_article(_SHARE)

    assert not result.ok
    assert result.channel == "mastodon"


# ── Dispatcher ───────────────────────────────────────────────────────────


class _FakeDistributor(SocialDistributor):
    def __init__(self, name: str, *, is_enabled: bool, ok: bool) -> None:
        self.name = name
        self._is_enabled = is_enabled
        self._ok = ok
        self.called = False

    @property
    def enabled(self) -> bool:
        return self._is_enabled

    def post_article(self, share: ArticleShare) -> DistributionResult:
        self.called = True
        return DistributionResult(channel=self.name, ok=self._ok)


def test_dispatcher_skips_disabled_channels_without_calling_them() -> None:
    a = _FakeDistributor("a", is_enabled=False, ok=True)
    b = _FakeDistributor("b", is_enabled=True, ok=True)
    with patch("app.modules.distribution.dispatcher._build_distributors", return_value=[a, b]):
        results = distribute(_SHARE)
    assert not a.called
    assert b.called
    assert [r.channel for r in results] == ["b"]


def test_dispatcher_isolates_one_channel_failing_from_another() -> None:
    class _RaisingDistributor(SocialDistributor):
        name = "broken"

        @property
        def enabled(self) -> bool:
            return True

        def post_article(self, share: ArticleShare) -> DistributionResult:
            raise RuntimeError("channel exploded")

    broken = _RaisingDistributor()
    healthy = _FakeDistributor("healthy", is_enabled=True, ok=True)
    with patch(
        "app.modules.distribution.dispatcher._build_distributors",
        return_value=[broken, healthy],
    ):
        results = distribute(_SHARE)
    assert healthy.called
    by_channel = {r.channel: r.ok for r in results}
    assert by_channel == {"broken": False, "healthy": True}
