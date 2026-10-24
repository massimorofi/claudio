"""Live wire-contract tests: real HTTP JSON vs the TypeScript mirror.

``test_event_protocol.py`` checks the SSE *event names* against the TS mirror.
This file checks the *payload shapes* the HTTP API actually returns against the
TS interfaces that describe them -- the four places the handoff found drift
(``SessionSummary``, ``ApprovalRequest``, ``RunFailedEvent``) plus the nested
``error`` object. The point is to catch the class of bug where the TS interface
declares a field the backend never sends (or vice-versa), which a compile-time
type check cannot see because the JSON is untyped at the boundary.

The assertions parse the field names straight out of ``types.ts`` so the test
fails the moment the two disagree, in either direction.

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_contract_shapes.py -v
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest

# Reuse the running-backend fixture (real uvicorn + fake model server) from the
# e2e suite rather than rebuilding it here.
from test_e2e import env, auth, set_plan, reset_reqs  # noqa: F401

FRONTEND_TYPES = (
    Path(__file__).resolve().parents[2] / "claudio-frontend" / "src" / "api" / "types.ts"
)


def _ts_interface_body(ts: str, iface: str) -> str:
    """Return the text inside ``interface <iface> { ... }`` (outer braces excluded)."""
    key = f"interface {iface} {{"
    i = ts.find(key)
    if i == -1:
        raise AssertionError(f"`{key}` not found in {FRONTEND_TYPES}")
    start = i + len(key) - 1  # index of the opening '{'
    depth = 0
    for j in range(start, len(ts)):
        c = ts[j]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return ts[start + 1 : j]
    raise AssertionError(f"unbalanced braces in interface {iface}")


def _ts_top_level_fields(ts: str, iface: str) -> set[str]:
    """Top-level field names of a TS interface, ignoring nested object fields.

    Walks the interface body tracking brace/bracket depth so a nested object
    (e.g. ``RunFailedEvent.error``) contributes only its own key, not the
    keys inside it.
    """
    body = _ts_interface_body(ts, iface)
    fields: set[str] = set()
    depth = 0
    i = 0
    n = len(body)
    ident = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)\??:")
    while i < n:
        c = body[i]
        if c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        elif depth == 0:
            m = ident.match(body, i)
            if m and (i == 0 or not (body[i - 1].isalnum() or body[i - 1] == "_")):
                fields.add(m.group(1))
                i = m.end()
                continue
        i += 1
    return fields


@pytest.fixture(scope="module")
def ts_types() -> str:
    return FRONTEND_TYPES.read_text()


# --------------------------------------------------------------------------
# GET /sessions  ->  SessionSummary
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_session_summary_matches_ts(env, ts_types):
    set_plan([{"content": "hello", "finish_reason": "stop"}])
    reset_reqs()
    async with httpx.AsyncClient(base_url=env["base_url"], timeout=30) as client:
        # Create a session via a chat run so the list is non-empty.
        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            json={"message": "hi", "approval_mode": "off"},
            headers=auth(),
        ) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("event: ") and line[len("event: ") :] in (
                    "run_completed",
                    "run_failed",
                ):
                    break
        r = await client.get("/api/v1/sessions", headers=auth())
        assert r.status_code == 200
        rows = r.json()["sessions"]
        assert len(rows) >= 1
        expected = _ts_top_level_fields(ts_types, "SessionSummary")
        for row in rows:
            assert set(row.keys()) == expected, (
                f"session row keys {sorted(row.keys())} != TS SessionSummary {sorted(expected)}"
            )


# --------------------------------------------------------------------------
# GET /approvals/pending  ->  ApprovalRequest
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_approval_request_matches_ts(env, ts_types):
    # A write_file under on_request pauses for approval, populating the queue.
    set_plan(
        [
            {
                "tool_calls": [
                    {"id": "c1", "name": "write_file", "arguments": {"path": "x.txt", "content": "hi"}}
                ],
                "finish_reason": "tool_calls",
            },
            {"content": "done", "finish_reason": "stop"},
        ]
    )
    reset_reqs()
    async with httpx.AsyncClient(base_url=env["base_url"], timeout=30) as client:
        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            json={"message": "write a file", "approval_mode": "on_request"},
            headers=auth(),
        ) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("event: ") and line[len("event: ") :] in (
                    "approval_required",
                    "run_completed",
                    "run_failed",
                ):
                    break
        r = await client.get("/api/v1/approvals/pending", headers=auth())
        assert r.status_code == 200
        rows = r.json()["approvals"]
        assert len(rows) >= 1, "expected a pending approval to exercise the shape"
        expected = _ts_top_level_fields(ts_types, "ApprovalRequest")
        for row in rows:
            assert set(row.keys()) == expected, (
                f"approval row keys {sorted(row.keys())} != TS ApprovalRequest {sorted(expected)}"
            )


# --------------------------------------------------------------------------
# run_failed frame  ->  RunFailedEvent (+ nested error)
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_run_failed_frame_matches_ts(env, ts_types):
    # max_turns is 5 in the fixture; a plan that never stops trips
    # max_turns_exceeded, which surfaces as a run_failed frame.
    set_plan(
        [
            {
                "tool_calls": [
                    {"id": f"c{i}", "name": "read_file", "arguments": {"path": "hello.txt"}}
                ]
                if i % 2 == 0
                else {"id": f"c{i}", "name": "write_file", "arguments": {"path": "o.txt", "content": "x"}},
                "finish_reason": "tool_calls",
            }
            for i in range(8)
        ]
    )
    reset_reqs()
    async with httpx.AsyncClient(base_url=env["base_url"], timeout=30) as client:
        failed = None
        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            json={"message": "loop forever", "approval_mode": "off"},
            headers=auth(),
        ) as resp:
            assert resp.status_code == 200
            name = None
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    name = line[len("event: ") :]
                elif line.startswith("data: "):
                    data = json.loads(line[len("data: ") :])
                    if name == "run_failed":
                        failed = data
                        break
        assert failed is not None, "expected a run_failed frame"
        # Top-level: run_id + error only.
        assert set(failed.keys()) == _ts_top_level_fields(ts_types, "RunFailedEvent"), (
            f"run_failed keys {sorted(failed.keys())} != TS RunFailedEvent"
        )
        # Nested error: code + message + retryable.
        err = failed["error"]
        assert set(err.keys()) == {"code", "message", "retryable"}, (
            f"run_failed.error keys {sorted(err.keys())} != {{code, message, retryable}}"
        )
