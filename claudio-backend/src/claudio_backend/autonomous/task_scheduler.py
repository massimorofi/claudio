"""Cron-based scheduler for user tasks and built-in maintenance.

Mirrors the shape of :class:`claudio_backend.autonomous.scheduler.HealthScheduler`
but schedules by cron (per-task) rather than a fixed interval. Each cycle it
re-reads the enabled tasks from the DB plus the built-in maintenance schedule,
computes the next fire time for each with ``croniter``, sleeps until the soonest
(capped so config changes are picked up even without a ``wake()``), and fires
whatever is due as a background task.

``wake()`` interrupts the current sleep so a task created / edited / toggled
through the API takes effect immediately instead of on the next cap tick.

Missed fires during downtime are skipped: the next fire is always computed from
``now``, never from a stale last-fire, so a laptop that slept through three
scheduled runs wakes up for the *next* one rather than firing three in a row.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from croniter import croniter

from claudio_core.db import Database
from claudio_core.settings import AppConfig

from .task_runner import TaskRunner

logger = logging.getLogger(__name__)

# Re-read the schedule at least this often even without a wake(), so external
# config changes (edits made directly to the DB / config file) are noticed.
MAX_SLEEP_SECONDS = 300.0
IDLE_SLEEP_SECONDS = 300.0

BUILTIN_MAINTENANCE = "builtin:maintenance"
BUILTIN_CONSOLIDATION = "builtin:consolidation"


def _cron_next(schedule: str, from_dt: datetime) -> datetime:
    return croniter(schedule, from_dt).get_next(datetime)


class TaskScheduler:
    def __init__(self, task_runner: TaskRunner, db: Database, config: AppConfig) -> None:
        self.task_runner = task_runner
        self.db = db
        self.config = config
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._next_fire: dict[str, datetime] = {}

    def _enabled(self) -> bool:
        return bool(self.config.autonomous.enabled)

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._loop())
        logger.info("Task scheduler started")

    async def stop(self) -> None:
        self._stop.set()
        self._wake.set()  # wake the loop so it can exit promptly
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001
                self._task.cancel()
            self._task = None
        logger.info("Task scheduler stopped")

    def wake(self) -> None:
        """Interrupt the current sleep so schedule changes take effect now."""
        self._wake.set()

    async def _wait(self, seconds: float) -> bool:
        """Sleep up to ``seconds``. Returns True if woken early by wake()."""
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
            self._wake.clear()
            return True
        except asyncio.TimeoutError:
            return False

    async def _collect_items(self) -> list[tuple[str, str]]:
        """(id, cron_schedule) for every scheduled item this cycle."""
        items: list[tuple[str, str]] = []
        for task in await self.db.list_enabled_tasks():
            items.append((f"task:{task['key']}", task["schedule"]))
        if self.config.autonomous.maintenance.enabled:
            items.append((BUILTIN_MAINTENANCE, self.config.autonomous.maintenance.schedule))
        if self.config.self_improvement.enabled:
            items.append(
                (BUILTIN_CONSOLIDATION, self.config.self_improvement.consolidation_schedule)
            )
        return items

    async def _tick(self) -> float:
        """One scheduling cycle: fire everything due, return seconds to sleep.

        Kept separate from :meth:`_loop` so it can be unit-tested without a
        running loop.
        """
        now = datetime.now(timezone.utc)
        items = await self._collect_items()
        item_ids = {i for i, _ in items}

        # Initialize next-fire for newly-seen items.
        for item_id, schedule in items:
            if item_id not in self._next_fire:
                self._next_fire[item_id] = _cron_next(schedule, now)

        # Drop fire entries for items that are gone / disabled.
        for stale in [k for k in self._next_fire if k not in item_ids]:
            del self._next_fire[stale]

        if not self._next_fire:
            return IDLE_SLEEP_SECONDS

        # Fire everything whose next-fire is now or past.
        for item_id, schedule in items:
            nf = self._next_fire.get(item_id)
            if nf is not None and now >= nf:
                self._spawn(item_id)
                # Recompute from now so missed fires are skipped.
                self._next_fire[item_id] = _cron_next(schedule, now)

        soonest = min(self._next_fire.values())
        wait = (soonest - now).total_seconds()
        return max(1.0, min(wait, MAX_SLEEP_SECONDS))

    async def _loop(self) -> None:
        while not self._stop.is_set():
            if not self._enabled():
                self._next_fire.clear()
                await self._wait(IDLE_SLEEP_SECONDS)
                continue
            wait = await self._tick()
            woke = await self._wait(wait)
            if woke:
                # A wake() means something changed — recompute immediately.
                continue

    def _spawn(self, item_id: str) -> None:
        if item_id == BUILTIN_MAINTENANCE:
            asyncio.create_task(self._run_builtin("maintenance"))
        elif item_id == BUILTIN_CONSOLIDATION:
            asyncio.create_task(self._run_builtin("consolidation"))
        elif item_id.startswith("task:"):
            key = item_id[len("task:"):]
            asyncio.create_task(self._run_task(key))
        else:
            logger.warning("unknown scheduled item: %s", item_id)

    async def _run_task(self, key: str) -> None:
        try:
            await self.task_runner.run(key, trigger="scheduled")
        except Exception as exc:  # noqa: BLE001 — never let a task kill the scheduler
            logger.warning("scheduled task %s failed: %s", key, exc)

    async def _run_builtin(self, kind: str) -> None:
        try:
            await self.task_runner.run_builtin(kind, trigger="scheduled")
        except Exception as exc:  # noqa: BLE001
            logger.warning("scheduled built-in %s failed: %s", kind, exc)
