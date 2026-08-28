"""Task registry import: scrape Celery tasks."""

from app.modules.scraper.tasks.bluesky_poll_tasks import poll_bluesky_sources
from app.modules.scraper.tasks.forum_poll_tasks import poll_forum_topics
from app.modules.scraper.tasks.youtube_poll_tasks import poll_youtube_sources

__all__ = [
    "poll_bluesky_sources",
    "poll_forum_topics",
    "poll_youtube_sources",
]
