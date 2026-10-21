"""Monitoring endpoints: health, metrics, logs, runs."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter, Request

from claudio_core.engine_metrics import EngineMetrics
from claudio_core.logging_setup import get_log_buffer

from .deps import require_auth

router = APIRouter()

_LEVEL_ORDER = {"DEBUG": 10, "INFO": 20, "WARNING": 30, "ERROR": 40, "CRITICAL": 50}


def _parse_log_line(line: str) -> dict:
    """Turn one raw log line into a display entry.

    Our own records are JSON (``ts``/``level``/``logger``/``message``); anything
    else (uvicorn startup lines, stray tracebacks) is shown verbatim as the
    message so nothing in the file is hidden from the Logs view.
    """
    line = line.strip()
    if not line:
        return {}
    try:
        obj = json.loads(line)
    except (ValueError, TypeError):
        return {"timestamp": "", "level": "", "logger": "", "message": line}
    if not isinstance(obj, dict):
        return {"timestamp": "", "level": "", "logger": "", "message": line}
    message = obj.get("message", "")
    exception = obj.get("exception")
    if exception:
        message = f"{message}\n{exception}" if message else str(exception)
    return {
        "timestamp": obj.get("ts") or obj.get("timestamp") or "",
        "level": obj.get("level", ""),
        "logger": obj.get("logger", ""),
        "message": message,
    }


def _tail_lines(path: Path, n: int, max_bytes: int = 4_000_000) -> list[str]:
    """Return the last ``n`` lines of ``path`` without reading the whole file."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            data = f.read()
    except OSError:
        return []
    if not data:
        return []
    lines = data.split(b"\n")
    # If we started mid-file the first chunk is a partial line — drop it.
    if size > max_bytes and lines:
        lines = lines[1:]
    out = [ln.decode("utf-8", "replace") for ln in lines if ln.strip()]
    return out[-n:]


@router.get("/monitoring/health")
async def health(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    checks = []
    supervisor_ok = supervisor.state in ("running", "degraded")
    checks.append({"name": "supervisor", "status": "ok" if supervisor.config else "down"})
    checks.append({"name": "agent", "status": supervisor.state})
    db_ok = supervisor.db is not None
    checks.append({"name": "db", "status": "ok" if db_ok else "down"})
    llm_status = "ok"
    if supervisor.config:
        # Cheap heuristic: if recent LLM errors dominate, mark degraded.
        snap = supervisor.metrics.snapshot()
        if snap["llm_errors_total"] > 0 and snap["runs_completed"] == 0:
            llm_status = "degraded"
    checks.append({"name": "llm", "status": llm_status})
    overall = "ok" if all(c["status"] in ("ok", "running") for c in checks) else "degraded"
    return {
        "supervisor": "ok" if supervisor_ok else "down",
        "agent": supervisor.state,
        "llm": llm_status,
        "db": "ok" if db_ok else "down",
        "checks": checks,
        "overall": overall,
    }


@router.get("/monitoring/metrics")
async def metrics(request: Request) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    snap = supervisor.metrics.snapshot(
        runs_active=supervisor.run_manager.active_count() if supervisor.run_manager else 0
    )
    # Overlay the persistent totals from the runs table. The in-memory counters
    # are uptime-scoped and reset on every restart; the DB is the durable source,
    # so the overview shows real cumulative numbers regardless of restarts.
    totals = await supervisor.db.run_totals()
    snap["runs_total"] = int(totals.get("runs_total") or 0)
    snap["runs_completed"] = int(totals.get("runs_completed") or 0)
    snap["runs_failed"] = int(totals.get("runs_failed") or 0)
    snap["tokens_in"] = int(totals.get("tokens_in") or 0)
    snap["tokens_out"] = int(totals.get("tokens_out") or 0)
    snap["cost_usd_total"] = round(float(totals.get("cost_usd") or 0.0), 6)
    # Overlay the engine's OWN metrics (real prefill/decode throughput, prompt
    # cache hit rate, KV-cache usage, speculation acceptance) read straight from
    # its Prometheus /metrics endpoint. These are computed by the engine, not
    # estimated, so the overview reflects what the model actually did. The
    # engine may be briefly unreachable; on failure we keep the last good
    # snapshot and flag availability so the UI can show it as stale.
    engine = _get_engine_metrics(request, supervisor)
    if engine is not None:
        try:
            snap.update(await engine.refresh())
        except Exception:  # noqa: BLE001 - metrics overlay must never break the view
            snap["engine_metrics_available"] = False
    return snap


def _get_engine_metrics(request: Request, supervisor):
    """Lazily build and cache the engine-metrics reader on app.state."""
    engine = getattr(request.app.state, "engine_metrics", None)
    if engine is None:
        base_url = (
            getattr(supervisor.config.llm, "base_url", None) if supervisor.config else None
        )
        if not base_url:
            return None
        engine = EngineMetrics(base_url)
        request.app.state.engine_metrics = engine
    return engine


@router.get("/monitoring/logs")
async def logs(
    request: Request,
    level: str | None = None,
    since: str | None = None,
    limit: int = 200,
) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    log_path = supervisor.base_dir / "data" / "logs" / "backend.log"

    # Read the actual log file so the view matches what the user sees on disk.
    # Fall back to the in-memory ring buffer when the file is missing/empty
    # (e.g. the backend was started without start.sh's redirect).
    raw_lines = _tail_lines(log_path, max(limit * 4, 2000))
    if raw_lines:
        entries = [_parse_log_line(ln) for ln in raw_lines]
    else:
        entries = get_log_buffer()

    if level:
        min_level = _LEVEL_ORDER.get(level.upper(), 0)
        entries = [e for e in entries if _LEVEL_ORDER.get(e.get("level", ""), 0) >= min_level]
    if since:
        entries = [e for e in entries if (e.get("timestamp") or "") >= since]
    return {"logs": entries[-limit:]}


@router.get("/monitoring/runs")
async def runs(request: Request, limit: int = 50) -> dict:
    require_auth(request)
    supervisor = request.app.state.supervisor
    rows = await supervisor.db.list_runs(limit=limit)
    return {
        "runs": [
            {
                "id": r["id"],
                "session_id": r["session_id"],
                "state": r["state"],
                "started_at": r["started_at"],
                "turns_used": r["turns_used"],
                "cost_usd": r["cost_usd"],
            }
            for r in rows
        ]
    }
