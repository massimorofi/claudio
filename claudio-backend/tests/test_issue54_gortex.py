"""Tests for Gortex code discovery wiring (issue #54, Option B).

Option B runs the Gortex daemon as its own container (compose profile "gortex")
that serves the MCP Streamable HTTP transport at ``http://gortex:7411/mcp``. The
agent connects over the internal bridge rather than spawning a stdio child.

Covers:
* the live ``config.yaml`` points the gortex entry at the HTTP endpoint and has it
  enabled (this deployment runs Option B);
* the committed ``config.example.yaml`` template keeps it disabled so a fresh
  deploy that does not run the container is unaffected;
* a disabled gortex server is skipped by ``_ensure_mcp`` (no toolset, no catalog
  entry) so the codebase-lookup policy stays out of the prompt until enabled;
* an enabled gortex server reaches the prompt end-to-end (config -> engine
  catalog -> static instruction);
* ``compose.yaml`` defines the gortex service the config points at (profile,
  network, /mcp healthcheck, matching host+port).

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_issue54_gortex.py -q
"""
from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import yaml

from claudio_core.db import Database
from claudio_core.settings import load_config

BACKEND_BASE = Path(__file__).resolve().parents[1]
CONFIG_PATH = BACKEND_BASE / "config" / "config.yaml"
EXAMPLE_PATH = BACKEND_BASE / "config" / "config.example.yaml"
COMPOSE_PATH = BACKEND_BASE.parent / "compose.yaml"


def _entry(path, sid="gortex"):
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    servers = (cfg.get("mcp") or {}).get("servers") or []
    return next((s for s in servers if s.get("id") == sid), None)


# --------------------------------------------------------------------------
# config: live (enabled, HTTP) + template (disabled, HTTP)
# --------------------------------------------------------------------------

def test_live_config_gortex_uses_http_endpoint():
    entry = _entry(CONFIG_PATH)
    assert entry is not None, "config.yaml has no gortex MCP server"
    assert entry["transport"] == "streamable_http"
    assert entry["url"].endswith("/mcp")
    assert urlparse(entry["url"]).hostname == "gortex"  # compose service name


def test_live_config_gortex_enabled():
    assert _entry(CONFIG_PATH)["enabled"] is True


def test_example_template_gortex_disabled():
    entry = _entry(EXAMPLE_PATH)
    assert entry is not None, "config.example.yaml has no gortex MCP server"
    assert entry["enabled"] is False, "template must ship gortex disabled"
    assert entry["transport"] == "streamable_http"


def test_gortex_entry_parses_as_mcpserverentry():
    cfg = load_config(CONFIG_PATH)
    gx = [s for s in cfg.mcp.servers if s.id == "gortex"]
    assert len(gx) == 1
    assert gx[0].transport == "streamable_http"
    assert gx[0].url and gx[0].url.endswith("/mcp")
    assert gx[0].enabled is True


# --------------------------------------------------------------------------
# engine: disabled skipped, enabled reaches the prompt
# --------------------------------------------------------------------------

def test_disabled_gortex_is_skipped_by_engine(monkeypatch):
    from claudio_backend.engine import adk_engine
    from claudio_backend.engine.adk_engine import AdkEngine

    class _Boom:
        def __init__(self, **kw):  # must never be constructed for a disabled server
            raise AssertionError("disabled gortex must not build a toolset")
        async def get_tools(self):  # pragma: no cover
            raise AssertionError

    monkeypatch.setattr(adk_engine, "McpToolset", _Boom)
    cfg = load_config(CONFIG_PATH)
    for s in cfg.mcp.servers:          # force gortex off for this test
        if s.id == "gortex":
            s.enabled = False
    eng = AdkEngine(config=cfg, db=Database(tempfile.mktemp(suffix=".db")))
    asyncio.run(eng._ensure_mcp("off"))
    assert all(e["id"] != "gortex" for e in eng._mcp_entries)


def test_end_to_end_enabled_gortex_reaches_prompt(monkeypatch):
    """config -> engine catalog -> system prompt: the policy appears when enabled."""
    import types
    from google.adk.tools.base_toolset import BaseToolset
    from claudio_backend.engine import adk_engine
    from claudio_backend.engine.adk_engine import AdkEngine
    from claudio_core.instructions import build_static_instructions

    class _GortexToolset(BaseToolset):
        def __init__(self, **kw):
            super().__init__(tool_name_prefix=kw.get("tool_name_prefix"))
        async def get_tools(self, readonly_context=None):
            return [types.SimpleNamespace(name="search_symbols")]
        async def list_resources(self, readonly_context=None):
            return []
        async def close(self):
            pass

    monkeypatch.setattr(adk_engine, "McpToolset", _GortexToolset)
    cfg = load_config(CONFIG_PATH)  # gortex already enabled (streamable_http)
    eng = AdkEngine(config=cfg, db=Database(tempfile.mktemp(suffix=".db")))
    asyncio.run(eng._ensure_mcp("off"))
    assert any("gortex" in e["id"] for e in eng._mcp_entries)
    prompt = build_static_instructions(
        cfg, "sess-1", mcp_entries=eng._mcp_entries,
        workspace=Path(cfg.agent.default_workspace_dir),
    )
    assert "Codebase lookup policy" in prompt


# --------------------------------------------------------------------------
# compose: the service the config points at actually exists
# --------------------------------------------------------------------------

def test_compose_defines_gortex_service():
    d = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    gx = d["services"]["gortex"]
    assert "gortex" in gx["profiles"]                 # opt-in profile
    assert "claudio-net" in gx["networks"]            # internal bridge only
    assert "ports" not in gx, "gortex must not publish a host port"
    # healthcheck probes the /mcp endpoint
    assert any("/mcp" in str(part) for part in gx["healthcheck"]["test"])
    # the config URL host+port match the service and its healthcheck port
    url = urlparse(_entry(CONFIG_PATH)["url"])
    assert url.hostname == "gortex"
    assert str(url.port) in str(gx["healthcheck"]["test"])


# --------------------------------------------------------------------------
# auth: Gortex requires a bearer token for its non-localhost /mcp bind
# --------------------------------------------------------------------------

def test_gortex_config_sends_auth_header():
    entry = _entry(CONFIG_PATH)
    assert entry.get("headers", {}).get("Authorization", "").startswith("Bearer ")


def test_connection_params_forward_headers():
    from claudio_backend.engine.adk_engine import _mcp_connection_params
    from claudio_core.settings import MCPServerEntry

    e = MCPServerEntry(
        id="gortex", transport="streamable_http", url="http://gortex:7411/mcp",
        headers={"Authorization": "Bearer tok"},
    )
    params = _mcp_connection_params(e)
    assert params.headers == {"Authorization": "Bearer tok"}


def test_connection_params_no_headers_defaults_none():
    from claudio_backend.engine.adk_engine import _mcp_connection_params
    from claudio_core.settings import MCPServerEntry

    e = MCPServerEntry(id="x", transport="streamable_http", url="http://x/mcp")
    params = _mcp_connection_params(e)
    assert params.headers is None  # empty dict -> None (unchanged behaviour)
