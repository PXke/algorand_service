"""Abstract session-transcript persistence for one compose run.

Lets the SAME compose orchestration (mistral_compose.py's checkpoint/telemetry
calls) write its transcript to prod Cassandra (SessionRegisterCassandra,
delegating verbatim to tool_insights_store) or to a local file
(SessionRegisterSQLite/SessionRegisterTxt) for offline multi-provider
benchmarking, with no fork in the orchestration code itself -- it always just
calls `register.new_ref()`/`register.upsert(...)`.

The local backends store the FULL debug["messages"]/trace untruncated (no
120KB cap, no per-role content-length cap, no stripped tool_call ids) --
unlike Cassandra's compose_sessions table, which caps aggressively because
it's read by the admin Sessions tab UI, a benchmark run wants the complete
transcript to actually compare provider behavior.
"""

from __future__ import annotations

import json
import sqlite3
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4


class SessionRegister(ABC):
    """One compose's transcript sink. `new_ref()` mints a stable (session_id, created_at) at the start of a compose so every later `upsert()` call updates the SAME logical row -- mirrors tool_insights_store.new_session_ref/record_compose_session's exact contract, so callers don't need to know which backend they're writing to."""

    @abstractmethod
    def new_ref(self) -> tuple[UUID, datetime]:
        """Mint a stable (session_id, created_at) pair for one compose's whole lifetime."""

    @abstractmethod
    def upsert(
        self,
        *,
        debug: dict[str, Any] | None,
        trace: list[dict[str, Any]] | None,
        service_id: str = "",
        source_url: str = "",
        model: str = "",
        final_output: str = "",
        status: str = "ok",
        duration_ms: int = 0,
        session_id: UUID | None = None,
        created_at: datetime | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cached_tokens: int = 0,
        digest: str = "",
    ) -> bool:
        """Write (or overwrite) this compose's transcript row. Returns True on success, False on any failure -- never raises, matching record_compose_session's fail-soft contract.

        `digest` is the research phase's compressed summary text, checkpointed
        at the "writing" stage (root-caused 2026-08-14: a Kimi K3 write call
        timed out after a real, expensive research phase completed, and the
        digest that phase produced was gone with it -- there was no recoverable
        artifact, only the raw research messages/trace). Local backends
        (SQLite/Txt) persist it; SessionRegisterCassandra does not yet -- prod's
        compose_sessions table has no digest column, and adding one is a
        separate migration decision, not folded into this benchmark-driven fix.
        """


class SessionRegisterCassandra(SessionRegister):
    """Prod backend -- delegates verbatim to tool_insights_store, so this is a zero-behavior-change wrapper around what every existing caller already does today. Imports tool_insights_store lazily (inside the methods, not at module level) so importing session_register.py never pulls in the cassandra-driver connection setup -- needed so SessionRegisterSQLite/Txt stay usable from a bare local script with no Cassandra reachable at all."""

    def new_ref(self) -> tuple[UUID, datetime]:
        """Delegate to tool_insights_store.new_session_ref."""
        from app.modules.ai.tool_insights_store import new_session_ref

        return new_session_ref()

    def upsert(
        self,
        *,
        debug: dict[str, Any] | None,
        trace: list[dict[str, Any]] | None,
        service_id: str = "",
        source_url: str = "",
        model: str = "",
        final_output: str = "",
        status: str = "ok",
        duration_ms: int = 0,
        session_id: UUID | None = None,
        created_at: datetime | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cached_tokens: int = 0,
        digest: str = "",  # noqa: ARG002 -- accepted for interface parity, not yet persisted (see class docstring / ABC docstring)
    ) -> bool:
        """Delegate to tool_insights_store.record_compose_session."""
        from app.modules.ai.tool_insights_store import record_compose_session

        return record_compose_session(
            debug=debug,
            trace=trace,
            service_id=service_id,
            source_url=source_url,
            model=model,
            final_output=final_output,
            status=status,
            duration_ms=duration_ms,
            session_id=session_id,
            created_at=created_at,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cached_tokens=cached_tokens,
        )


def _rounds_and_tool_calls(
    debug: dict[str, Any] | None, trace: list[dict[str, Any]] | None
) -> tuple[int, int]:
    rounds = int((debug or {}).get("rounds", 0) or 0)
    tool_calls = len(trace or [])
    return rounds, tool_calls


class SessionRegisterSQLite(SessionRegister):
    """Local benchmark backend: one sqlite3 (stdlib, no new dependency) file, one row per session_id, upserted in place across a compose's checkpoints -- mirrors compose_sessions' columns so a comparison query (`SELECT provider... GROUP BY provider`) reads naturally, but stores the FULL untruncated transcript."""

    def __init__(self, db_path: str | Path) -> None:
        """Open (creating if needed) the sqlite3 file at `db_path` and ensure the compose_sessions table exists."""
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS compose_sessions (
                session_id TEXT PRIMARY KEY,
                created_at TEXT,
                service_id TEXT,
                source_url TEXT,
                model TEXT,
                status TEXT,
                rounds INTEGER,
                tool_calls INTEGER,
                duration_ms INTEGER,
                messages TEXT,
                trace TEXT,
                final_output TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER
            )
            """
        )
        # CREATE TABLE IF NOT EXISTS is a no-op against an already-created file
        # (this same db_path gets reused across many benchmark relaunches) --
        # root-caused 2026-08-14: adding the digest column above did nothing
        # for an existing file, every upsert() silently failed on "no such
        # column: digest" (caught by upsert's own try/except), and a live
        # ~25-minute run recorded ZERO checkpoints, not even an error row.
        # ALTER TABLE ADD COLUMN is the safe, additive migration for a column
        # that may or may not already be there.
        existing_cols = {row[1] for row in self._conn.execute("PRAGMA table_info(compose_sessions)")}
        if "digest" not in existing_cols:
            self._conn.execute("ALTER TABLE compose_sessions ADD COLUMN digest TEXT")
        if "cached_tokens" not in existing_cols:
            self._conn.execute("ALTER TABLE compose_sessions ADD COLUMN cached_tokens INTEGER")
        self._conn.commit()

    def new_ref(self) -> tuple[UUID, datetime]:
        """A fresh local (uuid4, now) pair -- no need for Cassandra's time-ordered UUID locally."""
        return uuid4(), datetime.now(tz=UTC)

    def upsert(
        self,
        *,
        debug: dict[str, Any] | None,
        trace: list[dict[str, Any]] | None,
        service_id: str = "",
        source_url: str = "",
        model: str = "",
        final_output: str = "",
        status: str = "ok",
        duration_ms: int = 0,
        session_id: UUID | None = None,
        created_at: datetime | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cached_tokens: int = 0,
        digest: str = "",
    ) -> bool:
        """Insert or, keyed on session_id, update this compose's row with the full untruncated transcript."""
        try:
            if session_id is None or created_at is None:
                session_id, created_at = self.new_ref()
            rounds, tool_calls = _rounds_and_tool_calls(debug, trace)
            self._conn.execute(
                """
                INSERT INTO compose_sessions
                    (session_id, created_at, service_id, source_url, model, status,
                     rounds, tool_calls, duration_ms, messages, trace, final_output,
                     prompt_tokens, completion_tokens, total_tokens, digest, cached_tokens)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    created_at=excluded.created_at, service_id=excluded.service_id,
                    source_url=excluded.source_url, model=excluded.model,
                    status=excluded.status, rounds=excluded.rounds,
                    tool_calls=excluded.tool_calls, duration_ms=excluded.duration_ms,
                    messages=excluded.messages, trace=excluded.trace,
                    final_output=excluded.final_output, prompt_tokens=excluded.prompt_tokens,
                    completion_tokens=excluded.completion_tokens, total_tokens=excluded.total_tokens,
                    digest=excluded.digest, cached_tokens=excluded.cached_tokens
                """,
                (
                    str(session_id),
                    created_at.isoformat(),
                    service_id,
                    source_url,
                    model,
                    status,
                    rounds,
                    tool_calls,
                    int(duration_ms),
                    json.dumps((debug or {}).get("messages") or []),
                    json.dumps(trace or []),
                    final_output,
                    int(prompt_tokens),
                    int(completion_tokens),
                    int(total_tokens),
                    digest,
                    int(cached_tokens),
                ),
            )
            self._conn.commit()
            return True
        except Exception:
            return False


class SessionRegisterTxt(SessionRegister):
    """Local benchmark backend: appends one JSONL line per upsert to a plain text file -- cheap eyeballing of a transcript without a SQL client. Append-only (not a true upsert): each checkpoint during one compose writes a fresh full-snapshot line, so the LAST line for a given session_id is that compose's final state."""

    def __init__(self, path: str | Path) -> None:
        """Wire the JSONL file path (creating parent directories if needed) -- the file itself is created lazily on first upsert."""
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def new_ref(self) -> tuple[UUID, datetime]:
        """A fresh local (uuid4, now) pair -- no need for Cassandra's time-ordered UUID locally."""
        return uuid4(), datetime.now(tz=UTC)

    def upsert(
        self,
        *,
        debug: dict[str, Any] | None,
        trace: list[dict[str, Any]] | None,
        service_id: str = "",
        source_url: str = "",
        model: str = "",
        final_output: str = "",
        status: str = "ok",
        duration_ms: int = 0,
        session_id: UUID | None = None,
        created_at: datetime | None = None,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        total_tokens: int = 0,
        cached_tokens: int = 0,
        digest: str = "",
    ) -> bool:
        """Append one full-snapshot JSON line -- the last line for a given session_id is that compose's final state."""
        try:
            if session_id is None or created_at is None:
                session_id, created_at = self.new_ref()
            rounds, tool_calls = _rounds_and_tool_calls(debug, trace)
            record = {
                "session_id": str(session_id),
                "created_at": created_at.isoformat(),
                "service_id": service_id,
                "source_url": source_url,
                "model": model,
                "status": status,
                "rounds": rounds,
                "tool_calls": tool_calls,
                "duration_ms": int(duration_ms),
                "messages": (debug or {}).get("messages") or [],
                "trace": trace or [],
                "final_output": final_output,
                "prompt_tokens": int(prompt_tokens),
                "completion_tokens": int(completion_tokens),
                "total_tokens": int(total_tokens),
                "cached_tokens": int(cached_tokens),
                "digest": digest,
            }
            with self._path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
            return True
        except Exception:
            return False
