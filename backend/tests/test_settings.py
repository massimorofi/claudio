"""Tests for the configuration subsystem."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from claudio.settings import (
    AppConfig,
    load_config,
    load_config_dict,
    validate_candidate,
    write_config_atomic,
    redact_secrets,
    changed_keys,
    restart_required_for,
    config_schema_json,
)


def test_defaults_load():
    cfg = AppConfig()
    assert cfg.server.port == 8100
    assert cfg.agent.approval_mode == "on_request"
    assert cfg.llm.provider == "openai"


def test_yaml_override(tmp_path: Path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text(
        "server:\n  port: 9000\nllm:\n  model: test-model\n"
    )
    cfg = load_config(cfg_file)
    assert cfg.server.port == 9000
    assert cfg.llm.model == "test-model"
    # Untouched fields keep defaults.
    assert cfg.server.host == "127.0.0.1"


def test_env_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("CLAUDIO__LLM__MODEL", "env-model")
    monkeypatch.setenv("CLAUDIO__SERVER__PORT", "7777")
    cfg = load_config(tmp_path / "missing.yaml")
    assert cfg.llm.model == "env-model"
    assert cfg.server.port == 7777


def test_env_beats_yaml(tmp_path: Path, monkeypatch):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("llm:\n  model: yaml-model\n")
    monkeypatch.setenv("CLAUDIO__LLM__MODEL", "env-model")
    cfg = load_config(cfg_file)
    assert cfg.llm.model == "env-model"


def test_invalid_approval_mode_rejected():
    with pytest.raises(Exception):
        AppConfig.model_validate({"agent": {"approval_mode": "bogus"}})


def test_redact_secrets():
    cfg = AppConfig()
    redacted = redact_secrets(cfg.model_dump())
    assert redacted["llm"]["api_key"] == "***"
    assert redacted["server"]["auth_token"] == "***"


def test_changed_keys():
    old = {"server": {"port": 8100}, "llm": {"model": "a"}}
    new = {"server": {"port": 9000}, "llm": {"model": "a"}}
    changed = changed_keys(old, new)
    assert "server.port" in changed
    assert "llm.model" not in changed


def test_restart_required_mapping():
    assert "backend" in restart_required_for(["server.port"])
    assert "agent" in restart_required_for(["mcp.servers"])
    assert restart_required_for(["llm.model"]) == []


def test_atomic_write_roundtrip(tmp_path: Path):
    cfg = AppConfig()
    target = tmp_path / "out.yaml"
    write_config_atomic(target, cfg.model_dump())
    assert target.exists()
    reloaded = load_config(target)
    assert reloaded.server.port == cfg.server.port


def test_schema_is_valid_json():
    schema = config_schema_json()
    assert "properties" in schema
    assert "server" in schema["properties"]


def test_validate_candidate_ok():
    cfg = validate_candidate({"llm": {"model": "x"}})
    assert cfg.llm.model == "x"


def test_validate_candidate_bad():
    with pytest.raises(Exception):
        validate_candidate({"server": {"port": "not-a-number"}})
