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
    turns_since_todo INTEGER NOT NULL DEFAULT 0,
    todos TEXT NOT NULL DEFAULT '[]'
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

CREATE TABLE IF NOT EXISTS claudio_health_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    verdict TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '[]',
    duration_ms INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'scheduled'
);

CREATE INDEX IF NOT EXISTS idx_health_created ON claudio_health_checks (created_at DESC);

CREATE TABLE IF NOT EXISTS claudio_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'health',
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'open',
    resolved_at TEXT,
    resolution TEXT
);

CREATE INDEX IF NOT EXISTS idx_issues_status ON claudio_issues (status);

CREATE TABLE IF NOT EXISTS claudio_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id INTEGER,
    created_at TEXT NOT NULL,
    command TEXT NOT NULL,
    explanation TEXT NOT NULL DEFAULT '',
    risk TEXT NOT NULL DEFAULT 'low',
    reversible INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    executed_at TEXT,
    exit_code INTEGER,
    output TEXT
);

CREATE INDEX IF NOT EXISTS idx_suggestions_issue ON claudio_suggestions (issue_id);

CREATE TABLE IF NOT EXISTS claudio_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    actor TEXT NOT NULL DEFAULT 'operator',
    action TEXT NOT NULL,
    command TEXT,
    exit_code INTEGER,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS claudio_tasks (
    key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'prompt',
    prompt TEXT NOT NULL DEFAULT '',
    script TEXT,
    schedule TEXT NOT NULL,
    approval_mode TEXT NOT NULL DEFAULT 'off',
    report_only INTEGER NOT NULL DEFAULT 1,
    notify_on TEXT NOT NULL DEFAULT '["warning","critical","failure"]',
    enabled INTEGER NOT NULL DEFAULT 1,
    last_run_at TEXT,
    next_run_at TEXT,
    last_verdict TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS claudio_task_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    verdict TEXT,
    summary TEXT,
    output TEXT,
    exit_code INTEGER,
    trigger TEXT NOT NULL DEFAULT 'scheduled'
);

CREATE INDEX IF NOT EXISTS idx_task_runs_task ON claudio_task_runs (task_key, id DESC);
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
        if "todos" not in scols:
            await self.conn.execute(
                "ALTER TABLE claudio_sessions ADD COLUMN todos TEXT NOT NULL DEFAULT '[]'"
            )
            await self.conn.commit()
        # Task types: 'prompt' (agent playbook) vs 'script' (direct shell).
        # Existing rows default to 'prompt' with no script, preserving behavior.
        async with self.conn.execute("PRAGMA table_info(claudio_tasks)") as cur:
            tcols = {row["name"] for row in await cur.fetchall()}
        if "type" not in tcols:
            await self.conn.execute(
                "ALTER TABLE claudio_tasks ADD COLUMN type TEXT NOT NULL DEFAULT 'prompt'"
            )
            await self.conn.commit()
        if "script" not in tcols:
            await self.conn.execute("ALTER TABLE claudio_tasks ADD COLUMN script TEXT")
            await self.conn.commit()
        # Make suggestions.issue_id nullable: maintenance suggestions are not tied
        # to a detected issue. SQLite can't drop NOT NULL in place, so rebuild the
        # table only if the column is still NOT NULL (idempotent on fresh DBs).
        async with self.conn.execute("PRAGMA table_info(claudio_suggestions)") as cur:
            sugg = {row["name"]: row for row in await cur.fetchall()}
        issue_col = sugg.get("issue_id")
        if issue_col is not None and issue_col["notnull"] == 1:
            await self.conn.executescript(
                """
                CREATE TABLE claudio_suggestions_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    issue_id INTEGER,
                    created_at TEXT NOT NULL,
                    command TEXT NOT NULL,
                    explanation TEXT NOT NULL DEFAULT '',
                    risk TEXT NOT NULL DEFAULT 'low',
                    reversible INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'pending',
                    executed_at TEXT,
                    exit_code INTEGER,
                    output TEXT
                );
                INSERT INTO claudio_suggestions_new
                    (id, issue_id, created_at, command, explanation, risk,
                     reversible, status, executed_at, exit_code, output)
                    SELECT id, issue_id, created_at, command, explanation, risk,
                           reversible, status, executed_at, exit_code, output
                    FROM claudio_suggestions;
                DROP TABLE claudio_suggestions;
                ALTER TABLE claudio_suggestions_new RENAME TO claudio_suggestions;
                CREATE INDEX IF NOT EXISTS idx_suggestions_issue
                    ON claudio_suggestions (issue_id);
                """
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

    async def list_compaction_disabled_session_ids(self) -> list[str]:
        """Session ids whose compaction circuit breaker is tripped."""
        async with self.conn.execute(
            "SELECT id FROM claudio_sessions WHERE compaction_disabled = 1"
        ) as cur:
            rows = await cur.fetchall()
            return [r["id"] for r in rows]

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

    # ---- health checks --------------------------------------------------

    async def add_health_check(
        self,
        verdict: str,
        summary: str,
        details: list[dict[str, Any]],
        duration_ms: int = 0,
        source: str = "scheduled",
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO claudio_health_checks "
            "(created_at, verdict, summary, details_json, duration_ms, source) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (utcnow(), verdict, summary, json.dumps(details), duration_ms, source),
        )
        await self.conn.commit()
        return int(cur.lastrowid or 0)

    async def get_latest_health_check(self) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM claudio_health_checks ORDER BY id DESC LIMIT 1"
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["details"] = json.loads(d.get("details_json") or "[]")
        return d

    async def list_health_checks(self, limit: int = 50) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT * FROM claudio_health_checks ORDER BY id DESC LIMIT ?", (limit,)
        ) as cur:
            rows = await cur.fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["details"] = json.loads(d.get("details_json") or "[]")
            out.append(d)
        return out

    # ---- issues ---------------------------------------------------------

    async def create_issue(
        self,
        severity: str,
        title: str,
        detail: str = "",
        source: str = "health",
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO claudio_issues "
            "(created_at, source, severity, title, detail, status) "
            "VALUES (?, ?, ?, ?, ?, 'open')",
            (utcnow(), source, severity, title, detail),
        )
        await self.conn.commit()
        return int(cur.lastrowid or 0)

    async def get_issue(self, issue_id: int) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM claudio_issues WHERE id = ?", (issue_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def list_issues(self, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            async with self.conn.execute(
                "SELECT * FROM claudio_issues WHERE status = ? ORDER BY id DESC", (status,)
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with self.conn.execute(
                "SELECT * FROM claudio_issues ORDER BY id DESC"
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def update_issue_status(
        self, issue_id: int, status: str, resolution: str | None = None
    ) -> None:
        if status in ("acknowledged", "resolved"):
            await self.conn.execute(
                "UPDATE claudio_issues SET status = ?, resolution = ?, resolved_at = ? "
                "WHERE id = ?",
                (status, resolution, utcnow(), issue_id),
            )
        else:
            await self.conn.execute(
                "UPDATE claudio_issues SET status = ? WHERE id = ?", (status, issue_id)
            )
        await self.conn.commit()

    async def count_open_issues(self) -> int:
        async with self.conn.execute(
            "SELECT COUNT(*) AS n FROM claudio_issues WHERE status = 'open'"
        ) as cur:
            row = await cur.fetchone()
        if not row:
            return 0
        return int(row["n"] or 0)

    async def find_open_issue_by_title(self, title: str) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM claudio_issues WHERE status = 'open' AND title = ? LIMIT 1",
            (title,),
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    # ---- suggestions ----------------------------------------------------

    async def add_suggestion(
        self,
        issue_id: int,
        command: str,
        explanation: str = "",
        risk: str = "low",
        reversible: bool = True,
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO claudio_suggestions "
            "(issue_id, created_at, command, explanation, risk, reversible, status) "
            "VALUES (?, ?, ?, ?, ?, ?, 'pending')",
            (issue_id, utcnow(), command, explanation, risk, 1 if reversible else 0),
        )
        await self.conn.commit()
        return int(cur.lastrowid or 0)

    async def get_suggestion(self, suggestion_id: int) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM claudio_suggestions WHERE id = ?", (suggestion_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def list_suggestions_for_issue(self, issue_id: int) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT * FROM claudio_suggestions WHERE issue_id = ? ORDER BY id DESC",
            (issue_id,),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def update_suggestion(self, suggestion_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE claudio_suggestions SET {cols} WHERE id = ?",
            (*fields.values(), suggestion_id),
        )
        await self.conn.commit()

    # ---- audit ----------------------------------------------------------

    async def add_audit(
        self,
        action: str,
        command: str | None = None,
        exit_code: int | None = None,
        detail: str | None = None,
        actor: str = "operator",
    ) -> int:
        cur = await self.conn.execute(
            "INSERT INTO claudio_audit "
            "(created_at, actor, action, command, exit_code, detail) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (utcnow(), actor, action, command, exit_code, detail),
        )
        await self.conn.commit()
        return int(cur.lastrowid or 0)

    async def list_audit(
        self,
        limit: int = 100,
        action: str | None = None,
        actor: str | None = None,
        since: str | None = None,
        until: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if action:
            clauses.append("action = ?")
            params.append(action)
        if actor:
            clauses.append("actor = ?")
            params.append(actor)
        if since:
            clauses.append("created_at >= ?")
            params.append(since)
        if until:
            clauses.append("created_at <= ?")
            params.append(until)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        async with self.conn.execute(
            f"SELECT * FROM claudio_audit {where} ORDER BY id DESC LIMIT ?",
            (*params, limit),
        ) as cur:
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def delete_audit_older_than(self, cutoff_iso: str) -> int:
        """Delete audit rows created before ``cutoff_iso``. Returns rows removed."""
        cur = await self.conn.execute(
            "DELETE FROM claudio_audit WHERE created_at < ?", (cutoff_iso,)
        )
        await self.conn.commit()
        return cur.rowcount or 0

    # ---- tasks ----------------------------------------------------------

    @staticmethod
    def _task_from_row(row: Any) -> dict[str, Any]:
        d = dict(row)
        d["report_only"] = bool(d.get("report_only"))
        d["enabled"] = bool(d.get("enabled"))
        d["notify_on"] = json.loads(d.get("notify_on") or '["warning","critical","failure"]')
        return d

    async def create_task(
        self,
        key: str,
        name: str,
        prompt: str,
        schedule: str,
        approval_mode: str = "off",
        report_only: bool = True,
        notify_on: list[str] | None = None,
        enabled: bool = True,
        type: str = "prompt",
        script: str | None = None,
    ) -> dict[str, Any]:
        now = utcnow()
        notify = json.dumps(notify_on if notify_on is not None else ["warning", "critical", "failure"])
        await self.conn.execute(
            "INSERT INTO claudio_tasks "
            "(key, name, type, prompt, script, schedule, approval_mode, report_only, "
            "notify_on, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                name,
                type,
                prompt,
                script,
                schedule,
                approval_mode,
                1 if report_only else 0,
                notify,
                1 if enabled else 0,
                now,
                now,
            ),
        )
        await self.conn.commit()
        return await self.get_task(key)  # type: ignore[return-value]

    async def get_task(self, key: str) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM claudio_tasks WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
        return self._task_from_row(row) if row else None

    async def list_tasks(self) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT * FROM claudio_tasks ORDER BY created_at ASC"
        ) as cur:
            rows = await cur.fetchall()
        return [self._task_from_row(r) for r in rows]

    async def list_enabled_tasks(self) -> list[dict[str, Any]]:
        async with self.conn.execute(
            "SELECT * FROM claudio_tasks WHERE enabled = 1 ORDER BY created_at ASC"
        ) as cur:
            rows = await cur.fetchall()
        return [self._task_from_row(r) for r in rows]

    async def update_task(self, key: str, **fields: Any) -> dict[str, Any] | None:
        if not fields:
            return await self.get_task(key)
        if "report_only" in fields:
            fields["report_only"] = 1 if fields["report_only"] else 0
        if "enabled" in fields:
            fields["enabled"] = 1 if fields["enabled"] else 0
        if "notify_on" in fields and not isinstance(fields["notify_on"], str):
            fields["notify_on"] = json.dumps(fields["notify_on"])
        fields["updated_at"] = utcnow()
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE claudio_tasks SET {cols} WHERE key = ?",
            (*fields.values(), key),
        )
        await self.conn.commit()
        return await self.get_task(key)

    async def delete_task(self, key: str) -> None:
        await self.conn.execute("DELETE FROM claudio_tasks WHERE key = ?", (key,))
        await self.conn.commit()

    # ---- task runs ------------------------------------------------------

    async def add_task_run(self, task_key: str, trigger: str = "scheduled") -> int:
        cur = await self.conn.execute(
            "INSERT INTO claudio_task_runs (task_key, started_at, trigger) VALUES (?, ?, ?)",
            (task_key, utcnow(), trigger),
        )
        await self.conn.commit()
        return int(cur.lastrowid or 0)

    async def update_task_run(self, run_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        await self.conn.execute(
            f"UPDATE claudio_task_runs SET {cols} WHERE id = ?",
            (*fields.values(), run_id),
        )
        await self.conn.commit()

    async def get_task_run(self, run_id: int) -> dict[str, Any] | None:
        async with self.conn.execute(
            "SELECT * FROM claudio_task_runs WHERE id = ?", (run_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def list_task_runs(
        self, task_key: str | None = None, limit: int = 50
    ) -> list[dict[str, Any]]:
        if task_key:
            async with self.conn.execute(
                "SELECT * FROM claudio_task_runs WHERE task_key = ? ORDER BY id DESC LIMIT ?",
                (task_key, limit),
            ) as cur:
                rows = await cur.fetchall()
        else:
            async with self.conn.execute(
                "SELECT * FROM claudio_task_runs ORDER BY id DESC LIMIT ?", (limit,)
            ) as cur:
                rows = await cur.fetchall()
        return [dict(r) for r in rows]
