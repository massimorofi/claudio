"""Issue #58 Increment 6 -- benchmark aggregation (core side).

Run:  cd claudio-core && .venv/bin/python -m pytest tests/test_issue58_bench.py -q
"""
from __future__ import annotations

from claudio_core.bench import TrialMetrics, percentile, render_markdown, summarize


def test_percentile_basic():
    assert percentile([], 0.5) == 0.0
    assert percentile([10], 0.9) == 10
    vals = list(range(1, 11))  # 1..10
    assert percentile(vals, 0.5) in (5, 6)
    assert percentile(vals, 0.9) >= 9


def test_summarize_medians_and_pass_rate():
    trials = [
        TrialMetrics("read", model_calls=2, input_tokens=100, verified=True),
        TrialMetrics("read", model_calls=4, input_tokens=200, verified=False),
        TrialMetrics("edit", model_calls=6, input_tokens=300, verified=True),
    ]
    report = summarize(trials)
    assert report["overall"]["n"] == 3
    assert report["overall"]["model_calls"]["median"] == 4
    assert report["by_task_class"]["read"]["all"]["verify_pass_rate"] == 0.5
    assert report["by_task_class"]["edit"]["all"]["model_calls"]["median"] == 6


def test_summarize_splits_cold_warm():
    trials = [
        TrialMetrics("read", cold=True, model_calls=5),
        TrialMetrics("read", cold=False, model_calls=2),
    ]
    report = summarize(trials)
    assert report["by_task_class"]["read"]["cold"]["model_calls"]["median"] == 5
    assert report["by_task_class"]["read"]["warm"]["model_calls"]["median"] == 2


def test_render_markdown_has_table():
    report = summarize([TrialMetrics("read", model_calls=2, verified=True)])
    md = render_markdown(report)
    assert "# Claudio performance benchmark" in md
    assert "| read | model_calls |" in md
