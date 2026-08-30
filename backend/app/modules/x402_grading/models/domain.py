"""Domain types for x402 endpoint grades.

A grade is about an arbitrary URL the grader names. Nothing here refers to a
directory listing: an endpoint does not have to be listed in
modules/x402_directory (or anywhere else) to be graded, so this module has no
dependency on that one at all.

The settlement ledger is shared infrastructure and lives in
modules/x402/settlement.py. This module only READS it, and only to ask how much
a wallet has spent with this marketplace in total -- the credibility weight in
services/credibility.py. It never asks, and cannot answer, whether a grader
paid the endpoint they are grading: that payment goes to a third party's payTo
through a third party's facilitator and never traverses our gate, so it is not
in our ledger by construction.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.errors import PlatformError, http_status_for_code

# Constant partition key for x402_graded_endpoints. See migration 093 for why
# the whole index lives in one partition and when to shard it.
GRADING_PARTITION = "default"

# A 1-5 star scale. Deliberately the boring choice: it is the scale every agent
# author and every LLM already has a prior for, it needs no legend in the 402
# offer, and it survives being averaged in a way a free-form 0-100 does not
# (nobody agrees what 73 means, everybody agrees what 4 out of 5 means).
MIN_SCORE = 1
MAX_SCORE = 5

# A grade's optional opinion. Capped at the same 280 characters as a board
# pitch: this is a one-line justification served alongside a score, not a
# review essay, and an unbounded text column on a paid write is an abuse
# surface (CLAUDE.md section 4: bound request bodies).
MAX_COMMENT_LENGTH = 280

# Longest URL this module will grade, matched to the request schema's cap and
# to the other x402 modules' URL caps.
MAX_URL_LENGTH = 2048


class GradingError(PlatformError):
    """A grading-flow error mapped to an HTTP status."""

    def __init__(self, code: str, message: str) -> None:
        """Map a grading error code to its HTTP status via http_status_for_code."""
        super().__init__(code, message, http_status=http_status_for_code(code))


@dataclass
class StoredGrade:
    """One wallet's grade of one endpoint URL, as stored and as served.

    `url_hash` is this module's own hex SHA-256 of its own normalized URL (see
    services/url_key.py), not an identifier borrowed from another module. Any
    http(s) URL can be graded, listed with us or not.

    `grader` is the paying wallet from the settled payment, never anything the
    request body claimed. It is half the row's key, which is what makes
    re-grading an overwrite rather than a second vote.

    `settlement_tx_id` is the txid of the payment that bought THIS grade -- the
    audit trail from the stored opinion back to the payment that funded it.
    """

    url_hash: str
    url: str
    grader: str
    score: int
    comment: str
    settlement_tx_id: str
    created_at_epoch: int


@dataclass
class GradedEndpoint:
    """One entry in the free "which URLs have been graded" index.

    Carries no score by design -- free is existence, paid is signal, the same
    split the feature board draws between its free browse and its paid demand
    read.
    """

    url_hash: str
    url: str
    last_graded_at_epoch: int


@dataclass
class WeightedGrade:
    """One grade as it is served inside an aggregate, with the weight it carried.

    `weight` is the grader's credibility weight in atomic units of the payment
    asset (see services/credibility.py). It is served rather than hidden so a
    buyer can see how the weighted mean was arrived at and re-derive it -- a
    reputation number nobody can check is a number nobody should pay for.
    """

    grade: StoredGrade
    weight: int


@dataclass
class GradeAggregate:
    """The paid score lookup's answer for one URL.

    Carries BOTH means on purpose. `weighted_mean` is the product -- grades
    weighted by how much each grader has spent with this marketplace -- and
    `mean` is the raw one-wallet-one-vote average the weighting was applied to.
    Serving only the weighted number would hide the underlying signal behind an
    opinionated transform the buyer cannot undo.

    `distribution` is score -> number of graders (unweighted), always with
    every key from MIN_SCORE to MAX_SCORE present: a zero is a real answer
    ("nobody gave this a 1") and an absent key would read as "unknown",
    CLAUDE.md section 2 invariant 8 in spirit.

    `weights_resolved` is False when the settlement ledger could not be read,
    in which case every grade fell back to the base weight and `weighted_mean`
    equals `mean`. A reader who paid for a weighted number is told when the
    weighting did not actually run, rather than being handed an unweighted
    number wearing a weighted label.

    `truncated` says the grade scan hit its bound and the aggregate is over the
    first N grades rather than all of them.
    """

    url_hash: str
    url: str
    count: int
    mean: float
    weighted_mean: float
    total_weight: int
    distribution: dict[int, int] = field(default_factory=dict)
    grades: list[WeightedGrade] = field(default_factory=list)
    weights_resolved: bool = True
    truncated: bool = False
