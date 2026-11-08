# Model profiles, roles, and routing

Claudio's multi-model support is opt-in. The `llm` section is the implicit
primary profile. If a role is unset, or points to a profile that does not
exist, that role resolves to `llm`. Automatic routing is also off by default.
Consequently, a configuration with only `llm` will use that same model for
primary runs, delegated workers, and utility calls. This is expected, not a
failure to discover other models.

## Roles and intended workloads

Roles describe how Claudio uses a configured profile; they do not enforce a
model's quality or capability. Choose models by testing them against the
workload described here.

| Role | Expected characteristics | Workloads |
|---|---|---|
| **Primary** | Strong reasoning and instruction following; reliable structured tool calls; enough context and output capacity for a full conversation and multi-step work. It should be able to plan, judge ambiguity, and review other models' work. | Owns the user conversation. Plans and performs multi-step tasks, coding across files, debugging, decisions, tool use, and review of delegated results. This is the default for any request not explicitly routed elsewhere. |
| **Worker** | Cost-efficient but still dependable at following a bounded brief, using its allowed tools, and returning a checkable result. It needs enough context for the task, not necessarily the primary's full context window. | A short, self-contained task delegated by the primary, such as implementing one narrow change, writing one script, or retrieving and summarizing specific information. It does not own the conversation or choose the overall plan. |
| **Fast** | Low latency and/or low cost, concise, and dependable on simple transformations, summaries, and classification. It should not be relied on for difficult reasoning or tool-driven work. | One-shot utility calls (compaction summaries, learning extraction and consolidation, and fixer proposals); optionally, simple user requests when automatic routing is enabled. Auto-routed fast agent runs have no tools or skills. |

These are selection targets, not model-family guarantees. A profile's label is
for display only; choosing a "fast" label does not make it faster, and choosing
a larger model does not guarantee better tool use. Benchmark candidate profiles
on representative prompts, tool-call correctness, latency, context needs, and
cost before assigning them.

## Routing rules

### 1. Profile resolution

Each named profile supplies a model endpoint and its settings, including
`model`, `base_url`, `api_key`, `temperature`, token limits, context window,
timeout, and token prices. `roles` maps `primary`, `worker`, and `fast` to
profile names. The existing `llm` section remains the implicit profile and
backward-compatible fallback for every role. A misspelled role reference also
falls back to `llm`; the config API reports it as a warning.

### 2. A normal user run

Every new top-level run starts as `primary`. With
`agent.auto_routing_enabled: false` (the default), it stays there.

When auto-routing is enabled, Claudio first asks the **fast** profile to
classify the incoming message as `FAST` or `PRIMARY`. This is a conservative
filter, not a planner:

- The classifier selects `FAST` only for a simple, low-risk, one-step answer
  that needs no tools.
- Coding, commands, tool use, multi-step work, ambiguity, or a request needing
  planning/judgment should be `PRIMARY`.
- Claudio routes to the fast role only when the classifier's first output word
  is `FAST`; any other output routes to primary.
- If the fast profile is the same model at the same endpoint as primary, is
  known to be unavailable, or the classifier call fails, the request uses
  primary. Classifier failures are logged.
- A fast-routed run gets no tools or skills. It cannot delegate work.

Thus, enabling auto-routing does **not** make the fast model the default; it
only moves clearly simple, tool-free requests away from primary. It never
automatically assigns a user request to the worker role.

### 3. Delegated work

The primary model may call `delegate_task` for a configured subagent. That
explicit delegation is what selects the **worker** role. The child receives
the subagent's instructions, tool allowlist, and turn limit, and returns its
result to primary for review. It is a fresh run, not the primary conversation;
the task brief should include any context the worker needs. If the worker
profile is known to be unavailable, Claudio uses primary instead. If a worker
run fails with an availability or authentication error before any tool has
run, Claudio retries that task on primary; it does not switch models after
tool side effects have started.

If no worker role is configured, delegation still works but the child also
resolves to `llm`. A separate worker model is used only when both the profile
and role mapping are configured and the primary actually delegates.

### 4. Internal one-shot calls

Compaction, learning extraction/consolidation, and autonomous fixer proposals
resolve the fast role directly; they are not delegated worker runs. If no fast
profile is assigned, these calls also use `llm`.

### 5. Error fallback is separate from role routing

`llm.fallback_model` is an alternate model name for one fallback attempt after
the normal retry sequence. It is not the worker or fast role, and does not
route work to those profiles: Claudio keeps the selected role's
endpoint/settings and substitutes the fallback model name for that attempt.
Leave it unset unless that alternate model is available at the same endpoint.

## Configuration example

This is a partial example; retain the rest of the application's configuration
and provide valid endpoint details and credentials for the models you use.

```yaml
llm:
  model: primary-model
  base_url: http://primary-endpoint/v1
  api_key: local

model_profiles:
  worker-small:
    label: "Worker"
    model: worker-model
    base_url: http://worker-endpoint/v1
    api_key: local
    context_window: 32768
  utility-fast:
    label: "Fast utilities"
    model: fast-model
    base_url: http://fast-endpoint/v1
    api_key: local
    context_window: 16384

roles:
  # Omit primary to use the llm section.
  worker: worker-small
  fast: utility-fast

agent:
  auto_routing_enabled: true
```

In the UI, configure named profiles and role assignments in the LLM settings.
Automatic user-message routing is controlled by
`agent.auto_routing_enabled` in the backend configuration; it defaults to
`false`. Start with it disabled if predictable primary-model behavior matters,
then enable it only after confirming the fast model handles the intended
tool-free requests well.

## Verifying a route

For a live run, inspect the `run_started` event: `model` is the selected model
name and `model_role` is `primary`, `fast`, or `worker`. Delegated worker runs
have their own run start. A run that reports `primary` is expected when
auto-routing is disabled, when the classifier chooses primary, or when the
fast profile is unavailable. Also check the effective configuration for
unmapped roles: an unset role means `llm`, not "pick a model automatically".

Implementation references: [`resolve_profile`](../../claudio-core/src/claudio_core/settings.py),
the routing and delegation logic in
[`runs/manager.py`](../../claudio-backend/src/claudio_backend/runs/manager.py),
and fast-run tool restrictions in
[`adk_engine.py`](../../claudio-backend/src/claudio_backend/engine/adk_engine.py).
