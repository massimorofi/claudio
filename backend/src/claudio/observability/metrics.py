"""In-process metrics counters and latency tracking for GET /monitoring/metrics."""
from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any


class Metrics:
    """Thread-safe counters + latency samples. v1 is in-memory (uptime-scoped)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.started_at = time.time()
        self.requests_total = 0
        self.runs_total = 0
        self.runs_failed_total = 0
        self.runs_completed_total = 0
        self.retry_count = 0
        self.tool_errors_total = 0
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost_usd_total = 0.0
        self.llm_errors_total = 0
        self._latencies: deque[float] = deque(maxlen=500)

    def inc_request(self) -> None:
        with self._lock:
            self.requests_total += 1

    def inc_run_started(self) -> None:
        with self._lock:
            self.runs_total += 1

    def inc_run_completed(self) -> None:
        with self._lock:
            self.runs_completed_total += 1

    def inc_run_failed(self) -> None:
        with self._lock:
            self.runs_failed_total += 1

    def inc_retry(self) -> None:
        with self._lock:
            self.retry_count += 1

    def inc_tool_error(self) -> None:
        with self._lock:
            self.tool_errors_total += 1

    def inc_llm_error(self) -> None:
        with self._lock:
            self.llm_errors_total += 1

    def add_usage(self, tokens_in: int, tokens_out: int, cost_usd: float) -> None:
        with self._lock:
            self.tokens_in += tokens_in
            self.tokens_out += tokens_out
            self.cost_usd_total += cost_usd

    def record_llm_latency(self, ms: float) -> None:
        with self._lock:
            self._latencies.append(ms)

    def _percentile(self, sorted_vals: list[float], pct: float) -> float:
        if not sorted_vals:
            return 0.0
        idx = min(len(sorted_vals) - 1, int(len(sorted_vals) * pct))
        return sorted_vals[idx]

    def snapshot(self, runs_active: int = 0, queue_depth: int = 0) -> dict[str, Any]:
        with self._lock:
            lat = sorted(self._latencies)
            return {
                "uptime_seconds": round(time.time() - self.started_at, 1),
                "requests_total": self.requests_total,
                "runs_total": self.runs_total,
                "runs_active": runs_active,
                "runs_completed_total": self.runs_completed_total,
                "runs_failed_total": self.runs_failed_total,
                "tokens": {"in": self.tokens_in, "out": self.tokens_out},
                "cost_usd_total": round(self.cost_usd_total, 6),
                "llm_latency_p50_ms": round(self._percentile(lat, 0.50), 1),
                "llm_latency_p95_ms": round(self._percentile(lat, 0.95), 1),
                "retry_count": self.retry_count,
                "tool_errors_total": self.tool_errors_total,
                "llm_errors_total": self.llm_errors_total,
                "queue_depth": queue_depth,
            }
