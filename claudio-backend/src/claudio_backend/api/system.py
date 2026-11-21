"""System endpoints: liveness, readiness, version."""
from __future__ import annotations

from fastapi import APIRouter, Request

from .deps import require_auth

router = APIRouter()


@router.get("/healthz")
async def healthz() -> dict:
    """Liveness probe. No auth."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz(request: Request) -> dict:
    """Readiness: config loaded, DB open, and the selected engine initialized."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    engine_ready = getattr(supervisor, "engine_ready", True)
    ready = bool(supervisor.config and supervisor.db) and engine_ready
    return {"status": "ready" if ready else "not_ready"}


@router.get("/version")
async def version(request: Request) -> dict:
    from .. import __version__

    supervisor = getattr(request.app.state, "supervisor", None)
    engine_id = "adk"
    engine_version = "unknown"
    capabilities: dict = {}
    if supervisor is not None and getattr(supervisor, "engine", None) is not None:
        desc = getattr(supervisor.engine, "descriptor", None)
        if desc is not None:
            engine_id = desc.id
            engine_version = desc.version
            capabilities = {k: v for k, v in desc.capabilities.__dict__.items()}
    return {
        "backend": __version__,
        "engine": engine_id,
        "engine_version": engine_version,
        "engine_capabilities": capabilities,
        "protocol": 1,
    }
