"""Task runner: executes built-in handlers and user-defined tasks.

Three kinds of work run through here:

* **Built-ins** (``maintenance``, ``recovery``) — deterministic Python
  handlers driven by config, not by a task row. :meth:`TaskRunner.run_builtin`
  wraps them with a run record, a wall-clock timeout and an overlap guard.

* **User-defined prompt tasks** — a playbook prompt plus the scoped daemon tool
  set, run as a small agent that returns a :class:`TaskReport`. The agent is
  built with ADK in :mod:`claudio_backend.autonomous.agent`; this module stays
  framework-free and just calls the helper.

* **User-defined script tasks** — a shell script run directly in the workspace,
  outside the agent loop. A script task executes arbitrary shell as the backend
  user; the safety boundary is the ``report_only`` gate (a script task is skipped
  while report-only is in effect), the per-task ``enabled`` flag, and the audit
  trail. This is a deliberate operator-facing capability, not a sandbox.

:meth:`TaskRunner.run` loads the task row, dispatches on its ``type``, records
the run, updates the task and notifies per the task's ``notify_on`` policy.

Daemon agent sessions live in a *separate* ADK SQLite file so they never touch
``claudio_sessions`` (the chat session list).
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Awaitable

from claudio_core.db import Database, utcnow
from claudio_core.settings import AppConfig

from .agent import run_daemon_agent
from .report import TaskReport
from .tools import DaemonContext, build_daemon_tools, effective_report_only, _clip
from .maintenance import run_maintenance
from .recovery import run_recovery
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
SCRIPT_TASK_TIMEOUT_SECONDS = 300.0


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
        if task.get("type") == "script":
            report = await self._execute_script_task(task)
        else:
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
        ctx = DaemonContext(
            config=self.config,
            db=self.db,
            report_only=eff_report_only,
        )
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        session_id = f"daemon:{task['key']}:{day}"
        session_db = self.db.path.parent / "daemon_sessions.db"
        text = await run_daemon_agent(
            config=self.config,
            ctx=ctx,
            prompt=task["prompt"],
            tools=tools,
            session_id=session_id,
            session_db_path=str(session_db),
            max_turns=self.config.agent.max_turns,
        )
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

    # ---- user-defined script tasks -------------------------------------

    async def _execute_script_task(self, task: dict[str, Any]) -> TaskReport:
        """Run a script task: a shell script executed directly in the workspace.

        The report-only gate is checked first so a script never runs when the
        system is in report-only mode. The script is written to a private temp
        file (mode 0700), run under bash with a wall-clock timeout, and the
        temp file is always removed.
        """
        eff_report_only = effective_report_only(
            self.config.autonomous.report_only, task["report_only"]
        )
        if eff_report_only:
            await self.db.add_audit(
                action="script_task_skipped_report_only",
                command=task["key"],
                detail="script tasks require report_only=false",
                actor="daemon",
            )
            return TaskReport(
                verdict="skipped",
                summary="script tasks require report_only=false",
            )

        script = task.get("script") or ""
        if not script.strip():
            return TaskReport(
                verdict="critical",
                summary=f"task {task['key']} has no script content",
            )

        workspace = Path(self.config.agent.workspace_dir).resolve()
        workspace.mkdir(parents=True, exist_ok=True)
        fd, script_path = tempfile.mkstemp(
            prefix=f"claudio_task_{task['key']}_", suffix=".sh", dir=str(workspace)
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(script)
            os.chmod(script_path, 0o700)
            proc = await asyncio.create_subprocess_exec(
                "bash",
                script_path,
                cwd=str(workspace),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            # Shield the communicate() task so a timeout cancels the *wait*, not
            # the pipe drain — a cancelled communicate leaves the transport in a
            # bad state whose reader thread races the loop close.
            comm = asyncio.ensure_future(proc.communicate())
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    asyncio.shield(comm), timeout=SCRIPT_TASK_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                # Kill the runaway process, then drain the shielded task so the
                # transport closes cleanly before the event loop does.
                try:
                    proc.kill()
                except (ProcessLookupError, OSError):
                    pass
                try:
                    await asyncio.wait_for(comm, timeout=5)
                except (asyncio.TimeoutError, Exception):  # noqa: BLE001
                    pass
                return TaskReport(
                    verdict="critical",
                    summary=f"script {task['key']} exceeded {SCRIPT_TASK_TIMEOUT_SECONDS:.0f}s",
                )
            rc = proc.returncode if proc.returncode is not None else -1
            out = (stdout_b or b"").decode(errors="replace")
            err = (stderr_b or b"").decode(errors="replace")
            combined = out if out.strip() else err
            first_line = combined.strip().splitlines()[0] if combined.strip() else ""
            report_text = f"stdout:\n{_clip(out)}\n\nstderr:\n{_clip(err)}"
            verdict = "ok" if rc == 0 else "critical"
            summary = first_line if first_line else f"exit {rc}"
            await self.db.add_audit(
                action="script_task_run",
                command=task["key"],
                exit_code=rc,
                detail=summary,
                actor="daemon",
            )
            return TaskReport(
                verdict=verdict,
                summary=summary,
                report=report_text,
                details={"exit_code": rc},
            )
        finally:
            try:
                os.unlink(script_path)
            except OSError:
                pass

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
