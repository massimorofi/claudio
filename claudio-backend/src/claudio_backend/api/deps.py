"""API dependencies: bearer-token auth.

``ApiError`` and ``error_envelope`` live in :mod:`claudio_backend.errors` so
the run layer can raise them without importing the HTTP layer; they are
re-exported here so route modules keep the ``from .deps import ...`` spelling.
"""
from __future__ import annotations

from fastapi import Request

from ..errors import ApiError, error_envelope

__all__ = ["require_auth", "ApiError", "error_envelope"]


def require_auth(request: Request) -> None:
    """Bearer-token auth dependency. Raises 401 on missing/invalid token."""
    supervisor = request.app.state.supervisor
    token = (supervisor.config.server.auth_token if supervisor.config else None) or ""
    header = request.headers.get("authorization", "")
    if not header.startswith("Bearer "):
        raise ApiError(401, "unauthorized", "Missing Authorization header")
    provided = header[len("Bearer ") :].strip()
    if not token or provided != token:
        raise ApiError(401, "unauthorized", "Invalid bearer token")
