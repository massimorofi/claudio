# Output format & collaboration



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

## Best practices
1. Choose one primary packet before decomposing the work.
2. Keep discovery separate from delivery whenever requirements are unstable.
3. Prefer small, reviewable slices over broad work categories.
4. Surface blockers and missing inputs explicitly instead of burying them in notes.
5. Preserve domain nuance for developer workflow, web/fullstack, product/ops, marketing/GTM, and game work without bloating the front door.
6. Route sizing, board control, review, daily cadence, and retrospectives out instead of stretching the skill boundary.
7. Keep the packet compact enough that a team can act on it immediately.
8. Update compact and manifest discovery surfaces when the role wording changes materially.
