"""End-to-end test: HTTP API -> RunManager -> engine, against a fake model.

Drives the real FastAPI app (routers, auth, error envelope, lifespan) with a
scripted OpenAI-compatible chat-completions server, so the whole request path
is exercised without a live model. Covers the chat UI's happy path and the
approval pause/resume round-trip.

The backend is served by a real uvicorn instance (not httpx's ASGITransport):
the SSE stream is long-lived and httpx's ASGI transport buffers the whole
response before returning, which cannot express a stream that stays open while
the client posts a mid-stream approval.

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_e2e.py -v
"""
from __future__ import annotations

import asyncio
import json
import socket
import tempfile
import threading
import time
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import uvicorn
import yaml


# --------------------------------------------------------------------------
# Fake OpenAI-compatible chat-completions server
# --------------------------------------------------------------------------

_PLAN: list[dict] = []
_REQS: list[dict] = []


def set_plan(turns: list[dict]) -> None:
    global _PLAN
    _PLAN = list(turns)


def reset_reqs() -> None:
    _REQS.clear()


async def fake_app(scope, receive, send):
    if scope["type"] == "lifespan":
        while True:
            msg = await receive()
            if msg["type"] == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return
            if msg["type"] == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
        return

    body = b""
    while True:
        msg = await receive()
        body += msg.get("body", b"")
        if not msg.get("more_body", False):
            break

    path = scope["path"]
    if path.endswith("/models") and scope["method"] == "GET":
        await _json(send, 200, {"object": "list", "data": [{"id": "fake-model", "object": "model"}]})
        return

    if path.endswith("/chat/completions"):
        req = json.loads(body) if body else {}
        _REQS.append(req)
        # Sentinel: a message carrying this string makes the endpoint reject
        # the credential, so tests can exercise a non-recoverable auth
        # failure regardless of the scripted plan.
        last_content = ""
        msgs = req.get("messages") or []
        if msgs:
            c = msgs[-1].get("content")
            if isinstance(c, str):
                last_content = c
            elif isinstance(c, list):
                last_content = " ".join(
                    str(p.get("text", "")) for p in c if isinstance(p, dict)
                )
        if "TRIGGER_AUTH_FAIL" in last_content:
            await _json(
                send, 401, {"error": {"message": "Incorrect API key provided"}}
            )
            return
        idx = len(_REQS) - 1
        turn = _PLAN[idx] if idx < len(_PLAN) else {"content": "ok", "finish_reason": "stop"}
        if req.get("stream"):
            await _sse(send, turn)
        else:
            await _json(send, 200, _completion(turn))
        return

    await _json(send, 404, {"error": "not found"})


def _completion(turn: dict) -> dict:
    msg: dict = {"role": "assistant"}
    if turn.get("content"):
        msg["content"] = turn["content"]
    if turn.get("tool_calls"):
        msg["tool_calls"] = [
            {
                "id": tc["id"],
                "type": "function",
                "function": {"name": tc["name"], "arguments": json.dumps(tc["arguments"])},
            }
            for tc in turn["tool_calls"]
        ]
    return {
        "id": "chatcmpl-fake",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "fake-model",
        "choices": [{"index": 0, "message": msg, "finish_reason": turn.get("finish_reason", "stop")}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }


async def _json(send, status: int, payload: dict) -> None:
    body = json.dumps(payload).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode()),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body, "more_body": False})


async def _sse(send, turn: dict) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/event-stream"),
                (b"cache-control", b"no-cache"),
            ],
        }
    )

    def frame(delta: dict, finish=None, usage=None) -> bytes:
        d = {
            "id": "chatcmpl-fake",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "fake-model",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
        if usage is not None:
            d["usage"] = usage
        return f"data: {json.dumps(d)}\n\n".encode()

    out = [frame({"role": "assistant", "content": ""})]
    text = turn.get("content") or ""
    for i in range(0, len(text), 24):
        out.append(frame({"content": text[i : i + 24]}))
    for idx, tc in enumerate(turn.get("tool_calls", [])):
        out.append(
            frame(
                {
                    "tool_calls": [
                        {
                            "index": idx,
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"]),
                            },
                        }
                    ]
                }
            )
        )
    finish = turn.get("finish_reason", "stop")
    out.append(frame({}, finish, {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}))
    out.append(b"data: [DONE]\n\n")
    for chunk in out:
        await send({"type": "http.response.body", "body": chunk, "more_body": True})
    await send({"type": "http.response.body", "body": b"", "more_body": False})


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _wait_up(url: str, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            httpx.get(url, timeout=1)
            return
        except Exception:
            time.sleep(0.1)
    raise RuntimeError(f"server at {url} did not start")


def start_fake_server(port: int) -> None:
    cfg = uvicorn.Config(fake_app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    _wait_up(f"http://127.0.0.1:{port}/v1/models")


# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

AUTH_TOKEN = "test-token-12345"


@pytest_asyncio.fixture
async def env():
    """Start the fake model server and a running backend served by real uvicorn.

    A real HTTP server (not ASGITransport) is required so the SSE stream is
    delivered incrementally: the approval round-trip reads events, posts a
    resolution mid-stream, and keeps reading the resumed run on the same
    connection.
    """
    model_port = free_port()
    start_fake_server(model_port)
    model_base_url = f"http://127.0.0.1:{model_port}/v1"

    tmp = Path(tempfile.mkdtemp(prefix="claudio_e2e_"))
    workspace = tmp / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "hello.txt").write_text("hello world\n")

    backend_port = free_port()
    config = {
        "server": {
            "host": "127.0.0.1",
            "port": backend_port,
            "auth_token": AUTH_TOKEN,
            "cors_origins": ["http://localhost:5173"],
            "expose_raw_config": False,
        },
        "llm": {
            "provider": "openai",
            "model": "fake-model",
            "api_key": "local",
            "base_url": model_base_url,
            "temperature": 0.2,
            "max_tokens": 256,
            "timeout_seconds": 30,
            "retry_attempts": 0,
        },
        "agent": {
            "name": "claudio",
            "max_turns": 5,
            "max_budget_usd": 0.0,
            "approval_mode": "on_request",
            "workspace_dir": str(workspace),
            "context_window": 32768,
            "compaction_reserve": 8192,
            "skills_enabled": False,
            "tools": {"enabled": ["read_file", "write_file"], "disabled": []},
        },
    }
    config_dir = tmp / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "config.yaml").write_text(yaml.safe_dump(config))

    from claudio_backend.app import build_app
    from claudio_backend.supervisor import Supervisor

    supervisor = Supervisor(tmp)
    await supervisor.start()
    app = build_app(supervisor)

    cfg = uvicorn.Config(app, host="127.0.0.1", port=backend_port, log_level="warning")
    server = uvicorn.Server(cfg)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{backend_port}"
    _wait_up(f"{base_url}/healthz")
    try:
        yield {
            "app": app,
            "workspace": workspace,
            "base_url": base_url,
            "model_base_url": model_base_url,
            "tmp": tmp,
        }
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        await supervisor.stop()


def auth() -> dict:
    return {"Authorization": f"Bearer {AUTH_TOKEN}"}


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_healthz_no_auth(env):
    async with httpx.AsyncClient(base_url=env["base_url"], timeout=10) as client:
        r = await client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_auth_required(env):
    async with httpx.AsyncClient(base_url=env["base_url"], timeout=10) as client:
        r = await client.get("/api/v1/sessions")
        assert r.status_code == 401
        body = r.json()
        assert body["error"]["code"] == "unauthorized"


@pytest.mark.asyncio
async def test_chat_stream_completes(env):
    set_plan([{"content": "Hello there! All good.", "finish_reason": "stop"}])
    reset_reqs()
    async with httpx.AsyncClient(base_url=env["base_url"], timeout=30) as client:
        events = []
        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            json={"message": "say hi", "approval_mode": "off"},
            headers=auth(),
        ) as resp:
            assert resp.status_code == 200
            name = None
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    name = line[len("event: ") :]
                elif line.startswith("data: "):
                    data = json.loads(line[len("data: ") :])
                    events.append((name, data))
                    if name in ("run_completed", "run_failed"):
                        break
        names = [n for n, _ in events]
        assert "run_started" in names
        assert "run_completed" in names
        # The assistant text made it through as token events.
        token_text = "".join(d.get("text", "") for n, d in events if n == "token")
        assert "Hello there" in token_text
        # The completion carried usage.
        completed = next(d for n, d in events if n == "run_completed")
        assert "final_output" in completed
        assert completed["usage"]["input_tokens"] >= 0


@pytest.mark.asyncio
async def test_chat_approval_pause_resume(env):
    set_plan(
        [
            {
                "tool_calls": [
                    {"id": "c1", "name": "write_file", "arguments": {"path": "out.txt", "content": "hi"}}
                ],
                "finish_reason": "tool_calls",
            },
            {"content": "Wrote it.", "finish_reason": "stop"},
        ]
    )
    reset_reqs()
    async with httpx.AsyncClient(base_url=env["base_url"], timeout=30) as client:
        events = []
        run_id = None
        approval_id = None
        # Read the live stream until the run pauses for approval.
        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            json={"message": "write a file", "approval_mode": "on_request"},
            headers=auth(),
        ) as resp:
            assert resp.status_code == 200
            name = None
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    name = line[len("event: ") :]
                elif line.startswith("data: "):
                    data = json.loads(line[len("data: ") :])
                    events.append((name, data))
                    if name == "run_started":
                        run_id = data["run_id"]
                    if name == "approval_required":
                        approval_id = data["approval_id"]
                        break
        names = [n for n, _ in events]
        assert "approval_required" in names
        assert run_id and approval_id

        # The pending approval was recorded under the id the event carried.
        pend = await client.get("/api/v1/approvals/pending", headers=auth())
        assert pend.status_code == 200
        pending = pend.json()["approvals"]
        assert any(a["approval_id"] == approval_id for a in pending)

        # Resolve it; the resumed run continues on a fresh stream to completion.
        r = await client.post(
            f"/api/v1/approvals/{approval_id}",
            json={"decision": "approve"},
            headers=auth(),
        )
        assert r.status_code == 200

        resumed = []
        async with client.stream(
            "GET",
            f"/api/v1/chat/runs/{run_id}/stream",
            headers=auth(),
        ) as resp:
            assert resp.status_code == 200
            name = None
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    name = line[len("event: ") :]
                elif line.startswith("data: "):
                    data = json.loads(line[len("data: ") :])
                    resumed.append((name, data))
                    if name in ("run_completed", "run_failed"):
                        break
        assert any(n == "run_completed" for n, _ in resumed), [n for n, _ in resumed]

        # Final state is completed.
        rr = await client.get(f"/api/v1/chat/runs/{run_id}", headers=auth())
        assert rr.json().get("state") == "completed"


@pytest.mark.asyncio
async def test_smoke_all_gets(env):
    """Every read endpoint responds without a server error (5xx)."""
    gets = [
        "/api/v1/agent/status",
        "/api/v1/agent/tools",
        "/api/v1/approvals/pending",
        "/api/v1/autonomous/audit",
        "/api/v1/autonomous/health",
        "/api/v1/autonomous/issues",
        "/api/v1/autonomous/status",
        "/api/v1/autonomous/tasks",
        "/api/v1/config",
        "/api/v1/config/history",
        "/api/v1/config/raw",
        "/api/v1/config/schema",
        "/api/v1/monitoring/health",
        "/api/v1/monitoring/logs",
        "/api/v1/monitoring/metrics",
        "/api/v1/monitoring/runs",
        "/api/v1/sessions",
        "/api/v1/skills",
        "/api/v1/version",
        "/api/v1/readyz",
        "/api/v1/workspace/tree",
        "/api/v1/workspace/files",
    ]
    async with httpx.AsyncClient(base_url=env["base_url"], timeout=15) as client:
        for g in gets:
            r = await client.get(g, headers=auth())
            assert r.status_code < 500, f"{g} -> {r.status_code}: {r.text[:200]}"


@pytest.mark.asyncio
async def test_sessions_lifecycle(env):
    set_plan([{"content": "done", "finish_reason": "stop"}])
    reset_reqs()
    async with httpx.AsyncClient(base_url=env["base_url"], timeout=30) as client:
        # Create a session implicitly via a chat run.
        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            json={"message": "hello session", "approval_mode": "off"},
            headers=auth(),
        ) as resp:
            async for line in resp.aiter_lines():
                if line.startswith("event: ") and line[len("event: ") :] in (
                    "run_completed",
                    "run_failed",
                ):
                    break
        sessions = await client.get("/api/v1/sessions", headers=auth())
        assert sessions.status_code == 200
        rows = sessions.json()["sessions"]
        assert len(rows) >= 1
        sid = rows[0]["id"]
        msgs = await client.get(f"/api/v1/sessions/{sid}/messages", headers=auth())
        assert msgs.status_code == 200
        messages = msgs.json()["messages"]
        # The user message was mirrored to the transcript.
        assert any(m.get("type") == "user" for m in messages)


@pytest.mark.asyncio
async def test_turn_count_matches_model_responses(env):
    """``turns`` counts model responses, not every non-user event.

    ADK authors the function-response echo with the agent's name too, so a
    naive ``author != "user"`` count adds one per tool round-trip on top of
    the model's own reply. Three scripted model responses -- two tool-call
    turns and a final text turn -- must report three turns, and the same
    number must reach the persisted run row.
    """
    set_plan(
        [
            {
                "tool_calls": [
                    {"id": "t1", "name": "read_file", "arguments": {"path": "hello.txt"}}
                ],
                "finish_reason": "tool_calls",
            },
            {
                "tool_calls": [
                    {"id": "t2", "name": "read_file", "arguments": {"path": "hello.txt"}}
                ],
                "finish_reason": "tool_calls",
            },
            {"content": "All done.", "finish_reason": "stop"},
        ]
    )
    reset_reqs()
    async with httpx.AsyncClient(base_url=env["base_url"], timeout=30) as client:
        completed = None
        async with client.stream(
            "POST",
            "/api/v1/chat/stream",
            json={"message": "read the file twice", "approval_mode": "off"},
            headers=auth(),
        ) as resp:
            assert resp.status_code == 200
            name = None
            async for line in resp.aiter_lines():
                if line.startswith("event: "):
                    name = line[len("event: ") :]
                elif line.startswith("data: "):
                    data = json.loads(line[len("data: ") :])
                    if name == "run_completed":
                        completed = data
                        break
                    if name == "run_failed":
                        raise AssertionError(f"run failed: {data}")
        assert completed is not None
        assert completed.get("turns") == 3, (
            f"expected 3 model responses, got turns={completed.get('turns')}"
        )
        run_id = completed["run_id"]
        rr = await client.get(f"/api/v1/chat/runs/{run_id}", headers=auth())
        assert rr.status_code == 200
        assert rr.json().get("turns_used") == 3, (
            f"turns_used not persisted: {rr.json().get('turns_used')}"
        )
