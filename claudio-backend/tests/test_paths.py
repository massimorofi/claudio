"""Tests for the workspace-path anchoring helper.

The bug these guard against: ``workspace_dir`` is stored as a *relative* path
(``./data/workspace``) and was previously resolved with a bare
``Path(...).resolve()``, which anchors against the process cwd. The same logical
store then materialised wherever the backend happened to be launched. The helper
anchors a relative ``workspace_dir`` to the backend base instead, so the result
is independent of the launch directory.

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_paths.py -q
"""
from __future__ import annotations

import os
from pathlib import Path

from claudio_core.settings import AppConfig

from claudio_backend.paths import backend_base, resolve_workspace_dir


def _config_with_workspace(workspace_dir):
    return AppConfig.model_validate(
        {"agent": {"workspace_dir": workspace_dir}}
    )


def test_backend_base_is_claudio_backend():
    base = backend_base()
    assert base.name == "claudio-backend"
    assert (base / "src" / "claudio_backend").is_dir()


def test_absolute_workspace_passes_through():
    cfg = _config_with_workspace("/srv/claudio/ws")
    assert resolve_workspace_dir(cfg) == Path("/srv/claudio/ws")


def test_relative_workspace_anchors_to_backend_base():
    cfg = _config_with_workspace("./data/workspace")
    resolved = resolve_workspace_dir(cfg)
    assert resolved == (backend_base() / "data" / "workspace").resolve()
    assert resolved.is_absolute()


def test_relative_resolution_ignores_cwd(tmp_path):
    """Changing the process cwd must not change the resolved workspace."""
    cfg = _config_with_workspace("./data/workspace")
    original_cwd = os.getcwd()
    try:
        os.chdir(tmp_path)
        from_elsewhere = resolve_workspace_dir(cfg)
    finally:
        os.chdir(original_cwd)
    expected = (backend_base() / "data" / "workspace").resolve()
    assert from_elsewhere == expected


def test_tilde_is_expanded():
    cfg = _config_with_workspace("~/claudio-ws")
    resolved = resolve_workspace_dir(cfg)
    assert "~" not in str(resolved)
    assert resolved.is_absolute()
