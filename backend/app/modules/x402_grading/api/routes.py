"""HTTP routes for x402 endpoint grading: paid grade, paid weighted score, free index.

Route paths are /api/v1/x402/*, not the bare /x402/* the build plan names.
nginx only proxies `location ^~ /api/` to this backend on the API host and
answers everything else with 404 (deploy/nginx/algorand-platform.conf), so a
bare /x402/grades would be unreachable in production without an nginx change
this change is not authorized to deploy.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from app.core import serialization
from app.core.config import settings
from app.core.errors import PlatformError
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.query_params import query_param
from app.modules.admin.auth import require_admin_wallet
from app.modules.x402 import circuit_breaker
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import (
    challenge_if_unpaid,
    mark_fulfilled,
    require_paid_request,
    run_with_refund,
)
from app.modules.x402.preview import preview_requested
from app.modules.x402.promo import promo_request_params

# The ONLY import of x402_directory anywhere in this module, and read-only:
# the tag leaderboard needs the directory's public ListingService to say which
# listed URLs carry a tag. It is bound into GradingService as a callable
# below, so the service and the stores stay directory-free (a test enforces
# that this file is the only one allowed to import it).
from app.modules.x402_directory.services.listing_service import ListingService
from app.modules.x402_grading.models.domain import (
    MAX_COMMENT_LENGTH,
    MAX_SCORE,
    MAX_URL_LENGTH,
    MIN_SCORE,
    GradeAggregate,
    GradedEndpoint,
    GradeSummary,
    GradingError,
    StoredGrade,
    WeightedGrade,
)
from app.modules.x402_grading.services.grading_service import (
    MIN_LEADERBOARD_GRADES,
    TOP_CANDIDATE_LIMIT,
    GradingService,
)
from app.modules.x402_grading.services.rate_limit import (
    grading_index_rate_limited,
    grading_summary_rate_limited,
    grading_top_rate_limited,
    grading_usage_proof_rate_limited,
)
from app.modules.x402_grading.services.usage_proof import (
    UsageProofError,
    fetch_live_payto,
    validate_txid_shape,
    verify_onchain_payment,
)
from app.schemas import X402GradeSubmission

logger = logging.getLogger(__name__)

# Stores are resolved lazily on first use, so these are safe as module-level
# singletons shared by every route. listing_service is the directory's PUBLIC
# service; the lookup below is the one read the tag leaderboard makes of it.
listing_service = ListingService()


def _listed_urls_for_tag(tag: str, limit: int) -> list[tuple[str, str]]:
    """TagCandidateLookup bound to the directory: (url, owner payer) of live listings carrying `tag`, newest first."""
    return [(item.url, item.payer) for item in listing_service.search(limit=limit, tag=tag)]


grading_service = GradingService(tag_lookup=_listed_urls_for_tag)

_GRADE_EXAMPLE = {
    "url": "https://api.example.com/v1/quote",
    "score": 4,
    "comment": "Accurate quotes, ~300ms, spec matched the 402 offer exactly.",
}


def _grade_json(item: StoredGrade) -> dict:
    """Serialize one stored grade for the wire."""
    return {
        "grader": item.grader,
        "score": item.score,
        "comment": item.comment,
        "created_at_epoch": item.created_at_epoch,
        "settlement_tx_id": item.settlement_tx_id,
        "usage_verified": item.usage_verified,
    }


def _weighted_grade_json(item: WeightedGrade) -> dict:
    """Serialize one graded opinion with the credibility weight it carried."""
    return {**_grade_json(item.grade), "weight": item.weight}


def _aggregate_json(item: GradeAggregate) -> dict:
    """Serialize a URL's aggregate score for the wire.

    Distribution keys are stringified because JSON object keys are strings --
    an int-keyed mapping would round-trip differently for a client than it does
    for us, and this endpoint is read by agents, not by this codebase.
    """
    return {
        "url_hash": item.url_hash,
        "url": item.url,
        "count": item.count,
        "weighted_mean": item.weighted_mean,
        "mean": item.mean,
        "total_weight": item.total_weight,
        "weights_resolved": item.weights_resolved,
        "distribution": {str(score): count for score, count in sorted(item.distribution.items())},
        "grades": [_weighted_grade_json(grade) for grade in item.grades],
        "truncated": item.truncated,
    }


def _indexed_json(item: GradedEndpoint) -> dict:
    """Serialize one free-index entry for the wire. Deliberately carries no score."""
    return {
        "url_hash": item.url_hash,
        "url": item.url,
        "last_graded_at_epoch": item.last_graded_at_epoch,
    }


def _summary_json(item: GradeSummary) -> dict:
    """Serialize the free per-URL summary. Count and recency only, never a score."""
    return {
        "url_hash": item.url_hash,
        "url": item.url,
        "count": item.count,
        "last_graded_at_epoch": item.last_graded_at_epoch,
        "truncated": item.truncated,
    }


def _leaderboard_json(rank: int, item: GradeAggregate) -> dict:
    """Serialize one leaderboard row: the aggregate numbers without the per-grader rows.

    Kept separate from _aggregate_json on purpose: the leaderboard sells a
    ranking across endpoints, not every grader's opinion of each -- that is
    what the per-URL score lookup sells, and bundling it here would resell
    25 of those for one price.
    """
    return {
        "rank": rank,
        "url_hash": item.url_hash,
        "url": item.url,
        "count": item.count,
        "weighted_mean": item.weighted_mean,
        "mean": item.mean,
        "total_weight": item.total_weight,
        "truncated": item.truncated,
    }


@dataclass
class _UsageProofOutcome:
    """Pre-gate usage-proof resolution: either an error response to return as-is, or a resolved proof."""

    error: Response | None
    verified: bool = False
    sender: str | None = None


def _resolve_usage_proof(tx_id: str, normalized_url: str) -> _UsageProofOutcome:
    """Run every usage-proof check that is possible BEFORE the payment gate.

    Extracted out of x402_grade_submit to keep it under the complexity
    budget (CLAUDE.md: extract before adding a branch); see that function's
    own docstring for the full policy this implements, including why the
    sender side of the check cannot happen here.
    """
    expected_payto = _expected_payto_for(normalized_url)
    if expected_payto is None:
        if settings.x402_grading_usage_proof_required:
            return _UsageProofOutcome(
                error=json_error_response(
                    400,
                    "invalid_request",
                    "could not determine the graded endpoint's payTo to verify tx_id against",
                )
            )
        return _UsageProofOutcome(error=None, verified=False)

    proof = verify_onchain_payment(tx_id=tx_id, expected_payto=expected_payto)
    if proof.verified is False:
        return _UsageProofOutcome(error=json_error_response(400, "invalid_request", proof.detail))
    if proof.verified is None:
        if settings.x402_grading_usage_proof_required:
            return _UsageProofOutcome(
                error=json_error_response(
                    503, "unavailable", "could not verify tx_id right now — please retry shortly"
                )
            )
        return _UsageProofOutcome(error=None, verified=False)
    return _UsageProofOutcome(error=None, verified=True, sender=proof.actual_sender)


def _validate_and_prove_usage(
    raw_tx_id: str, normalized_url: str
) -> tuple[str, bool, str | None] | Response:
    """Shape-validate `tx_id` and resolve its usage proof in one call, or the error Response.

    Collapses the txid-shape try/except and the _resolve_usage_proof call
    into one branch at the call site (CLAUDE.md: extract before adding a
    branch) -- x402_grade_submit was over the complexity budget once the
    circuit-breaker check and run_with_refund wiring were added.
    """
    try:
        tx_id = validate_txid_shape(raw_tx_id)
    except UsageProofError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    proof_outcome = _resolve_usage_proof(tx_id, normalized_url)
    if proof_outcome.error is not None:
        return proof_outcome.error
    return tx_id, proof_outcome.verified, proof_outcome.sender


def _decode_and_resolve(
    request: Request,
) -> tuple[X402GradeSubmission, str, str] | Response:
    """Decode the body and normalize its url, or the error Response to return if either fails."""
    try:
        payload = serialization.decode(request.body, X402GradeSubmission)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))
    try:
        normalized_url, hashed = grading_service.resolve_url(payload.url)
    except GradingError as exc:
        return json_error_from_platform(exc)
    return payload, normalized_url, hashed


def _write_grade(
    *,
    normalized_url: str,
    hashed: str,
    payer: str,
    payload: X402GradeSubmission,
    payment_txid: str,
    usage_verified: bool,
) -> dict:
    """The real product write, for run_with_refund: raises on failure, never returns a Response.

    Was `_store_grade_or_error` (converted a GradingError to its own specific
    Response, payment taken, no refund) -- that path was already documented
    as "not reachable through this route today" (score range and the
    empty-payer/sender-mismatch cases are all handled before this point), so
    letting GradingError propagate into run_with_refund's refund-on-failure
    path is a strict improvement, not a loss of exercised behaviour: if
    `grading_service.submit` ever does fail here (a storage error, not a
    caller-input one -- every caller-checkable thing already ran pre-gate),
    the payer now gets their money back automatically instead of a bespoke
    error with no recourse.
    """
    grade = grading_service.submit(
        url=normalized_url,
        url_hash_value=hashed,
        grader=payer,
        score=payload.score,
        comment=payload.comment,
        settlement_tx_id=payment_txid or "",
        usage_verified=usage_verified,
    )
    return {"url_hash": grade.url_hash, "url": grade.url, "grade": _grade_json(grade)}


def _unattributable_payer_response(payment_txid: str, settlement_headers: dict) -> Response:
    """The gate settled a payment it could not attribute. GradingService.submit refuses this too."""
    logger.error(
        "x402 grading: settled payment %s carried no payer address; no grade stored", payment_txid
    )
    return Response(
        status_code=400,
        headers={"Content-Type": "application/json", **settlement_headers},
        description=serialization.dumps(
            {
                "error": {
                    "code": "invalid_request",
                    "message": (
                        "The settled payment carried no payer address, so there is no "
                        "wallet to record this grade under."
                    ),
                }
            }
        ),
    )


def _prepare_submission(
    request: Request,
) -> tuple[X402GradeSubmission, str, str, str, bool, str] | Response:
    """Every free, pre-gate check of a grade submission in order, or the Response that ends it.

    Decodes and resolves the body (_decode_and_resolve), applies the
    usage-proof rate limit, then verifies the cited txid on-chain
    (_validate_and_prove_usage). Returns (payload, normalized_url, hashed,
    tx_id, proof_verified, proof_sender). Extracted from x402_grade_submit
    so the route's own control flow stays within the complexity budget once
    the unpaid-challenge branch was added ahead of these checks.
    """
    decoded = _decode_and_resolve(request)
    if isinstance(decoded, Response):
        return decoded
    payload, normalized_url, hashed = decoded

    if grading_usage_proof_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many grade-submission requests — please try again later"
        )

    proof = _validate_and_prove_usage(payload.tx_id, normalized_url)
    if isinstance(proof, Response):
        return proof
    tx_id, proof_verified, proof_sender = proof
    return payload, normalized_url, hashed, tx_id, proof_verified, proof_sender


def _reject_if_sender_mismatched(
    *,
    proof_verified: bool,
    proof_sender: str | None,
    payer: str,
    tx_id: str,
    normalized_url: str,
    settlement_headers: dict,
) -> Response | None:
    """The txid checked out on-chain, but its sender is not the wallet that paid the grading fee.

    Rejected the same way an unattributable payer already is (see
    x402_grade_submit): payment taken, no grade stored, no refund on this
    path. Returns None when there is nothing to reject.
    """
    if not proof_verified or proof_sender is None or proof_sender == payer:
        return None
    logger.warning(
        "x402 grading: tx_id %s sender %s does not match settled payer %s for url=%s; "
        "no grade stored",
        tx_id,
        proof_sender,
        payer,
        normalized_url,
    )
    return Response(
        status_code=400,
        headers={"Content-Type": "application/json", **settlement_headers},
        description=serialization.dumps(
            {
                "error": {
                    "code": "invalid_request",
                    "message": (
                        "tx_id's sender does not match the wallet that paid this grading fee."
                    ),
                }
            }
        ),
    )


def _expected_payto_for(normalized_url: str) -> str | None:
    """The payTo a usage-proof txid must have paid, for any gradeable URL, or None if undeterminable.

    Directory-listed URL: its newest probe's payto_seen (097), no extra
    network call. Not listed, or listed with no useful probe result yet:
    fetch it live ourselves (SSRF-guarded, see usage_proof.fetch_live_payto)
    -- grading has never required a listing (see this function's caller's
    own docstring), so proof-of-payment must not narrow that either.
    """
    probe = listing_service.probe_status(normalized_url)
    if probe is not None:
        _listing, latest = probe
        if latest is not None and latest.payto_seen:
            return latest.payto_seen
    return fetch_live_payto(normalized_url)


def x402_grade_submit(request: Request) -> Response:
    """Paid: grade any x402 endpoint URL, 1-5 stars, with an optional one-line opinion.

    The flat fee is the whole cost of entry, exactly like the board's placement
    fee and the feature board's vote. There is no eligibility gate: the graded
    URL does not have to be listed with us.

    `tx_id` is MANDATORY (owner ask 2026-09-02): a real payment the grader
    made to the graded endpoint's own payTo, independently verified on-chain
    before the payment gate -- see services/usage_proof.py and
    _expected_payto_for above. This does not prove what the endpoint
    returned, only that a real payment happened; credibility weighting
    (services/credibility.py) is unchanged by it and still comes from the
    wallet's own track record spending WITH US.

    Nothing is held. The payment settles straight to the receive-only payTo
    like every other payment in this marketplace; there is no refund, no
    forfeiture and no escrow anywhere on this path.

    Everything checkable without knowing who is paying is checked BEFORE the
    payment gate, so a caller is never charged for a request that was doomed:
    the body is decoded and range-checked, the URL is normalized, the txid's
    shape and its on-chain receiver are checked -- all 400s (or, if the
    indexer itself is unreachable and settings.x402_grading_usage_proof_required
    is left at its default True, a 503). The one thing that cannot be
    checked pre-gate is whether the txid's SENDER is the same wallet that
    ends up paying the grading fee -- that is only knowable once the gate
    settles a real payer, so it is re-checked immediately after, and a
    mismatch there is rejected the same way an unattributable payer already
    is: payment taken, no grade stored, per this codebase's existing
    accepted contract for this class of after-gate failure -- deliberately
    NOT auto-refunded (see run_with_refund below): a sender mismatch means
    the grader cited a real payment that was not theirs, a caller-side
    problem, not ours, and refunding it would remove the one piece of
    friction against gaming usage-proof with someone else's txid.

    If the actual STORE write fails after the gate (see _write_grade), that
    IS auto-refunded -- money-back protection covers our own failures, not
    the grader's own bad-faith input.

    Does NOT accept modules/x402/promo.py's promo-code bypass (2026-09-07
    security review, finding 10 -- the same pattern already found and fixed
    for x402_directory and x402_board): `payer` is stored as the grade's
    attributed identity, and a promo redemption's wallet is checked for
    SYNTACTIC validity only, never proof of control (see promo.py's own
    docstring). Worse than a plain identity spoof here: the tx_id
    sender-mismatch check above only compares `payer` against the usage
    proof's real on-chain sender string -- a promo caller can read that
    sender address straight off the public chain and pass it as
    `?promo_wallet=`, sailing through the mismatch check while never having
    controlled that wallet at all. Real payment only; result.payer there is
    the cryptographically settled payer, not a caller-declared string.
    """
    if circuit_breaker.is_tripped("x402-grading-submit"):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    offer = {
        "price": settings.x402_grading_grade_price,
        "resource": "x402-grading-submit",
        # Reaches the payer as the 402's resource.description, before they
        # commit. It states the overwrite rule and how the grade will be
        # weighted, because neither is derivable from the price.
        "description": (
            "Grade any x402 endpoint 1-5 stars, with an optional one-line opinion. "
            "Any http(s) URL can be graded -- it does not have to be listed with us. "
            "Requires tx_id: a real payment YOU made to the graded endpoint's own payTo, "
            "verified on-chain before this gate -- no txid, no grade. One grade per "
            "wallet per URL: re-grading replaces your previous grade rather than adding "
            "a second one. Your grade is weighted in the published average by how much "
            "your wallet has paid this marketplace in total (unrelated to tx_id)."
        ),
        "extensions": describe_json_endpoint(
            # POST carries its input as a JSON body, so this must declare a
            # BODY discovery extension. Without body_type the package builds a
            # query-params one, which would describe this route's input
            # incorrectly.
            body_type="json",
            input={**_GRADE_EXAMPLE, "tx_id": "A" * 52},
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "maxLength": MAX_URL_LENGTH},
                    "score": {"type": "integer", "minimum": MIN_SCORE, "maximum": MAX_SCORE},
                    "comment": {"type": "string", "maxLength": MAX_COMMENT_LENGTH},
                    "tx_id": {"type": "string", "minLength": 52, "maxLength": 52},
                },
                "required": ["url", "score", "tx_id"],
            },
            output_example={
                "url_hash": "0" * 64,
                "url": _GRADE_EXAMPLE["url"],
                "grade": {
                    **_GRADE_EXAMPLE,
                    "grader": "...",
                    "created_at_epoch": 0,
                    "usage_verified": True,
                },
                "settlement_tx_id": "...",
            },
        ),
    }
    # An unpaid request sees the offer before its body is ever parsed (see
    # challenge_if_unpaid); with a payment attached, every check below still
    # runs before the gate so a doomed request is never charged.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    prepared = _prepare_submission(request)
    if isinstance(prepared, Response):
        return prepared
    payload, normalized_url, hashed, tx_id, proof_verified, proof_sender = prepared

    result = require_paid_request(request, **offer)
    if result.error:
        return result.error

    # No promo path reaches here (see this function's own docstring), so
    # payer is always the real, cryptographically settled payer.
    payer = result.payer or ""
    if not payer.strip():
        return _unattributable_payer_response(result.payment_txid, result.settlement_headers)

    # The txid's SENDER could only be known once the indexer answered
    # (pre-gate); whether it equals the wallet that actually paid the
    # grading fee could only be known once the gate settled a real payer
    # (this line). A mismatch here means the grader cited a real payment
    # that was not theirs -- rejected the same way an unattributable payer
    # already is above: payment taken, no grade stored, no refund on this
    # path (there is no refund mechanism here).
    mismatch_response = _reject_if_sender_mismatched(
        proof_verified=proof_verified,
        proof_sender=proof_sender,
        payer=payer,
        tx_id=tx_id,
        normalized_url=normalized_url,
        settlement_headers=result.settlement_headers,
    )
    if mismatch_response is not None:
        return mismatch_response

    outcome = run_with_refund(
        result,
        resource="x402-grading-submit",
        product_write=lambda: _write_grade(
            normalized_url=normalized_url,
            hashed=hashed,
            payer=payer,
            payload=payload,
            payment_txid=result.payment_txid,
            usage_verified=proof_verified,
        ),
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    mark_fulfilled(result.payment_txid, resource="x402-grading-submit")
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps({**outcome, "settlement_tx_id": result.payment_txid or ""}),
    )


def x402_grade_score(request: Request) -> Response:
    """Paid: the credibility-weighted aggregate for one URL, plus the raw mean and count.

    Priced above the grade-submission fee because it resells every grader's
    paid contribution at once, the same reasoning the feature board prices its
    demand read above its vote.

    Takes the URL itself as a query parameter rather than an opaque id: the
    caller already has the URL -- it is the thing they want to know about -- and
    making them look an id up first would be a second round trip for no gain.
    It is normalized with the same rule the write path uses, so a caller does
    not have to reproduce our normalization to hit the same aggregate.

    Two things are checked for FREE, before the gate: the URL is well-formed
    (400), and somebody has actually graded it (404). The second check reads
    the same existence index that GET /api/v1/x402/grades already serves for
    free, so it gives nothing away, and it means nobody is ever charged for an
    empty aggregate.
    """
    if circuit_breaker.is_tripped("x402-grading-score"):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )

    offer = {
        "price": settings.x402_grading_score_price,
        "resource": "x402-grading-score",
        "description": (
            "Read the aggregate grade for one x402 endpoint: the credibility-weighted "
            "mean, the plain unweighted mean, the grader count, the full 1-5 distribution, "
            "and every grader's score, opinion and weight. Each grade is weighted by how "
            "much that wallet has paid this marketplace in total, so a wallet with a long "
            "spending record counts for more than a fresh one. Supports ?preview=true "
            "(redacted, unpaid, rate-limited)."
        ),
        # GET takes its input in the query string, not a JSON body, so no
        # body_type here -- the package's default query-params declaration is
        # the right shape (see describe_json_endpoint's docstring).
        "extensions": describe_json_endpoint(
            input={"url": _GRADE_EXAMPLE["url"]},
            input_schema={
                "type": "object",
                "properties": {"url": {"type": "string", "maxLength": MAX_URL_LENGTH}},
                "required": ["url"],
            },
            output_example={
                "url_hash": "0" * 64,
                "url": _GRADE_EXAMPLE["url"],
                "count": 3,
                "weighted_mean": 4.612,
                "mean": 4.333,
                "total_weight": 930000,
                "weights_resolved": True,
                "distribution": {"1": 0, "2": 0, "3": 1, "4": 0, "5": 2},
                "grades": [],
                "truncated": False,
            },
        ),
    }
    # An unpaid request sees the offer before its query string is validated
    # (see challenge_if_unpaid); with a payment attached, the two free checks
    # below still run before the gate so nobody pays for an empty aggregate.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    raw_url = query_param(request.query_params.get("url", ""))
    if not raw_url:
        return json_error_response(400, "invalid_request", "url is required")

    try:
        _, hashed = grading_service.resolve_url(raw_url)
    except GradingError as exc:
        return json_error_from_platform(exc)

    endpoint = grading_service.graded_endpoint(hashed)
    if endpoint is None:
        return json_error_response(
            404,
            "not_found",
            "Nobody has graded that endpoint yet. GET /api/v1/x402/grades lists every "
            "endpoint that has at least one grade, free of charge.",
        )

    promo_code, promo_wallet = promo_request_params(request)
    result = require_paid_request(
        request,
        **offer,
        promo_code=promo_code,
        promo_wallet=promo_wallet,
        preview=preview_requested(request),
    )
    if result.error:
        return result.error

    if result.is_preview:
        # Shape, not values: real aggregation is never computed for a preview
        # caller. count=-1 is an unambiguous sentinel -- the free pre-check
        # above already proved this endpoint has at least one real grade, so
        # a real response can never legitimately carry a non-positive count.
        return Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            description=serialization.dumps(
                {
                    "url_hash": endpoint.url_hash,
                    "url": endpoint.url,
                    "count": -1,
                    "weighted_mean": 0.0,
                    "mean": 0.0,
                    "total_weight": 0,
                    "weights_resolved": False,
                    "distribution": {},
                    "grades": [],
                    "truncated": False,
                    "settlement_tx_id": "<preview>",
                }
            ),
        )

    outcome = run_with_refund(
        result,
        resource="x402-grading-score",
        product_write=lambda: _aggregate_json(grading_service.aggregate(endpoint)),
        request=request,
    )
    if isinstance(outcome, Response):
        return outcome

    if not result.is_promo:
        mark_fulfilled(result.payment_txid, resource="x402-grading-score")
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                **outcome,
                "settlement_tx_id": result.payment_txid or "",
                **({"via": "promo"} if result.is_promo else {}),
            }
        ),
    )


def x402_grade_index(request: Request) -> Response | dict:
    """Free: which endpoints have been graded at all, with no scores, rate-limited per IP.

    Free is existence, paid is signal -- the same line the feature board draws
    between its free browse and its paid demand read. This is this module's own
    index, built only from what has been graded here; it has nothing to do with
    what is or is not listed in the x402 directory.
    """
    if grading_index_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many grade-index requests — please try again later"
        )

    raw_limit = query_param(request.query_params.get("limit", ""))
    try:
        limit = int(raw_limit) if raw_limit else settings.x402_grading_max_results
    except ValueError:
        return json_error_response(400, "invalid_request", "limit must be an integer")

    items = grading_service.list_graded(limit=limit)
    return {"items": [_indexed_json(item) for item in items]}


def x402_grade_summary(request: Request) -> Response | dict:
    """Free: how many wallets graded one URL and when it was last graded, rate-limited per IP.

    Existence-tier data only -- the count and the timestamp, never a mean or
    a distribution -- so it gives away nothing the paid score lookup sells,
    and lets a caller decide whether an aggregate is worth paying for. The URL
    is normalized with the write path's rule, and an ungraded URL is a 404.
    """
    if grading_summary_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many grade-summary requests — please try again later"
        )

    raw_url = query_param(request.query_params.get("url", ""))
    if not raw_url:
        return json_error_response(400, "invalid_request", "url is required")
    try:
        _, hashed = grading_service.resolve_url(raw_url)
    except GradingError as exc:
        return json_error_from_platform(exc)

    endpoint = grading_service.graded_endpoint(hashed)
    if endpoint is None:
        return json_error_response(404, "not_found", "Nobody has graded that endpoint yet.")
    return _summary_json(grading_service.summary(endpoint))


def _resolve_leaderboard_tag(request: Request) -> tuple[str, list] | Response:
    """Validate `?tag=` and fetch its rankable candidates, or the error Response.

    Collapses the tag-presence check, the leaderboard_candidates try/except,
    and the empty-candidates 404 into one branch at the call site (CLAUDE.md:
    extract before adding a branch) -- x402_grade_top was over the
    complexity budget once the circuit-breaker check and run_with_refund
    wiring were added.
    """
    raw_tag = query_param(request.query_params.get("tag", ""))
    if not raw_tag:
        return json_error_response(400, "invalid_request", "tag is required")
    try:
        candidates = grading_service.leaderboard_candidates(raw_tag)
    except PlatformError as exc:
        # The directory's invalid-tag error (DirectoryError) or this module's
        # own "no lookup bound" -- both PlatformErrors, both pre-gate.
        return json_error_from_platform(exc)
    if not candidates:
        return json_error_response(
            404,
            "not_found",
            f"No endpoint listed under that tag has {MIN_LEADERBOARD_GRADES} or more "
            f"independent grades. GET /api/v1/x402/search?tag= lists the tag's endpoints "
            f"and GET /api/v1/x402/grades/summary?url= each one's grade count, free.",
        )
    return raw_tag, candidates


def x402_grade_top(request: Request) -> Response:
    """Paid: the top graded endpoints among directory listings carrying one tag.

    Candidates come from the directory's tag projection via its public
    service (the directory decides what "carries this tag" means); the
    ranking is by this module's credibility-weighted mean, ties broken by
    grader count then url_hash so the order is stable. Bounded to
    TOP_CANDIDATE_LIMIT listings, aggregated with ONE batched ledger lookup.

    Checked for FREE before the gate: the tag is usable (400) and at least one
    listing with that tag is rankable (404) -- at least MIN_LEADERBOARD_GRADES
    grades not counting the lister's own or a probe's (see
    GradingService.leaderboard_candidates), so nobody pays for an empty
    leaderboard. That pre-gate check scans up to TOP_CANDIDATE_LIMIT grade
    partitions, so the whole route is rate-limited per IP FIRST: the free
    part must not be a free scan surface.
    """
    if circuit_breaker.is_tripped("x402-grading-top"):
        return json_error_response(
            503,
            "temporarily_disabled",
            "This endpoint is temporarily disabled after an elevated failure rate. "
            "Try again later.",
        )
    if grading_top_rate_limited(request):
        return json_error_response(
            429, "rate_limited", "Too many leaderboard requests — please try again later"
        )
    offer = {
        "price": settings.x402_grading_score_price,
        "resource": "x402-grading-top",
        "description": (
            f"Read the top graded x402 endpoints listed under one directory tag, ranked "
            f"by credibility-weighted mean grade, with each endpoint's plain mean and "
            f"grader count. Considers at most {TOP_CANDIDATE_LIMIT} listings per tag; an "
            f"endpoint needs at least {MIN_LEADERBOARD_GRADES} grades to be ranked, and a "
            f"lister's grade of their own listing is not counted. "
            f"Per-grader opinions are not included -- GET /api/v1/x402/grades/score "
            f"sells those per endpoint. Supports ?preview=true (redacted, unpaid, "
            f"rate-limited)."
        ),
        # GET with query-string input, so no body_type (see x402_grade_score).
        "extensions": describe_json_endpoint(
            input={"tag": "pricing"},
            input_schema={
                "type": "object",
                "properties": {"tag": {"type": "string", "maxLength": 64}},
                "required": ["tag"],
            },
            output_example={
                "tag": "pricing",
                "items": [
                    {
                        "rank": 1,
                        "url_hash": "0" * 64,
                        "url": _GRADE_EXAMPLE["url"],
                        "count": 3,
                        "weighted_mean": 4.612,
                        "mean": 4.333,
                        "total_weight": 930000,
                        "truncated": False,
                    }
                ],
                "weights_resolved": True,
                "candidates_considered": 1,
                "settlement_tx_id": "...",
            },
        ),
    }
    # An unpaid request sees the offer before the tag is resolved (see
    # challenge_if_unpaid); with a payment attached, the free candidate check
    # below still runs before the gate so nobody pays for an empty board.
    challenge = challenge_if_unpaid(request, **offer)
    if challenge is not None:
        return challenge

    resolved = _resolve_leaderboard_tag(request)
    if isinstance(resolved, Response):
        return resolved
    raw_tag, candidates = resolved

    promo_code, promo_wallet = promo_request_params(request)
    result = require_paid_request(
        request,
        **offer,
        promo_code=promo_code,
        promo_wallet=promo_wallet,
        preview=preview_requested(request),
    )
    if result.error:
        return result.error

    if result.is_preview:
        # Shape, not values: rank_leaderboard (the real ranking) is never
        # called for a preview caller -- the order itself is part of what
        # this route sells, not just the numbers, so it must not leak
        # either. candidates_considered=-1 is an unambiguous sentinel: the
        # free pre-check above already proved at least one candidate exists.
        return Response(
            status_code=200,
            headers={"Content-Type": "application/json"},
            description=serialization.dumps(
                {
                    "tag": raw_tag.strip(),
                    "items": [
                        {
                            "rank": 1,
                            "url_hash": "<preview>",
                            "url": "<preview>",
                            "count": -1,
                            "weighted_mean": 0.0,
                            "mean": 0.0,
                            "total_weight": 0,
                            "truncated": False,
                        }
                    ],
                    "weights_resolved": False,
                    "candidates_considered": -1,
                    "settlement_tx_id": "<preview>",
                }
            ),
        )

    def _rank_and_build() -> dict:
        aggregates = grading_service.rank_leaderboard(candidates)
        return {
            "tag": raw_tag.strip(),
            "items": [
                _leaderboard_json(rank, item) for rank, item in enumerate(aggregates, start=1)
            ],
            # One batched lookup, so one answer for the whole board.
            "weights_resolved": all(item.weights_resolved for item in aggregates),
            "candidates_considered": len(aggregates),
        }

    outcome = run_with_refund(
        result, resource="x402-grading-top", product_write=_rank_and_build, request=request
    )
    if isinstance(outcome, Response):
        return outcome

    if not result.is_promo:
        mark_fulfilled(result.payment_txid, resource="x402-grading-top")
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                **outcome,
                "settlement_tx_id": result.payment_txid or "",
                **({"via": "promo"} if result.is_promo else {}),
            }
        ),
    )


def x402_admin_delete_grade(request: Request) -> Response | dict:
    """Admin: remove one wallet's grade of one URL outright.

    For a grade that is abuse (a bought or coerced rating). Same shape as the
    directory's admin delist: url and grader as query params, session wallet
    verified first -- the X-Admin-Wallet header is never trusted. The URL is
    normalized with the write path's rule so the admin does not have to
    reproduce our normalization to hit the stored key.
    """
    denied = require_admin_wallet(request)
    if denied is not None:
        return denied

    raw_url = query_param(request.query_params.get("url", ""))
    grader = query_param(request.query_params.get("grader", "")).strip()
    if not raw_url or not grader:
        return json_error_response(400, "invalid_request", "url and grader are required")
    try:
        _, hashed = grading_service.resolve_url(raw_url)
    except GradingError as exc:
        return json_error_from_platform(exc)

    deleted = grading_service.delete(url_hash_value=hashed, grader=grader)
    if not deleted:
        return json_error_response(404, "not_found", "No grade of that url by that grader")
    return {"deleted": True, "url_hash": hashed, "grader": grader}


def register_x402_grading_routes(app: Router) -> None:
    """Register the paid routes (grade, score, tag leaderboard), the free ones (index, summary) and the admin delete.

    x402-marketplace-ux-audit.md section 3.3 "trust / grades": `summary` is
    renamed to `lookup` and `top` moves into the shared trust leaderboards
    namespace (N4 -- three unrelated paid "leaderboards", three names). Each
    new path is a second direct registration against the identical old
    handler; the old paths are never removed (section 3.5). `score` is left
    alone here -- the doc's proposed merge of `score` into `lookup` via a
    `?score=true` flag is a behavior change, not a rename, and is out of
    scope for this pass.
    """
    app.post("/api/v1/x402/grades")(x402_grade_submit)
    app.get("/api/v1/x402/grades")(x402_grade_index)
    app.get("/api/v1/x402/grades/score")(x402_grade_score)
    app.get("/api/v1/x402/grades/summary")(x402_grade_summary)
    app.get("/api/v1/x402/grades/lookup")(x402_grade_summary)
    app.get("/api/v1/x402/grades/top")(x402_grade_top)
    app.get("/api/v1/x402/trust/leaderboards/graded")(x402_grade_top)
    app.delete("/api/v1/admin/x402/grades")(x402_admin_delete_grade)
