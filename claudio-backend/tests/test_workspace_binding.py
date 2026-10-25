"""Phase 3 regression: a session keeps the workspace it was created with.

The core rule under test: on resume the session's stored workspace **always
wins**, even if the configured default has since changed. A silent fallback to
the new default is exactly the bug this change removes — a transcript whose
file paths refer to one tree while the tools operate on another.
"""
from __future__ import annotations

import pytest

from claudio_core.db import Database
from claudio_core.settings import AppConfig
from claudio_backend.api.chat import _bind_workspace
from claudio_backend.errors import ApiError


class _Sup:
    """Minimal stand-in for the supervisor: just config + db."""

    def __init__(self, config, db):
        self.config = config
        self.db = db


def _cfg(default, allowed=None):
    return AppConfig(
        agent={
            "default_workspace_dir": str(default),
            "workspace_allowed_roots": allowed if allowed is not None else [],
        }
    )


async def test_new_session_binds_requested_workspace(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    ws = tmp_path / "proj"
    ws.mkdir()
    path, wid = await _bind_workspace(
        _Sup(_cfg(tmp_path / "default"), db),
        {"workspace_dir": str(ws)},
        session_id="s1",
        is_new=True,
    )
    assert path == ws.resolve()
    assert wid
    await db.close()


async def test_resume_keeps_workspace_after_default_changes(tmp_path):
    """The headline regression: a changed default must not repoint a session."""
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    ws_a = tmp_path / "a"
    ws_a.mkdir()
    ws_b = tmp_path / "b"
    ws_b.mkdir()
    await db.create_session("s1", workspace_dir=str(ws_a), workspace_origin="webui")

    # The configured default now points somewhere else entirely.
    sup = _Sup(_cfg(ws_b), db)
    path, _wid = await _bind_workspace(sup, {}, session_id="s1", is_new=False)

    # Must still be A — the workspace the session was created with.
    assert path == ws_a.resolve()
    assert path != ws_b.resolve()
    await db.close()


async def test_resume_missing_workspace_raises_409(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    ws_a = tmp_path / "a"
    ws_a.mkdir()
    await db.create_session("s1", workspace_dir=str(ws_a), workspace_origin="webui")
    ws_a.rmdir()  # the bound folder is gone

    sup = _Sup(_cfg(tmp_path / "default"), db)
    with pytest.raises(ApiError) as ei:
        await _bind_workspace(sup, {}, session_id="s1", is_new=False)
    assert ei.value.status == 409
    assert ei.value.code == "workspace_unavailable"
    await db.close()


async def test_legacy_session_without_workspace_adopts_default(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    ws_b = tmp_path / "b"
    ws_b.mkdir()
    await db.create_session("s1")  # legacy: no workspace bound

    sup = _Sup(_cfg(ws_b), db)
    path, _wid = await _bind_workspace(sup, {}, session_id="s1", is_new=False)
    assert path == ws_b.resolve()
    # The row is backfilled so the binding is durable from here on.
    row = await db.get_session("s1")
    assert row["workspace_dir"] == str(ws_b.resolve())
    await db.close()


async def test_new_session_rejects_workspace_outside_allowlist(tmp_path):
    db = Database(tmp_path / "db.sqlite")
    await db.connect()
    inside = tmp_path / "allowed" / "proj"
    inside.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir()
    sup = _Sup(_cfg(tmp_path / "default", allowed=[str(inside)]), db)
    with pytest.raises(ApiError) as ei:
        await _bind_workspace(
            sup, {"workspace_dir": str(outside)}, session_id="s1", is_new=True
        )
    assert ei.value.status == 422
    await db.close()
