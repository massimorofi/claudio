"""Tests for the task / task-run persistence and the suggestions.issue_id
nullable migration introduced with the autonomous task system.
"""
from __future__ import annotations

import aiosqlite
import pytest

from claudio.db import Database, utcnow


# ---- task CRUD ---------------------------------------------------------


async def test_create_and_get_task(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    task = await db.create_task(
        key="nightly",
        name="Nightly",
        prompt="do the thing",
        schedule="0 3 * * *",
    )
    assert task["key"] == "nightly"
    assert task["report_only"] is True
    assert task["enabled"] is True
    assert task["notify_on"] == ["warning", "critical", "failure"]
    fetched = await db.get_task("nightly")
    assert fetched["name"] == "Nightly"
    await db.close()


async def test_list_and_enabled_tasks(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    await db.create_task(key="a", name="A", prompt="", schedule="0 * * * *", enabled=True)
    await db.create_task(key="b", name="B", prompt="", schedule="0 * * * *", enabled=False)
    all_tasks = await db.list_tasks()
    assert {t["key"] for t in all_tasks} == {"a", "b"}
    enabled = await db.list_enabled_tasks()
    assert {t["key"] for t in enabled} == {"a"}
    await db.close()


async def test_update_task_normalizes_bools_and_notify(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    await db.create_task(key="x", name="X", prompt="", schedule="0 * * * *")
    updated = await db.update_task(
        "x", report_only=False, enabled=False, notify_on=["critical"]
    )
    assert updated["report_only"] is False
    assert updated["enabled"] is False
    assert updated["notify_on"] == ["critical"]
    await db.close()


async def test_delete_task(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    await db.create_task(key="gone", name="G", prompt="", schedule="0 * * * *")
    await db.delete_task("gone")
    assert await db.get_task("gone") is None
    await db.close()


async def test_duplicate_task_key_rejected(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    await db.create_task(key="dup", name="1", prompt="", schedule="0 * * * *")
    with pytest.raises(Exception):
        await db.create_task(key="dup", name="2", prompt="", schedule="0 * * * *")
    await db.close()


# ---- task runs -------------------------------------------------------


async def test_add_and_update_task_run(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    run_id = await db.add_task_run("nightly", trigger="manual")
    assert run_id >= 1
    await db.update_task_run(run_id, verdict="ok", summary="done", exit_code=0)
    run = await db.get_task_run(run_id)
    assert run["verdict"] == "ok"
    assert run["exit_code"] == 0
    assert run["trigger"] == "manual"
    await db.close()


async def test_list_task_runs_scoped_by_task(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    await db.add_task_run("a")
    await db.add_task_run("a")
    await db.add_task_run("b")
    a_runs = await db.list_task_runs(task_key="a")
    assert len(a_runs) == 2
    all_runs = await db.list_task_runs()
    assert len(all_runs) == 3
    await db.close()


# ---- audit retention + filters ---------------------------------------


async def test_list_audit_filters(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    await db.add_audit(action="fix_executed", command="a", actor="operator")
    await db.add_audit(action="task_run", command="b", actor="daemon")
    by_action = await db.list_audit(action="task_run")
    assert len(by_action) == 1
    assert by_action[0]["actor"] == "daemon"
    by_actor = await db.list_audit(actor="operator")
    assert len(by_actor) == 1
    await db.close()


async def test_delete_audit_older_than(tmp_path):
    db = Database(tmp_path / "t.db")
    await db.connect()
    # One old row (year 2000) and one fresh row.
    await db.conn.execute(
        "INSERT INTO claudio_audit (created_at, actor, action) VALUES (?, ?, ?)",
        ("2000-01-01T00:00:00+00:00", "operator", "old"),
    )
    await db.conn.commit()
    await db.add_audit(action="fresh")
    removed = await db.delete_audit_older_than("2020-01-01T00:00:00+00:00")
    assert removed == 1
    remaining = await db.list_audit()
    assert len(remaining) == 1
    assert remaining[0]["action"] == "fresh"
    await db.close()


# ---- suggestions.issue_id nullable migration -------------------------


async def test_migration_makes_issue_id_nullable(tmp_path):
    """Simulate an old DB whose suggestions.issue_id is NOT NULL, then open it
    with Database and confirm the column was rebuilt to nullable."""
    path = tmp_path / "old.db"
    async with aiosqlite.connect(str(path)) as conn:
        await conn.execute(
            """
            CREATE TABLE claudio_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_id INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                command TEXT NOT NULL,
                explanation TEXT NOT NULL DEFAULT '',
                risk TEXT NOT NULL DEFAULT 'low',
                reversible INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'pending',
                executed_at TEXT,
                exit_code INTEGER,
                output TEXT
            )
            """
        )
        await conn.execute(
            "INSERT INTO claudio_suggestions (issue_id, created_at, command) VALUES (?, ?, ?)",
            (7, utcnow(), "sudo foo"),
        )
        await conn.commit()

    db = Database(path)
    await db.connect()

    # The old row survived the rebuild.
    got = await db.get_suggestion(1)
    assert got["command"] == "sudo foo"
    assert got["issue_id"] == 7

    # A new suggestion with no issue (NULL) is now allowed.
    sid = await db.add_suggestion(
        issue_id=None, command="apt-get -y upgrade", explanation="full upgrade"
    )
    new = await db.get_suggestion(sid)
    assert new["issue_id"] is None
    await db.close()


async def test_migration_is_idempotent(tmp_path):
    """Re-opening an already-migrated DB must not error or double-rebuild."""
    db = Database(tmp_path / "t.db")
    await db.connect()
    await db.add_suggestion(issue_id=None, command="x")
    # Re-open the same file.
    db2 = Database(tmp_path / "t.db")
    await db2.connect()
    rows = await db2.list_audit()  # trivial query to prove it opened fine
    assert isinstance(rows, list)
    await db2.close()
    await db.close()
