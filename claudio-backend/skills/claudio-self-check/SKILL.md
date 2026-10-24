---
name: claudio-self-check
description: Run a health check on Claudio himself - services, config drift, skills loading, task scheduling, cost, and error logs - and turn findings into a verdict with evidence. Use when asked "are you healthy?", after any config change, or as the core of a recurring self-audit.
---

# Claudio Self-Check

Claudio checking himself. Verified against the live backend 2026-10-23.
Client: `scripts/claudio_api.py`. One command gives you the whole picture:

```bash
python3 scripts/claudio_api.py snapshot
```

## The seven checks

### 1. Is the backend alive at all?
```bash
curl -s http://127.0.0.1:8100/healthz          # unauthenticated liveness
python3 scripts/claudio_api.py get /version     # backend/engine/protocol
```
If `/healthz` fails, nothing else matters. Go to process triage.

### 2. Component health
```bash
python3 scripts/claudio_api.py get /monitoring/health
```
Returns `supervisor`, `agent`, `llm`, `db`, `overall`. Any non-`ok` is a finding.

### 3. Autonomous subsystem state — check this first, always
```bash
python3 scripts/claudio_api.py get /autonomous/status
```
Look at, in order:
- `enabled` — **if false, no scheduled work runs at all.** This is the single most
  common cause of "my tasks never fire."
- `report_only` — if true, no mutations happen anywhere.
- `health_check_enabled`, `interval_minutes`
- `notification_enabled`, `notification_channels` — an alert system that can't
  deliver is worse than none, because you believe you're covered.

**Trap: `enabled` gates the health check too.** `HealthScheduler._enabled()` is
`autonomous.enabled AND health_check.enabled` — both must be true. So with
`autonomous.enabled=false`, `health_check_enabled=true` is misleading: the check
is configured but never runs, and `last_check` goes stale silently. Check the
`created_at` of the last check against the current time. If it's old, the
scheduler is off, not the check.

### 4. Host health
```bash
python3 scripts/claudio_api.py get /autonomous/health
python3 scripts/claudio_api.py get /autonomous/health/history
```
Disk, memory, load, failed units, journal errors. Compare against history: a
number that moved is more informative than a number that is merely high.

### 5. Issues
```bash
python3 scripts/claudio_api.py get /autonomous/issues
```
For each: read the actual detail, don't stop at the title. Then decide —
acknowledge, resolve, or propose a fix:
```
POST /autonomous/issues/{id}/acknowledge
POST /autonomous/issues/{id}/resolve
POST /autonomous/issues/{id}/propose-fix
GET  /autonomous/issues/{id}/suggestions
POST /autonomous/suggestions/{id}/accept
POST /autonomous/suggestions/{id}/dismiss
```

### 6. Skills actually loaded
```bash
python3 scripts/claudio_api.py get /skills
```
Returns `directory`, `enabled`, and the `skills` list. **Compare the count to
what you expect.** If you wrote ten skills and this shows zero, they are not
installed — writing a file to the workspace is not the same as making it available.

```bash
# where the backend actually looks
python3 scripts/claudio_api.py get /skills | python3 -c "import sys,json;print(json.load(sys.stdin)['directory'])"
```

Validate a skill folder offline before deploying it:
```python
from claudio_core.skill_loader import scan_skills
scan = scan_skills("path/to/skills")   # raises on malformed SKILL.md
print(len(scan.skills), list(scan.skills))
```
Allowed frontmatter keys only: `name`, `description`, `license`, `compatibility`,
`metadata`, `allowed-tools`. Anything else → the skill is rejected.

### 7. Cost and volume
```bash
python3 scripts/claudio_api.py get /monitoring/metrics
```
`runs_total`, `runs_failed`, `tokens_in/out`, `cost_usd_total`, `tool_errors_total`,
`llm_errors_total`, `queue_depth`. Rising `runs_failed` or `tool_errors_total` is
a trend worth chasing before it becomes an incident.

Also: `GET /monitoring/logs`, `GET /monitoring/runs`.

## The self-check report

```
## Self-check: <timestamp>  VERDICT: ok | warning | critical

### Alive            backend <ver>, agent <state>, uptime <h>
### Components       supervisor/agent/llm/db: <statuses>
### Autonomous       enabled=<bool> report_only=<bool> interval=<n>
### Host             disk <n>% mem <n>% load <n> failed_units <n>
### Issues           <n> open: <one line each>
### Skills           <n> loaded from <dir>   [expected <n>]
### Cost/volume      runs <n> (failed <n>)  tokens <n>  cost $<n>
### Findings
1. <finding> — <evidence> — <recommended action>
### Not checked
- <what, and what it would take>
```

**A check with no evidence is an opinion.** Paste the output that could have failed.
If you didn't run it, it goes under "Not checked", not under "ok".

## Honesty rules for self-audit

1. **You cannot verify yourself with yourself.** If the thing you're checking is the
   thing doing the checking, get an independent signal (an external command, a
   second endpoint, a fresh session).
2. **Report what you find, not what you fixed.** A self-check that silently repairs
   and reports "all green" hides the incident.
3. **Escalate what you can't fix.** `report_only` exists so that finding problems
   is always safe and fixing them is a separate, deliberate decision.
4. **State the blind spots.** Every check has a scope; naming it is part of the result.

## After any config change

Minimum check: `/monitoring/health` + `/config` (did it apply?) +
`restart_required` (from the PUT response) + `/skills` (did anything break?).
A change that "worked" and a change that was *verified* are different claims.

## Fixing a failed systemd unit (the complete procedure)

A failed unit is not one command deep. Doing only the obvious step is what leaves
the failure in the logs after a "successful" fix. Work through all of it:

**1. Find the real unit name and the real cause.**
```bash
systemctl --failed --plain --no-legend --no-pager   # --plain: without it the row starts with the ● glyph
systemctl status <unit> --no-pager                # read the exit code, not just "failed"
systemctl cat <unit>                              # what it actually runs
```
`status=203/EXEC` means the executable is gone (package removed) — the unit is a
stale leftover, not a service to repair. `status=2/INVALIDARGUMENT` after a
timeout usually means a helper script bailed out.

**2. Find what re-triggers it.** A `.service` is usually woken by a `.timer` or a
`.path`, and stopping the service alone does nothing.
```bash
systemctl list-timers --all | grep <name>
systemctl list-units --all | grep <name>
systemctl show <unit> | grep -E 'TriggeredBy|WantedBy|RequiredBy'
```
When you disable or mask a service, systemd *prints* the triggering units:
`"but its triggering units are still active: foo.timer, foo.path"`. **That line is
the next instruction, not a warning to scroll past.**

**3. Stop and mask the triggers, then the service.**
```bash
sudo systemctl stop    <unit>.timer <unit>.path
sudo systemctl mask    <unit>.timer <unit>.path
sudo systemctl stop    <unit>
sudo systemctl mask    <unit>
```

**4. Clear the remembered failure.** Masking stops future runs; it does **not**
clear the existing failed state. Without this the unit still shows red:
```bash
sudo systemctl reset-failed <unit>
systemctl --failed --plain --no-legend --no-pager   # must print nothing
```

**5. Verify with a time-bounded query, not a line count.**
```bash
journalctl --since "5 min ago" -p err --no-pager
```
If you check `journalctl -p err -n 200` you are reading history, and old errors
will still be there no matter how well you fixed things.

## Deciding between masking and removing

Masking a unit whose package has been removed leaves a permanently broken stub.
If the software is gone (`systemctl cat` → not-found, executable missing), remove
the leftovers properly:
```bash
dpkg -l | grep <pkg>              # is it still installed?
sudo apt purge <pkg>              # removes unit files too
sudo systemctl reset-failed       # clear everything left in memory
```

## sudo without a TTY

Passwordless sudo is **not** configured on this host. Any privileged command run
non-interactively fails with `sudo: Authentication required but not attempted`.
Before proposing privileged fixes, check:
```bash
sudo -n true 2>&1 && echo "passwordless OK" || echo "needs password"
```
If it needs a password, a fix that requires root cannot be executed by the agent
at all — hand the exact command to the operator instead of iterating on it.
