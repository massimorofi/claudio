from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from claudio_backend.engine.adk_engine import (
    AdkEngine,
    _clip_subagent_output,
)
from claudio_backend.runs.manager import (
    RunManager,
    RunRecord,
    _combined_usage,
    _compute_cost,
)
from claudio_core.engine import ModelSpec, RunRequest, ToolPolicy
from claudio_core.settings import AppConfig


def _config():
    return AppConfig.model_validate(
        {
            "llm": {
                "model": "primary-legacy",
                "base_url": "http://primary/v1",
                "api_key": "primary-key",
                "price_input_per_mtok": 1.0,
                "price_output_per_mtok": 2.0,
            },
            "model_profiles": {
                "worker": {
                    "model": "worker-model",
                    "base_url": "http://worker/v1",
                    "api_key": "worker-key",
                    "max_tokens": 8192,
                    "context_window": 1024,
                    "price_input_per_mtok": 0.1,
                    "price_output_per_mtok": 0.2,
                },
                "primary-profile": {
                    "model": "named-primary",
                    "base_url": "http://named-primary/v1",
                    "api_key": "named-primary-key",
                    "max_tokens": 8192,
                    "context_window": 4096,
                    "price_input_per_mtok": 3.0,
                    "price_output_per_mtok": 4.0,
                },
            },
            "roles": {
                "primary": "primary-profile",
                "worker": "worker",
                "fast": "worker",
            },
            "agent": {
                "tools": {"enabled": ["delegate_task"]},
                "subagents": {
                    "coder": {
                        "tools": ["read_file", "write_file", "run_command"],
                        "max_turns": 4,
                    }
                },
            },
        }
    )


def test_model_spec_uses_primary_role_and_keeps_model_override():
    manager = RunManager.__new__(RunManager)
    manager.config = _config()

    spec = manager._model_spec()
    assert spec.model == "named-primary"
    assert spec.base_url == "http://named-primary/v1"
    assert spec.api_key == "named-primary-key"
    assert spec.max_tokens == 4096
    assert spec.price_input_per_mtok == 3.0
    assert spec.price_output_per_mtok == 4.0

    overridden = manager._model_spec("explicit-override")
    assert overridden.model == "explicit-override"
    assert overridden.base_url == "http://named-primary/v1"


def test_model_spec_falls_back_to_legacy_llm():
    manager = RunManager.__new__(RunManager)
    manager.config = AppConfig.model_validate(
        {"llm": {"model": "legacy", "base_url": "http://legacy/v1"}}
    )

    assert manager._model_spec().model == "legacy"
    assert manager._model_spec().base_url == "http://legacy/v1"


def test_primary_cost_uses_primary_profile_rates():
    config = _config()
    assert _compute_cost(1_000_000, 1_000_000, config) == 7.0


def test_compaction_summariser_uses_fast_role_and_its_context_budget(monkeypatch):
    import claudio_backend.runs.manager as manager_module

    manager = RunManager.__new__(RunManager)
    manager.config = _config()
    manager.compactor = None
    captured = {}

    async def fake_completion(**kwargs):
        captured.update(kwargs)
        return "summary"

    monkeypatch.setattr(manager_module, "openai_chat_completion", fake_completion)
    assert asyncio.run(manager._build_summariser()("prompt")) == "summary"
    assert captured["model"] == "worker-model"
    assert captured["base_url"] == "http://worker/v1"
    assert captured["max_tokens"] == 1024


def test_delegation_approval_is_conditional_on_worker_tools():
    engine = AdkEngine(_config(), db=None)
    assert engine._delegation_needs_approval("on_request") is True
    assert engine._delegation_needs_approval("off") is False
    assert engine._delegation_needs_approval("always") is True

    engine.config.agent.subagents["coder"].tools = ["read_file"]
    assert engine._delegation_needs_approval("on_request") is False


def test_delegated_usage_is_recorded_and_included_in_final_totals():
    record = RunRecord("run", "session", workspace=Path("/tmp"))
    manager = RunManager.__new__(RunManager)
    manager.session_store = type("Store", (), {"append": lambda *args: None})()

    manager._track(
        record,
        "tool_result",
        {
            "name": "delegate_task",
            "output": json.dumps(
                {
                    "ok": True,
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cost_usd": 0.000014,
                    },
                }
            ),
        },
    )

    assert record.delegated_input_tokens == 100
    assert record.delegated_output_tokens == 20
    assert record.delegated_cost_usd == 0.000014
    assert _combined_usage(
        {"input_tokens": 1_000, "output_tokens": 200, "cost_usd": 0.003},
        record,
        _config(),
        "answer",
    ) == pytest.approx((1_100, 220, 0.003014))


def test_worker_output_is_capped_with_an_explicit_marker():
    assert _clip_subagent_output("short", 5) == "short"
    clipped = _clip_subagent_output("x" * 100, 80)
    assert len(clipped) == 80
    assert clipped.endswith("[Worker output truncated at 80 characters.]")


def test_worker_rejects_unknown_and_recursive_tools():
    config = _config()
    engine = AdkEngine(config, db=None)
    request = RunRequest(
        run_id="run",
        session_id="session",
        workspace=Path("/tmp"),
        instructions="",
        user_input="",
        model=ModelSpec(model="primary"),
        tools=ToolPolicy(),
    )

    config.agent.subagents["coder"].tools = ["missing_tool"]
    result = asyncio.run(
        engine._run_subagent(request=request, agent="coder", task="do a task")
    )
    assert result["ok"] is False
    assert "unknown tool 'missing_tool'" in result["error"]

    config.agent.subagents["coder"].tools = ["delegate_task"]
    result = asyncio.run(
        engine._run_subagent(request=request, agent="coder", task="do a task")
    )
    assert result["ok"] is False
    assert "cannot delegate recursively" in result["error"]
