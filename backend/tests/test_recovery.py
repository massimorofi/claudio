"""Unit tests for the run-recovery layer: compaction wiring, budget enforcement,
retry/backoff, fallback model, and reactive compaction on context overflow.

These exercise ``RunManager._run_with_recovery`` / ``_run_stream`` with the SDK
call stubbed out, so the recovery *policy* is tested deterministically without a
live model.
"""
from __future__ import annotations

import types

import pytest

import claudio.runs.manager as mgr
from claudio.runs.manager import RunManager, BudgetExceededError
from claudio.settings import AppConfig
from claudio.db import Database
from claudio.observability.metrics import Metrics


# ---- fakes ---------------------------------------------------------------


class FakeSession:
    """Minimal stand-in for the SDK SQLiteSession."""

    def __init__(self, items=None):
        self._items = list(items or [])

    async def get_items(self):
        return list(self._items)

    async def add_items(self, items):
        self._items.extend(items)

    async def clear_session(self):
        self._items = []


class FakeCompactor:
    def __init__(self, should=False, compact_ok=True):
        self._should = should
        self.compact_ok = compact_ok
        self.compact_calls = 0

    def should_compact(self, input_tokens):
        return self._should

    def is_disabled(self, session_id):
        return False

    async def compact(self, session, model, session_id):
        self.compact_calls += 1
        return self.compact_ok


def make_run_stream(script):
    """A stub _run_stream that replays a scripted list of outcomes.

    Each entry is either an Exception instance (raised) or a return value.
    Records the agent passed on each call.
    """
    calls = []

    async def fake(record, agent, input_data, tctx, session, base_cost=0.0):
        calls.append(agent)
        idx = len(calls) - 1
        outcome = script[idx] if idx < len(script) else script[-1]
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    fake.calls = calls
    return fake


def make_record(run_id="r1", session_id="s1"):
    return mgr.RunRecord(run_id=run_id, session_id=session_id)


# Every Database opened via make_manager is tracked here so it can be closed
# in teardown. An aiosqlite connection runs a background worker thread; if
# the connection is still open when the test's event loop closes, that thread
# tries to call back into the dead loop and raises "Event loop is closed",
# which pytest surfaces as an unhandled-thread warning. Closing in teardown
# stops the worker before the loop goes away.
_open_dbs: list[Database] = []


async def make_manager(tmp_path, **agent_overrides):
    db = Database(tmp_path / "test.db")
    await db.connect()
    _open_dbs.append(db)
    await db.create_session("s1", title="test")
    if agent_overrides:
        await db.update_session("s1", **agent_overrides)
    config = AppConfig()
    metrics = Metrics()
    return db, config, metrics


@pytest.fixture(autouse=True)
async def _close_open_dbs():
    yield
    while _open_dbs:
        db = _open_dbs.pop()
        try:
            await db.close()
        except Exception:  # noqa: BLE001 — best-effort teardown
            pass


# ---- helper function tests ----------------------------------------------


def test_backoff_bounds():
    # attempt 1 ~0.5s, grows exponentially, capped at 30s + jitter
    assert 0.5 <= mgr._backoff_seconds(1) <= 0.75
    assert mgr._backoff_seconds(2) > mgr._backoff_seconds(1)
    assert mgr._backoff_seconds(20) <= 30.25


def test_is_context_overflow_detection():
    assert mgr._is_context_overflow(Exception("This model's maximum context length is 4096"))
    assert mgr._is_context_overflow(Exception("Please reduce the length of the messages"))
    assert mgr._is_context_overflow(Exception("context length exceeded"))
    assert not mgr._is_context_overflow(Exception("rate limit exceeded"))
    assert not mgr._is_context_overflow(Exception("bad request"))


def test_classify_budget_error():
    code, retryable = mgr._classify_error(BudgetExceededError(6.0, 5.0))
    assert code == "budget_exceeded"
    assert retryable is False


def test_last_response_input_tokens():
    resp = types.SimpleNamespace(usage=types.SimpleNamespace(input_tokens=1234))
    result = types.SimpleNamespace(raw_responses=[
        types.SimpleNamespace(usage=types.SimpleNamespace(input_tokens=100)),
        resp,
    ])
    assert mgr._last_response_input_tokens(result, 0) == 1234
    assert mgr._last_response_input_tokens(types.SimpleNamespace(raw_responses=[]), 42) == 42


def test_estimate_items_tokens():
    # The estimator sums every string value (content AND role/keys) at ~4 chars/token.
    items = [
        {"role": "user", "content": "x" * 200},
        {"role": "assistant", "content": "y" * 200},
    ]
    expected = (len("user") + 200 + len("assistant") + 200) // 4
    assert mgr._estimate_items_tokens(items) == expected
    assert mgr._estimate_items_tokens([]) == 0
    # nested blocks and tool args are counted too
    nested = [
        {"role": "assistant", "content": [{"type": "text", "text": "a" * 100}]},
        {"type": "function_call", "arguments": "{\"q\": \"" + "b" * 100 + "\"}"},
    ]
    nested_chars = (
        len("assistant") + len("text") + 100
        + len("function_call") + len("{\"q\": \"" + "b" * 100 + "\"}")
    )
    assert mgr._estimate_items_tokens(nested) == nested_chars // 4


def test_estimate_handles_mixed_types():
    # non-string leaves (numbers, bools, None) contribute nothing
    items = [{"a": 1, "b": True, "c": None, "d": "hello"}]
    assert mgr._estimate_items_tokens(items) == len("hello") // 4


# ---- retry tests ---------------------------------------------------------


async def test_retry_then_success(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "_backoff_seconds", lambda a: 0.0)
    db, config, metrics = await make_manager(tmp_path)
    config.llm.retry_attempts = 3
    config.agent.max_budget_usd = 0.0  # disable budget for this test
    compactor = FakeCompactor()
    session = FakeSession()
    rm = RunManager(config, db, None, metrics, compactor)
    rm._run_stream = make_run_stream([
        RuntimeError("connection reset"),
        RuntimeError("connection reset"),
        "ok",
    ])

    record = make_record()
    await rm._run_with_recovery(record, object(), "hi", None, session)

    assert metrics.retry_count == 2
    assert len(rm._run_stream.calls) == 3
    # two run_restarting events published
    restarting = [e for (_, e) in record.events if e.get("event") == "run_restarting"]
    assert len(restarting) == 2
    assert restarting[0]["data"]["reason"] == "llm_unavailable"


async def test_retry_exhausted_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "_backoff_seconds", lambda a: 0.0)
    db, config, metrics = await make_manager(tmp_path)
    config.llm.retry_attempts = 2
    config.agent.max_budget_usd = 0.0
    compactor = FakeCompactor()
    session = FakeSession()
    rm = RunManager(config, db, None, metrics, compactor)
    rm._run_stream = make_run_stream([RuntimeError("429 rate limit")] * 10)

    record = make_record()
    with pytest.raises(RuntimeError):
        await rm._run_with_recovery(record, object(), "hi", None, session)

    # initial attempt + 2 retries = 3 calls
    assert len(rm._run_stream.calls) == 3
    assert metrics.retry_count == 2


async def test_non_retryable_fails_immediately(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "_backoff_seconds", lambda a: 0.0)
    db, config, metrics = await make_manager(tmp_path)
    config.llm.retry_attempts = 3
    config.agent.max_budget_usd = 0.0
    rm = RunManager(config, db, None, metrics, FakeCompactor())
    rm._run_stream = make_run_stream([RuntimeError("invalid api key 401")])

    record = make_record()
    with pytest.raises(RuntimeError):
        await rm._run_with_recovery(record, object(), "hi", None, session := FakeSession())

    assert len(rm._run_stream.calls) == 1
    assert metrics.retry_count == 0


# ---- fallback tests ----------------------------------------------------


async def test_fallback_model_switch(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "_backoff_seconds", lambda a: 0.0)
    db, config, metrics = await make_manager(tmp_path)
    config.llm.retry_attempts = 0
    config.llm.fallback_model = "fallback-model-x"
    config.agent.max_budget_usd = 0.0

    def fake_build(cfg, model_override=None):
        return types.SimpleNamespace(model_name=model_override or cfg.llm.model)

    monkeypatch.setattr(mgr, "build_primary_agent", fake_build)
    rm = RunManager(config, db, None, metrics, FakeCompactor())
    rm._run_stream = make_run_stream([
        RuntimeError("503 overloaded"),
        "ok",
    ])

    original = types.SimpleNamespace(model_name=config.llm.model)
    record = make_record()
    await rm._run_with_recovery(record, original, "hi", None, FakeSession())

    assert rm._run_stream.calls[0].model_name == config.llm.model
    assert rm._run_stream.calls[1].model_name == "fallback-model-x"
    restarting = [e for (_, e) in record.events if e.get("event") == "run_restarting"]
    assert any(e["data"]["reason"] == "fallback" for e in restarting)


async def test_fallback_not_used_when_disabled(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "_backoff_seconds", lambda a: 0.0)
    db, config, metrics = await make_manager(tmp_path)
    config.llm.retry_attempts = 0
    config.llm.fallback_model = None
    config.agent.max_budget_usd = 0.0
    rm = RunManager(config, db, None, metrics, FakeCompactor())
    rm._run_stream = make_run_stream([RuntimeError("boom")] * 5)

    record = make_record()
    with pytest.raises(RuntimeError):
        await rm._run_with_recovery(record, object(), "hi", None, FakeSession())
    assert len(rm._run_stream.calls) == 1


# ---- budget tests ------------------------------------------------------


async def test_budget_refused_at_run_start(tmp_path):
    db, config, metrics = await make_manager(tmp_path, total_cost_usd=10.0)
    config.agent.max_budget_usd = 5.0
    rm = RunManager(config, db, None, metrics, FakeCompactor())
    rm._run_stream = make_run_stream(["ok"])

    record = make_record()
    with pytest.raises(BudgetExceededError):
        await rm._run_with_recovery(record, object(), "hi", None, FakeSession())
    # budget gate fires before any streaming attempt
    assert len(rm._run_stream.calls) == 0


async def test_budget_zero_disables_enforcement(tmp_path):
    db, config, metrics = await make_manager(tmp_path, total_cost_usd=999.0)
    config.agent.max_budget_usd = 0.0
    rm = RunManager(config, db, None, metrics, FakeCompactor())
    rm._run_stream = make_run_stream(["ok"])
    record = make_record()
    # should NOT raise despite huge spend, because budget=0 means disabled
    await rm._run_with_recovery(record, object(), "hi", None, FakeSession())
    assert len(rm._run_stream.calls) == 1


async def test_budget_mid_run_exceeded(tmp_path):
    """_run_stream raises BudgetExceededError when cumulative spend crosses the cap."""
    db, config, metrics = await make_manager(tmp_path)
    config.agent.max_budget_usd = 5.0
    config.llm.price_input_per_mtok = 1000.0  # so 10k input tokens = $10
    config.llm.price_output_per_mtok = 0.0

    # Fake streamed result: one response whose usage already exceeds budget.
    usage = types.SimpleNamespace(input_tokens=10_000, output_tokens=10)
    resp = types.SimpleNamespace(usage=usage)

    class FakeStreamResult:
        raw_responses = [resp]
        interruptions = []
        final_output = "done"

        async def stream_events(self):
            yield types.SimpleNamespace(type="response.output_text.delta")

    fake_result = FakeStreamResult()
    monkey = types.SimpleNamespace(run_streamed=lambda *a, **k: fake_result)
    monkeypatch_runner(monkey)

    rm = RunManager(config, db, None, metrics, FakeCompactor())
    record = make_record()
    with pytest.raises(BudgetExceededError):
        await rm._run_stream(record, object(), "hi", None, FakeSession(), base_cost=0.0)


def monkeypatch_runner(fake_runner):
    mgr.Runner = fake_runner


# ---- compaction wiring tests ------------------------------------------


async def test_proactive_compaction_at_start(tmp_path):
    # last_context_tokens above the compact threshold triggers compaction.
    db, config, metrics = await make_manager(tmp_path, last_context_tokens=30000)
    config.agent.max_budget_usd = 0.0
    config.agent.context_window = 32768
    config.agent.compaction_reserve = 8192  # compact_at = 24576
    compactor = FakeCompactor(should=True, compact_ok=True)
    rm = RunManager(config, db, None, metrics, compactor)
    rm._run_stream = make_run_stream(["ok"])

    record = make_record()
    await rm._run_with_recovery(record, object(), "hi", None, FakeSession())
    assert compactor.compact_calls >= 1


async def test_no_proactive_compaction_when_small(tmp_path):
    db, config, metrics = await make_manager(tmp_path, last_context_tokens=100)
    config.agent.max_budget_usd = 0.0
    compactor = FakeCompactor(should=False)
    rm = RunManager(config, db, None, metrics, compactor)
    rm._run_stream = make_run_stream(["ok"])
    record = make_record()
    await rm._run_with_recovery(record, object(), "hi", None, FakeSession())
    assert compactor.compact_calls == 0


async def test_proactive_compaction_uses_content_estimate(tmp_path):
    """A threshold-driven compactor (mirroring the real one) fires on a large
    session even though the provider reports no usage."""

    class ThresholdCompactor:
        def __init__(self, window, reserve):
            self.window = window
            self.reserve = reserve
            self.compact_calls = 0

        def should_compact(self, input_tokens):
            return input_tokens >= (self.window - self.reserve)

        def is_disabled(self, session_id):
            return False

        async def compact(self, session, model, session_id):
            self.compact_calls += 1
            await session.clear_session()
            await session.add_items([{"role": "user", "content": "summary"}])
            return True

    # ~120k chars of content -> ~30k tokens, over the 24576 threshold.
    big = FakeSession(items=[{"role": "user", "content": "x" * 60000},
                           {"role": "assistant", "content": "y" * 60000}])
    db, config, metrics = await make_manager(tmp_path)
    config.agent.max_budget_usd = 0.0
    config.agent.context_window = 32768
    config.agent.compaction_reserve = 8192
    compactor = ThresholdCompactor(32768, 8192)
    rm = RunManager(config, db, None, metrics, compactor)
    rm._run_stream = make_run_stream(["ok"])
    record = make_record()
    await rm._run_with_recovery(record, object(), "hi", None, big)
    assert compactor.compact_calls == 1
    # after compaction the session is small again
    assert mgr._estimate_items_tokens(await big.get_items()) < (32768 - 8192)


async def test_reactive_compaction_on_overflow(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "_backoff_seconds", lambda a: 0.0)
    db, config, metrics = await make_manager(tmp_path, last_context_tokens=0)
    config.llm.retry_attempts = 3
    config.agent.max_budget_usd = 0.0
    compactor = FakeCompactor(should=False, compact_ok=True)
    rm = RunManager(config, db, None, metrics, compactor)
    # First attempt overflows the context; after compaction the retry succeeds.
    rm._run_stream = make_run_stream([
        Exception("This model's maximum context length is 4096 tokens"),
        "ok",
    ])
    record = make_record()
    await rm._run_with_recovery(record, object(), "hi", None, FakeSession())
    # reactive compaction fired once
    assert compactor.compact_calls >= 1
    assert len(rm._run_stream.calls) == 2


async def test_reactive_compaction_only_once(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "_backoff_seconds", lambda a: 0.0)
    db, config, metrics = await make_manager(tmp_path, last_context_tokens=0)
    config.llm.retry_attempts = 5
    config.agent.max_budget_usd = 0.0
    compactor = FakeCompactor(should=False, compact_ok=True)
    rm = RunManager(config, db, None, metrics, compactor)
    # Every attempt overflows; reactive compaction must not loop forever.
    rm._run_stream = make_run_stream(
        [Exception("maximum context length exceeded")] * 10
    )
    record = make_record()
    with pytest.raises(Exception):
        await rm._run_with_recovery(record, object(), "hi", None, FakeSession())
    # reactive compaction is capped at one per run
    assert compactor.compact_calls == 1


# ---- session rollback on retry ----------------------------------------


async def test_session_rolled_back_to_snapshot_on_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(mgr, "_backoff_seconds", lambda a: 0.0)
    db, config, metrics = await make_manager(tmp_path)
    config.llm.retry_attempts = 2
    config.agent.max_budget_usd = 0.0
    rm = RunManager(config, db, None, metrics, FakeCompactor())

    session = FakeSession(items=["base"])

    # Simulate the SDK appending to the session during a failed attempt.
    calls = []

    async def fake(record, agent, input_data, tctx, sess, base_cost=0.0):
        calls.append(list(sess._items))
        if len(calls) < 3:
            await sess.add_items(["partial"])  # dirty the session
            raise RuntimeError("connection error")
        return "ok"

    rm._run_stream = fake
    record = make_record()
    await rm._run_with_recovery(record, object(), "hi", None, session)

    # On each retry the session was rolled back to the snapshot ["base"].
    assert calls[0] == ["base"]
    assert calls[1] == ["base"]
    assert calls[2] == ["base"]
