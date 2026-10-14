"""Claudio backend — FastAPI application entry point.

The supervisor (HTTP surface) is always up; the agent runtime inside it can be
started/stopped/restarted via the API. On boot we auto-start the agent runtime.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError

from .settings import load_config
from .supervisor import Supervisor
from .observability.logging import setup_logging
from .api.deps import ApiError, error_envelope
from .api import system, management, config, chat, approvals, sessions, monitoring

# Disable OpenAI tracing — we run against local/compatible endpoints and rely on
# our own run/event records, not OpenAI's trace exporter.
try:
    from agents import set_tracing_disabled

    set_tracing_disabled(True)
except Exception:  # pragma: no cover
    pass

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Load config for CORS before the supervisor fully starts.
    config_path = BASE_DIR / "config" / "config.yaml"
    try:
        cfg = load_config(config_path)
        setup_logging(cfg.monitoring.log_level)
    except Exception as e:  # noqa: BLE001
        setup_logging("INFO")
        logger.warning("Could not load config at startup: %s", e)
        cfg = None

    supervisor = Supervisor(BASE_DIR)
    app.state.supervisor = supervisor

    # Auto-start the agent runtime; the supervisor serves regardless of outcome.
    try:
        await supervisor.start()
        logger.info("Agent runtime auto-started on boot")
    except Exception as e:  # noqa: BLE001
        logger.error("Agent runtime failed to auto-start: %s", e)

    yield

    await supervisor.stop()


app = FastAPI(
    title="Claudio Agent Backend",
    version="1.0.0",
    lifespan=lifespan,
)

# CORS — read origins from config at startup.
try:
    _cors = (
        load_config(BASE_DIR / "config" / "config.yaml").server.cors_origins
        if (BASE_DIR / "config" / "config.yaml").exists()
        else ["http://localhost:5173"]
    )
except Exception:  # noqa: BLE001
    _cors = ["http://localhost:5173"]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def request_counter(request: Request, call_next):
    supervisor = getattr(request.app.state, "supervisor", None)
    if supervisor:
        supervisor.metrics.inc_request()
    return await call_next(request)


@app.exception_handler(ApiError)
async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content=error_envelope(exc.code, exc.message, exc.retryable, exc.details),
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
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
API_PREFIX = "/api/v1"
app.include_router(system.router, prefix=API_PREFIX)
app.include_router(management.router, prefix=API_PREFIX)
app.include_router(config.router, prefix=API_PREFIX)
app.include_router(chat.router, prefix=API_PREFIX)
app.include_router(approvals.router, prefix=API_PREFIX)
app.include_router(sessions.router, prefix=API_PREFIX)
app.include_router(monitoring.router, prefix=API_PREFIX)


@app.get("/")
async def root() -> dict:
    return {"service": "claudio-backend", "docs": "/docs", "api": API_PREFIX}


@app.get("/healthz")
async def root_healthz() -> dict:
    """Root-level liveness probe (used by start.sh)."""
    return {"status": "ok"}
