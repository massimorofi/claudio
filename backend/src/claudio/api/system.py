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
    """Readiness: config loaded, DB open."""
    require_auth(request)
    supervisor = request.app.state.supervisor
    ready = bool(supervisor.config and supervisor.db)
    return {"status": "ready" if ready else "not_ready"}


@router.get("/version")
async def version() -> dict:
    import agents
    from .. import __version__

    return {
        "backend": __version__,
        "openai_agents": getattr(agents, "__version__", "unknown"),
        "protocol": 1,
    }
