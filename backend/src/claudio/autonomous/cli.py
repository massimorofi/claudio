"""CLI for the autonomous subsystem — the entry point the systemd units call.

    python -m claudio.autonomous.cli health
    python -m claudio.autonomous.cli maintenance
    python -m claudio.autonomous.cli recovery
    python -m claudio.autonomous.cli task run <key>
    python -m claudio.autonomous.cli sudoers

Each command loads the live config + database, runs one pass, prints a short
result and exits. The exit code mirrors the verdict so a systemd unit can
surface failures: 0 = ok, 1 = warning, 2 = critical, 3 = the command errored.

``sudoers`` prints the generated ``/etc/sudoers.d/claudio`` snippet for the
current allowlist + restart list, so the operator can review / install it.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from ..settings import load_config
from ..db import Database
from .service import HealthService
from .notifier import Notifier
from .task_runner import TaskRunner
from .maintenance import run_maintenance
from .recovery import run_recovery
from . import allowlist

_EXIT = {"ok": 0, "warning": 1, "critical": 2}


def _backend_base() -> Path:
    return Path(__file__).resolve().parents[3]


def _config_path() -> Path:
    return _backend_base() / "config" / "config.yaml"


def _data_dir() -> Path:
    return _backend_base() / "data"


async def _run_health(config_path: Path) -> int:
    config = load_config(config_path)
    db = Database(_data_dir() / "claudio.db")
    await db.connect()
    try:
        service = HealthService(config, db, notifier=Notifier(config))
        result = await service.run_once(source="timer")
        print(f"{result['verdict'].upper()}: {result['summary']}")
        return _EXIT.get(result["verdict"], 1)
    finally:
        await db.close()


async def _run_maintenance(config_path: Path) -> int:
    config = load_config(config_path)
    db = Database(_data_dir() / "claudio.db")
    await db.connect()
    try:
        report = await run_maintenance(config, db, Notifier(config))
        print(report.report)
        return _EXIT.get(report.verdict, 1)
    finally:
        await db.close()


async def _run_recovery(config_path: Path) -> int:
    config = load_config(config_path)
    db = Database(_data_dir() / "claudio.db")
    await db.connect()
    try:
        report = await run_recovery(config, db, Notifier(config))
        print(report.report)
        return _EXIT.get(report.verdict, 1)
    finally:
        await db.close()


async def _run_task(config_path: Path, key: str) -> int:
    config = load_config(config_path)
    db = Database(_data_dir() / "claudio.db")
    await db.connect()
    try:
        runner = TaskRunner(config=config, db=db, notifier=Notifier(config))
        report = await runner.run(key, trigger="manual")
        print(report.report or report.summary)
        return _EXIT.get(report.verdict, 1)
    finally:
        await db.close()


def _print_sudoers(config_path: Path) -> int:
    config = load_config(config_path)
    print(allowlist.sudoers_snippet(config))
    return 0


_USAGE = """usage: claudio autonomous <command>

commands:
  health              run one health check
  maintenance         run one maintenance pass
  recovery            run one boot-recovery pass
  task run <key>      run one user-defined task now
  sudoers             print the generated sudoers snippet
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    command = argv[0] if argv else "health"
    config_path = _config_path()
    try:
        if command == "health":
            return asyncio.run(_run_health(config_path))
        if command == "maintenance":
            return asyncio.run(_run_maintenance(config_path))
        if command == "recovery":
            return asyncio.run(_run_recovery(config_path))
        if command == "task":
            if len(argv) < 3 or argv[1] != "run":
                print(_USAGE, file=sys.stderr)
                return 3
            return asyncio.run(_run_task(config_path, argv[2]))
        if command == "sudoers":
            return _print_sudoers(config_path)
        print(f"unknown command: {command}", file=sys.stderr)
        print(_USAGE, file=sys.stderr)
        return 3
    except Exception as exc:  # noqa: BLE001
        print(f"{command} error: {exc}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
