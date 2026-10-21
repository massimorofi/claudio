"""Engine implementations.

This package is the only place in the backend that is allowed to know that
Google ADK exists. Everything above it (``claudio_backend.api``,
``claudio_backend.runs``) talks to :class:`claudio_core.engine.RunEngine`.
"""
from .adk_engine import AdkEngine, create_engine

__all__ = ["AdkEngine", "create_engine"]
