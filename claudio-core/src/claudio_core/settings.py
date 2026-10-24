"""Configuration: load/validate/merge (env > yaml > defaults), atomic write, history.

The live config lives at ``config/config.yaml``. The config API writes it atomically
and keeps the last 20 versions in SQLite (see :mod:`claudio.db`).

Env overrides use the ``CLAUDIO__SECTION__KEY`` convention, e.g.
``CLAUDIO__LLM__MODEL=gpt-5``.
"""
from __future__ import annotations

import copy
import json
import os
import shlex
import tempfile
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator

ENV_PREFIX = "CLAUDIO__"

# Keys that require a full backend process restart to take effect.
BACKEND_RESTART_KEYS = {"server.host", "server.port"}
# Keys that require an agent-runtime restart to take effect.
AGENT_RESTART_KEYS = {"mcp"}

# Dotted paths to secret fields, redacted on read and preserved on save.
SECRET_PATHS = [
    "llm.api_key",
    "server.auth_token",
    "autonomous.notification.email.password",
]
REDACTED = "***"


def _get_path(d: dict, dotted: str):
    cur = d
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _set_path(d: dict, dotted: str, value) -> None:
    parts = dotted.split(".")
    cur = d
    for part in parts[:-1]:
        nxt = cur.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cur[part] = nxt
        cur = nxt
    cur[parts[-1]] = value


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8100
    auth_token: str = "dev-token-change-me"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    expose_raw_config: bool = False


class LLMConfig(BaseModel):
    provider: str = "openai"
    model: str = "qwen3.5-4b-claude-4.6-opus-reasoning-distilled"
    api_key: str = "local"
    base_url: str | None = "http://127.0.0.1:1234/v1"
    temperature: float = 0.2
    max_tokens: int = 16384
    fallback_model: str | None = None
    timeout_seconds: int = 300
    retry_attempts: int = 3
    price_input_per_mtok: float = 0.0
    price_output_per_mtok: float = 0.0


class SubagentConfig(BaseModel):
    description: str = ""
    instructions_file: str | None = None
    instructions: str | None = None
    tools: list[str] = Field(default_factory=list)
    max_turns: int = 30


class AgentToolsConfig(BaseModel):
    enabled: list[str] = Field(default_factory=list)
    disabled: list[str] = Field(default_factory=list)


class AgentConfig(BaseModel):
    name: str = "claudio"
    instructions_file: str | None = "instructions.md"
    instructions: str | None = None
    max_turns: int = 50
    # When a run finishes but its task list still has open items, the agent is
    # nudged to keep going until the plan is done. This caps how many times that
    # auto-continuation fires in one user turn — the backstop against a model
    # that keeps "finishing" without making progress. 0 disables it.
    max_auto_continues: int = 10
    max_budget_usd: float = 5.0
    approval_mode: str = "on_request"  # off | on_request | always
    workspace_dir: str = "./data/workspace"
    context_window: int = 32768
    compaction_reserve: int = 8192
    # HTTP timeout for the compaction summarizer call specifically. Compaction is
    # a long single-shot generation (a ~12k-char transcript summarized in one
    # request); on a slow local reasoning model the generic ``llm.timeout_seconds``
    # (120s) is too short and the request times out, which trips the circuit
    # breaker and disables compaction for the session. Give compaction its own,
    # much larger budget so a slow summarizer does not silently kill it.
    compaction_timeout_seconds: int = 300
    max_output_chars: int = 8000
    strict_tools: bool = False
    skills_enabled: bool = True
    skills_dir: str = "./skills"
    tools: AgentToolsConfig = Field(default_factory=AgentToolsConfig)
    subagents: dict[str, SubagentConfig] = Field(default_factory=dict)

    @field_validator("approval_mode")
    @classmethod
    def _check_approval_mode(cls, v: str) -> str:
        if v not in ("off", "on_request", "always"):
            raise ValueError("approval_mode must be one of: off, on_request, always")
        return v


class MCPServerEntry(BaseModel):
    id: str
    transport: str = "stdio"  # stdio | sse | streamable_http
    command: list[str] = Field(default_factory=list)
    url: str | None = None
    enabled: bool = True

    @field_validator("command", mode="before")
    @classmethod
    def _coerce_command(cls, v: Any) -> Any:
        """Accept a command line as a string and store it as an argv list.

        The UI edits ``command`` as a single string, and sends an empty string
        for HTTP/SSE servers where no command applies. Both arrive here as a
        ``str``; the schema is ``list[str]``. Normalize so an empty/blank string
        becomes ``[]`` and a populated one is split into argv tokens (quoted
        arguments preserved). A value that is already a list passes through.
        """
        if v is None:
            return []
        if isinstance(v, str):
            if not v.strip():
                return []
            return shlex.split(v)
        return v


class MCPConfig(BaseModel):
    servers: list[MCPServerEntry] = Field(default_factory=list)


class MonitoringConfig(BaseModel):
    log_level: str = "INFO"
    log_retention_days: int = 7
    metrics_snapshot_interval_seconds: int = 5


class EmailNotifyConfig(BaseModel):
    enabled: bool = False
    smtp_host: str = ""
    smtp_port: int = 587
    username: str = ""
    password: str = ""
    from_addr: str = ""
    to_addr: str = ""
    use_tls: bool = True


class WebhookNotifyConfig(BaseModel):
    enabled: bool = False
    url: str = ""


class NotificationConfig(BaseModel):
    enabled: bool = False
    email: EmailNotifyConfig = Field(default_factory=EmailNotifyConfig)
    webhook: WebhookNotifyConfig = Field(default_factory=WebhookNotifyConfig)


class HealthCheckConfig(BaseModel):
    enabled: bool = True
    interval_minutes: int = 10
    # Thresholds for the read-only diagnostics (see claudio.autonomous.health).
    disk_warn_percent: float = 85.0
    disk_crit_percent: float = 95.0
    mem_crit_percent: float = 95.0
    swap_warn_percent: float = 80.0
    load_warn_factor: float = 1.0
    load_crit_factor: float = 2.0
    # Backup freshness (see §9.1). Empty backup_path disables the check.
    backup_path: str = ""
    backup_max_age_hours: float = 26.0
    # The health check as an editable shell script. When non-empty it is the
    # source of truth: the script runs instead of the built-in Python diagnostics
    # and its exit code decides the verdict (0 = ok, non-zero = critical). Edit it
    # in the Tasks panel like any other task.
    script: str = ""


class MaintenanceConfig(BaseModel):
    """Scheduled maintenance (spec §8). Safe actions run automatically; the
    irreversible ones (autoremove, full upgrade, docker prune) are always routed
    through the suggestion/accept flow regardless of these toggles (spec §3.4)."""

    enabled: bool = False
    schedule: str = "0 3 * * *"          # cron, evaluated in UTC
    vacuum_size: str = "200M"            # journalctl --vacuum-size value
    temp_dirs: list[str] = Field(default_factory=lambda: ["/tmp", "/var/tmp"])
    audit_retention_days: int = 90       # how long to keep audit rows
    # Per-action toggles.
    apt_update: bool = True
    vacuum_logs: bool = True
    temp_cleanup: bool = True
    autoremove: bool = True
    full_upgrade: bool = False
    docker_prune: bool = False
    # The maintenance run as an editable shell script. When non-empty it is the
    # source of truth: the script runs directly instead of the built-in
    # config-driven playbook. Edit it in the Tasks panel like any other task.
    # Skipped while report_only is in effect (it mutates the host).
    script: str = ""

    @field_validator("schedule")
    @classmethod
    def _check_schedule(cls, v: str) -> str:
        from croniter import croniter

        if not croniter.is_valid(v):
            raise ValueError(f"invalid cron schedule: {v!r}")
        return v

    @field_validator("vacuum_size")
    @classmethod
    def _check_vacuum_size(cls, v: str) -> str:
        from .safety.allowlist import validate_vacuum_size

        return validate_vacuum_size(v)


class RecoveryConfig(BaseModel):
    """Boot-time disaster recovery (spec §9). Safe, reversible actions only."""

    enabled: bool = False
    expected_services: list[str] = Field(default_factory=list)
    restart_allowlist: list[str] = Field(default_factory=list)


class AutonomousConfig(BaseModel):
    enabled: bool = False
    report_only: bool = True
    # How many distinct fix attempts the agent will work through for one issue
    # before giving up. Each attempt is a different command, proposed with the
    # previous failures fed back to it.
    max_fix_attempts: int = 3
    health_check: HealthCheckConfig = Field(default_factory=HealthCheckConfig)
    maintenance: MaintenanceConfig = Field(default_factory=MaintenanceConfig)
    recovery: RecoveryConfig = Field(default_factory=RecoveryConfig)
    notification: NotificationConfig = Field(default_factory=NotificationConfig)
    # When set, replaces the built-in command allowlist (see autonomous/allowlist).
    command_allowlist: list[str] | None = None

    @field_validator("command_allowlist")
    @classmethod
    def _check_command_allowlist(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        from .safety.allowlist import validate_command_entry

        return [validate_command_entry(e) for e in v]


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)
    autonomous: AutonomousConfig = Field(default_factory=AutonomousConfig)


def _env_overrides() -> dict[str, Any]:
    """Collect CLAUDIO__SECTION__KEY env vars into a nested dict."""
    out: dict[str, Any] = {}
    for raw_key, value in os.environ.items():
        if not raw_key.startswith(ENV_PREFIX):
            continue
        parts = raw_key[len(ENV_PREFIX):].lower().split("__")
        if len(parts) != 2:
            continue
        section, key = parts
        # Try to parse JSON for non-string types (lists, numbers, bools, null).
        try:
            parsed: Any = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            parsed = value
        out.setdefault(section, {})[key] = parsed
    return out


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


def load_config_dict(config_path: Path) -> dict[str, Any]:
    """Raw config dict: defaults < yaml < env."""
    base = AppConfig().model_dump()
    if config_path.exists():
        with open(config_path) as f:
            loaded = yaml.safe_load(f) or {}
        if not isinstance(loaded, dict):
            raise ValueError("config.yaml must contain a mapping at the top level")
        base = _deep_merge(base, loaded)
    base = _deep_merge(base, _env_overrides())
    return base


def load_config(config_path: Path) -> AppConfig:
    return AppConfig.model_validate(load_config_dict(config_path))


def validate_candidate(candidate: dict[str, Any]) -> AppConfig:
    """Validate a candidate config dict (merged over current defaults)."""
    base = AppConfig().model_dump()
    merged = _deep_merge(base, candidate or {})
    return AppConfig.model_validate(merged)


def redact_secrets(cfg_dict: dict[str, Any]) -> dict[str, Any]:
    """Return a copy with secret fields replaced by '***'."""
    out = copy.deepcopy(cfg_dict)
    for path in SECRET_PATHS:
        if _get_path(out, path):
            _set_path(out, path, REDACTED)
    return out


def restore_redacted_secrets(candidate: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Replace redacted placeholders in ``candidate`` with the real values from ``current``.

    The config API redacts secrets on read (see :func:`redact_secrets`). When a
    client echoes that redacted config back on save, the ``***`` placeholders must
    not overwrite the stored secrets — a field carrying the placeholder is treated
    as "unchanged".
    """
    out = copy.deepcopy(candidate)
    for path in SECRET_PATHS:
        if _get_path(out, path) == REDACTED:
            cur = _get_path(current, path)
            if cur:
                _set_path(out, path, cur)
    return out


def changed_keys(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Dotted keys that differ between two config dicts."""
    diffs: list[str] = []
    for section in set(old) | set(new):
        o = old.get(section)
        n = new.get(section)
        if isinstance(o, dict) and isinstance(n, dict):
            for k in set(o) | set(n):
                if o.get(k) != n.get(k):
                    diffs.append(f"{section}.{k}")
        elif o != n:
            diffs.append(section)
    return diffs


def restart_required_for(changed: list[str]) -> list[str]:
    """Map changed keys to the restart actions they require."""
    required: list[str] = []
    for key in changed:
        if key in BACKEND_RESTART_KEYS and "backend" not in required:
            required.append("backend")
        section = key.split(".", 1)[0]
        if section in AGENT_RESTART_KEYS and "agent" not in required:
            required.append("agent")
    return required


def write_config_atomic(config_path: Path, cfg_dict: dict[str, Any]) -> None:
    """Write YAML atomically (temp file + rename)."""
    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(config_path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            yaml.safe_dump(cfg_dict, f, sort_keys=False, default_flow_style=False)
        os.replace(tmp, config_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def config_schema_json() -> dict[str, Any]:
    """JSON Schema of AppConfig for frontend form rendering."""
    return AppConfig.model_json_schema()
