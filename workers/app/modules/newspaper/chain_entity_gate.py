"""Deterministic on-chain citation gate: cited chain entities must be real.

Root-caused 2026-07-17 (AlgoGlyph, deleted article 9eb96392): the writer cited
a real asset and creator address but fabricated the arithmetic on top of them
("50.16% of supply" when the chain says 25.08%). Owner's insight: if every
cited ASA id / wallet address / txid HAD to resolve against the chain and got
rendered as an explorer link, a wrong or invented one becomes mechanically
detectable — and a reader can always click through to the ground truth.

Policy per entity found in the body:
- Fails local validation (58-char address with a bad checksum): definitionally
  fabricated — nothing with an invalid checksum was ever copied from a real
  page or tool result. Flagged for revision.
- Resolves on mainnet or testnet: auto-linked (first occurrence) to an
  explorer — allo.info for mainnet, Lora for testnet — so readers can verify.
- Provably missing from BOTH networks (clean 404s, not network errors):
  flagged for revision naming the exact entity; at the final gate an explorer
  link wrapping it is delinked and the entity is recorded under
  payload['_chain_entities_unverified'] for the audit trail.
- Network errors while checking: fail-open, leave the text alone — an indexer
  outage must never strip a correct citation.

Like the link gate, the final gate only ever delinks or adds links — it never
rewrites prose, so the worst case is a missing hyperlink, never a broken
sentence. The revision loop is where the writer gets told to fix or drop the
citation itself.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Algorand addresses are 58 chars of RFC-4648 base32 (A-Z, 2-7); txids are 52.
# The word boundaries stop a 58-char run inside a longer base32 blob (e.g. a
# base32 note field) from matching.
_ADDR_RE = re.compile(r"\b[A-Z2-7]{58}\b")
_TXID_RE = re.compile(r"\b[A-Z2-7]{52}\b")
# Asset ids only count when the prose says they are one — bare numbers are
# far too ambiguous (block heights, dollar amounts, round numbers).
_ASSET_CTX_RE = re.compile(r"(?i)\b(?:asset(?:[\s-]+id)?|asa)\s*[#:]?\s*(\d{3,15})\b")
# Explorer links the writer already emitted — their ids get verified too, and
# a link to a nonexistent entity is delinked (the id itself is handled by the
# entity rules above). Includes algoexplorer.io (root-caused live 2026-08-10,
# Kaafila article): the writer legitimately found that domain quoted verbatim
# inside a real fetched page (kaafila.org's own 2021-era token page cites it),
# so link_gate's "traced = trusted" rule correctly let it through even though
# AlgoExplorer itself has since gone dark (DNS no longer resolves) — a real,
# once-valid citation going stale, not a fabricated url. _LEGACY_EXPLORER_
# DOMAINS below get their surviving (entity-verified) links REWRITTEN to the
# gate's own live explorer rather than merely delinked, so the citation
# survives instead of being dropped.
_LEGACY_EXPLORER_DOMAINS = frozenset({"algoexplorer.io", "testnet.algoexplorer.io"})
_EXPLORER_URL_RE = re.compile(
    r"https?://(?:www\.)?"
    r"(allo\.info|lora\.algokit\.io/(?:mainnet|testnet)|explorer\.perawallet\.app"
    r"|algoexplorer\.io|testnet\.algoexplorer\.io)"
    r"/(asset|account|address|tx|transaction)s?/([A-Za-z0-9]+)",
    re.IGNORECASE,
)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
_CODE_SPAN_RE = re.compile(r"`[^`]*`")

# Per-call network budget (each entity may cost up to 2 lookups, mainnet +
# testnet). Shared `checked` cache across revision passes keeps the real
# total low; anything past the cap is treated as unknown (fail-open).
_MAX_LIVE_CHECKS = 8

_EXPLORER_KIND = {
    "asset": "asset",
    "account": "address",
    "address": "address",
    "tx": "txid",
    "transaction": "txid",
}


def _is_valid_address(addr: str) -> bool:
    from app.modules.ai.chain_tools import _is_valid_address as _chk

    return _chk(addr)


def _grounding_corpus(trace: list[dict] | None, extra_texts: tuple[str, ...] | list[str]) -> str:
    parts: list[str] = []
    for entry in trace or ():
        try:
            parts.append(json.dumps(entry))
        except (TypeError, ValueError):
            parts.append(str(entry))
    parts.extend(t for t in extra_texts if isinstance(t, str))
    return "\n".join(parts)


def _classify_lookup(data: dict[str, Any]) -> str:
    if not isinstance(data, dict) or data.get("error"):
        return "unknown"
    if data.get("_status") == 404:
        return "missing"
    return "found"


def _two_network_status(main_data: dict[str, Any], fetch_test: Any) -> str:  # noqa: ANN401 -- zero-arg lazy fetch callable, only invoked when mainnet misses
    """'mainnet' if found there; else 'testnet' if found there (fetch_test is only called when mainnet missed); 'missing' only when BOTH are clean 404s; 'unknown' on any transport/API error, so an outage can never condemn a real citation."""
    main = _classify_lookup(main_data)
    if main == "found":
        return "mainnet"
    test = _classify_lookup(fetch_test())
    if test == "found":
        return "testnet"
    if main == "missing" and test == "missing":
        return "missing"
    return "unknown"


def _lookup_status(kind: str, value: str) -> str:
    """One entity's on-chain status: 'mainnet' | 'testnet' | 'missing' | 'unknown'."""
    from app.modules.ai.chain_tools import _algod_get, _mainnet_idx_get, _testnet_idx_get

    if kind == "asset":
        return _two_network_status(
            _algod_get(f"/v2/assets/{value}"), lambda: _testnet_idx_get(f"/v2/assets/{value}")
        )
    if kind == "txid":
        return _two_network_status(
            _mainnet_idx_get(f"/v2/transactions/{value}"),
            lambda: _testnet_idx_get(f"/v2/transactions/{value}"),
        )
    if kind == "address":
        # Any checksum-valid address "exists" on algod (zero balance included),
        # so network lookups can't distinguish real from invented — provenance
        # (did it appear in research?) is the only meaningful test, done by the
        # caller. Default the link target to mainnet.
        return "mainnet"
    return "unknown"


def _entity_status(
    kind: str,
    value: str,
    *,
    checked: dict[tuple[str, str], str],
    budget: list[int],
) -> str:
    key = (kind, value)
    if key not in checked:
        if budget[0] <= 0:
            return "unknown"  # budget spent — fail-open, don't cache
        budget[0] -= 1
        checked[key] = _lookup_status(kind, value)
    return checked[key]


def find_chain_entities(body: str) -> list[tuple[str, str]]:
    """Unique (kind, value) entities cited in the body, in first-seen order.

    Kinds: 'address', 'txid', 'asset'. Explorer-link ids are included under
    their entity kind.
    """
    seen: set[tuple[str, str]] = set()
    ordered: list[tuple[str, str]] = []

    def _add(kind: str, value: str) -> None:
        key = (kind, value)
        if key not in seen:
            seen.add(key)
            ordered.append(key)

    for m in _ADDR_RE.finditer(body):
        _add("address", m.group(0))
    for m in _TXID_RE.finditer(body):
        _add("txid", m.group(0))
    for m in _ASSET_CTX_RE.finditer(body):
        _add("asset", m.group(1))
    for m in _EXPLORER_URL_RE.finditer(body):
        kind = _EXPLORER_KIND.get(m.group(2).lower())
        if kind:
            _add(kind, m.group(3))
    return ordered


def unverifiable_chain_entities(
    body: str,
    trace: list[dict] | None,
    *,
    extra_texts: tuple[str, ...] | list[str] = (),
    checked: dict[tuple[str, str], str] | None = None,
) -> list[str]:
    """Revision-loop feedback: one concrete message per fabrication-suspect on-chain citation, so the writer can fix or drop it instead of the final gate silently delinking. Pass a shared ``checked`` dict across passes to reuse network results."""
    if not body:
        return []
    checked = checked if checked is not None else {}
    corpus = _grounding_corpus(trace, extra_texts)
    budget = [_MAX_LIVE_CHECKS]
    issues: list[str] = []
    for kind, value in find_chain_entities(body):
        if kind == "address":
            if not _is_valid_address(value):
                issues.append(
                    f"on-chain citation: {value} is not a valid Algorand address "
                    "(bad checksum) — it cannot have come from a real page or tool "
                    "result; replace it with the real address from your research "
                    "or remove it"
                )
            elif value not in corpus:
                issues.append(
                    f"on-chain citation: address {value} never appeared in your "
                    "research — only cite addresses you actually found via tools "
                    "or fetched pages; verify with lookup_account or remove it"
                )
            continue
        if value in corpus:
            continue  # vouched by research; linking is the final gate's job
        status = _entity_status(kind, value, checked=checked, budget=budget)
        if status == "missing":
            label = "asset id" if kind == "asset" else "transaction"
            tool = "lookup_asset or lookup_asset_by_name" if kind == "asset" else "testnet_lookup"
            issues.append(
                f"on-chain citation: {label} {value} does not exist on Algorand "
                f"mainnet or testnet — verify the real one with {tool} and "
                "correct it, or remove the claim"
            )
    return issues


def _protected_spans(body: str) -> list[tuple[int, int]]:
    """Regions where we must not inject links: existing markdown links (text and url) and inline code spans."""
    spans = [m.span() for m in _MD_LINK_RE.finditer(body)]
    spans.extend(m.span() for m in _CODE_SPAN_RE.finditer(body))
    return spans


def _in_spans(pos: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(s < end and pos < e for s, e in spans)


def _explorer_url(kind: str, value: str, net: str) -> str:
    if net == "testnet":
        path = {"asset": "asset", "address": "account", "txid": "transaction"}[kind]
        return f"https://lora.algokit.io/testnet/{path}/{value}"
    path = {"asset": "asset", "address": "account", "txid": "tx"}[kind]
    return f"https://allo.info/{path}/{value}"


def _compute_entity_statuses(
    body: str, *, checked: dict[tuple[str, str], str], budget: list[int]
) -> dict[tuple[str, str], str]:
    """Every cited entity's status: 'invalid' for a bad-checksum address, else the live on-chain lookup result."""
    statuses: dict[tuple[str, str], str] = {}
    for kind, value in find_chain_entities(body):
        if kind == "address" and not _is_valid_address(value):
            statuses[(kind, value)] = "invalid"
            continue
        statuses[(kind, value)] = _entity_status(kind, value, checked=checked, budget=budget)
    return statuses


def _delink_dead_explorer_urls(body: str, statuses: dict[tuple[str, str], str]) -> str:
    """Delink explorer urls whose target entity is provably missing (or an invalid address) — same delink-not-rewrite rule as the link gate. Audit entries come from the statuses dict itself (covers every entity, explorer-linked or not), so this never appends its own."""

    def _delink(match: re.Match) -> str:
        um = _EXPLORER_URL_RE.search(match.group(2))
        if um is None:
            return match.group(0)
        kind = _EXPLORER_KIND.get(um.group(2).lower())
        if kind is None:
            return match.group(0)
        status = statuses.get((kind, um.group(3)))
        if status in ("missing", "invalid"):
            return match.group(1)
        return match.group(0)

    return _MD_LINK_RE.sub(_delink, body)


def _rewrite_legacy_explorer_urls(body: str, statuses: dict[tuple[str, str], str]) -> str:
    """Rewrite a legacy-domain explorer link (_LEGACY_EXPLORER_DOMAINS) whose entity is verified live to the gate's own current explorer — a citation surviving from a since-decayed source shouldn't just lose its link, it should point somewhere that still works. Run AFTER _delink_dead_explorer_urls, which already dropped any legacy link whose entity doesn't exist at all."""

    def _rewrite(match: re.Match) -> str:
        um = _EXPLORER_URL_RE.search(match.group(2))
        if um is None or um.group(1).lower() not in _LEGACY_EXPLORER_DOMAINS:
            return match.group(0)
        kind = _EXPLORER_KIND.get(um.group(2).lower())
        if kind is None:
            return match.group(0)
        status = statuses.get((kind, um.group(3)))
        if status not in ("mainnet", "testnet"):
            return match.group(0)
        new_url = _explorer_url(kind, um.group(3), status)
        return f"[{match.group(1)}]({new_url})"

    return _MD_LINK_RE.sub(_rewrite, body)


def _auto_link_entities(
    body: str, *, statuses: dict[tuple[str, str], str], corpus: str
) -> tuple[str, list[dict[str, str]], list[dict[str, str]]]:
    """Auto-link the first plain-text occurrence of each verified entity to an explorer. Returns (new_body, linked, unverified)."""
    spans = _protected_spans(body)
    linked: list[dict[str, str]] = []
    unverified: list[dict[str, str]] = []
    replacements: list[tuple[int, int, str]] = []
    for (kind, value), status in statuses.items():
        if status == "invalid":
            unverified.append({"kind": kind, "value": value, "why": "invalid"})
            continue
        if status == "missing":
            unverified.append({"kind": kind, "value": value, "why": "missing"})
            continue
        if status not in ("mainnet", "testnet"):
            continue
        if kind == "address" and value not in corpus:
            # An existing-but-untraced address is still fabrication-suspect
            # (algod can't disprove it); don't lend it an explorer link.
            unverified.append({"kind": kind, "value": value, "why": "untraced"})
            continue
        pattern = (
            re.compile(re.escape(value))
            if kind != "asset"
            else re.compile(rf"(?<![\d/]){re.escape(value)}(?![\d/])")
        )
        for m in pattern.finditer(body):
            if _in_spans(m.start(), m.end(), spans):
                continue
            if any(m.start() < e and s < m.end() for s, e, _ in replacements):
                continue  # overlaps an already-claimed replacement region
            url = _explorer_url(kind, value, status)
            replacements.append((m.start(), m.end(), f"[{value}]({url})"))
            linked.append({"kind": kind, "value": value, "net": status})
            break

    for start, end, text in sorted(replacements, key=lambda r: r[0], reverse=True):
        body = body[:start] + text + body[end:]
    return body, linked, unverified


def link_and_verify_chain_entities(
    payload: dict[str, Any],
    trace: list[dict] | None,
    *,
    extra_texts: tuple[str, ...] | list[str] = (),
    checked: dict[tuple[str, str], str] | None = None,
) -> dict[str, Any]:
    """Final compose gate. Auto-links the first occurrence of each verified entity to an explorer, delinks explorer urls that point at provably nonexistent entities, and records the audit trail on the payload (``_chain_entities_linked`` / ``_chain_entities_unverified``). Never touches prose."""
    from app.core.config import CHAIN_ENTITY_GATE_ENABLED

    if not CHAIN_ENTITY_GATE_ENABLED:
        return payload
    body = payload.get("body")
    if not isinstance(body, str) or not body:
        return payload

    checked = checked if checked is not None else {}
    corpus = _grounding_corpus(trace, extra_texts)
    budget = [_MAX_LIVE_CHECKS]

    statuses = _compute_entity_statuses(body, checked=checked, budget=budget)
    body = _delink_dead_explorer_urls(body, statuses)
    body = _rewrite_legacy_explorer_urls(body, statuses)
    body, linked, unverified = _auto_link_entities(body, statuses=statuses, corpus=corpus)

    if body != payload.get("body"):
        payload["body"] = body
    if linked:
        payload["_chain_entities_linked"] = linked
    if unverified:
        logger.warning(
            "chain-entity gate flagged %d unverifiable citation(s): %s",
            len(unverified),
            ", ".join(f"{u['kind']}:{u['value'][:20]}({u['why']})" for u in unverified[:8]),
        )
        payload["_chain_entities_unverified"] = unverified
    return payload
