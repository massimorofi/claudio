"""PATCH /sessions/{id} renames a session.

The extension pre-creates its session (``POST /sessions``) before any user
message exists, so the stored title is a placeholder. Once the first chat
message arrives it renames the session through this endpoint, restoring the
title-from-first-message behaviour the web UI gets for free from
``chat.chat_stream``. These tests drive the endpoint function directly with
a minimal fake request, matching ``test_create_session_endpoint``.
"""
from __future__ import annotations

import types

import pytest

from claudio_core.db import Database
from claudio_core.settings import AppConfig
from claudio_backend.api.sessions import (
    create_session as create_session_endpoint,
    patch_session as patch_session_endpoint,
)
from claudio_backend.errors import ApiError

TOKEN = "test-token"


class _Req:
    """Minimal stand-in for starlette's Request: headers + async json()."""

    def __init__(self, body, token=TOKEN):
        self._body = body
        self.headers = {"authorization": f"Bearer {token}"}
        self.app = types.SimpleNamespace(state=types.SimpleNamespace())

    async def json(self):
        return self._body


def _sup(db, default):
    config = AppConfig(
        server={"auth_token": TOKEN},
        agent={
            "default_workspace_dir": str(default),
            "workspace_allowed_roots": [],
        },
    )
    return types.SimpleNamespace(config=config, db=db)


async def _make_session(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    req = _Req({"workspace_dir": str(ws)})
    req.app.state.supervisor = _sup(db, tmp_path / "default")
    created = await create_session_endpoint(req)
    return db, created["session"]["id"]


async def test_patch_updates_title(tmp_path):
    db, sid = await _make_session(tmp_path)
    try:
        req = _Req({"title": "  hello world  "})
        req.app.state.supervisor = _sup(db, tmp_path / "default")
        res = await patch_session_endpoint(req, sid)
        assert res["session"]["title"] == "hello world"
        # The change is durable, not just echoed back.
        row = await db.get_session(sid)
        assert row["title"] == "hello world"
    finally:
        await db.close()


async def test_patch_rejects_empty_title(tmp_path):
    db, sid = await _make_session(tmp_path)
    try:
        req = _Req({"title": "   "})
        req.app.state.supervisor = _sup(db, tmp_path / "default")
        with pytest.raises(ApiError) as ei:
            await patch_session_endpoint(req, sid)
        assert ei.value.status == 422
    finally:
        await db.close()


async def test_patch_rejects_missing_title(tmp_path):
    db, sid = await _make_session(tmp_path)
    try:
        req = _Req({})
        req.app.state.supervisor = _sup(db, tmp_path / "default")
        with pytest.raises(ApiError) as ei:
            await patch_session_endpoint(req, sid)
        assert ei.value.status == 422
    finally:
        await db.close()


async def test_patch_unknown_session_404(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    try:
        req = _Req({"title": "whatever"})
        req.app.state.supervisor = _sup(db, tmp_path / "default")
        with pytest.raises(ApiError) as ei:
            await patch_session_endpoint(req, "no-such-session")
        assert ei.value.status == 404
    finally:
        await db.close()


async def test_patch_rejects_missing_auth(tmp_path):
    db, sid = await _make_session(tmp_path)
    try:
        req = _Req({"title": "x"}, token="wrong")
        req.app.state.supervisor = _sup(db, tmp_path / "default")
        with pytest.raises(ApiError) as ei:
            await patch_session_endpoint(req, sid)
        assert ei.value.status == 401
    finally:
        await db.close()
