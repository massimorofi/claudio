from __future__ import annotations

import asyncio
import multiprocessing
import stat
import threading
import time
from pathlib import Path

from claudio_backend.host_executor import (
    HostExecutor,
    ThreadingUnixHTTPServer,
    make_handler,
    serve,
)
from claudio_core.context import ToolContext
from claudio_core.settings import AppConfig
from claudio_core.tools.shell import run_command


def _start_server(tmp_path: Path):
    socket_path = tmp_path / "host-exec.sock"
    executor = HostExecutor(tmp_path, [tmp_path])
    server = ThreadingUnixHTTPServer(str(socket_path), make_handler(executor))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return socket_path, server, thread


def test_host_executor_runs_in_host_workspace(tmp_path):
    socket_path, server, thread = _start_server(tmp_path)
    try:
        config = AppConfig(
            agent={
                "host_execution_enabled": True,
                "host_execution_socket": str(socket_path),
            }
        )
        ctx = ToolContext(
            config=config,
            session_id="test",
            workspace=Path("/workspace"),
            approval_mode="off",
            db=None,
        )

        result = asyncio.run(run_command(ctx, "pwd"))

        assert result["ok"] is True
        assert Path(result["stdout"].strip()) == tmp_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_host_executor_rejects_working_directory_outside_allowed_roots(tmp_path):
    executor = HostExecutor(tmp_path, [tmp_path])

    status, result = executor.execute(
        {"command": "pwd", "cwd": str(tmp_path.parent), "timeout": 1}
    )

    assert status == 403
    assert result["ok"] is False
    assert "outside allowed host roots" in result["error"]


def test_host_executor_rejects_invalid_timeout(tmp_path):
    executor = HostExecutor(tmp_path, [tmp_path])

    status, result = executor.execute(
        {"command": "pwd", "cwd": "/workspace", "timeout": 601}
    )

    assert status == 400
    assert result["ok"] is False
    assert "timeout" in result["error"]


def test_host_executor_terminates_timed_out_command(tmp_path):
    executor = HostExecutor(tmp_path, [tmp_path])

    status, result = executor.execute(
        {"command": "sleep 5", "cwd": "/workspace", "timeout": 0.1}
    )

    assert status == 200
    assert result["ok"] is False
    assert "timed out" in result["error"]


def test_host_executor_shutdown_terminates_active_commands(tmp_path):
    executor = HostExecutor(tmp_path, [tmp_path])
    result = []
    worker = threading.Thread(
        target=lambda: result.append(
            executor.execute(
                {"command": "sleep 10", "cwd": "/workspace", "timeout": 30}
            )
        )
    )
    worker.start()
    for _ in range(100):
        with executor._process_lock:
            if executor._active_processes:
                break
        time.sleep(0.01)

    executor.terminate_active()
    worker.join(timeout=2)

    assert not worker.is_alive()
    assert result[0][1]["ok"] is False


def test_host_executor_serve_uses_private_socket_and_cleans_up(tmp_path):
    socket_path = tmp_path / "host-exec.sock"
    process = multiprocessing.Process(
        target=serve,
        args=(socket_path, tmp_path, [tmp_path]),
    )
    process.start()
    try:
        for _ in range(100):
            if socket_path.exists():
                break
            time.sleep(0.01)
        assert socket_path.is_socket()
        assert stat.S_IMODE(socket_path.stat().st_mode) == 0o600
    finally:
        process.terminate()
        process.join(timeout=2)

    assert not process.is_alive()
    assert not socket_path.exists()
