"""Phase 4: POST /sessions creates a workspace-bound session.

The VS Code extension now creates its own session (``POST /sessions``) instead
of writing ``agent.workspace_dir`` to the shared config, so each window owns
its workspace and cannot clobber another window's. These tests drive the
endpoint function directly with a minimal fake request — the same auth +
validation path the real ASGI app exercises, without booting uvicorn.
"""
from __future__ import annotations

import types

import pytest

from claudio_core.db import Database
from claudio_core.settings import AppConfig
from claudio_backend.api.sessions import create_session as create_session_endpoint
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


def _sup(db, default, allowed=None):
    """Minimal supervisor stand-in: just the config + db the endpoint reads."""
    config = AppConfig(
        server={"auth_token": TOKEN},
        agent={
            "default_workspace_dir": str(default),
            "workspace_allowed_roots": allowed if allowed is not None else [],
        },
    )
    return types.SimpleNamespace(config=config, db=db)


async def test_creates_session_bound_to_requested_workspace(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    req = _Req({"workspace_dir": str(ws), "origin": "vscode"})
    req.app.state.supervisor = _sup(db, tmp_path / "default")
    res = await create_session_endpoint(req)
    session = res["session"]
    assert session["id"]
    assert session["workspace_dir"] == str(ws.resolve())
    assert session["workspace_origin"] == "vscode"
    # The workspace is registered too, so the picker's recent list sees it.
    registered = await db.list_workspaces()
    assert str(ws.resolve()) in {w["path"] for w in registered}
    await db.close()


async def test_falls_back_to_default_when_no_workspace_given(tmp_path):
    default = tmp_path / "the_default"
    default.mkdir()
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    req = _Req({})
    req.app.state.supervisor = _sup(db, default)
    res = await create_session_endpoint(req)
    assert res["session"]["workspace_dir"] == str(default.resolve())
    await db.close()


async def test_origin_defaults_to_vscode(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    req = _Req({"workspace_dir": str(ws)})
    req.app.state.supervisor = _sup(db, tmp_path / "default")
    res = await create_session_endpoint(req)
    assert res["session"]["workspace_origin"] == "vscode"
    await db.close()


async def test_rejects_workspace_outside_allowlist(tmp_path):
    inside = tmp_path / "allowed" / "proj"
    inside.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    req = _Req({"workspace_dir": str(outside)})
    req.app.state.supervisor = _sup(db, tmp_path / "default", allowed=[str(inside)])
    try:
        with pytest.raises(ApiError) as ei:
            await create_session_endpoint(req)
        assert ei.value.status == 422
    finally:
        await db.close()


async def test_rejects_missing_auth(tmp_path):
    ws = tmp_path / "proj"
    ws.mkdir()
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    req = _Req({"workspace_dir": str(ws)}, token="wrong")
    req.app.state.supervisor = _sup(db, tmp_path / "default")
    try:
        with pytest.raises(ApiError) as ei:
            await create_session_endpoint(req)
        assert ei.value.status == 401
    finally:
        await db.close()
