from __future__ import annotations

import json
import re
from urllib.parse import urlparse

from app.modules.chain_tail.chain_reader import RoundTransaction

_URL_RE = re.compile(
    r"https?://[^\s<>\"']+",
    re.IGNORECASE,
)
_DOMAIN_RE = re.compile(
    r"(?:^|[\s(])([a-z0-9][-a-z0-9]*\.(?:com|org|io|app|fi|co|net|dev|xyz))(?:/[\w./-]*)?",
    re.IGNORECASE,
)
_REKEY_PROXY_HINTS: dict[str, str] = {
    "service-proxy": "https://service-proxy.com",
}


def _urls_from_note(txn_json: str | None) -> list[str]:
    if not txn_json:
        return []
    try:
        data = json.loads(txn_json)
    except json.JSONDecodeError:
        return []
    note = data.get("note") if isinstance(data, dict) else None
    if note is None:
        return []
    if isinstance(note, str):
        blob = note
    else:
        try:
            blob = bytes(note).decode("utf-8", errors="ignore")
        except (TypeError, ValueError):
            return []
    return _URL_RE.findall(blob)


def _urls_from_rekey(txn_json: str | None) -> list[str]:
    if not txn_json:
        return []
    try:
        data = json.loads(txn_json)
    except json.JSONDecodeError:
        return []
    txn = data.get("txn") if isinstance(data, dict) else None
    if not isinstance(txn, dict):
        return []
    rekey = txn.get("rekey") or txn.get("rekey-to")
    if not isinstance(rekey, str) or not rekey:
        return []
    lower = rekey.lower()
    for hint, url in _REKEY_PROXY_HINTS.items():
        if hint in lower:
            return [url]
    if rekey.startswith("http"):
        return [rekey]
    return []


def _urls_from_clawback(txn_json: str | None, addresses: set[str]) -> list[str]:
    found: list[str] = []
    for addr in addresses:
        if not addr:
            continue
        hostish = addr.replace("_", "-").lower()[:32]
        if "." in hostish:
            found.append(f"https://{hostish}")
    return found


def extract_urls_from_tx(tx: RoundTransaction) -> list[str]:
    """Extract crawlable URLs from chain transaction note, rekey, and related fields."""
    addresses = {tx.sender}
    if tx.receiver:
        addresses.add(tx.receiver)

    urls: list[str] = []
    urls.extend(_urls_from_note(tx.txn_json))
    urls.extend(_urls_from_rekey(tx.txn_json))
    urls.extend(_urls_from_clawback(tx.txn_json, addresses))

    if tx.txn_json:
        for match in _DOMAIN_RE.finditer(tx.txn_json):
            domain = match.group(1).lower()
            urls.append(f"https://{domain}")

    seen: set[str] = set()
    unique: list[str] = []
    for raw in urls:
        u = raw.rstrip(".,;)")
        parsed = urlparse(u if "://" in u else f"https://{u}")
        if not parsed.netloc:
            continue
        key = u.lower()
        if key in seen:
            continue
        seen.add(key)
        unique.append(u)
    return unique


def enqueue_discovered_urls(tx: RoundTransaction) -> int:
    """Enqueue URLs from a transaction when discovery mode is enabled."""
    from app.core.config import DISCOVERY_MODE_ENABLED
    from app.modules.crawler.url_queue import enqueue_url

    if not DISCOVERY_MODE_ENABLED:
        return 0
    count = 0
    for url in extract_urls_from_tx(tx):
        _, created = enqueue_url(url, source="chain", priority=50)
        if created:
            count += 1
    return count
