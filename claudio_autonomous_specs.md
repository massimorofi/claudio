# Claudio — Autonomous Server Management — Specification

Status: **PROPOSED** (design only — not implemented). Companion to `claudio_specs_agent.md` (agent capabilities) and `claudio_specs_workflow.md` (workflow view).

This spec describes running Claudio as an **autonomous Ubuntu server-management agent** *while keeping the chat app fully intact*. The backend serves multiple surfaces at once: the **Chat** role (free, interactive, as today), the **Daemon** role (scheduled, scoped, scripted), and a **Telegram** channel (mobile chat, alerts, and approvals). The Chat UI doubles as the **cockpit** where the operator sets up the daemon, schedules actions, and watches issues, suggestions, and status.

It is written against the current Claudio codebase and is explicit about what already exists versus what must be built.

---

## 1. Vision and scope

Claudio today is a **chat application**: a human sends a message, the agent acts, the human watches. This spec adds a second mode — **scheduled, unattended runs** that inspect and maintain the host — *without removing or constraining the chat*. Both run on the same backend, side by side, and a Telegram channel brings the whole thing to the operator's phone.

In scope:
- Run the backend as a managed service that starts on boot and survives crashes.
- **Two roles** on one backend: Chat (interactive) and Daemon (autonomous) — §2.
- Scheduled **health checks** (read-only diagnostics + report) — §7.
- Scheduled **maintenance** (package updates, disk cleanup, log rotation) — §8.
- **Boot-time disaster recovery** (review the system after a shutdown, take safe recovery actions) — §9.
- **Notification** so the agent can alert the operator when something is wrong — §10.2.
- A **cockpit / administration UI** in the chat app to configure the daemon, schedule tasks, and visualize issues, suggestions, and status — §11.
- A **Telegram channel** for mobile chat, alerts, and approve/deny from your phone — §12.

Out of scope (deliberately — see §15):
- Unrestricted autonomous root administration.
- Freeform shell execution without an allowlist.
- Recovery that improvises destructive actions.

---

## 2. Two roles: Chat and Daemon

The governing architecture is **one backend, two roles**. A "run" is already the unit of work in Claudio; the chat is just one kind of run. The daemon is another kind of run. They share the substrate but differ in everything that defines behavior.

### 2.1 The role / profile concept

A **role** is a bundle of:

| Dimension | Chat role | Daemon role |
|---|---|---|
| Instructions | conversational, general | task-specific playbook |
| Tool set | general (read / write / run) | **scoped** (named, allowlisted commands) |
| Approval policy | `on_request` — the human decides | `off` + hard command allowlist |
| Privilege | the operator's user | scoped sudo, exact commands only |
| Session namespace | the human's chat sessions | `daemon:*` sessions |
| Notification | none | email / webhook on verdict |
| Mutability | the human controls it live | operator-set, **read-only from chat** |

The same `build_primary_agent(...)` machinery builds both — it already takes parameters per build (`approval_mode`, tool list). It is extended to take a **role profile** that carries the instructions, tool set, and policy together. This is a small, clean change, not a rewrite.

### 2.2 Chat role — free and interactive

Unchanged from today. The human sends messages, the agent uses the general tool set, approvals are per-request, the conversation is exploratory. The chat is where a person *works with* Claudio.

### 2.3 Daemon role — scoped and scripted

The daemon never freeforms. Each daemon task:
- runs a **fixed playbook prompt** (its instructions),
- has access only to **scoped, named tools** that map to allowlisted commands (§10.3),
- runs in its **own session namespace** (`daemon:health:2026-10-16`, etc.) so it never pollutes the human's chat history,
- produces a **structured verdict** (OK / WARNING / CRITICAL) plus evidence,
- **notifies** on WARNING/CRITICAL/failure (§10.2).

The daemon is where Claudio *works on* the server without being asked each time.

### 2.4 Shared substrate

Both roles share: the FastAPI backend process, the RunManager, the SQLite database, the config store, and the agent builder. They do **not** share sessions or policy. The chat can *see* the daemon's runs (read-only) — that's the cockpit (§11) — but cannot *change* the daemon's safety policy from the chat box.

---

## 3. Safety posture (the governing principle)

> **Diagnose and report by default. Act only on a small, audited allowlist. Keep a human confirmation for anything irreversible.**

The single most important decision is **not** the approval toggle. Turning `approval_mode=off` while the agent holds a freeform `run_command` as root is exactly how a hallucinated command destroys a server. Safety comes from **scoping what the agent can do**, not from a yes/no gate.

Four rules that every part of this design obeys:

1. **Least privilege.** The agent runs as a dedicated non-root user. Elevated actions go through a *scoped* sudo allowlist (exact commands only), never a general root shell.
2. **Read before write.** Every task inspects state and reports before it mutates anything.
3. **Deterministic mutations.** The risky actions are fixed, reviewed scripts. The LLM *orchestrates and interprets*; it does not improvise privileged commands.
4. **Irreversible = confirm.** Package upgrades, service restarts, deletions, and recovery actions notify the operator and (by default) require confirmation, even in "autonomous" mode.

`approval_mode` still exists, but it is a coarse control. The real boundary is the **command allowlist** (§10.3) and the **report-only default** (§10.5), enforced in code (§4).

---

## 4. The safety boundary: enforced in code, not in the prompt

This is the line that decides whether the two-role design is safe or a liability.

**Guidelines are soft. Policies must be hard.**

A prompt that says "be careful, don't delete things" is *advisory* — a confused model, or a prompt-injection smuggled in through a file the agent reads, can ignore it. The safety must live in **code the model cannot talk its way past**:

- **Tool scoping in code.** The daemon role is built *without* a freeform `run_command`. It only has named tools (`check_disk`, `run_updates`, `vacuum_logs`, …), each validating its arguments against a strict schema and mapping to an allowlisted command (§10.3). The model cannot emit an arbitrary shell string because it has no tool that accepts one.
- **Command allowlist checked at execution.** Even for the chat's `run_command`, the effective command is validated against the allowlist before it runs. The boundary is the allowlist, not the user.
- **Report-only enforced by the tool layer.** When `report_only: true`, mutation tools *refuse* — they do not merely warn (§10.5).
- **Daemon policy is operator-set and read-only from the chat.** The daemon's schedule, tool set, and policy are configured in `config.yaml` / the admin page (§11), never from the chat message box. A chat message — or an injected instruction in some file — cannot weaken the daemon's guardrails.

The chat's "guidelines" (its system prompt) remain helpful context. The daemon's "policies" (scoped tools + allowlist + report-only + immutable config) are the actual safety.

---

## 5. Privilege isolation: soft vs hard

The chat and the daemon run in the **same backend process**, which runs as **one OS user**. If that user has `sudo systemctl restart nginx` for the daemon, then the chat can also invoke that via `run_command` — OS privilege is per-process, not per-prompt. Two ways to handle this:

### 5.1 Soft isolation (fine for a single-operator personal server)

One process. The chat's `run_command` obeys the **same command allowlist** the daemon uses. Neither role can exceed the allowlist, so the shared OS privilege is irrelevant — the boundary is the allowlist, not the user. Simplest to deploy.

### 5.2 Hard isolation (if the chat is ever multi-user or exposed)

The daemon runs as a **separate service with its own user and its own scoped sudo**, sharing only the DB and code with the chat backend. The chat can never touch the daemon's elevated surface. More moving parts, but a real security boundary between the two roles.

**Decision rule:** single trusted operator → soft isolation. Shared, multi-user, or internet-facing chat → hard isolation.

---

## 6. Process model (systemd on Ubuntu)

Use **systemd**, not cron. Cron fires discrete jobs and gives you little; systemd gives logging, restart policy, resource limits, and boot ordering.

### 6.1 Dedicated user

```bash
sudo useradd --system --create-home --shell /usr/sbin/nologin claudio
```

The agent never runs as root. Home is its workspace.

### 6.2 Scoped sudo

`/etc/sudoers.d/claudio` — **exact commands only**, no wildcards that can be smuggled:

```
claudio ALL=(root) NOPASSWD: /usr/bin/apt-get update
claudio ALL=(root) NOPASSWD: /usr/bin/apt-get -y upgrade
claudio ALL=(root) NOPASSWD: /usr/bin/apt-get -y autoremove
claudio ALL=(root) NOPASSWD: /usr/bin/journalctl --vacuum-size=*
claudio ALL=(root) NOPASSWD: /usr/bin/systemctl restart nginx
claudio ALL=(root) NOPASSWD: /usr/bin/systemctl restart postgresql
```

Validate with `visudo -cf /etc/sudoers.d/claudio`. Each entry is reviewed individually; nothing that takes an arbitrary argument that could become a command is allowed.

### 6.3 The backend service

`/etc/systemd/system/claudio.service`:

```ini
[Unit]
Description=Claudio autonomous server agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=claudio
Group=claudio
WorkingDirectory=/opt/claudio/backend
ExecStart=/opt/claudio/backend/.venv/bin/uvicorn claudio.main:app --host 127.0.0.1 --port 8100
Restart=always
RestartSec=5
# Resource ceilings so a runaway run cannot starve the host
CPUQuota=200%
MemoryMax=2G
# Hardening
NoNewPrivileges=false        # false only because scoped sudo is needed
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/opt/claudio/data /var/lib/claudio
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now claudio
```

### 6.4 Scheduled jobs (systemd timers)

Each scheduled task is a small script + a `.timer`. Example — health check every 10 minutes:

`/etc/systemd/system/claudio-health.service`:
```ini
[Unit]
Description=Claudio health check
[Service]
Type=oneshot
User=claudio
ExecStart=/opt/claudio/scripts/run_health_check.sh
```

`/etc/systemd/system/claudio-health.timer`:
```ini
[Unit]
Description=Claudio health check every 10 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
[Install]
WantedBy=timers.target
```

Same pattern for `claudio-maintenance` (nightly) and `claudio-recovery` (boot oneshot, §9).

---

## 7. Workload 1 — Health checks (read-only, start here)

**Posture: safe, no action, report only.** This is the highest-value, lowest-risk piece and it works with what already exists.

A scheduled task sends a fixed diagnostic prompt. The agent runs **read-only** commands and reports.

Example commands (all read-only):
- `df -h` — disk usage, flag any filesystem > 85%
- `free -h` — memory / swap pressure
- `uptime` / `loadavg` — load vs core count
- `systemctl --failed` — failed units
- `journalctl -p err -n 200 --no-pager` — recent errors
- TLS cert expiry check (`openssl s_client` / `certbot certificates`)
- Backup freshness check (§9.1)

**Output contract:** the task returns a structured verdict — `OK` / `WARNING` / `CRITICAL` — plus the evidence. `WARNING`/`CRITICAL` trigger a notification (§10.2). `OK` is logged silently.

This workload performs **no mutations**. It is a monitoring agent.

---

## 8. Workload 2 — Maintenance (scripted, audited)

**Posture: medium risk. Deterministic scripts, full audit, notify before/after.**

Do **not** let the model improvise `apt`/cleanup invocations. Each maintenance action is a reviewed shell script with its own exit codes and idempotency; the agent runs the script and interprets the result.

| Task | Script | Notes |
|---|---|---|
| Package index update | `apt-get update` | safe, read-only on the index |
| Security patches | `unattended-upgrades` | **prefer the OS mechanism** — deterministic, not LLM-decided |
| Full upgrade | `apt-get -y upgrade` | **irreversible → confirm** (§3.4) |
| Remove unused packages | `apt-get -y autoremove` | confirm |
| Vacuum logs | `journalctl --vacuum-size=200M` | safe, idempotent |
| Docker prune | `docker system prune -f` | confirm (can remove volumes) |
| Temp cleanup | `scripts/cleanup_tmp.sh` | scoped to known temp paths |

Each run:
1. Logs the command, start time, exit code, output to the audit log (§10.4).
2. Notifies the operator of what it did and the result.
3. On failure, escalates rather than retrying destructively.

**Recommendation:** for security patching specifically, rely on Ubuntu `unattended-upgrades` (a deterministic system) and have Claudio *verify and report* on it, rather than have the LLM decide when to upgrade.

---

## 9. Workload 3 — Disaster recovery (playbook + notify)

**Posture: highest risk. A written playbook, a short allowlist of safe actions, and a human for everything else.**

`claudio-recovery.service` runs once after boot (`OnBootSec=1min`). It reviews the system and takes **only** safe, reversible recovery actions.

### 9.1 Precondition: backups

Real recovery depends on backups existing **before** disaster. The agent's first recovery job is to **verify backups are present, fresh, and restorable** — not to improvise recovery. If backups are missing/stale, that is itself a `CRITICAL` alert.

### 9.2 Boot review checklist

- Failed units (`systemctl --failed`) → safe action: `systemctl restart <unit>` (allowlisted), once; if still failing, alert.
- Failed mounts / fstab errors → **alert, do not auto-edit fstab**.
- Disk full → safe action: vacuum logs / clear known temp dirs; if still full, alert.
- Services expected but not running → restart (allowlisted), verify, else alert.
- Filesystem errors reported by the kernel → **alert immediately, do not attempt fsck on a live root**.

### 9.3 The line

Safe recovery = restart a crashed service, clear a full temp dir, rotate a full log. **Anything beyond that → notify the human.** The agent does not improvise destructive recovery.

---

## 10. Required platform additions

These do not exist in Claudio today and must be built for the above to work unattended.

### 10.1 Scheduled tasks

A first-class "task" concept: a named prompt + schedule + its own session + its own approval policy, fired by a systemd timer (or an internal scheduler).

Proposed task record:
```json
{
  "id": "nightly-maintenance",
  "name": "Nightly maintenance",
  "prompt": "Run the nightly maintenance playbook: update packages, clean unused space, verify backups. Report what you did.",
  "schedule": "0 3 * * *",
  "approval_mode": "off",
  "report_only": false,
  "notify_on": ["warning", "critical", "failure"],
  "enabled": true
}
```

A `claudio task run <id>` CLI (invoked by the timer) and `GET /tasks` / `POST /tasks` over the API.

### 10.2 Notification channel

Without this, "monitoring" is logs nobody reads. Add an outbound channel:
- **Email (SMTP)** — simplest, works headless.
- **Telegram / Slack webhook** — instant, mobile.
- **Generic webhook** — for custom routing.

Config:
```yaml
notifications:
  enabled: true
  email:
    smtp_host: smtp.example.com
    smtp_port: 587
    username: claudio@example.com
    password: <secret>
    from: claudio@myserver
    to: me@example.com
  webhook:
    url: https://hooks.example.com/claudio
```
The agent calls a `notify` tool (or the task runner emits on the verdict) for every WARNING/CRITICAL/failure.

### 10.3 Scoped admin tools

Replace freeform `run_command` for autonomous use with **named, validated tools**, each wrapping a specific script:

`check_disk`, `check_memory`, `check_services`, `check_logs`, `run_updates`, `autoremove`, `vacuum_logs`, `verify_backups`, `restart_service(name)`.

Each tool validates its arguments against a strict schema and maps to an allowlisted command. The model cannot pass an arbitrary shell string. This is the real security boundary (§4).

### 10.4 Audit log

Every command, every task run, every notification — appended to an **immutable** audit log (separate from the chat transcript), with timestamp, actor, command, exit code, and verdict. This is what you read when something went wrong at 3am.

### 10.5 Report-only mode

A global and per-task flag: when `report_only: true`, the agent may run read-only tools and produce a report, but every mutation tool is **refused by the tool layer** (§4). This is the safe default for a new deployment and for the disaster-recovery pass until trust is established.

---

## 11. Cockpit — the Chat administration UI

The chat app gains a dedicated **Autonomous / Daemon** administration view (a fourth top-level view alongside Chat / Config / Monitor, or an extension of Config). This is the operator's control room: set up the daemon, schedule actions, and see everything it finds. The daemon's policy is configured here and is **read-only from the normal chat box** (§4).

### 11.1 Dashboard

The at-a-glance state of the whole system:
- **System health** — overall OK / WARNING / CRITICAL, from the latest health check.
- **Daemon status** — running / stopped, last run time, next scheduled run.
- **Active alerts** — count of open WARNING/CRITICAL items.
- **Recent activity** — the last few daemon runs with verdicts.

### 11.2 Tasks / Schedule

Manage the daemon's scheduled work:
- List all tasks with name, schedule, enabled state, last result, next run.
- **Create / edit** a task: name, prompt/playbook, cron schedule, approval mode, report-only toggle, notify-on conditions.
- **Enable / disable** a task without deleting it.
- **Run now** button to trigger a task on demand.
- View a task's last run output and history.

### 11.3 Issues / Alerts

Everything the daemon flagged that needs attention:
- Current WARNING / CRITICAL items with the evidence that produced them.
- **Acknowledge / resolve** actions.
- History of past issues and how they were resolved.

### 11.4 Suggestions

The daemon's recommendations — things it noticed and would act on but did not (because report-only, or because the action is irreversible and awaits confirmation):
- Each suggestion: what it is, why, the command it would run, and **Accept / Dismiss**.
- **Accept** runs the action (subject to the same allowlist + audit); **Dismiss** records the decision.
- This is the human-in-the-loop surface for the daemon's judgment.

### 11.5 Run history / Audit viewer

A browsable view of the audit log (§10.4):
- Every daemon run: task, start/end, verdict, exit codes.
- Every command executed, with output.
- Filter by task, time range, verdict.

### 11.6 Daemon configuration

The `autonomous` config block (§13) exposed as a form:
- Global enable / disable.
- **Report-only** master toggle.
- Health-check interval, maintenance schedule, recovery enable.
- Notification settings (§10.2) with a **test send** button.
- **Command allowlist viewer** — read-only view of exactly what the daemon may run.

### 11.7 UI principles

- **Status is never color alone** — icon + word, consistent with the workflow view.
- **Every daemon action is visible** — nothing happens that isn't in the run history.
- **Destructive actions are gated** — Accept on a suggestion that mutates shows a confirmation.
- **Read-mostly** — the cockpit is for watching and deciding, not for freeform control (that's the chat).

---

## 12. Telegram channel

A third surface: converse with Claudio from your phone. This is **bidirectional** — distinct from the outbound-only alerts of §10.2. The Telegram bot becomes the **mobile cockpit**: alerts, approvals, quick status, and read-only queries wherever you are.

### 12.1 What it is

- A Telegram bot connected to the same backend, reusing the existing RunManager, sessions, and approvals — no new core machinery.
- You message the bot; it runs the agent and replies.
- It is a *front-end*, like the web chat, not a separate agent.

### 12.2 Transport: long-polling (recommended)

- **Long-polling**: the backend polls Telegram's servers for new messages. **No public URL required, works behind NAT/firewall.** This is the right choice for a home/personal server.
- **Webhook**: Telegram POSTs messages to a public HTTPS URL you provide. Lower latency, but exposes a public endpoint — only worth it if you already have TLS termination and want the responsiveness.
- The Telegram worker runs as a **background task inside the backend** (async). Library options: `aiogram` (async, fits FastAPI well), `python-telegram-bot` v20+ (also async), or raw `httpx` calls to the Bot API for zero heavy dependencies.

### 12.3 Session mapping

- Each Telegram `chat_id` maps to a Claudio session `telegram:<chat_id>` — its own history, separate from the web chat.
- **DMs only** for the privileged role. Group chats are higher risk (multiple people can command the bot) and should not be wired to anything that mutates.
- The Telegram **user ID** is the identity; it maps to the operator (§12.7).

### 12.4 Message flow

1. Operator messages the bot.
2. The worker receives it and maps `chat_id` → session.
3. The message text goes into the RunManager as a run, with the **Telegram role profile** (§12.7).
4. The agent runs; the reply is collected.
5. The reply is sent back to Telegram.

**Telegram quirk:** Telegram does not stream tokens like SSE. Send the **final reply**, optionally with a `sendChatAction("typing")` indicator while working. Progressive `editMessageText` is possible but rate-limited (~1 edit/sec, ~20 msgs/min) — use sparingly or not at all.

### 12.5 Approvals from your phone (the key feature)

This is what makes Telegram genuinely valuable for the daemon:
- When the daemon pauses on an irreversible action (§3.4), it sends a Telegram message with **inline Approve / Deny buttons** (Telegram inline keyboard + callback query).
- The operator taps a button → the callback resolves the approval through the existing `/approvals` endpoint → the run resumes.
- A 3am disk-cleanup decision becomes a one-tap action instead of an unread email.

### 12.6 Slash commands (cockpit actions)

Quick cockpit actions from the chat:
- `/status` — overall system + daemon status
- `/tasks` — list scheduled tasks and their next runs
- `/health` — the latest health-check verdict
- `/approve [id]` — approve a pending approval
- `/deny [id]` — deny a pending approval
- `/help` — list available commands

### 12.7 Security requirements (critical)

Telegram chat is **remote, over the internet**. The threat model changes:

1. **User allowlist is mandatory.** Only specific Telegram user IDs may command the bot; reject all others silently. Stored in config. Without this, anyone who discovers the bot has an agent on your server.
2. **Restricted role.** Wire Telegram to the **cockpit role** — view status, read reports, approve/deny, trigger allowlisted tasks, ask read-only queries — **not** the free-form chat role with arbitrary `run_command`. A remote user with a general shell agent is a large attack surface.
3. **Transport trust.** Telegram is a third-party cloud. Bot chats are **not end-to-end encrypted** — Telegram can see all content. Do not send secrets over it; be aware every command and output transits their servers.
4. **Prompt injection still applies.** Messages from Telegram, and content the agent reads, can carry injected instructions. The protection is the **code-enforced allowlist + report-only boundary** (§4), not the assumption that the chat is friendly.
5. **Confirmation for mutations.** Any mutation triggered from Telegram goes through the same allowlist + audit + confirm as the daemon (§3.4).

### 12.8 Configuration

```yaml
telegram:
  enabled: true
  bot_token: <secret>
  allowed_user_ids: [123456789]
  mode: cockpit          # cockpit (restricted) | full (free-form chat)
  transport: polling     # polling | webhook
  webhook_url: https://...   # only if transport = webhook
```

`mode: cockpit` is the recommended default. `mode: full` gives the free-form chat role over Telegram and should only be enabled deliberately, with the user allowlist locked down and the transport trusted.

### 12.9 Recommendation

Start with Telegram as the **mobile cockpit + approval remote** (`mode: cockpit`). It is the high-value, low-risk piece: you get alerts and one-tap approvals on your phone without handing a remote free-form agent to the internet. Extend toward full chat only if you genuinely need it and can secure it.

---

## 13. Configuration

Add an `autonomous` block to `config.yaml`:

```yaml
autonomous:
  enabled: true
  report_only: true          # start here; flip to false per §14
  health_check:
    enabled: true
    interval_minutes: 10
  maintenance:
    enabled: false           # enable in Phase 2
    schedule: "0 3 * * *"
  recovery:
    enabled: false           # enable in Phase 3
  notification:
    enabled: true
    # see §10.2
```

And a `telegram` block (§12.8):

```yaml
telegram:
  enabled: true
  bot_token: <secret>
  allowed_user_ids: [123456789]
  mode: cockpit
  transport: polling
```

The chat's own behavior (approval mode select, etc.) is unchanged and independent. The `autonomous` block governs only the daemon role and is edited via the admin page (§11.6) or this file — not from the chat message box.

---

## 14. Staged rollout

Do not turn all of this on at once. Earn trust in stages.

1. **Phase 1 — Watch (safe, useful now).** systemd service + read-only health checks + email/Telegram report + the cockpit dashboard/issues view. `report_only: true`. No auto-action. You get a monitoring agent and a control room.
2. **Phase 2 — Tidy (medium risk).** Add a handful of safe, scripted maintenance actions (vacuum, autoremove, index update) with full audit + notify-before/after.
3. **Phase 3 — Recover (playbook).** Boot recovery with a written playbook, still notify-heavy, safe actions only.
4. **Phase 4 — Broader autonomy.** Only after the above are trusted. Even then, keep human confirmation for irreversible operations (§3.4).

The **Telegram channel** (§12) can be added in Phase 1 (alerts + approvals) or deferred — it does not change the safety model as long as it stays in `cockpit` mode with a locked user allowlist.

---

## 15. Caveats and non-goals

- **Model reliability is the whole game.** An autonomous privileged agent needs a reliable model. A weak local model making autonomous root decisions is a genuinely bad combination. Keep the LLM in an **interpret-and-report** role; put mutations in **deterministic scripts**.
- **Backups before autonomy.** Disaster recovery without verified backups is a fantasy. Establish and test backups first (§9.1).
- **Not a replacement for a real config-management tool.** For fleet/infrastructure-as-code (Ansible, etc.), use that. This spec is for a single-host, conversational, report-driven manager.
- **Non-goal: unrestricted root.** The agent never gets a general root shell. Every elevated action is an allowlisted, audited command.
- **Non-goal: silent destructive action.** Anything irreversible notifies and (by default) confirms.
- **Non-goal: weakening the chat.** The chat stays free and interactive; the daemon's guardrails are additive and never reduce what the chat can do for its operator.
- **Non-goal: a remote free-form shell.** The Telegram channel defaults to the restricted cockpit role. Free-form chat over the internet is a deliberate, higher-risk choice, not the default.

---

## 16. Suggested first build

**Phase 1** is self-contained and low-risk:
1. `claudio.service` systemd unit (§6.3) + dedicated user + scoped sudo (§6.1–6.2).
2. `run_health_check.sh` — read-only diagnostics with an OK/WARNING/CRITICAL verdict (§7).
3. `claudio-health.timer` (§6.4).
4. Email notification on WARNING/CRITICAL (§10.2).
5. The cockpit **Dashboard** + **Issues** view (§11.1, §11.3) so the operator can see what the daemon finds.
6. (Optional) The **Telegram channel** in `cockpit` mode (§12) for phone alerts and one-tap approvals.

This gives an immediately useful monitoring agent and control room, and lets you judge whether the autonomy level feels right before going further.
