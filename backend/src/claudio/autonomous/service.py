"""Health service: run a check, record it, raise an issue, notify (Phase 1).

This is the single code path every trigger uses — the internal scheduler, the
systemd timer (via the CLI), and the cockpit's "Run now" button all call
:meth:`HealthService.run_once`. Keeping one path means one behavior.

Policy (the "record vs push" rule):
- **Always record** the check in the health history — the system of record.
- On WARNING/CRITICAL, **record an issue** *unless an identical issue is already
  open* (so a persistent fault is one item, not one per interval).
- **Notify only when a new issue is recorded** — a fresh problem reaches the
  operator once, not every cycle.
"""
from __future__ import annotations

import logging
from typing import Any

from ..db import Database
from .health import run_health_checks
from .notifier import Notifier

logger = logging.getLogger(__name__)


class HealthService:
    def __init__(
        self,
        config,
        db: Database,
        notifier: Notifier | None = None,
        runner=None,
    ) -> None:
        self.config = config
        self.db = db
        self.notifier = notifier or Notifier(config)
        self.runner = runner

    async def run_once(self, source: str = "scheduled") -> dict[str, Any]:
        """Run the diagnostic suite once and persist the outcome.

        Returns the check result dict (verdict, summary, checks, duration_ms)
        plus ``id`` and ``new_issue`` (the issue id if one was just created).
        """
        result = await run_health_checks(self.config, runner=self.runner)
        verdict = result["verdict"]
        summary = result["summary"]

        check_id = await self.db.add_health_check(
            verdict=verdict,
            summary=summary,
            details=result["checks"],
            duration_ms=result["duration_ms"],
            source=source,
        )

        new_issue: int | None = None
        if verdict in ("warning", "critical"):
            existing = await self.db.find_open_issue_by_title(summary)
            if existing is None:
                detail = self._issue_detail(result["checks"])
                new_issue = await self.db.create_issue(
                    severity=verdict,
                    title=summary,
                    detail=detail,
                    source="health",
                )
                # Push only on a newly-recorded problem.
                await self.notifier.notify(verdict, summary, result["checks"])

        return {
            "id": check_id,
            "verdict": verdict,
            "summary": summary,
            "duration_ms": result["duration_ms"],
            "checks": result["checks"],
            "new_issue": new_issue,
        }

    @staticmethod
    def _issue_detail(checks: list[dict[str, Any]]) -> str:
        """Concatenate the evidence from the non-OK checks for the issue body."""
        parts: list[str] = []
        for c in checks:
            if c.get("status") in ("warning", "critical"):
                line = f"{c.get('name')}: {c.get('message')}"
                detail = c.get("detail")
                if detail:
                    line += f"\n{detail}"
                parts.append(line)
        return "\n\n".join(parts)
