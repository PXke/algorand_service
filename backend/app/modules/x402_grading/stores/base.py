"""Storage interface for endpoint grades.

The settlement ledger has its own SettlementStore Protocol in
modules/x402/settlement.py and is not re-declared here -- this module reads it
through services/credibility.py, which owns that read.
"""

from __future__ import annotations

from typing import Protocol

from app.modules.x402_grading.models.domain import GradedEndpoint, StoredGrade


class GradeStore(Protocol):
    """Storage interface for x402 endpoint grades."""

    def upsert(self, item: StoredGrade) -> None:
        """Create or replace one grader's grade of one URL, index projection included.

        Create-or-replace on (url_hash, grader) is the product rule, not an
        implementation detail: one grader holds at most one grade per URL, and
        re-grading moves that grade rather than stacking a second one. See
        GradingService.submit for why stacking would corrupt the aggregate.

        The graded URL travels on the item itself, so no backend has to be told
        it separately or look it up anywhere.
        """
        ...

    def get(self, url_hash: str, grader: str) -> StoredGrade | None:
        """Return this grader's grade of this URL, or None if they have not graded it."""
        ...

    def list_for_url(self, url_hash: str, *, limit: int) -> list[StoredGrade]:
        """Return grades of one URL, at most `limit` of them.

        Ordered by grader within the URL's partition, which is the storage
        order rather than a product-meaningful one -- the service sorts what it
        serves. The limit is always bound and always clamped by the caller: no
        unbounded listings (CLAUDE.md section 4).
        """
        ...

    def get_graded_endpoint(self, url_hash: str) -> GradedEndpoint | None:
        """Return the index entry for one URL, or None if nobody has graded it.

        A single-row read, used to answer "is there anything here to buy"
        BEFORE the paid score lookup takes payment. It leaks exactly what the
        free index already gives away, so answering it for free costs nothing
        and saves a caller from paying for an empty aggregate.
        """
        ...

    def list_graded_endpoints(self, *, limit: int) -> list[GradedEndpoint]:
        """Return URLs that have at least one grade, at most `limit` of them."""
        ...
