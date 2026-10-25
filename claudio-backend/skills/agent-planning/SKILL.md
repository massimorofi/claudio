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

## Output Format

Your planning output should include:

```yaml
plan:
  objective: "Clear description of the goal"
  phases:
    - name: "Phase Name"
      tasks:
        - id: "task-1"
          description: "What needs to be done"
          agent: "Which agent should handle this"
          dependencies: ["task-ids"]
          estimated_time: "15m"
          priority: "high|medium|low"
  
  critical_path: ["task-1", "task-3", "task-7"]
  
  risks:
    - description: "Potential issue"
      mitigation: "How to handle it"
  
  success_criteria:
    - "Measurable outcome 1"
    - "Measurable outcome 2"
```

## Collaboration Guidelines

- Coordinate with other agents to validate feasibility
- Update plans based on execution feedback
- Maintain clear communication channels
- Document all planning decisions

## Best Practices

1. Always create plans that are:
   - Specific and actionable
   - Measurable and time-bound
   - Realistic and achievable
   - Flexible and adaptable

2. Consider:
   - Available resources and constraints
   - Team capabilities and workload
   - External dependencies and blockers
   - Quality standards and requirements

3. Optimize for:
   - Parallel execution where possible
   - Clear handoffs between agents
   - Efficient resource utilization
   - Continuous progress visibility

## MCP Tool Integration

### Task Orchestration
```javascript
// Orchestrate complex tasks
mcp__claude-flow__task_orchestrate {
  task: "Implement authentication system",
  strategy: "parallel",
  priority: "high",
  maxAgents: 5
}

// Share task breakdown
mcp__claude-flow__memory_usage {
  action: "store",
  key: "swarm$planner$task-breakdown",
  namespace: "coordination",
  value: JSON.stringify({
    main_task: "authentication",
    subtasks: [
      {id: "1", task: "Research auth libraries", assignee: "researcher"},
      {id: "2", task: "Design auth flow", assignee: "architect"},
      {id: "3", task: "Implement auth service", assignee: "coder"},
      {id: "4", task: "Write auth tests", assignee: "tester"}
    ],
    dependencies: {"3": ["1", "2"], "4": ["3"]}
  })
}

// Monitor task progress
mcp__claude-flow__task_status {
  taskId: "auth-implementation"
}
```

### Memory Coordination
```javascript
// Report planning status
mcp__claude-flow__memory_usage {
  action: "store",
  key: "swarm$planner$status",
  namespace: "coordination",
  value: JSON.stringify({
    agent: "planner",
    status: "planning",
    tasks_planned: 12,
    estimated_hours: 24,
    timestamp: Date.now()
  })
}
```

Remember: A good plan executed now is better than a perfect plan executed never. Focus on creating actionable, practical plans that drive progress. Always coordinate through memory.

## When to Use
- Starting a new project that needs planning, development, testing, and deployment
- Setting up parallel AI agent teams (dev, QA, DevOps)
- Writing brainstorm prompts that produce real debate (not generic output)
- Creating sprint plans with cross-chat context survival
- Recovering from context overflow mid-sprint

## Team Roles

| Agent | Name | Role | Focus |
|-------|------|------|-------|
| Producer | **Remy** | Sprint planning, coordination, merging PRs | Scope control, handoffs, issue triage |
| Product Designer | **Kira** | UX, mechanics, user experience | Fun factor, user flows, feature design |
| Visual/Art Director | **Milo** | CSS, animations, visual identity | Design system, polish, accessibility |
| Frontend Engineer | **Nova** | UI framework, state management, components | React/Vue/Svelte, client-side logic |
| Backend Engineer | **Sage** | API, database, auth, security | Server-side logic, infrastructure |
| DevOps Engineer | **Dash** | CI/CD, cloud deployment, pipelines | GitHub Actions, Azure/AWS/GCP |
| QA Engineer | **Ivy** | E2E tests, automation, playtesting | Playwright/Cypress, bug filing, sign-off |

Customize names and roles for your project. Not every project needs all roles.

## Chat Architecture

The human (CEO) is the message bus between parallel chats:

```
┌────────────────────────────────────────┐
│  @ai-team-producer — Plans, merges     │
│  NEVER writes code                     │
└────────────────┬───────────────────────┘
                 │ Human carries messages
      ┌──────────┼──────────┐
      ▼          ▼          ▼
┌──────────┐ ┌────────┐ ┌────────┐
│@ai-team  │ │@ai-team│ │DevOps  │
│-dev      │ │-qa     │ │(on     │
│          │ │        │ │demand) │
│ Nova     │ │ Ivy    │ │        │
│ Sage     │ │        │ │        │
│ Milo     │ │        │ │        │
│          │ │feature/│ │feature/│
│ feature/ │ │qa-N    │ │devops-N│
│ sprint-N │ └────────┘ └────────┘
└──────────┘
```

Each team works in a **separate VS Code window** with its own clone:
```bash
git clone <repo> project-dev    # Dev team
git clone <repo> project-qa     # QA
git clone <repo> project-devops # DevOps (only when needed)
```

## Project Bootstrap

### 1. Create PROJECT_BRIEF.md

The single source of truth across all chats. See the [project brief template](./references/project-brief-template.md).

**Required sections (do not abbreviate):**
1. Project Overview
2. Concept / Product Description
3. Tech Stack
4. Architecture (ASCII diagram)
5. Key Files Map
6. Team Roles
7. Sprint Status (updated every sprint)
8. Current State (rewritten every sprint)
9. Security Rules
10. How to Run Locally
11. How to Deploy
12. **Cross-Chat Handoff Protocol** — how context survives between chats
13. **Bug & Fix Tracking** — GitHub Issues as single source of truth
14. **Multi-Repo Setup** — separate clones, branch strategy, merge rules

### 2. Run a Brainstorm

See the [brainstorm format](./references/brainstorm-format.md). Key: name each agent explicitly with distinct personality and perspective. Require at least 2 genuine disagreements to prevent groupthink.

### 3. Create Sprint Plans

See the [sprint plan template](./references/sprint-plan-template.md). Every sprint gets:
- `docs/sprint-N/plan.md` — prioritized tasks, success criteria
- `docs/sprint-N/progress.md` — live tracker, enables recovery
- `docs/sprint-N/done.md` — handoff doc written at sprint end

### 4. Execute Sprints

```
Read PROJECT_BRIEF.md, then read docs/sprint-N/plan.md. Execute Sprint N.

First: git pull origin main && git checkout -b feature/sprint-N

Close GitHub Issues in commits: "fix: description (Fixes #NN)"
Update docs/sprint-N/progress.md after each phase.
When done, push and create PR: git push origin feature/sprint-N
Follow Sections 12-14 of PROJECT_BRIEF.md.
```

### 5. QA Sign-off

After dev merges, QA does a full playthrough:
```
Read PROJECT_BRIEF.md. You are Ivy (QA).
Sprint N is merged to main. Do full playthrough.
File bugs as GitHub Issues. Write docs/qa/sprint-N-signoff.md.
```

## Context Recovery

When a chat gets long (>100 messages), save state and start fresh:

**Before closing:**
1. Update `docs/sprint-N/progress.md` with current status
2. Update `PROJECT_BRIEF.md` sections 7+8
3. Write `docs/sprint-N/done.md`

**Cold start prompt:**
```
Read PROJECT_BRIEF.md and docs/sprint-N/progress.md.
Continue from where it left off.
```

## Anti-Patterns

See [anti-patterns reference](./references/anti-patterns.md) for the full list. Top 5:

| Don't | Do Instead |
|-------|------------|
| Rebase feature branches | Merge (rebase loses commits) |
| Producer writes code | Producer only plans, merges, files issues |
| Batch "fix everything" commits | One commit per fix with issue reference |
| Vague brainstorm prompts | Name each agent with distinct perspective |
| Keep bugs only in chat | File GitHub Issues (chat context dies) |

## Tips for Better Results

- **"Take your time, do it right"** in prompts produces better output than rushing
- **Test before merge** — you playtest, file issues, dev fixes, then merge
- **Run team consiliums** before major sprints — each agent reviews the plan from their perspective
- **Save lessons to memory** after every milestone

Agent-Native Plans is structured visual planning mode for coding agents. Build
the plan you would normally write in Markdown, but as a scannable document with
editable blocks mixed in: inline diagrams, code snippets,
open questions, and an optional top visual review area (wireframe canvas, live
prototype, or both in tabs). Architecture and backend plans stay document-only;
UI and product plans start with the top canvas/prototype (the Visual Surface
Choice section owns that rule).

`/visual-plan` is the packaged command and main entry point. Choose the review
mode from the task: UI-first when the work is primarily product UI and review
should start with screens, prototype-first when review should start with a
functional live prototype, design-first when review needs full-fidelity branded
screens, or visual-intake when the user explicitly wants a questionnaire before
planning. When a Codex, Claude Code, Markdown, or pasted plan already exists,
`/visual-plan` uses that source plan as the starting point and builds the review
surface from it instead of starting over.

## When To Use

Create or adapt a visual plan whenever the plan would be better as a reviewable
artifact than a chat paragraph. This includes modest work such as a single UI
surface with states, a small workflow, a before/after product change, or a
component/API/data-shape decision that needs alignment, plus larger multi-file,
ambiguous, long-running, risky, or UI-heavy work. Use it when architecture /
data flow / UI direction / options / open questions would benefit from inline
diagrams or structured blocks, when the user needs to react to a direction
before you implement, or when an existing text plan needs a richer review
surface.

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

## Create A Structured Agent-Native Plan — Never Inline

The deliverable is ALWAYS a structured Agent-Native Plan, not a chat-only plan.
The hosted Plan MCP connector (`plan` server, or legacy `agent-native-plans`) is
the default collaboration and commenting surface; it is not a reason to reject
the planning pattern as an external dependency or rented layer. Plans are
portable source artifacts (`plan.mdx`, optional `canvas.mdx` /
`prototype.mdx`, JSON, and HTML export), and ownership-sensitive workflows can
use local-files mode or a self-hosted/custom Plan app URL without abandoning the
skill's review discipline. Do not advise the user to skip `/visual-plan` because
the default surface is hosted; choose the right Plan mode for the user's
ownership, privacy, sharing, and branding needs.

By default, create the plan via the Plan MCP connector and NEVER hand it over as
inline chat content — no Markdown prose, ASCII sketch, table, or fenced
wireframe. If the `plan` (or legacy `agent-native-plans`) tools are not visible,
discover them through the host's `tool_search` first; if they are still missing,
STOP and give the user the client-specific reconnect step rather than improvising
an inline plan. Before publishing, or whenever a connector or auth error appears,
READ `references/connection.md` in this skill directory — it is the single source
of truth for the never-inline rule, connector discovery, and the per-client
reconnect steps. Local-files privacy mode (after Tool Guidance) is the exception.

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
   (see `references/canvas.md` and `references/document-quality.md`). For
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

## Visual Surface Choice

Choose the surface before creating the plan or after reading the source plan. Do
not add visual chrome by default:

For UI/product plans, the top canvas is usually the primary review surface. Put
the first meaningful wireframes there, not buried as document-body blocks. Use
multiple canvas artboards when states matter, such as the default view, an
overflow menu or popover, a side panel, loading, or error. Put short annotations
beside frames with `targetId` plus `placement`; keep implementation details,
tradeoffs, file maps, data contracts, risks, and verification in the document
body below the canvas.

When the user asks for a flow, storyboard, journey, wireframe, canvas, or "what
this looks like", treat that as a canvas-first request. Make one artboard per
user-visible state, connect only adjacent transitions, and use short canvas
annotations for the product notes. Do not substitute a document-body `diagram`
block for the requested storyboard just because HTML diagrams are faster to
write; diagrams belong below the canvas for backend mechanics, architecture, or
data-flow explanation.

Keep product wireframes and explanatory/meta diagrams separate. Start with pure
screens that look like the app state under discussion, without callout prose or
architecture notes embedded inside the UI. Put arrows, labels, contracts, data
flow, and mode explanations in separate annotations, separate canvas diagrams,
or the document body.

When the plan touches an existing app, inspect the current shell/components
before drawing. The first artboard should look like the real app at the same
density: existing sidebars, toolbar placement, overflow menus, app chrome, and
framework agent chrome stay in their real places. Model secondary surfaces as
separate states, such as a top-right overflow popover, sheet, panel, loading
state, or separate AgentSidebar, rather than inventing a permanent inspector or
folding framework chrome into the product UI.

- **No visual surface** for architecture-only, backend-only, data migration,
  copy-only, or otherwise non-visual plans. Do not use the top canvas for
  architecture diagrams, dependency maps, file plans, API contracts, or
  data-flow-only reviews. Use a strong document with local inline diagrams
  only when relationships need a visual explanation, usually one spatial diagram
  per recommendation or decision. Prefer grouped regions, layers, quadrants,
  matrices, or before/after panels over a single-axis chain unless the
  relationship is truly sequential.
- **Canvas only** for one static screen, a before/after comparison, a component
  state, a small popover, or a visual direction that does not require clicking.
  Put those wireframes in `content.canvas` and omit `content.prototype`.
- **Canvas + prototype** for multi-step UI flows, onboarding, wizards,
  review/approval flows, navigation changes, or anything where the reviewer
  needs to operate the behavior. Keep the static wireframes in
  `content.canvas`, add the aligned functional prototype in
  `content.prototype`, and rely on the top visual tabs to switch between them.
- **Prototype-first** when the user asks to operate the UI or when interaction is
  the main question. Use `create-prototype-plan`, which still preserves static
  mocks where useful.

For mixed canvas + prototype plans, reuse the same real labels, app statuses,
and screen ids across both surfaces. The canvas is the inspectable static reference;
the prototype is the interactive version of that same flow, not a separate
design direction.

## Wireframe quality — read `references/wireframe.md`

UI recap/plan wireframes must meet a strict quality bar — full-width chrome,
pinned bottom bars, real product content, before/after comparability, the right
`surface` preset, `--wf-*` tokens instead of hex, and no `<html>`/`<style>`/font
tags. Before authoring ANY wireframe / `<Screen>` / `WireframeBlock`, READ
`references/wireframe.md` in this skill directory — it is the single source of
truth for HTML wireframe quality, shared word for word with `/visual-plan`
and `/visual-recap`. Do not author wireframes from memory.

## Canvas — read `references/canvas.md`

The canvas is the single source of truth for static UI mockups: the `surface`
locks each artboard's footprint, mixed surfaces lay out
in lanes, annotations are plain-text designer notes anchored by
`targetId`/`placement`, and edits are surgical `contentPatches`. Before
authoring or editing ANY canvas, artboard, or annotation, READ
`references/canvas.md` in this skill directory — it is the single source of truth
for canvas/artboard mechanics. Do not author canvas layouts from memory.
Canvas artboards use the same HTML wireframe path as document-body
`WireframeBlock` screens: author `<Screen surface="..." html={...} />` with a
semantic HTML fragment. Do not author fresh kit-tree children such as
`<FrameScreen>`, `<Card>`, `<Row>`, or `<Btn>` inside canvas `<Screen>` tags;
those are legacy compatibility markup for old plans and produce brittle canvas
layouts.

## Document quality — read `references/document-quality.md`

The document is a serious technical plan, not marketing: outcome-first,
prose-first, self-contained, built from the right native blocks, with open
questions in a single bottom `question-form` and a pre-handoff visual check.
Before authoring the plan document, READ `references/document-quality.md` in this
skill directory — it is the single source of truth for the document quality bar.
Do not write the document from memory.

## Good vs. bad exemplar — read `references/exemplar.md`

For a worked example of the bar — a great UI-first plan and `/visual-plan`, plus
the anti-patterns to avoid — READ `references/exemplar.md` in this skill
directory before authoring a plan.

## Tool Guidance

- `create-visual-plan`: start one structured visual plan per agent task/run, or
  import an existing text plan by passing `planText`; `content` may include no
  visual surface, canvas only, or canvas + prototype.
- `create-ui-plan`: start a UI-first plan when the work is primarily product UI.
- `create-prototype-plan`: start a prototype-first plan with a functional top
  review surface.
- `create-plan-design`: start a full-fidelity branded Design-tab plan with an
  optional matching Prototype tab.
- `convert-visual-plan-to-prototype`: convert an existing HTML wireframe canvas
  into a prototype plan.
- `create-visual-questions`: use only when the user explicitly asks for a visual
  intake questionnaire, not as `/visual-plan` preflight.
- `update-visual-plan`: revise content, status, or comments with targeted
  `contentPatches` (see Core Workflow step 6).
- `read-visual-plan-source`: read the normalized plan as `plan.mdx`,
  optional `canvas.mdx`, optional `.plan-state.json`, and JSON.
- `patch-visual-plan-source`: apply granular MDX AST patches by stable block,
  artboard, annotation, component, or wireframe-node id.
- `import-visual-plan-source`: create or replace a plan from an MDX folder.
- `get-visual-plan`: read the current structured plan, exported HTML, and
  annotations; it also returns the MDX folder for source workflows.
- `get-plan-feedback`: read unconsumed human feedback. Use it frequently; it
  returns grouped threads, exact anchor details, expected resolver, and recent
  review-event payloads so agents can act only on the comments meant for them.
- `get-plan-blocks`: resolve block tags before authoring — do not memorize tags;
  call this first to get the authoritative tag names, required fields, and prop
  shapes from the live block registry.
- `export-visual-plan`: export HTML, Markdown fallback, structured JSON, and MDX
  files for repo check-in.

When the user critiques a plan's look or structure, fix the renderer or this
skill — never hand-edit one stored plan. Turn feedback into better guidance.

## Local-Files Privacy Mode — read `references/local-files.md`

When the user wants no hosted Plan database writes — no DB writes, no Plan MCP
publish, fully local/offline/private planning, repo-owned source-controlled
artifacts, or `AGENT_NATIVE_PLANS_MODE=local-files` — do not call any hosted Plan
tool except the schema-only `get-plan-blocks` catalog lookup. Author a local MDX
folder and
preview it with `plan local check` / `plan local serve` / `plan local verify`.
Before using local-files mode, READ `references/local-files.md` in this skill
directory — it is the single source of truth for the full contract (catalog
lookup, MDX folder layout, the local bridge commands, and the hosted tools you
must not call). Carry forward only the code-research and plan-composition
guidance from Core Workflow; everything hosted is replaced by the local bridge.

## Interpreting comment anchors

This section applies to hosted plans with `get-plan-feedback` /
`update-visual-plan`. In local-files mode, do not call hosted feedback or update
tools; interpret file/chat feedback directly, edit the MDX files, rerun the
local bridge check/serve/verify command, and report the new local URL.

`get-plan-feedback` returns rich anchors — read them before acting on any comment.

- **Coordinate frames.** `targetX`/`targetY` are percentages *within* the
  element named by `targetSelector`/`targetKind`. Bare `x`/`y` are percentages
  of the whole plan document. `canvasX`/`canvasY` are raw board-world pixels on
  the design canvas (board size given when available).
- **Wireframe pins.** Anchors on wireframes include `targetNodeId` and
  `targetNodePath` (e.g. `card > list > listItem "Acme Inc"`) identifying the
  exact kit node. Use `targetNodeId` directly with wireframe node patch ops;
  use `data-design-id` values from design artboards with
  `update-design-element-style`. Prefer the node id/path over raw coordinates;
  fall back to coordinates plus the focused screenshot (red ring marks the exact
  point) only when no node id is present.
- **Text quotes.** Resolve `textQuote` against current prose using
  `contextBefore`/`contextAfter` for disambiguation. If `ambiguous: true`, ask
  the user — do not guess which occurrence is meant.
- **Detached comments.** `get-plan-feedback` flags threads whose quoted text no
  longer exists as `detached` (in `detachedThreads`). Reconcile these against
  rewritten content — never silently drop them.
- **Routing.** `resolutionTarget` is the only routing signal: act on `agent`,
  treat `human` as context only. `@mentions` are people to notify, never a
  routing signal.
- **Two-axis state.** Mark every ingested comment as consumed
  (`consumedCommentIds` on `update-visual-plan`). Set `status=resolved` only on
  agent-targeted comments you actually addressed; leave human-targeted comments
  open.

## Visibility & Sharing

Use `set-resource-visibility` to change who can see a plan (e.g. public, login,
or org-scoped). Use `share-resource` to grant specific users or roles access
by email or role. Gate visibility before sharing any plan that covers
unreleased or private work — default to the narrowest scope that meets the
review need.

## Setup & Authentication

There are two ways into Plans.

**Coding agent (CLI).** Install once with the Agent-Native CLI. The command
installs the Plans skills, registers the hosted Plans MCP connector, and runs
auth/setup for the selected local client(s) in the same step (a one-time browser
sign-in at setup — this is intended), so the first tool call in that client does
not hit an OAuth wall:

```bash
npx @agent-native/core@latest skills add visual-plans
```

After that, `/visual-plan` and `/visual-recap` are the two installed slash
commands. If you only need one command, use `skills add visual-plan` or
`skills add visual-recap` instead. The other planning modes
(`create-ui-plan`, `create-prototype-plan`, `create-plan-design`,
`create-visual-questions`) are MCP tools reachable from `/visual-plan`, not
separate slash commands. Pass `--no-connect` to register the connector without
authenticating, then run
`npx @agent-native/core@latest connect https://plan.agent-native.com --client all`
whenever you are ready, or choose a narrower `--client`. Auth and MCP tool
loading are per client config/session.

**Browser (people you share with).** Open the Plans editor and create & edit
with no sign-up — you work as a guest. Sign in only when you want to save or
share; signing in claims the plans you made as a guest into your account.

Sharing and commenting require an account: public/shared plans are viewable by
anyone with the link, but commenting on them needs an agent-native account.

For fully offline, no-account use, run the Plans app locally and sync plans to
your repo as MDX. This local mode is a separate advanced path, not the default
hosted flow.

If a Plans tool returns `needs auth`, `Unauthorized`, or `Session terminated`, do
not keep retrying it — stop and give the user the per-client reconnect step from
`references/connection.md`, then continue once the connector is available.

Hosted default: connect `https://plan.agent-native.com/_agent-native/mcp`. Do
not put shared secrets in skill files.

Use this skill when the job is **turning messy context into one small planning packet that a team can actually act on**.

`task-planning` is the PM front door for backlog cleanup, feature decomposition, sprint-candidate prep, release slicing, milestone packets, and roadmap-to-delivery translation before estimation, boards, review, or execution.

Core references:
- [references/intake-packets-and-route-outs.md](references/intake-packets-and-route-outs.md)
- [references/packet-shapes.md](references/packet-shapes.md)
- [references/readiness-checklist.md](references/readiness-checklist.md)
- [references/planning-patterns.md](references/planning-patterns.md)

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

## Instructions

### Step 1: Choose one intake packet
Use [references/intake-packets-and-route-outs.md](references/intake-packets-and-route-outs.md) and pick exactly one primary packet:

```yaml
task_planning_packet:
  packet: backlog-cleanup | feature-slice | sprint-candidate | release-packet | milestone-packet | discovery-first
  domain: developer-workflow | web-fullstack | product-ops | marketing-gtm | game-development | mixed
  source_material: repo/issues | prd/spec | gdd/playtest | launch-notes | chat-context | mixed | unknown
  readiness_state: mostly-ready | mixed | mostly-fuzzy
  output_shape: single-packet | slice-table-plus-not-ready | release-packet | milestone-packet | discovery-packet
```

Packet meanings:
- `backlog-cleanup` — clarify, dedupe, and separate ready from not-ready work.
- `feature-slice` — turn one feature or bug cluster into assignable slices.
- `sprint-candidate` — prepare the next iteration's ready work.
- `release-packet` — shape launch, rollout, campaign, or go-live work.
- `milestone-packet` — coordinate cross-discipline milestone or demo work, especially for games.
- `discovery-first` — expose unknowns before pretending implementation is ready.

### Step 2: Gather the minimum credible evidence
Do not plan from vibes alone. Pull the smallest packet that supports decomposition:
- goal or problem statement
- user, business, team, or player outcome
- current artifacts: issues, spec/PRD, GDD, launch notes, playtest notes, bug list, or chat context
- timeline or trigger: sprint, release, milestone, event, or dependency window
- obvious constraints: owner, platform, environment, approvals, external dependencies
- missing details that could make the packet fake-ready

If evidence is thin, say so and choose `discovery-first` instead of inventing certainty.

### Step 3: Split discovery from delivery early
Before you write slices, separate:
1. **Discovery** — unanswered questions, validation, missing decisions
2. **Foundation** — setup, architecture, shared assets, tooling, environments
3. **Delivery** — user-facing or system-facing implementation slices
4. **Verification** — QA, analytics, review, smoke tests, playtests, launch checks
5. **Follow-through** — docs, enablement, rollout, reporting, monitoring, distribution

Do not bury research or unresolved decisions inside build tickets.

### Step 4: Choose the smallest packet shape
Use [references/packet-shapes.md](references/packet-shapes.md).

Rules:
- If the user needs one next-action set, prefer a **single packet**.
- If the main need is ready vs not-ready triage, use **slice table plus not-ready list**.
- If the work is tied to a launch window, use a **release packet**.
- If the work is milestone-heavy and cross-discipline, use a **milestone packet**.
- If the packet starts doing decomposition, estimation, board control, review, and ceremony work at once, split responsibilities and route outward.

### Step 5: Build slices with readiness fields
Every slice should have:
- title
- outcome
- owner role
- dependencies
- inputs required
- acceptance criteria
- risk / uncertainty
- ready? yes / no
- if not ready, what is missing?

Use short, testable acceptance criteria. Avoid vague statements like "works better" or "launch ready".

### Step 6: Surface sequence and blockers
Explicitly name:
- what can run in parallel
- what must happen in order
- what is blocked
- what should be deferred

Use blocker buckets from [references/readiness-checklist.md](references/readiness-checklist.md):
- `missing-scope`
- `missing-design`
- `missing-data`
- `external-dependency`
- `environment-access`
- `approval-needed`
- `cross-team-handoff`

### Step 7: Run the route-out check
Verify all of these:
1. The chosen packet matches the real planning job.
2. Discovery is separated from implementation when confidence is low.
3. The packet does not silently absorb sizing, board control, plan review, standups, or retros.
4. Cross-domain nuance survives without turning the output into a tutorial.
5. The packet ends with one clear next move.

Route-outs to keep explicit:
- sizing → `task-estimation`
- issue state transitions → `triage`
- plan review / approval → `plannotator`
- daily coordination → `standup-meeting`
- completed-work reflection → `sprint-retrospective`
- concept framing / strategy shaping → `bmad`, `bmad-idea`, `bmad-gds`

### Step 8: Return the brief or the final packet
Preferred brief shape before full drafting:

```markdown
# Task Planning Brief

## Packet choice
- Packet:
- Domain:
- Why it fits:
- Output shape:

## Source material used
- Main evidence:
- Constraints / dependencies:
- Assumptions / gaps:

## Planned slices
1. slice
2. slice
3. slice

## Route-out notes
- Out of scope:
- Not-ready work kept separate:
- Recommended next move:
```

If the user already asked for the final artifact, return a compact planning packet directly.

## Output format
Default packet shape:

```markdown
# Planning Packet

## Planning horizon
- Packet:
- Domain:
- Confidence: high | medium | low

## Goal
- ...

## Assumptions
- ...

## Work slices
| Slice | Outcome | Owner role | Dependencies | Ready? |
|------|---------|------------|--------------|--------|
| ... | ... | ... | ... | yes/no |

## Slice details
### 1. [Slice name]
- Inputs required:
- Acceptance criteria:
  - [ ] ...
- Risks / uncertainty:
- Notes:

## Sequencing
1. ...
2. ...

## Blockers / not-ready items
- Bucket:
- Missing:
- Next action:

## Recommended next move
- start implementation | run discovery first | groom with owners | estimate now | defer until dependency clears
```

## Examples

### Example 1: Fullstack feature slicing
**Input**
> Break down a new team-invite flow for our SaaS app. We need email invites, acceptance, and admin visibility before sprint planning.

**Good output direction**
- packet: `sprint-candidate`
- domain: `web-fullstack`
- split backend/data, invite acceptance flow, admin visibility, verification, and follow-through
- keep story points out of scope

### Example 2: Backlog cleanup
**Input**
> We have a pile of vague onboarding backlog items. Clean them up so we can see what is actually ready next week.

**Good output direction**
- packet: `backlog-cleanup`
- output shape: `slice-table-plus-not-ready`
- mark missing scope, ownership, or approvals explicitly
- separate discovery tickets from implementation tickets

### Example 3: Marketing / launch packet
**Input**
> Plan the next release push for our B2B launch: landing-page updates, email sequence, attribution checks, and launch-day reporting.

**Good output direction**
- packet: `release-packet`
- domain: `marketing-gtm`
- separate asset creation, review/approval, distribution, measurement, and reporting
- route deep copywriting/campaign execution to `marketing-automation`

### Example 4: Game milestone packet
**Input**
> Plan the next milestone for our roguelike demo: tutorial polish, controller support, and a streamer-ready build.

**Good output direction**
- packet: `milestone-packet`
- domain: `game-development`
- separate code/system work, content/polish, build/QA, and playtest/distribution concerns
- keep broader game-production orchestration routed to `bmad-gds` when needed

## Best practices
1. Choose one primary packet before decomposing the work.
2. Keep discovery separate from delivery whenever requirements are unstable.
3. Prefer small, reviewable slices over broad work categories.
4. Surface blockers and missing inputs explicitly instead of burying them in notes.
5. Preserve domain nuance for developer workflow, web/fullstack, product/ops, marketing/GTM, and game work without bloating the front door.
6. Route sizing, board control, review, daily cadence, and retrospectives out instead of stretching the skill boundary.
7. Keep the packet compact enough that a team can act on it immediately.
8. Update compact and manifest discovery surfaces when the role wording changes materially.

## References
- [Atlassian — Backlog refinement](https://www.atlassian.com/agile/scrum/backlog-refinement)
- [Atlassian — Sprint planning](https://www.atlassian.com/agile/scrum/sprint-planning)
- [GitHub Docs — About Projects](https://docs.github.com/en/issues/planning-and-tracking-with-projects/learning-about-projects/about-projects)
- [GitHub Docs — About milestones](https://docs.github.com/en/issues/using-labels-and-milestones-to-track-work/about-milestones)
- [HacknPlan Docs](https://hacknplan.com/docs/milestones-and-deadlines/)

Esta skill habilita o Claude a conduzir e otimizar sessões de Sprint Planning, garantindo alinhamento, estimativas realistas e um Product Backlog refinado para a próxima iteração.

---

## Keywords

Sprint Planning, Product Backlog Refinement, Sprint Goal, Capacity Planning, Story Points, Velocity, DoD (Definition of Done), PBI (Product Backlog Item), Scrum Team, Commitments, Stakeholder Alignment, Iteration Planning, Sprint Backlog.

---

## Quick Start

1.  **Confirmar Preparação:** Verifique se o Product Backlog está priorizado e refinado com PBIs "Ready" para as próximas 2-3 sprints, e se a Definition of Done (DoD) foi revisada.
2.  **Calcular Capacidade:** Estime a capacidade da Development Team para a sprint atual, considerando a Velocity média e ausências planejadas (ex: 80% da média de velocity de 30 Story Points resulta em 24 Story Points de capacidade).
3.  **Definir Sprint Goal:** Facilite a criação de um Sprint Goal claro e mensurável, alinhado com o Product Goal, que a equipe possa alcançar.
4.  **Selecionar PBIs:** Guie a Development Team na seleção dos Product Backlog Items (PBIs) que contribuem para o Sprint Goal, dentro da capacidade calculada, e detalhe-os em tarefas.
5.  **Compromisso Final:** Assegure que a equipe compreenda o Sprint Backlog e se comprometa com o Sprint Goal, tornando-o visível para todos.

---

## Core Workflows

### Workflow 1: Definição do "O QUÊ" e "POR QUÊ" da Sprint

Este workflow foca na parte inicial da Sprint Planning, onde o Product Owner e a Development Team estabelecem o objetivo e selecionam os itens prioritários.

*   **Passo 1: Reafirmação do Product Goal e Visão:** O Scrum Master inicia a sessão reafirmando o Product Goal atual e a visão do produto. O Product Owner (PO) então apresenta os Product Backlog Items (PBIs) de maior prioridade, focando no valor que entregam para o próximo incremento e como se alinham ao Product Goal.
    *   **Exemplo Prático:** O Scrum Master (SM) projeta o Product Goal: "Lançar o sistema de agendamento online completo até o final do Q3." O Product Owner (PO) começa: "Para atingir esse Product Goal, nossa próxima sprint precisa focar no módulo de cadastro de clientes. Os PBIs 'Desenvolver formulário de cadastro', 'Implementar validação de CPF' e 'Persistir dados de usuário' são essenciais para iniciar esta fase."
*   **Passo 2: Definição do Sprint Goal Provisório:** A Development Team, em conjunto com o PO, discute os PBIs prioritários e propõe um objetivo coeso para a sprint. Este Sprint Goal deve ser uma declaração clara do que a equipe pretende alcançar e por que é importante.
    *   **Exemplo Prático:** Após a apresentação do PO, a equipe discute: "Se focarmos no cadastro, podemos ter um Sprint Goal como 'Habilitar o registro inicial e verificação de dados para novos clientes via formulário web', criando a base para o onboarding."
*   **Passo 3: Seleção e Entendimento dos PBIs:** O PO detalha os PBIs pré-selecionados para a sprint. A Development Team faz perguntas para esclarecer requisitos, identificar dependências e discutir o "porquê" de cada item, garantindo que todos compreendam o escopo e os critérios de aceitação.
    *   **Exemplo Prático:** Para o PBI "Implementar validação de CPF", o Desenvolvedor Carlos pergunta: "A validação de CPF usará um serviço externo? Já temos as credenciais de API e a documentação?" O PO responde: "Sim, usaremos a API da Receita Federal; as credenciais estão no LastPass e a documentação foi anexada ao PBI."

### Workflow 2: Planejamento do "COMO" e Compromisso Final

Este workflow aborda a fase de detalhamento técnico, estimativa e formalização do compromisso da Development Team.

*   **Passo 1: Detalhamento e Estimativa dos PBIs Selecionados:** Para cada PBI selecionado que contribui para o Sprint Goal, a Development Team discute as tarefas técnicas necessárias para atingir a Definition of Done (DoD). Eles então estimam o esforço usando Story Points (para complexidade relativa) ou horas (para tarefas mais granulares), considerando complexidade, incerteza e tamanho.
    *   **Exemplo Prático:** Para o PBI "Desenvolver formulário de cadastro de usuário (8 Story Points)", as tarefas detalhadas pela equipe podem ser: "Criar mockups da UI (3h)", "Implementar front-end do formulário (12h)", "Desenvolver endpoint de API para cadastro (10h)", "Escrever testes unitários e de integração (6h)".
*   **Passo 2: Verificação da Capacidade da Equipe:** O Scrum Master ou um membro da equipe compara o total de Story Points ou horas estimados para os PBIs selecionados com a capacidade calculada da Development Team para a sprint. Se o total exceder a capacidade, a equipe negocia com o PO para ajustar o escopo (remover um PBI, dividir um PBI grande, etc.).
    *   **Exemplo Prático:** A Development Team calculou uma capacidade de 28 Story Points para a sprint, baseada na Velocity média de 30 SP e uma ausência de 1 dia de Diana. A soma dos PBIs selecionados totaliza 32 Story Points. O Desenvolvedor Eduardo sugere: "Para caber na nossa capacidade, teremos que adiar o PBI 'Adicionar validação de e-mail por regex', pois os demais são críticos para o Sprint Goal." O PO concorda.
*   **Passo 3: Criação do Sprint Backlog e Definição do Sprint Goal Final:** Os PBIs e suas tarefas detalhadas formam o Sprint Backlog. O Sprint Goal é finalizado e escrito de forma clara e visível para toda a equipe. O Sprint Backlog e o Sprint Goal representam o compromisso da Development Team para a sprint.
    *   **Exemplo Prático:** O Sprint Backlog é populado com os PBIs e tarefas. O Sprint Goal final é formalizado como: "Habilitar o formulário de cadastro de usuário com validação de CPF funcionando e persistência inicial de dados no banco de dados, permitindo a criação básica de novas contas." Este goal é então afixado em um quadro físico ou digital e comunicado.

---

## Templates

### Template: Ata de Sprint Planning

```
ATA DE SPRINT PLANNING

Data: 2024-03-01
Sprint: #15 - "Início do Cadastro de Clientes"
Duração da Sprint: 11/03/2024 a 22/03/2024 (10 dias úteis)
Participantes: Ana (PO), Bruno (SM), Carlos (Dev), Diana (Dev), Eduardo (Dev)
Local: Sala de Reuniões Alpha / Google Meet

1. Revisão do Product Goal e Visão:
   - Product Goal: Lançar o sistema de agendamento online completo até o final do Q3.
   - Visão: Simplificar e agilizar o processo de agendamento de serviços para nossos usuários e parceiros.

2. Sprint Goal Definido:
   - Habilitar o formulário de cadastro de usuário com validação de CPF funcionando e persistência inicial de dados no banco de dados, permitindo a criação básica de novas contas.

3. Capacidade da Equipe:
   - Velocity Média (Últimas 3 Sprints): 30 Story Points
   - Ajustes na Capacidade: Diana estará em treinamento por 1 dia (redução de ~2 SP).
   - Capacidade para Sprint #15: 28 Story Points

4. Product Backlog Items (PBIs) Selecionados para a Sprint Backlog:
   - PBI-0056: Desenvolver tela de cadastro de usuário (8 SP)
     - Tarefas: Criar mockups UI (3h), Implementar front-end (12h), Validar campos front-end (8h), Desenvolver serviço de criação de usuário (10h), Testes unitários front-end (4h), Testes unitários back-end (5h), Teste de integração (3h).
   - PBI-0057: Implementar validação de CPF via serviço externo (5 SP)
     - Tarefas: Integrar API de validação (6h), Tratar erros da API (4h), Testes de integração da API (3h).
   - PBI-0058: Persistir dados de usuário no banco de dados (8 SP)
     - Tarefas: Modelar entidade de usuário (2h), Criar CRUD básico (10h), Escrever testes de persistência (6h).
   - PBI-0059: Criar endpoint de login de usuário (7 SP)
     - Tarefas: Desenvolver API REST de login (8h), Implementar autenticação JWT (6h), Escrever testes de segurança (4h).

5. Total de Story Points Compromissados: 28 SP

6. Riscos e Impedimentos Identificados:
   - Dependência da API de validação de CPF: Monitorar uptime do serviço externo.
   - Complexidade na implementação de autenticação JWT: Reservar tempo para pesquisa e spike, se necessário.

7. Observações Adicionais:
   - Definition of Done (DoD) revisada e confirmada.
   - Próxima reunião: Daily Scrum amanhã, 11/03, às 09:30.
```

### Template: Sprint Backlog Item Detalhado

```
SPRINT BACKLOG ITEM DETALHADO

ID do PBI: PBI-0056
Título: Desenvolver tela de cadastro de usuário
Story Points: 8
Sprint Goal Relacionado: Habilitar o formulário de cadastro de usuário com validação de CPF e persistência de dados.
Descrição: Como novo usuário, quero preencher um formulário de cadastro com meus dados pessoais (nome completo, e-mail, CPF, senha) para criar uma conta no sistema, de modo que eu possa acessar funcionalidades futuras.
Critérios de Aceitação:
- O formulário deve conter campos para Nome Completo, E-mail, CPF e Senha.
- Todos os campos obrigatórios devem ter validação de preenchimento (campo vazio).
- O e-mail deve ser validado para formato padrão (ex: `usuario@dominio.com`).
- A senha deve ter no mínimo 8 caracteres, contendo pelo menos uma letra maiúscula, uma minúscula e um número.
- Após preenchimento válido e submissão, o usuário deve ser redirecionado para uma tela de confirmação de cadastro.
- Dados do usuário devem ser persistidos no banco de dados com sucesso.

Tarefas Detalhadas (com estimativa em horas e responsável):
- [ ] Criar mockups e wireframes da UI do formulário (3h) - Responsável: Diana
- [ ] Implementar front-end da tela de cadastro (HTML/CSS/JS) (12h) - Responsável: Carlos
- [ ] Implementar validação de campos no front-end (JavaScript) (8h) - Responsável: Eduardo
- [ ] Desenvolver serviço de criação de usuário no back-end (API REST) (10h) - Responsável: Carlos
- [ ] Escrever testes unitários para o front-end (4h) - Responsável: Diana
- [ ] Escrever testes unitários para o back-end (5h) - Responsável: Eduardo
- [ ] Realizar teste de integração do fluxo completo (front-end -> back-end -> DB) (3h) - Responsável: Carlos
```

---

## Checklist

- [x] O Product Backlog está priorizado, refinado e com PBIs "Ready" para a sprint?
- [x] A Definition of Done (DoD) foi revisada e compreendida por todos os membros da Development Team?
- [x] A capacidade da Development Team para a sprint foi calculada e comunicada, considerando ausências?
- [x] O Sprint Goal foi claramente definido, é mensurável, coeso e alinhado com o Product Goal?
- [x] Todos os PBIs selecionados para a sprint contribuem diretamente para o alcance do Sprint Goal?
- [x] Os PBIs selecionados foram detalhados em tarefas granulares e estimados pela Development Team?
- [x] A Development Team se comprometeu com o Sprint Goal e com o Sprint Backlog, entendendo o que será entregue?
- [x] Riscos, impedimentos e dependências internas/externas foram identificados e planos de mitigação foram discutidos?
- [x] O Sprint Backlog (PBIs e tarefas) e o Sprint Goal estão visíveis e acessíveis para toda a equipe?
- [x] O tempo limite (time-box) da Sprint Planning foi respeitado (ex: máximo 8h para uma sprint de 4 semanas)?

---

## Métricas de Referência

| Métrica                         | Benchmark     | Meta          |
|---------------------------------|---------------|---------------|
| Velocity (Story Points/Sprint)  | 25-35 SP      | 30 SP (estável) |
| Flutuação de Velocity (%)       | < 20%         | < 10%         |
| Itens Concluídos/Comprometidos (%) | > 80%         | > 95%         |
| Tempo médio de Refinamento (h/sprint) | 4-6h (para 2 semanas) | 4h (para 2 semanas) |
| PBIs "Spill-over" (não concluídos) | < 10%         | < 5%          |
| Satisfação da Equipe com Planning (1-5) | 3.5           | 4.0           |

---

## Erros Comuns

1.  **Excesso de Compromisso (Overcommitment)**: A Development Team seleciona mais PBIs do que sua capacidade real, movida por otimismo ou pressão externa, resultando em PBIs inacabados e desmotivação.
    *   **Como evitar:** Baseie a capacidade da equipe na Velocity média das últimas 3-5 sprints, descontando ausências planejadas (feriados, treinamentos). Use técnicas como Planning Poker para estimativas e respeite o julgamento da equipe sobre sua capacidade. Exemplo: Se a Velocity média é 30 Story Points, resistir à tentação de aceitar 40 SP sem uma justificativa robusta ou um aumento real de capacidade.
2.  **Sprint Goal Vago ou Inexistente**: A Sprint Planning termina sem um objetivo claro e unificador, transformando a sprint em uma coleção de tarefas desconexas. Isso dificulta a priorização durante a sprint e o alinhamento com o Product Goal.
    *   **Como evitar:** O Scrum Master deve facilitar a definição de um único Sprint Goal, conciso e mensurável, no início da planning. O goal deve ser um compromisso que a equipe pode alcançar. Exemplo: Em vez de "Fazer algumas melhorias", defina "Habilitar a busca por produtos com filtros de preço e categoria no e-commerce", garantindo um foco claro.
3.  **Falta de Refinamento Pré-Planning**: O Product Backlog chega à Sprint Planning com PBIs mal definidos, sem estimativas, ou com muitas dúvidas e dependências não resolvidas, consumindo tempo excessivo da planning com esclarecimentos.
    *   **Como evitar:** Dedique tempo contínuo ao Product Backlog Refinement (ex: 10% da capacidade da equipe semanalmente). O Product Owner deve garantir que os PBIs de alta prioridade estejam "Ready" (prontos para serem puxados) antes da Planning. Um PBI "Ready" tem descrição clara, critérios de aceitação, estimativa inicial e dependências identificadas.
4.  **PO Ausente ou Passivo**: O Product Owner não participa ativamente ou não está disponível para esclarecer dúvidas sobre os PBIs, levando a suposições incorretas e retrabalho.
    *   **Como evitar:** O PO é uma presença mandatória na Sprint Planning. Ele deve estar preparado para apresentar os PBIs, responder a perguntas e colaborar na definição do Sprint Goal. Exemplo: O PO deve ter revisado os PBIs e antecipado perguntas comuns da Development Team.

---

## Dicas Avançadas

1.  **"What if" Scenarios na Capacidade:** Durante a discussão e seleção dos PBIs, explore cenários de "e se" para gerenciar riscos de forma proativa. Exemplo: "E se a integração com a API externa (PBI-0057) atrasar ou falhar? Temos um plano de contingência ou um PBI de menor prioridade que podemos substituir?" Isso permite à equipe criar um plano mais robusto e flexível.
2.  **Time-Boxing Rigoroso e Fases Claras:** Divida a Sprint Planning em fases com time-boxes explícitos para manter o foco e otimizar o tempo. Por exemplo, 1 hora para definição do Sprint Goal e seleção inicial de PBIs, 2 horas para detalhamento técnico e estimativas. Exemplo: O Scrum Master anuncia: "Nos próximos 60 minutos, vamos focar exclusivamente na definição do Sprint Goal e na seleção dos PBIs que são essenciais para alcançá-lo. As tarefas e estimativas virão na próxima fase."
3.  **Definição de "Ready" (Definition of Ready - DoR):** Além da Definition of Done (DoD), estabeleça uma Definition of Ready (DoR) clara para os PBIs que podem ser levados para a Sprint Planning. Isso garante que os itens estejam suficientemente maduros. Exemplo de DoR: "Um PBI é 'Ready' se: tem descrição clara, critérios de aceitação definidos, foi estimado pela equipe, dependências externas identificadas e mitigadas, e não excede X Story Points."
4.  **Análise de Tendências de Velocity em vez de Ponto Fixo:** Em vez de usar a Velocity como um número estático, analise sua tendência ao longo do tempo. Uma Velocity em queda pode indicar problemas subjacentes na equipe (excesso de débitos técnicos, desmotivação, interrupções), que precisam ser endereçados, enquanto uma em ascensão pode permitir um compromisso ligeiramente maior. Exemplo: Se a Velocity caiu de 30 para 20 SP nas últimas 3 sprints, a equipe deve discutir as causas na Retrospective antes de se comprometer com 30 SP novamente.
5.  **Simulação de Tarefas Críticas:** Para PBIs de alta complexidade ou risco, a equipe pode realizar uma "simulação" mental ou um mini-spike durante a Planning. Em vez de apenas estimar, eles discutem os passos técnicos chave em maior profundidade, identificando potenciais gargalos ou soluções. Exemplo: Para um PBI de integração com um novo sistema, a equipe pode desenhar brevemente a arquitetura da solução no quadro branco, antes de detalhar as tarefas.

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
