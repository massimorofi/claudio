# Project bootstrap & context recovery



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
