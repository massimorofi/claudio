#!/usr/bin/env python3
"""Aggregate benchmark run artifacts into ``benchmark/results/summary.json``.

Stdlib only (``json``, ``statistics``, ``argparse``, ``pathlib``).

Inputs
------
The aggregator walks ``--results`` and treats *any* directory that contains a
``run.json`` as one benchmark run. Siblings ``judge.json`` and
``capability.json`` in the same directory are optional:

    benchmark/results/<category>/<backend>/<run_id>/run.json         required
    benchmark/results/<category>/<backend>/<run_id>/judge.json       optional
    benchmark/results/<category>/<backend>/<run_id>/capability.json  optional

``run.json`` keys read (missing keys fall back to 0 / False / "unknown")::

    backend, category, engine_mode, verification_passed,
    tokens.{input,output,total}   (aliases: input_tokens / output_tokens /
                                   total_tokens; total defaults to in+out),
    errors.{command_errors,tool_errors,retries,timeouts},
    wall_clock_seconds            (alias: wall_clock)

``judge.json``: ``rubric.{quality,intelligence,coding}`` (also accepted flat).
``capability.json``: ``available`` (bool) and ``engine_mode``; they win over
the values in ``run.json`` when present.

Output
------
``summary.json`` with ``generated_at``, ``backends``, ``categories`` and
``winners`` (see ``README.md``), plus a compact markdown table on stdout.

Usage::

    python3 benchmark/aggregate.py --results benchmark/results \
        --out benchmark/results/summary.json
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
ROUND_TO = 4


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


def _count(value: float) -> int:
    """Whole-number counts stay ints in the JSON; fractions keep their decimals."""
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
        return value.strip().lower() in {"true", "yes", "1", "pass", "passed"}
    return bool(value)


# --------------------------------------------------------------------------
# reading one run directory
# --------------------------------------------------------------------------
def read_run(directory: Path) -> dict[str, Any]:
    """Normalise one run directory into a flat record."""
    run = _load_json(directory / "run.json") or {}
    judge = _load_json(directory / "judge.json") or {}
    capability = _load_json(directory / "capability.json") or {}

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
    errors = {f: _num(raw_errors.get(f, run.get(f, 0))) for f in ERROR_FIELDS}

    wall = _num(run.get("wall_clock_seconds", run.get("wall_clock", 0)))

    rubric_src = judge.get("rubric") if isinstance(judge.get("rubric"), dict) else judge
    rubric = {d: _num(rubric_src.get(d)) for d in RUBRIC_DIMENSIONS}
    has_rubric = any(d in rubric_src for d in RUBRIC_DIMENSIONS)

    backend = str(run.get("backend") or directory.parent.name)
    category = str(run.get("category") or _category_from_path(directory))

    return {
        "directory": directory,
        "run_id": str(run.get("run_id") or directory.name),
        "backend": backend,
        "category": category,
        "engine_mode": str(
            capability.get("engine_mode") or run.get("engine_mode") or "unknown"
        ),
        "available": _as_bool(
            capability.get("available", run.get("available", True))
        ),
        "verification_passed": _as_bool(
            run.get("verification_passed", run.get("passed", False))
        ),
        "tokens": {"input": used_input, "output": used_output, "total": total},
        "errors": errors,
        "wall_clock_seconds": wall,
        "rubric": rubric,
        "has_rubric": has_rubric,
    }


def _category_from_path(directory: Path) -> str:
    """Best-effort category when ``run.json`` omits it: first parent that is a
    known category, else the grandparent directory name."""
    for part in reversed(directory.parts[:-1]):
        if part in CATEGORY_ORDER:
            return part
    return directory.parent.parent.name if directory.parent.parent else "unknown"


def discover_runs(results_dir: Path) -> list[dict[str, Any]]:
    """Every directory under ``results_dir`` holding a ``run.json``."""
    if not results_dir.is_dir():
        return []
    dirs = {p.parent for p in results_dir.rglob("run.json")}
    return sorted((read_run(d) for d in dirs), key=lambda r: (r["backend"], r["category"], r["run_id"]))


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------
def aggregate_backends(runs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for backend in _ordered({r["backend"] for r in runs}, BACKEND_ORDER):
        rows = [r for r in runs if r["backend"] == backend]
        n = len(rows)
        tokens = {
            kind: {
                "mean": _mean([r["tokens"][kind] for r in rows]),
                "median": _median([r["tokens"][kind] for r in rows]),
            }
            for kind in ("input", "output", "total")
        }
        error_totals = {f: _count(sum(r["errors"][f] for r in rows)) for f in ERROR_FIELDS}
        errors_total = _count(sum(_num(v) for v in error_totals.values()))
        out[backend] = {
            "available": any(r["available"] for r in rows),
            "engine_mode": _mode(rows),
            "n_runs": n,
            "pass_rate": _round(sum(1 for r in rows if r["verification_passed"]) / n) if n else 0.0,
            "tokens": tokens,
            "errors": {
                **error_totals,
                "total": errors_total,
                "mean_per_run": _round(errors_total / n) if n else 0.0,
            },
            "wall_clock": {
                "mean": _mean([r["wall_clock_seconds"] for r in rows]),
                "median": _median([r["wall_clock_seconds"] for r in rows]),
            },
            "rubric": {
                dim: {
                    "mean": _mean([r["rubric"][dim] for r in rows if r["has_rubric"]]),
                    "median": _median([r["rubric"][dim] for r in rows if r["has_rubric"]]),
                }
                for dim in RUBRIC_DIMENSIONS
            },
        }
    return out


def _mode(rows: list[dict[str, Any]]) -> str:
    """Most common engine_mode (ties -> alphabetical)."""
    modes: dict[str, int] = {}
    for r in rows:
        modes[r["engine_mode"]] = modes.get(r["engine_mode"], 0) + 1
    if not modes:
        return "unknown"
    return sorted(modes.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def aggregate_categories(runs: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for category in _ordered({r["category"] for r in runs}, CATEGORY_ORDER):
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
                "pass_rate": _round(
                    sum(1 for r in rows if r["verification_passed"]) / len(rows)
                ),
                "tokens_total_median": _median([r["tokens"]["total"] for r in rows]),
                "rubric_mean": _mean(rubric_values),
            }
        out[category] = by_backend
    return out


def pick_winners(backends: dict[str, Any]) -> dict[str, str]:
    """tokens/errors: lower is better, rubric dims: higher is better.

    Only backends flagged ``available`` compete; if none is, every backend does.
    Ties break alphabetically so the block is deterministic.
    """
    pool = [name for name, b in backends.items() if b["available"]] or list(backends)

    def best(key, better):
        candidates = [n for n in pool if key(n, backends[n]) is not None]
        if not candidates:
            return ""
        return sorted(candidates, key=lambda n: (key(n, backends[n]), n))[0] if better == "min" \
            else sorted(candidates, key=lambda n: (-key(n, backends[n]), n))[0]

    return {
        "tokens": best(lambda n, b: b["tokens"]["total"]["mean"], "min"),
        "quality": best(lambda n, b: b["rubric"]["quality"]["mean"] or None, "max"),
        "errors": best(lambda n, b: b["errors"]["mean_per_run"], "min"),
        "intelligence": best(lambda n, b: b["rubric"]["intelligence"]["mean"] or None, "max"),
        "coding": best(lambda n, b: b["rubric"]["coding"]["mean"] or None, "max"),
    }


def build_summary(runs: list[dict[str, Any]], *, generated_at: str | None = None) -> dict[str, Any]:
    backends = aggregate_backends(runs)
    return {
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "n_runs": len(runs),
        "backends": backends,
        "categories": aggregate_categories(runs),
        "winners": pick_winners(backends),
    }


# --------------------------------------------------------------------------
# markdown
# --------------------------------------------------------------------------
def render_markdown(summary: dict[str, Any]) -> str:
    backends = summary["backends"]
    winners = summary["winners"]
    lines = [
        "# Benchmark summary",
        "",
        f"Generated: `{summary['generated_at']}` — {summary.get('n_runs', 0)} runs, "
        f"{len(backends)} backend(s).",
        "",
        "| backend | mode | runs | pass rate | tokens total (mean / median) | "
        "wall clock (mean / median) | quality | intelligence | coding | errors (total / per run) |",
        "|---|---|---:|---:|---|---|---:|---:|---:|---|",
    ]
    for name, b in backends.items():
        flag = "" if b["available"] else " *(unavailable)*"
        lines.append(
            "| {name} | {mode} | {n} | {rate:.0%} | {tm:g} / {td:g} | "
            "{wm:g}s / {wd:g}s | {q:g} | {i:g} | {c:g} | {et:g} / {ep:g} |".format(
                name=name + flag,
                mode=b["engine_mode"],
                n=b["n_runs"],
                rate=b["pass_rate"],
                tm=b["tokens"]["total"]["mean"],
                td=b["tokens"]["total"]["median"],
                wm=b["wall_clock"]["mean"],
                wd=b["wall_clock"]["median"],
                q=b["rubric"]["quality"]["mean"],
                i=b["rubric"]["intelligence"]["mean"],
                c=b["rubric"]["coding"]["mean"],
                et=b["errors"]["total"],
                ep=b["errors"]["mean_per_run"],
            )
        )
    lines += ["", "**Winners**", "", "| metric | backend |", "|---|---|"]
    for metric in ("tokens", "quality", "errors", "intelligence", "coding"):
        lines.append(f"| {metric} | {winners.get(metric) or '-'} |")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    here = Path(__file__).resolve().parent
    parser.add_argument("--results", default=str(here / "results"), help="results directory to scan")
    parser.add_argument("--out", default=None, help="summary.json path (default: <results>/summary.json)")
    parser.add_argument("--quiet", action="store_true", help="do not print the markdown table")
    args = parser.parse_args(argv)

    results_dir = Path(args.results).resolve()
    runs = discover_runs(results_dir)
    if not runs:
        print(f"aggregate: no run.json found under {results_dir}", file=sys.stderr)
        return 1

    summary = build_summary(runs)
    out_path = Path(args.out).resolve() if args.out else results_dir / "summary.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, sort_keys=False) + "\n", encoding="utf-8")

    if not args.quiet:
        sys.stdout.write(render_markdown(summary))
    print(f"\nwrote {out_path} ({summary['n_runs']} runs, {len(summary['backends'])} backends)",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
