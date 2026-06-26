from __future__ import annotations

import re

_TELEGRAM_RE = re.compile(
    r"^telegram://(?:(?:s|chat)/)?(?P<id>[-\d\w@]+)/?$",
    re.I,
)


def is_telegram_scrape_url(url: str) -> bool:
    raw = url.strip()
    if raw.startswith("https://t.me/"):
        return True
    return bool(_TELEGRAM_RE.match(raw))


def parse_telegram_chat_ref(url: str) -> str | None:
    raw = url.strip()
    if raw.startswith("https://t.me/"):
        path = raw.replace("https://t.me/", "").strip("/")
        if path.startswith("s/"):
            path = path[2:]
        return path.split("/")[0] or None
    match = _TELEGRAM_RE.match(raw)
    if not match:
        return None
    return match.group("id")


def resolve_telegram_preview_url(scrape_url: str) -> str | None:
    """Map registry URL to public web preview https://t.me/s/…"""
    ref = parse_telegram_chat_ref(scrape_url)
    if not ref:
        return None
    username = ref.lstrip("@")
    return f"https://t.me/s/{username}"
