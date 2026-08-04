"""compose_article_edit_mistral: the in-place article-edit path must record to compose_sessions and run the same deterministic post-compose gates as the create path.

Root-caused 2026-08-04 (Humanitarian Network special edition refresh): this
path called chat_with_tools without ever passing trace/debug, so no compose
session was ever recorded for ANY in-place article edit, and never called
_apply_post_compose_gates, so none of the deterministic gates (stale
deadline, link, chain-entity, authority, unsourced-specifics) applied
either -- silently, on every editorial-brief refresh after its first
compose (special editions included).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from app.modules.ai import mistral_compose as mc


class _FakeClient:
    def __init__(self, raw_payload: dict[str, Any]) -> None:
        self._raw = json.dumps(raw_payload)
        self.chat_with_tools_calls: list[dict[str, Any]] = []

    def chat_with_tools(self, messages: list[dict[str, Any]], **kwargs: object) -> str:
        self.chat_with_tools_calls.append({"messages": messages, **kwargs})
        return self._raw

    def usage_totals(self) -> dict[str, int]:
        return {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30}


@contextmanager
def _noop_lock(label: str = "") -> Iterator[None]:  # noqa: ARG001 -- test double for compose_lock
    yield


def _patch_collaborators(monkeypatch: pytest.MonkeyPatch, recorded: list[dict[str, Any]]) -> None:
    monkeypatch.setattr(
        "app.modules.ai.writer_tools.all_tools", lambda **_kw: ([], {}), raising=False
    )
    monkeypatch.setattr(
        "app.modules.newspaper.compose_lock.compose_lock", _noop_lock, raising=False
    )

    def _fake_record(**kwargs: object) -> bool:
        recorded.append(kwargs)
        return True

    monkeypatch.setattr(
        "app.modules.ai.tool_insights_store.record_compose_session", _fake_record
    )
    monkeypatch.setattr("app.core.config.WRITER_TOOLS_ENABLED", True, raising=False)


def _call(client: _FakeClient) -> mc.MistralArticleFields:
    return mc.compose_article_edit_mistral(
        service_name="Vestige.fi",
        source_url="https://vestige.fi",
        existing_title="Old title",
        existing_summary="Old summary",
        existing_body="Old body",
        new_page_title="New signal",
        new_page_text="New reporting text",
        diff="- old line\n+ new line",
        client=client,
    )


def test_records_a_compose_session_with_researching_then_ok_checkpoints(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A checkpoint fires before the call (researching) and a final one after (ok), both keyed to the same source_url -- previously this path recorded nothing at all."""
    recorded: list[dict[str, Any]] = []
    _patch_collaborators(monkeypatch, recorded)
    client = _FakeClient({"title": "New title", "summary": "New summary", "body": "New body"})

    _call(client)

    statuses = [r["status"] for r in recorded]
    assert "researching" in statuses
    assert "ok" in statuses
    assert all(r["service_id"] == "https://vestige.fi" for r in recorded)
    assert all(r["source_url"] == "https://vestige.fi" for r in recorded)


def test_chat_with_tools_receives_trace_and_debug(monkeypatch: pytest.MonkeyPatch) -> None:
    """The tool-calling round is threaded with a real trace/debug this time -- the root cause of the missing session (previously called with neither)."""
    recorded: list[dict[str, Any]] = []
    _patch_collaborators(monkeypatch, recorded)
    client = _FakeClient({"title": "New title", "summary": "New summary", "body": "New body"})

    _call(client)

    assert len(client.chat_with_tools_calls) == 1
    call = client.chat_with_tools_calls[0]
    assert isinstance(call.get("trace"), list)
    assert isinstance(call.get("debug"), dict)


def test_post_compose_gates_run_on_the_edited_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stale-deadline sentence in the edited body is caught by the same post-compose gate pipeline the create path gets -- verified via the recorded final_output, since the gate's flag doesn't survive into the narrower MistralArticleFields return type."""
    recorded: list[dict[str, Any]] = []
    _patch_collaborators(monkeypatch, recorded)
    stale_body = (
        "## Updated\n\nHolders have until 4:00pm (AEST) on June 29, 2026, to withdraw "
        "their tokens to an external wallet."
    )
    client = _FakeClient({"title": "New title", "summary": "New summary", "body": stale_body})

    _call(client)

    final = next(r for r in recorded if r["status"] == "ok")
    assert "_stale_deadlines" in final["final_output"]


def test_credit_error_checkpoints_before_reraising(monkeypatch: pytest.MonkeyPatch) -> None:
    """A credit-exhaustion failure still leaves a terminal checkpoint (not stuck at 'researching' forever in the admin Sessions view) before propagating."""
    from app.modules.ai.mistral_client import MistralCreditError

    recorded: list[dict[str, Any]] = []
    _patch_collaborators(monkeypatch, recorded)

    class _BoomClient(_FakeClient):
        def chat_with_tools(self, _messages: list[dict[str, Any]], **_kwargs: object) -> str:
            raise MistralCreditError("no credit")

    with pytest.raises(MistralCreditError):
        _call(_BoomClient({}))

    assert any(r["status"] == "credit_insufficient" for r in recorded)
