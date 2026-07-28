"""Both ArticleStore implementations must accept the protocol's keywords.

Production runs the Cassandra store while the suite runs the in-memory one, so
a signature that drifts between them is invisible to every other test. That is
exactly how /api/v1/news/stats came to 500 in production for as long as it had
existed: the Cassandra store spelled the keyword `_feed_bucket`, and
count_feed() calls it by name.
"""

from __future__ import annotations

import inspect

from app.modules.news.stores.base import ArticleStore
from app.modules.news.stores.cassandra import CassandraArticleStore
from app.modules.news.stores.memory import InMemoryArticleStore

IMPLEMENTATIONS = (CassandraArticleStore, InMemoryArticleStore)


def _keyword_names(func: object) -> set[str]:
    return {
        name
        for name, p in inspect.signature(func).parameters.items()  # type: ignore[arg-type]
        if p.kind is inspect.Parameter.KEYWORD_ONLY
    }


def _protocol_methods() -> list[str]:
    """Every method ArticleStore declares — derived, so new ones are covered."""
    return [
        name
        for name in vars(ArticleStore)
        if not name.startswith("_") and callable(getattr(ArticleStore, name, None))
    ]


def test_store_implementations_accept_the_protocol_keywords() -> None:
    """Every ArticleStore method's keyword-only params must exist on both implementations."""
    methods = _protocol_methods()
    assert methods, "ArticleStore declares no methods — the check would be vacuous"
    for method in methods:
        expected = _keyword_names(getattr(ArticleStore, method))
        for impl in IMPLEMENTATIONS:
            actual = _keyword_names(getattr(impl, method))
            missing = expected - actual
            assert not missing, (
                f"{impl.__name__}.{method} is missing keyword(s) {sorted(missing)} "
                f"required by ArticleStore — callers passing them by name will "
                f"raise TypeError at runtime against this store only."
            )
