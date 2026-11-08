"""Host-side command runner for the Docker deployment."""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import signal
import socketserver
import subprocess
import threading
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any

logger = logging.getLogger("claudio.host_executor")
MAX_REQUEST_BYTES = 64 * 1024
MAX_COMMAND_CHARS = 32 * 1024
MAX_TIMEOUT_SECONDS = 600.0
MAX_RESPONSE_CHARS = 64 * 1024


class HostExecutor:
    def __init__(self, workspace_root: Path, allowed_roots: list[Path]):
        self.workspace_root = workspace_root.resolve(strict=True)
        self.allowed_roots = [root.resolve(strict=True) for root in allowed_roots]
        self._active_processes: set[subprocess.Popen[bytes]] = set()
        self._process_lock = threading.Lock()
        self._stopping = False

    def execute(self, payload: Any) -> tuple[int, dict[str, Any]]:
        with self._process_lock:
            if self._stopping:
                return 503, {"ok": False, "error": "Host command runner is stopping."}

        if not isinstance(payload, dict):
            return 400, {"ok": False, "error": "Request must be a JSON object."}

        command = payload.get("command")
        requested_cwd = payload.get("cwd")
        timeout = payload.get("timeout", 60)
        if not isinstance(command, str) or not command or len(command) > MAX_COMMAND_CHARS:
            return 400, {"ok": False, "error": "Invalid command."}
        if not isinstance(requested_cwd, str) or not Path(requested_cwd).is_absolute():
            return 400, {"ok": False, "error": "Working directory must be absolute."}
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
            or timeout > MAX_TIMEOUT_SECONDS
        ):
            return 400, {"ok": False, "error": "Invalid command timeout."}

        container_cwd = Path(requested_cwd)
        try:
            relative_workspace_path = container_cwd.relative_to("/workspace")
        except ValueError:
            host_cwd = container_cwd
        else:
            host_cwd = self.workspace_root / relative_workspace_path

        try:
            cwd = host_cwd.resolve(strict=True)
        except OSError as exc:
            return 400, {"ok": False, "error": f"Working directory is unavailable: {exc}"}
        if not any(cwd == root or root in cwd.parents for root in self.allowed_roots):
            return 403, {"ok": False, "error": "Working directory is outside allowed host roots."}
        if not cwd.is_dir():
            return 400, {"ok": False, "error": "Working directory is not a directory."}

        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError as exc:
            return 500, {"ok": False, "error": f"Failed to start command: {exc}"}

        with self._process_lock:
            if self._stopping:
                self._kill_process_group(proc)
                proc.communicate()
                return 503, {"ok": False, "error": "Host command runner is stopping."}
            self._active_processes.add(proc)

        try:
            try:
                stdout, stderr = proc.communicate(timeout=float(timeout))
            except subprocess.TimeoutExpired:
                self._kill_process_group(proc)
                stdout, stderr = proc.communicate()
                return 200, {
                    "ok": False,
                    "error": f"Command timed out after {timeout}s",
                    "exit_code": proc.returncode,
                    "stdout": self._decode(stdout),
                    "stderr": self._decode(stderr),
                }

            return 200, {
                "ok": proc.returncode == 0,
                "exit_code": proc.returncode,
                "stdout": self._decode(stdout),
                "stderr": self._decode(stderr),
            }
        finally:
            with self._process_lock:
                self._active_processes.discard(proc)

    def terminate_active(self) -> None:
        with self._process_lock:
            self._stopping = True
            processes = list(self._active_processes)
        for proc in processes:
            self._kill_process_group(proc)

    @staticmethod
    def _kill_process_group(proc: subprocess.Popen[bytes]) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass

    @staticmethod
    def _decode(data: bytes | None) -> str:
        text = data.decode(errors="replace") if data else ""
        if len(text) > MAX_RESPONSE_CHARS:
            text = text[:MAX_RESPONSE_CHARS] + (
                f"\n[clipped: {len(text) - MAX_RESPONSE_CHARS} chars omitted]"
            )
        return text


class ThreadingUnixHTTPServer(
    socketserver.ThreadingMixIn,
    socketserver.UnixStreamServer,
):
    daemon_threads = True
    allow_reuse_address = True


def make_handler(executor: HostExecutor) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:
            if self.path == "/healthz":
                self._send_json(200, {"ok": True})
            else:
                self._send_json(404, {"ok": False, "error": "Not found."})

        def do_POST(self) -> None:
            if self.path != "/execute":
                self._send_json(404, {"ok": False, "error": "Not found."})
                return

            content_length = self.headers.get("Content-Length")
            try:
                size = int(content_length or "")
            except ValueError:
                self._send_json(400, {"ok": False, "error": "Invalid Content-Length."})
                return
            if size < 0 or size > MAX_REQUEST_BYTES:
                self._send_json(413, {"ok": False, "error": "Request body is too large."})
                return

            try:
                payload = json.loads(self.rfile.read(size))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self._send_json(400, {"ok": False, "error": "Invalid JSON request."})
                return

            status, result = executor.execute(payload)
            self._send_json(status, result)

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args: Any) -> None:
            logger.info("%s - %s", self.client_address, fmt % args)

    return Handler


def serve(socket_path: Path, workspace_root: Path, allowed_roots: list[Path]) -> None:
    socket_path.parent.mkdir(parents=True, exist_ok=True)
    if socket_path.exists():
        if not socket_path.is_socket():
            raise RuntimeError(f"Refusing to replace non-socket path: {socket_path}")
        socket_path.unlink()

    executor = HostExecutor(workspace_root, allowed_roots)
    server = ThreadingUnixHTTPServer(
        str(socket_path),
        make_handler(executor),
    )
    os.chmod(socket_path, 0o600)
    logger.info("Host executor listening on %s", socket_path)

    def remove_socket(*_: Any) -> None:
        executor.terminate_active()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, remove_socket)
    signal.signal(signal.SIGINT, remove_socket)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        socket_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--allowed-root", type=Path, action="append", default=[])
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    serve(args.socket, args.workspace_root, args.allowed_root or [args.workspace_root])


if __name__ == "__main__":
    main()
