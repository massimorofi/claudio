# benchmark

Backend comparison harness artefacts and reporting for Claudio.

This folder holds the **reporting** half of the benchmark. `report.py` turns
whatever run artefacts exist under `benchmark/results/` into
`BENCHMARK_REPORT.md` at the repo root.

```
benchmark/
├── report.py              # renders BENCHMARK_REPORT.md (stdlib only, no deps)
├── README.md
├── tasks.json             # task definitions (owned by the harness)
├── results/               # run artefacts (written by the runner)
│   ├── summary.json
│   ├── capability.json
│   └── <backend>/<task>/trial-<n>/{run.json,judge.json}
└── tests/test_report.py
```

## Render the report

```bash
python3 benchmark/report.py --results benchmark/results --out BENCHMARK_REPORT.md
```

It always exits `0` and always writes a report, even when `benchmark/results/`
is missing or contains corrupt JSON - the report then says plainly that no run
data was recorded instead of inventing numbers. Unreadable artefacts are listed
under *Caveats*.

## Tests

```bash
PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_report.py -q
```

## Report sections

`## Executive summary`, `## KPI comparison`, `## Per-task breakdown`,
`## Failure modes`, `## Token efficiency vs quality`, `## Caveats`,
`## Real-backend runbook`.

The five headline KPIs are tokens per task, quality, issues/errors per task,
intelligence and coding ability.

## Artefact schema (as read)

The loader is tolerant: it accepts the spellings below plus common aliases
(`engine`/`backend`, `total_tokens`/`tokens`, `scores`/`rubric`, list- or
map-shaped `backends`).

`capability.json` / `summary.json` → `backends`:

```json
{"backends": {"claude_code": {"available": false, "engine_mode": "stub",
               "reason": "cli_not_found", "model": null}}}
```

`results/<backend>/<task>/trial-<n>/run.json`:

```json
{"task_id": "fix-flaky-test", "backend": "claude_code", "trial": 1,
 "engine_mode": "stub", "ok": true, "tokens": 1200, "latency_ms": 900,
 "errors": ["cli_not_found"], "timeouts": 0, "config": {"temperature": 0.0}}
```

`.../judge.json`:

```json
{"judge_mode": "heuristic", "scores": {"quality": 4.0, "intelligence": 3.5,
 "coding_ability": 4.5}, "issues": ["no regression test added"],
 "cited_evidence": ["workspace/claude_code/fix-flaky-test/patch.diff"]}
```

Rubric values may be on any scale (0-1, 0-5, 0-10, 0-100); they are normalised
to 0-1 before averaging.

## The stub rule

`engine_mode` decides everything. A trial is **real** only if it records
`engine_mode: "real"` (or `cli`/`live`/`in_process`/`subprocess`/`api`) and the
capability probe marks the backend available. Anything else - `stub`, `fake`,
`mock`, `echo`, or absent - is **stub**.

Stub numbers are rendered, tagged `(stub)`, and never allowed to win a KPI or
drive a backend recommendation. If no backend has real data, the report says so
instead of ranking test doubles.

## Getting real numbers

See *Real-backend runbook* in `BENCHMARK_REPORT.md` for the exact argv and
environment per adapter (`claude -p --output-format stream-json --verbose
--permission-mode ...` with `ANTHROPIC_BASE_URL`/`ANTHROPIC_API_KEY`;
`opencode run <prompt> --format json --thinking` with
`OPENAI_API_KEY`/`OPENAI_BASE_URL`; `google_adk` in-process via
`claudio_backend.engine.create_engine(config, kind="adk"|"google_adk")`) and the
`--trials 3` re-run commands.
