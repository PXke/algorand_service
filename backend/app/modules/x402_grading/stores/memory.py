"""In-memory x402 grade store for dev and tests."""

from __future__ import annotations

from app.modules.x402_grading.models.domain import GradedEndpoint, StoredGrade


class InMemoryGradeStore:
    """In-memory x402 endpoint-grade storage."""

    def __init__(self) -> None:
        """Start with no grades and an empty graded-endpoint index."""
        self._grades: dict[tuple[str, str], StoredGrade] = {}
        self._index: dict[str, GradedEndpoint] = {}

    def upsert(self, item: StoredGrade) -> None:
        """Create or replace one grader's grade of one URL, index projection included.

        last_graded_at only ever moves forward, matching the Cassandra store's
        behaviour of writing max(previous, new): a grade replayed with an older
        timestamp must not make an endpoint look staler than it is.
        """
        self._grades[(item.url_hash, item.grader)] = item
        previous = self._index.get(item.url_hash)
        self._index[item.url_hash] = GradedEndpoint(
            url_hash=item.url_hash,
            url=item.url,
            last_graded_at_epoch=max(
                item.created_at_epoch, previous.last_graded_at_epoch if previous else 0
            ),
        )

    def get(self, url_hash: str, grader: str) -> StoredGrade | None:
        """Return this grader's grade of this URL, or None if they have not graded it."""
        return self._grades.get((url_hash, grader))

    def list_for_url(self, url_hash: str, *, limit: int) -> list[StoredGrade]:
        """Return grades of one URL, ordered by grader, at most `limit` of them.

        Ordered by grader ascending to match the Cassandra table's clustering
        order, so a truncated scan truncates identically in tests and in
        production.
        """
        rows = [item for item in self._grades.values() if item.url_hash == url_hash]
        rows.sort(key=lambda item: item.grader)
        return rows[: max(0, limit)]

    def get_graded_endpoint(self, url_hash: str) -> GradedEndpoint | None:
        """Return the index entry for one URL, or None if nobody has graded it."""
        return self._index.get(url_hash)

    def list_graded_endpoints(self, *, limit: int) -> list[GradedEndpoint]:
        """Return graded endpoints ordered by url hash, at most `limit` of them."""
        ordered = sorted(self._index.values(), key=lambda entry: entry.url_hash)
        return ordered[: max(0, limit)]
