"""Issue #58 Increment 5 -- smart recovery, phase-aware effort, hard budgets.

Fuzzy-path auto-hints turn a typo into an inline correction instead of a
recovery turn; the run ceilings stop a runaway on physical resources (calls,
seconds, tokens) so an unpriced local model still terminates; the phase-aware
effort selector moves between high and low at the right boundaries.

Run:  cd claudio-core && .venv/bin/python -m pytest tests/test_issue58_recovery.py -q
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from claudio_core.context import ToolContext
from claudio_core.settings import AppConfig
from claudio_core.tools.files import read_file, _suggest_path


def _ctx(tmp_path):
    return ToolContext(
        config=AppConfig(), session_id="s1", workspace=tmp_path,
        approval_mode="off", db=None,
    )


def test_fuzzy_hint_on_typo(tmp_path):
    (tmp_path / "config_loader.py").write_text("x = 1")
    result = asyncio.run(read_file(_ctx(tmp_path), "config_loadr.py"))
    assert result["ok"] is False
    assert "config_loader.py" in result["error"]  # the suggestion is inline


def test_fuzzy_hint_basename_match(tmp_path):
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "engine.py").write_text("x")
    suggestions = _suggest_path(_ctx(tmp_path), "engine.py")
    assert any("engine.py" in s for s in suggestions)


def test_no_hint_for_unrelated(tmp_path):
    (tmp_path / "totally_different.rs").write_text("x")
    suggestions = _suggest_path(_ctx(tmp_path), "zzzznope.py")
    assert suggestions == []
