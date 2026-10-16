"""Tests for the task CRUD API: validation (bad cron / bad key -> 422),
enable/disable, run-now, and the run-history endpoint.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from claudio.settings import AppConfig
from claudio.api.deps import ApiError, error_envelope
from claudio.api import tasks as tasks_api


AUTH = {"Authorization": "Bearer dev-token-change-me"}


class FakeDB:
    """In-memory stand-in for the task/audit DB methods the tasks API uses.

    Avoids binding a real aiosqlite connection to one event loop and using it
    from TestClient's separate loop.
    """

    def __init__(self):
        self.tasks: dict[str, dict] = {}
        self.audit: list[dict] = []
        self.runs: dict[str, list] = {}
        self._next_run_id = 1

    async def get_task(self, key):
        return self.tasks.get(key)

    async def create_task(self, key, name, prompt, schedule, approval_mode="off",
                         report_only=True, notify_on=None, enabled=True):
        if key in self.tasks:
            raise ValueError(f"task {key} exists")
        task = {
            "key": key, "name": name, "prompt": prompt, "schedule": schedule,
            "approval_mode": approval_mode, "report_only": bool(report_only),
            "notify_on": notify_on or ["warning", "critical", "failure"],
            "enabled": bool(enabled), "last_run_at": None, "next_run_at": None,
            "last_verdict": None, "created_at": "now", "updated_at": "now",
        }
        self.tasks[key] = task
        return task

    async def update_task(self, key, **fields):
        if key not in self.tasks:
            return None
        self.tasks[key].update(fields)
        return self.tasks[key]

    async def delete_task(self, key):
        self.tasks.pop(key, None)

    async def list_tasks(self):
        return list(self.tasks.values())

    async def add_audit(self, action, command=None, exit_code=None, detail=None, actor="operator"):
        self.audit.append({"action": action, "command": command})
        return len(self.audit)

    async def list_task_runs(self, task_key=None, limit=50):
        return self.runs.get(task_key, [])


class FakeScheduler:
    def __init__(self):
        self.wake_calls = 0

    def wake(self):
        self.wake_calls += 1


class FakeTaskRunner:
    def __init__(self, verdict="ok"):
        self.verdict = verdict
        self.calls = []

    async def run(self, key, trigger="scheduled"):
        self.calls.append((key, trigger))
        from claudio.autonomous.report import TaskReport

        return TaskReport(verdict=self.verdict, summary="ran", report="done")


def make_client(db, verdict="ok"):
    app = FastAPI()
    cfg = AppConfig()
    cfg.server.auth_token = "dev-token-change-me"

    class Sup:
        pass

    sup = Sup()
    sup.db = db
    sup.config = cfg
    sup.task_runner = FakeTaskRunner(verdict=verdict)
    sup.task_scheduler = FakeScheduler()
    app.state.supervisor = sup

    @app.exception_handler(ApiError)
    async def _handler(request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=error_envelope(exc.code, exc.message, exc.retryable, exc.details),
        )

    app.include_router(tasks_api.router)
    return TestClient(app)


@pytest.fixture
def db():
    return FakeDB()


# ---- create validation -------------------------------------------------


def test_create_task_ok(db):
    client = make_client(db)
    resp = client.post(
        "/autonomous/tasks",
        json={
            "key": "nightly",
            "name": "Nightly",
            "prompt": "do things",
            "schedule": "0 3 * * *",
        },
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["task"]["key"] == "nightly"


def test_create_task_bad_cron_422(db):
    client = make_client(db)
    resp = client.post(
        "/autonomous/tasks",
        json={
            "key": "bad",
            "name": "B",
            "prompt": "x",
            "schedule": "not a cron",
        },
        headers=AUTH,
    )
    assert resp.status_code == 422


def test_create_task_bad_key_422(db):
    client = make_client(db)
    resp = client.post(
        "/autonomous/tasks",
        json={
            "key": "Bad Key",
            "name": "B",
            "prompt": "x",
            "schedule": "0 3 * * *",
        },
        headers=AUTH,
    )
    assert resp.status_code == 422


def test_create_task_bad_notify_on_422(db):
    client = make_client(db)
    resp = client.post(
        "/autonomous/tasks",
        json={
            "key": "n",
            "name": "N",
            "prompt": "x",
            "schedule": "0 3 * * *",
            "notify_on": ["bogus"],
        },
        headers=AUTH,
    )
    assert resp.status_code == 422


def test_create_duplicate_key_409(db):
    client = make_client(db)
    payload = {"key": "dup", "name": "D", "prompt": "x", "schedule": "0 3 * * *"}
    r1 = client.post("/autonomous/tasks", json=payload, headers=AUTH)
    assert r1.status_code == 200
    r2 = client.post("/autonomous/tasks", json=payload, headers=AUTH)
    assert r2.status_code == 409


# ---- list --------------------------------------------------------------


def test_list_tasks_has_next_run(db):
    client = make_client(db)
    client.post(
        "/autonomous/tasks",
        json={"key": "t1", "name": "T", "prompt": "x", "schedule": "0 3 * * *"},
        headers=AUTH,
    )
    resp = client.get("/autonomous/tasks", headers=AUTH)
    assert resp.status_code == 200
    tasks = resp.json()["tasks"]
    assert len(tasks) == 1
    assert tasks[0]["next_run"] is not None


# ---- update ------------------------------------------------------------


def test_update_task(db):
    client = make_client(db)
    client.post(
        "/autonomous/tasks",
        json={"key": "u", "name": "U", "prompt": "x", "schedule": "0 3 * * *"},
        headers=AUTH,
    )
    resp = client.put(
        "/autonomous/tasks/u",
        json={"name": "Updated"},
        headers=AUTH,
    )
    assert resp.status_code == 200
    assert resp.json()["task"]["name"] == "Updated"


def test_update_task_bad_cron_422(db):
    client = make_client(db)
    client.post(
        "/autonomous/tasks",
        json={"key": "u2", "name": "U", "prompt": "x", "schedule": "0 3 * * *"},
        headers=AUTH,
    )
    resp = client.put(
        "/autonomous/tasks/u2",
        json={"schedule": "garbage"},
        headers=AUTH,
    )
    assert resp.status_code == 422


def test_update_missing_404(db):
    client = make_client(db)
    resp = client.put("/autonomous/tasks/nope", json={"name": "x"}, headers=AUTH)
    assert resp.status_code == 404


# ---- enable / disable --------------------------------------------------


def test_enable_disable(db):
    client = make_client(db)
    client.post(
        "/autonomous/tasks",
        json={"key": "ed", "name": "E", "prompt": "x", "schedule": "0 3 * * *", "enabled": True},
        headers=AUTH,
    )
    off = client.post("/autonomous/tasks/ed/disable", headers=AUTH)
    assert off.status_code == 200
    assert off.json()["task"]["enabled"] is False
    on = client.post("/autonomous/tasks/ed/enable", headers=AUTH)
    assert on.status_code == 200
    assert on.json()["task"]["enabled"] is True


def test_disable_missing_404(db):
    client = make_client(db)
    resp = client.post("/autonomous/tasks/ghost/disable", headers=AUTH)
    assert resp.status_code == 404


# ---- run now -----------------------------------------------------------


def test_run_now(db):
    client = make_client(db)
    client.post(
        "/autonomous/tasks",
        json={"key": "run1", "name": "R", "prompt": "x", "schedule": "0 3 * * *"},
        headers=AUTH,
    )
    resp = client.post("/autonomous/tasks/run1/run", headers=AUTH)
    assert resp.status_code == 200
    assert resp.json()["report"]["verdict"] == "ok"


def test_run_now_missing_404(db):
    client = make_client(db)
    resp = client.post("/autonomous/tasks/ghost/run", headers=AUTH)
    assert resp.status_code == 404


# ---- run history -------------------------------------------------------


def test_run_history(db):
    client = make_client(db)
    client.post(
        "/autonomous/tasks",
        json={"key": "hist", "name": "H", "prompt": "x", "schedule": "0 3 * * *"},
        headers=AUTH,
    )
    resp = client.get("/autonomous/tasks/hist/runs", headers=AUTH)
    assert resp.status_code == 200
    assert isinstance(resp.json()["runs"], list)


# ---- auth --------------------------------------------------------------


def test_requires_auth(db):
    client = make_client(db)
    resp = client.get("/autonomous/tasks")
    assert resp.status_code == 401
