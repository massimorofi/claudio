"""Agent runtime management endpoints: start/stop/restart/status/tools."""
from __future__ import annotations

from fastapi import APIRouter, Request

from .deps import require_auth, ApiError
from ..agent.tools.registry import tool_inventory

router = APIRouter()


@router.post("/agent/start", status_code=202)
async def agent_start(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    if supervisor.state == "running":
        raise ApiError(409, "agent_already_running", "Agent runtime is already running")
    try:
        await supervisor.start()
    except Exception as e:  # noqa: BLE001
        raise ApiError(409, "agent_start_failed", f"Failed to start agent: {e}")
    return {"state": "running"}


@router.post("/agent/stop")
async def agent_stop(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    await supervisor.stop()
    return {"state": "stopped"}


@router.post("/agent/restart", status_code=202)
async def agent_restart(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    try:
        await supervisor.restart()
    except Exception as e:  # noqa: BLE001
        raise ApiError(409, "agent_start_failed", f"Failed to restart agent: {e}")
    return {"state": "running"}


@router.get("/agent/status")
async def agent_status(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    return supervisor.status()


@router.get("/agent/tools")
async def agent_tools(request: Request) -> dict:
    require_auth(request)
    return {"tools": tool_inventory()}
