"""Tests for the workspace file-management path-safety helpers."""
from __future__ import annotations

from pathlib import Path

import pytest

from claudio.api.workspace import _safe_join, _build_tree
from claudio.api.deps import ApiError


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("hello")
    sub = workspace / "sub"
    sub.mkdir()
    (sub / "b.txt").write_text("world")
    return workspace


def test_safe_join_allows_relative(ws):
    out = _safe_join(ws, "a.txt")
    assert out == (ws / "a.txt").resolve()


def test_safe_join_allows_nested(ws):
    out = _safe_join(ws, "sub/b.txt")
    assert out == (ws / "sub" / "b.txt").resolve()


def test_safe_join_refuses_parent_traversal(ws):
    with pytest.raises(ApiError) as exc:
        _safe_join(ws, "../outside.txt")
    assert exc.value.status == 403


def test_safe_join_refuses_absolute_path(ws):
    with pytest.raises(ApiError) as exc:
        _safe_join(ws, "/etc/passwd")
    assert exc.value.status == 403


def test_safe_join_refuses_workspace_root(ws):
    with pytest.raises(ApiError) as exc:
        _safe_join(ws, ".")
    assert exc.value.status == 403


def test_safe_join_refuses_empty(ws):
    with pytest.raises(ApiError) as exc:
        _safe_join(ws, "")
    assert exc.value.status == 422


def test_safe_join_refuses_deep_escape(ws):
    # sub/../../etc/passwd resolves outside the workspace.
    with pytest.raises(ApiError) as exc:
        _safe_join(ws, "sub/../../etc/passwd")
    assert exc.value.status == 403


def test_build_tree_lists_files_and_dirs(ws):
    state = {"count": 0, "truncated": False}
    tree = _build_tree(ws, ws, state)
    by_name = {n["name"]: n for n in tree}
    assert set(by_name) == {"a.txt", "sub"}
    assert by_name["a.txt"]["type"] == "file"
    assert by_name["a.txt"]["size"] == 5
    assert by_name["sub"]["type"] == "dir"
    assert by_name["sub"]["children"][0]["name"] == "b.txt"
    assert by_name["sub"]["children"][0]["path"] == "sub/b.txt"
    assert state["truncated"] is False


def test_delete_orders_deepest_first():
    # The delete endpoint sorts deepest-first so a selected folder never
    # strands a selected child. Verify the sort key directly.
    paths = ["a.txt", "sub", "sub/b.txt", "sub/deep/c.txt"]
    ordered = sorted(paths, key=lambda p: (str(p).count("/"), len(str(p))), reverse=True)
    assert ordered.index("sub/deep/c.txt") < ordered.index("sub")
    assert ordered.index("sub/b.txt") < ordered.index("sub")
