"""Issue #57 -- pluggable backend engine contracts (core side).

Pins the framework-neutral pieces the open backend rests on: the opaque,
versioned, engine-tagged resume checkpoint; the engine descriptor/capabilities;
the typed engine config; and the engine-kind resolution precedence.

Run:  cd claudio-core && .venv/bin/python -m pytest tests/test_issue57_engine.py -q
"""
from __future__ import annotations

import pytest

from claudio_core.engine import (
    CHECKPOINT_SCHEMA_VERSION,
    CheckpointError,
    EngineCapabilities,
    EngineCheckpoint,
    EngineDescriptor,
)
from claudio_core.settings import AppConfig, resolve_engine_kind


# -- checkpoint envelope ----------------------------------------------------

def test_checkpoint_round_trip():
    cp = EngineCheckpoint(engine="opencode", payload={"native_session": "s1", "call_id": "c1"})
    d = cp.to_dict()
    assert d["engine"] == "opencode"
    assert d["schema_version"] == CHECKPOINT_SCHEMA_VERSION
    assert d["payload"]["native_session"] == "s1"
    back = EngineCheckpoint.from_dict(d)
    assert back.engine == "opencode"
    assert back.payload == {"native_session": "s1", "call_id": "c1"}


def test_checkpoint_rejects_bad_schema_version():
    with pytest.raises(CheckpointError):
        EngineCheckpoint.from_dict({"engine": "adk", "schema_version": 999, "payload": {}})


def test_checkpoint_rejects_missing_engine():
    with pytest.raises(CheckpointError):
        EngineCheckpoint.from_dict({"schema_version": 1, "payload": {}})


def test_checkpoint_rejects_non_object():
    with pytest.raises(CheckpointError):
        EngineCheckpoint.from_dict("not-an-object")


def test_checkpoint_engine_mismatch():
    cp = EngineCheckpoint(engine="opencode", payload={})
    cp.validate_for("opencode")  # ok
    with pytest.raises(CheckpointError):
        cp.validate_for("claude_code")


# -- descriptor / capabilities ---------------------------------------------

def test_descriptor_as_dict():
    d = EngineDescriptor(id="adk", version="1.2.3", capabilities=EngineCapabilities(streaming=True)).as_dict()
    assert d["id"] == "adk"
    assert d["version"] == "1.2.3"
    assert d["capabilities"]["streaming"] is True


# -- typed engine config ----------------------------------------------------

def test_engine_config_defaults_to_adk():
    cfg = AppConfig()
    assert cfg.engine.kind == "adk"
    assert cfg.engine.run_timeout_seconds == 1800


def test_engine_config_rejects_unknown_kind():
    with pytest.raises(Exception):
        AppConfig.model_validate({"engine": {"kind": "bogus"}})


def test_engine_config_accepts_opencode_and_claude():
    cfg = AppConfig.model_validate(
        {"engine": {"kind": "opencode", "opencode": {"model": "m", "server_mode": "server"}}}
    )
    assert cfg.engine.opencode.server_mode == "server"
    cfg2 = AppConfig.model_validate(
        {"engine": {"kind": "claude_code", "claude_code": {"permission_mode": "plan"}}}
    )
    assert cfg2.engine.claude_code.permission_mode == "plan"


def test_resolve_engine_kind_precedence(monkeypatch):
    monkeypatch.delenv("CLAUDIO_ENGINE", raising=False)
    assert resolve_engine_kind(AppConfig()) == "adk"
    cfg = AppConfig.model_validate({"engine": {"kind": "opencode"}})
    assert resolve_engine_kind(cfg) == "opencode"
    # env overrides config
    monkeypatch.setenv("CLAUDIO_ENGINE", "claude_code")
    assert resolve_engine_kind(cfg) == "claude_code"
    # alias normalization
    monkeypatch.setenv("CLAUDIO_ENGINE", "google-adk")
    assert resolve_engine_kind(cfg) == "adk"
