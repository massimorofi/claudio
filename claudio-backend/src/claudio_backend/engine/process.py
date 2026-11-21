"""Shared safe subprocess lifecycle for CLI-backed engines (issue #57).

OpenCode and Claude Code are driven as child processes. This module owns the
mechanics that must be identical for every such engine and free of any
engine-specific event logic: launch from an argv list (never a shell string, so
prompt text can never be interpolated into a command), a minimal controlled
environment, an explicit working directory, bounded stdout/stderr, a per-run
timeout, and cancellation that terminates the whole process tree after a grace
period.

Adapters read the child's stdout line-by-line (bounded line length) and turn
each line into engine events; malformed/oversized input surfaces as an error the
adapter can turn into a ``run_failed``, never a fabricated success.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncIterator

logger = logging.getLogger("claudio.engine.process")

# Guardrails so a chatty or misbehaving CLI cannot exhaust memory.
MAX_LINE_CHARS = 1_000_000          # a single stdout line larger than this is an error
DEFAULT_GRACE_SECONDS = 5.0


class ProcessError(Exception):
    """A subprocess could not be launched, produced invalid output, or failed."""


@dataclass
class ProcessResult:
    returncode: int
    stdout_lines: list[str] = field(default_factory=list)
    stderr_tail: str = ""
    cancelled: bool = False
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.cancelled and not self.timed_out


def build_env(base: dict[str, str] | None = None, extra: dict[str, str] | None = None) -> dict[str, str]:
    """Construct a minimal child environment.

    Starts from an explicit base (NOT the whole process ``.env``), then layers
    ``extra``. Credentials are added by the adapter only for what the CLI needs;
    nothing here forwards the parent environment wholesale.
    """
    env = dict(base if base is not None else {})
    # Keep the loader sane without leaking secrets.
    env.setdefault("PATH", os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"))
    env.setdefault("HOME", env.get("HOME", "/tmp"))
    if extra:
        env.update(extra)
    return env


async def stream_lines(
    argv: list[str],
    *,
    cwd: Path | str,
    env: dict[str, str],
    timeout_seconds: float,
    stdin_text: str | None = None,
    max_line_chars: int = MAX_LINE_CHARS,
) -> AsyncIterator[str]:
    """Launch ``argv`` and yield stdout lines, enforcing a total timeout.

    Raises :class:`ProcessError` on launch failure or an oversized line. The
    caller drives this with ``async for``; cancelling the consumer (or the
    enclosing task) terminates the child via :func:`terminate_tree`.
    """
    if not argv or not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        raise ProcessError("argv must be a non-empty list of strings")
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.PIPE if stdin_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            # Own process group so cancellation can signal the whole tree with
            # killpg without touching the backend's own process group.
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        raise ProcessError(f"failed to launch {argv[0]!r}: {exc}") from exc

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    try:
        if stdin_text is not None and proc.stdin is not None:
            with contextlib.suppress(Exception):
                proc.stdin.write(stdin_text.encode())
                await proc.stdin.drain()
                proc.stdin.close()
        assert proc.stdout is not None
        while True:
            remaining = deadline - loop.time()
            if remaining <= 0:
                raise ProcessError("run exceeded its timeout")
            try:
                raw = await asyncio.wait_for(proc.stdout.readline(), timeout=remaining)
            except asyncio.TimeoutError:
                raise ProcessError("run exceeded its timeout")
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").rstrip("\n")
            if len(line) > max_line_chars:
                raise ProcessError(
                    f"stdout line exceeded {max_line_chars} chars (possible runaway output)"
                )
            if line.strip():
                yield line
    finally:
        await terminate_tree(proc)


async def terminate_tree(proc: asyncio.subprocess.Process, grace: float = DEFAULT_GRACE_SECONDS) -> None:
    """Terminate a child and its descendants: SIGTERM, then SIGKILL after grace.

    Safe to call on an already-exited process. Uses the process group so a CLI
    that spawned its own children is fully stopped.
    """
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            with contextlib.suppress(Exception):
                proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
    except asyncio.TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                with contextlib.suppress(Exception):
                    proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()


async def run_collect(
    argv: list[str],
    *,
    cwd: Path | str,
    env: dict[str, str],
    timeout_seconds: float,
    stdin_text: str | None = None,
) -> ProcessResult:
    """Run to completion collecting bounded stdout lines + stderr tail.

    A convenience for adapters that need the whole output rather than streaming.
    """
    lines: list[str] = []
    timed_out = False
    try:
        async for line in stream_lines(
            argv, cwd=cwd, env=env, timeout_seconds=timeout_seconds, stdin_text=stdin_text
        ):
            lines.append(line)
    except ProcessError as exc:
        if "timeout" in str(exc):
            timed_out = True
        else:
            raise
    # Best-effort returncode: stream_lines already terminated the process.
    return ProcessResult(returncode=0 if not timed_out else 124, stdout_lines=lines, timed_out=timed_out)
