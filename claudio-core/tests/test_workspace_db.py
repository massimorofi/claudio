"""Tests for per-session workspace storage and the workspace registry (Phase 1).

The workspace is a property of a session, fixed at creation, plus a registry of
folders the user has made available. These cover the storage layer only.
"""
from __future__ import annotations

import pytest

from claudio_core.db import Database


@pytest.fixture
async def db(tmp_path):
    d = Database(tmp_path / "test.db")
    await d.connect()
    yield d
    await d.close()


# ---- session workspace columns -------------------------------------


async def test_create_session_without_workspace_defaults_empty(db):
    s = await db.create_session("s1", title="hello")
    assert s["workspace_dir"] == ""
    assert s["workspace_origin"] == "default"


async def test_create_session_with_workspace(db):
    s = await db.create_session(
        "s2", title="t", workspace_dir="/home/x/proj", workspace_origin="vscode"
    )
    assert s["workspace_dir"] == "/home/x/proj"
    assert s["workspace_origin"] == "vscode"


async def test_get_session_workspace(db):
    await db.create_session("s3", workspace_dir="/a/b", workspace_origin="webui")
    assert await db.get_session_workspace("s3") == "/a/b"


async def test_get_session_workspace_unset_returns_none(db):
    await db.create_session("s4")
    assert await db.get_session_workspace("s4") is None


async def test_get_session_workspace_missing_session_returns_none(db):
    assert await db.get_session_workspace("nope") is None


# ---- workspace registry ------------------------------------------


async def test_register_workspace_creates_record(db):
    w = await db.register_workspace("/home/x/proj", label="Proj")
    assert w["path"] == "/home/x/proj"
    assert w["label"] == "Proj"
    assert w["id"]


async def test_register_workspace_idempotent_no_duplicate(db):
    a = await db.register_workspace("/home/x/proj")
    b = await db.register_workspace("/home/x/proj")
    assert a["id"] == b["id"]
    assert len(await db.list_workspaces()) == 1


async def test_touch_workspace_updates_last_used(db):
    w = await db.register_workspace("/home/x/p2")
    await db.touch_workspace(w["id"])
    got = await db.get_workspace(w["id"])
    assert got["last_used_at"]


async def test_list_workspaces_orders_by_last_used(db):
    w1 = await db.register_workspace("/home/x/one")
    await db.register_workspace("/home/x/two")
    # Touch w1 so it becomes the most-recently-used.
    await db.touch_workspace(w1["id"])
    listed = await db.list_workspaces()
    assert listed[0]["id"] == w1["id"]


async def test_delete_workspace(db):
    w = await db.register_workspace("/home/x/del")
    await db.delete_workspace(w["id"])
    assert await db.get_workspace(w["id"]) is None
