# Multi-LLM Plan for Claudio

**Goal:** let Claudio run on more than one model. A **primary** model plans and does the
complex, reasoning-heavy work; **worker** models handle simpler, mechanical subtasks —
writing a script, a single source file, retrieving/summarising data — and a **fast**
model handles one-shot utility jobs (compaction summaries, titles, fixer proposals).

This plan is grounded in the current architecture (post ADK refactoring). Each phase is
independently shippable and backward compatible: a config with no new sections behaves
exactly as today.

---

## 1. Where things stand today

| Concern | Today | File |
|---|---|---|
| Model config | Single `llm` section: `model`, `base_url`, `api_key`, `temperature`, `max_tokens`, `fallback_model`, pricing | `claudio-core/src/claudio_core/settings.py` (`LLMConfig`) |
| Engine contract | `ModelSpec` (framework-neutral description of one endpoint) + `RunRequest` | `claudio-core/src/claudio_core/engine.py` |
| Agent engine | One `LlmAgent` per run, built from `request.model` | `claudio-backend/src/claudio_backend/engine/adk_engine.py` |
| Run wiring | `_model_spec()` builds the spec from `config.llm`; retry + `fallback_model` on failure | `claudio-backend/src/claudio_backend/runs/manager.py` |
| One-shot calls | `openai_chat_completion()` — compaction summariser (injected callable), title generation, fixer proposals | `claudio-core/src/claudio_core/llm.py`, `compaction.py`, `autonomous/fixer.py` |
| Subagents | `SubagentConfig` (description, instructions, tools, max_turns) **exists in settings but is not wired to anything** | `settings.py` |
| Tools | 10 built-ins with read-only/destructive metadata + approval policy | `claudio-core/src/claudio_core/tools/registry.py` |
| Config UI | Tabs: llm / agent / mcp / danger | `claudio-frontend/src/features/config/ConfigView.tsx` |

Two observations that make this easier than it looks:

1. **`ModelSpec` is already per-run.** Nothing structurally binds Claudio to one model —
   the whole run just happens to use the same spec everywhere.
2. **`SubagentConfig` is already the shape of a worker.** It has instructions, a tool
   subset and a turn budget. It only lacks a model assignment and an implementation.

---

## 2. Design: profiles + roles + delegation

### 2.1 Model profiles (config layer)

Named model configurations, each fully describing one reachable endpoint:

```yaml
# The existing `llm` section stays and IS the primary profile (backward compatible).
llm:
  model: big-reasoning-model
  base_url: http://127.0.0.1:1234/v1
  ...

# Additional named profiles.
model_profiles:
  qwen-fast:
    label: "Qwen 3.5 30B (fast)"
    provider: openai
    model: qwen3-30b
    base_url: http://127.0.0.1:1235/v1
    api_key: local
    temperature: 0.3
    max_tokens: 8192
    context_window: 131072        # per-profile: workers may be smaller
    price_input_per_mtok: 0.2
    price_output_per_mtok: 0.6

# Which profile plays which role.
roles:
  primary: primary        # omitted keys fall back to the `llm` section
  worker: qwen-fast       # delegated subtasks
  fast: qwen-fast         # one-shot jobs (compaction, titles, fixer)
```

**Roles:**

- **`primary`** — the main agent. Owns the conversation, plans, decomposes tasks,
  reviews delegated output, handles anything requiring real reasoning.
- **`worker`** — runs delegated subtasks with a restricted tool set.
- **`fast`** — cheap one-shot calls that don't need the primary model at all.

**Resolution rule:** `resolve_profile(role)` → the profile named in `roles.<role>`,
falling back to the `llm` section when `roles` / `model_profiles` are absent. One
helper in `settings.py`, used by every call site.

**Secrets:** `SECRET_PATHS` in `settings.py` is a static list; it must become dynamic
for `model_profiles.<name>.api_key` (redact on read, preserve on save — the existing
`redact_secrets` / `restore_redacted_secrets` helpers just need to enumerate the
profile keys instead of a fixed list).

### 2.2 Delegation is the routing mechanism (v1: model-driven)

The primary model decides what to hand off. A new core tool:

```python
delegate_task(task: str, agent: str) -> {output, usage}
```

- `agent` names a `SubagentConfig` (e.g. `"script_writer"`, `"data_retriever"`).
- The tool builds a **fresh, stateless sub-agent** on the worker profile with:
  - the subagent's own instructions (`instructions_file` / `instructions`),
  - only the tools listed in `SubagentConfig.tools` (validated against the registry),
  - its own `max_turns` and context budget (from the profile's `context_window`),
  - **no conversation history** — the task string must be self-contained (this is
    stated in the primary's instructions so it writes good task briefs).
- The sub-agent runs to completion inside the tool call; its final text is the tool
  result the primary model sees. The existing `tool_call` / `tool_result` events
  already surface this in the chat UI with no frontend change.

**Why delegation over a per-message classifier?** A classifier that labels each user
message "simple/complex" and routes the whole run is fragile: misclassification is
invisible, follow-up turns inherit the wrong model, and "simple" tasks turn out not
to be. Model-driven delegation keeps the smart model in charge of decomposition —
which is exactly the user's mental model: *the principal plans, the helpers execute.*

**Approval for worker writes (key decision).** Worker subtasks like "write a script"
use destructive tools (`write_file`). Pausing a *nested* ADK run for confirmation is
fragile (the engine docstring in `adk_engine.py` documents how delicate the
confirmation/resume mechanics already are). Recommendation:

> **One approval gate at delegation time.** If the subagent's tool list contains any
> destructive tool, `delegate_task` itself requires approval (the approval card shows
> the task brief and the tools the worker may use). The sub-agent then runs with
> approval off. The primary model reviews the result — and can read back files it
> wrote — before continuing.

This keeps the human in the loop at the meaningful boundary (what work is being
outsourced) instead of inside a nested loop.

### 2.3 Instructions carry the routing knowledge

`claudio_core/instructions.py` renders the available subagents (name + description +
tools) into the primary's system prompt, with explicit guidance:

- Delegate mechanical, well-scoped subtasks: write this one script, generate this one
  module, fetch and summarise this data, draft this config file.
- Keep for yourself: planning, architecture, multi-file changes, debugging, anything
  requiring judgment.
- After delegation, **review** the worker's output (read the file it wrote) before
  building on it.
- Write self-contained task briefs: the worker sees nothing else.

This is where the user's split becomes real behaviour — cheap models doing the typing,
the strong model doing the thinking.

---

## 3. Implementation phases

### Phase 1 — Profiles and roles (config + API + UI)

1. **`claudio-core/settings.py`**
   - New `ModelProfile(BaseModel)`: `label`, `provider`, `model`, `base_url`,
     `api_key`, `temperature`, `max_tokens`, `context_window`,
     `price_input_per_mtok`, `price_output_per_mtok`, `timeout_seconds`.
   - `AppConfig` gains `model_profiles: dict[str, ModelProfile]` and
     `roles: RolesConfig` (`primary` / `worker` / `fast`, each `str | None`).
   - `resolve_profile(config, role) -> ModelProfile` helper.
   - Dynamic secret paths for `model_profiles.*.api_key`.
   - Validation: every role must reference an existing profile (or fall back to `llm`).
2. **`claudio-backend/api/config.py`**
   - `/config/llm/test` already takes an arbitrary `{base_url, model, api_key}` —
     it works per-profile as-is. Optionally add `?profile=name` sugar.
3. **Frontend `ConfigView.tsx`**
   - New **Models** tab: list/add/edit/delete profiles, a "Test" button per profile
     (reuses the existing test mutation), and three role selectors
     (primary / worker / fast).
   - The existing "llm" tab stays as the primary model config.

*Ships value on its own:* per-profile connectivity testing and a single place to see
all configured models.

### Phase 2 — The delegation tool

1. **`claudio-core/tools/subagent.py`** (new)
   - `delegate_task(ctx, task, agent)`:
     - look up `SubagentConfig[agent]`; unknown name → error result listing valid names;
     - resolve the worker profile;
     - build the sub-agent (ADK `LlmAgent` with its own in-memory session service —
       stateless per delegation, no SQLite file needed);
     - drive it to completion with the task as the only user message;
     - return `{"ok": true, "output": <final text>, "usage": {...}}`.
   - Guardrails: hard cap on sub-agent turns (from `SubagentConfig.max_turns`),
     timeout from the profile, output size cap.
2. **`claudio-core/tools/registry.py`**
   - Register `delegate_task` in `TOOL_META` / `TOOL_FUNCTIONS`.
   - `is_destructive=True` when the referenced subagent's tools include any
     destructive tool (computed at tool-build time, per the static-bool policy in
     `needs_approval_for`).
3. **`claudio-backend/engine/adk_engine.py`**
   - `build_subagent(profile: ModelProfile, sub: SubagentConfig, ctx) -> LlmAgent`
     — same tool-wrapping as `_build_agent`, worker instruction, worker model.
   - The tool needs engine access: pass a small `subagent_runner` callable into
     `ToolContext` (the context already carries config/db/workspace and a comment
     anticipating exactly this — `context.py:40`).
4. **Usage rollup (`runs/manager.py`)**
   - Sub-agent tokens are added to the run's totals; cost computed with the
     **worker profile's** prices, not the primary's.
5. **`instructions.py`** — render the subagent roster + delegation guidance (§2.3).

### Phase 3 — One-shot jobs to the fast profile

Each of these already calls `openai_chat_completion` with explicit args — swap the
source of those args from `config.llm` to `resolve_profile(config, "fast")`:

- Compaction summariser (the injected callable in `runs/manager.py` that feeds
  `claudio_core/compaction.py`),
- Session title generation,
- `autonomous/fixer.py` proposal calls.

Also: the compaction budget should use the fast profile's `context_window`, not the
global `agent.context_window`, since the summariser model may be smaller.

### Phase 4 — Optional enhancements (evaluate after using v1)

- **Auto-routing mode:** a `fast`-model classifier on the incoming message picks the
  run's model for simple requests end-to-end (no delegation). Only worth adding if
  real usage shows the primary model under-delegating.
- **Per-step model badges in chat:** the frontend shows which model produced each
  step (the events already carry enough info if we add `model` to `run_started` /
  tool events).
- **Approval passthrough for sub-agents:** if the one-gate-at-delegation model feels
  too coarse, surface nested approvals by running the sub-agent as a full Claudio
  run (own run_id) rather than a nested loop — reuses the existing approval flow.
- **Per-profile health checks:** profiles marked unavailable after failed tests;
  delegation falls back to the primary with a warning.

---

## 4. Key decisions and trade-offs

| Decision | Recommendation | Why |
|---|---|---|
| Routing mechanism | Model-driven delegation (v1), classifier later (v2) | Classifier misclassification is silent and compounds across turns; the primary model is already the best router |
| `llm` section | Keep as the implicit primary profile | Zero migration for existing configs; `roles`/`model_profiles` are purely additive |
| Worker approval | One gate at `delegate_task` when the worker has destructive tools | Nested ADK confirmation is fragile; the human approves the *work order*, not every keystroke |
| Worker context | Stateless per delegation; task brief must be self-contained | Avoids two sources of truth; keeps cheap models on short, focused prompts |
| Worker review | Primary reads back delegated output before building on it | Cheap models make confident mistakes; review is one `read_file` |
| Cost tracking | Per-profile pricing, rolled into run totals | Keeps budget enforcement (`max_budget_usd`) honest across models |
| Sub-agent streaming | v1: result only (shown as tool result) | Nested streaming into the existing event pipeline adds complexity for little UX gain |

**What this deliberately does not do:** mid-run model switching. An ADK run is bound
to one model; roles are per-agent, not per-turn. That's fine — the primary never
changes mid-conversation; workers are fresh per task.

---

## 5. Testing

- **Core (`claudio-core/tests`):**
  - profile resolution: roles → profiles → `llm` fallback; validation errors;
  - secret redaction/restore with dynamic `model_profiles.*.api_key` paths;
  - `delegate_task` with a fake sub-agent runner: unknown agent name, turn cap,
    timeout, output cap, usage rollup math.
- **Backend (`claudio-backend/tests`):**
  - `/config` round-trip with profiles; `/config/llm/test` per profile;
  - an e2e run where the primary delegates a file-write task and reviews it.
- **Manual acceptance:** configure a strong local model as primary and a small fast
  model as worker; ask for something like "write a Python script that renames all
  .txt files by date" and watch the plan stay with the primary while the writing
  goes to the worker.

---

## 6. Suggested build order

1. Phase 1 (profiles + roles + Models tab) — config plumbing, low risk, immediately useful.
2. Phase 2 (delegate tool) — the core of the feature.
3. Phase 3 (one-shot jobs → fast profile) — small, immediate cost savings.
4. Phase 4 — only after real usage tells you which of them you actually need.
