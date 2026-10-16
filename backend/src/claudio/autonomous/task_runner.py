"""Task runner: executes built-in handlers and user-defined agent tasks.

Two kinds of work run through here:

* **Built-ins** (``maintenance``, ``recovery``) — deterministic Python
  handlers driven by config, not by a task row. :meth:`TaskRunner.run_builtin`
  wraps them with a run record, a wall-clock timeout and an overlap guard.

* **User-defined tasks** — a playbook prompt plus the scoped daemon tool set,
  run as a small agent that returns a :class:`TaskReport`. :meth:`TaskRunner.run`
  loads the task row, runs it, records the run, updates the task and notifies
  per the task's ``notify_on`` policy.

Daemon agent sessions use the SDK's own ``SQLiteSession`` in a *separate*
database file so they never touch ``claudio_sessions`` (the chat session list).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable

from agents import Agent, Runner, SQLiteSession

from ..db import Database, utcnow
from ..settings import AppConfig
from .report import TaskReport
from .tools import build_daemon_tools, effective_report_only
from .maintenance import run_maintenance
from .recovery import run_recovery
from ..agent.builder import _make_model, _model_settings
from .fixer import _extract_json

logger = logging.getLogger(__name__)

# Built-in handlers, keyed by kind. Config-driven, not task rows.
HANDLERS: dict[str, Callable[..., Awaitable[TaskReport]]] = {
    "maintenance": run_maintenance,
    "recovery": run_recovery,
}

# Wall-clock ceilings. A single elevated command already has its own timeout
# (tools.ELEVATED_TIMEOUT); these bound the whole run as a backstop.
AGENT_TASK_TIMEOUT_SECONDS = 900.0
BUILTIN_TIMEOUT_SECONDS = 1800.0


class TaskRunner:
    def __init__(self, config: AppConfig, db: Database, notifier) -> None:
        self.config = config
        self.db = db
        self.notifier = notifier
        self._running: set[str] = set()

    # ---- user-defined agent tasks --------------------------------------

    async def run(self, task_key: str, trigger: str = "scheduled") -> TaskReport:
        """Run one user-defined task by key."""
        task = await self.db.get_task(task_key)
        if task is None:
            raise ValueError(f"unknown task: {task_key}")

        if task_key in self._running:
            logger.info("task %s already running — skipping overlap", task_key)
            return TaskReport(
                verdict="skipped",
                summary=f"task {task_key} was already running",
            )

        self._running.add(task_key)
        run_id = await self.db.add_task_run(task_key, trigger=trigger)
        report = await self._execute_agent_task(task)
        await self._finish(task_key, run_id, report, notify_on=task["notify_on"])
        return report

    async def _execute_agent_task(self, task: dict[str, Any]) -> TaskReport:
        eff_report_only = effective_report_only(
            self.config.autonomous.report_only, task["report_only"]
        )
        try:
            return await asyncio.wait_for(
                self._run_agent_task(task, eff_report_only),
                timeout=AGENT_TASK_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            return TaskReport(
                verdict="critical",
                summary=f"task {task['key']} exceeded {AGENT_TASK_TIMEOUT_SECONDS:.0f}s",
            )
        except Exception as exc:  # noqa: BLE001 — a task failure must not escape
            logger.exception("task %s failed", task["key"])
            return TaskReport(
                verdict="critical",
                summary=f"task {task['key']} failed: {exc}",
            )

    async def _run_agent_task(
        self, task: dict[str, Any], eff_report_only: bool
    ) -> TaskReport:
        tools = build_daemon_tools(report_only=eff_report_only, strict_mode=False)
        agent = Agent(
            name=f"task:{task['key']}",
            instructions=task["prompt"],
            model=_make_model(self.config),
            model_settings=_model_settings(self.config),
            tools=tools,
            output_type=str,
        )
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        session_id = f"daemon:{task['key']}:{day}"
        session_db = self.db.path.parent / "daemon_sessions.db"
        session = SQLiteSession(session_id, db_path=session_db)
        result = await Runner.run(
            agent,
            task["prompt"],
            session=session,
            max_turns=self.config.agent.max_turns,
        )
        text = result.final_output if isinstance(result.final_output, str) else str(result.final_output)
        return self._coerce_report(text)

    @staticmethod
    def _coerce_report(text: str) -> TaskReport:
        """Parse a TaskReport out of the agent's text output, tolerantly."""
        try:
            data = _extract_json(text)
        except ValueError:
            return TaskReport(verdict="ok", summary=text[:200], report=text)
        verdict = str(data.get("verdict", "ok")).lower()
        if verdict not in ("ok", "warning", "critical"):
            verdict = "ok"
        return TaskReport(
            verdict=verdict,
            summary=str(data.get("summary", text[:200])),
            report=str(data.get("report", text)),
            details=data.get("details", {}) if isinstance(data.get("details"), dict) else {},
        )

    # ---- built-in handlers ---------------------------------------------

    async def run_builtin(self, kind: str, trigger: str = "scheduled", runner=None) -> TaskReport:
        """Run a built-in handler (maintenance / recovery).

        The built-in handlers notify on their own (non-OK verdicts), so this
        wrapper does not add a second notification. ``runner`` is passed
        through to the handler for testing; production leaves it ``None`` so
        the handler uses the real subprocess runner.
        """
        handler = HANDLERS.get(kind)
        if handler is None:
            raise ValueError(f"unknown built-in: {kind}")

        if kind in self._running:
            logger.info("built-in %s already running — skipping overlap", kind)
            return TaskReport(verdict="skipped", summary=f"{kind} was already running")

        self._running.add(kind)
        run_id = await self.db.add_task_run(kind, trigger=trigger)
        try:
            report = await asyncio.wait_for(
                handler(self.config, self.db, self.notifier, runner=runner),
                timeout=BUILTIN_TIMEOUT_SECONDS,
            )
        except asyncio.TimeoutError:
            report = TaskReport(
                verdict="critical",
                summary=f"{kind} exceeded {BUILTIN_TIMEOUT_SECONDS:.0f}s",
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("built-in %s failed", kind)
            report = TaskReport(verdict="critical", summary=f"{kind} failed: {exc}")
        finally:
            self._running.discard(kind)

        await self.db.update_task_run(
            run_id,
            verdict=report.verdict,
            summary=report.summary,
            output=report.report,
            exit_code=0 if report.verdict == "ok" else 1,
            finished_at=utcnow(),
        )
        await self.db.add_audit(
            action=f"builtin_{kind}",
            detail=f"{kind}: {report.verdict}",
            actor="daemon",
        )
        return report

    # ---- shared finish -------------------------------------------------

    async def _finish(
        self, task_key: str, run_id: int, report: TaskReport, notify_on: list[str]
    ) -> None:
        await self.db.update_task_run(
            run_id,
            verdict=report.verdict,
            summary=report.summary,
            output=report.report,
            exit_code=0 if report.verdict == "ok" else 1,
            finished_at=utcnow(),
        )
        await self.db.update_task(
            task_key, last_run_at=utcnow(), last_verdict=report.verdict
        )
        await self.db.add_audit(
            action="task_run",
            command=task_key,
            detail=f"{report.verdict}: {report.summary}",
            actor="daemon",
        )
        if report.verdict in (notify_on or []):
            await self.notifier.notify(report.verdict, report.summary, [
                {"name": task_key, "status": report.verdict, "message": report.summary}
            ])
