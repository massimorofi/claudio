"""Issue #58 Increment 6 -- benchmark trial collection (backend side).

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_issue58_bench.py -q
"""
from __future__ import annotations

from claudio_core.accounting import CallAccountant, LlmCallRecord
from claudio_core.bench import summarize
from claudio_backend.runs.manager import RunRecord

from claudio_backend.bench import trial_from_run


def test_trial_from_run_reads_ledger():
    acc = CallAccountant()
    acc.record_llm_call(
        LlmCallRecord(run_id="r1", input_tokens=100, cached_input_tokens=40,
                      output_tokens=10)
    )
    acc.record_llm_call(LlmCallRecord(run_id="r1", input_tokens=100,
                                      cached_input_tokens=40, output_tokens=5))
    acc.bump_lifecycle("r1", "tool_calls", 3)
    record = RunRecord(run_id="r1", session_id="s1")
    record.tier1_compactions = 1
    record.retries = 2
    trial = trial_from_run(
        task_class="read", cold=True, record=record, accountant=acc,
        wall_seconds=1.5, time_to_first_output=0.3, verified=True,
    )
    assert trial.model_calls == 2
    assert trial.input_tokens == 200
    assert trial.cached_input_tokens == 80
    assert trial.cache_hit_ratio == 0.4
    assert trial.tool_calls == 3
    assert trial.tier1_compactions == 1
    assert trial.retries == 2
    assert trial.verified is True


def test_trial_flows_into_report():
    acc = CallAccountant()
    acc.record_llm_call(LlmCallRecord(run_id="r1", input_tokens=50))
    record = RunRecord(run_id="r1", session_id="s1")
    trial = trial_from_run(
        task_class="edit", cold=False, record=record, accountant=acc,
        wall_seconds=2.0, time_to_first_output=0.5, verified=True,
    )
    report = summarize([trial])
    assert report["by_task_class"]["edit"]["warm"]["model_calls"]["median"] == 1
