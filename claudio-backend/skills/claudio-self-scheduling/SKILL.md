---
name: claudio-self-scheduling
description: Create, update, enable and verify scheduled tasks that Claudio runs on a cron schedule. Use when asked to schedule anything, set up recurring jobs, add a periodic check, or when a scheduled task appears not to be firing.
---

# Claudio Self-Scheduling

Claudio creating his own recurring work. Verified against the live backend
2026-10-23. Client: `scripts/claudio_api.py`.

## The task contract

`POST /autonomous/tasks` with:

```json
{
  "key": "nightly-audit",
  "name": "Nightly system audit",
  "type": "prompt",
  "prompt": "<the playbook instructions the task agent should follow>",
  "schedule": "17 3 * * *",
  "notify_on": ["warning", "critical", "failure"],
  "report_only": true,
  "enabled": true
}
```

| Field | Rule |
|---|---|
| `key` | `^[a-z0-9][a-z0-9-]*$` — lowercase letters, digits, hyphens only. Must be unique. |
| `type` | `"prompt"` (agent runs a playbook) or `"script"` (raw shell, no agent) |
| `prompt` | Required and non-empty for `type=prompt` |
| `script` | Required and non-empty for `type=script` |
| `schedule` | Valid 5-field cron |
| `notify_on` | Subset of `ok`, `warning`, `critical`, `failure` |
| `report_only` | `true` = no mutations. **Default true.** |
| `enabled` | Default `true` |

**Verdicts a task can return:** `ok` / `warning` / `critical` / `failure` / `skipped`.

## The gotcha that will bite you first

**Invalid cron returns HTTP 500, not 422.** The backend's validation error is not
JSON-serializable, so you get `{"code":"internal_error","message":"Object of type
ValueError is not JSON serializable"}` instead of a useful message.

Validate the cron expression yourself **before** posting:

```python
from croniter import croniter
assert croniter.is_valid("17 3 * * *"), "bad cron"
```

Or sanity-check against reality — what will this actually fire at?
```python
from datetime import datetime, timezone
from croniter import croniter
print(croniter("17 3 * * *", datetime.now(timezone.utc)).get_next(datetime))
```

## The gotcha that will bite you second

**`autonomous.enabled` gates the scheduler.** If it is `false`, you can create
tasks, and `POST .../run` will still work manually, but **nothing fires on
schedule**. Check it first:

```bash
python3 scripts/claudio_api.py get /autonomous/status   # look at "enabled"
```

If you're scheduling real recurring work and `enabled` is false, say so and get it
turned on — otherwise you have built a clock that isn't plugged in.

**Missed fires are skipped, not caught up.** The next fire is always computed from
`now`. A machine that slept through three runs wakes up for the *next* one. Do not
rely on a schedule to guarantee a run happened.

## Scheduling discipline

### 1. Design the task as a report, not a chore
A scheduled task has no human watching it. Its output must be self-explanatory:
what it checked, what it found, what verdict, what it changed (if anything).
Write the prompt so a reader seeing only the report knows whether to act.

### 2. Start report-only
Default `report_only: true` is correct. Run a new task in report-only mode for
enough cycles to trust its judgement before enabling mutations. Effective mode is
**conservative OR**: if the global `autonomous.report_only` is true, your task is
report-only regardless of its own flag.

### 3. Schedule so you can tell tasks apart
Don't put everything at `0 3 * * *`. Stagger minutes (`17 3`, `43 3`) so logs
and load don't collide, and so a failure is attributable.

### 4. Mind the timeouts
| Kind | Wall-clock ceiling |
|---|---|
| Agent (prompt) task | 900s (15 min) |
| Built-in (maintenance/recovery) | 1800s (30 min) |
| Script task | 300s (5 min) |
| Single elevated command | 600s (10 min) |

A task that exceeds its ceiling is killed and reported `critical`. Design under it.

### 5. Overlap is skipped, not queued
If a task is still running when it's due again, the new run is **skipped** with
verdict `skipped`. If you see skips, the task takes longer than its interval —
shorten the work or lengthen the schedule.

## Procedure for creating a task

1. Decide: what does this task check, and what verdict does it return?
2. Validate the cron expression locally (see above).
3. `POST /autonomous/tasks` with the payload.
4. **Run it once manually**: `POST /autonomous/tasks/{key}/run` — never ship an
   untested schedule.
5. `GET /autonomous/tasks/{key}/runs` — read the actual output.
6. `GET /autonomous/tasks` — confirm `next_run` is the time you expected.
7. Confirm `autonomous.enabled` is true, or state that it isn't.

Step 4 is not optional. A schedule you have never seen fire is a hypothesis.

## Managing existing tasks

```bash
python3 scripts/claudio_api.py get   /autonomous/tasks              # list + next_run
python3 scripts/claudio_api.py put   /autonomous/tasks/<key> --body '{"schedule":"0 */6 * * *"}'
python3 scripts/claudio_api.py post  /autonomous/tasks/<key>/disable
python3 scripts/claudio_api.py post  /autonomous/tasks/<key>/enable
python3 scripts/claudio_api.py post  /autonomous/tasks/<key>/run    # manual fire
python3 scripts/claudio_api.py get   /autonomous/tasks/<key>/runs   # history
python3 scripts/claudio_api.py delete /autonomous/tasks/<key>
```

Every mutation calls `task_scheduler.wake()`, so changes take effect immediately
rather than on the next 300s cap tick.

## When a task isn't firing — triage

1. `GET /autonomous/status` → is `enabled` true?
2. `GET /autonomous/tasks` → is this task's `enabled` true? Is `next_run` sensible?
3. `GET /autonomous/tasks/{key}/runs` → when did it last run? What verdict?
4. `GET /autonomous/audit` → is anything else touching it?
5. Check the clock: `timedatectl`. Cron is meaningless with clock skew.
6. Check the schedule math yourself — `croniter` in a REPL.

## Script tasks — extra caution

`type: "script"` runs **arbitrary shell as the backend user**, outside the agent
loop, with a 300s timeout. It is a deliberate operator capability, not a sandbox.

- Prefer `prompt` tasks. They are scoped to validated tools.
- Use `script` only when the work is deterministic and doesn't need judgement.
- Write the script to a file first, test it by hand, *then* put it in the task.
- `set -euo pipefail`, absolute paths, explicit exit codes.
- It is skipped entirely while report-only is in effect.
