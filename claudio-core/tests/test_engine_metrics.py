"""Tests for the engine-metrics reader (observability/engine_metrics.py)."""
from __future__ import annotations

import pytest

from claudio_core.engine_metrics import (
    EngineMetrics,
    metrics_url,
    parse_prometheus,
)


def test_metrics_url_strips_api_path():
    assert metrics_url("http://192.168.1.172:8731/v1") == "http://192.168.1.172:8731/metrics"
    assert metrics_url("http://localhost:1234/v1/") == "http://localhost:1234/metrics"
    assert metrics_url("http://host:8080") == "http://host:8080/metrics"


def test_parse_prometheus_maps_known_metrics():
    text = """# HELP llamacpp:prompt_tokens_seconds Average prompt throughput.
# TYPE llamacpp:prompt_tokens_seconds gauge
llamacpp:prompt_tokens_seconds 1070.131585
# HELP llamacpp:predicted_tokens_seconds Average generation throughput.
# TYPE llamacpp:predicted_tokens_seconds gauge
llamacpp:predicted_tokens_seconds 56.36962748
llamacpp:prompt_tokens_total 5490350
llamacpp:tokens_predicted_total 365377
llamacpp:kv_cache_usage_ratio 0.8857421875
halogen:requests_total 635
halogen:prompt_tokens_cached_total 42138719
halogen:draft_tokens_total 309951
halogen:draft_tokens_accepted_total 213499
"""
    out = parse_prometheus(text)
    assert out["prefill_tok_s"] == pytest.approx(1070.131585)
    assert out["decode_tok_s"] == pytest.approx(56.36962748)
    assert out["prompt_tokens_processed"] == 5490350
    assert out["prompt_tokens_cached"] == 42138719
    assert out["kv_cache_usage_ratio"] == pytest.approx(0.8857421875)


def test_parse_ignores_unknown_and_malformed():
    text = """some_unknown_metric 42
llamacpp:prompt_tokens_seconds notanumber
brokenline_without_value
# comment
"""
    out = parse_prometheus(text)
    assert "prefill_tok_s" not in out  # value was not a number
    assert out == {}


def test_derived_cache_hit_ratio():
    text = """llamacpp:prompt_tokens_total 1000
halogen:prompt_tokens_cached_total 9000
"""
    out = parse_prometheus(text)
    # _derive runs inside fetch; call it via the module helper path by using
    # EngineMetrics.refresh is async, so test the pure derive through a fetch-like
    # call here instead.
    from claudio_core.engine_metrics import _derive

    d = _derive(out)
    assert d["prompt_cache_hit_ratio"] == pytest.approx(0.9)


def test_derived_handles_zero_total():
    from claudio_core.engine_metrics import _derive

    d = _derive({"prompt_tokens_processed": 0.0, "prompt_tokens_cached": 0.0})
    assert "prompt_cache_hit_ratio" not in d


async def test_engine_metrics_caches_last_good(monkeypatch):
    em = EngineMetrics("http://x/v1")

    async def fake_fetch(base_url, timeout=3.0):
        return {"prefill_tok_s": 1000.0, "decode_tok_s": 50.0}

    monkeypatch.setattr(
        "claudio_core.engine_metrics.fetch_engine_metrics", fake_fetch
    )
    snap = await em.refresh()
    assert snap["prefill_tok_s"] == 1000.0
    assert snap["engine_metrics_available"] is True

    # Now simulate the engine going down: the last good values are retained.
    async def dead_fetch(base_url, timeout=3.0):
        return {}

    monkeypatch.setattr(
        "claudio_core.engine_metrics.fetch_engine_metrics", dead_fetch
    )
    snap2 = await em.refresh()
    assert snap2["prefill_tok_s"] == 1000.0  # retained
    assert snap2["engine_metrics_available"] is False
