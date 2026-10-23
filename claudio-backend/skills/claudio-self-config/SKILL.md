---
name: claudio-self-config
description: Read, validate, change and roll back Claudio's own configuration through the HTTP API. Use when asked to change a setting, tune the model or agent, enable a capability, or when a config change is suspected to have broken something.
---

# Claudio Self-Config

Claudio changing his own settings. Every claim below was verified against the live
backend on 2026-10-23 (`backend 0.1.0`, engine `adk`, protocol 1).

## The API

Base `http://127.0.0.1:8100/api/v1`, auth `Authorization: Bearer <token>`.
Token lives in `claudio-backend/config/config.yaml` → `server.auth_token`.
A working client is bundled: `scripts/claudio_api.py`.

| Endpoint | Method | Purpose |
|---|---|---|
| `/config` | GET | Current config, secrets redacted to `***`, plus `generation` |
| `/config/schema` | GET | Full JSON Schema of the config |
| `/config/validate` | POST | Validate a candidate without saving |
| `/config` | PUT | Merge-patch + validate + persist + hot-reload |
| `/config/llm/test` | POST | Live LLM connectivity test with candidate settings |
| `/config/history` | GET | Every stored config version |
| `/config/restore` | POST | Restore version `{"id": N}` |
| `/config/raw` | GET | Unredacted — 403 unless `server.expose_raw_config=true` |

## The five rules

### 1. Read before you write, every time
```bash
python3 scripts/claudio_api.py get /config
```
`PUT /config` is a **merge-patch**, not a replace. Nested dicts merge; you only send
the keys you want to change. That is a feature — but it means you can never see
what you're about to overwrite unless you read first.

### 2. `***` means "unchanged", never "set to ***"
Secrets (`llm.api_key`, `server.auth_token`,
`autonomous.notification.email.password`) are redacted on read. The backend
restores the real value on save. **Never** construct a config payload by copying a
redacted response into a script and expecting the secret to survive — it does, but
only because the backend patches it back. If you write a literal password yourself,
you own it.

### 3. Validate before you save
```bash
python3 scripts/claudio_api.py post /config/validate --body '{"llm":{"temperature":0.7}}'
```
`PUT` validates too, but validating first means you get a clean 422 without having
decided to commit. For LLM changes, also run the live test:
```bash
python3 scripts/claudio_api.py post /config/llm/test --body '{"llm":{"model":"<model>","base_url":"<url>"}}'
```
A config that passes schema validation can still point at a dead model. Schema
validation proves shape; only the live test proves function.

### 4. Know what needs a restart
```
BACKEND_RESTART_KEYS = {server.host, server.port}   → requires backend restart
AGENT_RESTART_KEYS   = {mcp}                       → requires agent restart
```
Everything else hot-reloads. **`PUT /config` returns `restart_required` — read it.**
If it lists something, the change is persisted but not live. Changing
`server.port` and not restarting means you are now talking to a config that no
longer matches the running server.

### 5. Rollback is one call
```bash
python3 scripts/claudio_api.py get /config/history      # find the good version id
python3 scripts/claudio_api.py post /config/restore --body '{"id": 3}'
```
Every `PUT` writes a version first. This is your safety net — but it only helps if
you check `history` and identify the last-known-good version **before** you start
making a series of changes.

## Safe change procedure

1. `GET /config` — record current values of what you're touching.
2. `GET /config/history` — note the current good version id.
3. `POST /config/validate` with the intended patch.
4. If LLM-related: `POST /config/llm/test`.
5. `PUT /config` with the patch.
6. Read the response: `changed_keys`, `generation`, `restart_required`.
7. `GET /config` again — confirm the value actually changed.
8. If `restart_required` is non-empty: say so explicitly and handle it.
9. State the rollback command in your report.

## What can actually go wrong

| Failure | How you catch it |
|---|---|
| Change silently didn't apply | Re-`GET /config` and diff |
| Change needs restart you didn't do | Read `restart_required` |
| Config valid but LLM dead | `POST /config/llm/test` |
| Locked out by bad `auth_token` | Keep the old token written down before changing it |
| Server unreachable after host/port change | Backend restart required; console access |
| Invalid YAML written directly to file | Never edit the file directly — always go through the API |

**Never hand-edit `config.yaml` while the backend is running.** The API writes
atomically (temp file + rename) and records history. A manual edit does neither,
and the next `PUT` will happily overwrite it.

## Scope discipline

Changing your own configuration is a mutation with real blast radius. Before any
change that affects security posture (`approval_mode`, `auth_token`,
`expose_raw_config`, CORS origins) or that you cannot immediately verify, ask.
Changing `temperature` is not the same as changing `approval_mode`.
