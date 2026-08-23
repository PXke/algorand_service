"""Chain pulse: per-block txn counts from header `tc` deltas."""

from __future__ import annotations

import pytest

from app.modules.metrics.services import network_service


def test_chain_pulse_counts_txns_per_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """Twenty bars; each bar is the tc delta from the previous header."""
    last = 1_000

    def header(rnd: int, *, timeout: float = 8.0) -> dict[str, int | None] | None:
        offset = rnd - (last - network_service._PULSE_BARS)
        return {"round": rnd, "tc": 1_000 + offset * 10, "ts": 1_700_000_000 + rnd}

    monkeypatch.setattr(
        network_service,
        "_fetch_algod_status_uncached",
        lambda **_k: {"last-round": last},
    )
    monkeypatch.setattr(network_service, "_block_header", header)
    monkeypatch.setattr(
        network_service, "cached_json", lambda _key, _ttl, compute: compute()
    )
    monkeypatch.setattr(network_service, "fetch_block_composition", lambda *_a, **_k: None)
    network_service.reset_chain_pulse_ring()

    pulse = network_service.fetch_chain_pulse()
    assert pulse["last_round"] == last
    assert len(pulse["blocks"]) == 20
    assert pulse["blocks"][0]["txns"] == 10
    assert pulse["blocks"][-1]["txns"] == 10
    assert pulse["txns_last_minute"] == 200
    assert pulse["blocks"][0]["kinds"] == []
    assert pulse["blocks"][0]["inners"] == 0


def test_chain_pulse_empty_when_algod_down(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(**_k):
        raise RuntimeError("algod down")

    monkeypatch.setattr(network_service, "_fetch_algod_status_uncached", boom)
    monkeypatch.setattr(
        network_service, "cached_json", lambda _key, _ttl, compute: compute()
    )
    monkeypatch.setattr(network_service, "fetch_block_composition", lambda *_a, **_k: None)
    network_service.reset_chain_pulse_ring()
    pulse = network_service.fetch_chain_pulse()
    assert pulse["last_round"] == 0
    assert pulse["blocks"] == []


def test_summarize_block_groups_types_and_counts_inners() -> None:
    """Payset types collapse into editorial buckets; inners are a footnote."""
    block = {
        "ts": 1_700_000_000,
        "txns": [
            {"txn": {"type": "pay"}},
            {"txn": {"type": "pay"}},
            {
                "txn": {"type": "appl"},
                "dt": {"itx": [{"txn": {"type": "appl"}}, {"txn": {"type": "axfer"}}]},
            },
            {"txn": {"type": "axfer"}},
            {"txn": {"type": "acfg"}},
        ],
    }
    mix = network_service.summarize_block(99, block)
    assert mix["round"] == 99
    assert mix["txns"] == 5
    assert mix["inners"] == 2
    by_id = {k["id"]: k["count"] for k in mix["kinds"]}
    assert by_id == {"pay": 2, "axfer": 2, "appl": 1}


def test_chain_pulse_attaches_kinds_from_composition(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each bar carries the pay/asset/app mix so the front page need not click."""
    last = 1_000

    def header(rnd: int, *, timeout: float = 8.0) -> dict[str, int | None] | None:
        offset = rnd - (last - network_service._PULSE_BARS)
        return {"round": rnd, "tc": 1_000 + offset * 10, "ts": 1_700_000_000 + rnd}

    def mix(rnd: int, *, timeout: float = 12.0) -> dict:
        return {
            "round": rnd,
            "txns": 10,
            "inners": 2,
            "kinds": [{"id": "pay", "count": 7}, {"id": "appl", "count": 3}],
        }

    monkeypatch.setattr(
        network_service,
        "_fetch_algod_status_uncached",
        lambda **_k: {"last-round": last},
    )
    monkeypatch.setattr(network_service, "_block_header", header)
    monkeypatch.setattr(
        network_service, "cached_json", lambda _key, _ttl, compute: compute()
    )
    monkeypatch.setattr(network_service, "fetch_block_composition", mix)
    network_service.reset_chain_pulse_ring()

    pulse = network_service.fetch_chain_pulse()
    bar = pulse["blocks"][-1]
    assert bar["kinds"] == [{"id": "pay", "count": 7}, {"id": "appl", "count": 3}]
    assert bar["inners"] == 2
    assert bar["txns"] == 10


def test_chain_pulse_reuses_composition_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Steady-state polls only fetch txn mix for rounds not yet in the window cache."""
    last = 1_000
    fetched: list[int] = []

    def header(rnd: int, *, timeout: float = 8.0) -> dict[str, int | None] | None:
        offset = rnd - (last - network_service._PULSE_BARS)
        return {"round": rnd, "tc": 1_000 + offset * 10, "ts": 1_700_000_000 + rnd}

    def mix(rnd: int, *, timeout: float = 12.0) -> dict:
        fetched.append(rnd)
        return {
            "round": rnd,
            "txns": 10,
            "inners": 1,
            "kinds": [{"id": "pay", "count": 7}],
        }

    monkeypatch.setattr(
        network_service,
        "_fetch_algod_status_uncached",
        lambda **_k: {"last-round": last},
    )
    monkeypatch.setattr(network_service, "_block_header", header)
    monkeypatch.setattr(
        network_service, "cached_json", lambda _key, _ttl, compute: compute()
    )
    monkeypatch.setattr(network_service, "fetch_block_composition", mix)
    network_service.reset_chain_pulse_ring()

    pulse = network_service.fetch_chain_pulse()
    assert len(fetched) == network_service._PULSE_BARS
    assert pulse["blocks"][-1]["kinds"] == [{"id": "pay", "count": 7}]

    fetched.clear()
    again = network_service.fetch_chain_pulse()
    assert fetched == []
    assert again["blocks"][-1]["kinds"] == [{"id": "pay", "count": 7}]


def test_fetch_block_composition_none_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(network_service, "_fetch_block_json", lambda *_a, **_k: None)
    monkeypatch.setattr(
        network_service, "cached_json", lambda _key, _ttl, compute: compute()
    )
    assert network_service.fetch_block_composition(1) is None
