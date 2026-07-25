"""Fan out an article to every enabled social channel. Each channel is independent — one failing (bad credentials, rate limit, network hiccup) never blocks or affects another. Never raises: distribution is a best-effort side effect of publishing, not part of the publish transaction itself."""

from __future__ import annotations

import logging

from app.modules.distribution.base import ArticleShare, DistributionResult, SocialDistributor

log = logging.getLogger(__name__)


def _build_distributors() -> list[SocialDistributor]:
    from app.core import config
    from app.modules.distribution.bluesky import BlueskyDistributor
    from app.modules.distribution.mastodon import MastodonDistributor
    from app.modules.distribution.telegram import TelegramDistributor

    return [
        BlueskyDistributor(
            handle=config.BLUESKY_IDENTIFIER, app_password=config.BLUESKY_APP_PASSWORD
        ),
        TelegramDistributor(bot_token=config.TELEGRAM_BOT_TOKEN, chat_id=config.TELEGRAM_CHAT_ID),
        MastodonDistributor(
            instance_url=config.MASTODON_INSTANCE_URL,
            access_token=config.MASTODON_ACCESS_TOKEN,
        ),
    ]


def distribute(share: ArticleShare) -> list[DistributionResult]:
    """Post `share` to every distributor that has credentials configured.

    Returns one DistributionResult per attempted (enabled) channel — a
    channel with no credentials is silently skipped, not reported as a
    failure, since "not configured" isn't an error state.
    """
    results: list[DistributionResult] = []
    for distributor in _build_distributors():
        if not distributor.enabled:
            continue
        try:
            result = distributor.post_article(share)
        except Exception as exc:  # belt-and-suspenders — distributors should
            # already catch internally, but a channel must never take down
            # the others or the calling task.
            log.warning(
                "%s distributor raised unexpectedly: %s", distributor.name, exc, exc_info=True
            )
            result = DistributionResult(channel=distributor.name, ok=False, detail=str(exc)[:300])
        results.append(result)
        if result.ok:
            log.info("posted article to %s", distributor.name)
        else:
            log.warning("failed to post article to %s: %s", distributor.name, result.detail)
    return results
