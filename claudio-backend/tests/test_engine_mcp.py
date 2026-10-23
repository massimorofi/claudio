"""Tests for the engine's MCP wiring.

Covers the pieces added when MCP servers were wired into the ADK engine:

* ``_sanitize_mcp_prefix`` folds a server id into a model-safe tool prefix.
* ``_mcp_connection_params`` maps each configured transport to the matching
  ADK connection-params class, and rejects bad input.
* ``AdkEngine._ensure_mcp`` builds one toolset per *enabled* server, skips
  disabled servers, skips a server that fails to load (without raising), and
  rebuilds only when the (config, approval-mode) key changes.
* ``AdkEngine._build_agent`` appends the MCP toolsets and folds the MCP
  catalog into the static instruction.
* ``render_mcp_block`` renders the catalog and returns "" when empty.

No live MCP server is contacted -- ``McpToolset`` is monkeypatched.

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_engine_mcp.py -q
"""
from __future__ import annotations

import asyncio
import tempfile
import types
from pathlib import Path

import pytest
from google.adk.tools.base_toolset import BaseToolset

from claudio_core.context import ToolContext
from claudio_core.db import Database
from claudio_core.engine import ModelSpec, RunRequest, ToolPolicy
from claudio_core.instructions import render_mcp_block
from claudio_core.settings import AppConfig, MCPServerEntry

from claudio_backend.engine import adk_engine
from claudio_backend.engine.adk_engine import (
    AdkEngine,
    _mcp_connection_params,
    _sanitize_mcp_prefix,
    SseConnectionParams,
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _entry(server_id, transport="stdio", url=None, command=None, enabled=True):
    return MCPServerEntry(
        id=server_id,
        transport=transport,
        url=url,
        command=command or [],
        enabled=enabled,
    )


def _config(*, servers, workspace, skills_enabled=False, approval_mode="off"):
    return AppConfig.model_validate(
        {
            "agent": {
                "skills_enabled": skills_enabled,
                "workspace_dir": str(workspace),
                "approval_mode": approval_mode,
                "tools": {"enabled": ["read_file"], "disabled": []},
            },
            "mcp": {"servers": [s.model_dump() for s in servers]},
        }
    )


def _request(config, workspace, approval_mode="off"):
    return RunRequest(
        run_id="run-1",
        session_id="sess-1",
        workspace=Path(workspace),
        instructions="",
        user_input="hello",
        model=ModelSpec(model="fake-model", base_url="http://127.0.0.1:1/v1"),
        tools=ToolPolicy(enabled=("read_file",), approval_mode=approval_mode),
        max_turns=5,
    )


class _FakeToolset(BaseToolset):
    """Stand-in for McpToolset that never touches the network.

    Subclasses ``BaseToolset`` so it satisfies the ``tools`` union validation
    that ``LlmAgent`` performs (callable | BaseTool | BaseToolset).
    """

    def __init__(self, **kwargs):
        super().__init__(
            tool_filter=kwargs.get("tool_filter"),
            tool_name_prefix=kwargs.get("tool_name_prefix"),
        )
        self.kwargs = kwargs
        self.closed = False

    async def get_tools(self, readonly_context=None):
        # Raw, unprefixed names -- exactly what a real MCP server returns.
        # The engine prefixes them for the catalog to match what the model sees.
        return [
            types.SimpleNamespace(name="search"),
            types.SimpleNamespace(name="fetch"),
        ]

    async def list_resources(self, readonly_context=None):
        return ["res_a", "res_b"]

    async def close(self):
        self.closed = True


class _FailingToolset:
    """Stand-in whose tool listing blows up (simulates an unreachable server)."""

    def __init__(self, **kwargs):
        self.closed = False

    async def get_tools(self, readonly_context=None):
        raise ConnectionError("server down")

    async def list_resources(self, readonly_context=None):
        return []

    async def close(self):
        self.closed = True


class _NoResourcesToolset(BaseToolset):
    """Tools load fine, but the server has no resources capability.

    ``list_resources`` raises the way an MCP server without the resources
    capability answers ("Method not found"). The server must still contribute
    its tools -- only the resource catalog is empty.
    """

    def __init__(self, **kwargs):
        super().__init__(
            tool_filter=kwargs.get("tool_filter"),
            tool_name_prefix=kwargs.get("tool_name_prefix"),
        )
        self.closed = False

    async def get_tools(self, readonly_context=None):
        return [types.SimpleNamespace(name="do_thing")]

    async def list_resources(self, readonly_context=None):
        raise RuntimeError("Method not found: resources/list")

    async def close(self):
        self.closed = True


# --------------------------------------------------------------------------
# _sanitize_mcp_prefix
# --------------------------------------------------------------------------


def test_sanitize_prefix_folds_invalid_chars():
    assert _sanitize_mcp_prefix("SkillMD.com") == "SkillMD_com"
    assert _sanitize_mcp_prefix("my server!!") == "my_server"


def test_sanitize_prefix_empty_falls_back():
    assert _sanitize_mcp_prefix("...") == "mcp"
    assert _sanitize_mcp_prefix("") == "mcp"


# --------------------------------------------------------------------------
# _mcp_connection_params
# --------------------------------------------------------------------------


def test_connection_params_streamable_http():
    p = _mcp_connection_params(
        _entry("s1", "streamable_http", url="https://x/mcp")
    )
    assert isinstance(p, StreamableHTTPConnectionParams)
    assert p.url == "https://x/mcp"


def test_connection_params_sse():
    p = _mcp_connection_params(_entry("s1", "sse", url="http://x/sse"))
    assert isinstance(p, SseConnectionParams)
    assert p.url == "http://x/sse"


def test_connection_params_stdio():
    p = _mcp_connection_params(
        _entry("s1", "stdio", command=["npx", "-y", "x"])
    )
    assert isinstance(p, StdioConnectionParams)
    assert p.server_params.command == "npx"
    assert p.server_params.args == ["-y", "x"]


def test_connection_params_unknown_transport_raises():
    with pytest.raises(ValueError):
        _mcp_connection_params(_entry("s1", "bogus"))


def test_connection_params_http_missing_url_raises():
    with pytest.raises(ValueError):
        _mcp_connection_params(_entry("s1", "streamable_http", url=None))


def test_connection_params_stdio_missing_command_raises():
    with pytest.raises(ValueError):
        _mcp_connection_params(_entry("s1", "stdio", command=[]))


# --------------------------------------------------------------------------
# render_mcp_block
# --------------------------------------------------------------------------


def test_render_mcp_block_lists_names():
    block = render_mcp_block(
        [{"id": "alpha", "tools": ["t1", "t2"], "resources": ["r1"]}]
    )
    assert "Connected MCP servers" in block
    assert "alpha" in block
    assert "t1" in block and "t2" in block
    assert "r1" in block
    assert "load_mcp_resource" in block


def test_render_mcp_block_empty_returns_blank():
    assert render_mcp_block([]) == ""
    assert render_mcp_block(None) == ""


# --------------------------------------------------------------------------
# _ensure_mcp
# --------------------------------------------------------------------------


def test_ensure_mcp_builds_enabled_servers_only(monkeypatch):
    monkeypatch.setattr(adk_engine, "McpToolset", _FakeToolset)
    workspace = tempfile.mkdtemp()
    cfg = _config(
        servers=[
            _entry("a", "streamable_http", url="http://a", enabled=True),
            _entry("b", "streamable_http", url="http://b", enabled=False),
        ],
        workspace=workspace,
    )
    eng = AdkEngine(config=cfg, db=Database(tempfile.mktemp(suffix=".db")))
    asyncio.run(eng._ensure_mcp("off"))
    assert len(eng._mcp_toolsets) == 1
    assert eng._mcp_entries[0]["id"] == "a"
    # Catalog names carry the sanitized prefix, matching the model's view.
    assert eng._mcp_entries[0]["tools"] == ["a_search", "a_fetch"]
    assert eng._mcp_entries[0]["resources"] == ["res_a", "res_b"]


def test_ensure_mcp_skips_failing_server(monkeypatch):
    calls = {"n": 0}

    def factory(**kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return _FailingToolset(**kwargs)
        return _FakeToolset(**kwargs)

    monkeypatch.setattr(adk_engine, "McpToolset", factory)
    workspace = tempfile.mkdtemp()
    cfg = _config(
        servers=[
            _entry("bad", "streamable_http", url="http://bad", enabled=True),
            _entry("good", "streamable_http", url="http://good", enabled=True),
        ],
        workspace=workspace,
    )
    eng = AdkEngine(config=cfg, db=Database(tempfile.mktemp(suffix=".db")))
    # Must not raise even though the first server fails.
    asyncio.run(eng._ensure_mcp("off"))
    assert len(eng._mcp_toolsets) == 1
    assert eng._mcp_entries[0]["id"] == "good"


def test_ensure_mcp_keeps_server_without_resources(monkeypatch):
    """A server that fails only the resource listing keeps its tools."""
    monkeypatch.setattr(adk_engine, "McpToolset", _NoResourcesToolset)
    workspace = tempfile.mkdtemp()
    cfg = _config(
        servers=[_entry("nosr", "streamable_http", url="http://x", enabled=True)],
        workspace=workspace,
    )
    eng = AdkEngine(config=cfg, db=Database(tempfile.mktemp(suffix=".db")))
    asyncio.run(eng._ensure_mcp("off"))
    # The server is kept (its tools work), with an empty resource list.
    assert len(eng._mcp_toolsets) == 1
    assert eng._mcp_entries[0]["id"] == "nosr"
    assert eng._mcp_entries[0]["tools"] == ["nosr_do_thing"]
    assert eng._mcp_entries[0]["resources"] == []


def test_ensure_mcp_reuses_until_key_changes(monkeypatch):
    count = {"n": 0}

    def factory(**kwargs):
        count["n"] += 1
        return _FakeToolset(**kwargs)

    monkeypatch.setattr(adk_engine, "McpToolset", factory)
    workspace = tempfile.mkdtemp()
    cfg = _config(
        servers=[_entry("a", "streamable_http", url="http://a", enabled=True)],
        workspace=workspace,
    )
    eng = AdkEngine(config=cfg, db=Database(tempfile.mktemp(suffix=".db")))
    asyncio.run(eng._ensure_mcp("off"))
    assert count["n"] == 1
    asyncio.run(eng._ensure_mcp("off"))  # same key -> no rebuild
    assert count["n"] == 1
    asyncio.run(eng._ensure_mcp("always"))  # approval changed -> rebuild
    assert count["n"] == 2


def test_ensure_mcp_require_confirmation_tracks_approval(monkeypatch):
    captured = {}

    def factory(**kwargs):
        captured["require_confirmation"] = kwargs.get("require_confirmation")
        return _FakeToolset(**kwargs)

    monkeypatch.setattr(adk_engine, "McpToolset", factory)
    workspace = tempfile.mkdtemp()
    cfg = _config(
        servers=[_entry("a", "streamable_http", url="http://a", enabled=True)],
        workspace=workspace,
    )
    eng = AdkEngine(config=cfg, db=Database(tempfile.mktemp(suffix=".db")))
    asyncio.run(eng._ensure_mcp("always"))
    assert captured["require_confirmation"] is True
    asyncio.run(eng._ensure_mcp("off"))
    assert captured["require_confirmation"] is False


# --------------------------------------------------------------------------
# _build_agent includes MCP
# --------------------------------------------------------------------------


def test_build_agent_appends_mcp_toolsets_and_block(monkeypatch):
    monkeypatch.setattr(adk_engine, "McpToolset", _FakeToolset)
    workspace = tempfile.mkdtemp()
    cfg = _config(
        servers=[_entry("alpha", "streamable_http", url="http://a", enabled=True)],
        workspace=workspace,
    )
    eng = AdkEngine(config=cfg, db=Database(tempfile.mktemp(suffix=".db")))
    asyncio.run(eng._ensure_mcp("off"))
    ctx = ToolContext(
        config=cfg,
        session_id="sess-1",
        workspace=Path(workspace),
        approval_mode="off",
        db=eng.db,
        run_id="run-1",
    )
    agent = eng._build_agent(_request(cfg, workspace), ctx)
    assert any(isinstance(t, _FakeToolset) for t in agent.tools)
    instruction = (
        agent.instruction(None) if callable(agent.instruction) else agent.instruction
    )
    assert "Connected MCP servers" in instruction
    assert "alpha" in instruction
