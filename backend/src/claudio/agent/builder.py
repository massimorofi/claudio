"""Build OpenAI Agents SDK ``Agent`` objects from Claudio config.

The primary agent and each subagent are constructed here from the live config, so a
config change + agent restart is all that's needed to reshape the agent.
"""
from __future__ import annotations

from typing import Any

from agents import (
    Agent,
    ModelSettings,
    OpenAIChatCompletionsModel,
    function_tool,
)
from openai import AsyncOpenAI

from ..settings import AppConfig
from .context import ToolContext
from .instructions import build_instructions
from .guardrails import input_safety_guardrail, output_safety_guardrail
from .subagents import spawn_subagent
from .tools.registry import build_tools


def _make_model(config: AppConfig, model_name: str | None = None) -> OpenAIChatCompletionsModel:
    """Build a Chat Completions model bound to the configured OpenAI-compatible endpoint.

    ``max_retries=0`` disables the OpenAI client's own retry loop so the
    application-level retry policy in :class:`claudio.runs.manager.RunManager`
    is the single source of truth (otherwise retries nest and double up).
    """
    client = AsyncOpenAI(
        api_key=config.llm.api_key or "local",
        base_url=config.llm.base_url,
        timeout=float(config.llm.timeout_seconds),
        max_retries=0,
    )
    return OpenAIChatCompletionsModel(
        model=model_name or config.llm.model,
        openai_client=client,
    )


def _model_settings(config: AppConfig) -> ModelSettings:
    return ModelSettings(
        temperature=config.llm.temperature,
        max_tokens=config.llm.max_tokens,
    )


def _dynamic_instructions(config: AppConfig):
    """Instructions that recompute per turn (todo nag depends on turn count)."""

    async def _instructions(ctx, agent) -> str:  # noqa: ANN001
        tctx: ToolContext = ctx.context
        return build_instructions(
            config,
            session_id=tctx.session_id,
            turns_since_todo=tctx.turns_since_todo,
        )

    return _instructions


def build_subagent(config: AppConfig, agent_id: str) -> Agent[ToolContext] | None:
    """Build a configured subagent by id. Returns None if unknown."""
    cfg = config.agent.subagents.get(agent_id)
    if cfg is None:
        return None

    if cfg.instructions:
        instructions_text = cfg.instructions
    elif cfg.instructions_file:
        from pathlib import Path

        f = Path(cfg.instructions_file)
        if not f.is_absolute():
            f = Path(config.agent.workspace_dir).parent.parent / cfg.instructions_file
        instructions_text = f.read_text() if f.exists() else cfg.description
    else:
        instructions_text = cfg.description

    tools = build_tools(
        enabled=cfg.tools,
        strict_mode=config.agent.strict_tools,
    )
    return Agent[ToolContext](
        name=agent_id,
        instructions=instructions_text,
        model=_make_model(config),
        model_settings=_model_settings(config),
        tools=tools,
        output_type=str,
    )


def _agent_factory(config: AppConfig):
    """Factory callable exposed to tools so spawn_subagent can build children."""

    def factory(agent_id: str):
        if agent_id == "list":
            return list(config.agent.subagents.keys())
        return build_subagent(config, agent_id)

    return factory


def build_primary_agent(
    config: AppConfig, model_override: str | None = None
) -> Agent[ToolContext]:
    """Build the primary agent from the current config.

    ``model_override`` replaces the configured model name (used for fallback).
    """
    enabled = list(config.agent.tools.enabled)

    tools = build_tools(
        enabled=enabled,
        disabled=config.agent.tools.disabled,
        strict_mode=config.agent.strict_tools,
    )
    # The spawn tool is only exposed when explicitly enabled (it is not part of the
    # base registry's raw-function map, so build_tools skips it).
    if "spawn_subagent" in enabled:
        spawn_tool = function_tool(
            spawn_subagent,
            name_override="spawn_subagent",
            strict_mode=config.agent.strict_tools,
        )
        tools.append(spawn_tool)

    return Agent[ToolContext](
        name=config.agent.name,
        instructions=_dynamic_instructions(config),
        model=_make_model(config, model_override),
        model_settings=_model_settings(config),
        tools=tools,
        output_type=str,
        input_guardrails=[input_safety_guardrail],
        output_guardrails=[output_safety_guardrail],
    )
