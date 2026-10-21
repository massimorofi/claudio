"""Shared shell-script execution for the built-in checks.

The built-in health check and maintenance can each be overridden with an
operator-supplied shell script (``autonomous.health_check.script`` /
``autonomous.maintenance.script``). When a script is present it replaces the
built-in Python logic and runs directly: written to a private temp file
(mode 0700) inside the workspace, executed with bash under a wall-clock
timeout, and always removed afterwards.

This is the same execution model the user-defined *script tasks* use, so a
built-in check that has been turned into a script behaves "like any other
task": the operator owns the exact commands that run.

Exit-code convention (Nagios-style, the standard for monitoring scripts):

    0   -> ok
    1   -> warning
    2   -> critical
    any other non-zero, or a timeout -> critical
"""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path
from typing import Optional, Tuple


async def run_shell_script(
    script: str,
    workspace: str | Path,
    timeout: float = 300.0,
) -> Tuple[Optional[int], str, str]:
    """Run ``script`` under bash in ``workspace``.

    Returns ``(exit_code, stdout, stderr)``. ``exit_code`` is ``None`` when the
    script was killed for exceeding ``timeout``.
    """
    workspace = Path(workspace).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    fd, script_path = tempfile.mkstemp(
        prefix="claudio_check_", suffix=".sh", dir=str(workspace)
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
        # Shield the communicate() task so a timeout cancels the *wait*, not the
        # pipe drain — a cancelled communicate leaves the transport in a bad state
        # whose reader thread races the loop close.
        comm = asyncio.ensure_future(proc.communicate())
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                asyncio.shield(comm), timeout=timeout
            )
        except asyncio.TimeoutError:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
            try:
                await asyncio.wait_for(comm, timeout=5)
            except Exception:  # noqa: BLE001 — best-effort drain after a kill
                pass
            return None, "", f"script exceeded {timeout:.0f}s and was killed"
        rc = proc.returncode if proc.returncode is not None else -1
        out = (stdout_b or b"").decode(errors="replace")
        err = (stderr_b or b"").decode(errors="replace")
        return rc, out, err
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass


def verdict_from_exit(rc: Optional[int]) -> str:
    """Map a script exit code to a health verdict (Nagios convention)."""
    if rc == 0:
        return "ok"
    if rc == 1:
        return "warning"
    return "critical"


def first_line(text: str, limit: int = 200) -> str:
    """First non-empty line of ``text``, clipped to ``limit`` chars."""
    for line in (text or "").splitlines():
        if line.strip():
            return line.strip()[:limit]
    return ""
