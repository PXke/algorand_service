"""HTTP routes for x402 endpoint grading: paid grade, paid weighted score, free index.

Route paths are /api/v1/x402/*, not the bare /x402/* the build plan names.
nginx only proxies `location ^~ /api/` to this backend on the API host and
answers everything else with 404 (deploy/nginx/algorand-platform.conf), so a
bare /x402/grades would be unreachable in production without an nginx change
this change is not authorized to deploy.
"""

from __future__ import annotations

import logging

from app.core import serialization
from app.core.config import settings
from app.core.http import Request, Response, Router
from app.core.http_errors import json_error_from_platform, json_error_response
from app.core.query_params import query_param
from app.modules.x402.discovery import describe_json_endpoint
from app.modules.x402.paid_request import require_paid_request
from app.modules.x402_grading.models.domain import (
    MAX_COMMENT_LENGTH,
    MAX_SCORE,
    MAX_URL_LENGTH,
    MIN_SCORE,
    GradeAggregate,
    GradedEndpoint,
    GradingError,
    StoredGrade,
    WeightedGrade,
)
from app.modules.x402_grading.services.grading_service import GradingService
from app.modules.x402_grading.services.rate_limit import grading_index_rate_limited
from app.schemas import X402GradeSubmission

logger = logging.getLogger(__name__)

# Stores are resolved lazily on first use, so this is safe as a module-level
# singleton shared by all three routes.
grading_service = GradingService()

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


def x402_grade_submit(request: Request) -> Response:
    """Paid: grade any x402 endpoint URL, 1-5 stars, with an optional one-line opinion.

    The flat fee is the whole cost of entry, exactly like the board's placement
    fee and the feature board's vote. There is no eligibility gate: the graded
    URL does not have to be listed with us, and this module never tries to
    prove the grader paid the endpoint they are grading -- that payment goes to
    a third party's payTo through a third party's facilitator and is not in our
    ledger by construction. What the payment buys is one weighted data point,
    where the weight is that wallet's own track record with US (see
    services/credibility.py).

    Nothing is held. The payment settles straight to the receive-only payTo
    like every other payment in this marketplace; there is no refund, no
    forfeiture and no escrow anywhere on this path.

    Everything checkable without knowing who is paying is checked BEFORE the
    payment gate, so a caller is never charged for a request that was doomed:
    the body is decoded and range-checked and the URL is normalized, both 400s.
    After the gate the only way to fail is an unattributable payer, which is a
    property of the payment itself rather than of the request.
    """
    try:
        payload = serialization.decode(request.body, X402GradeSubmission)
    except serialization.DecodeError as exc:
        return json_error_response(400, "invalid_request", str(exc))

    try:
        normalized_url, hashed = grading_service.resolve_url(payload.url)
    except GradingError as exc:
        return json_error_from_platform(exc)

    result = require_paid_request(
        request,
        price=settings.x402_grading_grade_price,
        resource="x402-grading-submit",
        # Reaches the payer as the 402's resource.description, before they
        # commit. It states the overwrite rule and how the grade will be
        # weighted, because neither is derivable from the price.
        description=(
            "Grade any x402 endpoint 1-5 stars, with an optional one-line opinion. "
            "Any http(s) URL can be graded -- it does not have to be listed with us. "
            "One grade per wallet per URL: re-grading replaces your previous grade "
            "rather than adding a second one. Your grade is weighted in the published "
            "average by how much your wallet has paid this marketplace in total."
        ),
        extensions=describe_json_endpoint(
            # POST carries its input as a JSON body, so this must declare a
            # BODY discovery extension. Without body_type the package builds a
            # query-params one, which would describe this route's input
            # incorrectly.
            body_type="json",
            input=_GRADE_EXAMPLE,
            input_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "maxLength": MAX_URL_LENGTH},
                    "score": {"type": "integer", "minimum": MIN_SCORE, "maximum": MAX_SCORE},
                    "comment": {"type": "string", "maxLength": MAX_COMMENT_LENGTH},
                },
                "required": ["url", "score"],
            },
            output_example={
                "url_hash": "0" * 64,
                "url": _GRADE_EXAMPLE["url"],
                "grade": {**_GRADE_EXAMPLE, "grader": "...", "created_at_epoch": 0},
                "settlement_tx_id": "...",
            },
        ),
    )
    if result.error:
        return result.error

    payer = result.payer or ""
    if not payer.strip():
        # The gate settled a payment it could not attribute. GradingService
        # .submit refuses this too -- see there for why an unattributable grade
        # cannot be stored at all.
        logger.error(
            "x402 grading: settled payment %s carried no payer address; no grade stored",
            result.payment_txid,
        )
        return Response(
            status_code=400,
            headers={"Content-Type": "application/json", **result.settlement_headers},
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

    try:
        grade = grading_service.submit(
            url=normalized_url,
            url_hash_value=hashed,
            grader=payer,
            score=payload.score,
            comment=payload.comment,
            settlement_tx_id=result.payment_txid or "",
        )
    except GradingError as exc:
        # Not reachable through this route today -- the score range is enforced
        # by the request schema before the gate and the empty payer just above.
        # Kept explicit so a future validation rule in submit cannot silently
        # 500 a request whose payment has already been taken.
        logger.error(
            "x402 grading: settled payment %s could not be stored as a grade: %s",
            result.payment_txid,
            exc.message,
        )
        return Response(
            status_code=exc.http_status,
            headers={"Content-Type": "application/json", **result.settlement_headers},
            description=serialization.dumps({"error": {"code": exc.code, "message": exc.message}}),
        )

    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {
                "url_hash": grade.url_hash,
                "url": grade.url,
                "grade": _grade_json(grade),
                "settlement_tx_id": result.payment_txid or "",
            }
        ),
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

    result = require_paid_request(
        request,
        price=settings.x402_grading_score_price,
        resource="x402-grading-score",
        description=(
            "Read the aggregate grade for one x402 endpoint: the credibility-weighted "
            "mean, the plain unweighted mean, the grader count, the full 1-5 distribution, "
            "and every grader's score, opinion and weight. Each grade is weighted by how "
            "much that wallet has paid this marketplace in total, so a wallet with a long "
            "spending record counts for more than a fresh one."
        ),
        # GET takes its input in the query string, not a JSON body, so no
        # body_type here -- the package's default query-params declaration is
        # the right shape (see describe_json_endpoint's docstring).
        extensions=describe_json_endpoint(
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
    )
    if result.error:
        return result.error

    aggregate = grading_service.aggregate(endpoint)
    return Response(
        status_code=200,
        headers={"Content-Type": "application/json", **result.settlement_headers},
        description=serialization.dumps(
            {**_aggregate_json(aggregate), "settlement_tx_id": result.payment_txid or ""}
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


def register_x402_grading_routes(app: Router) -> None:
    """Register the paid grade-submission and weighted-score routes and the free index."""
    app.post("/api/v1/x402/grades")(x402_grade_submit)
    app.get("/api/v1/x402/grades")(x402_grade_index)
    app.get("/api/v1/x402/grades/score")(x402_grade_score)
