"""Task CRUD endpoints for the autonomous task system.

User-defined tasks (a playbook prompt + the scoped daemon tool set, run on a
cron schedule) are managed here. Built-in maintenance / recovery are *not*
managed here — they live in the ``autonomous`` config block and are edited via
``/config``.

Every mutation pokes ``supervisor.task_scheduler.wake()`` so the running
scheduler recomputes its fire times immediately instead of waiting for the next
cap tick.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from croniter import croniter
from fastapi import APIRouter, Request
from pydantic import BaseModel, field_validator, model_validator

from .deps import require_auth, ApiError

router = APIRouter()

_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_VALID_VERDICTS = {"ok", "warning", "critical", "failure"}
_VALID_TYPES = {"prompt", "script"}


def _validate_key(v: str) -> str:
    if not _KEY_RE.match(v):
        raise ValueError(
            "key must start with a letter or digit and contain only lowercase "
            "letters, digits and hyphens"
        )
    return v


def _validate_schedule(v: str) -> str:
    if not croniter.is_valid(v):
        raise ValueError(f"invalid cron schedule: {v!r}")
    return v


def _validate_notify_on(v: list[str]) -> list[str]:
    bad = [x for x in v if x not in _VALID_VERDICTS]
    if bad:
        raise ValueError(f"unknown notify_on values: {bad}")
    return v


def _validate_type(v: str) -> str:
    if v not in _VALID_TYPES:
        raise ValueError(f"type must be one of {sorted(_VALID_TYPES)}")
    return v


def _validate_content(task_type: str, prompt: str | None, script: str | None) -> None:
    """A task must carry the content its type runs: a prompt for 'prompt' tasks,
    a script for 'script' tasks. Enforced on the merged effective values so a
    partial update can't leave a task with the wrong content for its type."""
    if task_type == "script":
        if not (script and script.strip()):
            raise ValueError("script tasks require a non-empty script")
    elif task_type == "prompt":
        if not (prompt and prompt.strip()):
            raise ValueError("prompt tasks require a non-empty prompt")


class TaskCreate(BaseModel):
    key: str
    name: str
    type: str = "prompt"
    prompt: str = ""
    script: str | None = None
    schedule: str
    notify_on: list[str] = ["warning", "critical", "failure"]
    report_only: bool = True
    enabled: bool = True

    @field_validator("key")
    @classmethod
    def _key(cls, v: str) -> str:
        return _validate_key(v)

    @field_validator("schedule")
    @classmethod
    def _sched(cls, v: str) -> str:
        return _validate_schedule(v)

    @field_validator("notify_on")
    @classmethod
    def _notify(cls, v: list[str]) -> list[str]:
        return _validate_notify_on(v)

    @field_validator("type")
    @classmethod
    def _type(cls, v: str) -> str:
        return _validate_type(v)

    @model_validator(mode="after")
    def _content(self) -> "TaskCreate":
        _validate_content(self.type, self.prompt, self.script)
        return self


class TaskUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    prompt: str | None = None
    script: str | None = None
    schedule: str | None = None
    notify_on: list[str] | None = None
    report_only: bool | None = None
    enabled: bool | None = None

    @field_validator("schedule")
    @classmethod
    def _sched(cls, v: str | None) -> str | None:
        return _validate_schedule(v) if v is not None else v

    @field_validator("notify_on")
    @classmethod
    def _notify(cls, v: list[str] | None) -> list[str] | None:
        return _validate_notify_on(v) if v is not None else v

    @field_validator("type")
    @classmethod
    def _type(cls, v: str | None) -> str | None:
        return _validate_type(v) if v is not None else v


def _next_run(schedule: str) -> str:
    return croniter(schedule, datetime.now(timezone.utc)).get_next(datetime).isoformat()


@router.get("/autonomous/tasks")
async def list_tasks(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    tasks = await supervisor.db.list_tasks()
    for t in tasks:
        try:
            t["next_run"] = _next_run(t["schedule"])
        except (ValueError, KeyError):
            t["next_run"] = None
    return {"tasks": tasks}


@router.post("/autonomous/tasks")
async def create_task(request: Request, payload: TaskCreate) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    existing = await supervisor.db.get_task(payload.key)
    if existing:
        raise ApiError(409, "already_exists", f"A task with key {payload.key!r} already exists")
    task = await supervisor.db.create_task(
        key=payload.key,
        name=payload.name,
        prompt=payload.prompt,
        schedule=payload.schedule,
        notify_on=payload.notify_on,
        report_only=payload.report_only,
        enabled=payload.enabled,
        type=payload.type,
        script=payload.script,
    )
    await supervisor.db.add_audit(action="task_created", command=payload.key, actor="operator")
    if supervisor.task_scheduler:
        supervisor.task_scheduler.wake()
    return {"task": task}


@router.put("/autonomous/tasks/{key}")
async def update_task(request: Request, key: str, payload: TaskUpdate) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    existing = await supervisor.db.get_task(key)
    if not existing:
        raise ApiError(404, "not_found", f"No task {key}")
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    # Validate the merged effective values: a partial update that flips the type
    # (or clears the content) must still leave a runnable task.
    eff_type = fields.get("type", existing.get("type", "prompt"))
    eff_prompt = fields.get("prompt", existing.get("prompt"))
    eff_script = fields.get("script", existing.get("script"))
    try:
        _validate_content(eff_type, eff_prompt, eff_script)
    except ValueError as e:
        raise ApiError(422, "validation_error", str(e))
    updated = await supervisor.db.update_task(key, **fields)
    await supervisor.db.add_audit(action="task_updated", command=key, actor="operator")
    if supervisor.task_scheduler:
        supervisor.task_scheduler.wake()
    return {"task": updated}


@router.delete("/autonomous/tasks/{key}")
async def delete_task(request: Request, key: str) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    existing = await supervisor.db.get_task(key)
    if not existing:
        raise ApiError(404, "not_found", f"No task {key}")
    await supervisor.db.delete_task(key)
    await supervisor.db.add_audit(action="task_deleted", command=key, actor="operator")
    if supervisor.task_scheduler:
        supervisor.task_scheduler.wake()
    return {"deleted": key}


@router.post("/autonomous/tasks/{key}/enable")
async def enable_task(request: Request, key: str) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    existing = await supervisor.db.get_task(key)
    if not existing:
        raise ApiError(404, "not_found", f"No task {key}")
    updated = await supervisor.db.update_task(key, enabled=True)
    if supervisor.task_scheduler:
        supervisor.task_scheduler.wake()
    return {"task": updated}


@router.post("/autonomous/tasks/{key}/disable")
async def disable_task(request: Request, key: str) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    existing = await supervisor.db.get_task(key)
    if not existing:
        raise ApiError(404, "not_found", f"No task {key}")
    updated = await supervisor.db.update_task(key, enabled=False)
    if supervisor.task_scheduler:
        supervisor.task_scheduler.wake()
    return {"task": updated}


@router.post("/autonomous/tasks/{key}/run")
async def run_task(request: Request, key: str) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    existing = await supervisor.db.get_task(key)
    if not existing:
        raise ApiError(404, "not_found", f"No task {key}")
    report = await supervisor.task_runner.run(key, trigger="manual")
    return {"report": report.to_dict()}


@router.get("/autonomous/tasks/{key}/runs")
async def task_runs(request: Request, key: str, limit: int = 50) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    limit = max(1, min(limit, 500))
    rows = await supervisor.db.list_task_runs(task_key=key, limit=limit)
    return {"runs": rows}
