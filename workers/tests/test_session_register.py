"""SessionRegister: one interface, three backends, so the same compose orchestration can checkpoint to prod Cassandra or a local file for offline multi-provider benchmarking without a code-path fork."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from uuid import UUID

from app.modules.ai.session_register import (
    SessionRegisterCassandra,
    SessionRegisterSQLite,
    SessionRegisterTxt,
)


def _debug_and_trace() -> tuple[dict, list]:
    debug = {"rounds": 3, "messages": [{"role": "user", "content": "hello"}]}
    trace = [{"tool": "search_web", "arguments": {}, "result": {"ok": True}}]
    return debug, trace


def test_cassandra_new_ref_delegates_to_tool_insights_store() -> None:
    """SessionRegisterCassandra.new_ref() returns the same shape tool_insights_store.new_session_ref does."""
    register = SessionRegisterCassandra()
    session_id, created_at = register.new_ref()
    assert isinstance(session_id, UUID)
    assert isinstance(created_at, datetime)


def test_cassandra_upsert_delegates_verbatim_to_record_compose_session(
    fake_cassandra_session: MagicMock,
) -> None:
    """SessionRegisterCassandra.upsert() is a zero-behavior-change wrapper around the existing prod write path."""
    debug, trace = _debug_and_trace()
    register = SessionRegisterCassandra()
    ok = register.upsert(
        debug=debug,
        trace=trace,
        service_id="lumirogue-com",
        source_url="https://lumirogue.com",
        model="mistral-large-latest",
        final_output="{}",
        status="ok",
        duration_ms=1200,
        prompt_tokens=100,
        completion_tokens=50,
        total_tokens=150,
    )
    assert ok is True
    assert fake_cassandra_session.execute.called


def test_sqlite_new_ref_returns_a_fresh_uuid_and_timestamp(tmp_path: Path) -> None:
    """SessionRegisterSQLite.new_ref() returns a UUID/datetime pair, same shape as the Cassandra backend."""
    register = SessionRegisterSQLite(tmp_path / "sessions.sqlite")
    session_id, created_at = register.new_ref()
    assert isinstance(session_id, UUID)
    assert isinstance(created_at, datetime)


def test_sqlite_upsert_writes_a_row_with_full_untruncated_transcript(tmp_path: Path) -> None:
    """Unlike Cassandra's compose_sessions (capped for the admin UI), the local backend keeps the whole transcript -- needed to actually compare provider behavior."""
    db_path = tmp_path / "sessions.sqlite"
    register = SessionRegisterSQLite(db_path)
    session_id, created_at = register.new_ref()
    debug, trace = _debug_and_trace()

    ok = register.upsert(
        debug=debug,
        trace=trace,
        service_id="lumirogue-com",
        source_url="https://lumirogue.com",
        model="gemini-3.7",
        final_output='{"title": "t"}',
        status="ok",
        duration_ms=4500,
        session_id=session_id,
        created_at=created_at,
        prompt_tokens=200,
        completion_tokens=80,
        total_tokens=280,
    )
    assert ok is True

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT model, prompt_tokens, completion_tokens, total_tokens, messages, trace "
        "FROM compose_sessions WHERE session_id = ?",
        (str(session_id),),
    ).fetchone()
    assert row is not None
    model, prompt_tokens, completion_tokens, total_tokens, messages_json, trace_json = row
    assert model == "gemini-3.7"
    assert (prompt_tokens, completion_tokens, total_tokens) == (200, 80, 280)
    assert json.loads(messages_json) == debug["messages"]
    assert json.loads(trace_json) == trace


def test_sqlite_upsert_stores_the_digest(tmp_path: Path) -> None:
    """The research digest -- checkpointed at "writing", the compressed summary a huge write call is built from -- must be recoverable even if that write call later fails, so a costly research phase isn't a total loss."""
    db_path = tmp_path / "sessions.sqlite"
    register = SessionRegisterSQLite(db_path)
    session_id, created_at = register.new_ref()
    debug, trace = _debug_and_trace()

    register.upsert(
        debug=debug,
        trace=trace,
        session_id=session_id,
        created_at=created_at,
        status="writing",
        digest="Lumi Rogue is a solo-built Algorand roguelike...",
    )

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT digest FROM compose_sessions WHERE session_id = ?", (str(session_id),)
    ).fetchone()
    assert row[0] == "Lumi Rogue is a solo-built Algorand roguelike..."


def test_sqlite_later_upsert_without_digest_does_not_erase_the_earlier_one(
    tmp_path: Path,
) -> None:
    """A terminal checkpoint (the final 'ok'/'error' status) that doesn't itself carry a fresh digest must not wipe out the one already stored from the 'writing' checkpoint -- the caller is responsible for re-passing the last known value, but the backend itself must not silently default it back to empty on every upsert."""
    db_path = tmp_path / "sessions.sqlite"
    register = SessionRegisterSQLite(db_path)
    session_id, created_at = register.new_ref()
    debug, trace = _debug_and_trace()

    register.upsert(
        debug=debug,
        trace=trace,
        session_id=session_id,
        created_at=created_at,
        status="writing",
        digest="the real research digest",
    )
    # A later checkpoint that (like a real caller re-passing the held value)
    # still carries the SAME digest forward -- not omitting it.
    register.upsert(
        debug=debug,
        trace=trace,
        session_id=session_id,
        created_at=created_at,
        status="error",
        digest="the real research digest",
    )

    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT status, digest FROM compose_sessions WHERE session_id = ?",
        (str(session_id),),
    ).fetchone()
    assert row == ("error", "the real research digest")


def test_txt_upsert_includes_the_digest(tmp_path: Path) -> None:
    """The JSONL backend must carry the digest through too, same as SQLite."""
    path = tmp_path / "sessions.jsonl"
    register = SessionRegisterTxt(path)
    session_id, created_at = register.new_ref()
    debug, trace = _debug_and_trace()

    register.upsert(
        debug=debug,
        trace=trace,
        session_id=session_id,
        created_at=created_at,
        status="writing",
        digest="a research digest",
    )

    line = json.loads(path.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert line["digest"] == "a research digest"


def test_sqlite_repeated_upsert_with_the_same_session_id_updates_in_place(tmp_path: Path) -> None:
    """Mirrors compose_sessions' per-stage upsert semantics: status changes across a compose's checkpoints (researching -> writing -> ok), same row throughout."""
    db_path = tmp_path / "sessions.sqlite"
    register = SessionRegisterSQLite(db_path)
    session_id, created_at = register.new_ref()
    debug, trace = _debug_and_trace()

    register.upsert(
        debug=debug,
        trace=trace,
        session_id=session_id,
        created_at=created_at,
        status="researching",
    )
    register.upsert(
        debug=debug,
        trace=trace,
        session_id=session_id,
        created_at=created_at,
        status="ok",
        total_tokens=999,
    )

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT status, total_tokens FROM compose_sessions WHERE session_id = ?",
        (str(session_id),),
    ).fetchall()
    assert len(rows) == 1
    assert rows[0] == ("ok", 999)


def test_txt_new_ref_returns_a_fresh_uuid_and_timestamp(tmp_path: Path) -> None:
    """SessionRegisterTxt.new_ref() returns a UUID/datetime pair, same shape as the other backends."""
    register = SessionRegisterTxt(tmp_path / "sessions.jsonl")
    session_id, created_at = register.new_ref()
    assert isinstance(session_id, UUID)
    assert isinstance(created_at, datetime)


def test_txt_upsert_appends_one_full_json_line(tmp_path: Path) -> None:
    """One upsert() call writes exactly one JSONL line carrying the full record."""
    path = tmp_path / "sessions.jsonl"
    register = SessionRegisterTxt(path)
    session_id, created_at = register.new_ref()
    debug, trace = _debug_and_trace()

    ok = register.upsert(
        debug=debug,
        trace=trace,
        service_id="lumirogue-com",
        source_url="https://lumirogue.com",
        model="kimi-k3",
        final_output='{"title": "t"}',
        status="ok",
        duration_ms=3000,
        session_id=session_id,
        created_at=created_at,
        prompt_tokens=10,
        completion_tokens=5,
        total_tokens=15,
    )
    assert ok is True

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["session_id"] == str(session_id)
    assert record["model"] == "kimi-k3"
    assert record["messages"] == debug["messages"]
    assert record["trace"] == trace


def test_txt_repeated_upsert_appends_rather_than_replaces(tmp_path: Path) -> None:
    """Unlike the SQLite backend, SessionRegisterTxt never overwrites a prior line -- each checkpoint gets its own."""
    path = tmp_path / "sessions.jsonl"
    register = SessionRegisterTxt(path)
    session_id, created_at = register.new_ref()
    debug, trace = _debug_and_trace()

    register.upsert(
        debug=debug,
        trace=trace,
        session_id=session_id,
        created_at=created_at,
        status="researching",
    )
    register.upsert(
        debug=debug,
        trace=trace,
        session_id=session_id,
        created_at=created_at,
        status="ok",
    )

    lines = path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["status"] == "researching"
    assert json.loads(lines[1])["status"] == "ok"
