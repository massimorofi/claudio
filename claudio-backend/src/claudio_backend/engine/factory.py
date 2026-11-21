"""Engine selection and validation (issue #57).

The single place that decides which engine implementation backs the backend.
Selection is deployment-wide: ``CLAUDIO_ENGINE`` (env) overrides ``engine.kind``
(config), defaulting to ``adk``. Unknown ids fail loudly -- there is no silent
fallback to ADK, because a mis-set selector must surface, not quietly run a
different engine than the operator chose.
"""
from __future__ import annotations

from typing import Any

from claudio_core.engine import EngineError
from claudio_core.settings import resolve_engine_kind

from .base import EngineContext


def create_engine(
    config: Any,
    db: Any = None,
    kind: str | None = None,
    accountant: Any = None,
    *,
    session_store: Any = None,
    metrics: Any = None,
    profile_health: Any = None,
) -> Any:
    """Construct the selected engine.

    ``kind`` (explicit) wins over ``CLAUDIO_ENGINE``/``engine.kind`` resolution,
    which is what the supervisor relies on. Raises :class:`EngineError` for an
    unknown id so a bad selector fails startup rather than defaulting silently.
    """
    selected = (kind or resolve_engine_kind(config) or "adk").strip().lower()
    if selected in ("adk", "google_adk", "google-adk"):
        from .adk_engine import AdkEngine

        ctx = EngineContext(
            config=config, db=db, session_store=session_store, metrics=metrics,
            accountant=accountant, profile_health=profile_health,
        )
        engine = AdkEngine(config=config, db=db, accountant=accountant)
        # Back-compat: the supervisor historically set profile_health as an
        # attribute; keep it available while the typed context is the source.
        engine.profile_health = profile_health
        return engine
    if selected == "opencode":
        from .opencode import OpenCodeEngine

        return OpenCodeEngine(EngineContext(
            config=config, db=db, session_store=session_store, metrics=metrics,
            accountant=accountant, profile_health=profile_health,
        ))
    if selected == "claude_code":
        from .claude_code import ClaudeCodeEngine

        return ClaudeCodeEngine(EngineContext(
            config=config, db=db, session_store=session_store, metrics=metrics,
            accountant=accountant, profile_health=profile_health,
        ))
    raise EngineError(
        "unknown_engine",
        f"Unknown engine {selected!r}; this build provides 'adk', 'opencode', "
        "'claude_code'.",
        retryable=False,
    )


__all__ = ["create_engine"]
