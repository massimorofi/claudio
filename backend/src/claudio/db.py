"""SQLite persistence for Claudio's own state (sessions metadata, config history,
approvals, run records). The OpenAI Agents SDK's SQLiteSession manages its own
tables in the same file; ours are namespaced under ``claudio_``.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS claudio_sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    total_cost_usd REAL NOT NULL DEFAULT 0,
    total_input_tokens INTEGER NOT NULL DEFAULT 0,
    total_output_tokens INTEGER NOT NULL DEFAULT 0,
    last_context_tokens INTEGER NOT NULL DEFAULT 0,
    compaction_disabled INTEGER NOT NULL DEFAULT 0,
    turns_since_todo INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS claudio_config_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    saved_at TEXT NOT NULL,
    changed_keys TEXT NOT NULL DEFAULT '[]',
    config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claudio_approvals (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    call_id TEXT NOT NULL,
    arguments TEXT NOT NULL DEFAULT '{}',
    reason TEXT NOT NULL DEFAULT '',
    state TEXT NOT NULL DEFAULT 'pending',
    requested_at TEXT NOT NULL,
    resolved_at TEXT,
    decision TEXT,
    decision_reason TEXT
);

CREATE TABLE IF NOT EXISTS claudio_runs (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    state TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    turns_used INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    final_output TEXT,
    error_code TEXT,
    error_message TEXT,
    resume_state_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_runs_session ON claudio_runs (session_id);
CREATE INDEX IF NOT EXISTS idx_approvals_state ON claudio_approvals (state);
"""


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    """Thin async wrapper over aiosqlite with Claudio's schema."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(str(self.path))
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(SCHEMA)
        await self._migrate()
        await self._conn.commit()

    async def _migrate(self) -> None:
        """Add columns introduced after the initial schema, if missing."""
        async with self.conn.execute("PRAGMA table_info(claudio_runs)") as cur:
            cols = {row["name"] for row in await cur.fetchall()}
        if "resume_state_json" not in cols:
            await self.conn.execute(
                "ALTER TABLE claudio_runs ADD COLUMN resume_state_json TEXT"
            )
            await self.conn.commit()
        async with self.conn.execute("PRAGMA table_info(claudio_sessions)") as cur:
            scols = {row["name"] for row in await cur.fetchall()}
        if "last_context_tokens" not in scols:
            await self.conn.execute(
                "ALTER TABLE claudio_sessions ADD COLUMN last_context_tokens INTEGER NOT NULL DEFAULT 0"
            )
            await self.conn.commit()

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected")
        return self._conn

    # ---- sessions -------------------------------------------------------

    async def create_session(self, session_id: str, title: str = "") -> dict[str, Any]:
        now = utcnow()
        await self.conn.execute(
            "INSERT INTO claudio_sessions (id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (session_id, title, now, now),
        )
        await self.conn.commit()
        return await self.get_session(session_id)  # type: ignore[return-value]

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM claudio_sessions WHERE id = ?", (session_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_sessions(self) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT * FROM claudio_sessions ORDER BY updated_at DESC"
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def update_session(self, session_id: str, **fields: Any) -> None:
        if not fields:
            return
        fields["updated_at"] = utcnow()
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE claudio_sessions SET {cols} WHERE id = ?",
            (*fields.values(), session_id),
        )
        await self.conn.commit()

    async def delete_session(self, session_id: str) -> None:
        await self.conn.execute("DELETE FROM claudio_sessions WHERE id = ?", (session_id,))
        await self.conn.commit()

    async def clear_all_sessions(self) -> None:
        await self.conn.execute("DELETE FROM claudio_sessions")
        await self.conn.commit()

    # ---- config history -------------------------------------------------

    async def add_config_version(
        self, cfg_dict: dict[str, Any], changed: list[str]
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO claudio_config_history (saved_at, changed_keys, config_json) VALUES (?, ?, ?)",
            (utcnow(), json.dumps(changed), json.dumps(cfg_dict)),
        )
        await self.conn.commit()
        new_id = cur.lastrowid
        # Keep only the last 20 versions.
        await self.conn.execute(
            "DELETE FROM claudio_config_history WHERE id < (SELECT MAX(id) - 19 FROM claudio_config_history)"
        )
        await self.conn.commit()
        return int(new_id or 0)

    async def list_config_history(self) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT id, saved_at, changed_keys FROM claudio_config_history ORDER BY id DESC"
        ) as cur:
            rows = await cur.fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["changed_keys"] = json.loads(d.get("changed_keys") or "[]")
                out.append(d)
            return out

    async def get_config_version(self, version_id: int) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM claudio_config_history WHERE id = ?", (version_id,)
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return None
        d = dict(row)
        d["config"] = json.loads(d["config_json"])
        d["changed_keys"] = json.loads(d.get("changed_keys") or "[]")
        return d

    # ---- approvals ------------------------------------------------------

    async def create_approval(
        self,
        run_id: str,
        session_id: str,
        tool_name: str,
        call_id: str,
        arguments: Any,
        reason: str = "",
    ) -> str:
        approval_id = str(uuid.uuid4())
        await self.conn.execute(
            """INSERT INTO claudio_approvals
               (id, run_id, session_id, tool_name, call_id, arguments, reason, state, requested_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)""",
            (
                approval_id,
                run_id,
                session_id,
                tool_name,
                call_id,
                json.dumps(arguments),
                reason,
                utcnow(),
            ),
        )
        await self.conn.commit()
        return approval_id

    async def get_approval(self, approval_id: str) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM claudio_approvals WHERE id = ?", (approval_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_pending_approvals(self) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT * FROM claudio_approvals WHERE state = 'pending' ORDER BY requested_at ASC"
        ) as cur:
            rows = await cur.fetchall()
            out = []
            for r in rows:
                d = dict(r)
                d["arguments"] = json.loads(d.get("arguments") or "{}")
                out.append(d)
            return out

    async def resolve_approval(
        self, approval_id: str, decision: str, reason: str = ""
    ) -> None:
        await self.conn.execute(
            "UPDATE claudio_approvals SET state = 'resolved', decision = ?, decision_reason = ?, resolved_at = ? WHERE id = ?",
            (decision, reason, utcnow(), approval_id),
        )
        await self.conn.commit()

    # ---- runs -----------------------------------------------------------

    async def create_run(self, run_id: str, session_id: str) -> None:
        await self.conn.execute(
            "INSERT INTO claudio_runs (id, session_id, state, started_at) VALUES (?, ?, 'running', ?)",
            (run_id, session_id, utcnow()),
        )
        await self.conn.commit()

    async def update_run(self, run_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE claudio_runs SET {cols} WHERE id = ?",
            (*fields.values(), run_id),
        )
        await self.conn.commit()

    async def get_run(self, run_id: str) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM claudio_runs WHERE id = ?", (run_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None

    async def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT * FROM claudio_runs ORDER BY started_at DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    async def run_totals(self) -> dict[str, Any]:
        """Cumulative totals across every persisted run.

        Computed from the runs table so the monitoring overview reflects durable
        reality and survives a backend restart, unlike the in-memory counters
        which are scoped to the current process uptime.
        """
        async with self.conn.execute(
            "SELECT "
            "COUNT(*) AS runs_total, "
            "COALESCE(SUM(CASE WHEN state = 'completed' THEN 1 ELSE 0 END), 0) AS runs_completed, "
            "COALESCE(SUM(CASE WHEN state = 'failed' THEN 1 ELSE 0 END), 0) AS runs_failed, "
            "COALESCE(SUM(input_tokens), 0) AS tokens_in, "
            "COALESCE(SUM(output_tokens), 0) AS tokens_out, "
            "COALESCE(SUM(cost_usd), 0) AS cost_usd "
            "FROM claudio_runs"
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else {}

    async def fail_interrupted_runs(self) -> int:
        """On boot, mark any run left 'running' by a crash as failed."""
        cur = await self.conn.execute(
            "UPDATE claudio_runs SET state = 'failed', error_code = 'backend_restarted', "
            "error_message = 'Backend restarted while the run was in progress', finished_at = ? "
            "WHERE state = 'running'",
            (utcnow(),),
        )
        await self.conn.commit()
        return cur.rowcount or 0
