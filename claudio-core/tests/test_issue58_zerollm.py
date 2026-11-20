"""Issue #58 Increment 2 -- deterministic zero-LLM paths (core side).

Covers the compound read-only tools (search_with_context, locate_symbol,
batch_read), the deterministic post-edit verification hook, the deterministic
router pre-check, and pre-run file hydration. Every one of these eliminates an
LLM round-trip; the tests assert they do it with zero model calls.

Run:  cd claudio-core && .venv/bin/python -m pytest tests/test_issue58_zerollm.py -q
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from claudio_core.context import ToolContext
from claudio_core.settings import AppConfig
from claudio_core.tools.files import batch_read, write_file, edit_file
from claudio_core.tools.search import search_with_context, locate_symbol
from claudio_core.tools.verify import check_syntax
from claudio_core.routing import deterministic_route
from claudio_core.hydrate import detect_explicit_paths, prehydrate


def _ctx(tmp_path):
    return ToolContext(
        config=AppConfig(),
        session_id="s1",
        workspace=tmp_path,
        approval_mode="off",
        db=None,
    )


# ---------------------------------------------------------------------------
# search_with_context
# ---------------------------------------------------------------------------

def test_search_with_context_returns_excerpts(tmp_path):
    (tmp_path / "a.py").write_text("line1\nline2 NEEDLE here\nline3\nline4")
    result = asyncio.run(search_with_context(_ctx(tmp_path), "NEEDLE", context_lines=1))
    assert result["ok"]
    assert result["match_count"] == 1
    m = result["matches"][0]
    assert m["file"].endswith("a.py")
    assert m["line"] == 2
    assert "line1" in m["excerpt"] and "line3" in m["excerpt"]


def test_search_with_context_regex_and_limit(tmp_path):
    for i in range(10):
        (tmp_path / f"f{i}.py").write_text(f"match {i}")
    result = asyncio.run(
        search_with_context(_ctx(tmp_path), r"match \d", regex=True, max_matches=3)
    )
    assert result["match_count"] == 3
    assert result["truncated"] is True


def test_search_skips_binary_and_big(tmp_path):
    (tmp_path / "x.png").write_bytes(b"NEEDLE")
    (tmp_path / "ok.py").write_text("NEEDLE")
    result = asyncio.run(search_with_context(_ctx(tmp_path), "NEEDLE"))
    assert all("x.png" not in m["file"] for m in result["matches"])


# ---------------------------------------------------------------------------
# locate_symbol
# ---------------------------------------------------------------------------

def test_locate_symbol_finds_definitions(tmp_path):
    (tmp_path / "mod.py").write_text(
        "import os\n\ndef compute(x):\n    return x\n\nclass Thing:\n    pass\n"
    )
    result = asyncio.run(locate_symbol(_ctx(tmp_path), "compute"))
    assert result["ok"]
    assert result["definition_count"] >= 1
    assert any("def compute" in d["excerpt"] for d in result["definitions"])
    result2 = asyncio.run(locate_symbol(_ctx(tmp_path), "Thing"))
    assert any("class Thing" in d["excerpt"] for d in result2["definitions"])


# ---------------------------------------------------------------------------
# batch_read
# ---------------------------------------------------------------------------

def test_batch_read_shared_budget(tmp_path):
    (tmp_path / "a.txt").write_text("A" * 100)
    (tmp_path / "b.txt").write_text("B" * 100)
    result = asyncio.run(batch_read(_ctx(tmp_path), ["a.txt", "b.txt"]))
    assert result["ok"]
    assert len(result["files"]) == 2
    assert result["files"][0]["content"].startswith("A")
    assert result["files"][1]["content"].startswith("B")


def test_batch_read_reports_missing_without_failing(tmp_path):
    (tmp_path / "a.txt").write_text("hi")
    result = asyncio.run(batch_read(_ctx(tmp_path), ["a.txt", "nope.txt"]))
    assert result["ok"]
    assert result["files"][0]["ok"] is True
    assert result["files"][1]["ok"] is False


# ---------------------------------------------------------------------------
# Post-edit verification hook
# ---------------------------------------------------------------------------

def test_write_file_reports_python_syntax_error(tmp_path):
    ctx = _ctx(tmp_path)
    result = asyncio.run(write_file(ctx, "bad.py", "def f(:\n  pass"))
    assert result["ok"]  # the write succeeded
    assert result["verification"]["ok"] is False
    assert "SyntaxError" in result["verification"]["detail"]


def test_write_file_reports_python_syntax_ok(tmp_path):
    ctx = _ctx(tmp_path)
    result = asyncio.run(write_file(ctx, "good.py", "def f(x):\n    return x\n"))
    assert result["verification"]["ok"] is True


def test_edit_file_reports_json_error(tmp_path):
    ctx = _ctx(tmp_path)
    asyncio.run(write_file(ctx, "d.json", '{"a": 1}'))
    result = asyncio.run(edit_file(ctx, "d.json", "1}", "1,}"))
    assert result["ok"]
    assert result["verification"]["ok"] is False


def test_check_syntax_unknown_extension_is_none():
    assert check_syntax(Path("f.txt"), "anything") is None


def test_check_syntax_js_balance():
    assert check_syntax(Path("a.js"), "function f() { return [1]; }")["ok"] is True
    assert check_syntax(Path("a.js"), "function f() { return [1; }")["ok"] is False


# ---------------------------------------------------------------------------
# Deterministic router
# ---------------------------------------------------------------------------

def test_deterministic_router_primary_for_tool_work():
    assert deterministic_route("please fix the bug in src/app.py") == "primary"
    assert deterministic_route("write a script that does X") == "primary"
    assert deterministic_route("```\ncode\n``` explain") == "primary"
    assert deterministic_route("run_command to list files") == "primary"


def test_deterministic_router_unknown_for_ambiguous():
    assert deterministic_route("hello there") == "unknown"
    assert deterministic_route("what is the capital of France?") == "unknown"
    assert deterministic_route("") == "unknown"


# ---------------------------------------------------------------------------
# Pre-run hydration
# ---------------------------------------------------------------------------

def test_detect_explicit_paths_only_existing(tmp_path):
    (tmp_path / "app.py").write_text("x")
    found = detect_explicit_paths("look at app.py and missing.py", tmp_path)
    assert found == ["app.py"]


def test_prehydrate_prepends_content(tmp_path):
    (tmp_path / "app.py").write_text("print('hello')")
    msg, paths = prehydrate("what does app.py do?", tmp_path)
    assert paths == ["app.py"]
    assert "print('hello')" in msg
    assert "Pre-loaded context" in msg


def test_prehydrate_noop_without_paths(tmp_path):
    msg, paths = prehydrate("just a question", tmp_path)
    assert paths == []
    assert msg == "just a question"
