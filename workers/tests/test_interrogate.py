"""The session-interrogation core: reviving a compose transcript, flattening its
tool-call protocol into a replayable conversation, confronting the writer with
live ground truth, and asking the same model a question. No network, no real
Cassandra, no real Mistral — the Cassandra row and the model are both faked."""

from __future__ import annotations

import json

import pytest

from app.modules.ai import interrogate as ir


# --------------------------------------------------------------------------- #
# fakes
# --------------------------------------------------------------------------- #
class _Row:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def one(self):
        return self._rows[0] if self._rows else None


class _FakeCass:
    """Returns the given rows for the partition scan (newest-first order as
    stored)."""
    def __init__(self, rows):
        self._rows = rows

    def execute(self, cql, params=None):
        return _Result(self._rows)


class _FakeClient:
    """Captures the conversation it was asked to complete and echoes a canned
    answer, so we can assert on what the transcript replay actually sent."""
    def __init__(self, answer="that figure was not in my sources."):
        self.answer = answer
        self.seen: list[dict] = []

    def chat_completion(self, messages, *, json_object=True, temperature=0.3):
        self.seen = messages
        return self.answer


def _session_row(**over):
    msgs = over.pop("messages", [
        {"role": "system", "content": "Compose an article about GoPlausible."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "search_web", "arguments": '{"q": "goplausible"}'}}]},
        {"role": "tool", "name": "search_web", "content": "GoPlausible ships MCP tooling."},
        {"role": "assistant", "content": "Draft: GoPlausible has over 1,000 issuers."},
    ])
    base = dict(
        created_at="2026-07-19 20:09:28",
        session_id="sess-1",
        service_id="goplausible-com",
        source_url="https://goplausible.com/",
        model="mistral-large-latest",
        status="ok",
        rounds=2,
        tool_calls=1,
        messages=json.dumps(msgs),
        final_output="GoPlausible has [over 1,000 issuers](https://goplausible.com/).",
    )
    base.update(over)
    return _Row(**base)


# --------------------------------------------------------------------------- #
# flattening
# --------------------------------------------------------------------------- #
def test_flatten_narrates_tool_calls_and_results():
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "fetch_url", "arguments": '{"url":"https://x"}'}}]},
        {"role": "tool", "name": "fetch_url", "content": "page body"},
        {"role": "assistant", "content": "final draft"},
    ]
    flat = ir._flatten(msgs)
    roles = [m["role"] for m in flat]
    # tool role collapses to a user turn; no bare tool-role / tool_call ids remain
    assert "tool" not in roles
    assert all("tool_call_id" not in m and "tool_calls" not in m for m in flat)
    assert any("called tool `fetch_url`" in m["content"] for m in flat)
    assert any("RESULT of tool `fetch_url`" in m["content"] for m in flat)
    assert flat[0] == {"role": "system", "content": "sys"}


def test_flatten_truncates_huge_tool_result():
    big = "A" * (ir._TOOL_RESULT_CAP + 5000)
    flat = ir._flatten([{"role": "tool", "name": "fetch_url", "content": big}])
    assert len(flat) == 1
    assert "[tool result truncated]" in flat[0]["content"]
    assert len(flat[0]["content"]) < ir._TOOL_RESULT_CAP + 200


def test_flatten_handles_list_content_blocks():
    msgs = [{"role": "assistant", "content": [{"type": "text", "text": "hello"},
                                              {"type": "thinking", "text": "ignored?"}]}]
    flat = ir._flatten(msgs)
    assert flat[0]["content"].startswith("hello")


# --------------------------------------------------------------------------- #
# revive
# --------------------------------------------------------------------------- #
def test_revive_picks_matching_source_url():
    cass = _FakeCass([_session_row()])
    rev = ir.revive_session(source_url="goplausible", _session=cass)
    assert rev.model == "mistral-large-latest"
    assert rev.rounds == 2
    assert rev.replay and rev.replay[0]["role"] == "system"


def test_revive_resolves_article_id_to_source_url(monkeypatch):
    # article-id path: _resolve_article_source_url reads the article's source_url,
    # then the scan matches the compose session on it.
    monkeypatch.setattr(ir, "_resolve_article_source_url",
                        lambda sess, aid: "https://goplausible.com/")
    cass = _FakeCass([_session_row()])
    rev = ir.revive_session(article_id="19e2cc05-c7da-43b0-86b0-46931ee37a28", _session=cass)
    assert rev.source_url == "https://goplausible.com/"


def test_revive_raises_when_no_match():
    cass = _FakeCass([_session_row()])
    with pytest.raises(LookupError):
        ir.revive_session(source_url="nonexistent-service", _session=cass)


# --------------------------------------------------------------------------- #
# ground truth
# --------------------------------------------------------------------------- #
def test_ground_truth_flags_dead_linked_domain(monkeypatch):
    monkeypatch.setattr(ir._dg, "_resolves", lambda h: h != "goplausible.com")
    rev = ir.revive_session(source_url="goplausible", _session=_FakeCass([_session_row()]))
    note = ir.ground_truth_note(rev)
    assert note and "DOES NOT RESOLVE" in note and "goplausible.com" in note


def test_ground_truth_none_when_all_live_and_no_failures(monkeypatch):
    monkeypatch.setattr(ir._dg, "_resolves", lambda h: True)
    rev = ir.revive_session(source_url="goplausible", _session=_FakeCass([_session_row()]))
    assert ir.ground_truth_note(rev) is None


def test_ground_truth_surfaces_transcript_fetch_failures(monkeypatch):
    monkeypatch.setattr(ir._dg, "_resolves", lambda h: True)
    msgs = [
        {"role": "tool", "name": "fetch_url",
         "content": '{"error": "dns resolution failed for wallet.myalgo.com"}'},
        {"role": "assistant", "content": "Use MyAlgo."},
    ]
    rev = ir.revive_session(
        source_url="x", _session=_FakeCass([_session_row(
            messages=msgs, source_url="https://x", final_output="no links here")]))
    note = ir.ground_truth_note(rev)
    assert note and "wallet.myalgo.com" in note


# --------------------------------------------------------------------------- #
# interrogate
# --------------------------------------------------------------------------- #
def test_interrogate_replays_transcript_and_appends_question(monkeypatch):
    monkeypatch.setattr(ir._dg, "_resolves", lambda h: True)
    rev = ir.revive_session(source_url="goplausible", _session=_FakeCass([_session_row()]))
    client = _FakeClient()
    answer, history = ir.interrogate(
        rev, "where did 1,000 issuers come from?", ground_truth=False, client=client)

    assert answer == "that figure was not in my sources."
    # the framing system prompt leads, the replay is present, the question is last
    assert client.seen[0]["role"] == "system"
    assert "COMPLETE transcript" in client.seen[0]["content"]
    assert client.seen[-1]["content"] == "where did 1,000 issuers come from?"
    assert any("GoPlausible" in m["content"] for m in client.seen)
    # history carries the Q + A for the next turn
    assert history[-2:] == [
        {"role": "user", "content": "where did 1,000 issuers come from?"},
        {"role": "assistant", "content": "that figure was not in my sources."},
    ]


def test_interrogate_injects_ground_truth_before_question(monkeypatch):
    monkeypatch.setattr(ir._dg, "_resolves", lambda h: False)  # everything dead
    rev = ir.revive_session(source_url="goplausible", _session=_FakeCass([_session_row()]))
    client = _FakeClient()
    _, history = ir.interrogate(rev, "why link a dead domain?", ground_truth=True, client=client)
    # ground-truth note sits in the sent convo, just before the question
    assert any("GROUND-TRUTH CHECK" in m["content"] for m in client.seen)
    assert client.seen[-1]["content"] == "why link a dead domain?"
    # and is preserved in history so a follow-up keeps the confrontation
    assert any("GROUND-TRUTH CHECK" in m["content"] for m in history)


def test_interrogate_second_turn_keeps_history(monkeypatch):
    monkeypatch.setattr(ir._dg, "_resolves", lambda h: True)
    rev = ir.revive_session(source_url="goplausible", _session=_FakeCass([_session_row()]))
    client = _FakeClient(answer="A1")
    _, history = ir.interrogate(rev, "Q1", ground_truth=False, client=client)
    client2 = _FakeClient(answer="A2")
    _, history = ir.interrogate(rev, "Q2", ground_truth=False, history=history, client=client2)
    # second call's convo includes the first Q/A pair before Q2
    contents = [m["content"] for m in client2.seen]
    assert "Q1" in contents and "A1" in contents and contents[-1] == "Q2"
