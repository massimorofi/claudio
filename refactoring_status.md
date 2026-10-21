# Claudio refactoring — status

Last updated: 2026-10-21

This document tracks the migration of Claudio from a monolithic
FastAPI + OpenAI Agents SDK backend into a three-project layout. It is the
authoritative "where are we" record; the design rationale lives in
[`claudio_refactoring.md`](./claudio_refactoring.md), the prose documentation in
[`doc/`](./doc).

---

## Target architecture

```
claudio-core/       tools, DB, settings, event protocol, approvals, allowlist,
                    compaction, budgets      ← imports NO agent framework
claudio-backend/    ADK engine + FastAPI     ← the only ADK-aware deployable
claudio-frontend/   React SPA              ← unchanged
```

The refactor rests on one rule: **`claudio_core` must not know that any agent
framework exists.** That rule is enforced mechanically by an import-linter
contract in `claudio-core/pyproject.toml`, not by convention. A second contract
in `claudio-backend/pyproject.toml` confines ADK to `claudio_backend.engine` so
that swapping engines later touches one package rather than the whole backend.

---

## Progress summary

| Step | What | Status |
|---|---|---|
| 0 | Freeze the SSE wire contract | ✅ Done — `claudio-core/src/claudio_core/events.py` |
| 1 | Extract framework-free core | ✅ Done — 133 tests pass, `lint-imports` **KEPT** |
| 1b | Copy frontend, verify build | ✅ Done — `tsc -b` + `vite build` clean |
| 2a | ADK engine implementation | ✅ Done — `claudio_backend/engine/adk_engine.py`, exercised end-to-end |
| 2b | `RunManager` port | ✅ Done — `claudio_backend/runs/manager.py`, e2e-tested |
| 2c | HTTP API layer (`api/`) | ✅ Done — 11 routers, 61 routes, all respond |
| 2d | Engine flag-switch (legacy SDK vs ADK) A/B | ✅ All 3 scenarios pass — `_engine_ab/` |
| 3 | Autonomous daemon / tasks layer | ✅ Done — 14 modules, endpoints live |
| — | Documentation | ✅ This file + `doc/` + `README.md` |

**Bottom line:** the port is complete. The backend serves the full API over a
real HTTP server, drives runs through the ADK engine via the framework-free
`RunEngine` protocol, and the chat + approval pause/resume round-trip passes
end-to-end. `lint-imports` confirms ADK stays confined to the engine package.
The only remaining action is deleting the scratch directories (step 6).

---

## What is done, in detail

### Step 0 — SSE protocol frozen

`claudio_core/events.py` defines all 13 event names, a TypedDict payload per
event, `encode_event()` (validation + normalisation) and `format_sse()` (wire
rendering). Two load-bearing rules are enforced in code rather than trusted to
producers:

1. `tool_call.arguments` and `approval_required.arguments` must be **JSON
   strings**. If an object reaches React it is rendered as a child and the view
   crashes.
2. `tool_result.output` must be a **string**, for the same reason.

An unknown event name or a contract-violating payload raises `ValueError` at the
seam, so a producer bug surfaces loudly instead of as a blank screen.

Covered by 18 tests in `claudio-core/tests/test_event_protocol.py`.

### Step 1 — Core extraction

Method: taint classification rather than rewrite. Every upstream module was
labelled by whether it imports `agents`/`openai`. 21 modules were tainted, 34
were clean. Clean modules were copied verbatim; tainted modules were rewritten at
the seam so the framework dependency became an injected callable or moved to the
engine side.

| Core module | Origin | Notes |
|---|---|---|
| `db.py` | copied verbatim | 818 LOC SQLite layer, 10 tables |
| `settings.py` | copied | import repointed to `.safety.allowlist` |
| `memory.py` | copied verbatim | `CLAUDE.md` / `AGENTS.md` discovery |
| `instructions.py` | copied | import fixes only |
| `extract.py` | copied verbatim | docx/xlsx/pdf/text extraction |
| `logging_setup.py` | from `observability/logging.py` | |
| `metrics.py` | from `observability/metrics.py` | |
| `engine_metrics.py` | from `observability/engine_metrics.py` | Prometheus scrape for LM Studio |
| `safety/allowlist.py` | copied + absorbed `is_catastrophic` | |
| `context.py` | de-tainted | + new `context_usage_payload()` |
| `tools/*.py` | de-tainted | plain async functions taking `ToolContext` |
| `tools/registry.py` | rewritten | framework-free metadata + selection |
| `skill_loader.py` | de-tainted | `build_skill_functions()` returns a plain dict |
| `recovery.py` | de-tainted | input filter, token calibration |
| `guardrails.py` | rewritten | injection screening, secret redaction |
| `compaction.py` | de-tainted | injected `Summariser`, own 300 s timeout |
| `session_store.py` | de-tainted | JSONL mirror + `ConversationStore` protocol |
| `events.py` | **new** | Step 0 artifact |
| `engine.py` | **new** | `RunEngine` protocol |
| `llm.py` | **new** | framework-free chat-completion client |

Verification at time of writing:

```
claudio-core$ .venv/bin/python -m pytest -q     → 133 passed
claudio-core$ .venv/bin/lint-imports           → core is framework-free KEPT
```

### Step 2a — ADK engine

`claudio-backend/src/claudio_backend/engine/adk_engine.py` implements
`RunEngine` over Google ADK 2.12. Three findings drove the design; all were
established by probing ADK directly rather than from its documentation.

**Tool wrapping.** Every Claudio tool takes `ToolContext` as its first argument.
ADK builds its tool declaration from the callable's signature, so a bound `ctx`
would leak into the schema as a required model-facing parameter. Each tool is
wrapped in a `**kwargs` closure that is then given a synthetic `__signature__`
omitting `ctx`. Verified: ADK emits the correct JSON schema with `ctx` invisible.

**Approval / pause.** `require_confirmation=True` is inert unless the `App` is
built with `ResumabilityConfig(is_resumable=True)`. When it is set, ADK emits
three events and stops:

1. the model's original `function_call` (id `c1`)
2. an injected `adk_request_confirmation` call with its **own** id
   (`adk-<uuid>`) and `long_running_tool_ids: ["adk-<uuid>"]` ← the pause signal
3. a `function_response` for the *original* id `c1` carrying an error, with
   `actions.requested_tool_confirmations = {"c1": {...}}`

Resuming requires a `function_response` keyed by the **confirmation call's** id,
not the original tool-call id. Using `c1` produces zero events — a silent no-op.
This is the single most important thing in the file.

**Cross-process resume verified.** A completely fresh Python process with a new
`SqliteSessionService`, new `FunctionTool`s, new `App` and new `Runner`
successfully resumed a run that a *different, already-exited* process had paused.
That property is what makes Claudio's "pause → HTTP approval → resume" flow work
on top of ADK.

**Text.** ADK delivers text per-response, not token-by-token, so a `token` event
from this engine is a chunk of model output rather than a streamed token. The
contract permits that; the frontend appends.

`create_engine(config, db, kind=None)` is the factory. `kind` defaults to the
`CLAUDIO_ENGINE` env var, then `"adk"`.

---

## Port completed

### 2b — `RunManager`

`claudio_backend/runs/manager.py` now owns the full run lifecycle against the
`RunEngine` protocol instead of the SDK `Runner`: `RunRecord` (ring buffer,
subscriber set, cancel event, accumulators), `start_run` / `resume_run` /
`cancel_run` / `request_compact`, the `open_stream` / `close_stream` reconnect
machinery, the budget gate, the recovery loop (retry/backoff, fallback model,
overflow restart), and finalisation (`run_completed` summary, deliverable
verification, session-total update, JSONL transcript mirror).

Two behavioural differences from the SDK-coupled original follow from the
engine owning the conversation: the recovery loop reads terminal *events*
(`run_completed` / `run_failed` / `approval_required`) rather than catching
exceptions, and `request_compact` keeps its external contract but refreshes the
context gauge rather than rewriting the model context mid-run (the engine owns
that). The mutable accumulators are persisted to the run/session rows and
rebuilt on resume, as planned.

**Approval id fix.** The `approval_required` event carries the **database**
approval id (the value `db.create_approval` returns), not the ADK confirmation
id — because the client resolves via `/approvals/{id}`, which looks the row up
by its own id. The ADK confirmation id is kept only inside `resume_state_json`,
which is what `engine.resume` needs to feed the `function_response` back. This
matches upstream exactly and is what makes the pause → HTTP approval → resume
round-trip work.

### 2c — HTTP API

All 11 routers are ported under `/api/v1` (61 routes): `system`, `management`,
`config`, `chat`, `approvals`, `sessions`, `monitoring`, `workspace`,
`autonomous`, `tasks`, `files`. Each is thin — auth check, core call, JSON
response — with `chat.py` the substantial one (SSE streaming). The app factory
(`app.py`) wires CORS, the request-counter middleware, the `ApiError` /
validation / catch-all handlers, and the routers; `create_app()` adds a
lifespan that starts/stops the supervisor.

### 2d — Engine flag-switch

`create_engine` accepts `kind`, so the plumbing exists. A deterministic A/B
harness lives in `_engine_ab/ab_test.py`. It drives both engines against the
**same scripted model behaviour** served by a fake OpenAI-compatible
chat-completions SSE server, so any difference in the resulting Claudio event
stream is a real translation difference rather than model nondeterminism.

Run it from the backend directory (needs both `agents` and `google-adk`, which
`claudio-backend/.venv` has):

```bash
cd claudio-backend
PYTHONPATH=src .venv/bin/python ../_engine_ab/ab_test.py
```

**Current result: all 3 scenarios pass.**

| Scenario | Result |
|---|---|
| plain text | MATCH — identical token stream |
| tool call → text | MATCH — identical `tool_call` / `tool_result` payloads |
| approval pause/resume | OK — `approval_required` and `run_completed` both emitted |

The first version of this harness reported a mismatch on scenario 2. The cause
was attribute drift in the OpenAI Agents SDK, and it was in the **legacy**
translator, not the new engine:

- `_upstream/backend/src/claudio/runs/events.py` reads `item.raw_input`, which
  does not exist on `ToolCallItem` in `openai-agents` 0.23.1 — the version
  upstream itself pins. `getattr(..., None)` returns `None` and `_safe_args`
  falls back to `"{}"`, so every legacy `tool_call` went out with empty
  arguments. The arguments live at `item.raw_item.arguments`.
- The same file reads `getattr(item, "tool_name", None)` on `tool_output`.
  `ToolCallOutputItem` has no `tool_name` attribute either (only `ToolCallItem`
  does), so every legacy `tool_result` went out with `name: null`.

`_engine_ab/legacy_translator.py` is the corrected baseline: it reads
`raw_item.arguments` and remembers the `call_id → name` mapping from the
preceding `tool_called` event so `tool_result` can be labelled. `_upstream/` is
left untouched as a historical reference. With the corrected baseline the two
engines agree, which is the point of the exercise: the ADK engine reproduces
Claudio's wire format exactly.

### 3 — Autonomous daemon

Ported: health checks, scheduled maintenance, boot recovery, user cron tasks
and the fixer/suggestions flow — 14 modules under
`claudio_backend/autonomous/` (`health`, `scheduler`, `service`, `maintenance`,
`recovery`, `task_runner`, `task_scheduler`, `fixer`, `notifier`, `agent`,
`tools`, `script`, `report`). The supervisor starts the health and task
schedulers on boot; both gate themselves on `autonomous.enabled` each cycle, so
toggling it needs no restart. All `/api/v1/autonomous/*` and
`/api/v1/autonomous/tasks*` endpoints respond.

Note: MCP servers are not wired into the current engine. If `mcp.servers` are
configured the supervisor logs a one-time warning and ignores them.

---

## Known issues and open questions

1. **`google-adk[openai]` and `openai-agents` declare incompatible `openai`
   ranges.** ADK requires `openai>=2.20,<3`; `openai-agents 0.23.1` requires
   `openai>=3.0.0,<4`. They cannot both be satisfied by pip's metadata. In
   practice `claudio-backend/.venv` runs with `openai 2.54.0` and the legacy
   path in the A/B harness works, so the declared conflict is over-cautious
   rather than real — but it means the two engines cannot be co-installed from a
   clean resolve. This only affects the A/B harness and any transitional period
   where both engines are present. Once step 2b is done and the legacy path is
   dropped, the conflict disappears with it.
2. **`claudio_backend.runs` now exports `RunManager`, `RunRecord`,
   `BudgetExceededError`.** Filled in with step 2b.
3. **The ADK engine has executed end-to-end against a scripted model** (the
   e2e suite drives the full request path: chat stream, tool call, approval
   pause/resume, completion). It has not been run against a *live* model — no
   LM Studio instance was available in this environment.
4. **`OpenAILlm` vs `OpenAIResponsesLlm`.** Upstream used
   `OpenAIChatCompletionsModel`, so `OpenAILlm` is the correct analogue for an
   OpenAI-compatible chat-completions endpoint such as LM Studio. Unverified
   against a live endpoint.
5. **`history` on `RunRequest` is currently unused by the ADK engine.** ADK owns
   its own conversation state, so it does not need seeded history. The field
   exists for engines that do, and for post-compaction restarts.
6. **The TypeScript event mirror is not genuinely exhaustive.**
   `claudio-frontend/src/api/types.ts` declares event payload interfaces but
   does not enumerate the event string literals, so the "mirror" test checks
   names rather than coverage. Worth tightening when the protocol is next touched.
7. **`_upstream/` and `_deferred_backend_tests/` are working scratch.** They
   should be deleted once the port is complete.

---

## How to verify the current state

```bash
# Core: tests + framework-free contract
cd claudio-core
.venv/bin/python -m pytest -q      # expect: 133 passed
.venv/bin/lint-imports             # expect: core is framework-free KEPT

# Backend: install, ADK-confinement contract, engine construction
cd ../claudio-backend
.venv/bin/pip install -e ".[dev]"
.venv/bin/lint-imports             # expect: only the engine knows about ADK KEPT
PYTHONPATH=src:../claudio-core/src python -c "
from claudio_backend.engine import create_engine
from claudio_core import AppConfig, Database
import pathlib, tempfile
db = Database(pathlib.Path(tempfile.mkdtemp()) / 't.db')
print(type(create_engine(AppConfig(), db)).__name__)
"

# Engine A/B: legacy SDK vs ADK against identical scripted model output
PYTHONPATH=src python ../_engine_ab/ab_test.py   # expect: OVERALL: ALL PASS

# Frontend: typecheck + build
cd ../claudio-frontend
npx tsc -b
npm run build
```

Note that each block above must be run from the directory it names. `pytest` and
`lint-imports` both resolve configuration relative to the working directory, so
running them from the repo root fails with collection errors or
"Could not read any configuration" rather than reporting on the project.

---

## Next actions, in order

1. ~~Port `runs/manager.py`~~ ✅ done.
2. ~~Port `api/`~~ ✅ done — all 11 routers, 61 routes.
3. Run one real chat run against LM Studio with approval pause/resume. (The
   scripted-model e2e suite covers this path; a live-model pass is still worth
   doing when an LM Studio instance is available.)
4. ~~Port the autonomous daemon layer~~ ✅ done.
5. Delete `_upstream/` and `_deferred_backend_tests/` (scratch; pending
   confirmation).
