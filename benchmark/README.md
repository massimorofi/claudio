# Engine benchmark harness

Offline, stdlib-only harness that runs the same locked task suite through each
Claudio engine backend and records a comparable artifact per run.

```
python3 benchmark/runner.py \
    --tasks benchmark/tasks.json \
    --capability benchmark/results/capability.json \
    --backends google_adk,claude_code,opencode \
    --trials 1 \
    --out benchmark/results
```

3 backends x 8 tasks x 1 trial = 24 runs in about 8 s on this machine (the
stub path never calls a model), well inside the 3-minute budget.

## Files

| path | owner | what it is |
| --- | --- | --- |
| `tasks.json` | T3 | the locked task manifest (prompts, fixtures, verification) |
| `fixtures/<id>/` | T3 | the seed tree each run starts from |
| `capability.json` | T2 | which backends are actually usable on this box |
| `runner.py` | T4 | the driver described here |
| `stub_engine.py` | T4 | deterministic offline engine + canned solutions |
| `tests/test_runner.py` | T4 | sandbox-isolation and timeout proofs |

## How one run works

For every `(backend, task, trial)` triple `runner.py`:

1. `tempfile.mkdtemp()` a sandbox **outside the worktree** (`make_sandbox`
   raises if the directory lands inside the repo, so the gate tree can never
   be dirtied by a run).
2. Copies `benchmark/fixtures/<id>/` into it. If that directory is missing the
   runner falls back to the embedded copy in `stub_engine.SEEDS`.
3. `git init` + one seed commit (fixed author/committer identity and dates, so
   `diff.patch` is reproducible).
4. Runs an engine:
   * **real** when `capability.json` marks the backend available *and* the
     binary resolves: `claude -p <prompt> --output-format stream-json
     --verbose`, `opencode run <prompt> --format json`, or google-adk
     in-process. `subprocess.run(..., timeout=task.max_seconds)` enforces the
     task timeout; a `TimeoutExpired` becomes `exit_status="timeout"`.
   * **stub** otherwise (and whenever a "real" backend turns out to be
     unrunnable). `stub_engine.run_stub` applies the canned solution for that
     task and writes a deterministic JSONL session log with `init`,
     `tool_call`, `tool_result`, `retry`, `usage` and `result` events. Token
     counts are a SHA-256 of `(backend, task_id, trial)` plus the prompt
     length, so artifacts are byte-stable across machines and Python hash
     seeds.
5. Runs the task's verification command in the sandbox.
6. `git add -A && git diff --cached` -> `diff.patch` (after pruning
   `__pycache__`/`.pytest_cache`, which are ignored by the repo anyway).
7. Writes the artifacts and removes the sandbox.

## Artifacts

`benchmark/results/<backend>/<task_id>/trial_<k>/`

| file | contents |
| --- | --- |
| `run.json` | summary: `backend`, `task_id`, `trial`, `engine_mode`, `duration_s`, `exit_status`, `timed_out`, `tokens{input,output,cache_read,cache_write,total}`, `errors{command_errors,tool_errors,retries,timeouts}`, `verification_passed` |
| `session.jsonl` | engine session log (JSONL) |
| `stdout.txt` / `stderr.txt` | raw engine output (`.txt`, not `.log` - `.gitignore` hides `*.log`) |
| `verification.txt` | output of the verification command |
| `diff.patch` | the change the engine made to the sandbox |

The runner commits its own output directory at the end (`--no-commit` turns
that off) so the tree is clean when it exits.

## Useful flags

| flag | meaning |
| --- | --- |
| `--trials N` | repeat every task N times (`trial_1..N`) |
| `--only a,b` | run a subset of task ids |
| `--timeout-cap S` | clamp every per-run timeout, for a fast smoke run |
| `--sandbox-dir D` | put sandboxes somewhere other than `$TMPDIR` |
| `--keep-sandbox` | leave the sandbox on disk (path is in `run.json`) |
| `--no-commit` | write artifacts without committing them |

## Tests

```
PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_runner.py -q
```

Hermetic (`tmp_path` only, no network): one test proves the repo tree is
byte-identical and git-clean after a run and that the sandbox lived outside it;
one proves a real engine that overruns `max_seconds` is killed and reported as
`timed_out`; one proves `make_sandbox` refuses to create a sandbox inside the
repo.
