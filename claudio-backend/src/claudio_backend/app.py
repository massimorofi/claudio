"""Claudio backend — FastAPI application factory.

The supervisor (HTTP surface) is always up; the agent runtime inside it can be
started/stopped/restarted via the API. On boot we auto-start the agent runtime.

All routes are mounted under ``/api/v1``. The error envelope, CORS and the
request-counter middleware are installed here; the route modules themselves
stay thin and framework-light.

Two entry points share one configurator:

* :func:`create_app` builds the production app — it owns the supervisor and a
  lifespan that starts/stops it.
* :func:`build_app` takes an already-constructed supervisor and wires the same
  middleware, handlers and routers onto it. Used by tests that want to drive
  the real request path against a controlled supervisor.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from claudio_core.logging_setup import setup_logging
from claudio_core.settings import load_config

from .errors import ApiError, error_envelope
from .supervisor import Supervisor
from .api import (
    approvals,
    autonomous,
    chat,
    config,
    files,
    management,
    monitoring,
    sessions,
    system,
    tasks,
    workspace,
)

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]
CONFIG_PATH = BASE_DIR / "config" / "config.yaml"

API_PREFIX = "/api/v1"


def _load_cors_origins() -> list[str]:
    try:
        if CONFIG_PATH.exists():
            return load_config(CONFIG_PATH).server.cors_origins
    except Exception:  # noqa: BLE001
        pass
    return ["http://localhost:5173"]


def build_app(supervisor: Supervisor, lifespan: Callable | None = None) -> FastAPI:
    """Wire middleware, exception handlers and routers onto ``supervisor``.

    The supervisor is expected to already exist; whether it is started is the
    caller's concern. ``lifespan`` is an optional ASGI lifespan context manager
    (used by :func:`create_app` to auto-start the runtime).
    """
    app = FastAPI(
        title="Claudio Agent Backend",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.state.supervisor = supervisor

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_load_cors_origins(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_counter(request: Request, call_next):
        sup = getattr(request.app.state, "supervisor", None)
        if sup:
            sup.metrics.inc_request()
        return await call_next(request)

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=error_envelope(exc.code, exc.message, exc.retryable, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=error_envelope(
                "validation_error",
                "Request validation failed",
                False,
                {"errors": exc.errors()},
            ),
        )

    @app.exception_handler(Exception)
    async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=error_envelope("internal_error", str(exc), False),
        )

    # Mount routers under /api/v1.
    app.include_router(system.router, prefix=API_PREFIX)
    app.include_router(management.router, prefix=API_PREFIX)
    app.include_router(config.router, prefix=API_PREFIX)
    app.include_router(chat.router, prefix=API_PREFIX)
    app.include_router(approvals.router, prefix=API_PREFIX)
    app.include_router(sessions.router, prefix=API_PREFIX)
    app.include_router(monitoring.router, prefix=API_PREFIX)
    app.include_router(workspace.router, prefix=API_PREFIX)
    app.include_router(autonomous.router, prefix=API_PREFIX)
    app.include_router(tasks.router, prefix=API_PREFIX)
    app.include_router(files.router, prefix=API_PREFIX)

    @app.get("/")
    async def root() -> dict:
        return {"service": "claudio-backend", "docs": "/docs", "api": API_PREFIX}

    @app.get("/healthz")
    async def root_healthz() -> dict:
        """Root-level liveness probe (used by start.sh)."""
        return {"status": "ok"}

    return app


def create_app() -> FastAPI:
    """Build the production app: a supervisor plus a lifespan that runs it."""
    supervisor = Supervisor(BASE_DIR)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Load config for logging before the supervisor fully starts.
        try:
            cfg = load_config(CONFIG_PATH)
            setup_logging(cfg.monitoring.log_level)
        except Exception as e:  # noqa: BLE001
            setup_logging("INFO")
            logger.warning("Could not load config at startup: %s", e)
        # Auto-start the agent runtime; the supervisor serves regardless of outcome.
        try:
            await supervisor.start()
            logger.info("Agent runtime auto-started on boot")
        except Exception as e:  # noqa: BLE001
            logger.error("Agent runtime failed to auto-start: %s", e)
        yield
        await supervisor.stop()

    return build_app(supervisor, lifespan=lifespan)


app = create_app()
