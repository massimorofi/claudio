"""Run lifecycle: budgets, cancellation, fan-out, persistence.

Framework-free by contract -- this package talks to
:class:`claudio_core.engine.RunEngine`, never to ADK directly.
"""
from .manager import BudgetExceededError, RunManager, RunRecord

__all__ = ["RunManager", "RunRecord", "BudgetExceededError"]
