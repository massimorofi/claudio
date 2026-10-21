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
import time
from typing import Any

from claudio_core.db import Database

from .health import run_health_checks
from .notifier import Notifier
from .script import first_line, run_shell_script, verdict_from_exit

logger = logging.getLogger(__name__)

# Wall-clock ceiling for an operator-supplied health-check script. The check is
# read-only diagnostics, so a short bound is plenty; a runaway script is killed
# and reported as critical rather than hanging the scheduler.
HEALTH_SCRIPT_TIMEOUT_SECONDS = 120.0


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

        When the operator has supplied a health-check script
        (``autonomous.health_check.script``) it is the source of truth and runs
        in place of the built-in Python diagnostics. Otherwise the built-in
        read-only check suite runs.

        Returns the check result dict (verdict, summary, checks, duration_ms)
        plus ``id`` and ``new_issue`` (the issue id if one was just created).
        """
        script = (getattr(self.config.autonomous.health_check, "script", "") or "").strip()
        if script:
            return await self._run_script_once(script, source)

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

    async def _run_script_once(self, script: str, source: str) -> dict[str, Any]:
        """Run the operator-supplied health-check script and persist the outcome.

        The script's exit code is the verdict (0 ok / 1 warning / 2 critical);
        its stdout is the summary and the full output is the recorded detail.
        The same record-vs-push policy as the built-in path applies: always
        record, raise an issue on warning/critical unless one is already open,
        notify only on a newly-recorded problem.
        """
        workspace = self.config.agent.workspace_dir
        t0 = time.monotonic()
        rc, out, err = await run_shell_script(
            script, workspace, timeout=HEALTH_SCRIPT_TIMEOUT_SECONDS
        )
        duration_ms = int((time.monotonic() - t0) * 1000)

        if rc is None:
            verdict = "critical"
            summary = first_line(out or err) or "health script timed out"
        else:
            verdict = verdict_from_exit(rc)
            combined = out if out.strip() else err
            summary = first_line(combined) or f"health script exited {rc}"

        detail = out if not err.strip() else (out + ("\n" if out.strip() else "") + err)
        check_item = {
            "name": "script",
            "status": verdict,
            "message": summary,
            "value": None,
            "threshold": None,
            "detail": detail,
        }

        check_id = await self.db.add_health_check(
            verdict=verdict,
            summary=summary,
            details=[check_item],
            duration_ms=duration_ms,
            source=source,
        )

        new_issue: int | None = None
        if verdict in ("warning", "critical"):
            existing = await self.db.find_open_issue_by_title(summary)
            if existing is None:
                new_issue = await self.db.create_issue(
                    severity=verdict,
                    title=summary,
                    detail=detail,
                    source="health",
                )
                await self.notifier.notify(verdict, summary, [check_item])

        return {
            "id": check_id,
            "verdict": verdict,
            "summary": summary,
            "duration_ms": duration_ms,
            "checks": [check_item],
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
