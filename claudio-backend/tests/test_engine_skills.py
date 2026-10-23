"""Focused tests for the engine's skill wiring and reasoning persistence.

These cover the two behaviours added when skills were wired into the ADK
engine and when reasoning was made to survive a view remount:

* ``AdkEngine._build_agent`` registers the three read-only skill tools
  (``list_skills`` / ``load_skill`` / ``read_skill_resource``) with no
  approval, and folds the skill catalog into the static system prompt --
  but only when ``agent.skills_enabled`` is true, and never at the cost of
  a failed run if the scan blows up.
* ``RunManager._track`` mirrors a ``reasoning`` event onto the JSONL
  transcript so the chat view can rebuild it after a tab switch / reload.

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_engine_skills.py -q
"""
from __future__ import annotations

from pathlib import Path

from claudio_core.context import ToolContext
from claudio_core.db import Database
from claudio_core.engine import ModelSpec, RunRequest, ToolPolicy
from claudio_core.session_store import SessionStore
from claudio_core.settings import AppConfig

from claudio_backend.engine.adk_engine import AdkEngine
from claudio_backend.runs.manager import RunManager, RunRecord

BACKEND_BASE = Path(__file__).resolve().parents[1]
SKILLS_DIR = BACKEND_BASE / "skills"

SKILL_TOOL_NAMES = {"list_skills", "load_skill", "read_skill_resource"}


def _config(*, skills_enabled: bool, skills_dir: Path, workspace: Path) -> AppConfig:
    return AppConfig.model_validate(
        {
            "agent": {
                "skills_enabled": skills_enabled,
                "skills_dir": str(skills_dir),
                "workspace_dir": str(workspace),
                "approval_mode": "off",
                "tools": {"enabled": ["read_file", "write_file"], "disabled": []},
            }
        }
    )


def _request(config: AppConfig, workspace: Path) -> RunRequest:
    return RunRequest(
        run_id="run-1",
        session_id="sess-1",
        workspace=workspace,
        instructions="",
        user_input="hello",
        model=ModelSpec(model="fake-model", base_url="http://127.0.0.1:1/v1"),
        tools=ToolPolicy(enabled=("read_file", "write_file"), approval_mode="off"),
        max_turns=5,
    )


def _tool_names(agent) -> set[str]:
    names = set()
    for t in agent.tools:
        name = getattr(t, "name", None)
        if name:
            names.add(name)
    return names


# --------------------------------------------------------------------------
# Skill-tool registration in _build_agent
# --------------------------------------------------------------------------


def test_skill_tools_registered_when_enabled(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    config = _config(skills_enabled=True, skills_dir=SKILLS_DIR, workspace=workspace)
    engine = AdkEngine(config=config, db=Database(tmp_path / "db.sqlite"))
    ctx = ToolContext(
        config=config,
        session_id="sess-1",
        workspace=workspace,
        approval_mode="off",
        db=engine.db,
        run_id="run-1",
    )
    agent = engine._build_agent(_request(config, workspace), ctx)
    names = _tool_names(agent)
    assert SKILL_TOOL_NAMES <= names, f"missing skill tools: {SKILL_TOOL_NAMES - names}"


def test_skill_tools_require_no_approval(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    config = _config(skills_enabled=True, skills_dir=SKILLS_DIR, workspace=workspace)
    engine = AdkEngine(config=config, db=Database(tmp_path / "db.sqlite"))
    ctx = ToolContext(
        config=config,
        session_id="sess-1",
        workspace=workspace,
        approval_mode="off",
        db=engine.db,
        run_id="run-1",
    )
    agent = engine._build_agent(_request(config, workspace), ctx)
    for t in agent.tools:
        if getattr(t, "name", None) in SKILL_TOOL_NAMES:
            assert t._require_confirmation is False, (
                f"skill tool {t.name} must not require approval"
            )


def test_skill_catalog_in_instruction(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    config = _config(skills_enabled=True, skills_dir=SKILLS_DIR, workspace=workspace)
    engine = AdkEngine(config=config, db=Database(tmp_path / "db.sqlite"))
    ctx = ToolContext(
        config=config,
        session_id="sess-1",
        workspace=workspace,
        approval_mode="off",
        db=engine.db,
        run_id="run-1",
    )
    agent = engine._build_agent(_request(config, workspace), ctx)
    # The instruction is a provider; call it with no args to get the text.
    instruction = agent.instruction(None) if callable(agent.instruction) else agent.instruction
    assert "load_skill" in instruction
    assert "Available installed skills" in instruction


def test_no_skill_tools_when_disabled(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    config = _config(skills_enabled=False, skills_dir=SKILLS_DIR, workspace=workspace)
    engine = AdkEngine(config=config, db=Database(tmp_path / "db.sqlite"))
    ctx = ToolContext(
        config=config,
        session_id="sess-1",
        workspace=workspace,
        approval_mode="off",
        db=engine.db,
        run_id="run-1",
    )
    agent = engine._build_agent(_request(config, workspace), ctx)
    names = _tool_names(agent)
    assert not (SKILL_TOOL_NAMES & names), (
        f"skill tools must be absent when skills disabled: {SKILL_TOOL_NAMES & names}"
    )


def test_missing_skills_dir_does_not_crash(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    bogus = tmp_path / "does-not-exist"
    config = _config(skills_enabled=True, skills_dir=bogus, workspace=workspace)
    engine = AdkEngine(config=config, db=Database(tmp_path / "db.sqlite"))
    ctx = ToolContext(
        config=config,
        session_id="sess-1",
        workspace=workspace,
        approval_mode="off",
        db=engine.db,
        run_id="run-1",
    )
    # A missing skills dir yields an empty scan (not an exception), so the
    # build must still succeed -- just with an empty catalog.
    agent = engine._build_agent(_request(config, workspace), ctx)
    instruction = agent.instruction(None) if callable(agent.instruction) else agent.instruction
    assert "Available installed skills" not in instruction


# --------------------------------------------------------------------------
# Reasoning persistence in _track
# --------------------------------------------------------------------------


def _manager(tmp_path) -> RunManager:
    config = AppConfig()
    db = Database(tmp_path / "db.sqlite")
    store = SessionStore(tmp_path / "sessions")
    return RunManager(
        config=config,
        db=db,
        session_store=store,
        metrics=None,
        compactor=None,
        engine=None,
    )


def test_reasoning_persisted_to_transcript(tmp_path):
    mgr = _manager(tmp_path)
    record = RunRecord(run_id="run-1", session_id="sess-1")
    mgr._track(record, "reasoning", {"text": "thinking hard"})
    entries = mgr.session_store.read("sess-1")
    reasoning = [e for e in entries if e.get("type") == "reasoning"]
    assert len(reasoning) == 1
    assert reasoning[0]["content"] == "thinking hard"
    assert reasoning[0]["run_id"] == "run-1"


def test_reasoning_deltas_append_in_order(tmp_path):
    mgr = _manager(tmp_path)
    record = RunRecord(run_id="run-1", session_id="sess-1")
    for chunk in ["one ", "two ", "three"]:
        mgr._track(record, "reasoning", {"text": chunk})
    entries = mgr.session_store.read("sess-1")
    reasoning = [e for e in entries if e.get("type") == "reasoning"]
    assert [e["content"] for e in reasoning] == ["one ", "two ", "three"]


def test_empty_reasoning_not_persisted(tmp_path):
    mgr = _manager(tmp_path)
    record = RunRecord(run_id="run-1", session_id="sess-1")
    mgr._track(record, "reasoning", {"text": ""})
    entries = mgr.session_store.read("sess-1")
    assert [e for e in entries if e.get("type") == "reasoning"] == []
