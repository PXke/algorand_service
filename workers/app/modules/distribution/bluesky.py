"""Bluesky (AT Protocol) auto-poster. Free, open API — no paywall, no
approval process, just a bot account with an app password (Settings > App
Passwords in the Bluesky app; never the real account password — an app
password can be revoked independently and can't change account settings).

Three calls per post: authenticate (com.atproto.server.createSession),
upload the share-card image as a blob (com.atproto.repo.uploadBlob), then
create the post record with an external-link embed
(com.atproto.repo.createRecord) so the shared link renders as a card with
the article's title/summary/thumbnail, not a bare URL.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import httpx

from app.modules.distribution.base import (
    ArticleShare,
    DistributionResult,
    SocialDistributor,
    compose_caption,
)

log = logging.getLogger(__name__)

_SERVICE = "https://bsky.social"
_TIMEOUT = 15.0
# Bluesky posts are capped at 300 graphemes; leave headroom since the embed
# card (not the post text) carries the full title/description/image.
_MAX_POST_CHARS = 280


class BlueskyDistributor(SocialDistributor):
    name = "bluesky"

    def __init__(self, *, handle: str, app_password: str) -> None:
        self._handle = handle
        self._app_password = app_password

    @property
    def enabled(self) -> bool:
        return bool(self._handle and self._app_password)

    def post_article(self, share: ArticleShare) -> DistributionResult:
        try:
            with httpx.Client(base_url=_SERVICE, timeout=_TIMEOUT) as client:
                did, access_jwt = self._create_session(client)
                thumb_blob = self._upload_thumb(client, access_jwt, share.image_url)
                self._create_post(client, access_jwt, did, share, thumb_blob)
            return DistributionResult(channel=self.name, ok=True)
        except Exception as exc:
            log.warning("bluesky post failed for %s: %s", share.url, exc, exc_info=True)
            return DistributionResult(channel=self.name, ok=False, detail=str(exc)[:300])

    def _create_session(self, client: httpx.Client) -> tuple[str, str]:
        resp = client.post(
            "/xrpc/com.atproto.server.createSession",
            json={"identifier": self._handle, "password": self._app_password},
        )
        resp.raise_for_status()
        data = resp.json()
        return data["did"], data["accessJwt"]

    def _upload_thumb(self, client: httpx.Client, access_jwt: str, image_url: str) -> dict | None:
        if not image_url:
            return None
        try:
            img_resp = client.get(image_url, timeout=_TIMEOUT)
            img_resp.raise_for_status()
            content_type = img_resp.headers.get("content-type", "image/png").split(";")[0]
            upload_resp = client.post(
                "/xrpc/com.atproto.repo.uploadBlob",
                content=img_resp.content,
                headers={
                    "Authorization": f"Bearer {access_jwt}",
                    "Content-Type": content_type,
                },
            )
            upload_resp.raise_for_status()
            return upload_resp.json()["blob"]
        except Exception:
            # A share card image is a nice-to-have, not worth failing the
            # whole post over — Bluesky still renders a text-only link card.
            log.warning("bluesky thumb upload failed for %s", image_url, exc_info=True)
            return None

    def _create_post(
        self,
        client: httpx.Client,
        access_jwt: str,
        did: str,
        share: ArticleShare,
        thumb_blob: dict | None,
    ) -> None:
        text = compose_caption(
            parts=[share.title.strip()], tags=share.tags, max_chars=_MAX_POST_CHARS
        )
        external: dict = {
            "uri": share.url,
            "title": share.title[:300],
            "description": share.summary[:1000],
        }
        if thumb_blob is not None:
            external["thumb"] = thumb_blob
        record = {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
            "embed": {"$type": "app.bsky.embed.external", "external": external},
        }
        resp = client.post(
            "/xrpc/com.atproto.repo.createRecord",
            json={"repo": did, "collection": "app.bsky.feed.post", "record": record},
            headers={"Authorization": f"Bearer {access_jwt}"},
        )
        resp.raise_for_status()
