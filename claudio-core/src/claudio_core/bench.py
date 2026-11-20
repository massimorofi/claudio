"""Matched performance benchmark aggregation (issue #58, Increment 6).

The plan's Increment 6 is a repeatable benchmark that measures, per task class
and split cold vs warm, the physical model calls, token/cache figures, tool
calls and concurrency overlap, time-to-first-output and end-to-end wall time,
and the task verification pass rate -- so an optimization is only accepted when
it moves those numbers without hurting verification quality.

This module is the framework-free aggregation layer: it takes a list of
:class:`TrialMetrics` (one per trial, produced by whatever drives the runs)
and computes medians and p90s per task class. Keeping it pure makes the report
deterministic and unit-testable; the backend CLI that actually drives runs and
fills these records lives in ``claudio-backend/bench``.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from statistics import median
from typing import Any

# Metrics we report medians/p90s for. Each is numeric on TrialMetrics.
REPORTED_METRICS = (
    "model_calls",
    "input_tokens",
    "output_tokens",
    "cached_input_tokens",
    "cache_hit_ratio",
    "tool_calls",
    "tier1_compactions",
    "tier2_compactions",
    "retries",
    "replans",
    "wall_seconds",
    "time_to_first_output",
)


@dataclass
class TrialMetrics:
    """One benchmark trial's measured outcome."""

    task_class: str
    cold: bool = False
    model_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0
    cache_hit_ratio: float = 0.0
    tool_calls: int = 0
    tier1_compactions: int = 0
    tier2_compactions: int = 0
    retries: int = 0
    replans: int = 0
    wall_seconds: float = 0.0
    time_to_first_output: float = 0.0
    verified: bool = False
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile; 0.0 for an empty list."""
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    idx = min(len(ordered) - 1, max(0, int(round(pct * (len(ordered) - 1)))))
    return float(ordered[idx])


def summarize(trials: list[TrialMetrics]) -> dict[str, Any]:
    """Aggregate trials into a per-task-class report of medians and p90s.

    Returns ``{"overall": {...}, "by_task_class": {cls: {...}}}`` where each
    block has, per reported metric, ``{"median": x, "p90": y, "n": count}``,
    plus ``verify_pass_rate`` and the cold/warm split counts.
    """
    def _block(group: list[TrialMetrics]) -> dict[str, Any]:
        out: dict[str, Any] = {"n": len(group)}
        if not group:
            return out
        for metric in REPORTED_METRICS:
            vals = [float(getattr(t, metric)) for t in group]
            out[metric] = {
                "median": round(median(vals), 4),
                "p90": round(percentile(vals, 0.90), 4),
            }
        verified = sum(1 for t in group if t.verified)
        out["verify_pass_rate"] = round(verified / len(group), 4)
        out["cold_trials"] = sum(1 for t in group if t.cold)
        out["warm_trials"] = sum(1 for t in group if not t.cold)
        return out

    by_class: dict[str, Any] = {}
    classes = sorted({t.task_class for t in trials})
    for cls in classes:
        group = [t for t in trials if t.task_class == cls]
        by_class[cls] = {
            "all": _block(group),
            "cold": _block([t for t in group if t.cold]),
            "warm": _block([t for t in group if not t.cold]),
        }
    return {"overall": _block(trials), "by_task_class": by_class}


def render_markdown(report: dict[str, Any]) -> str:
    """Render a summarize() report as a readable markdown table."""
    lines = ["# Claudio performance benchmark", ""]
    overall = report.get("overall", {})
    lines.append(f"- Trials: {overall.get('n', 0)}")
    lines.append(f"- Verify pass rate: {overall.get('verify_pass_rate', 0)}")
    lines.append("")
    lines.append("| task class | metric | median | p90 |")
    lines.append("|---|---|---|---|")
    for cls, blocks in report.get("by_task_class", {}).items():
        block = blocks.get("all", {})
        for metric in REPORTED_METRICS:
            if metric in block:
                lines.append(
                    f"| {cls} | {metric} | {block[metric]['median']} "
                    f"| {block[metric]['p90']} |"
                )
    return "\n".join(lines)


__all__ = [
    "TrialMetrics",
    "REPORTED_METRICS",
    "percentile",
    "summarize",
    "render_markdown",
]
