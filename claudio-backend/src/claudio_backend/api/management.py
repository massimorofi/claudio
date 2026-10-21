"""Agent runtime management endpoints: start/stop/restart/status/tools."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request

from claudio_core.settings import AppConfig, load_config
from claudio_core.skill_loader import scan_skills
from claudio_core.tools.registry import tool_inventory

from .deps import require_auth, ApiError

router = APIRouter()


def _resolve_skills_dir(config: AppConfig) -> Path:
    """Resolve the configured skills directory to an absolute path.

    A relative ``skills_dir`` is anchored to the backend base directory (the
    folder that contains ``src/``), derived from this module's own location so it
    does not depend on the process cwd. An absolute ``skills_dir`` is used as-is.
    """
    p = Path(config.agent.skills_dir).expanduser()
    if p.is_absolute():
        return p
    backend_base = Path(__file__).resolve().parents[3]
    return (backend_base / p).resolve()


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


@router.get("/skills")
async def list_skills(request: Request) -> dict:
    """List installed Agent Skills (valid and broken) with their directory."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    config = supervisor.config
    if config is None:
        config = load_config(supervisor.config_path)
    skills_dir = _resolve_skills_dir(config)
    if not config.agent.skills_enabled:
        return {"directory": str(skills_dir), "enabled": False, "skills": []}
    try:
        scan = scan_skills(skills_dir)
    except (OSError, ValueError) as exc:
        raise ApiError(500, "skills_scan_failed", f"Failed to scan skills: {exc}")
    return {
        "directory": str(skills_dir),
        "enabled": True,
        "skills": scan.entries,
    }
