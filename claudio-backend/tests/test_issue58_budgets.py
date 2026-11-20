"""Issue #58 Increment 5 -- run ceilings & phase-aware effort (manager side).

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_issue58_budgets.py -q
"""
from __future__ import annotations

import time

import pytest

from claudio_core.accounting import CallAccountant, LlmCallRecord
from claudio_core.settings import AppConfig
from claudio_backend.runs.manager import RunManager, RunRecord


def _mgr(**agent_overrides):
    mgr = RunManager.__new__(RunManager)
    mgr.config = AppConfig.model_validate({"agent": agent_overrides})
    mgr.accountant = CallAccountant()
    return mgr


def _record(**kw):
    rec = RunRecord(run_id="r1", session_id="s1", approval_mode="off")
    rec.started_at = kw.pop("started_at", time.time())
    for k, v in kw.items():
        setattr(rec, k, v)
    return rec


def test_model_call_ceiling():
    mgr = _mgr(max_model_calls=3)
    for _ in range(3):
        mgr.accountant.record_llm_call(LlmCallRecord(run_id="r1"))
    assert mgr._run_ceiling(_record()) == "model_call_ceiling"


def test_token_ceiling():
    mgr = _mgr(max_run_tokens=100)
    mgr.accountant.record_llm_call(
        LlmCallRecord(run_id="r1", input_tokens=60, output_tokens=50)
    )
    assert mgr._run_ceiling(_record()) == "token_ceiling"


def test_time_ceiling():
    mgr = _mgr(max_run_seconds=1)
    rec = _record(started_at=time.time() - 5)
    assert mgr._run_ceiling(rec) == "run_time_exceeded"


def test_no_ceiling_when_unlimited():
    mgr = _mgr()  # all ceilings 0
    mgr.accountant.record_llm_call(LlmCallRecord(run_id="r1", input_tokens=99999))
    assert mgr._run_ceiling(_record()) is None


def test_phase_effort_high_on_first_clean_attempt():
    mgr = _mgr(reasoning_effort_high="high", reasoning_effort_low="low")
    assert mgr._phase_reasoning_effort(_record(attempt=1, retries=0, replans=0)) == "high"


def test_phase_effort_high_after_failure():
    mgr = _mgr(reasoning_effort_high="high", reasoning_effort_low="low")
    assert mgr._phase_reasoning_effort(_record(attempt=2, retries=1, replans=0)) == "high"


def test_phase_effort_low_on_continuation():
    mgr = _mgr(reasoning_effort_high="high", reasoning_effort_low="low")
    # attempt>1 but no failure this attempt (a plain auto-continuation)
    assert mgr._phase_reasoning_effort(_record(attempt=2, retries=0, replans=0)) == "low"


def test_phase_effort_disabled_returns_empty():
    mgr = _mgr()
    assert mgr._phase_reasoning_effort(_record()) == ""
