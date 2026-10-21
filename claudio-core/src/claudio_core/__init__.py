"""claudio-core: the framework-free half of Claudio.

Everything in this package must be free of agent-framework dependencies — no
OpenAI Agents SDK, no Google ADK, no LangChain. That rule is what makes the
engine swappable, and it is enforced mechanically by an import-linter contract
in ``pyproject.toml`` rather than by convention. Run ``lint-imports`` to check.

What lives here:

* :mod:`claudio_core.events` — the frozen SSE wire contract.
* :mod:`claudio_core.engine` — the :class:`RunEngine` protocol an engine implements.
* :mod:`claudio_core.db` — SQLite persistence.
* :mod:`claudio_core.settings` — configuration model and validation.
* :mod:`claudio_core.tools` — the tool implementations and their metadata.
* :mod:`claudio_core.safety` — allowlist, guardrails, redaction.
* :mod:`claudio_core.recovery` — request sanitisation ("prevent, don't react").
* :mod:`claudio_core.compaction` — context compaction with a circuit breaker.
* :mod:`claudio_core.instructions` — system-prompt assembly.
* :mod:`claudio_core.skill_loader` — skill discovery and rendering.
"""
from .context import ToolContext, context_usage_payload
from .db import Database, utcnow
from .engine import (
    EngineError,
    ModelSpec,
    RunEngine,
    RunOutcome,
    RunRequest,
    ToolPolicy,
    is_terminal,
)
from .events import (
    ALL_EVENT_NAMES,
    EventName,
    encode_event,
    format_sse,
)
from .instructions import build_instructions
from .llm import LlmCallError, openai_chat_completion
from .memory import discover_memory_files, render_memory_block
from .session_store import ConversationStore, SessionStore
from .settings import AppConfig
from .skill_loader import Skill, SkillScan, scan_skills
from .tools.registry import (
    ALL_TOOL_NAMES,
    TOOL_META,
    ToolMeta,
    needs_approval_for,
    select_tool_names,
    tool_inventory,
)

__version__ = "0.1.0"

__all__ = [
    "__version__",
    # context
    "ToolContext",
    "context_usage_payload",
    # persistence
    "Database",
    "utcnow",
    "SessionStore",
    "ConversationStore",
    # config
    "AppConfig",
    # engine contract
    "RunEngine",
    "RunRequest",
    "RunOutcome",
    "ModelSpec",
    "ToolPolicy",
    "EngineError",
    "is_terminal",
    # events
    "EventName",
    "ALL_EVENT_NAMES",
    "encode_event",
    "format_sse",
    # prompt
    "build_instructions",
    "openai_chat_completion",
    "LlmCallError",
    "discover_memory_files",
    "render_memory_block",
    # skills
    "Skill",
    "SkillScan",
    "scan_skills",
    # tools
    "ToolMeta",
    "TOOL_META",
    "ALL_TOOL_NAMES",
    "needs_approval_for",
    "select_tool_names",
    "tool_inventory",
]
