"""Tests for the built-in run-now endpoints added to the autonomous API:
``POST /autonomous/maintenance/run`` and ``POST /autonomous/recovery/run``.

These trigger the deterministic built-in handlers on demand (the scheduler
runs them on their own cron). The handlers themselves are covered in
test_maintenance.py / test_disaster_recovery.py; here we only check the
HTTP wiring: auth, delegation to the task runner, and the response shape.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from claudio.settings import AppConfig
from claudio.api.deps import ApiError, error_envelope
from claudio.api import autonomous as auto_api
from claudio.autonomous.report import TaskReport


AUTH = {"Authorization": "Bearer dev-token-change-me"}


class FakeTaskRunner:
    def __init__(self, verdict="ok"):
        self.verdict = verdict
        self.calls = []

    async def run_builtin(self, kind, trigger="scheduled", runner=None):
        self.calls.append((kind, trigger))
        return TaskReport(
            verdict=self.verdict,
            summary=f"{kind} ran",
            report=f"{kind} report body",
        )


class FakeHealthService:
    def __init__(self):
        self.notifier = object()

    async def run_once(self, source="manual"):
        return {"id": 1, "verdict": "ok", "summary": "all good"}


def make_client(verdict="ok"):
    app = FastAPI()
    cfg = AppConfig()
    cfg.server.auth_token = "dev-token-change-me"

    class Sup:
        pass

    sup = Sup()
    sup.config = cfg
    sup.task_runner = FakeTaskRunner(verdict=verdict)
    sup.health_service = FakeHealthService()

    async def _list_tasks():
        return []

    sup.db = type(
        "DB",
        (),
        {
            "get_latest_health_check": staticmethod(lambda: None),
            "count_open_issues": staticmethod(lambda: 0),
            "list_tasks": staticmethod(_list_tasks),
        },
    )()
    app.state.supervisor = sup

    @app.exception_handler(ApiError)
    async def _handler(request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=error_envelope(exc.code, exc.message, exc.retryable, exc.details),
        )

    app.include_router(auto_api.router)
    return TestClient(app)


def test_maintenance_run_now():
    client = make_client()
    resp = client.post("/autonomous/maintenance/run", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["report"]["verdict"] == "ok"
    assert "maintenance" in body["report"]["summary"]
    # delegated with the manual trigger
    assert client.app.state.supervisor.task_runner.calls == [("maintenance", "manual")]


def test_recovery_run_now():
    client = make_client()
    resp = client.post("/autonomous/recovery/run", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["report"]["verdict"] == "ok"
    assert client.app.state.supervisor.task_runner.calls == [("recovery", "manual")]


def test_maintenance_run_requires_auth():
    client = make_client()
    resp = client.post("/autonomous/maintenance/run")
    assert resp.status_code == 401


def test_recovery_run_requires_auth():
    client = make_client()
    resp = client.post("/autonomous/recovery/run")
    assert resp.status_code == 401


def test_run_now_surfaces_critical_verdict():
    client = make_client(verdict="critical")
    resp = client.post("/autonomous/maintenance/run", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["report"]["verdict"] == "critical"
