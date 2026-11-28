#!/usr/bin/env python3
"""Aggregate benchmark run artifacts into ``benchmark/results/summary.json``.

Stdlib only (``json``, ``statistics``, ``argparse``, ``pathlib``).

Inputs
------
The aggregator walks ``--results`` and treats *every* directory that contains a
``run.json`` as one measured run. ``judge.json`` next to it and the capability
report are optional. The layout written by ``benchmark/runner.py`` is::

    benchmark/results/<backend>/<task_id>/trial_<k>/run.json       required
    benchmark/results/<backend>/<task_id>/trial_<k>/judge.json     optional
    benchmark/results/capability.json                              optional
    benchmark/results/<backend>/capability.json                    optional

``capability.json`` is either the harness-wide report
(``{"backends": {"<backend>": {"available": ..., "engine_mode": ...}}}``) or a
single-run report (``{"available": ..., "engine_mode": ...}``); a per-run file
wins over the harness-wide one.

``run.json`` keys read (missing keys fall back to ``0`` / ``False``)::

    backend, task_id, category, engine_mode, verification_passed,
    tokens.{input,output,total}   (aliases: input_tokens/output_tokens/
                                   total_tokens; total defaults to in+out),
    errors.{command_errors,tool_errors,retries,timeouts},
    duration_s                    (aliases: wall_clock_seconds, wall_clock)

``judge.json``: ``scores.{quality,intelligence,coding}`` (``rubric.{...}`` and a
flat ``{quality: ...}`` are accepted too). A run without a ``judge.json`` (or
without any of the three dimensions) still counts towards ``n_runs``,
``pass_rate``, tokens, errors and wall-clock, but contributes **no sample** to
the rubric means/medians, so unjudged runs cannot drag a rubric down.

Categories
----------
``run.json`` carries a ``task_id``, not a category, so the category is resolved
as: explicit ``category`` in ``run.json`` -> ``--category-map`` JSON file
(``{"<task_id>": "<category>"}``) -> prefix rule on the task id:

    fix_* / *_bug / bug_*            -> bug_fix
    add_* / implement_* / feature_*  -> feature
    refactor_* / *_debug / debug_*   -> refactor_debug
    qa_* / explain_* / describe_*    -> codebase_qa

The four canonical categories are always present in ``categories``; one with no
runs maps to an empty object.

Usage::

    python3 benchmark/aggregate.py --results benchmark/results \
        --out benchmark/results/summary.json

Task manifest
-------------
Only ``results/<backend>/<task>/`` directories whose task id appears in the
manifest (``--tasks``, default ``benchmark/tasks.json``) are aggregated; result
dirs left behind by removed tasks are skipped. ``--tasks none`` disables the
filter and aggregates every ``run.json`` on disk.

Exit ``0`` with a compact markdown table on stdout (progress on stderr), or
exit ``1`` when no ``run.json`` is found.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Canonical display order. Anything not listed is appended alphabetically.
BACKEND_ORDER = ("google_adk", "claude_code", "opencode")
CATEGORY_ORDER = ("bug_fix", "feature", "refactor_debug", "codebase_qa")
RUBRIC_DIMENSIONS = ("quality", "intelligence", "coding")
ERROR_FIELDS = ("command_errors", "tool_errors", "retries", "timeouts")
TOKEN_FIELDS = ("input", "output", "total")
ROUND_TO = 4

# task_id prefix/infix -> category, first match wins.
CATEGORY_RULES: tuple[tuple[str, str], ...] = (
    ("refactor_", "refactor_debug"),
    ("_debug", "refactor_debug"),
    ("debug_", "refactor_debug"),
    ("fix_", "bug_fix"),
    ("bug_", "bug_fix"),
    ("_bug", "bug_fix"),
    ("add_", "feature"),
    ("implement_", "feature"),
    ("feature_", "feature"),
    ("qa_", "codebase_qa"),
    ("explain_", "codebase_qa"),
    ("describe_", "codebase_qa"),
    ("where_", "codebase_qa"),
)
FALLBACK_CATEGORY = "uncategorized"

# Task manifest that decides which ``results/<backend>/<task>/`` directories are
# real. ``benchmark/results`` keeps accumulating directories for tasks that have
# since been removed from ``benchmark/tasks.json``; those runs are stale
# artefacts and would otherwise inflate ``n_runs``, invent ``uncategorized``
# rows and flip the KPI winners. Override with ``--tasks``; ``--tasks none``
# turns the filter off.
DEFAULT_TASKS_MANIFEST = Path(__file__).resolve().parent / "tasks.json"
NO_MANIFEST = {"", "none", "off", "null"}


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def _load_json(path: Path) -> dict[str, Any] | None:
    """Read a JSON object; return ``None`` when absent or unparsable."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _num(value: Any, default: float = 0.0) -> float:
    """Coerce to a finite number, else ``default``."""
    if isinstance(value, bool) or value is None:
        return default
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if out == out else default  # NaN guard


def _round(value: float) -> float:
    return round(float(value), ROUND_TO)


def _count(value: float) -> Any:
    """Whole-number counts stay ints in the JSON; fractions keep decimals."""
    out = _round(value)
    return int(out) if float(out).is_integer() else out


def _mean(values: list[float]) -> float:
    return _round(statistics.fmean(values)) if values else 0.0


def _median(values: list[float]) -> float:
    return _round(statistics.median(values)) if values else 0.0


def _ordered(names: set[str], preferred: tuple[str, ...]) -> list[str]:
    rank = {name: i for i, name in enumerate(preferred)}
    return sorted(names, key=lambda n: (rank.get(n, len(rank)), n))


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "1", "pass", "passed", "ok"}
    return bool(value)


# --------------------------------------------------------------------------
# capability report
# --------------------------------------------------------------------------
def load_capability(results_dir: Path) -> dict[str, dict[str, Any]]:
    """``{backend: {"available": bool, "engine_mode": str}}`` from capability files.

    Reads ``<results>/capability.json`` and ``<results>/<backend>/capability.json``.
    Both the harness-wide (``{"backends": {...}}``) and the flat single-backend
    shape are accepted.
    """
    out: dict[str, dict[str, Any]] = {}
    candidates = [results_dir / "capability.json", *sorted(results_dir.glob("*/capability.json"))]
    for path in candidates:
        data = _load_json(path)
        if not data:
            continue
        entries = data.get("backends")
        if isinstance(entries, dict) and entries:
            for name, info in entries.items():
                if isinstance(info, dict):
                    out[str(name)] = _capability_entry(info)
        else:
            name = str(data.get("backend") or path.parent.name)
            out.setdefault(name, _capability_entry(data))
    return out


def _capability_entry(info: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": _as_bool(info.get("available", True)),
        "engine_mode": str(info.get("engine_mode") or "") or None,
    }


# --------------------------------------------------------------------------
# categories
# --------------------------------------------------------------------------
def infer_category(task_id: str, explicit: Any = None,
                   category_map: dict[str, str] | None = None) -> str:
    """Explicit value wins, then the ``--category-map`` file, then the prefix rule."""
    if explicit:
        return str(explicit)
    if category_map and task_id in category_map:
        return str(category_map[task_id])
    lowered = task_id.strip().lower()
    for needle, category in CATEGORY_RULES:
        if needle in lowered:
            return category
    return FALLBACK_CATEGORY


def load_task_manifest(path: Path | None) -> set[str] | None:
    """Task ids declared by the task manifest (``benchmark/tasks.json``).

    Accepts ``{"tasks": [{"id": ...}, ...]}`` and ``{"tasks": ["<id>", ...]}``.
    Returns ``None`` - meaning *do not filter* - when *path* is empty or one of
    ``none``/``off``/``null``, or when the file is missing, unparsable or has an
    empty ``tasks`` list: a broken manifest must never silently drop every run.
    """
    if path is None:
        return None
    text = str(path).strip()
    if text.lower() in NO_MANIFEST:
        return None
    data = _load_json(Path(text))
    entries = data.get("tasks") if isinstance(data, dict) else None
    if not isinstance(entries, list):
        print(f"aggregate: no 'tasks' list in manifest, filter disabled: {text}",
              file=sys.stderr)
        return None
    ids: set[str] = set()
    for entry in entries:
        if isinstance(entry, dict):
            task_id = entry.get("id") or entry.get("task_id") or entry.get("name")
            if task_id:
                ids.add(str(task_id))
        elif isinstance(entry, str) and entry.strip():
            ids.add(entry.strip())
    if not ids:
        print(f"aggregate: empty task manifest, filter disabled: {text}", file=sys.stderr)
        return None
    return ids


def task_id_from_path(directory: Path, results_dir: Path) -> str | None:
    """Task id implied by the ``results/<backend>/<task>/...`` layout.

    ``directory`` is the directory holding ``run.json``. ``None`` when the path
    has no task level (e.g. ``results/<backend>/run.json``).
    """
    try:
        relative = directory.relative_to(results_dir)
    except ValueError:
        return None
    parts = relative.parts
    return parts[1] if len(parts) >= 2 else None


def split_by_manifest(results_dir: Path, task_ids: set[str] | None,
                      ) -> tuple[list[Path], list[Path]]:
    """``(kept, skipped)`` run directories under *results_dir* for the manifest.

    A run directory is kept when its task id (path first, ``run.json`` second) is
    listed in *task_ids*. Everything is kept when *task_ids* is ``None``.
    """
    if not results_dir.is_dir():
        return [], []
    dirs = sorted({path.parent for path in results_dir.rglob("run.json")})
    if task_ids is None:
        return dirs, []
    kept: list[Path] = []
    skipped: list[Path] = []
    for directory in dirs:
        task_id = task_id_from_path(directory, results_dir)
        if task_id is None:
            task_id = _task_from_path(directory)
        (kept if str(task_id) in task_ids else skipped).append(directory)
    return kept, skipped


def load_category_map(path: Path | None) -> dict[str, str]:
    """Optional ``{task_id: category}`` override file; absent/invalid -> ``{}``."""
    if not path:
        return {}
    data = _load_json(path) or {}
    return {str(k): str(v) for k, v in data.items()}


# --------------------------------------------------------------------------
# reading one run directory
# --------------------------------------------------------------------------
def read_run(directory: Path, results_dir: Path,
             capability: dict[str, dict[str, Any]] | None = None,
             category_map: dict[str, str] | None = None) -> dict[str, Any]:
    """Normalise one run directory (the one holding ``run.json``) into a record."""
    run = _load_json(directory / "run.json") or {}
    judge = _load_json(directory / "judge.json") or {}
    local_capability = _load_json(directory / "capability.json")

    tokens = run.get("tokens") if isinstance(run.get("tokens"), dict) else {}

    def token_value(*keys: str) -> float:
        for src in (tokens, run):
            for key in keys:
                if key in src:
                    return _num(src.get(key))
        return 0.0

    used_input = token_value("input", "input_tokens", "prompt_tokens")
    used_output = token_value("output", "output_tokens", "completion_tokens")
    total = token_value("total", "total_tokens") or (used_input + used_output)

    raw_errors = run.get("errors") if isinstance(run.get("errors"), dict) else {}
    errors = {field: _num(raw_errors.get(field, run.get(field, 0))) for field in ERROR_FIELDS}

    wall = _num(run.get("duration_s", run.get("wall_clock_seconds", run.get("wall_clock", 0))))

    rubric_src = judge.get("scores")
    if not isinstance(rubric_src, dict):
        rubric_src = judge.get("rubric")
    if not isinstance(rubric_src, dict):
        rubric_src = judge
    rubric = {dim: _num(rubric_src.get(dim)) for dim in RUBRIC_DIMENSIONS}
    has_rubric = any(dim in rubric_src for dim in RUBRIC_DIMENSIONS)

    backend = str(run.get("backend") or _backend_from_path(directory, results_dir))
    task_id = str(run.get("task_id") or run.get("task") or _task_from_path(directory))

    cap: dict[str, Any] = dict((capability or {}).get(backend, {}))
    if local_capability:
        cap.update(_capability_entry(local_capability))

    return {
        "directory": directory,
        "backend": backend,
        "task_id": task_id,
        "run_id": str(run.get("run_id") or _run_id_from_path(directory)),
        "category": infer_category(task_id, run.get("category"), category_map),
        "engine_mode": str(cap.get("engine_mode") or run.get("engine_mode") or "unknown"),
        "available": _as_bool(cap.get("available", run.get("available", True))),
        "verification_passed": _as_bool(run.get("verification_passed", run.get("passed", False))),
        "tokens": {"input": used_input, "output": used_output, "total": total},
        "errors": errors,
        "wall_clock_seconds": wall,
        "rubric": rubric,
        "has_rubric": has_rubric,
    }


def _relative_parts(directory: Path, results_dir: Path) -> list[str]:
    try:
        return list(directory.relative_to(results_dir).parts)
    except ValueError:
        return list(directory.parts)


def _backend_from_path(directory: Path, results_dir: Path) -> str:
    """Backend = first path component under ``results_dir`` that is not a run dir."""
    parts = _relative_parts(directory, results_dir)[:-1]
    for part in parts:
        if part in BACKEND_ORDER:
            return part
    return parts[0] if parts else "unknown"


def _task_from_path(directory: Path) -> str:
    """Task id = parent directory name, skipping a ``trial_N``/``run_N`` leaf."""
    names = [p.name for p in directory.parents]
    for name in names:
        if not _is_trial_dir(name):
            return name
    return "unknown"


def _run_id_from_path(directory: Path) -> str:
    parent = "" if _is_trial_dir(directory.name) else f"{directory.name}/"
    return f"{parent}{directory.name}"


def _is_trial_dir(name: str) -> bool:
    lowered = name.lower()
    return lowered.startswith("trial") or lowered.startswith("run_") or lowered == "runs"


def discover_runs(results_dir: Path, capability: dict[str, dict[str, Any]] | None = None,
                  category_map: dict[str, str] | None = None,
                  task_ids: set[str] | None = None) -> list[dict[str, Any]]:
    """Every directory under ``results_dir`` that holds a ``run.json``.

    ``task_ids`` (from :func:`load_task_manifest`) restricts the scan to the task
    directories listed in the task manifest, so leftover result dirs for removed
    tasks are skipped instead of polluting the summary. ``None`` keeps everything.
    """
    kept, skipped = split_by_manifest(results_dir, task_ids)
    if skipped:
        print(f"aggregate: skipped {len(skipped)} run dir(s) whose task is not in "
              f"the task manifest", file=sys.stderr)
    return sorted(
        (read_run(d, results_dir, capability, category_map) for d in kept),
        key=lambda r: (r["backend"], r["category"], r["task_id"], r["run_id"]),
    )


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------
def _mode(rows: list[dict[str, Any]]) -> str:
    """Most common ``engine_mode`` (ties break alphabetically)."""
    modes: dict[str, int] = {}
    for row in rows:
        modes[row["engine_mode"]] = modes.get(row["engine_mode"], 0) + 1
    if not modes:
        return "unknown"
    return sorted(modes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _pass_rate(rows: list[dict[str, Any]]) -> float:
    """Share of runs with ``verification_passed`` true, as a float in ``0..1``."""
    if not rows:
        return 0.0
    return _round(sum(1 for r in rows if r["verification_passed"]) / len(rows))


def aggregate_backends(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-backend KPI block (``summary.json`` -> ``backends``)."""
    out: dict[str, Any] = {}
    for backend in _ordered({r["backend"] for r in runs}, BACKEND_ORDER):
        rows = [r for r in runs if r["backend"] == backend]
        judged = [r for r in rows if r["has_rubric"]]
        tokens = {
            kind: {
                "mean": _mean([r["tokens"][kind] for r in rows]),
                "median": _median([r["tokens"][kind] for r in rows]),
            }
            for kind in TOKEN_FIELDS
        }
        error_totals = {f: _count(sum(r["errors"][f] for r in rows)) for f in ERROR_FIELDS}
        errors_total = _count(sum(_num(value) for value in error_totals.values()))
        out[backend] = {
            "available": any(r["available"] for r in rows),
            "engine_mode": _mode(rows),
            "n_runs": len(rows),
            "pass_rate": _pass_rate(rows),
            "tokens": tokens,
            "errors": {
                **error_totals,
                "total": errors_total,
                "mean_per_run": _round(errors_total / len(rows)) if rows else 0.0,
            },
            "wall_clock": {
                "mean": _mean([r["wall_clock_seconds"] for r in rows]),
                "median": _median([r["wall_clock_seconds"] for r in rows]),
            },
            "rubric": {
                dim: {
                    "mean": _mean([r["rubric"][dim] for r in judged]),
                    "median": _median([r["rubric"][dim] for r in judged]),
                }
                for dim in RUBRIC_DIMENSIONS
            },
        }
    return out


def aggregate_categories(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """``{category: {backend: {...}}}``. Canonical categories are always present."""
    found = {r["category"] for r in runs} | set(CATEGORY_ORDER)
    out: dict[str, Any] = {}
    for category in _ordered(found, CATEGORY_ORDER):
        by_backend: dict[str, Any] = {}
        for backend in _ordered({r["backend"] for r in runs}, BACKEND_ORDER):
            rows = [r for r in runs if r["category"] == category and r["backend"] == backend]
            if not rows:
                continue
            rubric_values = [
                r["rubric"][dim] for r in rows if r["has_rubric"] for dim in RUBRIC_DIMENSIONS
            ]
            by_backend[backend] = {
                "n_runs": len(rows),
                "pass_rate": _pass_rate(rows),
                "tokens_total_median": _median([r["tokens"]["total"] for r in rows]),
                "rubric_mean": _mean(rubric_values),
            }
        out[category] = by_backend
    return out


def pick_winners(backends: dict[str, Any]) -> dict[str, str]:
    """Best backend per KPI: tokens/errors lower is better, rubric higher.

    Only backends flagged ``available`` compete; when none is, every backend does.
    A rubric mean of ``0`` means "nothing was judged" and is excluded, while a
    zero error rate is a real (and winning) result. Ties break alphabetically so
    the block is deterministic.
    """
    pool = [name for name, block in backends.items() if block["available"]] or list(backends)

    def best(value_of, better: str, zero_means_missing: bool = False) -> str:
        scored = []
        for name in pool:
            value = value_of(backends[name])
            if value is None or (zero_means_missing and not value):
                continue
            scored.append((value, name))
        if not scored:
            return ""
        sign = -1 if better == "max" else 1
        return sorted(scored, key=lambda vn: (vn[0] * sign, vn[1]))[0][1]

    return {
        "tokens": best(lambda b: b["tokens"]["total"]["mean"], "min"),
        "quality": best(lambda b: b["rubric"]["quality"]["mean"], "max", True),
        "errors": best(lambda b: b["errors"]["mean_per_run"], "min"),
        "intelligence": best(lambda b: b["rubric"]["intelligence"]["mean"], "max", True),
        "coding": best(lambda b: b["rubric"]["coding"]["mean"], "max", True),
    }


def build_summary(runs: list[dict[str, Any]], *, generated_at: str | None = None) -> dict[str, Any]:
    backends = aggregate_backends(runs)
    return {
        "generated_at": generated_at
        or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "n_runs": len(runs),
        "backends": backends,
        "categories": aggregate_categories(runs),
        "winners": pick_winners(backends),
    }


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------
def _fmt(value: Any, digits: int = 2) -> str:
    """``3246`` for whole numbers, otherwise ``digits`` decimals."""
    number = _num(value)
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    return f"{number:.{digits}f}"


def _pct(value: Any) -> str:
    return f"{_num(value) * 100:.1f}%"


def render_markdown(summary: dict[str, Any]) -> str:
    """Compact markdown report (one table per backend, one per category)."""
    backends = summary["backends"]
    categories = summary["categories"]
    winners = summary["winners"]
    backend_names = list(backends)

    lines = [
        f"## Benchmark summary - {summary['n_runs']} runs "
        f"(generated {summary['generated_at']})",
        "",
        "| backend | mode | avail | n | pass rate | tokens total mean/median "
        "| errors total/mean | wall clock s mean/median | rubric Q/I/C mean |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for name in backend_names:
        block = backends[name]
        rubric = " / ".join(_fmt(block["rubric"][dim]["mean"]) for dim in RUBRIC_DIMENSIONS)
        lines.append(
            "| {name} | {mode} | {avail} | {n} | {rate} | {tokens} | {errors} "
            "| {wall} | {rubric} |".format(
                name=name,
                mode=block["engine_mode"],
                avail="yes" if block["available"] else "no",
                n=block["n_runs"],
                rate=_pct(block["pass_rate"]),
                tokens=f"{_fmt(block['tokens']['total']['mean'])} / "
                       f"{_fmt(block['tokens']['total']['median'])}",
                errors=f"{_fmt(block['errors']['total'])} / "
                       f"{_fmt(block['errors']['mean_per_run'])}",
                wall=f"{_fmt(block['wall_clock']['mean'], 3)} / "
                     f"{_fmt(block['wall_clock']['median'], 3)}",
                rubric=rubric,
            )
        )

    lines += [
        "",
        "### By category - n runs, pass rate, tokens total median, rubric mean",
        "",
        "| category | " + " | ".join(backend_names) + " |",
        "| --- |" + " --- |" * len(backend_names),
    ]
    for category, by_backend in categories.items():
        cells = []
        for name in backend_names:
            entry = by_backend.get(name)
            if not entry:
                cells.append("-")
                continue
            cells.append(
                f"{entry['n_runs']} / {_pct(entry['pass_rate'])} / "
                f"{_fmt(entry['tokens_total_median'])} / {_fmt(entry['rubric_mean'])}"
            )
        lines.append(f"| {category} | " + " | ".join(cells) + " |")

    lines += [
        "",
        "**Winners** "
        + " - ".join(f"{key}: `{value or 'n/a'}`" for key, value in winners.items()),
    ]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent


def write_summary(summary: dict[str, Any], out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--results", type=Path, default=HERE / "results",
                        help="directory to scan for run.json (default: benchmark/results)")
    parser.add_argument("--out", type=Path, default=None,
                        help="summary.json path (default: <results>/summary.json)")
    parser.add_argument("--category-map", type=Path, default=None,
                        help="JSON file mapping task_id -> category")
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS_MANIFEST,
                        help="task manifest JSON; only results/<backend>/<task>/ dirs whose "
                             "id is listed here are aggregated (default: benchmark/tasks.json; "
                             "'none' disables the filter)")
    parser.add_argument("--quiet", action="store_true", help="do not print the markdown table")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    results_dir: Path = args.results
    out_path: Path = args.out or results_dir / "summary.json"

    capability = load_capability(results_dir)
    category_map = load_category_map(args.category_map)
    task_ids = load_task_manifest(args.tasks)
    runs = discover_runs(results_dir, capability, category_map, task_ids)
    if not runs:
        print(f"aggregate: no run.json found under {results_dir}", file=sys.stderr)
        return 1

    summary = build_summary(runs)
    write_summary(summary, out_path)
    print(f"aggregate: wrote {out_path} ({len(runs)} runs, "
          f"{len(summary['backends'])} backends)", file=sys.stderr)
    if not args.quiet:
        print(render_markdown(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
