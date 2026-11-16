"""Tests for the Gortex codebase-lookup policy (issue #54).

The policy names tools that only exist through a live Gortex MCP server, so it
must appear in the system prompt ONLY when such a server is connected, and be
absent otherwise (a deployment without Gortex pays no tokens for it).

Run:  cd claudio-core && .venv/bin/python -m pytest tests/test_instructions_gortex.py -q
"""
from __future__ import annotations

from pathlib import Path

from claudio_core.instructions import (
    CODE_DISCOVERY_POLICY,
    build_static_instructions,
    render_code_discovery_policy,
)
from claudio_core.settings import AppConfig


def test_policy_absent_without_gortex():
    assert render_code_discovery_policy([]) == ""
    assert render_code_discovery_policy(None) == ""
    assert render_code_discovery_policy([{"id": "SkillMD.com", "tools": []}]) == ""


def test_policy_present_when_gortex_connected():
    out = render_code_discovery_policy([{"id": "gortex", "tools": ["gortex_search"]}])
    assert out == CODE_DISCOVERY_POLICY
    assert "Codebase lookup policy" in out
    assert "Gortex" in out


def test_policy_id_match_is_case_insensitive():
    assert render_code_discovery_policy([{"id": "Gortex Code"}]) == CODE_DISCOVERY_POLICY


def _config(tmp_path: Path) -> AppConfig:
    return AppConfig.model_validate(
        {"agent": {"workspace_dir": str(tmp_path), "skills_enabled": False,
                   "tools": {"enabled": ["read_file"], "disabled": []}}}
    )


def test_static_instruction_omits_policy_without_gortex(tmp_path):
    prompt = build_static_instructions(
        _config(tmp_path), "sess-1", mcp_entries=[{"id": "other", "tools": []}],
        workspace=tmp_path,
    )
    assert "Codebase lookup policy" not in prompt


def test_static_instruction_includes_policy_with_gortex(tmp_path):
    prompt = build_static_instructions(
        _config(tmp_path), "sess-1",
        mcp_entries=[{"id": "gortex", "tools": ["gortex_search"], "resources": []}],
        workspace=tmp_path,
    )
    assert "Codebase lookup policy" in prompt
