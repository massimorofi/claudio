"""Shell command tool — always approval-gated (destructive)."""
from __future__ import annotations

import asyncio

import httpx

from ..context import ToolContext


async def _run_host_command(
    ctx: ToolContext,
    command: str,
    timeout: float,
) -> dict:
    socket_path = ctx.config.agent.host_execution_socket
    if not socket_path:
        return {
            "ok": False,
            "error": (
                "Host command execution is enabled but no host execution socket "
                "is configured."
            ),
        }

    transport = httpx.AsyncHTTPTransport(uds=socket_path)
    try:
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://claudio-host-executor",
            timeout=httpx.Timeout(timeout + 10),
        ) as client:
            response = await client.post(
                "/execute",
                json={
                    "command": command,
                    "cwd": str(ctx.workspace),
                    "timeout": timeout,
                },
            )
            result = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        return {
            "ok": False,
            "error": f"Host command execution failed: {exc}",
        }

    if response.is_error:
        message = result.get("error") if isinstance(result, dict) else None
        return {
            "ok": False,
            "error": message or f"Host command runner returned HTTP {response.status_code}.",
        }
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        return {
            "ok": False,
            "error": "Host command runner returned an invalid response.",
        }

    max_chars = ctx.max_output_chars
    for key in ("stdout", "stderr"):
        value = result.get(key, "")
        if not isinstance(value, str):
            return {
                "ok": False,
                "error": "Host command runner returned invalid output.",
            }
        if len(value) > max_chars:
            result[key] = value[:max_chars] + (
                f"\n[clipped: {len(value) - max_chars} chars omitted]"
            )
    return result


async def run_command(
    ctx: ToolContext,
    command: str,
    timeout: float = 60.0,
) -> dict:
    """Run a shell command in the workspace directory.

    Args:
        command: The shell command to execute.
        timeout: Timeout in seconds (default 60).
    """
    if ctx.config.agent.host_execution_enabled:
        return await _run_host_command(ctx, command, timeout)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(ctx.workspace),
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
    max_chars = ctx.max_output_chars
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
