"""Mastodon auto-poster. Free, open (ActivityPub) — create a bot account on any instance, generate an access token under Settings > Development > New Application (needs `write:statuses` and `write:media` scopes), no approval process.

Uploads the share-card image via /api/v1/media first (returns a media id),
then posts the status with that media attached — Mastodon doesn't support
external link-card embeds the way Bluesky/Telegram do, so the image is
attached directly and the link lives in the status text.
"""

from __future__ import annotations

import logging

import httpx

from app.modules.distribution.base import (
    ArticleShare,
    DistributionResult,
    SocialDistributor,
    compose_caption,
)

log = logging.getLogger(__name__)

_TIMEOUT = 15.0
# Mastodon's default status limit is 500 chars; leave room for the link and
# a trailing ellipsis.
_MAX_STATUS_CHARS = 480


class MastodonDistributor(SocialDistributor):
    """SocialDistributor implementation for Mastodon."""

    name = "mastodon"

    def __init__(self, *, instance_url: str, access_token: str) -> None:
        """Store the Mastodon instance URL and access token used to authenticate posts."""
        self._instance_url = instance_url.rstrip("/")
        self._access_token = access_token

    @property
    def enabled(self) -> bool:
        """Whether the Mastodon instance URL and access token are configured."""
        return bool(self._instance_url and self._access_token)

    def post_article(self, share: ArticleShare) -> DistributionResult:
        """Post an article to Mastodon as a status with the share image attached."""
        headers = {"Authorization": f"Bearer {self._access_token}"}
        try:
            with httpx.Client(base_url=self._instance_url, timeout=_TIMEOUT) as client:
                media_id = self._upload_media(client, headers, share.image_url)
                status = self._compose_status(share)
                payload: dict = {"status": status}
                if media_id is not None:
                    payload["media_ids"] = [media_id]
                resp = client.post("/api/v1/statuses", data=payload, headers=headers)
                resp.raise_for_status()
            return DistributionResult(channel=self.name, ok=True)
        except Exception as exc:
            log.warning("mastodon post failed for %s: %s", share.url, exc, exc_info=True)
            return DistributionResult(channel=self.name, ok=False, detail=str(exc)[:300])

    def _upload_media(self, client: httpx.Client, headers: dict, image_url: str) -> str | None:
        if not image_url:
            return None
        try:
            img_resp = client.get(image_url, timeout=_TIMEOUT)
            img_resp.raise_for_status()
            content_type = img_resp.headers.get("content-type", "image/png").split(";")[0]
            upload_resp = client.post(
                "/api/v1/media",
                files={"file": ("share.png", img_resp.content, content_type)},
                headers=headers,
            )
            upload_resp.raise_for_status()
            return upload_resp.json()["id"]
        except Exception:
            # A share-card image is a nice-to-have — the status still posts,
            # link-only, if the fetch or upload fails.
            log.warning("mastodon media upload failed for %s", image_url, exc_info=True)
            return None

    @staticmethod
    def _compose_status(share: ArticleShare) -> str:
        parts = [share.title.strip()]
        if share.summary:
            parts.append(share.summary.strip())
        parts.append(share.url)
        # Hashtags matter more here than on Bluesky/Telegram: Mastodon has no
        # site-wide full-text search, so a hashtag is the main way someone
        # outside our followers ever finds this post.
        return compose_caption(parts=parts, tags=share.tags, max_chars=_MAX_STATUS_CHARS)
