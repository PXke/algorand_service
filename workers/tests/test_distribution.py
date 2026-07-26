"""Social auto-post on publish (owner decision 2026-07-12): Bluesky first, Telegram second, in that priority order. Each channel is independently "enabled" by having credentials configured — no separate flag. These test the HTTP call shapes against mocked httpx clients (no real network) and the dispatcher's per-channel failure isolation."""

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
    """ArticleShare is plain string data with no article_id/DB coupling."""
    assert _SHARE.title
    assert _SHARE.url


# ── Hashtags / caption composition ──────────────────────────────────────


def test_hashtags_for_always_leads_with_algorand() -> None:
    """hashtags_for always leads with #Algorand even when no tags are given."""
    assert hashtags_for(()) == ["#Algorand"]


def test_hashtags_for_dedupes_case_insensitively() -> None:
    # Article already tagged "algorand" shouldn't produce #Algorand twice.
    """hashtags_for dedupes a tag already spelled "algorand" against the leading #Algorand."""
    assert hashtags_for(("Algorand", "defi")) == ["#Algorand", "#DeFi"]


def test_hashtags_for_applies_known_casing_overrides() -> None:
    """hashtags_for applies the known acronym casing overrides (NFT, DAO, API, KYC)."""
    tags = hashtags_for(("nft", "dao", "api", "kyc"), limit=10)
    assert tags == ["#Algorand", "#NFT", "#DAO", "#API", "#KYC"]


def test_hashtags_for_camel_cases_multiword_slugs() -> None:
    """hashtags_for camel-cases a hyphenated multi-word slug into one hashtag."""
    assert hashtags_for(("layer-1",), limit=10) == ["#Algorand", "#Layer1"]


def test_hashtags_for_respects_limit() -> None:
    """hashtags_for stops once it hits the requested limit."""
    tags = hashtags_for(("a", "b", "c", "d", "e"), limit=3)
    assert len(tags) == 3
    assert tags[0] == "#Algorand"


def test_compose_caption_appends_hashtag_line() -> None:
    """compose_caption appends a hashtag line built from the given tags."""
    text = compose_caption(parts=["Title"], tags=("defi",), max_chars=100)
    assert text == "Title\n\n#Algorand #DeFi"


def test_compose_caption_drops_hashtags_before_truncating_content() -> None:
    # A tight budget should shed the (least important) hashtag line before
    # it ever chops into the real content with an ellipsis.
    """compose_caption drops hashtags one at a time before it ever truncates real content."""
    title = "A" * 20
    text = compose_caption(parts=[title], tags=("defi",), max_chars=22)
    assert text == title
    assert "#" not in text


def test_compose_caption_truncates_with_ellipsis_when_content_alone_overflows() -> None:
    """compose_caption truncates with a trailing ellipsis once content alone overflows the budget."""
    title = "A" * 50
    text = compose_caption(parts=[title], tags=(), max_chars=10)
    assert text == "A" * 9 + "…"
    assert len(text) == 10


# ── Bluesky ──────────────────────────────────────────────────────────────


def test_bluesky_disabled_without_credentials() -> None:
    """BlueskyDistributor is disabled unless both handle and app password are set."""
    assert not BlueskyDistributor(handle="", app_password="").enabled
    assert not BlueskyDistributor(handle="x.bsky.social", app_password="").enabled
    assert BlueskyDistributor(handle="x.bsky.social", app_password="app-pw").enabled


def test_bluesky_post_success() -> None:
    """Posts to Bluesky via session, blob upload, and record creation, embedding the article as an external link card."""
    with patch("app.modules.distribution.bluesky.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value

        def post_side_effect(path: str, **_kwargs: object) -> MagicMock:
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

        result = BlueskyDistributor(handle="x.bsky.social", app_password="pw").post_article(_SHARE)

    assert result.ok
    assert result.channel == "bluesky"
    # 3 calls: createSession, uploadBlob (via GET+POST -> 1 post call), createRecord
    post_paths = [c.args[0] for c in client.post.call_args_list]
    assert "/xrpc/com.atproto.server.createSession" in post_paths
    assert "/xrpc/com.atproto.repo.uploadBlob" in post_paths
    assert "/xrpc/com.atproto.repo.createRecord" in post_paths
    # The createRecord call must carry the external embed with our URL.
    create_record_call = next(
        c for c in client.post.call_args_list if c.args[0] == "/xrpc/com.atproto.repo.createRecord"
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
    """A failed thumbnail upload still lets the post go out, link-only."""
    with patch("app.modules.distribution.bluesky.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value

        def post_side_effect(path: str, **_kwargs: object) -> MagicMock:
            resp = MagicMock()
            resp.raise_for_status = lambda: None
            if path == "/xrpc/com.atproto.server.createSession":
                resp.json.return_value = {"did": "did:plc:abc", "accessJwt": "jwt-token"}
            elif path == "/xrpc/com.atproto.repo.createRecord":
                resp.json.return_value = {"uri": "at://did:plc:abc/app.bsky.feed.post/xyz"}
            return resp

        client.post.side_effect = post_side_effect
        client.get.side_effect = ConnectionError("image host down")

        result = BlueskyDistributor(handle="x.bsky.social", app_password="pw").post_article(_SHARE)

    assert result.ok
    create_record_call = next(
        c for c in client.post.call_args_list if c.args[0] == "/xrpc/com.atproto.repo.createRecord"
    )
    assert "thumb" not in create_record_call.kwargs["json"]["record"]["embed"]["external"]


def test_bluesky_post_failure_does_not_raise() -> None:
    """A post failure is caught and returned as a failed DistributionResult, never raised."""
    with patch("app.modules.distribution.bluesky.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.post.side_effect = ConnectionError("auth service down")

        result = BlueskyDistributor(handle="x.bsky.social", app_password="pw").post_article(_SHARE)

    assert not result.ok
    assert result.channel == "bluesky"
    assert "auth service down" in result.detail


# ── Telegram ─────────────────────────────────────────────────────────────


def test_telegram_disabled_without_credentials() -> None:
    """TelegramDistributor is disabled unless both bot token and chat id are set."""
    assert not TelegramDistributor(bot_token="", chat_id="").enabled
    assert not TelegramDistributor(bot_token="tok", chat_id="").enabled
    assert TelegramDistributor(bot_token="tok", chat_id="@chan").enabled


def test_telegram_sends_photo_when_image_present() -> None:
    """Sends the share image via sendPhoto with the caption and hashtags when an image is present."""
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
    """Falls back to sendMessage (plain text) when the article has no image."""
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
    """Surfaces a Telegram API-level failure (HTTP 200, ok: false) as a failed result."""
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
    """MastodonDistributor is disabled unless both instance URL and access token are set."""
    assert not MastodonDistributor(instance_url="", access_token="").enabled
    assert not MastodonDistributor(instance_url="https://mastodon.social", access_token="").enabled
    assert MastodonDistributor(instance_url="https://mastodon.social", access_token="tok").enabled


def test_mastodon_post_success_with_media() -> None:
    """Posts to Mastodon with the uploaded share image attached as media."""
    with patch("app.modules.distribution.mastodon.httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        client.get.return_value.raise_for_status = lambda: None
        client.get.return_value.content = b"fake-image-bytes"
        client.get.return_value.headers = {"content-type": "image/png"}

        def post_side_effect(path: str, **_kwargs: object) -> MagicMock:
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
    status_call = next(c for c in client.post.call_args_list if c.args[0] == "/api/v1/statuses")
    assert status_call.kwargs["data"]["media_ids"] == ["media-42"]
    assert _SHARE.url in status_call.kwargs["data"]["status"]
    assert "#Algorand #Infrastructure #Layer1" in status_call.kwargs["data"]["status"]


def test_mastodon_post_survives_media_upload_failure() -> None:
    """A failed media upload still lets the status post, without an attached image."""
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
    """A post failure is caught and returned as a failed DistributionResult, never raised."""
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

    def post_article(self, _share: ArticleShare) -> DistributionResult:
        self.called = True
        return DistributionResult(channel=self.name, ok=self._ok)


def test_dispatcher_skips_disabled_channels_without_calling_them() -> None:
    """The dispatcher skips disabled channels without ever calling post_article on them."""
    a = _FakeDistributor("a", is_enabled=False, ok=True)
    b = _FakeDistributor("b", is_enabled=True, ok=True)
    with patch("app.modules.distribution.dispatcher._build_distributors", return_value=[a, b]):
        results = distribute(_SHARE)
    assert not a.called
    assert b.called
    assert [r.channel for r in results] == ["b"]


def test_dispatcher_isolates_one_channel_failing_from_another() -> None:
    """One channel raising does not stop the dispatcher from posting to the others."""
    class _RaisingDistributor(SocialDistributor):
        name = "broken"

        @property
        def enabled(self) -> bool:
            return True

        def post_article(self, _share: ArticleShare) -> DistributionResult:
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
