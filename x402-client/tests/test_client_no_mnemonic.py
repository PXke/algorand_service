"""Calling a paid method without a mnemonic (and without an injected http_client) must fail clearly and immediately.

Not with a confusing low-level error, and not after an HTTP round-trip that
could never have been paid for.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from pxke_x402 import PxkeClient, PxkeConfigError

from .conftest import FakeResponse, FakeSession

_PAID_CALLS = [
    lambda c: c.search_news("tinyman"),
    lambda c: c.scan_url("https://example.com/file.zip"),
    lambda c: c.storage_create_backup(b"hello"),
    lambda c: c.storage_renew_backup("b1", "WALLETADDR"),
]


@pytest.mark.parametrize("make_call", _PAID_CALLS)
def test_every_paid_method_raises_pxke_config_error_without_a_mnemonic(
    make_call: Callable[[PxkeClient], Any],
) -> None:
    session = FakeSession([])  # no responses scripted: a real request would blow up loudly
    client = PxkeClient(session=session)  # no mnemonic, no http_client

    with pytest.raises(PxkeConfigError) as excinfo:
        make_call(client)

    assert "mnemonic" in str(excinfo.value)
    assert session.calls == []  # fails before any network request, not after


def test_the_error_message_names_the_free_methods_still_available() -> None:
    client = PxkeClient()

    with pytest.raises(PxkeConfigError) as excinfo:
        client.scan_url("https://example.com/file.zip")

    assert "catalog()" in str(excinfo.value)


def test_address_is_none_without_a_mnemonic() -> None:
    client = PxkeClient()

    assert client.address is None


def test_free_methods_still_work_on_a_client_with_no_mnemonic() -> None:
    session = FakeSession([FakeResponse(200, {"name": "PXke x402 marketplace"})])
    client = PxkeClient(session=session)

    assert client.catalog() == {"name": "PXke x402 marketplace"}


def test_read_article_needs_no_mnemonic() -> None:
    """The article read is free -- no payment gate, so no wallet is required."""
    session = FakeSession([FakeResponse(200, {"article_id": "a", "title": "t"})])
    client = PxkeClient(session=session)

    assert client.read_article("a") == {"article_id": "a", "title": "t"}
