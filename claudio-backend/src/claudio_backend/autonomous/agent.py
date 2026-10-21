"""ADK daemon-agent builder for user-defined prompt tasks.

A prompt task runs a small agent over the scoped daemon tool set with a
playbook prompt. The daemon tools are plain callables taking a
:class:`~claudio_backend.autonomous.tools.DaemonContext` first; ADK builds its
tool schema from the callable's signature, so each tool is wrapped in a closure
that hides ``ctx`` — the same technique the chat engine uses in
:func:`claudio_backend.engine.adk_engine.make_adk_tool`. The agent runs to
completion and its final text is returned for the caller to coerce into a
:class:`~claudio_backend.autonomous.report.TaskReport`.

This module is the ADK seam for the daemon: everything above it (the task
runner) stays framework-free and never touches ADK directly.
"""
from __future__ import annotations

import inspect
import logging
from typing import Any, Callable

from google.adk.agents import LlmAgent
from google.adk.apps import App
from google.adk.runners import Runner
from google.adk.sessions.sqlite_session_service import SqliteSessionService
from google.adk.tools.function_tool import FunctionTool
from google.genai import types

from claudio_core.settings import AppConfig

from .tools import DaemonContext

logger = logging.getLogger(__name__)

DAEMON_APP_NAME = "claudio-daemon"


def make_daemon_adk_tool(fn: Callable, ctx: DaemonContext) -> FunctionTool:
    """Wrap one daemon tool callable into an ADK ``FunctionTool`` with ``ctx`` hidden.

    The wrapper accepts ``**kwargs`` and is given a synthetic ``__signature__``
    listing only the model-facing parameters (everything but ``ctx``), so ADK
    builds the real schema while the context stays invisible to the model.
    """
    real = inspect.signature(fn)
    model_params = [p for p in real.parameters.values() if p.name != "ctx"]

    async def wrapper(**kwargs: Any) -> dict[str, Any]:
        try:
            return await fn(ctx, **kwargs)
        except Exception as exc:  # noqa: BLE001 - tool errors are data, not crashes
            logger.warning("daemon tool %s raised: %s", fn.__name__, exc)
            return {"error": str(exc)}

    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__ or f"{fn.__name__}."
    wrapper.__signature__ = inspect.Signature(parameters=model_params)
    return FunctionTool(wrapper)


def _make_model(config: AppConfig) -> Any:
    from google.adk.integrations.openai import OpenAILlm

    return OpenAILlm(
        model=config.llm.model,
        base_url=config.llm.base_url,
        api_key=config.llm.api_key or "local",
    )


async def run_daemon_agent(
    *,
    config: AppConfig,
    ctx: DaemonContext,
    prompt: str,
    tools: list[Callable],
    session_id: str,
    session_db_path: str,
    max_turns: int = 30,
) -> str:
    """Run one daemon agent pass and return its final text output.

    The daemon agent keeps its own ADK session in a separate SQLite file so it
    never touches the chat session list. The session is get-or-created on the
    passed ``session_id`` (the caller keys it by task + day so a daily task
    carries its own thread).
    """
    adk_tools = [make_daemon_adk_tool(fn, ctx) for fn in tools]
    agent = LlmAgent(
        name="claudio_daemon",
        instruction=prompt,
        model=_make_model(config),
        tools=adk_tools,
    )
    app = App(name=DAEMON_APP_NAME, root_agent=agent)
    session_service = SqliteSessionService(db_path=session_db_path)
    existing = await session_service.get_session(
        app_name=DAEMON_APP_NAME, user_id=session_id, session_id=session_id
    )
    if existing is None:
        await session_service.create_session(
            app_name=DAEMON_APP_NAME, user_id=session_id, session_id=session_id
        )
    runner = Runner(app=app, session_service=session_service)

    new_message = types.Content(role="user", parts=[types.Part(text=prompt)])
    final_text = ""
    turns = 0
    agen = runner.run_async(
        user_id=session_id,
        session_id=session_id,
        new_message=new_message,
    )
    try:
        async for event in agen:
            content = getattr(event, "content", None)
            if content is None or not getattr(content, "parts", None):
                continue
            author = getattr(event, "author", "") or ""
            if author in ("", "user"):
                continue
            for part in content.parts:
                text = getattr(part, "text", None) or ""
                if text and not getattr(part, "thought", False):
                    final_text = text
            turns += 1
            if max_turns and turns >= max_turns:
                await agen.aclose()
                break
    finally:
        try:
            await agen.aclose()
        except Exception:  # noqa: BLE001 - already finished; close is best-effort
            pass
    return final_text


__all__ = [
    "run_daemon_agent",
    "make_daemon_adk_tool",
    "DAEMON_APP_NAME",
]
