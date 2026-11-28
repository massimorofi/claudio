#!/usr/bin/env python3
"""Render ``BENCHMARK_REPORT.md`` from the artifacts in ``benchmark/results/``.

Reads (every one of them optional -- the report always renders):

    <results>/summary.json        run-level aggregates + backend availability
    <results>/capability.json     per-backend capability / availability probe
    <results>/**/run.json         one trial: tokens, errors, timings, engine_mode
    <results>/**/judge.json       one trial: rubric scores, issues, cited evidence

The loader is deliberately tolerant: sibling schema spellings (``engine`` vs
``backend``, ``total_tokens`` vs ``tokens``, ``scores`` vs ``rubric`` ...) are
normalised, and anything unparseable is skipped and reported under *Caveats*
rather than crashing the run.

Honesty rule (non-negotiable): a trial whose ``engine_mode`` is not a real
engine (``stub``/``fake``/``mock``/``echo``) is labelled **stub** everywhere it
appears, and the report refuses to turn stub numbers into a backend
recommendation.

Usage::

    python3 benchmark/report.py --results benchmark/results --out BENCHMARK_REPORT.md
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median
from typing import Any, Iterable

# --- constants ---------------------------------------------------------------

BACKENDS = ("claude_code", "opencode", "google_adk")

#: Task manifest (``--tasks``). Only ``results/<backend>/<task>/`` directories whose
#: task id is listed here are rendered. ``benchmark/results`` keeps stale dirs from
#: removed tasks, which would otherwise show up as phantom rows and move the KPI
#: winners. ``--tasks none`` turns the filter off.
DEFAULT_TASKS_MANIFEST = Path(__file__).resolve().parent / "tasks.json"
NO_MANIFEST = {"", "none", "off", "null"}

#: Canonical backend id <- aliases seen in the wild (CLI names, config kinds).
BACKEND_ALIASES = {
    "claude": "claude_code",
    "claude-code": "claude_code",
    "claudecode": "claude_code",
    "claude_code": "claude_code",
    "opencode": "opencode",
    "open-code": "opencode",
    "open_code": "opencode",
    "adk": "google_adk",
    "google-adk": "google_adk",
    "google_adk": "google_adk",
    "googleadk": "google_adk",
}

#: The five headline KPIs, in report order. ``direction`` drives winner choice.
KPIS = (
    {"id": "tokens_per_task", "label": "Tokens per task", "better": "lower"},
    {"id": "quality", "label": "Quality", "better": "higher"},
    {"id": "issues_per_task", "label": "Issues / errors per task", "better": "lower"},
    {"id": "intelligence", "label": "Intelligence", "better": "higher"},
    {"id": "coding_ability", "label": "Coding ability", "better": "higher"},
)

#: Rubric key aliases per KPI, most specific first.
RUBRIC_ALIASES = {
    "quality": ("quality", "overall_quality", "output_quality", "correctness",
                "completeness", "verification", "accuracy"),
    "intelligence": ("intelligence", "reasoning", "planning", "problem_solving",
                     "insight", "analysis"),
    "coding_ability": ("coding_ability", "coding", "code_quality", "implementation",
                       "code", "correctness_code"),
}

STUB_MODES = {"stub", "fake", "mock", "echo", "null", "dry", "dry_run", "offline"}
REAL_MODES = {"real", "cli", "live", "in_process", "in-process", "subprocess", "api"}

NO_DATA = "n/a"


# --- small helpers -----------------------------------------------------------


def _read_json(path: Path) -> dict[str, Any] | None:
    """Parse a JSON object, or ``None`` if missing / unreadable / not an object."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def canonical_backend(raw: Any) -> str | None:
    """Map any backend spelling to one of :data:`BACKENDS` (else ``None``)."""
    if raw is None:
        return None
    key = re.sub(r"[^a-z0-9]+", "", str(raw).strip().lower())
    if key in ("claudcode", "claudcode"):  # collapsed separator edge case
        return "claude_code"
    if key in ("googleadk", "adk"):
        return "google_adk"
    if key in BACKEND_ALIASES:
        return BACKEND_ALIASES[key]
    for backend in BACKENDS:
        if backend in key:
            return backend
    return None


def norm_score(raw: Any) -> float | None:
    """Normalise a rubric value on any scale (0-1, 0-5, 0-10, 0-100) to 0..1."""
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        x = float(raw)
    except (TypeError, ValueError):
        return None
    if x <= 1.0:
        return max(0.0, x)
    if x <= 5.0:
        return x / 5.0
    if x <= 10.0:
        return x / 10.0
    if x <= 100.0:
        return x / 100.0
    return None


def as_float(raw: Any) -> float | None:
    if isinstance(raw, bool) or raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def fmt(x: float | None, digits: int = 2) -> str:
    return NO_DATA if x is None else f"{x:,.{digits}f}"


def pct(x: float | None) -> str:
    return NO_DATA if x is None else f"{x * 100:.0f}%"


def md_cell(text: Any) -> str:
    """Make arbitrary text safe inside a markdown table cell."""
    s = str(text if text is not None else "")
    s = s.replace("|", "\\|").replace("\n", " ").replace("\r", " ").strip()
    return s or NO_DATA


def classify_error(text: str) -> str:
    """Bucket a raw error string into a stable error class."""
    t = str(text or "").lower()
    if not t:
        return "unspecified"
    if "timeout" in t or "timed out" in t or "deadline" in t:
        return "timeout"
    if "not found" in t or "enoent" in t or "which" in t or "no such file" in t:
        return "cli_not_found"
    if "unavailable" in t or "not installed" in t or "not configured" in t or "disabled" in t:
        return "backend_unavailable"
    if "auth" in t or "api_key" in t or "api key" in t or "401" in t or "403" in t or "unauthorized" in t:
        return "auth_error"
    if "connection" in t or "network" in t or "dns" in t or "econn" in t or "502" in t or "503" in t or "429" in t:
        return "network_error"
    if "schema" in t or "parse" in t or "json" in t or "contract" in t or "validation" in t:
        return "schema_error"
    if "budget" in t or "token limit" in t or "context" in t or "overflow" in t:
        return "budget_exceeded"
    if "cancel" in t or "abort" in t:
        return "cancelled"
    if "crash" in t or "exit" in t or "signal" in t:
        return "process_crash"
    return "other"


def _issue_texts(record: dict[str, Any]) -> list[str]:
    """Flatten judge/run ``issues`` (strings or {message:...} dicts) to strings."""
    out: list[str] = []
    raw = record.get("issues") if isinstance(record, dict) else None
    if raw is None:
        return out
    items = raw if isinstance(raw, list) else [raw]
    for item in items:
        if isinstance(item, dict):
            text = item.get("message") or item.get("issue") or item.get("detail") or item
            out.append(md_cell(json.dumps(text, default=str) if isinstance(text, dict) else str(text)))
        elif item is not None:
            out.append(str(item))
    return [s for s in (e.strip() for e in out) if s]


def _error_texts(record: dict[str, Any]) -> list[str]:
    """Flatten whatever shape ``errors`` / ``error`` / ``failures`` has to strings."""
    out: list[str] = []
    for key in ("errors", "error", "failures", "issues_raw"):
        raw = record.get(key)
        if raw is None:
            continue
        items = raw if isinstance(raw, list) else [raw]
        for item in items:
            if isinstance(item, dict):
                text = item.get("message") or item.get("error") or item.get("detail") or item
                out.append(md_cell(json.dumps(text, default=str) if isinstance(text, dict) else str(text)))
            elif item is not None:
                out.append(str(item))
    return [e for e in (s.strip() for s in out) if e]


# --- loading -----------------------------------------------------------------


def _backend_from_path(path: Path, results_dir: Path) -> str | None:
    try:
        parts = path.relative_to(results_dir).parts
    except ValueError:
        parts = path.parts
    for part in parts[:-1]:
        backend = canonical_backend(part)
        if backend:
            return backend
    return None


def _engine_mode(record: dict[str, Any], backend: str, available: bool) -> str:
    raw = str(record.get("engine_mode") or record.get("mode") or record.get("engine") or "").strip().lower()
    if raw in STUB_MODES:
        return "stub"
    if raw in REAL_MODES:
        return "real"
    # Unknown/absent: trust the availability probe, default to the pessimistic
    # answer so we never imply a real engine ran when we do not know.
    return "real" if available else "stub"


def load_task_manifest(path: Path | None) -> set[str] | None:
    """Task ids declared by the task manifest (``benchmark/tasks.json``).

    Accepts ``{"tasks": [{"id": ...}, ...]}`` and ``{"tasks": ["<id>", ...]}``.
    Returns ``None`` - meaning *do not filter* - when *path* is empty or one of
    ``none``/``off``/``null``, or when the file is missing, unparsable or has an
    empty ``tasks`` list: a broken manifest must never silently empty the report.
    """
    if path is None:
        return None
    text = str(path).strip()
    if text.lower() in NO_MANIFEST:
        return None
    data = _read_json(Path(text))
    entries = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        return None
    ids: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict):
            task_id = entry.get("id") or entry.get("task_id") or entry.get("name")
            if task_id:
                ids.add(str(task_id))
        elif isinstance(entry, str) and entry.strip():
            ids.add(entry.strip())
    return ids or None


def task_id_from_path(run_path: Path, results_dir: Path) -> str | None:
    """Task id implied by ``results/<backend>/<task>/.../run.json``.

    ``None`` when the path has no task level, in which case the id recorded
    inside ``run.json`` is used instead.
    """
    try:
        relative = run_path.parent.relative_to(results_dir)
    except ValueError:
        return None
    parts = relative.parts
    return parts[1] if len(parts) >= 2 else None


def load_trials(results_dir: Path, capability: dict[str, Any],
                task_ids: set[str] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """Collect every ``run.json`` + sibling ``judge.json`` under *results_dir*.

    ``task_ids`` (from :func:`load_task_manifest`) restricts the scan to task
    directories listed in the task manifest; runs belonging to tasks that are no
    longer in the manifest are skipped. ``None`` keeps everything.

    Returns ``(trials, warnings)``; a warning is emitted for each unreadable file
    so the report can be honest about incomplete input.
    """
    warnings: list[str] = []
    trials: list[dict[str, Any]] = []
    if not results_dir.is_dir():
        return trials, warnings

    skipped_not_in_manifest = 0
    for run_path in sorted(results_dir.rglob("run.json")):
        if task_ids is not None:
            path_task = task_id_from_path(run_path, results_dir)
            if path_task is not None and str(path_task) not in task_ids:
                skipped_not_in_manifest += 1
                continue
        run = _read_json(run_path)
        if run is None:
            warnings.append(f"unreadable run.json skipped: `{run_path}`")
            continue
        judge_path = run_path.with_name("judge.json")
        judge = _read_json(judge_path)
        if judge_path.exists() and judge is None:
            warnings.append(f"unreadable judge.json skipped: `{judge_path}`")
        if judge is None:
            # judge.json may live one level up (results/<backend>/<task>/judge.json)
            for candidate in (run_path.parent.parent / "judge.json",):
                if candidate.exists():
                    judge = _read_json(candidate)
                    break
        judge = judge or {}

        backend = (canonical_backend(run.get("backend") or run.get("engine")
                                      or run.get("backend_id") or judge.get("backend"))
                   or _backend_from_path(run_path, results_dir) or "unknown")
        avail = backend_available(capability, backend)
        task_id = str(run.get("task_id") or run.get("task") or run.get("id")
                     or judge.get("task_id") or run_path.parent.name)

        tokens = as_float(run.get("tokens"))
        if tokens is None:
            tokens = as_float(run.get("total_tokens"))
        if tokens is None:
            parts = [as_float(run.get(k)) for k in ("input_tokens", "output_tokens",
                                                    "prompt_tokens", "completion_tokens")]
            tokens = sum(p for p in parts if p is not None) if any(p is not None for p in parts) else None

        errors = _error_texts(run) + _error_texts(judge)
        timeouts = run.get("timeouts")
        if timeouts is None:
            timeouts = run.get("timeout_count")
        if timeouts is None:
            timeouts = sum(1 for e in errors if classify_error(e) == "timeout")

        passed = run.get("passed")
        if not isinstance(passed, bool):
            passed = run.get("ok") if isinstance(run.get("ok"), bool) else None
        if passed is None and run.get("status") is not None:
            passed = str(run.get("status")).lower() in ("pass", "passed", "ok", "success", "verified")

        rubric: dict[str, float] = {}
        scores = judge.get("scores") or judge.get("rubric") or judge.get("rubric_scores") or {}
        if isinstance(scores, dict):
            flat: dict[str, Any] = {}
            for k, v in scores.items():
                if isinstance(v, dict):
                    flat[str(k)] = v.get("score", v.get("value"))
                else:
                    flat[str(k)] = v
            for kpi, aliases in RUBRIC_ALIASES.items():
                for alias in aliases:
                    for key, value in flat.items():
                        if re.sub(r"[^a-z0-9]+", "", str(key).lower()) == re.sub(r"[^a-z0-9]+", "", alias):
                            n = norm_score(value)
                            if n is not None:
                                rubric[kpi] = n
                                break
                    if kpi in rubric:
                        break
            if "quality" not in rubric and flat:
                vals = [norm_score(v) for v in flat.values()]
                vals = [v for v in vals if v is not None]
                if vals:
                    rubric["quality"] = mean(vals)

        issues = _issue_texts(judge) + _issue_texts(run)

        evidence = judge.get("cited_evidence") or judge.get("evidence") or run.get("evidence") or []
        if isinstance(evidence, str):
            evidence = [evidence]
        if not isinstance(evidence, list):
            evidence = []

        trials.append({
            "backend": backend,
            "task_id": task_id,
            "trial": run.get("trial") or run.get("trial_index") or 1,
            "tokens": tokens,
            "errors": errors,
            "issues": issues,
            "timeouts": int(timeouts or 0),
            "passed": passed,
            "rubric": rubric,
            "evidence": [str(e) for e in evidence],
            "judge_mode": str(judge.get("judge_mode") or judge.get("mode") or "heuristic").lower(),
            "engine_mode": _engine_mode(run, backend, avail),
            "model": run.get("model") or judge.get("model") or None,
            "config": run.get("config") or run.get("engine_config") or {},
            "latency_ms": as_float(run.get("latency_ms") or run.get("wall_ms") or run.get("duration_ms")),
            "verdict": judge.get("verdict") or judge.get("total") or judge.get("score"),
            "run_path": run_path,
            "judge_path": judge_path if judge else None,
        })
    if skipped_not_in_manifest:
        warnings.append(f"{skipped_not_in_manifest} run.json skipped: "
                        "task not in the task manifest")
    return trials, warnings


# --- capability / summary merge ----------------------------------------------


def _backend_map(doc: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return ``{backend_id: entry}`` from a capability/summary document.

    Accepts ``{"backends": {...}}``, ``{"engines": {...}}``, a bare
    ``{backend: {...}}`` map, or ``{"entries": [...]}`` lists.
    """
    if not isinstance(doc, dict):
        return {}
    raw = doc.get("backends") or doc.get("engines") or doc.get("backend")
    out: dict[str, dict[str, Any]] = {}
    if isinstance(raw, dict):
        items: Iterable[tuple[Any, Any]] = raw.items()
    elif isinstance(raw, list):
        items = [(e.get("backend") or e.get("id") or e.get("engine"), e) for e in raw if isinstance(e, dict)]
    else:
        items = doc.items()
    for key, value in items:
        backend = canonical_backend(key)
        if not backend or not isinstance(value, dict):
            continue
        merged = dict(out.get(backend, {}))
        merged.update(value)
        out[backend] = merged
    return out


def backend_available(capability: dict[str, Any], backend: str) -> bool:
    entry = capability.get(backend) or {}
    for key in ("available", "is_available", "usable", "present", "reachable"):
        if isinstance(entry.get(key), bool):
            return entry[key]
    for key in ("unavailable", "missing", "unavailable_reason", "reason", "error"):
        if entry.get(key):
            return False
    mode = str(entry.get("engine_mode") or entry.get("mode") or "").lower()
    if mode in STUB_MODES:
        return False
    if mode in REAL_MODES:
        return True
    return False


def backend_reason(capability: dict[str, Any], backend: str) -> str:
    entry = capability.get(backend) or {}
    for key in ("reason", "unavailable_reason", "error", "detail", "message", "cause"):
        if entry.get(key):
            return str(entry[key])
    return "no availability probe recorded"


def backend_model(capability: dict[str, Any], summary: dict[str, Any], backend: str) -> str:
    for doc in (capability.get(backend) or {}, summary.get(backend) or {}):
        for key in ("model", "model_id", "llm_model", "resolved_model"):
            if doc.get(key):
                return str(doc[key])
    return ""


# --- aggregation -------------------------------------------------------------


def aggregate(trials: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Per-backend rollups: tasks, KPIs, error classes, modes."""
    agg: dict[str, dict[str, Any]] = {}
    for backend in BACKENDS:
        rows = [t for t in trials if t["backend"] == backend]
        by_task: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_task[row["task_id"]].append(row)

        task_rows: dict[str, dict[str, Any]] = {}
        for task_id, group in sorted(by_task.items()):
            token_vals = [g["tokens"] for g in group if g["tokens"] is not None]
            rubric: dict[str, list[float]] = defaultdict(list)
            for g in group:
                for kpi, val in g["rubric"].items():
                    rubric[kpi].append(val)
            task_rows[task_id] = {
                "trials": len(group),
                "tokens": sum(token_vals) if token_vals else None,
                "passed": any(g["passed"] for g in group) if any(isinstance(g["passed"], bool) for g in group) else None,
                "pass_counts": (sum(1 for g in group if g["passed"] is True),
                                sum(1 for g in group if g["passed"] is False)),
                "errors": [e for g in group for e in g["errors"]],
                "issues": [i for g in group for i in g["issues"]],
                "timeouts": sum(int(g["timeouts"] or 0) for g in group),
                "rubric": {k: mean(v) for k, v in rubric.items()},
                "evidence": list(dict.fromkeys(e for g in group for e in g["evidence"])),
                "engine_modes": sorted({g["engine_mode"] for g in group}),
                "judge_modes": sorted({g["judge_mode"] for g in group}),
                "models": sorted({str(g["model"]) for g in group if g["model"]}),
            }

        task_token_totals = [v["tokens"] for v in task_rows.values() if v["tokens"] is not None]
        quality_vals = [g["rubric"]["quality"] for g in rows if "quality" in g["rubric"]]
        intel_vals = [g["rubric"]["intelligence"] for g in rows if "intelligence" in g["rubric"]]
        code_vals = [g["rubric"]["coding_ability"] for g in rows if "coding_ability" in g["rubric"]]
        issue_counts = [len(v["errors"]) + len(v["issues"]) for v in task_rows.values()]
        error_classes: Counter[str] = Counter()
        for v in task_rows.values():
            for e in v["errors"]:
                error_classes[classify_error(e)] += 1

        agg[backend] = {
            "trials": rows,
            "n_trials": len(rows),
            "n_tasks": len(task_rows),
            "tasks": task_rows,
            "kpis": {
                "tokens_per_task": mean(task_token_totals) if task_token_totals else None,
                "quality": mean(quality_vals) if quality_vals else None,
                "issues_per_task": mean(issue_counts) if issue_counts else None,
                "intelligence": mean(intel_vals) if intel_vals else None,
                "coding_ability": mean(code_vals) if code_vals else None,
            },
            "median_tokens": median(task_token_totals) if task_token_totals else None,
            "error_classes": error_classes,
            "timeouts": sum(int(g["timeouts"] or 0) for g in rows),
            "pass_rate": (mean([1.0 if g["passed"] else 0.0 for g in rows if isinstance(g["passed"], bool)])
                          if any(isinstance(g["passed"], bool) for g in rows) else None),
            "engine_modes": sorted({g["engine_mode"] for g in rows}) or ["stub"],
            "judge_modes": sorted({g["judge_mode"] for g in rows}),
            "models": sorted({str(g["model"]) for g in rows if g["model"]}),
        }
    return agg


def is_stub(agg_entry: dict[str, Any], capability: dict[str, Any], backend: str) -> bool:
    """True unless the backend has trials that all ran against a real engine."""
    modes = agg_entry.get("engine_modes") or []
    if modes:
        return not any(m == "real" for m in modes)
    return not backend_available(capability, backend)


def kpi_winners(agg: dict[str, dict[str, Any]], stub_only: set[str]) -> dict[str, str | None]:
    """Best backend per KPI, ignoring backends that only produced stub numbers."""
    winners: dict[str, str | None] = {}
    for kpi in KPIS:
        candidates = {b: a["kpis"][kpi["id"]] for b, a in agg.items()
                      if b not in stub_only and a["kpis"].get(kpi["id"]) is not None}
        if not candidates:
            winners[kpi["id"]] = None
            continue
        pick = min if kpi["better"] == "lower" else max
        winners[kpi["id"]] = pick(candidates, key=lambda b: candidates[b])
    return winners


# --- runbook -----------------------------------------------------------------

RUNBOOK = {
    "claude_code": {
        "kind": "claude_code",
        "adapter": "claudio_backend.engine.claude_code.adapter.ClaudeCodeEngine",
        "binary": "claude",
        "argv": [
            "claude", "-p",
            "--output-format", "stream-json",
            "--verbose",
            "--model", "<llm.model>",
            "--permission-mode", "<engine.claude_code.permission_mode, default acceptEdits>",
            "--append-system-prompt", "<mandatory-TodoWrite plan prompt>",
        ],
        "env": {
            "ANTHROPIC_BASE_URL": "<llm.base_url> (Anthropic-compatible, /v1/messages; a trailing /v1 is stripped)",
            "ANTHROPIC_API_KEY": "<llm.api_key>",
            "ANTHROPIC_MODEL": "<llm.model>",
            "ANTHROPIC_SMALL_FAST_MODEL": "<llm.model>",
            "CLAUDE_CONFIG_DIR": "<workspace state>/__engine__/claude-code",
            "CLAUDE_CODE_ENABLE_TODO_TOOLS": "1",
            "CLAUDE_CODE_ENABLE_TASKS": "0",
        },
        "notes": [
            "`--verbose` is mandatory: the CLI refuses `--output-format stream-json` in print mode without it and exits 1 with empty stdout.",
            "No permission-bypass flag is used; `--permission-mode` comes from config and Claudio's policy bridge gates file/shell tools.",
            "Hard gate: the adapter refuses to start against an endpoint that is not declared Anthropic-compatible unless `engine.claude_code.allow_openai_endpoint` is set after verifying a conversion gateway.",
            "Turn 2+ replays the CLI transcript via `--resume <cli_session_id>`.",
        ],
    },
    "opencode": {
        "kind": "opencode",
        "adapter": "claudio_backend.engine.opencode.adapter.OpenCodeEngine",
        "binary": "opencode",
        "argv": [
            "opencode", "run", "<prompt>",
            "--format", "json",
            "--thinking",
            "-m", "<engine.opencode.provider_name>/<llm.model>",
        ],
        "env": {
            "OPENAI_API_KEY": "<llm.api_key> (referenced as {env:OPENAI_API_KEY} from the generated opencode provider config)",
            "OPENAI_BASE_URL": "<llm.base_url> (OpenAI-compatible /v1/chat/completions endpoint handed to the provider)",
            "XDG_CONFIG_HOME": "<workspace state>/__engine__/opencode/.config",
        },
        "notes": [
            "The prompt is a positional argument passed through execv (argv only, never shell-interpolated).",
            "`--format json` emits raw JSONL events; `--thinking` surfaces reasoning blocks.",
            "Continuation adds `-s <native_session_id>`; the model ref is qualified as `provider/model` when not already.",
        ],
    },
    "google_adk": {
        "kind": "adk / google_adk (both accepted; also google-adk)",
        "adapter": "claudio_backend.engine.adk_engine.AdkEngine via claudio_backend.engine.create_engine",
        "binary": "(none - in-process, no CLI)",
        "argv": [
            "python3 -c \"from claudio_backend.engine import create_engine; ...\"",
        ],
        "env": {
            "CLAUDIO_ENGINE": "adk (or google_adk) - deployment-wide selector",
            "ANTHROPIC_BASE_URL / OPENAI_BASE_URL": "whatever llm.base_url the shared config points at",
        },
        "notes": [
            "In-process engine: `create_engine(config, db, kind=\"google_adk\")` returns an `AdkEngine`; an explicit `kind` wins over `CLAUDIO_ENGINE`/`engine.kind`, which default to `adk`.",
            "An unknown kind raises `EngineError('unknown_engine')` rather than silently falling back.",
            "No subprocess, so there is no argv to reproduce; the equivalent call is shown in the snippet below.",
        ],
    },
}


def find_runner(repo_root: Path) -> Path | None:
    """Locate the benchmark runner a sibling task provides, if present."""
    for name in ("runner.py", "run_benchmark.py", "harness.py", "__main__.py", "cli.py"):
        candidate = repo_root / "benchmark" / name
        if candidate.exists():
            return candidate
    return None


def render_runbook(repo_root: Path, runner: Path | None) -> list[str]:
    lines = ["## Real-backend runbook", ""]
    lines += [
        "Everything below is how a **real** (non-stub) trial is produced. The stub",
        "path exists so the pipeline runs without credentials; it is never a",
        "substitute for these steps.",
        "",
    ]
    for backend in BACKENDS:
        spec = RUNBOOK[backend]
        lines += [f"### `{backend}`", ""]
        lines += [f"- **engine kind:** `{spec['kind']}`", f"- **adapter:** `{spec['adapter']}`", ""]
        if backend == "google_adk":
            lines += ["In-process construction (no CLI argv):", "", "```bash",
                      "cd claudio-backend && PYTHONPATH=src:../claudio-core/src python3 - <<'PY'",
                      "from claudio_core.settings import load_config",
                      "from claudio_backend.engine import create_engine",
                      "",
                      "config = load_config('claudio-backend/config/config.yaml')",
                      "engine = create_engine(config, db=None, kind='google_adk')  # or kind='adk'",
                      "PY", "```", ""]
        else:
            lines += ["Exact argv (argv list, execv - never a shell string):", "", "```bash",
                      " ".join(_shell_quote(a) for a in spec["argv"]), "```", ""]
        lines += ["Required environment:", "", "| var | value |", "|---|---|"]
        for key, value in spec["env"].items():
            lines.append(f"| `{md_cell(key)}` | {md_cell(value)} |")
        lines.append("")
        if spec["notes"]:
            lines += ["Notes:", ""] + [f"- {n}" for n in spec["notes"]] + [""]

    runner_name = runner.relative_to(repo_root).as_posix() if runner else Path("benchmark") / "runner.py"
    runner_note = ("" if runner else
                   "\n> NOTE: `benchmark/runner.py` is not present in this tree yet; the commands below are the intended invocations once the harness lands.\n")
    lines += [
        "### Re-running with real backends",
        "",
        runner_note,
        "```bash",
        "# 0) export credentials / endpoints for the engines you want to exercise",
        "export ANTHROPIC_BASE_URL=https://<anthropic-compatible-gateway>/",
        "export ANTHROPIC_API_KEY=<key>",
        "export OPENAI_API_KEY=<key>",
        "export OPENAI_BASE_URL=https://<openai-compatible-endpoint>/v1",
        "",
        "# 1) one backend at a time, 3 trials per task",
        f"python3 {runner_name} --backend claude_code --trials 3 --results benchmark/results",
        f"python3 {runner_name} --backend opencode   --trials 3 --results benchmark/results",
        f"python3 {runner_name} --backend google_adk --trials 3 --results benchmark/results",
        "",
        "# 2) or everything at once",
        f"python3 {runner_name} --backends claude_code,opencode,google_adk --trials 3 \\",
        "      --results benchmark/results --engine-mode real",
        "",
        "# 3) re-render this report",
        "python3 benchmark/report.py --results benchmark/results --out BENCHMARK_REPORT.md",
        "```",
        "",
        "A run counts as *real* only when its `run.json` records `engine_mode: \"real\"`",
        "and `capability.json` marks the backend available. Anything else is reported",
        "as stub and excluded from recommendations.",
        "",
    ]
    return lines


def _shell_quote(arg: str) -> str:
    if arg and all(c.isalnum() or c in "-_./" for c in arg):
        return arg
    return "'" + arg.replace("'", "'\\''") + "'"


# --- section renderers -------------------------------------------------------


def _provenance_banner(results_dir: Path, summary: dict[str, Any], trials: list[dict[str, Any]],
                       agg: dict[str, dict[str, Any]], stub_only: set[str], real_only: set[str]) -> list[str]:
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    src = summary.get("generated_at") or summary.get("generated") or ""
    lines = [
        "# Benchmark Report",
        "",
        f"Generated by `benchmark/report.py` on {generated}"
        + (f" from results recorded {src}" if src else "")
        + f". Source directory: `{results_dir.as_posix()}`.",
        "",
    ]
    if not trials:
        lines += [
            "> **NO RUN DATA RECORDED.** No `run.json` / `judge.json` / `summary.json`",
            f"> artifacts were found under `{results_dir.as_posix()}`. Every number below is",
            "> `n/a`: **no backend has been measured**, so nothing here may be quoted as a",
            "> backend result. Use the *Real-backend runbook* to produce data, then",
            "> re-render this file.",
            "",
        ]
        return lines
    if stub_only:
        lines += [
            "> **STUB-MODE DATA.** The following backends did **not** run against a real",
            "> engine and are shown only to prove the pipeline works:",
            "",
        ]
        for backend in sorted(stub_only):
            lines.append(f"> - `{backend}` - engine_mode=`{'`, `'.join(agg[backend]['engine_modes'])}`, "
                         f"{len(agg[backend]['trials'])} trial(s). **Not real backend results.**")
        lines.append("")
    if real_only:
        lines += [f"> Real-engine data exists for: {', '.join(f'`{b}`' for b in sorted(real_only))}.", ""]
    return lines


def _exec_summary(agg, capability, summary, stub_only, winners, probe=None) -> list[str]:
    probe = probe or {}
    lines = ["## Executive summary", ""]
    total = sum(a["n_trials"] for a in agg.values())
    real_backends = [b for b in BACKENDS if b not in stub_only and agg[b]["n_trials"]]
    per_backend = ", ".join("`{0}`: {1}".format(b, agg[b]["n_trials"]) for b in BACKENDS)
    lines += [
        "- **Trials on disk:** {} across {} of {} backends ({}).".format(
            total, sum(1 for a in agg.values() if a["n_trials"]), len(BACKENDS), per_backend),
        f"- **Backends with real-engine data:** "
        + (", ".join(f"`{b}`" for b in real_backends) if real_backends else "**none**"),
        f"- **Backends that were unavailable / ran in stub mode:** "
        + (", ".join(f"`{b}` ({backend_reason(capability, b)})" for b in sorted(stub_only))
           if stub_only else "none"),
        "",
    ]
    if probe:
        lines += ["**Availability at render time (live probe, no backend executed):**", ""]
        for backend in BACKENDS:
            lines.append(f"- `{backend}`: {probe.get(backend, 'not probed')}")
        lines.append("")

    lines += ["### Winner per KPI", ""]
    if not real_backends:
        lines += [
            "**No winner can be declared.** Every trial on disk is stub-mode (or there are no",
            "trials at all), so the numbers measure the harness, not the engines. Ranking",
            "stubs would be a measurement artefact, so the honest answer is: unknown.",
            "",
        ]
    else:
        lines += ["| KPI | Winner | Value |", "|---|---|---|"]
        for kpi in KPIS:
            winner = winners.get(kpi["id"])
            value = agg[winner]["kpis"][kpi["id"]] if winner else None
            digits = 0 if kpi["id"] == "tokens_per_task" else 2
            lines.append(f"| {kpi['label']} | `{winner or 'unknown'}` | {fmt(value, digits)} |")
        lines.append("")

    lines += ["### Recommended backend per workload type", ""]
    workload_types = _workload_types(summary, agg)
    if not real_backends:
        lines += [
            "No recommendation is possible from stub data. The table records what would be",
            "needed to decide, and states plainly that the evidence is missing.",
            "",
            "| Workload type | Recommended backend | Basis |",
            "|---|---|---|",
        ]
        for name in workload_types:
            lines.append(f"| {name} | *undetermined - no real-backend data* | stub trials do not exercise the engine |")
        lines.append("")
    else:
        lines += ["| Workload type | Recommended backend | Basis |", "|---|---|---|"]
        for name in workload_types:
            scored = {}
            for b in real_backends:
                k = agg[b]["kpis"]
                if k["quality"] is None:
                    continue
                scored[b] = k["quality"] + k["coding_ability"] + k["intelligence"] - 0.5 * (k["issues_per_task"] or 0)
            best = max(scored, key=lambda b: scored[b]) if scored else None
            lines.append(f"| {name} | `{best or 'undetermined'}` | best combined quality/intelligence/coding, "
                         f"penalised for issues per task |")
        lines.append("")
    return lines


def _workload_types(summary: dict[str, Any], agg) -> list[str]:
    raw = summary.get("workload_types") or summary.get("workloads")
    if isinstance(raw, list) and raw:
        out = []
        for item in raw:
            out.append(str(item.get("name") if isinstance(item, dict) else item))
        return out
    tasks = sorted({t for a in agg.values() for t in a["tasks"]})
    if tasks:
        return tasks
    return ["code generation", "multi-step tool use", "reasoning / planning", "long-context retrieval"]


def _kpi_table(agg, stub_only, winners) -> list[str]:
    lines = ["## KPI comparison", ""]
    header = "| KPI | " + " | ".join(f"`{b}`" for b in BACKENDS) + " | Winner (real data only) |"
    lines += [header, "|" + "---|" * (len(BACKENDS) + 2)]
    for kpi in KPIS:
        digits = 0 if kpi["id"] == "tokens_per_task" else 2
        cells = []
        for backend in BACKENDS:
            value = agg[backend]["kpis"].get(kpi["id"])
            text = fmt(value, digits)
            if value is not None and backend in stub_only:
                text += " (stub)"
            cells.append(text)
        winner = winners.get(kpi["id"]) or "none - no real-backend data"
        lines.append(f"| {kpi['label']} ({'lower is better' if kpi['better'] == 'lower' else 'higher is better'}) | "
                     + " | ".join(cells) + f" | `{winner}` |")
    lines += [
        "",
        "Definitions: **Tokens per task** = mean total tokens consumed by one task",
        "(summed over its trials); **Quality / Intelligence / Coding ability** = mean",
        "judge rubric score normalised to 0-1; **Issues / errors per task** = mean count",
        "of recorded errors plus judge-flagged issues per task.",
        "",
        "> Values marked `(stub)` come from a stub engine and measure the harness, not",
        "> the backend. They are never eligible for a winner.",
        "",
    ]
    return lines


def _per_task(agg, stub_only) -> list[str]:
    lines = ["## Per-task breakdown", ""]
    task_ids = sorted({t for a in agg.values() for t in a["tasks"]})
    if not task_ids:
        lines += [
            "No per-task records were found (`benchmark/results/**/run.json` is empty or",
            "absent), so there is nothing to break down. Each row below would show",
            "pass/fail, tokens, errors, rubric scores and cited evidence per task x backend.",
            "",
            "| task | backend | result | tokens | errors | rubric scores | cited evidence |",
            "|---|---|---|---|---|---|---|",
        ]
        for backend in BACKENDS:
            mode = "stub" if backend in stub_only else "unavailable - no data"
            lines.append(f"| {NO_DATA} | `{backend}` | no data ({mode}) | {NO_DATA} | {NO_DATA} | {NO_DATA} | {NO_DATA} |")
        lines.append("")
        return lines

    for task_id in task_ids:
        lines += [f"### Task `{task_id}`", "",
                  "| backend | result | tokens | errors / timeouts | rubric scores | cited evidence |",
                  "|---|---|---|---|---|---|"]
        for backend in BACKENDS:
            info = agg[backend]["tasks"].get(task_id)
            if not info:
                lines.append(f"| `{backend}` | not run | {NO_DATA} | {NO_DATA} | {NO_DATA} | {NO_DATA} |")
                continue
            if info["passed"] is True:
                result = "PASS"
            elif info["passed"] is False:
                result = "FAIL"
            else:
                result = "unknown"
            if info["engine_modes"] and "real" not in info["engine_modes"]:
                result += " (stub engine - not a real result)"
            trials_note = f"{info['trials']} trial(s)"
            pass_counts = info["pass_counts"]
            if pass_counts[0] + pass_counts[1]:
                result += f" [{pass_counts[0]}/{pass_counts[0] + pass_counts[1]} trials passed]"
            errs = info["errors"]
            err_text = f"{len(errs)} error(s)" + (f", {info['timeouts']} timeout(s)" if info["timeouts"] else "")
            if errs:
                err_text += ": " + "; ".join(md_cell(e) for e in errs[:3])
                if len(errs) > 3:
                    err_text += f"; +{len(errs) - 3} more"
            rubric = info["rubric"]
            rubric_text = ", ".join(f"{k}={v:.2f}" for k, v in sorted(rubric.items())) or NO_DATA
            evidence = info["evidence"]
            ev_text = "; ".join(md_cell(e) for e in evidence[:3]) or NO_DATA
            if len(evidence) > 3:
                ev_text += f"; +{len(evidence) - 3} more"
            lines.append(f"| `{backend}` | {result} | {fmt(info['tokens'], 0)} ({trials_note}) | "
                         f"{err_text} | {rubric_text} | {ev_text} |")
        lines.append("")
    return lines


def _failure_modes(agg, stub_only) -> list[str]:
    lines = ["## Failure modes", ""]
    any_data = any(a["n_trials"] for a in agg.values())
    if not any_data:
        lines += [
            "No trials were recorded, so no error classes or timeouts exist to report.",
            "The classes below are the ones the harness classifies; re-run with the",
            "*Real-backend runbook* to populate them.",
            "",
            "| backend | top error classes | timeouts | total failures |",
            "|---|---|---|---|",
        ]
        for backend in BACKENDS:
            lines.append(f"| `{backend}` | {NO_DATA} | 0 | {NO_DATA} |")
        lines.append("")
        return lines

    lines += ["| backend | trials | top error classes (count) | timeouts | failed tasks |",
              "|---|---|---|---|---|"]
    for backend in BACKENDS:
        entry = agg[backend]
        classes = entry["error_classes"]
        top = ", ".join(f"`{name}` ({count})" for name, count in classes.most_common(5)) or "none recorded"
        failed = sum(1 for v in entry["tasks"].values() if v["passed"] is False)
        suffix = " (stub engine)" if backend in stub_only else ""
        lines.append(f"| `{backend}`{suffix} | {entry['n_trials']} | {top} | {entry['timeouts']} | {failed} |")
    lines.append("")

    for backend in BACKENDS:
        entry = agg[backend]
        if not entry["n_trials"]:
            continue
        classes = entry["error_classes"]
        lines += [f"### `{backend}` - dominant failure modes", ""]
        if not classes:
            lines += ["No error strings were recorded on any trial.", ""]
            continue
        for name, count in classes.most_common(5):
            examples = [e for task in entry["tasks"].values() for e in task["errors"]
                        if classify_error(e) == name]
            lines.append(f"- **{name}** - {count} occurrence(s); e.g. `{md_cell(examples[0]) if examples else NO_DATA}`")
        lines.append("")
    lines += [
        "Error classes: `timeout`, `cli_not_found`, `backend_unavailable`, `auth_error`,",
        "`network_error`, `schema_error`, `budget_exceeded`, `cancelled`, `process_crash`, `other`.",
        "",
    ]
    return lines


def _token_vs_quality(agg, stub_only) -> list[str]:
    lines = ["## Token efficiency vs quality", ""]
    lines += ["| backend | tokens / task | quality (0-1) | quality per 1k tokens | intelligence | coding ability | data class |",
              "|---|---|---|---|---|---|---|"]
    for backend in BACKENDS:
        entry = agg[backend]
        tokens = entry["kpis"]["tokens_per_task"]
        quality = entry["kpis"]["quality"]
        ratio = (quality / (tokens / 1000.0)) if (tokens and quality is not None and tokens > 0) else None
        data_class = "stub - not comparable" if backend in stub_only else (
            "real engine" if entry["n_trials"] else "no data")
        lines.append(f"| `{backend}` | {fmt(tokens, 0)} | {fmt(quality)} | {fmt(ratio, 3)} | "
                     f"{fmt(entry['kpis']['intelligence'])} | {fmt(entry['kpis']['coding_ability'])} | {data_class} |")
    lines += [
        "",
        "`quality per 1k tokens` = quality score divided by (tokens per task / 1000);",
        "higher means the backend bought more quality per token spent.",
        "",
    ]
    if stub_only:
        lines += [
            f"> Efficiency is **not** comparable for {', '.join(f'`{b}`' for b in sorted(stub_only))}:",
            "> a stub engine emits a canned answer at a synthetic token cost, so its",
            "> tokens/task and rubric scores are properties of the test double.",
            "",
        ]
    real = [b for b in BACKENDS if b not in stub_only and agg[b]["n_trials"]]
    if len(real) >= 2:
        best = max(real, key=lambda b: ((agg[b]["kpis"]["quality"] or 0) /
                                        (agg[b]["kpis"]["tokens_per_task"] or 1)))
        lines += [f"Among backends with real data, `{best}` currently has the best quality-per-token.", ""]
    elif not real:
        lines += ["No backend has real-engine data, so no quality-per-token ranking is made.", ""]
    return lines


def _caveats(agg, capability, summary, stub_only, warnings, results_dir: Path, probe=None) -> list[str]:
    probe = probe or {}
    lines = ["## Caveats", ""]
    total = sum(a["n_trials"] for a in agg.values())
    lines += [
        f"- **Sample size.** N = {total} trial(s) total on disk: "
        + ", ".join(f"`{b}` = {agg[b]['n_trials']} trials over {agg[b]['n_tasks']} task(s)" for b in BACKENDS)
        + ". With N this small (0-3 per task) medians are not stable and no",
        "  confidence interval is reported; treat every delta as directional only.",
        "",
        "- **Stub vs real engine per backend.**",
        "",
        "| backend | engine_mode | available per capability probe | trials |",
        "|---|---|---|---|",
    ]
    for backend in BACKENDS:
        entry = agg[backend]
        modes = ", ".join(entry["engine_modes"]) if entry["engine_modes"] else "no trials"
        avail = backend_available(capability, backend)
        lines.append(f"| `{backend}` | {modes} | {'yes' if avail else 'no - unavailable'} | {entry['n_trials']} |")
    lines += [
        "",
        "  A `stub` engine_mode means the real CLI / SDK was **not** invoked: the",
        "  recorded tokens, latencies and rubric scores are synthetic and must not be",
        "  quoted as backend performance.",
        "",
    ]
    if probe:
        lines += ["  Live probe at render time (explains *why* a backend could not run):", ""]
        for backend in BACKENDS:
            lines.append("  - `{}`: {}".format(backend, probe.get(backend, "not probed")))
        lines.append("")
    lines += [
        "- **Model and configuration differences.** The three engines are not",
        "  configuration-matched, so cross-backend deltas conflate engine and settings:",
        "",
        "| backend | model recorded | config differences |",
        "|---|---|---|",
    ]
    for backend in BACKENDS:
        entry = agg[backend]
        models = ", ".join(entry["models"]) or backend_model(capability, summary, backend) or "not recorded"
        cfg = sorted({k for task in entry["tasks"].values() for t in entry["trials"] for k in (t["config"] or {})})
        detail = ", ".join(f"`{k}`" for k in cfg) if cfg else "no per-trial config recorded"
        if backend == "claude_code":
            detail += "; runs with `--permission-mode` from config and a mandatory plan system prompt"
        elif backend == "opencode":
            detail += "; runs with `--format json --thinking` and a provider-qualified model ref"
        else:
            detail += "; runs in-process via `create_engine(kind='adk'|'google_adk')`"
        lines.append(f"| `{backend}` | {md_cell(models)} | {detail} |")
    judge_modes = sorted({m for a in agg.values() for m in a["judge_modes"]})
    if not judge_modes:
        judge_modes = [str(summary.get("judge_mode") or "heuristic")]
    lines += [
        "",
        f"- **Judge mode: {' + '.join(judge_modes)}.**",
        "",
        "  - `heuristic` = rule-based rubric (regex/keyword checks, artifact presence,",
        "    exit status). Cheap and deterministic, but it cannot detect a confident",
        "    wrong answer, so quality scores are an optimistic floor.",
        "  - `llm` = model-graded rubric. Richer but non-deterministic and sensitive to",
        "    the judge model/version; scores are not comparable across judge upgrades.",
        "",
        f"  This run used: **{', '.join(judge_modes)}**. Scores are only comparable",
        "  against other runs using the same judge mode.",
        "",
        "- **Other limits.** Single machine (no hardware variance), no warm/cold cache",
        "  split unless `summary.json` records one, per-run artefacts are read from",
        f"  `{results_dir.as_posix()}` and stale files from earlier runs are included",
        "  if they were not cleaned, and the report never imputes a missing value.",
        "",
    ]
    if warnings:
        lines += ["- **Input problems skipped by the loader:**", ""]
        lines += [f"  - {w}" for w in warnings]
        lines.append("")
    return lines


# --- assembly ----------------------------------------------------------------


def probe_environment() -> dict[str, str]:
    """Best-effort check of what a real run would need *right now*.

    Never executes a backend and never touches the network: it only looks for
    the CLI binaries on PATH, the credentials the adapters read, and whether the
    ADK package is importable. Used to explain *why* a backend is unavailable.
    """
    import importlib.util
    import os
    import shutil as _shutil

    probe: dict[str, str] = {}
    for backend, binary in (("claude_code", "claude"), ("opencode", "opencode")):
        found = _shutil.which(binary)
        probe[backend] = f"`{binary}` found at {found}" if found else f"`{binary}` not on PATH"
    if importlib.util.find_spec("google.adk") is None:
        probe["google_adk"] = "python package `google-adk` not importable"
    else:
        probe["google_adk"] = "`google.adk` importable"
    creds = {
        "claude_code": ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"),
        "opencode": ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
        "google_adk": ("ANTHROPIC_API_KEY", "OPENAI_API_KEY"),
    }
    for backend, keys in creds.items():
        missing = [k for k in keys if not os.environ.get(k)]
        if missing:
            probe[backend] += "; missing env " + ", ".join(f"`{k}`" for k in missing)
        else:
            probe[backend] += "; env present (" + ", ".join(keys) + ")"
    return probe


def render_report(results_dir: Path, task_ids: set[str] | None = None) -> str:
    """Render the markdown report.

    ``task_ids`` restricts rendering to task directories listed in the task
    manifest (see :func:`load_task_manifest`); ``None`` renders everything found.
    """
    summary = _read_json(results_dir / "summary.json") or {}
    capability_doc = _read_json(results_dir / "capability.json") or {}
    capability = _backend_map(summary)
    capability = {b: {**_backend_map(capability_doc).get(b, {}), **capability.get(b, {})}
                  for b in BACKENDS}
    trials, warnings = load_trials(results_dir, capability, task_ids)
    if not trials:
        extra, extra_warnings = trials_from_summary(summary, capability)
        trials.extend(extra)
        warnings.extend(extra_warnings)

    agg = aggregate(trials)
    stub_only = {b for b in BACKENDS if not agg[b]["n_trials"] or is_stub(agg[b], capability, b)}
    real_only = {b for b in BACKENDS if b not in stub_only}
    winners = kpi_winners(agg, stub_only)

    repo_root = Path(__file__).resolve().parent.parent
    probe = probe_environment()
    lines: list[str] = []
    lines += _provenance_banner(results_dir, summary, trials, agg, stub_only, real_only)
    lines += _exec_summary(agg, capability, summary, stub_only, winners, probe)
    lines += _kpi_table(agg, stub_only, winners)
    lines += _per_task(agg, stub_only)
    lines += _failure_modes(agg, stub_only)
    lines += _token_vs_quality(agg, stub_only)
    lines += _caveats(agg, capability, summary, stub_only, warnings, results_dir, probe)
    lines += render_runbook(repo_root, find_runner(repo_root))
    text = "\n".join(lines).rstrip("\n") + "\n"
    return re.sub(r"\n{3,}", "\n\n", text)


def trials_from_summary(summary: dict[str, Any], capability: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Fallback: some runners embed trials directly in ``summary.json``."""
    out: list[dict[str, Any]] = []
    warnings: list[str] = []
    raw = summary.get("trials") or summary.get("runs") or summary.get("results")
    if not isinstance(raw, list):
        return out, warnings
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        run = entry.get("run") if isinstance(entry.get("run"), dict) else entry
        judge = entry.get("judge") if isinstance(entry.get("judge"), dict) else {}
        backend = canonical_backend(run.get("backend") or run.get("engine") or judge.get("backend"))
        if not backend:
            warnings.append("summary trial with unknown backend skipped")
            continue
        out.append(_build_trial(backend, str(run.get("task_id") or run.get("task") or "unknown"),
                                run, judge, capability))
    return out, warnings


def _build_trial(backend: str, task_id: str, run: dict[str, Any], judge: dict[str, Any],
                 capability: dict[str, Any]) -> dict[str, Any]:
    """Shared normalisation used by both the file loader and the summary fallback."""
    errors = _error_texts(run) + _error_texts(judge)
    issues = _issue_texts(judge) + _issue_texts(run)
    rubric: dict[str, float] = {}
    scores = judge.get("scores") or judge.get("rubric") or {}
    if isinstance(scores, dict):
        for kpi, aliases in RUBRIC_ALIASES.items():
            for alias in aliases:
                for key, value in scores.items():
                    if re.sub(r"[^a-z0-9]+", "", str(key).lower()) == re.sub(r"[^a-z0-9]+", "", alias):
                        n = norm_score(value.get("score") if isinstance(value, dict) else value)
                        if n is not None:
                            rubric[kpi] = n
                            break
                if kpi in rubric:
                    break
    tokens = as_float(run.get("tokens") or run.get("total_tokens"))
    return {
        "backend": backend,
        "task_id": task_id,
        "trial": run.get("trial") or 1,
        "tokens": tokens,
        "errors": errors,
        "issues": issues,
        "timeouts": int(run.get("timeouts") or 0),
        "passed": run.get("passed") if isinstance(run.get("passed"), bool) else run.get("ok"),
        "rubric": rubric,
        "evidence": [str(e) for e in (judge.get("cited_evidence") or judge.get("evidence") or [])]
                    if isinstance(judge.get("cited_evidence") or judge.get("evidence"), list) else [],
        "judge_mode": str(judge.get("judge_mode") or judge.get("mode") or "heuristic").lower(),
        "engine_mode": _engine_mode(run, backend, backend_available(capability, backend)),
        "model": run.get("model"),
        "config": run.get("config") or {},
        "latency_ms": as_float(run.get("latency_ms")),
        "verdict": judge.get("verdict"),
        "run_path": None,
        "judge_path": None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render BENCHMARK_REPORT.md from benchmark/results artifacts.")
    parser.add_argument("--results", default="benchmark/results", help="directory holding summary.json/capability.json/run.json/judge.json")
    parser.add_argument("--tasks", default=str(DEFAULT_TASKS_MANIFEST),
                        help="task manifest JSON; only results/<backend>/<task>/ dirs whose id "
                             "is listed here are rendered (default: benchmark/tasks.json; "
                             "'none' disables the filter)")
    parser.add_argument("--out", default="BENCHMARK_REPORT.md", help="markdown file to write")
    parser.add_argument("--stdout", action="store_true", help="also print the report to stdout")
    args = parser.parse_args(argv)

    results_dir = Path(args.results)
    task_ids = load_task_manifest(Path(args.tasks))
    try:
        text = render_report(results_dir, task_ids)
    except Exception as exc:  # never leave the repo without a report
        text = (f"# Benchmark Report\n\n## Executive summary\n\nReport generation failed: `{exc!r}`.\n"
                "\nNo backend result should be inferred from this file.\n")
    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(text, encoding="utf-8")
    if args.stdout:
        sys.stdout.write(text)
    else:
        print(f"wrote {out_path} ({len(text.splitlines())} lines) from {results_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
