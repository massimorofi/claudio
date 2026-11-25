# Claudio engine benchmark

A stdlib-only, offline benchmark harness that runs the same coding task through
each pluggable engine backend (**ADK**, **Claude Code**, **OpenCode**) and writes
comparable, committed artifacts per run.

```
benchmark/
  runner.py          # the driver (CLI below)
  stub_engine.py     # deterministic offline stand-in engine
  tasks.json         # task definitions (id, prompt, verify, timeout)
  fixtures/<id>/     # optional seed trees copied into each sandbox
  results/           # committed artifacts
    capability.json  # which backends can run for real on this machine
    <backend>/<task_id>/trial_<k>/{run.json,stdout.txt,stderr.txt,
                                   session.jsonl,diff.patch,verify.txt,prompt.txt}
  tests/test_runner.py
```

## Running

```bash
python3 benchmark/runner.py --tasks benchmark/tasks.json \
    --capability benchmark/results/capability.json \
    --backends google_adk,claude_code,opencode --trials 1 \
    --out benchmark/results
```

One summary line is printed per run, plus a final `SUMMARY` line. The full
3 backends x 7 tasks x 1 trial matrix finishes in ~4 s (well under the 3-minute
gate budget). Useful extra flags: `--only <task_id,...>`, `--engine-mode
auto|stub|real`, `--keep-sandbox`, `--probe-capability`.

If `benchmark/tasks.json` is absent the runner falls back to its built-in task
set (7 small Python tasks, each with an inline seed, a canned solution and a
`unittest` verification command), so the pipeline always runs end-to-end.

## What one run does

1. `tempfile.mkdtemp()` **outside the repo** (the worktree is never used as a
   scratch space, so the gate tree stays clean; the runner refuses a sandbox
   inside the repo).
2. Copies `benchmark/fixtures/<id>` into the sandbox, or seeds the sandbox from
   the task's inline `seed_files`.
3. `git init` + commits the seed, so the engine's work is measurable as a diff.
4. Runs an engine:
   * **real** when `capability.json` marks the backend available —
     `claude -p <prompt> --output-format stream-json --verbose`,
     `opencode run <prompt> --format json --thinking`, or an in-process
     `google.adk` runner;
   * otherwise the **stub engine**, which emits a deterministic JSONL session
     log (usage records with input/output/cache token counts, `tool_call`,
     `tool_result`, `error` and `retry` events) and applies the task's canned
     solution patch.
   Real engines are opt-in on a gate: set `CLAUDIO_BENCH_ALLOW_REAL=1` to let an
   in-process ADK run be attempted. If a "real" run cannot produce a change, the
   backend is downgraded to the stub for the rest of the matrix and the record
   says so.
5. Enforces the task timeout with `subprocess.run(timeout=...)`; a killed run is
   recorded as `exit_status="timeout"`, `timed_out=true`, `errors.timeouts=1`.
6. Captures `stdout.txt` / `stderr.txt` (never `*.log` — `.gitignore` hides
   those), runs the task verification command inside the sandbox, and captures
   `git diff` as `diff.patch`.
7. Writes `run.json`.

## run.json

```json
{
  "backend": "claude_code", "task_id": "fix_sum_list", "trial": 1,
  "engine_mode": "stub", "duration_s": 0.18, "exit_status": "ok",
  "timed_out": false,
  "tokens":  {"input": 2288, "output": 381, "cache_read": 1074, "cache_write": 0, "total": 3743},
  "errors":  {"command_errors": 0, "tool_errors": 0, "retries": 0, "timeouts": 0},
  "verification_passed": true
}
```

Extras: `engine_exit_code`, `verify_command`, `verify_exit_code`, `seeded_from`,
`changed_files`, `diff_lines`, `session_events`, `timeout_s`, `notes`.

`tokens.total` is `input + output + cache_read + cache_write`. Token counts and
error events for stub runs are a deterministic function of
`(backend, task_id, trial)`, so re-running the matrix reproduces them exactly.

## Tests

```bash
PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_runner.py -q
```

Covers sandbox isolation (no sandbox directory ever appears inside the repo and
the worktree's `git status` is unchanged), timeout handling (a slow engine is
killed and recorded), artifact completeness and stub determinism. No network.
