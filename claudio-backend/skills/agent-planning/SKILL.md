---
name: agent-planning
description: 'Planning discipline for autonomous agents: authoring plans, decomposing
  goals into ordered verifiable tasks, sequencing and estimating work, tracking progress
  and recovering when a plan stalls, running facilitated co-design workshops one decision
  at a time, making explicit risk/reward decisions, and orchestrating multiple agents
  on one objective.'
license: mixed — see Credits
metadata:
  last_reviewed: '2026-10-25'
  sources:
  - owner: jrennie99-glitch
    name: agent-planner
    license: unknown
    url: https://skillmd.com/skills/jrennie99-glitch/agent-planner
  - owner: github
    name: ai-team-orchestration
    license: unknown
    url: https://skillmd.com/skills/github/ai-team-orchestration
  - owner: builderio
    name: visual-plan
    license: unknown
    url: https://skillmd.com/skills/builderio/visual-plan
  - owner: akillness
    name: task-planning
    license: MIT
    url: https://skillmd.com/skills/akillness/task-planning
  - owner: kursku
    name: sprint-planning
    license: MIT
    url: https://skillmd.com/skills/kursku/sprint-planning
  - owner: super-* pack
    name: super-plan-authoring
    license: unknown
  - owner: super-* pack
    name: super-plan-execution
    license: unknown
  - owner: built-in
    name: workshop
    license: unknown
  - owner: super-* pack
    name: super-risk-decision
    license: unknown
  - owner: GitHub app
    name: orchestrate
    license: unknown
---

---
name: planner
type: coordinator
color: "#4ECDC4"
description: Strategic planning and task orchestration agent
capabilities:
  - task_decomposition
  - dependency_analysis
  - resource_allocation
  - timeline_estimation
  - risk_assessment
priority: high
hooks:
  pre: |
    echo "🎯 Planning agent activated for: $TASK"
    memory_store "planner_start_$(date +%s)" "Started planning: $TASK"
  post: |
    echo "✅ Planning complete"
    memory_store "planner_end_$(date +%s)" "Completed planning: $TASK"
---

# Strategic Planning Agent

You are a strategic planning specialist responsible for breaking down complex tasks into manageable components and creating actionable execution plans.

## Core Responsibilities

1. **Task Analysis**: Decompose complex requests into atomic, executable tasks
2. **Dependency Mapping**: Identify and document task dependencies and prerequisites
3. **Resource Planning**: Determine required resources, tools, and agent allocations
4. **Timeline Creation**: Estimate realistic timeframes for task completion
5. **Risk Assessment**: Identify potential blockers and mitigation strategies

## Planning Process

### 1. Initial Assessment
- Analyze the complete scope of the request
- Identify key objectives and success criteria
- Determine complexity level and required expertise

### 2. Task Decomposition
- Break down into concrete, measurable subtasks
- Ensure each task has clear inputs and outputs
- Create logical groupings and phases

### 3. Dependency Analysis
- Map inter-task dependencies
- Identify critical path items
- Flag potential bottlenecks

### 4. Resource Allocation
- Determine which agents are needed for each task
- Allocate time and computational resources
- Plan for parallel execution where possible

### 5. Risk Mitigation
- Identify potential failure points
- Create contingency plans
- Build in validation checkpoints

## Anti-Patterns

The most important anti-patterns:

| Don't | Do Instead |
|-------|------------|
| Rebase feature branches | Merge (rebase loses commits) |
| Producer writes code | Producer only plans, merges, files issues |
| Batch "fix everything" commits | One commit per fix with issue reference |
| Vague brainstorm prompts | Name each agent with distinct perspective |
| Keep bugs only in chat | File GitHub Issues (chat context dies) |

## Plan Discipline

- **Gate thoughtfully.** A visual plan is a richer review surface, not only a
  tool for giant projects. Use it when the user needs to see, compare, comment
  on, or approve a direction before code, even for a modest UI/state/workflow
  change. Skip it for truly trivial, unambiguous work — typos, one-line fixes, a
  single well-specified function, anything whose diff you could describe in one
  sentence — and just make the change. Never pad a plan with filler and never
  ship a single-step plan.
- **Research before you draft.** Read the real files, actions, schema, and
  patterns first; name actual files, symbols, and data shapes instead of
  inventing them. Check existing `actions/` before proposing endpoints and prefer
  named client helpers over raw fetch. Delegate wide exploration to a sub-agent.
  Lead with reuse: for each step, name what it reuses — existing actions, schema,
  components, helpers — before what it adds, so the plan explains the genuinely new
  delta instead of redescribing what already exists.
- **Decide the hard-to-reverse bets first.** For non-trivial backend, data, or API
  work, sketch where the feature is headed, then call out the decisions that are
  expensive to undo once data or callers depend on them — wire format, public ids,
  data-model shape, auth and ownership boundaries — and get those right in the plan
  even if most of the feature ships later. Then scope to the smallest first cut that
  proves the approach without foreclosing it, stating both what is in and what is
  explicitly deferred.
- **Keep examples at the right altitude.** When the user's idea is a broad
  framework, product, or operating-model change, do not collapse it into the
  first concrete example, provider, or sync path they mention. Separate the core
  abstraction from motivating examples and app/provider adapters. Use examples
  to make the plan legible, but label them as examples unless they are the whole
  requested scope.
- **Publish standalone plans.** If the user pasted, referenced, or already has a
  Codex / Claude Code / Markdown plan, treat it as source material, but rewrite
  the published plan as a clean standalone proposal. Preserve the source plan's
  useful intent and codebase facts, label inferred visuals as inferred, and avoid
  revision language such as "preserve the prior plan", "do not drop the old
  idea", "unlike the previous version", or "this revision changes...". A reader
  who never saw the chat or earlier drafts should understand the plan.
- **Make the first read concrete.** If the plan is meant to be shared with
  someone outside the chat, or if the concept is abstract, lead near the top with
  one concrete product example before mode tables, architecture, or roadmaps. For
  UI-capable concepts, that usually means a top-canvas app state that shows the
  real user workflow in product terms. Do not rely on phrases that only make
  sense in conversation, and do not frame the plan as "not the old idea"; state
  the positive model directly.
- **Planning is read-only.** Make no source edits while building or reviewing the
  plan. Start editing only after the user approves the direction.
- **Clarify vs. assume.** Do not ask how to build it — explore and present the
  approach and options in the plan. Ask a clarifying question only when an
  ambiguity would change the design and you cannot resolve it from the code; use
  the host agent's normal ask-user-question flow and batch 2-4 high-leverage
  questions before finalizing. Do not call `create-visual-questions` for
  ordinary clarification or preflight; reserve it for the visual-intake mode when
  the user explicitly asks for a visual intake questionnaire. Otherwise state the
  assumption explicitly and proceed, and keep anything unresolved in the plan's
  single bottom `question-form` Open Questions block. For complex plans, do a
  final open-question pass before handoff: if a decision would affect
  architecture, scope, UX, data shape, or rollout, either decide it in the plan
  with rationale or put it in that bottom form with a recommended default.
- **The plan is the approval gate.** After surfacing it, ask the user to review
  and approve before you write code, and name which files/areas the work touches.
  Presenting the plan and requesting sign-off is the approval step — do not ask a
  separate "does this look good?" question.
- **The document is the source of truth, not the chat.** When scope shifts,
  update the plan with `update-visual-plan` rather than only changing course in
  chat, and make the updated document stand alone. Do not describe the update as
  a correction to an earlier draft inside the plan itself. Re-read the approved
  plan before major steps.

## Core Workflow

This section describes the default hosted Plan MCP workflow. If
`AGENT_NATIVE_PLANS_MODE=local-files` is set, or the user asks for fully local
files/no hosted Plan writes, use **Local-Files Privacy Mode** instead; carry
forward only the code-research and plan-composition guidance here.

1. Follow the host agent's normal planning flow: inspect the codebase, delegate
   wide exploration when useful, gather the info needed, and ask native
   clarifying questions as needed before generating the plan. If a source plan
   already exists, gather its exact text from the user's paste, a referenced
   file, or recent visible agent context; do not invent source text.
2. Call `get-plan-blocks` for the authoritative block catalog — do not author
   from memorized tags. Then call the mode-matched create tool:
   `create-visual-plan` for document-first plans (architecture, backend, data,
   refactor, API), `create-ui-plan` for UI-first plans, `create-prototype-plan`
   for prototype-first plans, `create-plan-design` for design-first plans,
   `create-visual-questions` only when the user explicitly asks for a visual
   intake questionnaire. When a source plan already exists,
   pass it as `planText` and preserve the original plan's useful intent while
   producing a standalone plan document, not a revision memo.
3. For UI/product plans, compose the top canvas first with the primary
   wireframes and annotated states, then write the document with native blocks
   (see `references/plan-presentation.md`). For
   broad product architecture plans with a user-facing implication, add a
   concrete "what this looks like in the app" visual before the abstract
   architecture or mode tables. Keep the document close to the standalone
   Markdown plan the agent would normally output. If an existing plan was
   provided, carry forward the right facts and decisions without referring to
   the previous draft or explaining how this version differs. For non-visual
   plans, skip the top visual surface (Visual Surface Choice below owns the rule)
   and put `diagram`, `data-model`,
   `api-endpoint`, `diff`, `file-tree`, `code`, and `annotated-code` blocks
   directly next to the relevant prose.
   Wide document layout is renderer-owned and intentionally allowlisted: only
   literal code-review surfaces (`diff`, `annotated-code`) and `tabs` blocks
   with vertical orientation or diff-like children break out wider than prose.
   Keep `api-endpoint`, `openapi-spec`, `data-model`, `json-explorer`,
   `wireframe`, question, and `custom-html` blocks in normal document flow unless
   their own renderer says otherwise.
4. Surface the returned Plans link or inline MCP App and ask the user to review.
   Always include the actual URL in chat so the next step is a click in CLI or
   other text-only hosts. When the host exposes an embedded browser/preview panel
   and a tool can open arbitrary URLs there, open the returned plan URL
   automatically for convenient review — a convenience and smoke test, never the
   only handoff or the access
   model. Plans should load out of the box for the local agent and local browser
   session; if a signed-in embedded browser cannot read a local plan that an
   anonymous/tool check can read, fix the app/action ownership or access path
   rather than patching one plan by hand. For high-stakes plans (architecture,
   backend, data, multi-file, or risky), also kick off the self-review pass in
   **Self-Review Before Handoff** while the user reads, instead of blocking the
   handoff on it.
5. For hosted plans, call `get-plan-feedback` before editing, after review,
   after any long pause,
   and before the final response. Treat `anchorDetails`, resolver intent, recent
   review events, and any focused screenshots from browser handoff as the source
   of truth for exactly what changed and exactly what each comment points at.
6. For hosted plans, apply changes with `update-visual-plan`, preferring
   targeted `contentPatches`.
   Treat the top-level `content` payload as a full replacement, not a merge; do
   not send a partial `content` object to add a canvas or one block. If a full
   replacement is unavoidable, first read the complete plan source/content, carry
   forward every existing block and visual surface, and verify the source/export
   afterward so the document body was not truncated. When the user wants
   source-control friendly edits, use `patch-visual-plan-source` against the MDX
   files instead of regenerating the plan.
7. For hosted plans, export with `export-visual-plan` only when the user wants a
   shareable receipt or repo-check-in artifacts.

## Self-Review Before Handoff

For high-stakes plans — architecture, backend, data-model, migration, multi-file,
or otherwise risky work — run one adversarial self-review pass before treating the
plan as final. Skip it for small, UI-only, or single-decision plans where the cost
outweighs the value. Keep the pass cheap and non-blocking:

- **Surface the plan first, review concurrently.** Post the link and let the user
  start reading, then run the review in parallel — never make the user wait on it.
- **Review the written plan; do not re-research.** Critique the plan text and its
  own blocks. The grounding was already done while drafting, so the review checks
  the output instead of re-exploring the repo.
- **Spawn one skeptical reviewer** whose only job is to find what is weak, missing,
  or wrong — not to praise. Point it at: hard-to-reverse decisions made implicitly
  or not at all (wire format, public ids, data-model shape, auth, ownership); steps
  not anchored in real files or symbols; a menu of options where the plan should
  commit to one; obvious missing decisions ("what happens when X?", "why not Y?");
  and padding or single-step filler.
- **Fix vs. ask.** Apply clear-cut fixes yourself with `update-visual-plan`
  `contentPatches` — vague non-goals, unanchored claims, an obvious missing
  decision. Route genuine judgment calls back to the user instead: add them to the
  bottom `question-form` Open Questions block or batch them into the normal
  ask-user-question flow. Do not silently decide them.
- **Do not surprise the user mid-read.** On a large plan, apply the patches before
  the editor loads; otherwise note briefly that a self-review is running so the
  plan changing under them is expected. When you next respond, summarize what the
  review changed and what it surfaced for the user to decide.

## When to use this skill
- The request is too vague, too large, or too mixed to hand straight to implementation.
- A backlog needs to be cleaned into ready vs not-ready work.
- A roadmap item, feature, bug cluster, launch beat, or playtest finding needs execution slices.
- A team needs one compact packet that names dependencies, blockers, and acceptance criteria.
- The packet spans multiple disciplines: frontend/backend, PM/ops, GTM/content, or code/content/build/playtest work.
- The key question is "what should we actually do next?" rather than "how big is it?" or "what board should we use?"

## When not to use this skill
- **The main job is sizing, forecasting, or story-point language** → `task-estimation`.
- **The work already exists and the real job is issue state transitions** → `triage`.
- **The plan already exists and the real job is review/approval or diff markup** → `plannotator`.
- **The main job is daily status coordination** → `standup-meeting`.
- **The main job is reflection on completed work** → `sprint-retrospective`.
- **The work is still concept framing, scope shaping, or game-production orchestration before decomposition** → `bmad`, `bmad-idea`, or `bmad-gds` first.

## Credits

- **jrennie99-glitch/agent-planner** — license: unknown — https://skillmd.com/skills/jrennie99-glitch/agent-planner
- **github/ai-team-orchestration** — license: unknown — https://skillmd.com/skills/github/ai-team-orchestration
- **builderio/visual-plan** — license: unknown — https://skillmd.com/skills/builderio/visual-plan
- **akillness/task-planning** — license: MIT — https://skillmd.com/skills/akillness/task-planning
- **kursku/sprint-planning** — license: MIT — https://skillmd.com/skills/kursku/sprint-planning

---

## Planning And Execution (merged from `temp/planning-and-execution`, 2026-10-25)

# Planning, Orchestration & Decision-Making

The full lifecycle from *intent* to *done*: write the plan down, decide
what is risky, run it, and coordinate multiple workers without losing the
thread.

## Constituent skills (5)

| Skill | Origin | What it contributes |
|---|---|---|
| super-plan-authoring | super-* pack | Writing a plan: scope, decomposition, sequencing, estimates |
| super-plan-execution | super-* pack | Executing a plan: tracking, deviation handling, completion |
| workshop | built-in | Co-design with the user one decision at a time, publishing an evolving plan Artifact |
| super-risk-decision | super-* pack | Explicit risk/reward judgment calls |
| orchestrate | GitHub app | Coordinating multiple agents/workers on one objective |

## Consolidated guidance

1. **A plan is a written artifact, not a mental state.** If it is not
   written, it cannot be tracked, shared or challenged.
2. **Decompose into verifiable tasks.** Every task needs a check that
   proves it done. "Improve performance" is not a task; "get p95 under
   200ms on the listed endpoints" is.
3. **One decision at a time with the human.** `workshop` posture: do not
   present a 20-decision document and ask for approval. Pose the single
   next decision, get an answer, publish the updated plan, repeat.
4. **Plans deviate; the plan must be updated, not abandoned.** When reality
   diverges, write the change into the plan and note why. Restarting the
   plan from scratch every deviation is the failure mode.
5. **Risk decisions need explicit numbers.** State what you lose if it
   fails, what you gain if it succeeds, and how reversible it is.
   Reversibility is the cheapest risk reducer available.
6. **Orchestration is mostly interfaces.** Multiple agents fail on unclear
   boundaries: give each worker a scoped, non-overlapping deliverable with
   its own verification, and integrate explicitly. Overlapping ownership
   produces conflicts, not parallelism.
7. **Sequence by dependency, not by preference.** Do the work that
   unblocks others first; identify the risky assumption and test it early.
8. **Small batches.** Short plan → execute → check beats a long plan that
   is wrong by chapter three.

## When to use this skill

"Plan this project", "break this down", "run a design workshop", "should
we do X or Y?", "coordinate these agents", "we've drifted from the plan".

## References (load on demand)

The detailed sections below are **not** in this body to keep the loaded skill small. When a task needs one, load it with `read_skill_resource("<skill-name>", "references/<file>.md")` (substitute this skill's name). Read only the reference the current task calls for.

- **`references/output-collaboration.md`** — planning output format, collaboration guidelines and best-practice tips
- **`references/mcp-integration.md`** — wiring planning to MCP tools, tool guidance and setup/authentication
- **`references/team-and-chat.md`** — multi-agent team roles, chat architecture and when to use them
- **`references/bootstrap-recovery.md`** — bootstrapping a new planning project and recovering lost context
- **`references/plan-presentation.md`** — choosing a visual surface and producing wireframes, canvases, documents and exemplars for a plan
- **`references/comments-visibility.md`** — interpreting review-comment anchors and visibility/sharing of plans
- **`references/instructions-templates.md`** — detailed planning instructions, output templates, examples, checklists and (pt-BR) reference metrics
