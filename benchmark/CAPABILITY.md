# Backend capability report

Which of the three Claudio backend engines can actually run **in this
container**, and what the benchmark harness falls back to when one cannot.
Generated from [`probes.py`](probes.py) by [`capability.py`](capability.py);
the machine-readable twin of this page is
[`results/capability.json`](results/capability.json).

Regenerate (idempotent — only `generated_at` changes):

```bash
python3 benchmark/capability.py            # rewrites results/capability.json
PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_capability.py -q
```

Snapshot taken: `2026-11-27T21:09:29Z` (Python 3.11.18).

## Availability

A backend is **available** only when *every* prerequisite below is satisfied.
Probes are offline: PATH lookup, importable-module lookup, environment
variables. No credential is ever validated against a live endpoint, and no CLI
is executed.

| backend | binary | env vars (any one satisfies the credential) | available | missing prerequisites | fallback used offline |
| --- | --- | --- | --- | --- | --- |
| `google_adk` | — (library, no CLI) | `CLAUDIO__LLM__API_KEY`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` | no | python module `google.adk` **is** installed; no credential set | `stub_driver` |
| `claude_code` | `claude` | `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, `CLAUDIO__LLM__API_KEY` | no | binary `claude` not found on PATH; no credential set | `stub_driver` |
| `opencode` | `opencode` | `OPENAI_API_KEY`, `OPENAI_AUTH_TOKEN`, `CLAUDIO__LLM__API_KEY` | no | binary `opencode` not found on PATH; no credential set | `stub_driver` |

Optional env vars: `OPENAI_BASE_URL`, `ANTHROPIC_BASE_URL`,
`CLAUDIO__ENGINE__CLAUDE_CODE__ALLOW_OPENAI_ENDPOINT`, `CLAUDIO_ENGINE`
(engine selector). Binary names are overridable with
`CLAUDIO__ENGINE__CLAUDE_CODE__BINARY` / `CLAUDIO__ENGINE__OPENCODE__BINARY`
(config fields `engine.claude_code.binary`, `engine.opencode.binary`).

## What `stub_driver` means

When a backend is not available, the harness does **not** skip it silently: it
drives that backend with a deterministic stub driver so the run still produces
a full result matrix. Numbers from a `stub_driver` row measure harness
overhead only (queueing, persistence, event plumbing) and are **not** comparable
to a real-engine run. Compare engines only when both rows report
`available: yes` / fallback `none`.

To make a row real, install the missing piece and rerun the generator:

| backend | to make it available |
| --- | --- |
| `google_adk` | `pip install google-adk` (already present here) **and** export a model key, e.g. `CLAUDIO__LLM__API_KEY=sk-...` |
| `claude_code` | `claude` CLI (not installed here) **and** `export ANTHROPIC_API_KEY=sk-ant-...` (or `ANTHROPIC_AUTH_TOKEN` for a gateway) |
| `opencode` | `opencode` CLI pinned to 1.19.6 (`scripts/install-engine.sh`) **and** `export OPENAI_API_KEY=...` |

## Where the probe metadata comes from

Nothing here is guessed; each value is read out of this worktree:

* engine ids `adk` / `google_adk`, `claude_code`, `opencode` —
  `claudio-backend/src/claudio_backend/engine/factory.py`
* default binaries `claude`, `opencode` —
  `claudio-core/src/claudio_core/settings.py`
  (`ClaudeCodeEngineConfig.binary`, `OpenCodeEngineConfig.binary`)
* `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL` —
  `claudio-backend/src/claudio_backend/engine/claude_code/adapter.py`
* `OPENAI_API_KEY` (the generated `opencode.json` references
  `{env:OPENAI_API_KEY}`) —
  `claudio-backend/src/claudio_backend/engine/opencode/adapter.py`
* `CLAUDIO__LLM__API_KEY` — `ENV_PREFIX = "CLAUDIO__"` plus the secret path
  `llm.api_key` in `claudio-core/src/claudio_core/settings.py`

## Constraints this artifact upholds

* **stdlib only** — `probes.py` / `capability.py` import nothing third-party
  (asserted by the tests via AST).
* **offline** — no network module, no subprocess; a hanging CLI can never hang
  the probe.
* **never raises** — a broken probe degrades to `available: false` with a
  `missing` reason.
* **idempotent** — rerunning changes only `generated_at`, so the committed diff
  stays one line.
* **committed** — the artifact is JSON under `benchmark/results/`, never a
  `.log` file (repo `.gitignore` hides `*.log`, which would silently drop it
  from the commit).
