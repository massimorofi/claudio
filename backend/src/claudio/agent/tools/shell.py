"""Shell command tool — always approval-gated (destructive)."""
from __future__ import annotations

import asyncio

from agents import RunContextWrapper

from ..context import ToolContext


async def run_command(
    ctx: RunContextWrapper[ToolContext],
    command: str,
    timeout: float = 60.0,
) -> dict:
    """Run a shell command in the workspace directory.

    Args:
        command: The shell command to execute.
        timeout: Timeout in seconds (default 60).
    """
    tctx = ctx.context
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(tctx.workspace),
        )
    except OSError as e:
        return {"ok": False, "error": f"Failed to start command: {e}"}

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return {"ok": False, "error": f"Command timed out after {timeout}s"}

    out = stdout.decode(errors="replace") if stdout else ""
    err = stderr.decode(errors="replace") if stderr else ""
    max_chars = tctx.max_output_chars
    if len(out) > max_chars:
        out = out[:max_chars] + f"\n[clipped: {len(out) - max_chars} chars omitted]"
    if len(err) > max_chars:
        err = err[:max_chars] + f"\n[clipped: {len(err) - max_chars} chars omitted]"
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": out,
        "stderr": err,
    }
