"""Supervisor: owns the agent runtime lifecycle (start/stop/restart) and shared
services. The HTTP surface stays up regardless of the agent runtime's state.

In the refactored architecture the supervisor wires the framework-free core
services (config, database, session store, metrics, compactor) together with
the engine (the one ADK-aware component) and the run manager that drives it,
plus the autonomous watch/task subsystem. The engine is built once at start
and handed to the run manager; everything above the engine talks to the
:class:`claudio_core.engine.RunEngine` protocol, not to ADK.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from claudio_core.compaction import Compactor
from claudio_core.db import Database, utcnow
from claudio_core.metrics import Metrics
from claudio_core.session_store import SessionStore
from claudio_core.settings import AppConfig, load_config

from .engine import create_engine
from .runs.manager import RunManager
from .autonomous.notifier import Notifier
from .autonomous.service import HealthService
from .autonomous.scheduler import HealthScheduler
from .autonomous.task_runner import TaskRunner
from .autonomous.task_scheduler import TaskScheduler

logger = logging.getLogger(__name__)


class Supervisor:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = base_dir
        self.config_path = base_dir / "config" / "config.yaml"
        self.data_dir = base_dir / "data"
        self.state = "stopped"
        self.started_at: str | None = None
        self.config: AppConfig | None = None
        self.db: Database | None = None
        self.session_store: SessionStore | None = None
        self.metrics = Metrics()
        self.compactor: Compactor | None = None
        self.engine = None
        self.run_manager: RunManager | None = None
        self.notifier: Notifier | None = None
        self.health_service: HealthService | None = None
        self.health_scheduler: HealthScheduler | None = None
        self.task_runner: TaskRunner | None = None
        self.task_scheduler: TaskScheduler | None = None
        self._config_generation = 0

    # ---- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        if self.state == "running":
            raise RuntimeError("Agent runtime already running")
        self.state = "starting"
        try:
            self.config = load_config(self.config_path)
            self.db = Database(self.data_dir / "claudio.db")
            await self.db.connect()
            # Mark runs left 'running' by a previous crash as failed.
            await self.db.fail_interrupted_runs()
            self.session_store = SessionStore(self.data_dir)
            self.compactor = Compactor(
                context_window=self.config.agent.context_window,
                reserve=self.config.agent.compaction_reserve,
            )
            # Restore persisted compaction circuit-breaker state so a session
            # that tripped the breaker before a restart stays disabled until the
            # operator clears it. Seed before any run can start.
            for sid in await self.db.list_compaction_disabled_session_ids():
                self.compactor.disable(sid)
            # The engine is the single ADK-aware component; the run manager
            # drives it through the framework-free RunEngine protocol.
            self.engine = create_engine(self.config, self.db)
            self.run_manager = RunManager(
                config=self.config,
                db=self.db,
                session_store=self.session_store,
                metrics=self.metrics,
                compactor=self.compactor,
                engine=self.engine,
            )
            # Autonomous watch layer: health service + scheduler. The scheduler
            # is always started; it gates itself on the enabled flag each cycle,
            # so toggling autonomous.enabled takes effect without a restart.
            self.notifier = Notifier(self.config)
            self.health_service = HealthService(
                config=self.config, db=self.db, notifier=self.notifier
            )
            self.health_scheduler = HealthScheduler(self.health_service)
            await self.health_scheduler.start()
            # Task system: user-defined cron tasks + built-in maintenance. Like
            # the health scheduler, it gates itself on autonomous.enabled each
            # cycle, so toggling it needs no restart.
            self.task_runner = TaskRunner(
                config=self.config, db=self.db, notifier=self.notifier
            )
            self.task_scheduler = TaskScheduler(
                task_runner=self.task_runner, db=self.db, config=self.config
            )
            await self.task_scheduler.start()
            self.state = "running"
            self.started_at = utcnow()
            logger.info("Agent runtime started")
        except Exception:
            self.state = "stopped"
            raise

    async def stop(self) -> None:
        if self.state == "stopped":
            return
        self.state = "stopping"
        try:
            # Stop the autonomous schedulers first so no new check fires during
            # teardown.
            if self.health_scheduler:
                await self.health_scheduler.stop()
            if self.task_scheduler:
                await self.task_scheduler.stop()
            # Cancel active runs.
            if self.run_manager:
                for run_id in list(self.run_manager._runs.keys()):
                    if self.run_manager._runs[run_id].state == "running":
                        await self.run_manager.cancel_run(run_id)
            # Release any MCP sessions the engine is holding open.
            if self.engine:
                try:
                    await self.engine.close()
                except Exception as e:  # noqa: BLE001 - teardown is best-effort
                    logger.warning("Error closing engine MCP sessions: %s", e)
            if self.db:
                await self.db.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("Error during stop: %s", e)
        finally:
            self.state = "stopped"
            self.started_at = None
            logger.info("Agent runtime stopped")

    async def restart(self) -> None:
        await self.stop()
        await self.start()

    # ---- config ---------------------------------------------------------

    def reload_config(self) -> None:
        """Re-read config from disk (hot-reload)."""
        self.config = load_config(self.config_path)
        self._config_generation += 1
        if self.run_manager:
            self.run_manager.config = self.config
        if self.engine:
            self.engine.config = self.config
        if self.compactor:
            self.compactor.context_window = self.config.agent.context_window
            self.compactor.reserve = self.config.agent.compaction_reserve
        # The autonomous components read config live; point them at the new one.
        if self.health_service:
            self.health_service.config = self.config
        if self.notifier:
            self.notifier.config = self.config
        if self.task_runner:
            self.task_runner.config = self.config
        if self.task_scheduler:
            self.task_scheduler.config = self.config
            # Re-evaluate the schedule now rather than waiting out the re-read
            # cap, so a maintenance toggle/edits take effect immediately.
            self.task_scheduler.wake()

    @property
    def config_generation(self) -> int:
        return self._config_generation

    # ---- status ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        import datetime

        uptime = 0.0
        if self.started_at:
            try:
                started = datetime.datetime.fromisoformat(self.started_at)
                uptime = (
                    datetime.datetime.now(datetime.timezone.utc) - started
                ).total_seconds()
            except (ValueError, TypeError):
                pass
        return {
            "state": self.state,
            "started_at": self.started_at,
            "active_runs": self.run_manager.active_count() if self.run_manager else 0,
            "uptime_seconds": round(uptime, 1),
            "config_generation": self._config_generation,
        }
