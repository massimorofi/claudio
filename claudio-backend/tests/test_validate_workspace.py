"""Tests for ``validate_workspace_dir`` (Phase 2 security validation).

A workspace is exactly what the agent's file tools can read and write, so the
candidate path is validated, not trusted. These cover the resolution and the
deny/allow rules directly.
"""
from __future__ import annotations

import pytest

from claudio_backend.paths import (
    WorkspaceValidationError,
    backend_base,
    validate_workspace_dir,
)


# ---- resolution ---------------------------------------------------


def test_relative_path_anchors_to_backend_base():
    resolved = validate_workspace_dir(
        "./data/ws", allowed_roots=[str(backend_base())], must_exist=False
    )
    assert resolved == (backend_base() / "data" / "ws").resolve()


def test_absolute_path_used_as_is(tmp_path):
    resolved = validate_workspace_dir(
        str(tmp_path), allowed_roots=[str(tmp_path.parent)], must_exist=True
    )
    assert resolved == tmp_path.resolve()


def test_tilde_is_expanded():
    resolved = validate_workspace_dir("~/some-ws", allowed_roots=[], must_exist=False)
    assert "~" not in str(resolved)
    assert resolved.is_absolute()


def test_trailing_slash_is_normalised(tmp_path):
    resolved = validate_workspace_dir(
        str(tmp_path) + "/", allowed_roots=[str(tmp_path.parent)], must_exist=True
    )
    assert resolved == tmp_path.resolve()


def test_symlink_resolves_to_real_target(tmp_path):
    """A symlinked path resolves to its target before the allowlist check.

    Mirrors the ``/home/briggen/Dev`` -> ``/run/media/briggen/DEV`` case: the
    allowlist names the real path, the candidate arrives via the symlink, and
    they must still match.
    """
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    resolved = validate_workspace_dir(
        str(link), allowed_roots=[str(real)], must_exist=True
    )
    assert resolved == real.resolve()


# ---- existence rules ---------------------------------------------


def test_nonexistent_path_raises_when_required(tmp_path):
    missing = tmp_path / "nope"
    with pytest.raises(WorkspaceValidationError):
        validate_workspace_dir(str(missing), allowed_roots=[], must_exist=True)


def test_file_instead_of_directory_raises(tmp_path):
    f = tmp_path / "afile.txt"
    f.write_text("x")
    with pytest.raises(WorkspaceValidationError):
        validate_workspace_dir(str(f), allowed_roots=[], must_exist=True)


def test_empty_path_raises():
    with pytest.raises(WorkspaceValidationError):
        validate_workspace_dir("   ", allowed_roots=[], must_exist=False)


# ---- dangerous-root backstop -------------------------------------


def test_filesystem_root_refused():
    with pytest.raises(WorkspaceValidationError):
        validate_workspace_dir("/", allowed_roots=[], must_exist=False)


def test_home_refused():
    with pytest.raises(WorkspaceValidationError):
        validate_workspace_dir("/home", allowed_roots=[], must_exist=False)


def test_backend_data_dir_refused():
    data_dir = (backend_base() / "data").resolve()
    with pytest.raises(WorkspaceValidationError):
        validate_workspace_dir(str(data_dir), allowed_roots=[], must_exist=False)


# ---- allowlist ----------------------------------------------------


def test_disallowed_root_raises(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    with pytest.raises(WorkspaceValidationError):
        validate_workspace_dir(str(outside), allowed_roots=[str(allowed)], must_exist=True)


def test_allowed_root_passes(tmp_path):
    allowed = tmp_path / "allowed"
    inner = allowed / "proj"
    inner.mkdir(parents=True)
    resolved = validate_workspace_dir(
        str(inner), allowed_roots=[str(allowed)], must_exist=True
    )
    assert resolved == inner.resolve()


def test_empty_allowlist_disables_check(tmp_path):
    # With no allowlist, any non-dangerous existing directory passes.
    resolved = validate_workspace_dir(str(tmp_path), allowed_roots=[], must_exist=True)
    assert resolved == tmp_path.resolve()
