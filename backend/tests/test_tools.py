"""Tests for file tools, registry approval logic, and event translation."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from claudio.settings import AppConfig
from claudio.agent.context import ToolContext
from claudio.agent.tools import files as file_tools
from claudio.agent.tools.registry import build_tools, TOOL_META
from claudio.runs.events import translate_stream_event
from agents import RawResponsesStreamEvent, RunItemStreamEvent, AgentUpdatedStreamEvent


@pytest.fixture
def tctx(tmp_path: Path) -> ToolContext:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    cfg = AppConfig()
    cfg.agent.workspace_dir = str(workspace)
    return ToolContext(
        config=cfg,
        session_id="test-session",
        workspace=workspace,
        approval_mode="on_request",
        db=None,  # not used by file tools except todo
    )


def _ctx(tctx: ToolContext):
    return SimpleNamespace(context=tctx)


# ---- file tools ---------------------------------------------------------

async def test_write_and_read(tctx):
    res = await file_tools.write_file(_ctx(tctx), "a.txt", "hello")
    assert res["ok"] is True
    read = await file_tools.read_file(_ctx(tctx), "a.txt")
    assert read["ok"] is True
    assert "hello" in read["content"]


async def test_edit_file(tctx):
    await file_tools.write_file(_ctx(tctx), "b.txt", "foo bar baz")
    res = await file_tools.edit_file(
        _ctx(tctx), "b.txt", "bar", "qux"
    )
    assert res["ok"] is True
    read = await file_tools.read_file(_ctx(tctx), "b.txt")
    assert "qux" in read["content"]


async def test_edit_requires_unique(tctx):
    await file_tools.write_file(_ctx(tctx), "c.txt", "x x x")
    res = await file_tools.edit_file(_ctx(tctx), "c.txt", "x", "y")
    assert res["ok"] is False


async def test_edit_replace_all(tctx):
    await file_tools.write_file(_ctx(tctx), "d.txt", "x x x")
    res = await file_tools.edit_file(
        _ctx(tctx), "d.txt", "x", "y", replace_all=True
    )
    assert res["ok"] is True
    assert res["replacements"] == 3


async def test_backup_created_on_edit(tctx):
    await file_tools.write_file(_ctx(tctx), "e.txt", "v1")
    await file_tools.edit_file(_ctx(tctx), "e.txt", "v1", "v2")
    # Rewind should restore v1.
    res = await file_tools.rewind_files(_ctx(tctx), "e.txt")
    assert res["ok"] is True
    read = await file_tools.read_file(_ctx(tctx), "e.txt")
    assert "v1" in read["content"]


async def test_read_missing_file(tctx):
    res = await file_tools.read_file(_ctx(tctx), "nope.txt")
    assert res["ok"] is False


async def test_list_dir(tctx):
    await file_tools.write_file(_ctx(tctx), "f.txt", "x")
    res = await file_tools.list_dir(_ctx(tctx), ".")
    assert res["ok"] is True
    names = [e["name"] for e in res["entries"]]
    assert "f.txt" in names


# ---- registry approval logic -------------------------------------------

async def test_approval_destructive_on_request(tctx):
    meta = TOOL_META["write_file"]
    checker = _make_checker(meta)
    needs = await checker(_ctx(tctx), {"path": "x.txt"}, "call1")
    assert needs is True


async def test_approval_readonly_on_request(tctx):
    meta = TOOL_META["read_file"]
    checker = _make_checker(meta)
    needs = await checker(_ctx(tctx), {"path": "x.txt"}, "call1")
    assert needs is False


async def test_approval_off_never(tctx):
    tctx.approval_mode = "off"
    meta = TOOL_META["run_command"]
    checker = _make_checker(meta)
    needs = await checker(_ctx(tctx), {"command": "ls"}, "call1")
    assert needs is False


async def test_approval_always(tctx):
    tctx.approval_mode = "always"
    meta = TOOL_META["read_file"]
    checker = _make_checker(meta)
    needs = await checker(_ctx(tctx), {"path": "x.txt"}, "call1")
    assert needs is True


def _make_checker(meta):
    from claudio.agent.tools.registry import _make_approval_check

    return _make_approval_check(meta)


def test_build_tools_filters_unknown():
    tools = build_tools(["read_file", "nonexistent"])
    names = [t.name for t in tools]
    assert "read_file" in names
    assert "nonexistent" not in names


def test_build_tools_disabled():
    tools = build_tools(["read_file", "write_file"], disabled=["write_file"])
    names = [t.name for t in tools]
    assert "write_file" not in names


# ---- event translation -------------------------------------------------

def test_translate_text_delta():
    raw = SimpleNamespace(type="response.output_text.delta", delta="hi")
    evt = RawResponsesStreamEvent(data=raw)
    out = translate_stream_event(evt)
    assert out[0]["event"] == "token"
    assert out[0]["data"]["text"] == "hi"


def test_translate_tool_call():
    item = SimpleNamespace(call_id="c1", tool_name="read_file", raw_input={"path": "x"})
    evt = RunItemStreamEvent(name="tool_called", item=item)
    out = translate_stream_event(evt)
    assert out[0]["event"] == "tool_call"
    assert out[0]["data"]["name"] == "read_file"


def test_translate_tool_result_error():
    item = SimpleNamespace(call_id="c1", tool_name="x", output={"ok": False, "error": "boom"})
    evt = RunItemStreamEvent(name="tool_output", item=item)
    out = translate_stream_event(evt)
    assert out[0]["event"] == "tool_result"
    assert out[0]["data"]["is_error"] is True


def test_translate_agent_updated():
    agent = SimpleNamespace(name="explorer")
    evt = AgentUpdatedStreamEvent(new_agent=agent)
    out = translate_stream_event(evt)
    assert out[0]["event"] == "handoff"
