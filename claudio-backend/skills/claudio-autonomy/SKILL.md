---
name: claudio-autonomy
description: Orchestrate Claudio's full self-management loop - self-check, self-configuration, scheduled tasks, learning from experience, and safe escalation. Use when asked to become autonomous, to set up self-improvement, to run a standing operational loop, or to decide whether to act without asking.
---

# Claudio Autonomy

The operating loop that ties self-check, self-config and self-scheduling into
something that actually runs unattended. Verified against the live backend
2026-10-23. Clients: `scripts/claudio_api.py`, `scripts/autonomy.py`.

## The loop

```
   ┌──────────────────────────────────────────────────┐
   │  1. CHECK      claudio-self-check                │
   │       ↓                                          │
   │  2. INTERPRET  what changed? what is new?        │
   │       ↓                                          │
   │  3. ACT        only within your authority        │
   │       ↓                                          │
   │  4. VERIFY     re-check; did the action work?    │
   │       ↓                                          │
   │  5. RECORD     journal the finding + outcome     │
   │       ↓                                          │
   │  6. LEARN      update your own skills/playbooks  │
   └──────────────────────────────────────────────────┘
```

Step 6 is what makes this self-improvement rather than a cron job. Steps 1–5
without 6 is a watchdog. Steps 1–6 is a learner.

## The authority ladder

Autonomy is not a switch, it is a gradient. Know which rung you are on for each
action.

| Rung | Action class | Requires |
|---|---|---|
| **1. Observe** | Read config, logs, metrics, health | Nothing. Always safe. Do it freely. |
| **2. Report** | Write a report, journal a finding | Nothing. Do it freely. |
| **3. Reversible local** | Edit a file you own, create a report artifact | Do it, record it. |
| **4. Reversible system** | Create/edit a task, change a tunable | Do it, verify it, record it. |
| **5. Irreversible / security** | Delete data, change `approval_mode`, `auth_token`, firewall, run elevated | **Ask first.** No exceptions. |

**The test for rung 5:** can you undo it, and would a reasonable operator be
surprised? If either answer is "yes", ask.

## What is actually safe right now

Verified state of this install:

- `autonomous.enabled = false` → **scheduled tasks do not fire.** Creating tasks
  without flipping this is building a clock that isn't plugged in.
- `autonomous.report_only = true` → **no mutations happen.** This is conservative
  composition: global report-only overrides any task's own `report_only: false`.
- `server.expose_raw_config = false` → raw secrets not readable via API. Good.
- `agent.approval_mode = off` → chat runs without approval prompts.

**Do not change any of these without asking.** They are the safety envelope.

## Bootstrap procedure (what to actually do)

### Phase 1 — Establish the baseline (safe, do immediately)
```bash
python3 scripts/claudio_api.py snapshot > baseline-$(date +%F).json
```
Read every field. You cannot detect drift against a baseline you never took.

### Phase 2 — Install the skills (safe, reversible)
The backend loads skills from `agent.skills_dir` (currently
`claudio-backend/skills`). Writing to the workspace does **not** install them.
```bash
cp -r <workspace>/skills/* /run/media/briggen/DEV/code/ai/claudio/claudio-backend/skills/
python3 scripts/claudio_api.py get /skills      # verify the count changed
```
Validate before deploying:
```python
from claudio_core.skill_loader import scan_skills
scan_skills("skills")   # raises on malformed SKILL.md
```

### Phase 3 — Create the standing tasks (report-only first)
Start with observation tasks only. Suggested set:

| Task | Schedule | Purpose |
|---|---|---|
| `self-check` | `*/30 * * * *` | Health sweep, journal the verdict |
| `disk-watch` | `17 * * * *` | Disk growth trend before it's critical |
| `log-scan` | `43 * * * *` | New error patterns in journal |
| `weekly-review` | `7 6 * * 1` | Review the week's findings, update playbooks |

Create each with `report_only: true`, run it once manually, read the output,
**then** trust it.

### Phase 4 — Turn on autonomy (ASK FIRST)
This is rung 5. Requires explicit operator consent:
- `autonomous.enabled = true`
- Any task with `report_only: false`
- Any notification channel

Present: what will run, how often, what it can change, how to stop it.

### Phase 5 — Learn (the actual self-improvement)
After each cycle:
- Did the check find something new? → write it into the relevant skill.
- Did a task produce a useless report? → rewrite the prompt.
- Did a task fail twice? → the diagnosis is wrong; stop and re-derive.
- Did you discover a fact about this system not in any skill? → add it.

**Skills are the memory. If you learn it and don't write it down, you didn't learn
it — you just experienced it.**

## The kill switch

Before enabling anything, make sure you know how to stop it:
```bash
python3 scripts/claudio_api.py put /config --body '{"autonomous":{"enabled":false}}'
python3 scripts/claudio_api.py post /autonomous/tasks/<key>/disable
```
State this in your report. An autonomy setup without a stated stop path is a
liability.

## Guardrails that are not negotiable

1. **Never disable your own safety controls to make something work.** If
   `report_only` blocks a task, the answer is not to turn it off — it's to ask.
2. **Never widen an allowlist as a fix.** That's removing the guardrail, not
   fixing the problem.
3. **Never delete to fix disk pressure without an owner decision.** Caches yes,
   data/state/credentials never.
4. **Never trust your own last report.** Re-check. A report is a snapshot, not a
   live state.
5. **Escalate rather than improvise.** An unattended agent that improvises on
   infrastructure is a liability. Ask.
6. **Budget.** Check `cost_usd_total` and `runs_failed` each cycle. A runaway loop
   shows up there first.

## What "self-improving" should not mean

Not: rewriting your own instructions until they drift into something nobody
reviewed. Not: widening your own permissions because a task was inconvenient.
Not: silent self-modification that leaves an operator unable to predict what you
will do.

It should mean: **getting better at noticing, reporting, and handling the specific
things this system actually does**, recorded in writing, reviewable by a human.

Every improvement should be visible in a diff.
