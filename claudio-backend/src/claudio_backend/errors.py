"""Shared error types for the backend.

``ApiError`` is the single exception the HTTP layer renders as the standard
error envelope. It lives here (rather than in ``api.deps``) so the run layer
can raise a typed error without depending on the HTTP layer that renders it.
"""
from __future__ import annotations

from typing import Any


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


def error_envelope(
    code: str,
    message: str,
    retryable: bool = False,
    details: dict | None = None,
) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "details": details or {},
        }
    }


__all__ = ["ApiError", "error_envelope"]
