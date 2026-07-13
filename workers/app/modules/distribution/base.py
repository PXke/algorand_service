"""Abstract base for auto-posting a published article to an external social
channel (Bluesky, Telegram, ...). One method per channel to implement; the
dispatcher (dispatcher.py) handles enable-checking, per-channel failure
isolation, and fan-out so adding a new channel never touches an existing one.
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArticleShare:
    """Everything a channel needs to compose a post — deliberately just
    strings, no article_id/DB coupling, so a distributor is trivially
    testable without Cassandra."""

    title: str
    summary: str
    url: str
    image_url: str
    tags: tuple[str, ...] = ()  # writer's lowercase topic slugs, e.g. ("defi", "governance")


# Writer tags are lowercase slugs (mistral_compose.py); a few read oddly
# title-cased so spell them out the way the ecosystem actually writes them.
_HASHTAG_CASE_OVERRIDES = {
    "defi": "DeFi",
    "nft": "NFT",
    "nfts": "NFTs",
    "api": "API",
    "dao": "DAO",
    "daos": "DAOs",
    "kyc": "KYC",
    "seo": "SEO",
    "dapp": "dApp",
    "dapps": "dApps",
    "asa": "ASA",
    "asas": "ASAs",
    "amm": "AMM",
    "tvl": "TVL",
    "algorand": "Algorand",
}


def _hashtag_label(raw: str) -> str:
    key = raw.strip().lower()
    override = _HASHTAG_CASE_OVERRIDES.get(key)
    if override:
        return override
    words = re.split(r"[^a-zA-Z0-9]+", raw)
    return "".join(w[:1].upper() + w[1:] for w in words if w)


def hashtags_for(tags: Sequence[str], *, limit: int = 4) -> list[str]:
    """Deterministic tag-slug -> hashtag list, always anchored by #Algorand
    first (ecosystem discovery matters more than any one article's topic,
    especially on Mastodon where hashtags ARE the discovery mechanism — no
    site-wide full-text search there). Deduped case-insensitively so an
    article already tagged "algorand" doesn't produce #Algorand twice."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in ("algorand", *tags):
        key = re.sub(r"[^a-z0-9]", "", raw.strip().lower())
        if not key or key in seen:
            continue
        seen.add(key)
        label = _hashtag_label(raw)
        if label:
            out.append(f"#{label}")
        if len(out) >= limit:
            break
    return out


def compose_caption(*, parts: Sequence[str], tags: Sequence[str], max_chars: int) -> str:
    """Join `parts` with blank lines and append a hashtag line built from
    `tags`. Hashtags are the least important content, so they're dropped one
    at a time before anything else is truncated — a caption that fits without
    a mid-sentence ellipsis reads far better than one with all hashtags kept."""
    tag_list = hashtags_for(tags)
    text = "\n\n".join([*parts, " ".join(tag_list)] if tag_list else parts)
    while len(text) > max_chars and tag_list:
        tag_list = tag_list[:-1]
        text = "\n\n".join([*parts, " ".join(tag_list)] if tag_list else parts)
    if len(text) > max_chars:
        text = text[: max_chars - 1].rstrip() + "…"
    return text


@dataclass(frozen=True)
class DistributionResult:
    channel: str
    ok: bool
    detail: str = ""


class SocialDistributor(ABC):
    """One external channel. `name` is the short id used in config/logging
    (e.g. "bluesky"); `enabled` gates whether post_article() should even be
    attempted (missing credentials, feature flag off, etc — checked by the
    dispatcher before calling post_article so a disabled channel never makes
    a network call)."""

    name: str

    @property
    @abstractmethod
    def enabled(self) -> bool: ...

    @abstractmethod
    def post_article(self, share: ArticleShare) -> DistributionResult:
        """Post the article to this channel. Must not raise — catch
        everything internally and return a failed DistributionResult, since
        the dispatcher calls every enabled channel regardless of whether an
        earlier one raised."""
