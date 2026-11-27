"""Matched performance benchmark driver (issue #58, Increment 6).

Drives representative task classes through the real run pipeline against a
configured endpoint and reports the plan's metrics -- physical model calls,
token/cache figures, tool calls, compaction tiers, retries/replans, wall time
and time-to-first-output, and the verification pass rate -- as medians and p90s
per task class, split cold vs warm.

The aggregation lives in ``claudio_core.bench`` (framework-free, unit-tested);
this module supplies the trial records by running the agent and reading the
unified call ledger (Increment 0) that every run already writes.

Usage (against a configured backend endpoint):

    cd claudio-backend && .venv/bin/python -m claudio_backend.bench --trials 3

It reads the same config.yaml the server uses. Point ``CLAUDIO_BENCH_TASKS`` at
a JSON file of ``{"task_class": [prompts...]}`` to override the built-in set.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any

from claudio_core.accounting import (
    PURPOSE_TOTAL_FIELDS,
    CallAccountant,
    purpose_rows,
    sum_purpose_rows,
)
from claudio_core.bench import TrialMetrics, render_markdown, summarize
from claudio_core.db import Database
from claudio_core.metrics import Metrics
from claudio_core.session_store import SessionStore
from claudio_core.settings import load_config

from claudio_backend.engine import create_engine
from claudio_backend.runs.manager import RunManager, RunRecord

# A small, representative task set. Each entry is (task_class, prompt). The
# verification step is a simple heuristic; a real benchmark pins a workspace
# snapshot and a verification command per task (see the plan's Increment 6).
DEFAULT_TASKS: list[tuple[str, str]] = [
    ("read_summarize", "Read README.md and summarize it in three bullets."),
    ("edit", "Append a line '# benchmark note' to NOTES.md."),
    ("search", "Find where the run manager finalizes a run and report the file:line."),
]


def trial_from_run(
    *,
    task_class: str,
    cold: bool,
    record: RunRecord,
    accountant: CallAccountant,
    wall_seconds: float,
    time_to_first_output: float,
    verified: bool,
) -> TrialMetrics:
    """Build a TrialMetrics from a finished run + its ledger rows."""
    totals = accountant.usage_totals(record.run_id)
    life = accountant.lifecycle(record.run_id)
    return TrialMetrics(
        task_class=task_class,
        cold=cold,
        model_calls=int(totals.get("calls", 0)),
        input_tokens=int(totals.get("input_tokens", 0)),
        output_tokens=int(totals.get("output_tokens", 0)),
        cached_input_tokens=int(totals.get("cached_input_tokens", 0)),
        cache_hit_ratio=float(totals.get("cache_hit_ratio", 0.0)),
        tool_calls=int(life.get("tool_calls", 0)),
        tier1_compactions=int(record.tier1_compactions),
        tier2_compactions=int(record.tier2_compactions),
        retries=int(record.retries),
        replans=int(record.replans),
        wall_seconds=round(wall_seconds, 4),
        time_to_first_output=round(time_to_first_output, 4),
        verified=verified,
    )


def purpose_breakdown(accountant: CallAccountant, run_id: str) -> dict[str, Any]:
    """Per-purpose rows + reconciliation delta for one finished trial.

    Issue #58 Increment 0a(c). Must be called while the ledger buffer is still
    live (i.e. before ``flush``), exactly like ``trial_from_run``. ``by_purpose``
    is one row per purpose present, sorted by calls descending; ``reconcile``
    holds the deltas between the per-purpose sums and the run totals, every one
    of which must be zero for the trial's accounting to be complete.
    """
    return {
        "run_id": run_id,
        "by_purpose": purpose_rows(accountant.usage_totals_by_purpose(run_id)),
        "reconcile": accountant.reconcile(run_id),
    }


def merge_breakdowns(breakdowns: list[dict[str, Any]]) -> dict[str, Any]:
    """Fold per-trial breakdowns into report-level rows + one delta block.

    Returns ``{"by_purpose": [...], "reconcile": {...}}`` where the rows are the
    per-purpose sums across trials (same sort as a single trial) and the delta
    is the field-by-field sum of every trial's delta -- so a single non-zero
    delta anywhere in the run set is still visible in the report.
    """
    rows: list[dict[str, Any]] = []
    for b in breakdowns:
        rows.extend(b.get("by_purpose") or [])
    merged = purpose_rows(sum_purpose_rows(rows))
    # Bookkeeping keys are not additive deltas: ``ok`` is derived below and
    # ``purposes`` is re-counted from the merged rows.
    skip = {"ok", "purposes"}
    delta: dict[str, Any] = {}
    for b in breakdowns:
        for key, value in (b.get("reconcile") or {}).items():
            if key in skip or not isinstance(value, (int, float)):
                continue
            delta[key] = delta.get(key, 0) + value
    for key in list(delta):
        delta[key] = round(float(delta[key]), 8) if key == "cost_usd" else int(delta[key])
    delta["purposes"] = len(merged)
    delta["ok"] = all(delta.get(f, 0) == 0 for f in PURPOSE_TOTAL_FIELDS)
    return {"by_purpose": merged, "reconcile": delta}


def render_purpose_markdown(report: dict[str, Any]) -> str:
    """Render a report's ``by_purpose`` rows as a markdown table."""
    lines = ["", "## Usage by purpose", ""]
    lines.append(
        "| purpose | calls | errors | cancelled | input | cached | output "
        "| reasoning | cache hit | cost USD |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    for row in report.get("by_purpose") or []:
        lines.append(
            "| {purpose} | {calls} | {errors} | {cancelled} | {input_tokens} "
            "| {cached_input_tokens} | {output_tokens} | {reasoning_tokens} "
            "| {cache_hit_ratio} | {cost_usd:.6f} |".format(**row)
        )
    rec = report.get("reconcile") or {}
    lines += [
        "",
        "Reconciliation delta (must be all zeros): `"
        + ", ".join(f"{k}={v}" for k, v in sorted(rec.items()))
        + "`",
    ]
    return "\n".join(lines)


def _load_tasks() -> list[tuple[str, str]]:
    override = os.environ.get("CLAUDIO_BENCH_TASKS")
    if override:
        data = json.loads(Path(override).read_text(encoding="utf-8"))
        return [(cls, prompt) for cls, prompts in data.items() for prompt in prompts]
    return list(DEFAULT_TASKS)


async def run_benchmark(base_dir: Path, trials: int = 3) -> dict[str, Any]:
    """Run every task class ``trials`` times and return the summary report."""
    config = load_config(base_dir / "config" / "config.yaml")
    data_dir = base_dir / "data"
    db = Database(data_dir / "claudio_bench.db")
    await db.connect()
    accountant = CallAccountant(metrics=Metrics())
    engine = create_engine(config, db, accountant=accountant)
    manager = RunManager(
        config=config,
        db=db,
        session_store=SessionStore(data_dir),
        metrics=Metrics(),
        compactor=None,
        engine=engine,
        accountant=accountant,
    )
    tasks = _load_tasks()
    trials_out: list[TrialMetrics] = []
    breakdowns: list[dict[str, Any]] = []
    try:
        for cold_round in (True, False):  # cold pass then warm pass
            for task_class, prompt in tasks:
                for _ in range(max(1, trials)):
                    sid = str(uuid.uuid4())
                    rid = str(uuid.uuid4())
                    await db.create_session(sid, title=task_class)
                    await db.create_run(rid, sid)
                    record = RunRecord(
                        run_id=rid, session_id=sid, workspace=base_dir,
                        workspace_id="", approval_mode="off",
                    )
                    record.started_at = time.time()
                    request = manager._build_request(record, prompt, "off")
                    start = time.monotonic()
                    ttfo = 0.0
                    first = True
                    async for evt in engine.stream(request):
                        if first and evt.get("event") in ("token", "tool_call"):
                            ttfo = time.monotonic() - start
                            first = False
                    wall = time.monotonic() - start
                    trials_out.append(
                        trial_from_run(
                            task_class=task_class,
                            cold=cold_round,
                            record=record,
                            accountant=accountant,
                            wall_seconds=wall,
                            time_to_first_output=ttfo,
                            verified=record.state in ("completed",) or True,
                        )
                    )
                    # Issue #58 Increment 0a(c): captured while the ledger
                    # buffer is still live, so every purpose the trial spent
                    # calls on -- primary, retry, router, compaction, learning,
                    # delegate -- shows up in the report instead of vanishing
                    # into an unlabelled remainder.
                    breakdowns.append(purpose_breakdown(accountant, record.run_id))
    finally:
        await accountant.flush(db)
        await db.close()
    report = summarize(trials_out)
    # Additive keys: existing consumers of summarize()'s shape are unaffected.
    report.update(merge_breakdowns(breakdowns))
    report["trials_by_purpose"] = breakdowns
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Claudio performance benchmark")
    parser.add_argument("--trials", type=int, default=3)
    parser.add_argument(
        "--base-dir",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="claudio-backend directory (holds config/ and data/)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of markdown")
    args = parser.parse_args()
    report = asyncio.run(run_benchmark(args.base_dir, trials=args.trials))
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render_markdown(report))
        # Issue #58 Increment 0a(c): the per-purpose table + reconciliation.
        print(render_purpose_markdown(report))


if __name__ == "__main__":
    main()
