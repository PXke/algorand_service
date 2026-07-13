"""Telegram auto-poster. Free Bot API, no review process — create a bot via
@BotFather, get a token, add the bot as admin to the target channel.

sendPhoto with the share-card image as an explicit `photo` URL, rather than
relying on Telegram's own link-preview crawler off a bare URL in the text —
guarantees the branded card renders instead of whatever Telegram's crawler
happens to fetch (or fails to, if it hasn't seen the URL before). Falls back
to sendMessage (plain text + link) when there's no image.
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
# Telegram caption limit is 1024 chars for media messages, 4096 for
# sendMessage — 1024 is the binding constraint here since sendPhoto is tried
# first.
_MAX_CAPTION_CHARS = 1024


def _caption(share: ArticleShare) -> str:
    parts = [f"<b>{_escape(share.title)}</b>"]
    if share.summary:
        parts.append(_escape(share.summary))
    parts.append(share.url)
    # Hashtags are plain alnum (see hashtags_for) — safe to drop into HTML
    # parse_mode unescaped.
    return compose_caption(parts=parts, tags=share.tags, max_chars=_MAX_CAPTION_CHARS)


def _escape(text: str) -> str:
    # Telegram HTML parse_mode only requires escaping these three.
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class TelegramDistributor(SocialDistributor):
    name = "telegram"

    def __init__(self, *, bot_token: str, chat_id: str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id

    @property
    def enabled(self) -> bool:
        return bool(self._bot_token and self._chat_id)

    def post_article(self, share: ArticleShare) -> DistributionResult:
        base = f"https://api.telegram.org/bot{self._bot_token}"
        caption = _caption(share)
        try:
            with httpx.Client(timeout=_TIMEOUT) as client:
                if share.image_url:
                    resp = client.post(
                        f"{base}/sendPhoto",
                        json={
                            "chat_id": self._chat_id,
                            "photo": share.image_url,
                            "caption": caption,
                            "parse_mode": "HTML",
                        },
                    )
                else:
                    resp = client.post(
                        f"{base}/sendMessage",
                        json={
                            "chat_id": self._chat_id,
                            "text": caption,
                            "parse_mode": "HTML",
                        },
                    )
                resp.raise_for_status()
                body = resp.json()
                if not body.get("ok"):
                    return DistributionResult(
                        channel=self.name, ok=False, detail=str(body.get("description", ""))[:300]
                    )
            return DistributionResult(channel=self.name, ok=True)
        except Exception as exc:
            log.warning("telegram post failed for %s: %s", share.url, exc, exc_info=True)
            return DistributionResult(channel=self.name, ok=False, detail=str(exc)[:300])
