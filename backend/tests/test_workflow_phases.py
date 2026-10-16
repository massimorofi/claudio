"""Tests for the workflow-view phases added to the run/session layer.

Covers Phase 4 (context usage payload + manual compact endpoint) and Phase 5
(produced-artifact tracking + turn_status persistence). Pure-Python where
possible; the compact endpoint is exercised through a minimal FastAPI app with a
fake supervisor so no live model is required.
"""
from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from claudio.settings import AppConfig
from claudio.runs.manager import (
    RunRecord,
    RunManager,
    _context_usage_payload,
    _track_produced,
)
from claudio.api import sessions as sessions_api
from claudio.api.deps import ApiError, error_envelope
from fastapi.responses import JSONResponse


# ---- Phase 4: context usage payload -------------------------------------


def test_context_usage_payload_math_against_usable_window():
    cfg = AppConfig()  # window 32768, reserve 8192 -> usable 24576
    p = _context_usage_payload(12288, cfg)
    assert p["usable_tokens"] == 24576
    assert p["free_tokens"] == 12288
    assert p["percent_used"] == 50.0
    assert p["compact_threshold"] == 24576
    assert p["source"] == "estimate"
    assert p["auto_disabled"] is False


def test_context_usage_payload_clamps_over_threshold():
    cfg = AppConfig()
    p = _context_usage_payload(30000, cfg)
    # Used exceeds usable: free floors at 0, percent caps at 100.
    assert p["free_tokens"] == 0
    assert p["percent_used"] == 100.0


def test_context_usage_payload_carries_source_and_breaker():
    cfg = AppConfig()
    p = _context_usage_payload(100, cfg, "model", auto_disabled=True)
    assert p["source"] == "model"
    assert p["auto_disabled"] is True


# ---- Phase 5: produced-artifact tracking -------------------------------


def test_track_produced_records_written_files():
    r = RunRecord(run_id="r1", session_id="s1")
    _track_produced(r, {"name": "write_file", "arguments": json.dumps({"path": "out/a.py"})})
    _track_produced(r, {"name": "edit_file", "arguments": json.dumps({"path": "b.py"})})
    assert r.files_written == ["out/a.py", "b.py"]
    assert r.commands_run == 0


def test_track_produced_counts_commands():
    r = RunRecord(run_id="r1", session_id="s1")
    _track_produced(r, {"name": "run_command", "arguments": json.dumps({"command": "ls"})})
    _track_produced(r, {"name": "run_command", "arguments": json.dumps({"command": "pwd"})})
    assert r.commands_run == 2
    assert r.files_written == []


def test_track_produced_ignores_read_only_tools():
    r = RunRecord(run_id="r1", session_id="s1")
    _track_produced(r, {"name": "read_file", "arguments": json.dumps({"path": "x"})})
    _track_produced(r, {"name": "web_search", "arguments": json.dumps({"query": "q"})})
    assert r.files_written == []
    assert r.commands_run == 0


def test_track_produced_tolerates_malformed_arguments():
    r = RunRecord(run_id="r1", session_id="s1")
    # Not JSON at all -> no crash, no path recorded.
    _track_produced(r, {"name": "write_file", "arguments": "not-json"})
    assert r.files_written == []


# ---- Phase 1: tool-event transcript mirroring --------------------------


@pytest.mark.asyncio
async def test_mirror_tool_event_writes_call_and_result():
    mgr, store, _ = make_manager()
    record = RunRecord(run_id="r1", session_id="s1")
    mgr._mirror_tool_event(
        record,
        {"event": "tool_call", "data": {"call_id": "c1", "name": "write_file", "arguments": "{}"}},
    )
    mgr._mirror_tool_event(
        record,
        {"event": "tool_result", "data": {"call_id": "c1", "name": "write_file", "output": "ok", "is_error": False}},
    )
    assert [e["type"] for e in store.entries] == ["tool_call", "tool_result"]
    call = store.entries[0]
    assert call["call_id"] == "c1"
    assert call["name"] == "write_file"
    assert call["run_id"] == "r1"
    res = store.entries[1]
    assert res["call_id"] == "c1"
    assert res["is_error"] is False


@pytest.mark.asyncio
async def test_mirror_tool_event_ignores_non_tool_events():
    mgr, store, _ = make_manager()
    record = RunRecord(run_id="r1", session_id="s1")
    mgr._mirror_tool_event(record, {"event": "token", "data": {"text": "hi"}})
    mgr._mirror_tool_event(record, {"event": "run_completed", "data": {}})
    assert store.entries == []


# ---- Phase 5: turn_status persistence ----------------------------------


class RecordingStore:
    def __init__(self):
        self.entries = []

    def append(self, session_id, entry):
        self.entries.append(entry)

    def make_sdk_session(self, session_id):
        return FakeSession([])


class FakeSession:
    def __init__(self, items):
        self._items = list(items)

    async def get_items(self):
        return list(self._items)

    async def add_items(self, items):
        self._items.extend(items)

    async def clear_session(self):
        self._items = []


class FakeDB:
    def __init__(self, session_row=None):
        self._session_row = session_row or {
            "id": "s1",
            "total_cost_usd": 0.0,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "message_count": 0,
        }
        self.run_updates = []
        self.session_updates = []

    async def get_session(self, session_id):
        return dict(self._session_row)

    async def update_run(self, run_id, **fields):
        self.run_updates.append(fields)

    async def update_session(self, session_id, **fields):
        self.session_updates.append(fields)


class FakeMetrics:
    def __init__(self):
        self.completed = 0
        self.failed = 0

    def inc_run_completed(self):
        self.completed += 1

    def inc_run_failed(self):
        self.failed += 1

    def add_usage(self, *a, **k):
        pass


class FakeResult:
    final_output = "done"
    raw_responses = []


def make_manager():
    cfg = AppConfig()
    store = RecordingStore()
    db = FakeDB()
    metrics = FakeMetrics()
    compactor = FakeCompactorStub()
    return RunManager(cfg, db, store, metrics, compactor), store, db


class FakeCompactorStub:
    def is_disabled(self, session_id):
        return False

    async def compact(self, session, model, session_id):
        return True


@pytest.mark.asyncio
async def test_finalize_completed_writes_turn_status():
    mgr, store, db = make_manager()
    record = RunRecord(run_id="r1", session_id="s1")
    record.files_written = ["tetris.html"]
    record.commands_run = 2
    await mgr._finalize_completed(record, FakeResult(), FakeSession([]))

    turn_entries = [e for e in store.entries if e.get("type") == "turn_status"]
    assert len(turn_entries) == 1
    ts = turn_entries[0]
    assert ts["status"] == "completed"
    assert ts["files_written"] == ["tetris.html"]
    assert ts["commands_run"] == 2
    assert "duration_seconds" in ts


@pytest.mark.asyncio
async def test_finalize_failed_writes_turn_status():
    mgr, store, _ = make_manager()
    record = RunRecord(run_id="r1", session_id="s1")
    await mgr._finalize_failed(record, RuntimeError("boom"))
    turn_entries = [e for e in store.entries if e.get("type") == "turn_status"]
    assert len(turn_entries) == 1
    assert turn_entries[0]["status"] == "failed"
    assert turn_entries[0]["error_message"] == "boom"


@pytest.mark.asyncio
async def test_finalize_cancelled_writes_interrupted():
    mgr, store, _ = make_manager()
    record = RunRecord(run_id="r1", session_id="s1")
    await mgr._finalize_cancelled(record)
    turn_entries = [e for e in store.entries if e.get("type") == "turn_status"]
    assert len(turn_entries) == 1
    assert turn_entries[0]["status"] == "interrupted"


# ---- Phase 4: manual compact endpoint ----------------------------------


class EndpointSupervisor:
    def __init__(self, active=None, compact_ok=True, disabled=False):
        self.config = AppConfig()
        self.db = FakeDB()
        self.session_store = RecordingStore()
        self.compactor = _EndpointCompactor(compact_ok, disabled)
        self.run_manager = _EndpointRunManager(active)


class _EndpointCompactor:
    def __init__(self, ok, disabled):
        self.ok = ok
        self.disabled = disabled
        self.context_window = 32768
        self.reserve = 8192

    def is_disabled(self, session_id):
        return self.disabled

    async def compact(self, session, model, session_id):
        return self.ok


class _EndpointRunManager:
    def __init__(self, active):
        self._active = active

    def get_active_run_for_session(self, session_id):
        return self._active


def make_client(supervisor) -> TestClient:
    app = FastAPI()
    app.state.supervisor = supervisor

    @app.exception_handler(ApiError)
    async def _handler(request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status,
            content=error_envelope(exc.code, exc.message, exc.retryable, exc.details),
        )

    app.include_router(sessions_api.router)
    return TestClient(app)


AUTH = {"Authorization": "Bearer dev-token-change-me"}


def test_compact_endpoint_success():
    sup = EndpointSupervisor(compact_ok=True)
    client = make_client(sup)
    resp = client.post("/sessions/s1/compact", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert "used_tokens" in body
    assert "context_usage" in body
    # last_context_tokens persisted on the session row.
    assert any("last_context_tokens" in u for u in sup.db.session_updates)


def test_compact_endpoint_busy_when_run_active():
    sup = EndpointSupervisor(active=object())
    client = make_client(sup)
    resp = client.post("/sessions/s1/compact", headers=AUTH)
    assert resp.status_code == 409


def test_compact_endpoint_breaker_tripped():
    sup = EndpointSupervisor(compact_ok=False, disabled=True)
    client = make_client(sup)
    resp = client.post("/sessions/s1/compact", headers=AUTH)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "circuit breaker" in body["error"]


def test_compact_endpoint_requires_auth():
    sup = EndpointSupervisor()
    client = make_client(sup)
    resp = client.post("/sessions/s1/compact")
    assert resp.status_code == 401
