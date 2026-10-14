"""API dependencies: auth and the standard error envelope."""
from __future__ import annotations

from typing import Any

from fastapi import Request, HTTPException


class ApiError(Exception):
    """Raised for expected API errors; rendered as the standard error envelope."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details or {}
        super().__init__(message)


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


def error_envelope(code: str, message: str, retryable: bool = False, details: dict | None = None) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        }
    }
