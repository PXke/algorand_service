"""xGov proposal watch: governance stories straight from the chain.

The new xGov portal (xgov.algorand.co) has no public REST API — it reads chain
state client-side. But every proposal is its own application created by the
xGov Registry app's escrow account, so one algod ``/v2/accounts/{escrow}``
call enumerates every proposal with its full global state inline: title,
status, requested amount, proposer, vote tallies, quorum.

One publish signal per (proposal, phase): a proposal entering discussion,
opening to vote, and reaching an outcome are each separate stories, deduped by
the snapshot store on a phase-scoped service_id (same pattern as the Bluesky
per-post lane). Draft/empty proposals are not news and are skipped.
"""

from __future__ import annotations

import base64
import hashlib
import logging
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

# smart_contracts/proposal/enums.py in algorandfoundation/xgov-beta-sc.
STATUS_PHASES: dict[int, str] = {
    20: "submitted",
    25: "voting",
    30: "approved",
    40: "rejected",
    45: "reviewed",
    50: "funded",
    60: "blocked",
}
# Reviewed (45) is an internal step between approved and funded — not a story.
_SILENT_PHASES = frozenset({"reviewed"})

_FUNDING_CATEGORY = {10: "small", 20: "medium", 30: "large"}
_FUNDING_TYPE = {10: "proactive", 20: "retroactive"}
_FOCUS = {
    10: "DeFi",
    20: "education",
    30: "libraries",
    40: "NFT",
    50: "tooling",
    60: "SaaS",
    70: "other",
}


def _checksummed(data: bytes) -> str:
    chk = hashlib.new("sha512_256", data).digest()[-4:]
    return base64.b32encode(data + chk).decode().rstrip("=")


def registry_escrow_address(app_id: int) -> str:
    """The application account that creates proposal apps (standard Algorand app-address derivation)."""
    return _checksummed(hashlib.new("sha512_256", b"appID" + app_id.to_bytes(8, "big")).digest())


def encode_address(pubkey: bytes) -> str:
    """Encode a 32-byte public key as a checksummed Algorand address string."""
    return _checksummed(pubkey) if len(pubkey) == 32 else ""


def decode_global_state(entries: list[dict]) -> dict[str, Any]:
    """Decode an algod TEAL key-value global-state list into a plain dict of ints/bytes."""
    state: dict[str, Any] = {}
    for kv in entries or []:
        try:
            key = base64.b64decode(kv["key"]).decode("utf-8", "replace")
            value = kv["value"]
            if value.get("type") == 2:
                state[key] = int(value.get("uint", 0))
            else:
                state[key] = base64.b64decode(value.get("bytes", "") or "")
        except Exception:
            continue
    return state


def _utf8(value: Any) -> str:  # noqa: ANN401 -- decoded TEAL global-state value, bytes or already-scalar
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace").strip()
    return str(value or "").strip()


def _iso(epoch: Any) -> str:  # noqa: ANN401 -- decoded TEAL global-state value, coerced via int()
    try:
        return datetime.fromtimestamp(int(epoch), tz=UTC).isoformat()
    except (TypeError, ValueError, OSError):
        return ""


def proposal_facts(app_id: int, state: dict[str, Any]) -> dict[str, str]:
    """Human-readable fact sheet from a proposal app's global state — the page_text handed to the writer, who verifies live via lookup_application."""
    status = int(state.get("status") or 0)
    phase = STATUS_PHASES.get(status, "")
    requested = int(state.get("requested_amount") or 0) / 1_000_000
    proposer = encode_address(state.get("proposer") or b"")
    lines = [
        f"xGov proposal #{app_id}: {_utf8(state.get('title'))}",
        f"Status: {phase} (on-chain status code {status})",
        f"Requested amount: {requested:,.0f} ALGO",
        f"Funding: {_FUNDING_CATEGORY.get(int(state.get('funding_category') or 0), 'unknown')} "
        f"category, {_FUNDING_TYPE.get(int(state.get('funding_type') or 0), 'unknown')} type, "
        f"focus {_FOCUS.get(int(state.get('focus') or 0), 'unknown')}",
        f"Proposer: {proposer}",
        f"Submitted: {_iso(state.get('submission_timestamp'))}",
        f"Voting opened: {_iso(state.get('vote_opening_timestamp'))}",
        f"Votes: {int(state.get('approvals') or 0)} approvals / "
        f"{int(state.get('rejections') or 0)} rejections / "
        f"{int(state.get('nulls') or 0)} null, "
        f"{int(state.get('voted_members') or 0)} of "
        f"{int(state.get('committee_members') or 0)} committee members voted",
        f"Quorum: {int(state.get('quorum_threshold') or 0)} members "
        f"(weighted {int(state.get('weighted_quorum_threshold') or 0)})",
        f"Portal: https://xgov.algorand.co/proposals/{app_id}",
        "",
        "VERIFY live state with lookup_application before publishing — tallies "
        "move while voting is open. Search the forum (discourse_forum) for the "
        "proposal number to find the community discussion.",
    ]
    return {"phase": phase, "title": _utf8(state.get("title")), "text": "\n".join(lines)}


def _phase_age_days(phase: str, state: dict[str, Any]) -> float | None:
    """Approximate age of the CURRENT phase. Terminal phases have no on-chain timestamp of their own — voting close (open + duration) approximates them. None = no usable timestamp (treated as stale, never fresh)."""
    from datetime import datetime

    epoch = None
    if phase == "submitted":
        epoch = state.get("submission_timestamp")
    elif phase == "voting":
        epoch = state.get("vote_opening_timestamp")
    else:
        opened = int(state.get("vote_opening_timestamp") or 0)
        if opened:
            epoch = opened + int(state.get("voting_duration") or 0)
    try:
        then = datetime.fromtimestamp(int(epoch), tz=UTC)
    except (TypeError, ValueError, OSError):
        return None
    return (datetime.now(tz=UTC) - then).total_seconds() / 86400.0


def poll_xgov_proposals() -> dict[str, Any]:
    """Enumerate proposals via the registry escrow's created apps and emit one publish signal per (proposal, phase) not yet seen.

    Phases older than XGOV_MAX_PHASE_AGE_DAYS are skipped, not seeded: without
    this the FIRST run backfills every historical proposal's current phase
    (~60 stale queue rows at once). Skipping by age is idempotent and cheap —
    old proposals are re-examined and re-skipped each poll.
    """
    from app.core import config
    from app.modules.ai.chain_tools import _algod_get
    from app.modules.newspaper.ingest_signal import ingest_publish_signal
    from app.modules.newspaper.snapshot_store import get_latest_snapshot, source_id_for_service

    escrow = registry_escrow_address(config.XGOV_REGISTRY_APP_ID)
    account = _algod_get(f"/v2/accounts/{escrow}")
    if not isinstance(account, dict) or account.get("error") or account.get("_status") == 404:
        return {"status": "error", "detail": str(account)[:200]}

    proposals = account.get("created-apps") or []
    new_signals = 0
    stale = 0
    results: list[dict[str, object]] = []
    for app in proposals:
        app_id = int(app.get("id") or 0)
        if not app_id:
            continue
        state = decode_global_state((app.get("params") or {}).get("global-state") or [])
        facts = proposal_facts(app_id, state)
        phase, title = facts["phase"], facts["title"]
        if not phase or phase in _SILENT_PHASES or not title:
            continue
        age = _phase_age_days(phase, state)
        if age is None or age > config.XGOV_MAX_PHASE_AGE_DAYS:
            stale += 1
            continue
        service_id = f"xgov-proposal:{app_id}:{phase}"
        if get_latest_snapshot(source_id_for_service(service_id)) is not None:
            continue
        outcome = ingest_publish_signal(
            service_id=service_id,
            display_name="xGov Governance",
            source_url=f"https://xgov.algorand.co/proposals/{app_id}",
            page_title=f"xGov proposal {phase}: {title}"[:150],
            page_text=facts["text"],
            source_kind="xgov",
            match_kind="xgov_proposal",
            match_value=str(app_id),
            txid=f"xgov-{app_id}-{phase}",
            published_at=_iso(
                state.get("vote_opening_timestamp") or state.get("submission_timestamp")
            ),
            # The proposal app is new, but xGov itself is a known program —
            # without the override every phase signal would misclassify as
            # SERVICE_DISCOVERY of a brand-new service.
            is_first_override=False,
        )
        if outcome.get("status") == "enqueued":
            new_signals += 1
        results.append({"app_id": app_id, "phase": phase, **outcome})

    return {
        "status": "ok",
        "proposals": len(proposals),
        "new_signals": new_signals,
        "stale_skipped": stale,
        "results": results[:40],
    }
