# Team roles & chat architecture



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
