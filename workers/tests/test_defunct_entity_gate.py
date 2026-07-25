"""The defunct-entity gate holds a draft for review when it links a domain that does not resolve to a usable address — actively DNS-checked at gate time, so it fires whether the writer fetched the domain in research (the MyAlgo incident, 2026-07-19) or recommended it blind from memory. It must NOT fire on prose-only mentions, on live links, or on a transient resolver hiccup."""

from __future__ import annotations

import socket
from collections.abc import Callable
from typing import Never

import pytest

from app.modules.newspaper import defunct_entity_gate as gate


@pytest.fixture
def dead_hosts(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Make only the named hosts read as unreachable; everything else resolves.

    Patches the injectable _resolves seam. Returns a mutable set.
    """
    dead: set[str] = set()

    def fake_resolves(host: str) -> bool:
        return host not in dead

    monkeypatch.setattr(gate, "_resolves", fake_resolves)
    return dead


def test_fires_on_linked_dead_domain(dead_hosts: set[str]) -> None:
    """Flags a linked domain that fails to resolve DNS."""
    dead_hosts.add("wallet.myalgo.com")
    body = (
        "Supported wallets: [Pera](https://perawallet.app) and [MyAlgo](https://wallet.myalgo.com)."
    )
    assert gate.defunct_linked_domains(body) == ["wallet.myalgo.com"]


def test_fires_without_any_trace_signal(dead_hosts: set[str]) -> None:
    """Fires the active DNS lookup even with no trace fetch-failure signal for the domain."""
    # The stale-memory case this enhancement targets: the writer linked a dead
    # domain it never fetched, so there is NO trace DNS-failure — the active
    # lookup alone must still catch it.
    dead_hosts.add("deadproject.xyz")
    body = "Try [DeadProject](https://deadproject.xyz) for staking."
    assert gate.defunct_linked_domains(body) == ["deadproject.xyz"]


def test_no_fire_when_all_links_resolve(dead_hosts: set[str]) -> None:  # noqa: ARG001 -- name must match the real callee's keyword arg
    """Does not flag any domain when every linked domain resolves."""
    body = "See [Pera](https://perawallet.app) and [Defly](https://defly.app)."
    assert gate.defunct_linked_domains(body) == []


def test_no_fire_on_prose_only_mention(dead_hosts: set[str]) -> None:
    """Does not flag a dead entity mentioned only in prose, with no dead link present."""
    # A wallet round-up that mentions MyAlgo but links only a LIVE source about
    # its shutdown — the dead domain is never linked, so no hold.
    dead_hosts.add("wallet.myalgo.com")
    body = (
        "MyAlgo Wallet was sunset in 2024; see "
        "[Ledger's notice](https://support.ledger.com/article/myalgo)."
    )
    assert gate.defunct_linked_domains(body) == []


def test_dedupes_repeated_dead_host(dead_hosts: set[str]) -> None:
    """Reports a repeated dead host only once, even with multiple links to it."""
    dead_hosts.add("wallet.myalgo.com")
    body = "[a](https://wallet.myalgo.com/x) then [b](https://wallet.myalgo.com/y)."
    assert gate.defunct_linked_domains(body) == ["wallet.myalgo.com"]


def test_flag_sets_payload_signal_and_reason(
    monkeypatch: pytest.MonkeyPatch, dead_hosts: set[str]
) -> None:
    """Sets the defunct-domains signal and a hold reason noting the matching trace DNS failure."""
    monkeypatch.setattr("app.core.config.DEFUNCT_ENTITY_GATE_ENABLED", True, raising=False)
    dead_hosts.add("wallet.myalgo.com")
    payload = {"body": "Use [MyAlgo](https://wallet.myalgo.com)."}
    # A trace DNS-failure for the same entity should be noted in the reason.
    trace = [
        {
            "role": "tool",
            "name": "fetch_url",
            "content": '{"url": "https://wallet.myalgo.com/", "error": "dns resolution failed for wallet.myalgo.com"}',
        }
    ]
    out = gate.flag_defunct_entities(payload, trace)

    assert out["_defunct_domains"] == ["wallet.myalgo.com"]
    assert "wallet.myalgo.com" in out["_hold_reason"]
    assert "research already flagged" in out["_hold_reason"]


def test_flag_noop_when_disabled(monkeypatch: pytest.MonkeyPatch, dead_hosts: set[str]) -> None:
    """Adds no defunct-domains signal when the gate is disabled via config."""
    monkeypatch.setattr("app.core.config.DEFUNCT_ENTITY_GATE_ENABLED", False, raising=False)
    dead_hosts.add("wallet.myalgo.com")
    payload = {"body": "Use [MyAlgo](https://wallet.myalgo.com)."}
    assert "_defunct_domains" not in gate.flag_defunct_entities(payload, None)


# --- the real resolver's errno logic (no mock of _resolves) -------------------


def _raise_gaierror(errno: int) -> Callable[..., Never]:
    def _boom(*_a: object, **_k: object) -> Never:
        raise socket.gaierror(errno, "test")

    return _boom


def test_resolve_blocking_dead_on_no_address(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reads a name with no A/AAAA record (EAI_NODATA) as dead."""
    # EAI_NODATA is the real MyAlgo case: name exists, no A/AAAA record.
    monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror(socket.EAI_NODATA))
    assert gate._resolve_blocking("wallet.myalgo.com") is False


def test_resolve_blocking_dead_on_unknown_name(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reads an unknown-name failure (EAI_NONAME) as dead."""
    monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror(socket.EAI_NONAME))
    assert gate._resolve_blocking("nope.invalid") is False


def test_resolve_blocking_alive_on_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reads a transient resolver failure (EAI_AGAIN) as alive, not dead."""
    # EAI_AGAIN is a transient resolver failure — must read as ALIVE so a blip
    # never holds a good article.
    monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror(socket.EAI_AGAIN))
    assert gate._resolve_blocking("perawallet.app") is True


def test_resolve_blocking_alive_when_resolves(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reads a host as alive when getaddrinfo returns a usable address."""
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: [(2, 1, 6, "", ("1.2.3.4", 0))])
    assert gate._resolve_blocking("perawallet.app") is True
