"""Shared report shape for built-in handlers and scheduled tasks.

Both the maintenance handler and the recovery handler (and, later, user-defined
agent tasks) produce a :class:`TaskReport`. It lives in its own module so the
handlers can import it without a circular dependency on the task runner (which
imports the handlers back).
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class TaskReport:
    """The outcome of one maintenance / recovery / task run.

    ``verdict`` follows the same vocabulary as the health checks:
    ``ok`` | ``warning`` | ``critical``.
    """

    verdict: str
    summary: str
    report: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
