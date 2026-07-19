"""The defunct-entity gate holds a draft for review when it links a domain the
research trace recorded as DNS-unresolvable and that still does not resolve —
the MyAlgo incident (2026-07-19). It must NOT fire on prose-only mentions, on
links the research fetched successfully, or on domains that have since recovered."""

from __future__ import annotations

import pytest

from app.modules.newspaper import defunct_entity_gate as gate


@pytest.fixture
def _dead_hosts(monkeypatch):
    """Make only the named hosts fail DNS at gate-time re-check; everything else
    resolves. Returns a mutable set the test can adjust."""
    dead: set[str] = set()

    def fake_resolves(host: str) -> bool:
        return host not in dead

    monkeypatch.setattr(gate, "_resolves", fake_resolves)
    return dead


# A trace shaped like the real one: a fetch_url tool result carrying the
# net_guard DNS-failure error string.
def _trace_with_dns_failure(host: str) -> list[dict]:
    return [
        {"role": "assistant", "content": "let me check the wallets"},
        {"role": "tool", "name": "fetch_url",
         "content": f'{{"url": "https://{host}/", "error": "dns resolution failed for {host}"}}'},
    ]


def test_fires_on_linked_dead_domain(_dead_hosts):
    _dead_hosts.add("wallet.myalgo.com")
    body = "Supported wallets: [Pera](https://perawallet.app) and [MyAlgo](https://wallet.myalgo.com)."
    trace = _trace_with_dns_failure("myalgo.com")  # research fetched the apex

    dead = gate.defunct_linked_domains(body, trace)

    assert dead == ["wallet.myalgo.com"]  # subdomain matched apex via registrable


def test_no_fire_when_domain_recovered(_dead_hosts):
    # Research saw a DNS failure, but the host resolves again now (transient blip)
    # — must not hold the article.
    body = "See [MyAlgo](https://wallet.myalgo.com)."
    trace = _trace_with_dns_failure("myalgo.com")

    assert gate.defunct_linked_domains(body, trace) == []


def test_no_fire_on_prose_only_mention(_dead_hosts):
    # The wallet round-up case: mentions MyAlgo and links only a LIVE source
    # about its shutdown — the dead domain is never linked, so no hold.
    _dead_hosts.add("wallet.myalgo.com")
    body = (
        "MyAlgo Wallet was sunset in 2024; see "
        "[Ledger's notice](https://support.ledger.com/article/myalgo)."
    )
    trace = _trace_with_dns_failure("myalgo.com")

    assert gate.defunct_linked_domains(body, trace) == []


def test_no_fire_without_trace_failure(_dead_hosts):
    # Domain is dead now, but the research never recorded a DNS failure for it —
    # out of scope for THIS gate (that is the link gate's delink job).
    _dead_hosts.add("wallet.myalgo.com")
    body = "See [MyAlgo](https://wallet.myalgo.com)."
    assert gate.defunct_linked_domains(body, trace=[]) == []


def test_flag_sets_payload_signal(monkeypatch, _dead_hosts):
    monkeypatch.setattr(
        "app.core.config.DEFUNCT_ENTITY_GATE_ENABLED", True, raising=False
    )
    _dead_hosts.add("wallet.myalgo.com")
    payload = {"body": "Use [MyAlgo](https://wallet.myalgo.com)."}
    out = gate.flag_defunct_entities(payload, _trace_with_dns_failure("myalgo.com"))

    assert out["_defunct_domains"] == ["wallet.myalgo.com"]
    assert "wallet.myalgo.com" in out["_hold_reason"]


def test_flag_noop_when_disabled(monkeypatch, _dead_hosts):
    monkeypatch.setattr(
        "app.core.config.DEFUNCT_ENTITY_GATE_ENABLED", False, raising=False
    )
    _dead_hosts.add("wallet.myalgo.com")
    payload = {"body": "Use [MyAlgo](https://wallet.myalgo.com)."}
    out = gate.flag_defunct_entities(payload, _trace_with_dns_failure("myalgo.com"))

    assert "_defunct_domains" not in out
