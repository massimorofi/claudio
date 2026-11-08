"""Tests for the multi-LLM feature: profiles, roles, and delegation.

Covers the framework-free core added for issue #1 (Multiple LLM for Claudio):
``ModelProfile`` / ``RolesConfig`` resolution with ``llm`` fallback, dynamic
secret redaction for per-profile ``api_key``, and the ``delegate_task`` tool
contract (validation + passthrough to the injected runner).
"""
from __future__ import annotations

import pytest

from claudio_core.context import ToolContext
from claudio_core.settings import (
    REDACTED,
    AppConfig,
    ModelProfile,
    RolesConfig,
    redact_secrets,
    resolve_profile,
    restore_redacted_secrets,
    validate_role_references,
)
from claudio_core.tools.registry import TOOL_FUNCTIONS, select_tool_names
from claudio_core.tools.subagent import delegate_task


# ---- profile resolution ----------------------------------------------------

def test_resolve_profile_falls_back_to_llm_when_unset():
    cfg = AppConfig()
    prof = resolve_profile(cfg, "worker")
    assert prof.model == cfg.llm.model
    assert prof.base_url == cfg.llm.base_url
    assert prof.context_window == cfg.agent.context_window


def test_resolve_profile_uses_named_profile():
    cfg = AppConfig.model_validate({
        "model_profiles": {
            "qwen-fast": {
                "model": "qwen3-30b",
                "base_url": "http://x:1235/v1",
                "api_key": "k",
                "context_window": 131072,
                "price_input_per_mtok": 0.2,
                "price_output_per_mtok": 0.6,
            }
        },
        "roles": {"worker": "qwen-fast", "fast": "qwen-fast"},
    })
    assert resolve_profile(cfg, "worker").model == "qwen3-30b"
    assert resolve_profile(cfg, "fast").model == "qwen3-30b"
    # primary unset -> llm section
    assert resolve_profile(cfg, "primary").model == cfg.llm.model


def test_resolve_profile_unknown_role_falls_back():
    cfg = AppConfig()
    prof = resolve_profile(cfg, "nonexistent_role")
    assert prof.model == cfg.llm.model


# ---- role reference validation -------------------------------------------

def test_validate_role_references_ok_when_all_exist():
    cfg = AppConfig.model_validate({
        "model_profiles": {"fast1": {"model": "m"}},
        "roles": {"worker": "fast1", "fast": "fast1"},
    })
    assert validate_role_references(cfg) == []


def test_validate_role_references_flags_missing():
    cfg = AppConfig.model_validate({"roles": {"worker": "ghost"}})
    assert validate_role_references(cfg) == ["worker"]


def test_validate_role_references_unset_is_ok():
    cfg = AppConfig()
    assert validate_role_references(cfg) == []


def test_invalid_worker_turn_budget_is_rejected():
    with pytest.raises(ValueError):
        AppConfig.model_validate(
            {"agent": {"subagents": {"worker": {"max_turns": 0}}}}
        )


def test_invalid_profile_context_window_is_rejected():
    with pytest.raises(ValueError):
        AppConfig.model_validate(
            {"model_profiles": {"worker": {"model": "m", "context_window": 0}}}
        )


# ---- dynamic secret redaction --------------------------------------------

def test_redact_profile_api_keys():
    cfg = AppConfig.model_validate({
        "model_profiles": {
            "a": {"model": "m1", "api_key": "secret-a"},
            "b": {"model": "m2", "api_key": "secret-b"},
        }
    })
    red = redact_secrets(cfg.model_dump())
    assert red["model_profiles"]["a"]["api_key"] == REDACTED
    assert red["model_profiles"]["b"]["api_key"] == REDACTED


def test_restore_profile_api_keys():
    current = {
        "model_profiles": {"a": {"model": "m1", "api_key": "real-a"}},
        "llm": {"api_key": "real-llm"},
    }
    candidate = {
        "model_profiles": {"a": {"model": "m1", "api_key": REDACTED}},
        "llm": {"api_key": REDACTED},
    }
    out = restore_redacted_secrets(candidate, current)
    assert out["model_profiles"]["a"]["api_key"] == "real-a"
    assert out["llm"]["api_key"] == "real-llm"


def test_restore_new_profile_real_key_not_touched():
    # A profile added in this save carries a real key (not a placeholder);
    # restore must leave it alone.
    current = {"model_profiles": {}}
    candidate = {"model_profiles": {"new": {"model": "m", "api_key": "brand-new"}}}
    out = restore_redacted_secrets(candidate, current)
    assert out["model_profiles"]["new"]["api_key"] == "brand-new"


# ---- delegate_task contract ----------------------------------------------

def _ctx(**overrides):
    base = dict(
        config=AppConfig(),
        session_id="s1",
        workspace=__import__("pathlib").Path("/tmp"),
        approval_mode="off",
        db=None,
    )
    base.update(overrides)
    return ToolContext(**base)


@pytest.mark.asyncio
async def test_delegate_task_empty_task():
    res = await delegate_task(_ctx(), task="   ", agent="x")
    assert res["ok"] is False
    assert "non-empty" in res["error"]


@pytest.mark.asyncio
async def test_delegate_task_unknown_agent():
    res = await delegate_task(_ctx(), task="do it", agent="ghost")
    assert res["ok"] is False
    assert "unknown subagent" in res["error"]


@pytest.mark.asyncio
async def test_delegate_task_missing_factory():
    cfg = AppConfig.model_validate({"agent": {"subagents": {"w": {"description": "d"}}}})
    res = await delegate_task(_ctx(config=cfg), task="do it", agent="w")
    assert res["ok"] is False
    assert "agent_factory" in res["error"]


@pytest.mark.asyncio
async def test_delegate_task_success_passthrough():
    cfg = AppConfig.model_validate({"agent": {"subagents": {"w": {"description": "d"}}}})

    async def fake_factory(*, agent, task):
        return {"ok": True, "output": f"did {task} via {agent}", "usage": {"input_tokens": 3}}

    ctx = _ctx(config=cfg, agent_factory=fake_factory)
    res = await delegate_task(ctx, task="hello", agent="w")
    assert res["ok"] is True
    assert res["output"] == "did hello via w"
    assert res["usage"]["input_tokens"] == 3


def test_delegate_task_is_registered_and_selectable():
    assert "delegate_task" in TOOL_FUNCTIONS
    assert select_tool_names(["delegate_task"]) == ["delegate_task"]


@pytest.mark.asyncio
async def test_delegate_task_factory_raises():
    cfg = AppConfig.model_validate({"agent": {"subagents": {"w": {"description": "d"}}}})

    async def boom(*, agent, task):
        raise RuntimeError("kaboom")

    ctx = _ctx(config=cfg, agent_factory=boom)
    res = await delegate_task(ctx, task="x", agent="w")
    assert res["ok"] is False
    assert "kaboom" in res["error"]


# ---- roster rendering -----------------------------------------------------

def test_roster_empty_when_no_subagents():
    from claudio_core.instructions import render_subagent_roster
    assert render_subagent_roster(AppConfig()) == ""


def test_roster_lists_subagents():
    from claudio_core.instructions import render_subagent_roster
    cfg = AppConfig.model_validate({
        "agent": {
            "subagents": {
                "script_writer": {"description": "writes scripts", "tools": ["write_file"]},
            }
        }
    })
    block = render_subagent_roster(cfg)
    assert "script_writer" in block
    assert "writes scripts" in block
    assert "delegate_task" in block
