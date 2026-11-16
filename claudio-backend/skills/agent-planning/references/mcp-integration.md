# MCP tool integration & setup



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
