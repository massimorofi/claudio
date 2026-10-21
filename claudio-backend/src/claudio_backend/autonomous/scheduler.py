"""Internal health scheduler (Phase 1: Watch).

A single asyncio task that runs the health check on a fixed interval. This is
the mechanism that makes the cockpit live without depending on systemd — the
systemd timer (``claudio-health.timer``) is the alternative for operators who
prefer OS-level scheduling; both call the same :class:`HealthService`.

The enabled flag and the interval are re-read from the live config on every
cycle, so toggling ``autonomous.enabled`` / ``health_check.enabled`` or
changing the interval via the config API takes effect on the next tick with no
process restart.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from claudio_core.db import utcnow

logger = logging.getLogger(__name__)

MIN_INTERVAL_SECONDS = 30


def _plus_seconds(seconds: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


class HealthScheduler:
    def __init__(self, service) -> None:
        self.service = service
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self.last_run_at: str | None = None
        self.next_run_at: str | None = None

    def _interval_seconds(self) -> int:
        hc = self.service.config.autonomous.health_check
        minutes = int(hc.interval_minutes or 10)
        return max(MIN_INTERVAL_SECONDS, minutes * 60)

    def _enabled(self) -> bool:
        a = self.service.config.autonomous
        return bool(a.enabled and a.health_check.enabled)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(self._loop())
        logger.info("Health scheduler started")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                self._task.cancel()
            self._task = None
        logger.info("Health scheduler stopped")

    async def _loop(self) -> None:
        while not self._stop.is_set():
            if self._enabled():
                self.last_run_at = utcnow()
                try:
                    await self.service.run_once("scheduled")
                except Exception as exc:  # noqa: BLE001 — never let one run kill the loop
                    logger.warning("scheduled health check failed: %s", exc)
            interval = self._interval_seconds()
            self.next_run_at = _plus_seconds(interval)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=interval)
                # stop was set during the wait — exit.
                return
            except asyncio.TimeoutError:
                # interval elapsed; loop runs the next check.
                continue
