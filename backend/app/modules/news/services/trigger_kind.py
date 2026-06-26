from __future__ import annotations

_ALGORAND_TXID_LEN = 52


def classify_article_trigger(
    *,
    service_id: str,
    trigger_txid: str | None,
    trigger_round: int | None,
    source_url: str | None = None,
    tags: list[str] | None = None,
) -> str:
    """Classify how an article was produced: chain, scheduled, or editorial (crawl)."""
    tagset = {t.lower() for t in (tags or [])}
    tx = (trigger_txid or "").strip()
    sid = (service_id or "").lower()
    url = (source_url or "").lower()

    if "weekly" in tagset or "digest" in tagset or sid.startswith("weekly-"):
        return "scheduled"
    if tx.startswith("weekly-digest") or tx.startswith("weekly-"):
        return "scheduled"
    if "coingecko.com" in url and (trigger_round is None or trigger_round == 0):
        return "scheduled"

    if (
        len(tx) == _ALGORAND_TXID_LEN
        and tx.isalnum()
        and tx.upper() == tx
        and not tx.startswith("weekly")
    ):
        return "chain"

    return "editorial"
