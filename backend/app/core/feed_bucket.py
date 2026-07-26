"""Month-bucketed feed partitioning + keyset pagination — re-export shim.

The implementation is shared (`algorand_shared.feed_bucket`): both deployables
read and write the `articles_feed` projection, so the bucket rule must be
byte-identical on both sides — a divergence writes rows into a partition the
reader never scans.
"""

from __future__ import annotations

from algorand_shared.feed_bucket import cursor_from_ms, feed_month, months_back, to_ms

__all__ = ["cursor_from_ms", "feed_month", "months_back", "to_ms"]
