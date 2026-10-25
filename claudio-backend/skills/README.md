# Claudio skills

This is the **skills directory**: `claudio-backend/skills/`.

It is the directory the backend scans for installed Agent Skills. The path
comes from `agent.skills_dir` in `claudio-backend/config/config.yaml`
(default `./skills`), anchored to this `claudio-backend/` folder regardless
of where the backend is launched from.

The original pre-refactor skills directory was lost and is not recoverable
from this repo's git history. Recreate skills here.

## Adding a skill

Each skill is **one folder** with a `SKILL.md`:

```
skills/
└── my-skill/
    ├── SKILL.md          # required
    └── ...               # optional bundled scripts / reference files
```

`SKILL.md` is YAML frontmatter followed by markdown instructions:

```markdown
---
name: my-skill
description: One sentence on when this skill is relevant.
---

Full instructions here. Reference bundled files by paths relative to this
skill's own folder.
```

Rules:
- `name` is lowercase-hyphenated and **must equal the folder name**.
- `description` is required, ≤ 1024 characters.
- Allowed frontmatter keys only: `name`, `description`, `license`,
  `compatibility`, `metadata`, `allowed-tools`.

Only direct child folders are scanned. A folder that fails validation is
reported as broken (via `GET /api/v1/skills`) rather than dropped.

For the full picture — including the workspace location and the path
resolution rules — see [doc/skills-and-workspace.md](../../doc/skills-and-workspace.md).

---

# Skills Manifest

Last refreshed: **2026-10-25**

This section is the credits record for the curated set of merged Agent
Skills in this directory, one per core activity area. Each merged skill is a
superset of the best source skills selected from the
[SkillMD registry](https://skillmd.com). Every skill is attributed to its
original author and remains under its original license; that attribution is
preserved through every merge and recorded below.

On 2026-10-25 the fifteen area summaries from `temp/` (generated from
`todo/claude-code-skills-inventory.md`) were merged in: five areas folded
into existing backend skills (their guidance appended and their source
skills added to the source tables below), and ten areas with no existing
counterpart were created as new skills. The local `python-monkey-patching`
skill was folded into `python-typing-and-tooling`.

To refresh this set, run the prompt in `doc/skills-refresh-prompt.md`,
then validate with `python -m pytest tests/test_skills.py -q`.

## Agent Planning (`agent-planning`)

- **Merged skill:** `agent-planning`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-25
- **Sources merged:** 10

| Owner | Skill | License | Source |
|---|---|---|---|
| jrennie99-glitch | agent-planner | unknown | https://skillmd.com/skills/jrennie99-glitch/agent-planner |
| github | ai-team-orchestration | unknown | https://skillmd.com/skills/github/ai-team-orchestration |
| builderio | visual-plan | unknown | https://skillmd.com/skills/builderio/visual-plan |
| akillness | task-planning | MIT | https://skillmd.com/skills/akillness/task-planning |
| kursku | sprint-planning | MIT | https://skillmd.com/skills/kursku/sprint-planning |
| super-* pack | super-plan-authoring | unknown | temp/planning-and-execution |
| super-* pack | super-plan-execution | unknown | temp/planning-and-execution |
| built-in | workshop | unknown | temp/planning-and-execution |
| super-* pack | super-risk-decision | unknown | temp/planning-and-execution |
| GitHub app | orchestrate | unknown | temp/planning-and-execution |

## Coding (`coding`)

- **Merged skill:** `coding`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-25
- **Sources merged:** 15

| Owner | Skill | License | Source |
|---|---|---|---|
| dokhacgiakhoa | clean-code | unknown | https://skillmd.com/skills/dokhacgiakhoa/clean-code |
| getsentry | code-review | unknown | https://skillmd.com/skills/getsentry/code-review |
| github | refactor | MIT | https://skillmd.com/skills/github/refactor |
| github | sql-code-review | unknown | https://skillmd.com/skills/github/sql-code-review |
| adobe | code-review | Apache-2.0 | https://skillmd.com/skills/adobe/code-review |
| built-in | coding-standards | unknown | temp/code-review-and-standards |
| built-in | review-changes | unknown | temp/code-review-and-standards |
| built-in | artifact-pr-review | unknown | temp/code-review-and-standards |
| super-* pack | super-code-review | unknown | temp/code-review-and-standards |
| GitHub app | impeccable | unknown | temp/code-review-and-standards |
| built-in | fix-lint | unknown | temp/code-review-and-standards |
| GitHub app | pr-stack | unknown | temp/code-review-and-standards |
| GitHub app | agent-merge | unknown | temp/code-review-and-standards |
| built-in | commit-push-pr | unknown | temp/code-review-and-standards |
| built-in | ticket-analyzer | unknown | temp/code-review-and-standards |

## Software Architecture (`software-architecture`)

- **Merged skill:** `software-architecture`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-23
- **Sources merged:** 5

| Owner | Skill | License | Source |
|---|---|---|---|
| github | architecture-blueprint-generator | unknown | https://skillmd.com/skills/github/architecture-blueprint-generator |
| alirezarezvani | design-system | unknown | https://skillmd.com/skills/alirezarezvani/design-system |
| cloudai-x | designing-architecture | unknown | https://skillmd.com/skills/cloudai-x/designing-architecture |
| anthropic | canvas-design | Complete terms in LICENSE.txt | https://skillmd.com/skills/anthropic/canvas-design |
| google-labs-code | stitch-extract-design-md | unknown | https://skillmd.com/skills/google-labs-code/stitch-extract-design-md |

## Linux Skills (`linux`)

- **Merged skill:** `linux`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-25
- **Sources merged:** 9

| Owner | Skill | License | Source |
|---|---|---|---|
| github | centos-linux-triage | unknown | https://skillmd.com/skills/github/centos-linux-triage |
| antigravity | bash-linux | unknown | https://skillmd.com/skills/antigravity/bash-linux |
| dokhacgiakhoa | bash-linux | unknown | https://skillmd.com/skills/dokhacgiakhoa/bash-linux |
| neuralblitz | bash | MIT | https://skillmd.com/skills/neuralblitz/bash |
| github | fedora-linux-triage | unknown | https://skillmd.com/skills/github/fedora-linux-triage |
| super-* pack | super-linux-ops | unknown | temp/linux-ops-and-security |
| super-* pack | super-linux-triage | unknown | temp/linux-ops-and-security |
| super-* pack | super-linux-hardening | unknown | temp/linux-ops-and-security |
| claude-security | claude-security | unknown | temp/linux-ops-and-security |

## Agent Tools Usage (`agent-tools-usage`)

- **Merged skill:** `agent-tools-usage`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-25
- **Sources merged:** 12

| Owner | Skill | License | Source |
|---|---|---|---|
| github | mcp-create-declarative-agent | unknown | https://skillmd.com/skills/github/mcp-create-declarative-agent |
| jrennie99-glitch | agent-user-tools | unknown | https://skillmd.com/skills/jrennie99-glitch/agent-user-tools |
| github | mcp-cli | unknown | https://skillmd.com/skills/github/mcp-cli |
| github | python-mcp-server-generator | unknown | https://skillmd.com/skills/github/python-mcp-server-generator |
| microsoft | mcp-builder | unknown | https://skillmd.com/skills/microsoft/mcp-builder |
| mcp-server-dev | build-mcp-server | unknown | temp/mcp-integration |
| mcp-server-dev | build-mcp-app | unknown | temp/mcp-integration |
| mcp-server-dev | build-mcpb | unknown | temp/mcp-integration |
| plugin-dev | mcp-integration | unknown | temp/mcp-integration |
| GitHub app | connector-finder | unknown | temp/mcp-integration |
| GitHub app | agentfinder | unknown | temp/mcp-integration |
| GitHub app | af | unknown | temp/mcp-integration |

## Debugging (`debugging`)

- **Merged skill:** `debugging`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-23
- **Sources merged:** 5

| Owner | Skill | License | Source |
|---|---|---|---|
| haongo232 | debug | unknown | https://skillmd.com/skills/haongo232/debug |
| github | power-bi-performance-troubleshooting | unknown | https://skillmd.com/skills/github/power-bi-performance-troubleshooting |
| georgeqle | debug | unknown | https://skillmd.com/skills/georgeqle/debug |
| nvidia | dynamo-troubleshoot | Apache-2.0 | https://skillmd.com/skills/nvidia/dynamo-troubleshoot |
| projectious-work | debugging | unknown | https://skillmd.com/skills/projectious-work/debugging |

## Information Gathering (`information-gathering`)

- **Merged skill:** `information-gathering`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-25
- **Sources merged:** 8

| Owner | Skill | License | Source |
|---|---|---|---|
| browserbase | search | MIT | https://skillmd.com/skills/browserbase/search |
| mattpocock | research | unknown | https://skillmd.com/skills/mattpocock/research |
| gonglingrui | web-search | MIT | https://skillmd.com/skills/gonglingrui/web-search |
| emaduneme | information-gathering | unknown | https://skillmd.com/skills/emaduneme/information-gathering |
| seb1n | summarization | MIT | https://skillmd.com/skills/seb1n/summarization |
| third-party Python package | browser-use | unknown | temp/browser-automation-and-web-research |
| third-party Python package | browser_harness | unknown | temp/browser-automation-and-web-research |
| built-in | deep-research | unknown | temp/browser-automation-and-web-research |

## New areas added 2026-10-25

The following ten areas had no existing backend skill and were created as
new merged skills from the `temp/` inventory.

## Claude Extension Authoring (`claude-extension-authoring`)

- **Merged skill:** `claude-extension-authoring`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-25
- **Sources merged:** 19
- **Scope:** Authoring skills, slash commands, subagents, hooks, plugin
  scaffolding, plugin settings, hookify rules, automation recommendations
  and first-time setup.

| Owner | Skill | License | Source |
|---|---|---|---|
| plugin-dev | skill-development | unknown | temp/claude-extension-authoring |
| skill-creator | skill-creator | unknown | temp/claude-extension-authoring |
| built-in | run-skill-generator | unknown | temp/claude-extension-authoring |
| built-in | workflow-authoring | unknown | temp/claude-extension-authoring |
| example-plugin | example-skill | unknown | temp/claude-extension-authoring |
| example-plugin | example-command | unknown | temp/claude-extension-authoring |
| plugin-dev | plugin-structure | unknown | temp/claude-extension-authoring |
| plugin-dev | plugin-settings | unknown | temp/claude-extension-authoring |
| built-in | plugin-authoring | unknown | temp/claude-extension-authoring |
| plugin-dev | agent-development | unknown | temp/claude-extension-authoring |
| plugin-dev | command-development | unknown | temp/claude-extension-authoring |
| plugin-dev | hook-development | unknown | temp/claude-extension-authoring |
| hookify | writing-hookify-rules | unknown | temp/claude-extension-authoring |
| claude-code-setup | claude-automation-recommender | unknown | temp/claude-extension-authoring |
| built-in | setup-claude | unknown | temp/claude-extension-authoring |
| built-in | import-to-claude-code | unknown | temp/claude-extension-authoring |
| Copilot CLI | agent-customization | unknown | temp/claude-extension-authoring |
| Copilot CLI | migrate-customizations | unknown | temp/claude-extension-authoring |
| GitHub app | factory-creator | unknown | temp/claude-extension-authoring |

## Documents & Knowledge Artifacts (`documents-and-knowledge-artifacts`)

- **Merged skill:** `documents-and-knowledge-artifacts`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-25
- **Sources merged:** 13
- **Scope:** PDF/DOCX/XLSX/PPTX/LaTeX production, explainers, reports,
  data tables, meeting notes, project status artifacts and CLAUDE.md
  quality.

| Owner | Skill | License | Source |
|---|---|---|---|
| built-in | pdf | unknown | temp/documents-and-knowledge-artifacts |
| built-in | docx | unknown | temp/documents-and-knowledge-artifacts |
| built-in | xlsx | unknown | temp/documents-and-knowledge-artifacts |
| built-in | pptx | unknown | temp/documents-and-knowledge-artifacts |
| GitHub app | latex | unknown | temp/documents-and-knowledge-artifacts |
| built-in | artifact-explainer | unknown | temp/documents-and-knowledge-artifacts |
| built-in | artifact-report | unknown | temp/documents-and-knowledge-artifacts |
| built-in | artifact-data-table | unknown | temp/documents-and-knowledge-artifacts |
| built-in | artifact-capabilities | unknown | temp/documents-and-knowledge-artifacts |
| built-in | meeting-notes | unknown | temp/documents-and-knowledge-artifacts |
| project-artifact | project-artifact | unknown | temp/documents-and-knowledge-artifacts |
| claude-md-management | claude-md-improver | unknown | temp/documents-and-knowledge-artifacts |
| MS Python ext | pylance-docs | unknown | temp/documents-and-knowledge-artifacts |

## Embedded Devices (`embedded-devices`)

- **Merged skill:** `embedded-devices`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-25
- **Sources merged:** 2
- **Scope:** M5Stack/ESP32 onboarding and Cardputer MicroPython app
  iteration.

| Owner | Skill | License | Source |
|---|---|---|---|
| cwc-makers | m5-onboard | unknown | temp/embedded-devices |
| cwc-makers | cardputer-buddy | unknown | temp/embedded-devices |

## Mathematics (`mathematics`)

- **Merged skill:** `mathematics`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-25
- **Sources merged:** 3
- **Scope:** Olympiad problem solving and sustained proof work (siege and
  solo).

| Owner | Skill | License | Source |
|---|---|---|---|
| math-olympiad | math-olympiad | unknown | temp/mathematics |
| math-proof | siege | unknown | temp/mathematics |
| math-proof | solo | unknown | temp/mathematics |

## Messaging Channel Integration (`messaging-channel-integration`)

- **Merged skill:** `messaging-channel-integration`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-25
- **Sources merged:** 6
- **Scope:** Discord / Telegram / iMessage setup, pairing, allowlists and
  access policy.

| Owner | Skill | License | Source |
|---|---|---|---|
| discord | configure | unknown | temp/messaging-channel-integration |
| discord | access | unknown | temp/messaging-channel-integration |
| telegram | configure | unknown | temp/messaging-channel-integration |
| telegram | access | unknown | temp/messaging-channel-integration |
| imessage | configure | unknown | temp/messaging-channel-integration |
| imessage | access | unknown | temp/messaging-channel-integration |

## Python Typing & Tooling (`python-typing-and-tooling`)

- **Merged skill:** `python-typing-and-tooling`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-25
- **Sources merged:** 11
- **Scope:** Type annotations, stubs, refactoring, version support,
  environment & paths, profiling, and safe monkey-patching.

| Owner | Skill | License | Source |
|---|---|---|---|
| Copilot CLI | pylance-refactoring | unknown | temp/python-typing-and-tooling |
| Copilot CLI | python-write-stubs | unknown | temp/python-typing-and-tooling |
| Copilot CLI | python-add-type-annotations | unknown | temp/python-typing-and-tooling |
| Copilot CLI | python-type-inference | unknown | temp/python-typing-and-tooling |
| Copilot CLI | python-fact-grounded-coding | unknown | temp/python-typing-and-tooling |
| Copilot CLI | add-python-version-support | unknown | temp/python-typing-and-tooling |
| Copilot CLI | drop-python-version-support | unknown | temp/python-typing-and-tooling |
| MS Python ext | python-manager-discovery | unknown | temp/python-typing-and-tooling |
| MS Python ext | cross-platform-paths | unknown | temp/python-typing-and-tooling |
| MS Python ext | pylance-python-profiling | unknown | temp/python-typing-and-tooling |
| local | python-monkey-patching | unknown | folded in 2026-10-25 |

## Testing & Verification (`testing-and-verification`)

- **Merged skill:** `testing-and-verification`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-25
- **Sources merged:** 9
- **Scope:** E2E/behavior verification, failing tests, smoke/integration/
  pre-commit runs and snapshots.

| Owner | Skill | License | Source |
|---|---|---|---|
| built-in | verify | unknown | temp/testing-and-verification |
| super-* pack | super-behavior-testing | unknown | temp/testing-and-verification |
| super-* pack | super-implementation-loop | unknown | temp/testing-and-verification |
| MS Python ext | debug-failing-test | unknown | temp/testing-and-verification |
| MS Python ext | run-e2e-tests | unknown | temp/testing-and-verification |
| MS Python ext | run-integration-tests | unknown | temp/testing-and-verification |
| MS Python ext | run-smoke-tests | unknown | temp/testing-and-verification |
| MS Python ext | run-pre-commit-checks | unknown | temp/testing-and-verification |
| MS Python ext | generate-snapshot | unknown | temp/testing-and-verification |

## Usage Analytics & Cost (`usage-analytics-and-cost`)

- **Merged skill:** `usage-analytics-and-cost`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-25
- **Sources merged:** 4
- **Scope:** Usage/impact reports, session reports, cost explanation and
  agent diagnostics from local transcripts.

| Owner | Skill | License | Source |
|---|---|---|---|
| receipts | receipts | unknown | temp/usage-analytics-and-cost |
| session-report | session-report | unknown | temp/usage-analytics-and-cost |
| built-in | explain-usage | unknown | temp/usage-analytics-and-cost |
| Copilot CLI | troubleshoot | unknown | temp/usage-analytics-and-cost |

## Visual Design & Artifacts (`visual-design-and-artifacts`)

- **Merged skill:** `visual-design-and-artifacts`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-25
- **Sources merged:** 9
- **Scope:** Aesthetic direction, artifact design, dataviz, whiteboard,
  playgrounds and prototypes.

| Owner | Skill | License | Source |
|---|---|---|---|
| frontend-design | frontend-design | unknown | temp/visual-design-and-artifacts |
| built-in | prototype | unknown | temp/visual-design-and-artifacts |
| built-in | artifact-design | unknown | temp/visual-design-and-artifacts |
| built-in | artifact-components | unknown | temp/visual-design-and-artifacts |
| built-in | dataviz | unknown | temp/visual-design-and-artifacts |
| built-in | whiteboard | unknown | temp/visual-design-and-artifacts |
| playground | playground | unknown | temp/visual-design-and-artifacts |
| built-in | design-sync | unknown | temp/visual-design-and-artifacts |
| GitHub app | create-canvas | unknown | temp/visual-design-and-artifacts |

## Web & CLI Frameworks (`web-and-cli-frameworks`)

- **Merged skill:** `web-and-cli-frameworks`
- **License:** mixed — see Credits
- **Last reviewed:** 2026-10-25
- **Sources merged:** 2
- **Scope:** FastAPI and Typer.

| Owner | Skill | License | Source |
|---|---|---|---|
| third-party Python package | fastapi | unknown | temp/web-and-cli-frameworks |
| third-party Python package | typer | unknown | temp/web-and-cli-frameworks |

---

## Attribution

All skills are the work of their respective authors and are used under their
original licenses. SkillMD is the distribution registry; the original source
repository for each skill is linked above where available. The `temp/`
summaries were generated from `todo/claude-code-skills-inventory.md`; where
a source's license is recorded as `unknown`, treat it as unlicensed-until-
confirmed and preserve the attribution. If you reuse these skills, preserve
the attribution and license terms of each source.
