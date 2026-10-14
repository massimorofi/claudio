"""Structured JSON logging with an in-memory ring buffer for the monitoring API."""
from __future__ import annotations

import json
import logging
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

# Secret values that must never appear in logs.
_SECRET_MARKERS = ("sk-",)

_buffer: deque[dict[str, Any]] = deque(maxlen=2000)
_buffer_lock = threading.Lock()


class RingBufferHandler(logging.Handler):
    """Keeps the last N formatted log records in memory for GET /monitoring/logs."""

    def emit(self, record: logging.LogRecord) -> None:  # noqa: D102
        try:
            entry = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
            }
            if record.exc_info and record.exc_info[0]:
                entry["exception"] = repr(record.exc_info[1])
            with _buffer_lock:
                _buffer.append(entry)
        except Exception:
            pass  # logging must never raise


def get_log_buffer() -> list[dict[str, Any]]:
    with _buffer_lock:
        return list(_buffer)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:  # noqa: D102
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0]:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "INFO") -> None:
    """Configure root logging: JSON to stderr + ring buffer."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    root.handlers.clear()

    stream = logging.StreamHandler()
    stream.setFormatter(JsonFormatter())
    root.addHandler(stream)
    root.addHandler(RingBufferHandler())

    # Quieten noisy third-party loggers.
    for noisy in ("httpx", "httpcore", "openai", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
