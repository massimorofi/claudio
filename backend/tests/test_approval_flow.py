"""Integration test for the human-in-the-loop approval flow.

Forces a tool call (tool_choice="required") against the local model so the
approval pause is exercised deterministically rather than depending on the model
choosing to call a destructive tool.
"""
from __future__ import annotations

import json

import pytest
from agents import Agent, Runner, function_tool, ModelSettings
from openai import AsyncOpenAI

from claudio.settings import AppConfig
from claudio.db import Database
from claudio.agent.context import ToolContext
from claudio.runs.manager import _serialize_tool_context


@pytest.fixture
def cfg() -> AppConfig:
    return AppConfig()


@function_tool(needs_approval=True)
async def dangerous_tool(ctx, value: str) -> dict:
    """A destructive tool that always requires approval.

    Args:
        value: A value to process.
    """
    return {"ok": True, "did": value}


async def test_approval_pause_and_resume(cfg):
    model = AsyncOpenAI(api_key="local", base_url=cfg.llm.base_url)
    from agents import OpenAIChatCompletionsModel

    chat_model = OpenAIChatCompletionsModel(
        model=cfg.llm.model, openai_client=model
    )

    agent = Agent(
        name="test",
        instructions="Always call the dangerous_tool with value 'test'.",
        model=chat_model,
        model_settings=ModelSettings(tool_choice="required"),
        tools=[dangerous_tool],
        output_type=str,
    )

    result = await Runner.run(
        agent,
        "Please call the dangerous tool now.",
        max_turns=2,
    )

    # The run should pause with an approval interruption.
    assert result.interruptions, "Expected an approval interruption"
    item = result.interruptions[0]
    assert getattr(item, "tool_name", None) == "dangerous_tool" or "dangerous" in str(
        getattr(item, "name", "")
    )

    # Resume by approving.
    state = result.to_state()
    state.approve(item)
    resumed = await Runner.run(agent, state, max_turns=2)
    # After approval the tool runs and the agent produces a final answer.
    assert resumed.final_output is not None


async def test_tool_context_serializes_with_live_db(tmp_path):
    """Regression: the run context holds a live sqlite connection.

    The SDK serializes the run context when a run pauses for approval. A raw
    ``dataclasses.asdict`` over the ``ToolContext`` chokes on the sqlite
    connection ("cannot pickle 'sqlite3.Connection' object"), which used to fail
    every approval-required run. The context serializer must produce JSON-safe
    output that omits the connection.
    """
    db = Database(tmp_path / "t.db")
    await db.connect()
    try:
        ctx = ToolContext(
            config=AppConfig(),
            session_id="s1",
            workspace=tmp_path,
            approval_mode="on_request",
            db=db,
            run_id="r1",
        )
        # The serializer returns a JSON-safe mapping that omits the live DB.
        out = _serialize_tool_context(ctx)
        json.dumps(out)  # must not raise
        assert out["session_id"] == "s1"
        assert out["run_id"] == "r1"
        assert "db" not in out
    finally:
        await db.close()
