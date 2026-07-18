from __future__ import annotations

import os

import pytest

# Tests must never report to Bugsnag: app.main calls init_bugsnag at import
# time with a hardcoded default key (release_stage "prod") and attaches an
# ERROR-log handler to the root logger, so every error-path unit test shipped
# a prod event. An empty key makes init_bugsnag return before configuring
# anything; set here so it is in place before any test imports app.main.
os.environ["BUGSNAG_API_KEY"] = ""


def _install_no_network_guard() -> None:
    """Unit tests must never open real sockets — before this guard, a few tests
    were quietly dialing live Cassandra (:9042). Any connect fails with
    ConnectionRefusedError naming the target — the same failure mode as
    "service not running", so code under test that deliberately exercises a
    backend-down path behaves as before, just without a real connection
    attempt. Installed process-wide at conftest import (not a per-test fixture)
    so driver background threads — e.g. the Cassandra reconnector — stay
    blocked between tests too. A test that trips this should fake the
    store/session at its seam."""
    import errno
    import socket

    def _blocked_connect(self, address):
        raise ConnectionRefusedError(
            errno.ECONNREFUSED,
            f"unit test attempted a real network connection to {address}",
        )

    socket.socket.connect = _blocked_connect


_install_no_network_guard()

from app.modules.chain.models import IndexedTransaction
from app.modules.chain.repository import FakeChainRepository, set_chain_repository


@pytest.fixture
def fake_chain_repo() -> FakeChainRepository:
    repo = FakeChainRepository()
    set_chain_repository(repo)
    yield repo
    set_chain_repository(None)


@pytest.fixture
def sample_tx() -> IndexedTransaction:
    return IndexedTransaction(
        txid="A" * 52,
        round=42,
        intra=0,
        sender="B" * 58,
        txn_type="pay",
        txn_json="{}",
    )
