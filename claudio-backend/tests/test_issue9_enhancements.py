from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from claudio_backend.engine.adk_engine import AdkEngine
from claudio_backend.model_health import ModelHealthRegistry
from claudio_backend.runs.manager import RunManager, RunRecord
from claudio_core.db import Database
from claudio_core.engine import ModelSpec, RunRequest, ToolPolicy
from claudio_core.metrics import Metrics
from claudio_core.session_store import SessionStore
from claudio_core.settings import AppConfig, SubagentConfig


def _config(**agent_overrides):
    return AppConfig.model_validate(
        {
            "llm": {"model": "primary-model", "base_url": "http://primary/v1"},
            "model_profiles": {
                "worker": {
                    "model": "worker-model",
                    "base_url": "http://worker/v1",
                    "context_window": 4096,
                },
                "fast": {
                    "model": "fast-model",
                    "base_url": "http://fast/v1",
                    "context_window": 2048,
                },
            },
            "roles": {"worker": "worker", "fast": "fast"},
            "agent": {
                "auto_routing_enabled": True,
                **agent_overrides,
            },
        }
    )


def test_optional_features_default_off_and_approval_mode_is_validated():
    config = AppConfig()
    assert config.agent.auto_routing_enabled is False
    assert config.agent.subagent_approval_mode == "one_gate"
    assert (
        AppConfig(
            agent={"subagent_approval_mode": "passthrough"}
        ).agent.subagent_approval_mode
        == "passthrough"
    )
    with pytest.raises(ValidationError):
        AppConfig(agent={"subagent_approval_mode": "invalid"})


def test_auto_routing_only_uses_fast_for_explicit_fast_decision(monkeypatch):
    import claudio_backend.runs.manager as manager_module

    manager = RunManager.__new__(RunManager)
    manager.config = _config()
    manager.profile_health = ModelHealthRegistry()
    calls = []

    async def classify(**kwargs):
        calls.append(kwargs)
        return "FAST"

    monkeypatch.setattr(manager_module, "openai_chat_completion", classify)
    assert asyncio.run(manager._auto_route_role("What is 2 + 2?")) == "fast"
    assert calls[0]["model"] == "fast-model"
    assert calls[0]["max_tokens"] == 512

    async def uncertain(**kwargs):
        return "something else"

    monkeypatch.setattr(manager_module, "openai_chat_completion", uncertain)
    assert asyncio.run(manager._auto_route_role("Explain a complicated task")) == "primary"


def test_auto_routing_falls_back_to_primary_when_classifier_fails(monkeypatch):
    import claudio_backend.runs.manager as manager_module

    manager = RunManager.__new__(RunManager)
    manager.config = _config()
    manager.profile_health = ModelHealthRegistry()

    async def fail(**kwargs):
        raise RuntimeError("fast endpoint unavailable")

    monkeypatch.setattr(manager_module, "openai_chat_completion", fail)
    assert asyncio.run(manager._auto_route_role("hello")) == "primary"
    assert manager.profile_health.is_unavailable("fast")


def test_fast_routed_requests_cannot_use_tools_or_delegate(tmp_path):
    manager = RunManager.__new__(RunManager)
    manager.config = _config()
    record = RunRecord(
        "run",
        "session",
        workspace=tmp_path,
        model_role="fast",
    )
    request = manager._build_request(record, "simple", None)
    assert request.model.model == "fast-model"
    assert request.tools.enabled == ()
    engine = AdkEngine(manager.config, db=None)
    agent = engine._build_agent(request, engine._make_context(request))
    assert agent.tools == []


def test_event_attribution_adds_model_and_role_without_overwriting_worker_model():
    manager = RunManager.__new__(RunManager)
    record = RunRecord("r", "s", model_role="primary")
    request = type("Request", (), {"model": ModelSpec(model="primary-model")})()
    event = {
        "event": "tool_result",
        "data": {"worker_model": "worker-model"},
    }

    attributed = manager._attribute_event(record, request, event)
    assert attributed["data"] == {
        "model": "primary-model",
        "worker_model": "worker-model",
        "model_role": "primary",
    }


def test_profile_health_snapshot_tracks_probe_and_runtime_failure():
    health = ModelHealthRegistry()
    config = _config()
    health.mark("worker", healthy=False, error_code="llm_unavailable")
    snapshot = health.snapshot(config)
    assert snapshot["worker"]["status"] == "unavailable"
    assert snapshot["worker"]["error_code"] == "llm_unavailable"
    assert snapshot["fast"]["status"] == "unknown"
    health.mark("worker", healthy=True, latency_ms=12)
    assert health.snapshot(config)["worker"]["status"] == "healthy"


def test_passthrough_delegation_creates_manager_child_without_outer_approval():
    config = _config(subagent_approval_mode="passthrough")
    config.agent.subagents["coder"] = SubagentConfig(
        instructions="Do the task",
        tools=["read_file"],
    )
    engine = AdkEngine(config, db=None)
    called = {}

    async def child_factory(**kwargs):
        called.update(kwargs)
        return {"ok": True, "output": "done"}

    request = RunRequest(
        run_id="parent",
        session_id="session",
        workspace=Path("/tmp"),
        instructions="",
        user_input="",
        model=ModelSpec(model="primary-model"),
        tools=ToolPolicy(approval_mode="on_request"),
        metadata={"subagent_run_factory": child_factory},
    )

    result = asyncio.run(
        engine._run_subagent(request=request, agent="coder", task="read a file")
    )
    assert result == {"ok": True, "output": "done"}
    assert called["task"] == "read a file"
    assert "Do the task" in called["instructions"]
    assert engine._delegation_needs_approval("always") is False


@pytest.mark.asyncio
async def test_worker_unavailable_falls_back_to_primary_and_reports_warning():
    config = _config()
    config.agent.subagents["coder"] = SubagentConfig(tools=["read_file"])
    manager = RunManager.__new__(RunManager)
    manager.config = config
    manager.profile_health = ModelHealthRegistry()
    manager.profile_health.mark("worker", healthy=False)
    roles = []

    async def start_run(**kwargs):
        roles.append(kwargs["model_role"])
        return RunRecord(
            kwargs["run_id"],
            kwargs["session_id"],
            state="completed",
            final_output="done",
            input_tokens=10,
            output_tokens=5,
            cost_usd=0.1,
        )

    manager.start_run = start_run
    manager._model_spec_for_role = lambda role: ModelSpec(model=f"{role}-model")
    result = await manager._run_child_subagent(
        RunRecord("parent", "session"),
        agent="coder",
        task="work",
        instructions="instructions",
    )
    assert roles == ["primary"]
    assert result["ok"] is True
    assert result["usage"]["model"] == "primary-model"
    assert "unavailable" in result["usage"]["fallback_reason"]


@pytest.mark.asyncio
async def test_worker_failure_retries_primary_only_before_tool_side_effects():
    config = _config()
    config.agent.subagents["coder"] = SubagentConfig(tools=["read_file"])
    manager = RunManager.__new__(RunManager)
    manager.config = config
    manager.profile_health = ModelHealthRegistry()
    roles = []

    async def start_run(**kwargs):
        roles.append(kwargs["model_role"])
        if kwargs["model_role"] == "worker":
            return RunRecord(
                kwargs["run_id"],
                kwargs["session_id"],
                state="failed",
                error_code="llm_unavailable",
                error_message="worker unavailable",
            )
        return RunRecord(
            kwargs["run_id"],
            kwargs["session_id"],
            state="completed",
            final_output="done",
        )

    manager.start_run = start_run
    manager._model_spec_for_role = lambda role: ModelSpec(model=f"{role}-model")
    result = await manager._run_child_subagent(
        RunRecord("parent", "session"),
        agent="coder",
        task="work",
        instructions="instructions",
    )
    assert roles == ["worker", "primary"]
    assert result["ok"] is True
    assert "retried" in result["usage"]["fallback_reason"]


@pytest.mark.asyncio
async def test_child_approval_is_relayed_to_parent_and_persisted_to_child(tmp_path):
    db = Database(tmp_path / "test.db")
    await db.connect()
    await db.create_session("session", title="test", workspace_dir=str(tmp_path))
    await db.create_run("parent", "session")
    await db.create_run("child", "session")

    config = _config()
    store = SessionStore(tmp_path / "data")
    manager = RunManager(
        config=config,
        db=db,
        session_store=store,
        metrics=Metrics(),
        compactor=None,
        engine=object(),
    )
    parent = RunRecord("parent", "session")
    child = RunRecord("child", "session", parent_run_id="parent")
    manager._runs.update({"parent": parent, "child": child})

    try:
        await manager._handle_approval(
            child,
            {
                "approval_id": "confirmation",
                "tool": "write_file",
                "arguments": json.dumps({"path": "out.txt"}),
                "reason": "Write file",
            },
        )
        approval = await db.get_approval(parent.events[0][1]["data"]["approval_id"])
        assert child.state == "awaiting_approval"
        assert child.state_changed.is_set()
        assert approval["run_id"] == "child"
        assert parent.events[0][1]["event"] == "approval_required"
        assert parent.events[0][1]["data"]["run_id"] == "child"
    finally:
        await db.close()
