# dsh-superagent-prototype → Claudio: how it works, and can we reproduce it?

Analysis of <https://github.com/deepseek-ai/dsh-superagent-prototype> (MIT, DeepSeek "DSH"
team, 19.4k stars, 1 commit, cloned and read locally 2026-11-05) against Claudio's own
architecture.

Measurements taken from the clone, not from the README:

| | dsh-superagent | Claudio |
|---|---|---|
| Language | TypeScript (Node 22+) | Python 3.11/3.14 |
| `src` size | 111 files / **15,205** LOC | core+backend **15,699** LOC |
| Pure domain core | `src/core` = **2,088** LOC | `claudio-core` (domain + tools + events) |
| UI | `src/client` = 4,037 LOC React | `claudio-frontend` React SPA |
| Tests | 53 files / **12,981** LOC (46% of the repo) | 20 files / 3,986 LOC |
| Prompt text | **291 lines total** (`prompts/*.md`) | `claudio_core/instructions.py` + skills |

## TL;DR / Verdict

**dsh-superagent is not an agent. It is a control plane that sits on top of somebody else's
agent runtime, and Claudio already *is* that runtime.**

The prototype issues **no completion call anywhere in its source** (grepped for
`createMessage|complete(|streamText|generateContent|.chat(` → nothing; `@deepseek-ai/dsh-llm` is
imported only to *resolve* model config and message types). Every piece of intelligence — reading
a GitHub thread, deciding whether a comment is a request, writing the code — happens inside a
DeepSeek Harness (DSH) *Session*, which the prototype treats as an external service behind a
narrow `Executor` port of exactly three methods (`start`, `deliver`, `stop`). What the 15k
lines actually implement is everything *around* the agent loop: who may ask for work, whether
this message is work, which unit of work it belongs to, where it runs, who gets told, and what
needs a human.

That is precisely the layer Claudio is missing, and precisely the layer Claudio can host:
Claudio already has sessions, an agent loop (ADK), tools, approvals, per-session workspaces, a
cron scheduler, an autonomous daemon, a durable SQLite store, skills in the same SKILL.md
format, and a React panel. **You can reproduce it natively. No external super agent is
required.** The work is ~2–3k lines of new Python domain code plus a GitHub *Source* adapter
and a worker-launch adapter, mapped onto things that already exist in the backend.

Three things are genuinely hard and are where I'd expect the real cost: mid-turn message
delivery into a running worker, durable transactional side-effects (the outbox pattern), and
the discipline of the authorization model itself. Details in §5 and §6.

---

## 1. What dsh-superagent actually is

One sentence from its own `package.json`:

> A proactive agent for one GitHub repository, as a DSH (DeepSeek Harness) bundle: it listens
> to GitHub and optionally Feishu, evaluates requests under a Policy, turns authorized ones
> into Tickets, and executes each in its own DSH Session.

So the shape is: **GitHub/Feishu in → policy gate → LLM judgement → work item → isolated
agent session → pull request + reply out → web panel showing what waits for a human.**

It ships as a *bundle* (`cordis.patch.yml`, `package.json#dsh.bundle`), i.e. a plugin for the
DeepSeek Harness process. Its `peerDependencies` are 20+ `@deepseek-ai/dsh-*` packages:
`dsh-agent` (the loop), `dsh-session`, `dsh-session-persistence`, `dsh-tools`, `dsh-llm`,
`dsh-workspace`, `dsh-permission-presets`, `dsh-system-prompt`, `dsh-skill`,
`dsh-host-webserver`, `dsh-subprocess`, plus the client UI kit. **Not one of those is written
in this repo.** The repo's own claim to originality is the orchestration layer.

Its stated philosophy (`docs/000-philosophy.md`) is unusually crisp and drives every design
choice:

> **Take as little human attention as possible, and give as much value as possible.**

Operationalised as: act only when asked or when a written rule allows it; speak only when a
person must act or can use a result; ask one concrete question at a time; stop by impact, not
by effort; report honestly (a draft is marked unfinished, a limit is never bypassed to avoid
asking). And for the design itself: few concepts, few settings, and a panel that lists first
what waits for a person — "a healthy state takes no space."

### The pipeline

```
Source ──Trigger──▶ Thread ──batch──▶ Eval ──accept──▶ Ticket ──Assignment──▶ Executor
  ▲                    │  ▲             (gate +          (Mandate)              (own DSH
  │                    │  └── Subscription fan-out ──┐                          Session +
  └────── Channel ◀────┘                            │                          own dir)
                    ▲                               ▼
                Policy ──────────▶ Outbox ◀── Reports (ready / blocked / failed / waiting)
```

## 2. The architecture, layer by layer

Directory map of `src/` with LOC:

| Dir | LOC | Role |
|---|---|---|
| `core/` | 2,088 | **Pure domain.** Engine + Intake/Evals/Tickets/Assignments/Replies, model types, ports. No I/O, no DSH import. |
| `app/` | 5,292 | Composition root, SQLite store, durable effects, LLM assessor adapter, sessions, settings/prompts, HTTP, tools, view data. |
| `sources/` | 2,059 | GitHub + Feishu adapters (poll, trigger translation, thread-context readers, channels). |
| `executor/` | 838 | `dsh.ts` worker-session manager + `background.ts` job/subagent tracking. |
| `client/` | 4,037 | React management panel. |
| `infra/` | 347 | env, text, commands, paths, health. |
| (`src` root) | 544 | `index.ts` plugin entry, `deployment.ts`, `policy.ts`, `limits.ts`, `kv.ts`. |

### 2.1 The domain core (`src/core`, 2,088 lines — read this first)

`Engine` (`core/engine.ts`) is **the only writer** of Triggers, Threads, Decisions, Tickets and
Assignments. Two properties make the whole thing robust:

1. **Every Engine method is synchronous and runs in one repository transaction**
   (`this.repo.atomic(() => ...)`). State transitions are atomic by construction.
2. **Side effects are *enqueued* inside the transaction, performed after commit.** The Engine
   talks to the outside world only through six ports declared in `core/ports.ts`:

| Port | Methods | Realised by |
|---|---|---|
| `Repositories` | aggregate CRUD, `atomic` | `app/store.ts` (node:sqlite) |
| `Policy` | `version`, `paused`, `gate()`, `revalidate()`, `admitsInferred()`, `evalTimeoutMs` | `policy.ts` from saved Settings |
| `SourceScope` | `scope()` — does this Source listen to this event at all | `sources/registry.ts` |
| `Assessor` | `request(candidate)`, `release(evalId)` | `app/assessment.ts` → the main DSH Session |
| `Executor` | `start(ticket, assignment)`, `deliver(...)`, `stop(...)` | `executor/dsh.ts` |
| `Outbox` | `send`, `status`, `withdraw` | `app/outbox.ts` → Source channels |

This is a hexagonal architecture done properly, and it is the single most important fact for
our reproduction question. It is not a style claim: **`grep -rn "@deepseek-ai" src/core/` returns
nothing**, and `EngineOptions` (`src/core/part.ts:7`) lists exactly six injected ports —
`repo`, `policy`, `scope`, `assessor`, `executor`, `outbox` — plus three optional ones
(`prepareTicket`, `now`, `emit`). Swapping DSH for another runtime means re-implementing
`Assessor`, `Executor`, `Outbox` and `Repositories`, and touching zero lines of the state
machine.

### 2.2 The five domain parts

| Part | Responsibility |
|---|---|
| `Intake` | Store a Trigger once, record it on its Threads + correlations, fan it out to subscribed Tickets, else queue it for Eval on its primary Thread. |
| `Evals` | Batch triggers per Thread, apply the gate (strength/actor/basis), deadline + requeue, produce the Decision. |
| `Tickets` | Lifecycle: create-from-accept with Mandate/Subscriptions/first Assignment, followed Threads, Inputs, resume, retry, stop, end. |
| `Assignments` | What the Executor reports back: started, inputs delivered/failed, interrupted, worker Report + question. |
| `Replies` | Channel writes through the Outbox: acknowledgement, answer, result, attention message — each by the rule that permits it. |

`Engine.evaluateDue()` is the clock: requeues overdue Evals, starts queued ones, wakes tickets
with undelivered inputs. A 2-second `passes.ts` loop drives it plus due effects.

### 2.3 Sources — how work arrives (no webhooks)

A `SourceKind` is a plugin contract (`sources/kind.ts`): config schema + UI field specs +
policy part (who's allowed, in *that source's* identities) + channel adapter (`send` returning
`sent | gone`) + thread-context reader (normalised `ContextMessage { id, from, at, text, bot }`).

**GitHub** (`sources/github.ts`, 459 lines) **polls**; it does not receive webhooks. Config
`pollSeconds` 60–3600. It reads the Notifications API plus repository event streams
(`issues`, `pulls`, `comments`, `review-comments`) oldest-first with
`sort=updated&direction=asc&per_page=100&since=<cursor>`, keeping a per-stream cursor in the
Source's KV state. The cursor is stepped **5 s before** the newest item (`cursorAt`) and
overlapping re-reads are made harmless by **deterministic Trigger IDs** ("repeated messages are
no-ops"). The cursor is clamped to a **14-day horizon aligned to the hour**
(`POLL_HORIZON_MS = 14 * 24 * 3600_000`), which bounds replay after downtime. While the cursor
hasn't moved, the last page's `If-None-Match` ETag is replayed so an unchanged list returns 304
and "costs no rate limit". CI is polled on the default branch and emits a Trigger **only when the
set of failing checks changes** against an initial baseline. Consecutive failures only surface as
a health entry after `failuresBeforeError` — one network blip stays silent. The same 14-day
horizon also decides how long a subscribed Thread with no live Ticket keeps being polled at all.

PR binding (`github-pull.ts`) is neat: a PR belongs to a Ticket when the branch's last path
segment equals the Ticket's short name; an agent-authored PR from the configured repo (not a
fork) may be claimed; one PR binds to at most one open Ticket.

**Feishu** (`sources/feishu*.ts`, 756 lines across 6 files) is the same contract over chat:
live events plus history recovery plus polling of subscribed topics, bot identity, chat roster
sync into the Policy, image download, and a channel that replies in-thread.

### 2.4 Eval — two layers, and the LLM never holds authority

This is the authorization design (`core/evals.ts` + `app/assessment.ts` + `docs/002`):

- **Layer 1 — the gate (deterministic code).** `Policy.gate(trigger, thread, tickets)` returns
  admissible/not with a *strength* and a *basis*, from the versioned allow-list and the
  Source's admission table (`listedUnaddressed`, `unlistedAddressed`, `unlistedUnaddressed`).
  Cheap, auditable, no tokens. It also decides whether a bot or CI message can ever originate
  work (`admitsInferred`).
- **Layer 2 — the assessment (LLM).** Only if the gate admits. The `MasterAssessor` relays a
  bounded **candidate card** to the *main DSH Session*, which must answer with a strict Zod
  schema (`AssessmentSchema`): `accept | ignore | unavailable`, one `rule_assessment` per
  enabled rule, `direction`, `title`, kebab-case `worktree_name`, `scope`,
  `intent_evidence` (the quotes establishing three facts, ≤700 chars), a one-sentence
  `acknowledgement` in the source language, and `reason`.

Crucially: **the assessor receives facts, never authority** (`Candidate` = evalId, thread,
triggers, strength, policyVersion, ticket list). It cannot widen scope. While an Eval runs, the
Session may issue *bounded, read-only, cached* reads via `orchestrator_eval_read`
(`message | root | parent | thread | file | directory | image`, char-capped at
4000/1200/400/50). A timed-out Eval becomes `unavailable`, never silently accepted.

A **Mandate** is the receipt the accept mints: policy version, scope, strength, matched rules,
sender identity on that Source, delivery mode. Immutable; wider scope ⇒ a new Ticket; only a
person's stop withdraws it; a Policy that no longer holds it revokes it; it is revalidated
before an ended Ticket resumes.

### 2.5 Executor — one Session and one directory per Ticket

`executor/dsh.ts` (673 lines). Per Ticket: session id `worker-<ticketId>`, directory named
`<worktree_name>-<first 12 chars of ticket id>` under an execution root. Three permission
presets: `dsh-superagent-router` (main session, small tool set), `dsh-superagent-worker`
(commands run **without approval** as the operator's OS user; foreground shell moves to
background after 5 s so a worker can take a new message mid-job), and DSH's `standard` for
nested subagents (60 s background threshold).

The operational detail that makes this feel real:

- **Worker slots + concurrency.** A Ticket holds a slot from launch through an active turn. The
  configured limit has **one extra reserved slot** reserved for a person's message or status
  request — so new work can never starve a human trying to continue an existing Ticket.
- **Background accounting.** If a turn ends with shell jobs or subagents alive, the Ticket
  stays `working` and holds its slot; a finished job *wakes* the Executor with its result.
  After 30 min the jobs are killed and the Ticket reports `waiting`, naming what was killed.
  At handoff to a human the slot frees immediately; a review server may keep running until the
  Assignment ends.
- **Prepare command.** Before the first Assignment, one `sh` command (10 min cap, typically
  `git worktree add`) runs in the execution root. Failure fails the Ticket — and deliberately
  does **not** auto-retry, because a half-prepared directory counts as prepared.
- **Transient-error handling.** `transient()` matches ECONNRESET/429/5xx/"overloaded"/etc. and
  retries 3× with 15 s backoff, separate from real failures.
- **Execution variables** (`DSH_SUPERAGENT_DIR/_NAME/_TICKET/_BLOCKED_COMMANDS/
  _GITHUB_REPOSITORY/_ACCOUNT/_COMMIT_EMAIL/_NUMBER/_PR_HEAD/_FEISHU_BOT/_FEISHU_CHAT`), none
  containing credentials. The docs are honest about the limit: *"these wrappers and directories
  establish defaults, not a security boundary. A worker can still execute a program by absolute
  path."*
- **Merging is prompt-governed, not code.** Auto-merge lives in a "Delivery requirements"
  prompt; the default forbids the agent account from enabling it (GitHub credits whoever
  enabled it) and allows it only for the Ticket's own ready PR with approvals covering the head
  SHA and < 200 changed lines.

### 2.6 Ticket stage = what a human must do

One stage per open Ticket, first match wins: `person/delivery` (input undelivered, retried with
exponential backoff 2 s → 60 s) · `person/answer` (has an Attention) · `person/review` (latest
Report `ready`) · `person/blocked,failed` · `working/queued` · `idle` (Report `waiting`) ·
`working`. The React panel is ordered by exactly this. Worker Reports are a closed 6-valued enum
(`core/model.ts:216` `ReportState`) — `progress | waiting | ready | blocked | failed | dropped`
— which is what keeps the orchestrator quiet: nothing free-text reaches the router to be
misinterpreted, and the docs explicitly *reject* "inferring 'waiting on a person' from a
report's free text".

### 2.7 Roles, and 291 lines of prompts

`prompts/`: `orchestrator.md`, `worker.md`, `admission.md`, `dispatch.md`, `handoff*.md`,
`replies.md`, `subscriptions.md`, `work.md`, `cli.md`, `shared.md`, `team/`. Two roles only:
a **main/router Session** (routes triggers with `orchestrator_route` — `accept | attach | stop |
ignore`; assesses candidates; writes replies; edits settings & prompts) and a **worker Session**
per Ticket. Per-repo behaviour is *prompt text*, never a setting. The entire `SettingsSchema`
(`src/policy.ts`) is: `paused`, `sources{}`, `rules[]`, `mode: off|observe|auto`,
`timeoutSeconds`, `maxWorkers` (default 3), `directory`, `prepare`, `blockedCommands[]`. That is
the whole configuration surface. `mode: observe` is the philosophy made concrete — proactive work
is *recorded but not acted on* until the operator trusts what the agent would have done.

---

## 3. The genuinely interesting parts (what is worth stealing)

Ranked by how much they'd improve Claudio, not by cleverness.

1. **Authority is data, not a code path.** The `Mandate` is a minted, immutable, versioned
   object: *which* policy version, *which* rules matched, *what* scope, *how* the sender was
   identified. A running worker gets told when the rules change under it ("delivered, does not
   revoke") instead of being cancelled mid-diff and leaving half-finished work. Nobody in the
   agent-codebase I know models this.
2. **Gate before model.** Deterministic admission (allow-list × addressed-or-not ×
   bot-or-human, per Source, in a 3-cell table) rejects 95% of noise for free. The LLM only ever
   judges things that already passed a code check, and only gets *facts*.
3. **Subscriptions as the routing primitive.** A Ticket subscribes to Threads (the origin
   issue, its own PR). Later comments reach the worker **without a re-mention and without an
   Eval** — "later discussion reaches the Executor without anyone mentioning the agent again."
   Correlation *adds context, never routes*. This is why it doesn't feel like talking to a
   wall.
4. **Attention as the organizing axis of the UI.** Stage is derived, not stored
   (`person/answer` beats `person/review` beats `blocked` beats `queued` beats `idle`), so the
   panel is a pure function of state and a healthy system renders almost nothing.
5. **The reserved slot for humans.** One extra worker slot that only a person's message can
   take. Cheap idea, huge difference in perceived reliability.
6. **Durable effects + transactional outbox.** `Effects.enqueue` inside the Engine's
   transaction, performed after commit, with per-message receipts
   (`pending | sent | dropped{channelGone|channelIgnored}`) and idempotent send-by-id. Replies
   cannot be double-posted by a restart, and cannot be lost between "decided" and "sent".
7. **Prompt-as-config, and `SETUP.md` as an agent-driven installer.** Setup is not a script; it
   is a document an agent follows with rules ("ask one question per message", "under 300
   words", "never ask for a secret in chat", "don't execute while asking", "one plan message,
   then go").
8. **A rejected-concepts table in the domain doc** (`docs/001`). **Twelve** designs considered
   and refused with reasons — no `Principal` entity, no cross-Source identity mapping, no Eval
   scheduling delayed triggers, no "append to Ticket" Decision, no Ticket state mirroring the
   Session, no cancelling on policy change, no locking an Executor's tools when it fails, no
   inferring "waiting on a person" from free text. Same doc has a **"What is reusable"** section
   that draws the line for us: *the concepts and their rules are reusable; the Source
   implementations, the Policy contents, the Executor and the Channel implementations belong to a
   deployment.* That sentence is the licence for the port described in §6.

---

## 4. Claudio today: the capability map

Verified by reading `claudio-core/src/claudio_core/` and
`claudio-backend/src/claudio_backend/` (15,699 LOC, 77 HTTP endpoints).

| dsh-superagent needs | Claudio already has | Where |
|---|---|---|
| An agent loop + tool calling | **Yes** — Google ADK behind a framework-neutral core | `engine/adk_engine.py`, `core/engine.py` |
| Sessions, persisted, resumable | **Yes** — create/list/patch/delete/compact, message history, session DB | `api/sessions.py`, `core/session_store.py` |
| Run lifecycle (start/pause/resume/cancel/recover) | **Yes** — `RunManager`, per-run `asyncio.Task`, `_run_with_recovery` with backoff + replan | `runs/manager.py` |
| Concurrent runs | **Yes** — many runs at once; one active run *per session* (`get_active_run_for_session`) | `runs/manager.py:298` |
| Isolated working directory per job | **Yes** — workspace is a *per-session* property with `workspace_allowed_roots`; `/workspaces` add/list/remove | `core/settings.py:156`, `api/workspaces.py` |
| Permission presets (router vs worker) | **Yes, roughly** — `ToolPolicy` per run from `approval_mode`, plus allowlist + guardrails; approval mode can be `off` (headless) | `runs/manager.py:_tool_policy`, `core/safety/` |
| Durable store with transactions | **Yes** — SQLite via `claudio_core/db.py` | `core/db.py` |
| Structured streaming to a UI | **Yes** — 13 frozen SSE events incl. `HANDOFF`, `APPROVAL_REQUIRED`, `CONTEXT_USAGE`, `HEARTBEAT`, with `after_seq` catch-up (`subscribe`, `open_stream`) | `core/events.py`, `runs/manager.py:344` |
| Scheduled / autonomous work | **Yes** — `TaskScheduler` (croniter, `wake()`, missed fires skipped) + `TaskRunner` (**agent tasks and shell-script tasks**), built-in maintenance + consolidation | `autonomous/task_scheduler.py`, `task_runner.py` |
| A "record only, don't act" mode | **Yes** — `report_only` (global and per-task, `effective_report_only`) — direct analogue of `mode: observe` | `task_runner.py:96` |
| Self-healing / health / recovery | **Yes** — `health.py`, `recovery.py`, `fixer.py`, issues with acknowledge/resolve/propose-fix | `autonomous/` |
| Outbound notifications | **Partial** — email (SMTP) + generic webhook POST, best-effort, never raises | `autonomous/notifier.py` |
| Skills | **Yes** — same `SKILL.md` convention, loader + backup + drafts with approve/reject | `core/skill_loader.py`, `api/learning.py` |
| Learning loop | **Yes** — `learning.py`, `extract.py`, consolidation | `core/learning.py` |
| Budget / cost control | **Yes** — per-run budget + spend tracking (`BudgetExceeded(spent, budget)`), metrics | `runs/manager.py:145`, `core/metrics.py` |
| Management panel | **Yes, as a shell** — React SPA over `/api/v1` + SSE | `claudio-frontend/` |
| Shell / file / web tools so a worker can run `git` + `gh` | **Yes** — `tools/shell.py`, `files.py`, `web.py`, `planning.py`, `tasks.py` | `core/tools/` |

**What Claudio does not have at all:** any *inbound* event source (no GitHub polling, no
webhook receiver, no chat integration — the notifier is send-only); any domain vocabulary
beyond Session/Run/Task (nothing like Trigger/Thread/Ticket/Mandate/Decision/Subscription); a
policy gate or an authorization receipt; an idempotent reply outbox; a mid-run message inbox;
worker-slot scheduling; and any attention-first view.

---

## 5. Can Claudio reproduce it? (and do we need an external super agent?)

### The direct answer

**Reproduce natively: yes.** **Need an external super agent: no — and it would be actively
harmful.**

The reasoning is structural. dsh-superagent's `Executor` port is three methods and one
`WorkerReport` enum. It does not need an agent *framework* with any particular power; it needs
something that can (a) run a model turn with tools in a directory, (b) accept a message later,
(c) report back. DSH provides that. **Claudio provides exactly that**, plus the two things DSH
does not give a DeepSeek user for free: per-run budgets and an approval gate you control.

Running the real thing next to Claudio would put a second agent runtime, a second session
store, a second permission system and a second cost ledger beside ours, and then bridge them
over HTTP. You would lose the one thing Claudio's whole design protects — a single component
that owns run state and guarantees running work stays consistent (README, "one long-lived
agent, two independent client surfaces").

The honest alternatives are therefore:

| Option | Verdict |
|---|---|
| **A. Port the control plane into Claudio** (§6) | ✅ Recommended. 80% of it is pure domain code with no framework dependency. |
| B. Run DSH + dsh-superagent as an *external* super agent, delegating tasks to Claudio | ❌ Two runtimes, duplicated state, no shared approvals/budget/events. Only defensible if we could never write the control plane. |
| C. Use a hosted agent (GitHub Copilot agent / Codex / Claude Code GitHub Action) as the super agent | ⚠️ Legitimate shortcut for the *executor* role only, but you surrender the policy gate, the attention model and cost visibility — i.e. you keep the noise and lose the interesting parts. |
| D. Do nothing, keep using scheduled Claudio tasks | ⚠️ Fine today: `TaskRunner` + `report_only` already gives "cron-triggered agent with observe mode". It lacks everything event-driven and conversational. |

### The four things that will actually cost us

Honest difficulty, not a cheerleading list.

1. **Mid-turn input delivery — MEDIUM-HARD, and the most important one.** dsh workers take a
   new message *at their next step* because foreground shell commands move to background after
   5 s and the host has an inbox that batches relay messages into the running turn. Claudio's
   `RunManager` delivers approvals (`_handle_approval`) but has no "inject this message into a
   run that is currently looping" path — `resume_run` is for `awaiting_approval`. Design fix:
   an **inbox queue per run**, drained at each step boundary in `_consume`, plus a `RUN_INPUT`
   event. Without this, every follow-up comment has to wait for the worker to finish, and the
   "keep talking to it" feel disappears.
2. **Crash semantics — MEDIUM.** Verified: `db.py:656 fail_interrupted_runs()` marks every
   running run `failed / backend_restarted` at boot. dsh instead persists effects and *resumes
   interrupted Assignments with a recovery note*. Our Ticket state must live in tables that
   outlive the run, and boot must reconcile ("Ticket #7 was `working`, its run is gone → requeue
   with recovery note"), not just mark things failed. `resume_state_json` (added by migration in
   `db.py:272`) is the hook to build on.
3. **Transactional outbox — MEDIUM, but small and well-specified.** We need
   "decide + enqueue reply" in one SQLite transaction and delivery after commit with
   idempotency keys. `claudio_core/db.py` gives us the transaction; the pattern is ~150 lines.
4. **The GitHub Source — EASY code, FIDDLY operations.** Polling with cursor + `If-None-Match`
   + horizon clamp is ~200 lines with `httpx`. The real work is operational: a **dedicated
   GitHub account** (everything the agent writes appears under that name — the prototype refuses
   to recommend the operator's own), `gh` auth for it, repo push rights, and a rate-limit
   posture. Budget a day of hands-on-keys for this and none of it is code.

Everything else — the domain model (2,088 LOC in TS, likely ~1,500 in Python), the gate, the
stage derivation, the panel view — is ordinary work.

### What we should *not* copy

- **Worker sessions running shell with approval off as the operator's OS user.** The prototype
  says the quiet part out loud: "these wrappers and directories establish defaults, not a
  security boundary. A worker can still execute a program by absolute path." Claudio has an
  approval gate and an allowlist; we should keep approvals *on* for destructive actions and get
  isolation from `git worktree` + a per-ticket workspace root + `blockedCommands`, or run workers
  in a container (`compose.yaml` already exists here).
- **A 15k-LOC TypeScript rewrite.** Port the ~2k-line core and the two adapters; skip Feishu
  entirely unless we actually use it.

---

## 6. Build plan (if we do it)

Landing zone that respects Claudio's three layout rules (event protocol is the contract; core
knows nothing about ADK; exactly one package touches ADK — all enforced by import-linter).
So: domain in `claudio-core`, adapters and scheduler in `claudio-backend`.

**Phase 0 — decide the boundary (½ day).** Copy `dsh-superagent/src/core/ports.ts` and
`docs/001-domain.md` + its rejected-concepts table into `doc/`. Name the five aggregates in
Python. Write no code until the Ticket↔Run mapping is on paper: *a Ticket owns 1..n Runs; a Run
may exist without a Ticket (all interactive chat does).*

**Phase 1 — the domain core (2–3 days, no LLM, fully unit-testable).**
`claudio_core/tickets/`: `model.py` (Trigger, Thread, Decision, Mandate, Ticket, Assignment,
Report), `ports.py` (the six Protocols), `engine.py` (`atomic()` + the five parts),
`policy.py` (`gate()` over an allow-list table + `mode: off|observe|auto`). Tables in
`claudio_core/db.py`. Test with a fake Executor/Assessor exactly as their 13k lines of vitest
do. **Ship `mode="observe"` first** — record what would have happened, act on nothing. Note the
ratio we are copying: their tests (12,981 lines) are 6× their domain core. The state machine is
the part that must be provably right, because a wrong transition is a PR posted in your name.

**Phase 2 — the GitHub Source (1–2 days).** `claudio_backend/sources/github.py`: notifications +
repo-event polling, cursor and ETag in KV, `ContextMessage` reader, `gh`-based channel
(comment / reply / PR). Register it behind `/api/v1/sources` so a second source (webhook, email,
Slack) is just another adapter. Health via the existing `autonomous/health.py` entries, with
`failures_before_error` so one blip stays quiet.

**Phase 3 — the assessor (1–2 days).** Gate in Python; assessment as a *structured-output* call
using the existing engine + a Pydantic model mirroring `AssessmentSchema` (`decision`,
`rule_assessments`, `intent_evidence`, `title`, `worktree_name`, `acknowledgement`, `reason`),
with bounded read-only context. Cheaper and more testable than giving an agent tools: reuse
`TaskRunner._coerce_report`'s tolerant-JSON discipline and add `unavailable` on timeout.

**Phase 4 — the executor (2–3 days, the hard one).** Per Ticket: a session + a workspace
(`api/workspaces.py` already creates these) + a prepare command (`git worktree add`), a ToolPolicy
derived from a new `worker` approval mode, and `max_workers` slots **with one reserved for a
person's message**. Implement the run **inbox** here (gap #1) and boot reconciliation (gap #2).
Worker reports become a closed 6-valued `Report` (`progress | waiting | ready | blocked |
failed | dropped`) returned as structured output, never prose — the closed enum is what makes the
router's silence provable.

**Phase 5 — replies + panel (2 days).** Outbox with receipts and idempotent send-by-id; extend
`notifier.py` from "one broadcast webhook" to "addressed, threaded, idempotent". New frontend
view sorted by the derived stage, with `person/*` on top and an empty healthy state.

**Phase 6 — prompts.** Write `orchestrator` / `worker` / `admission` / `replies` /
`handoff` as files, not string constants, and steal the two `skills/dsh-superagent-*/SKILL.md`
as the shape for a `github` skill (our loader already reads the same format).

Rough total: **9–13 focused days** to a working GitHub-only version in observe mode, plus the
same again before it is trustworthy enough to open up.

---

## 7. Method, verification, and what I did not check

**How this was produced.** The prototype was shallow-cloned to `/tmp/dshsa` and read directly;
Claudio's claims were read from this workspace. Everything in §1–§4 is grounded in a file I
opened. Line counts came from `wc`, not from prose.

Verified directly (file: `dsh-superagent/…`): the six ports and their signatures
(`src/core/ports.ts`); synchronous, transactional Engine and `evaluateDue`
(`src/core/engine.ts`); SQLite aggregates with generated columns for active sets
(`src/app/store.ts`); Zod gate/assessment schemas, `mode: off|observe|auto`, `maxWorkers` default
3 (`src/policy.ts`, `src/app/assessment.ts`); GitHub **polling** with cursor + `If-None-Match` +
horizon clamp and `pollSeconds` 60–3600 (`src/sources/github.ts`, `src/sources/poll.ts`);
`worker-<ticketId>` sessions, prepare timeout, transient-error matcher, background limits
(`src/executor/dsh.ts`); stage table and worker slots (`docs/003-execution.md`); domain
concepts, Mandate rules, the 12-row rejected-concepts table and the "What is reusable" split
(`docs/001-domain.md`); `POLL_HORIZON_MS = 14 days` and `cursorAt` = −5 s
(`src/sources/poll.ts`); `ReportState` = 6 values (`src/core/model.ts:216`); Feishu = 756 LOC /
6 files; **23 peer dependencies, all `@deepseek-ai/*`, 21 of them `dsh-*`** (`package.json`).
On our side: 77 endpoints, run/session/workspace/task/autonomous routes, `_tool_policy`,
`BudgetExceeded`, **exactly 13 `EventName` members**, `Notifier` transports,
`fail_interrupted_runs()`, `resume_state_json`, per-session workspaces, Python 3.11 (backend) /
3.14 (core) venvs. Gap #1 was checked by grep, not inference: searching
`claudio-{core,backend}/src` for `inbox|inject_message|steer|queue_input|pending_message`
returns **nothing**, confirming there is no mid-run message-delivery path today.

**Inferred, not proven:** that the main Session and worker Sessions are ordinary DSH agent loops
with no privileged hook (they are reached via `agents`/`sessionController` injections and tool
calls, which strongly implies it); the Python LOC estimate for a port (analogy from
2,088 TS LOC, same architecture, less type gymnastics); the day estimates in §6.

**Not examined:** `src/app/migration.ts` (616 lines of schema migration), the Feishu image and
channel internals, most of `src/client` (only sized it), and the 12,981 lines of tests beyond
their existence. The README also warns the prototype "is not identical to our internal version
or the implementation described in *Exploring Super-agent Design: Towards a More Autonomous
Agent System*", and that some designs in that article are **not implemented here** — so treat
the article as aspirational and this repo as the ground truth. I did not read the article.

**One caveat about the repo itself:** 1 commit, 2.2k forks in a short window, self-described as
"grew out of our team's day-to-day development work … engineering practices may be
inconsistent". Its *ideas* are mature; its code is a publication of internal code. Port the
domain model and the docs, not the plumbing.

## Bottom line

Claudio is missing a **work-intake and authorization plane**, not an agent brain. The prototype
proves that plane is small (2,088 lines of pure domain), framework-independent (six ports), and
worth more than the model behind it. Build it in `claudio-core`, drive it from a GitHub Source
in the backend, run each Ticket as a Claudio Run in its own workspace, and keep our approvals
and budgets where the prototype waved them away. No external super agent.
