# benchmark/

Offline harness artifacts for measuring the Claudio backend engines
(`google_adk`, `claude_code`, `opencode`) under identical conditions.

## Contents

| path | what it is |
| --- | --- |
| `probes.py` | stdlib-only, offline availability probes. `probe_all(environ=None) -> dict`, never raises. |
| `capability.py` | `build_capability(environ=None) -> dict` = probe output + per-backend `fallback` (`none` / `stub_driver`) + `notes`. `__main__` writes `results/capability.json`. |
| `results/capability.json` | committed machine-readable capability report (`indent=2`, sorted keys, deterministic apart from `generated_at`). |
| `CAPABILITY.md` | human-readable table: binary, env vars, available yes/no, missing prerequisites, offline fallback. |
| `tests/test_capability.py` | contract tests for the above (`PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_capability.py -q`). |

## Usage

```bash
python3 benchmark/capability.py            # regenerate the artifact
python3 benchmark/capability.py --stdout   # print without writing
```

`build_capability()` accepts an explicit `environ` mapping, which is how the
tests probe hypothetical environments (missing binary, placeholder key)
without touching the host.

## Rules for anything added here

* stdlib only, no network, no subprocess in probes;
* artifacts must be committable files — never `*.log` (gitignored repo-wide);
* regenerating an artifact must change nothing but its timestamp.
