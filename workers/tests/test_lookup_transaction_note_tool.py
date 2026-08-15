"""lookup_transaction_note: the note field of one specific transaction, decoded when it's UTF-8 text -- no existing tool surfaces this (list tools summarize many transactions and omit note, mostly empty, to avoid bloat)."""

from __future__ import annotations

import base64

import pytest

from app.modules.ai import chain_tools


def test_requires_txid() -> None:
    """An empty txid is a usage error, not an indexer call."""
    result = chain_tools._tool_lookup_transaction_note("")
    assert "error" in result


def test_transaction_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 from the indexer is reported as not found, not a raw indexer error."""
    monkeypatch.setattr(chain_tools, "_mainnet_idx_get", lambda _path, params=None, **_kwargs: {"_status": 404})  # noqa: ARG005
    result = chain_tools._tool_lookup_transaction_note("AAAA")
    assert result["error"] == "transaction not found"


def test_no_note_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """Most transactions carry no note at all -- reported plainly, not as an error."""
    monkeypatch.setattr(
        chain_tools,
        "_mainnet_idx_get",
        lambda _path, params=None, **_kwargs: {"transaction": {"confirmed-round": 42, "round-time": 1_700_000_000}},  # noqa: ARG005
    )
    result = chain_tools._tool_lookup_transaction_note("AAAA")
    assert result["has_note"] is False
    assert result["note"] is None


def test_utf8_note_is_decoded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A real memo (UTF-8 text) is decoded and returned directly, not left as base64 for the model to guess at."""
    note_b64 = base64.b64encode(b"payment for invoice #4021").decode()
    monkeypatch.setattr(
        chain_tools,
        "_mainnet_idx_get",
        lambda _path, params=None, **_kwargs: {"transaction": {"note": note_b64, "confirmed-round": 42}},  # noqa: ARG005
    )
    result = chain_tools._tool_lookup_transaction_note("AAAA")
    assert result["has_note"] is True
    assert result["is_utf8_text"] is True
    assert result["note"] == "payment for invoice #4021"
    assert result["note_base64"] is None


def test_non_utf8_note_stays_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    """A binary/non-text note is never mistaken for a real memo -- it stays base64, flagged is_utf8_text=false."""
    note_b64 = base64.b64encode(b"\xff\xfe\x00\x01").decode()
    monkeypatch.setattr(
        chain_tools,
        "_mainnet_idx_get",
        lambda _path, params=None, **_kwargs: {"transaction": {"note": note_b64, "confirmed-round": 42}},  # noqa: ARG005
    )
    result = chain_tools._tool_lookup_transaction_note("AAAA")
    assert result["is_utf8_text"] is False
    assert result["note"] is None
    assert result["note_base64"] == note_b64


def test_transaction_note_tool_registered() -> None:
    """Registers lookup_transaction_note in both the tool schemas and handlers."""
    schemas, handlers = chain_tools.chain_tools()
    names = {s["function"]["name"] for s in schemas}
    assert "lookup_transaction_note" in names
    assert "lookup_transaction_note" in handlers
