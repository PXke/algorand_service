"""Shared fixtures for the backend test suite."""

from __future__ import annotations

import os
from typing import Any, Never
from unittest.mock import MagicMock

import pytest

# Tests must never report to Bugsnag: app.main calls init_bugsnag at import
# time with a hardcoded default key (release_stage "prod") and attaches an
# ERROR-log handler to the root logger, so every error-path unit test shipped
# a prod event. An empty key makes init_bugsnag return before configuring
# anything; set here so it is in place before any test imports app.main.
os.environ["BUGSNAG_API_KEY"] = ""


def _install_no_network_guard() -> None:
    """Unit tests must never open real sockets — before this guard, a few tests were quietly dialing live Cassandra (:9042). Any connect fails with ConnectionRefusedError naming the target — the same failure mode as "service not running", so code under test that deliberately exercises a backend-down path behaves as before, just without a real connection attempt. Installed process-wide at conftest import (not a per-test fixture) so driver background threads — e.g. the Cassandra reconnector — stay blocked between tests too. A test that trips this should fake the store/session at its seam."""
    import errno
    import socket

    def _blocked_connect(_self: socket.socket, address: object) -> Never:
        raise ConnectionRefusedError(
            errno.ECONNREFUSED,
            f"unit test attempted a real network connection to {address}",
        )

    socket.socket.connect = _blocked_connect


_install_no_network_guard()


@pytest.fixture(autouse=True)
def _no_real_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never burn wall clock on time.sleep in unit tests."""
    monkeypatch.setattr("time.sleep", lambda *_a, **_kw: None)


def stmt_cql(registry: type, name: str) -> str:
    """Raw CQL for a ``_Stmt`` registry entry without triggering ``prepare_cached``."""
    return registry.__dict__[name].cql


def patch_cassandra(monkeypatch: pytest.MonkeyPatch, session: Any | None = None) -> MagicMock:
    """Patch ``get_cassandra_session`` + ``prepare_cached`` (identity) for unit tests."""
    if session is None:
        session = MagicMock()
    monkeypatch.setattr("app.core.cassandra.prepare_cached", lambda cql: cql)
    monkeypatch.setattr("app.core.cassandra.get_cassandra_session", lambda: session)
    return session


def execute_pairs(session: MagicMock) -> list[tuple[object, object]]:
    """``(stmt, params)`` for ``session.execute`` calls that passed params."""
    pairs: list[tuple[object, object]] = []
    for c in session.execute.call_args_list:
        if len(c.args) < 2:
            continue
        pairs.append((c.args[0], c.args[1]))
    return pairs


# Must import after the guard installs above (not a style choice — see its
# docstring): anything importing app.main transitively must never race ahead
# of the socket block.
from app.modules.chain.models import IndexedTransaction  # noqa: E402
from app.modules.chain.repository import FakeChainRepository, set_chain_repository  # noqa: E402


@pytest.fixture
def fake_chain_repo() -> FakeChainRepository:
    """Install and yield a fake chain repository for the duration of a test."""
    repo = FakeChainRepository()
    set_chain_repository(repo)
    yield repo
    set_chain_repository(None)


@pytest.fixture
def sample_tx() -> IndexedTransaction:
    """Build a minimal sample indexed transaction for tests."""
    return IndexedTransaction(
        txid="A" * 52,
        round=42,
        intra=0,
        sender="B" * 58,
        txn_type="pay",
        txn_json="{}",
    )
