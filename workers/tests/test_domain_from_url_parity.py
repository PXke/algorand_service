"""Parity guard for domain_from_url (eTLD+1 keying).

The same registrable-domain logic is duplicated in two SEPARATE services that
can't share a package:
  - workers: app.modules.crawler.domain_tracker.domain_from_url
  - backend: app.modules.admin.stores.cassandra.AdminCassandraStore._domain_from_url

If they disagree, the admin accepts/keys a domain under one name while the
frontier crawls/keys it under another → split rows, "approved" domains that never
get crawled. These vectors are kept BYTE-IDENTICAL with
backend/tests/test_domain_from_url_parity.py; edit both together.
"""

from __future__ import annotations

import pytest

from app.modules.crawler.domain_tracker import domain_from_url

# (input url, expected registrable domain) — mirror in the backend test.
VECTORS = [
    ("https://www.algorand.co/x", "algorand.co"),
    ("https://xgov.algorand.co", "algorand.co"),
    ("https://docs.perawallet.app", "perawallet.app"),
    ("https://a.b.example.com", "example.com"),
    ("https://example.com", "example.com"),
    # Multi-label public suffix keeps the registrable label.
    ("https://blog.example.co.uk", "example.co.uk"),
    # Japan's non-co.jp category suffixes need the same treatment (2026-07-21:
    # a JVCEA citation collapsed to the meaningless "or.jp" registry category).
    ("https://jvcea.or.jp/x", "jvcea.or.jp"),
    # Platform suffixes keep the publisher subdomain (distinct sources).
    ("https://foo.medium.com", "foo.medium.com"),
    ("https://medium.com", "medium.com"),
    ("https://sub.notion.site/p", "sub.notion.site"),
    ("not-a-url", ""),
    ("", ""),
]


@pytest.mark.parametrize(("url", "expected"), VECTORS)
def test_domain_from_url(url: str, expected: str) -> None:
    """domain_from_url resolves each vector URL to its expected registrable domain."""
    assert domain_from_url(url) == expected
