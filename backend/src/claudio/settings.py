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

SECRET_FIELDS = {("llm", "api_key"), ("server", "auth_token")}
REDACTED = "***"


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
    max_tokens: int = 4096
    fallback_model: str | None = None
    timeout_seconds: int = 120
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
    max_budget_usd: float = 5.0
    approval_mode: str = "on_request"  # off | on_request | always
    workspace_dir: str = "./data/workspace"
    context_window: int = 32768
    compaction_reserve: int = 8192
    max_output_chars: int = 8000
    strict_tools: bool = False
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


class MCPConfig(BaseModel):
    servers: list[MCPServerEntry] = Field(default_factory=list)


class MonitoringConfig(BaseModel):
    log_level: str = "INFO"
    log_retention_days: int = 7
    metrics_snapshot_interval_seconds: int = 5


class AppConfig(BaseModel):
    server: ServerConfig = Field(default_factory=ServerConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    agent: AgentConfig = Field(default_factory=AgentConfig)
    mcp: MCPConfig = Field(default_factory=MCPConfig)
    monitoring: MonitoringConfig = Field(default_factory=MonitoringConfig)


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
    for section, key in SECRET_FIELDS:
        if isinstance(out.get(section), dict) and out[section].get(key):
            out[section][key] = REDACTED
    return out


def restore_redacted_secrets(candidate: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Replace redacted placeholders in ``candidate`` with the real values from ``current``.

    The config API redacts secrets on read (see :func:`redact_secrets`). When a
    client echoes that redacted config back on save, the ``***`` placeholders must
    not overwrite the stored secrets — a field carrying the placeholder is treated
    as "unchanged".
    """
    out = copy.deepcopy(candidate)
    for section, key in SECRET_FIELDS:
        if isinstance(out.get(section), dict) and out[section].get(key) == REDACTED:
            cur = current.get(section)
            if isinstance(cur, dict) and cur.get(key):
                out[section][key] = cur[key]
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
