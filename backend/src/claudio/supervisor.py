"""Supervisor: owns the agent runtime lifecycle (start/stop/restart) and shared
services. The HTTP surface stays up regardless of the agent runtime's state.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from .db import Database
from .settings import AppConfig, load_config
from .observability.metrics import Metrics
from .sessions.store import SessionStore
from .sessions.compaction import Compactor
from .runs.manager import RunManager

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
        self.run_manager: RunManager | None = None
        self._mcp_servers: list[Any] = []
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
            self.run_manager = RunManager(
                config=self.config,
                db=self.db,
                session_store=self.session_store,
                metrics=self.metrics,
                compactor=self.compactor,
            )
            await self._start_mcp()
            self.state = "running"
            from .db import utcnow

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
            # Cancel active runs.
            if self.run_manager:
                for run_id in list(self.run_manager._runs.keys()):
                    if self.run_manager._runs[run_id].state == "running":
                        await self.run_manager.cancel_run(run_id)
            await self._stop_mcp()
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
        if self.compactor:
            self.compactor.context_window = self.config.agent.context_window
            self.compactor.reserve = self.config.agent.compaction_reserve

    @property
    def config_generation(self) -> int:
        return self._config_generation

    # ---- MCP ------------------------------------------------------------

    async def _start_mcp(self) -> None:
        if not self.config:
            return
        from agents.mcp import MCPServerStdio, MCPServerSse, MCPServerStreamableHttp

        for entry in self.config.mcp.servers:
            if not entry.enabled:
                continue
            try:
                if entry.transport == "stdio":
                    server = MCPServerStdio(
                        params={"command": entry.command},
                        name=entry.id,
                    )
                elif entry.transport == "sse":
                    server = MCPServerSse(
                        params={"url": entry.url},
                        name=entry.id,
                    )
                elif entry.transport in ("streamable_http", "http"):
                    server = MCPServerStreamableHttp(
                        params={"url": entry.url},
                        name=entry.id,
                    )
                else:
                    logger.warning("Unknown MCP transport: %s", entry.transport)
                    continue
                await server.connect()
                self._mcp_servers.append(server)
                logger.info("Connected MCP server: %s", entry.id)
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to connect MCP server %s: %s", entry.id, e)

    async def _stop_mcp(self) -> None:
        for server in self._mcp_servers:
            try:
                await server.cleanup()
            except Exception as e:  # noqa: BLE001
                logger.debug("MCP cleanup error: %s", e)
        self._mcp_servers = []

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
