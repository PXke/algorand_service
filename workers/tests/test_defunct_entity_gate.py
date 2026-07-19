"""The defunct-entity gate holds a draft for review when it links a domain that
does not resolve to a usable address — actively DNS-checked at gate time, so it
fires whether the writer fetched the domain in research (the MyAlgo incident,
2026-07-19) or recommended it blind from memory. It must NOT fire on prose-only
mentions, on live links, or on a transient resolver hiccup."""

from __future__ import annotations

import socket

import pytest

from app.modules.newspaper import defunct_entity_gate as gate


@pytest.fixture
def _dead_hosts(monkeypatch):
    """Make only the named hosts read as unreachable; everything else resolves.
    Patches the injectable _resolves seam. Returns a mutable set."""
    dead: set[str] = set()

    def fake_resolves(host: str) -> bool:
        return host not in dead

    monkeypatch.setattr(gate, "_resolves", fake_resolves)
    return dead


def test_fires_on_linked_dead_domain(_dead_hosts):
    _dead_hosts.add("wallet.myalgo.com")
    body = "Supported wallets: [Pera](https://perawallet.app) and [MyAlgo](https://wallet.myalgo.com)."
    assert gate.defunct_linked_domains(body) == ["wallet.myalgo.com"]


def test_fires_without_any_trace_signal(_dead_hosts):
    # The stale-memory case this enhancement targets: the writer linked a dead
    # domain it never fetched, so there is NO trace DNS-failure — the active
    # lookup alone must still catch it.
    _dead_hosts.add("deadproject.xyz")
    body = "Try [DeadProject](https://deadproject.xyz) for staking."
    assert gate.defunct_linked_domains(body) == ["deadproject.xyz"]


def test_no_fire_when_all_links_resolve(_dead_hosts):
    body = "See [Pera](https://perawallet.app) and [Defly](https://defly.app)."
    assert gate.defunct_linked_domains(body) == []


def test_no_fire_on_prose_only_mention(_dead_hosts):
    # A wallet round-up that mentions MyAlgo but links only a LIVE source about
    # its shutdown — the dead domain is never linked, so no hold.
    _dead_hosts.add("wallet.myalgo.com")
    body = (
        "MyAlgo Wallet was sunset in 2024; see "
        "[Ledger's notice](https://support.ledger.com/article/myalgo)."
    )
    assert gate.defunct_linked_domains(body) == []


def test_dedupes_repeated_dead_host(_dead_hosts):
    _dead_hosts.add("wallet.myalgo.com")
    body = "[a](https://wallet.myalgo.com/x) then [b](https://wallet.myalgo.com/y)."
    assert gate.defunct_linked_domains(body) == ["wallet.myalgo.com"]


def test_flag_sets_payload_signal_and_reason(monkeypatch, _dead_hosts):
    monkeypatch.setattr("app.core.config.DEFUNCT_ENTITY_GATE_ENABLED", True, raising=False)
    _dead_hosts.add("wallet.myalgo.com")
    payload = {"body": "Use [MyAlgo](https://wallet.myalgo.com)."}
    # A trace DNS-failure for the same entity should be noted in the reason.
    trace = [{"role": "tool", "name": "fetch_url",
              "content": '{"url": "https://wallet.myalgo.com/", "error": "dns resolution failed for wallet.myalgo.com"}'}]
    out = gate.flag_defunct_entities(payload, trace)

    assert out["_defunct_domains"] == ["wallet.myalgo.com"]
    assert "wallet.myalgo.com" in out["_hold_reason"]
    assert "research already flagged" in out["_hold_reason"]


def test_flag_noop_when_disabled(monkeypatch, _dead_hosts):
    monkeypatch.setattr("app.core.config.DEFUNCT_ENTITY_GATE_ENABLED", False, raising=False)
    _dead_hosts.add("wallet.myalgo.com")
    payload = {"body": "Use [MyAlgo](https://wallet.myalgo.com)."}
    assert "_defunct_domains" not in gate.flag_defunct_entities(payload, None)


# --- the real resolver's errno logic (no mock of _resolves) -------------------

def _raise_gaierror(errno):
    def _boom(*a, **k):
        raise socket.gaierror(errno, "test")
    return _boom


def test_resolve_blocking_dead_on_no_address(monkeypatch):
    # EAI_NODATA is the real MyAlgo case: name exists, no A/AAAA record.
    monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror(socket.EAI_NODATA))
    assert gate._resolve_blocking("wallet.myalgo.com") is False


def test_resolve_blocking_dead_on_unknown_name(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror(socket.EAI_NONAME))
    assert gate._resolve_blocking("nope.invalid") is False


def test_resolve_blocking_alive_on_transient_failure(monkeypatch):
    # EAI_AGAIN is a transient resolver failure — must read as ALIVE so a blip
    # never holds a good article.
    monkeypatch.setattr(socket, "getaddrinfo", _raise_gaierror(socket.EAI_AGAIN))
    assert gate._resolve_blocking("perawallet.app") is True


def test_resolve_blocking_alive_when_resolves(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("1.2.3.4", 0))])
    assert gate._resolve_blocking("perawallet.app") is True
