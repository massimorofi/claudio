"""Engine implementations.

This package is the only place in the backend that is allowed to know that a
concrete agent engine (Google ADK, OpenCode, Claude Code) exists. Everything
above it (``claudio_backend.api``, ``claudio_backend.runs``) talks to the
framework-neutral :class:`claudio_core.engine.RunEngine` protocol through the
:func:`create_engine` factory (issue #57).
"""
from .adk_engine import AdkEngine
from .factory import create_engine

__all__ = ["AdkEngine", "create_engine"]
