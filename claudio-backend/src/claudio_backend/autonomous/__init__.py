"""Autonomous server-management subsystem.

Read-only health monitoring plus scoped, audited maintenance and recovery:
deterministic diagnostics produce an OK / WARNING / CRITICAL verdict, which is
stored, surfaced in the cockpit, and pushed to the operator by the notification
channel when it is not OK. Built-in handlers (maintenance, recovery) and
user-defined prompt / script tasks run on cron through the task scheduler.

The daemon's agent is built with Google ADK in :mod:`.agent`; the rest of this
package is framework-free.
"""
from __future__ import annotations

from .health import CheckResult, HealthChecker, overall_verdict, run_health_checks
from .notifier import Notifier
from .report import TaskReport
from .service import HealthService
from .scheduler import HealthScheduler
from .task_runner import TaskRunner
from .task_scheduler import TaskScheduler

__all__ = [
    "CheckResult",
    "HealthChecker",
    "overall_verdict",
    "run_health_checks",
    "Notifier",
    "TaskReport",
    "HealthService",
    "HealthScheduler",
    "TaskRunner",
    "TaskScheduler",
]
