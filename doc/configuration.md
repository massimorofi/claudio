# Configuration Reference

Single source of truth for every Claudio setting: where config comes from, the
complete field reference, how secrets are handled, how the auth token is
rotated, what needs a restart, and the Config API.

> Cross-refs: system narrative in [architecture.md](./architecture.md);
> engine/daemon internals in [backend.md](./backend.md); core module notes in
> [core.md](./core.md); skills/workspace paths in [skills.md](./skills.md).

---

## Sources & Merge Order

Config is assembled in three layers, later layers winning:

1. **Built-in defaults** — `claudio_core/settings.py` (every field has a default).
2. **`claudio-backend/config/config.yaml`** — the live, gitignored config file.
3. **Environment** — `CLAUDIO__SECTION__KEY=value` (JSON-typed).

The live file is the authority: environment variables only fill what the file
does **not** carry. (Reversing this order silently reverted explicit saves whose
keys a deployment also pinned in `CLAUDIO__*` env vars, because the Config API
re-reads config right after writing.)

Environment overrides use the `CLAUDIO__` prefix and `__` as the path separator,
and are JSON-parsed so non-string types work:

```bash
CLAUDIO__LLM__MODEL="gpt-5"
CLAUDIO__SERVER__PORT=8100
CLAUDIO__AGENT__MAX_TURNS=50
CLAUDIO__AGENT__STRICT_TOOLS=true
```

---

## Complete Field Reference

Top-level model is `AppConfig`. Each section maps to a Pydantic model.

### `server`

| Field | Type | Default | Notes |
|---|---|---|---|
| `host` | str | `127.0.0.1` | Bind address. Restart required. |
| `port` | int | `8100` | Bind port. Restart required. |
| `auth_token` | str | `dev-token-change-me` | Bearer token. **Secret.** |
| `cors_origins` | list[str] | `["http://localhost:5173"]` | Allowed CORS origins. |
| `expose_raw_config` | bool | `false` | Gates `GET /config/raw` (unredacted). |

### `llm` (primary model — legacy single-model section)

| Field | Type | Default | Notes |
|---|---|---|---|
| `provider` | str | `openai` | OpenAI-compatible endpoint. |
| `model` | str | `qwen3.5-4b-...` | Model id. |
| `api_key` | str | `local` | **Secret.** |
| `base_url` | str\|None | `http://127.0.0.1:1234/v1` | Endpoint base. |
| `temperature` | float | `0.2` | |
| `max_tokens` | int | `16384` | Response cap. `0` means "use default", not "send zero". |
| `fallback_model` | str\|None | `None` | One fallback attempt on failure. |
| `timeout_seconds` | int | `300` | |
| `retry_attempts` | int | `3` | Exponential-backoff retries. |
| `price_input_per_mtok` | float | `0.0` | $ per million input tokens. |
| `price_output_per_mtok` | float | `0.0` | $ per million output tokens. |

### `model_profiles` (named inference endpoints)

A dict of `name -> ModelProfile`. Each profile is one reachable model server:

| Field | Type | Default | Notes |
|---|---|---|---|
| `label` | str | `""` | Display label. |
| `provider` | str | `openai` | |
| `model` | str | `""` | Model id. |
| `base_url` | str\|None | `None` | |
| `api_key` | str | `local` | **Secret** (per-profile). |
| `temperature` | float | `0.2` | |
| `max_tokens` | int | `16384` | |
| `context_window` | int | `32768` | Per-profile window (≥1). Drives compaction for the fast role. |
| `price_input_per_mtok` | float | `0.0` | |
| `price_output_per_mtok` | float | `0.0` | |
| `timeout_seconds` | int | `300` | |

### `roles` (which profile plays each role)

| Field | Type | Default | Notes |
|---|---|---|---|
| `primary` | str\|None | `None` | Profile name for the primary role. |
| `worker` | str\|None | `None` | Profile name for delegated worker runs. |
| `fast` | str\|None | `None` | Profile name for one-shot fast calls (compaction, auto-routing). |

A role left unset falls back to the `llm` section, so a config with no
`model_profiles`/`roles` behaves exactly as before (one model everywhere).
See [model-routing.md (archive)](./archive/model-routing.md) for the routing
narrative.

### `agent`

| Field | Type | Default | Notes |
|---|---|---|---|
| `name` | str | `claudio` | |
| `instructions_file` | str\|None | `instructions.md` | |
| `instructions` | str\|None | `None` | Inline instructions (override file). |
| `max_turns` | int | `50` | Turn cap per run. |
| `max_auto_continues` | int | `10` | Auto-continue cap when the task list has open items. |
| `max_budget_usd` | float | `5.0` | Session budget. `0` disables the guard. |
| `approval_mode` | str | `on_request` | `off` \| `on_request` \| `always`. |
| `host_execution_enabled` | bool | `false` | Host-execution bridge. |
| `host_execution_socket` | str\|None | `None` | |
| `default_workspace_dir` | str | `./data/workspace` | **cwd-dependent** (see skills.md). |
| `workspace_allowed_roots` | list[str] | `[]` | Allowed workspace roots. |
| `context_window` | int | `32768` | Legacy primary window. |
| `compaction_reserve` | int | `8192` | Tokens held back for the response. |
| `compaction_timeout_seconds` | int | `300` | Summariser timeout. |
| `max_output_chars` | int | `8000` | Tool output cap (≥1). |
| `strict_tools` | bool | `false` | |
| `skills_enabled` | bool | `true` | |
| `skills_dir` | str | `./skills` | **cwd-independent** (anchored to backend base; see skills.md). |
| `auto_routing_enabled` | bool | `false` | Fast-profile auto-routing of simple requests. |
| `subagent_approval_mode` | str | `one_gate` | |
| `tools` | object | `{enabled: [], disabled: []}` | Tool allow/deny lists. |
| `subagents` | dict | `{}` | `name -> SubagentConfig` (`description`, `instructions`/`instructions_file`, `tools`, `max_turns`). |

### `search`

| Field | Type | Default | Notes |
|---|---|---|---|
| `provider` | str | `auto` | `auto` \| `brave` \| `serper` \| `tavily` \| `scraping`. |
| `brave_api_key` | str | `""` | **Secret.** |
| `serper_api_key` | str | `""` | **Secret.** |
| `tavily_api_key` | str | `""` | **Secret.** |
| `language` | str | `en` | |
| `country` | str | `us` | |
| `min_relevance` | float | `0.2` | Relevance gate threshold. |
| `timeout_seconds` | int | `12` | |

### `mcp`

| Field | Type | Default | Notes |
|---|---|---|---|
| `servers` | list | `[]` | Each: `id`, `transport` (`stdio`\|`sse`\|`streamable_http`), `command` (list), `url`, `enabled`. |

Any `mcp.*` change requires an agent restart (`POST /agent/restart`).

### `monitoring`

| Field | Type | Default | Notes |
|---|---|---|---|
| `log_level` | str | `INFO` | |
| `log_retention_days` | int | `7` | |
| `metrics_snapshot_interval_seconds` | int | `5` | |

### `autonomous`

| Field | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `false` | Master switch for the daemon. |
| `report_only` | bool | `true` | Observe-only; no host mutations. |
| `max_fix_attempts` | int | `3` | Distinct fix attempts per issue. |
| `health_check` | object | see below | `enabled`, `interval_minutes`, disk/mem/swap/load thresholds, `journal_lookback_minutes`, `script`. |
| `maintenance` | object | see below | `enabled`, `schedule` (cron), `vacuum_size`, `temp_dirs`, `audit_retention_days`, per-action toggles (`apt_update`, `vacuum_logs`, `temp_cleanup`, `autoremove`, `full_upgrade`, `docker_prune`), `script`. |
| `recovery` | object | see below | `enabled`, `expected_services`, `restart_allowlist`. |
| `notification` | object | see below | `enabled`, `email` (SMTP + **password secret**), `webhook` (`url`). |
| `command_allowlist` | list\|None | `None` | Replaces the built-in allowlist when set. |

### `self_improvement`

| Field | Type | Default | Notes |
|---|---|---|---|
| `enabled` | bool | `false` | Master switch (off by default). |
| `capture_enabled` | bool | `true` | Run the post-run extractor. |
| `consolidation_schedule` | str | `0 4 * * 1` | Weekly, Mon 04:00 UTC (cron-validated). |
| `promotion_threshold` | int | `3` | `recurrence_count >= N` promotes. |
| `auto_promote` | bool | `false` | Apply skill changes without approval. |
| `max_learnings` | int | `5000` | Episodic table cap. |
| `retention_days` | int | `180` | Prune raw learnings older than this. |
| `max_auto_skills` | int | `20` | Bloat guard on auto-generated skills. |

---

## Secrets Handling

Secrets never round-trip through the UI in the clear.

- **Redacted on read.** `GET /config` returns every secret as `***`.
- **`***` on write means "unchanged".** A `***` in a `PUT /config` payload is
  restored to the stored secret server-side (`restore_redacted_secrets`) before
  validation — it never sets the secret to the literal `***`. To change a
  secret, send its real value.
- **Secret paths.** The fixed set is:
  - `llm.api_key`
  - `server.auth_token`
  - `autonomous.notification.email.password`
  - `search.brave_api_key`
  - `search.serper_api_key`
  - `search.tavily_api_key`

  Plus the dynamic per-profile paths `model_profiles.<name>.api_key` for every
  profile present in the config (the fixed list can't enumerate operator-named
  profiles, so they're discovered at redact/restore time).
- **Unredacted read is gated.** `GET /config/raw` returns the real values only
  when `server.expose_raw_config` is `true` (off by default).

---

## Auth Token Rotation

1. Generate a strong token:
   ```bash
   python -c "import secrets; print('claudio-' + secrets.token_urlsafe(32))"
   ```
2. Either `PUT /config` with the new `server.auth_token`, or edit
   `claudio-backend/config/config.yaml` directly.
3. Restart the backend: `./backend-stop.sh && ./backend-start.sh`.
4. Update the token in the frontend's `localStorage` so the UI keeps talking to
   the backend.

---

## Restart Semantics

| Changed key | Restart required |
|---|---|
| `server.host`, `server.port` | Backend process |
| Any `mcp.*` | Agent runtime (`POST /agent/restart`) |
| Everything else | Hot-reload |

`PUT /config` returns `changed_keys` (the dotted paths that differed) and
`restart_required` (whether those keys need a process restart).

---

## Config API

| Method | Path | Notes |
|---|---|---|
| `GET` | `/config` | Current config (secrets redacted) + `generation`. |
| `GET` | `/config/schema` | JSON Schema for the settings UI forms. |
| `PUT` | `/config` | **Merge-patch** — send only changed keys; deep-merged over current. |
| `POST` | `/config/validate` | Validate a candidate without saving. |
| `POST` | `/config/llm/test` | Live connectivity test → `{ok, latency_ms, model, error}`. |
| `GET` | `/config/models/health` | Per-profile health status. |
| `GET` | `/config/history` | Last saved versions. |
| `POST` | `/config/restore` | Roll back to a version (redacted placeholders restored first). |
| `GET` | `/config/raw` | Unredacted — gated by `server.expose_raw_config`. |

**Merge-patch, not replace.** The payload is deep-merged over the current config,
so you only send the keys you want to change; anything omitted keeps its current
value. Validation is whole-config: if the merge produces an invalid `AppConfig`,
the request returns `422` and nothing is written.
