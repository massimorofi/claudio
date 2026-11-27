"""Issue #58 Increment 0a(b)-ii -- every *auxiliary* call site is purpose-tagged.

Increment 0a(b)-i proved that one logical call through
``claudio_core.llm.openai_chat_completion`` emits exactly one ledger record.
This increment closes the other half of "complete call coverage": the
non-primary call sites must each reach the ledger **with the right ``purpose``**
(values from ``claudio_core.accounting.PURPOSES``) and with
``run_id``/``session_id``/``model_role`` filled in where they are known.

Covered here, one test group per call site:

* ``claudio_core.learning.extract_learnings``          -> ``learning``
* ``runs.manager.RunManager._build_summariser``       -> ``compaction`` (Tier-2)
* ``runs.manager.RunManager._auto_route_role``        -> ``router``
* ``autonomous.fixer.propose_fix``                    -> ``fixer``
* ``autonomous.consolidation._merge_categories``      -> ``consolidation``
* ``autonomous.consolidation._draft_skill``           -> ``consolidation``
* ``api.config.run_llm_probe`` (/config/llm/test)     -> ``probe``
* ``engine.reasoning_llm.make_openai_client`` ctx     -> ``daemon``

For each one: **exactly one** record on success with the expected purpose, and
still **exactly one** record when the call fails (``outcome="error"``) or is
cancelled (``outcome="cancelled"``) -- a branch that used to record nothing.

Everything is offline: ``httpx.AsyncClient`` is monkeypatched onto an in-memory
``httpx.MockTransport`` and the engine SDK client is replaced by a stdlib stub,
so no provider credentials and no non-loopback socket are involved.

Run:
    cd claudio-backend && PYTHONPATH="$(pwd)/src:$(pwd)/../claudio-core/src" \\
        python3 -m pytest tests/test_issue58_aux_purposes.py \\
        -p no:cacheprovider -o asyncio_mode=strict
"""
from __future__ import annotations

import asyncio
import copy
import json
from types import SimpleNamespace

import httpx
import pytest

from claudio_core.accounting import PURPOSES, CallAccountant, set_default_recorder
from claudio_core.learning import extract_learnings
from claudio_core.settings import AppConfig, resolve_profile

BASE_URL = "http://127.0.0.1:9/v1"  # never contacted: the transport is faked


# ---------------------------------------------------------------------------
# fakes
# ---------------------------------------------------------------------------


def completion_body(text: str = "OK", usage: dict | None = None) -> dict:
    body = {
        "id": "chatcmpl-aux",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}}
        ],
    }
    if usage is not None:
        body["usage"] = usage
    return body


USAGE = {
    "prompt_tokens": 120,
    "completion_tokens": 34,
    "prompt_tokens_details": {"cached_tokens": 40},
}


def install_transport(monkeypatch, handler) -> None:
    """Point ``httpx.AsyncClient`` (as used by claudio_core.llm) at a fake."""
    real = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)


def ok_transport(text: str = "OK"):
    """Handler that answers 200 with a completion body."""

    def handler(request):
        return httpx.Response(200, json=completion_body(text, USAGE))

    return handler


def failing_transport(status: int = 500):
    def handler(request):
        return httpx.Response(status, json={"error": {"message": "nope"}})

    return handler


def cancelling_transport(request):
    raise asyncio.CancelledError()


@pytest.fixture
def accountant():
    """A fresh ledger, with the process-wide default recorder cleared.

    Tests that exercise the "no run is live" path install their own recorder,
    exactly as ``supervisor.start()`` does in production.
    """
    set_default_recorder(None)
    acc = CallAccountant()
    yield acc
    set_default_recorder(None)


def sink_for(acc: CallAccountant, purpose: str, **ids):
    """A purpose-tagged sink on the shared accountant (the production shape)."""
    assert purpose in PURPOSES
    return acc.make_llm_sink(
        run_id=ids.get("run_id", ""),
        session_id=ids.get("session_id", ""),
        purpose=purpose,
        model=ids.get("model", "fast-model"),
        model_role=ids.get("model_role", ""),
    )


def only(acc: CallAccountant, run_id: str = ""):
    """Assert exactly one record exists for ``run_id`` and return it."""
    recs = acc.llm_calls(run_id)
    assert len(recs) == 1, f"expected exactly 1 record, got {len(recs)}: {recs}"
    return recs[0]


# ---------------------------------------------------------------------------
# learning -- claudio_core/learning.py
# ---------------------------------------------------------------------------

TRANSCRIPT = [
    {"type": "user", "content": "nginx keeps crashing on this box"},
    {"type": "tool_result", "name": "run_command", "content": "unit failed"},
]


@pytest.mark.asyncio
async def test_learning_emits_one_record_tagged_learning(accountant, monkeypatch):
    install_transport(monkeypatch, ok_transport("[]"))
    sink = sink_for(
        accountant, "learning", run_id="run-L", session_id="sess-L", model_role="fast"
    )

    await extract_learnings(TRANSCRIPT, AppConfig(), usage_sink=sink)

    rec = only(accountant, "run-L")
    assert rec.purpose == "learning"
    assert rec.run_id == "run-L" and rec.session_id == "sess-L"
    assert rec.model_role == "fast"
    assert rec.outcome == "ok" and rec.measured


@pytest.mark.asyncio
async def test_learning_failure_still_emits_one_error_record(accountant, monkeypatch):
    install_transport(monkeypatch, failing_transport())
    sink = sink_for(accountant, "learning", run_id="run-L", session_id="sess-L")

    from claudio_core.learning import extract_learnings

    assert await extract_learnings(TRANSCRIPT, AppConfig(), usage_sink=sink) == []

    rec = only(accountant, "run-L")
    assert rec.purpose == "learning" and rec.outcome == "error"


@pytest.mark.asyncio
async def test_learning_cancellation_emits_one_cancelled_record(accountant, monkeypatch):
    install_transport(monkeypatch, cancelling_transport)
    sink = sink_for(accountant, "learning", run_id="run-L", session_id="sess-L")

    from claudio_core.learning import extract_learnings

    with pytest.raises(asyncio.CancelledError):
        await extract_learnings(TRANSCRIPT, AppConfig(), usage_sink=sink)

    rec = only(accountant, "run-L")
    assert rec.purpose == "learning" and rec.outcome == "cancelled"


@pytest.mark.asyncio
async def test_learning_without_a_sink_uses_the_default_recorder(accountant, monkeypatch):
    """No run live -> the process-wide recorder still tags it ``learning``."""
    install_transport(monkeypatch, ok_transport("[]"))
    set_default_recorder(accountant.make_engine_recorder())

    from claudio_core.learning import extract_learnings

    await extract_learnings(TRANSCRIPT, AppConfig())

    rec = only(accountant, "")
    assert rec.purpose == "learning"


# ---------------------------------------------------------------------------
# compaction (Tier-2) and router -- runs/manager.py
# ---------------------------------------------------------------------------


def make_manager(acc: CallAccountant):
    from claudio_backend.runs import manager as manager_mod

    config = AppConfig()
    mgr = manager_mod.RunManager(
        config=config,
        db=None,
        session_store=None,
        metrics=None,
        compactor=None,
        engine=None,
        accountant=acc,
    )
    return mgr, config


def record_ids():
    return SimpleNamespace(run_id="run-C", session_id="sess-C")


@pytest.mark.asyncio
async def test_tier2_compaction_emits_one_record_tagged_compaction(accountant, monkeypatch):
    install_transport(monkeypatch, ok_transport("summary text"))
    mgr, _ = make_manager(accountant)

    summarise = mgr._build_summariser(record_ids())
    assert await summarise("summarise this") == "summary text"

    rec = only(accountant, "run-C")
    assert rec.purpose == "compaction"
    assert rec.run_id == "run-C" and rec.session_id == "sess-C"
    assert rec.model_role == "fast" and rec.outcome == "ok"


@pytest.mark.asyncio
async def test_tier2_compaction_failure_emits_one_error_record(accountant, monkeypatch):
    from claudio_core.llm import LlmCallError

    install_transport(monkeypatch, failing_transport())
    mgr, _ = make_manager(accountant)

    with pytest.raises(LlmCallError):
        await mgr._build_summariser(record_ids())("summarise this")

    rec = only(accountant, "run-C")
    assert rec.purpose == "compaction" and rec.outcome == "error"


@pytest.mark.asyncio
async def test_tier2_compaction_cancellation_emits_one_cancelled_record(
    accountant, monkeypatch
):
    install_transport(monkeypatch, cancelling_transport)
    mgr, _ = make_manager(accountant)

    with pytest.raises(asyncio.CancelledError):
        await mgr._build_summariser(record_ids())("summarise this")

    rec = only(accountant, "run-C")
    assert rec.purpose == "compaction" and rec.outcome == "cancelled"


def enable_router(monkeypatch, config):
    """Force the router LLM path: routing on, deterministic pre-check off.

    The fast and primary profiles are made distinct so the "same model, skip the
    call" short-circuit cannot swallow the audited call site.
    """
    from claudio_backend.runs import manager as manager_mod

    config.agent.auto_routing_enabled = True
    config.agent.router_deterministic_first = False
    real = manager_mod.resolve_profile

    def fake(cfg, role):
        prof = copy.deepcopy(real(cfg, role))
        prof.base_url = BASE_URL
        prof.model = "fast-router" if role == "fast" else "primary-model"
        return prof

    monkeypatch.setattr(manager_mod, "resolve_profile", fake)


@pytest.mark.asyncio
async def test_router_emits_one_record_tagged_router(accountant, monkeypatch):
    install_transport(monkeypatch, ok_transport("FAST"))
    mgr, config = make_manager(accountant)
    enable_router(monkeypatch, config)

    role = await mgr._auto_route_role("what does uname do?", record_ids())

    assert role == "fast"
    rec = only(accountant, "run-C")
    assert rec.purpose == "router"
    assert rec.run_id == "run-C" and rec.session_id == "sess-C"
    assert rec.model_role == "fast" and rec.outcome == "ok"


@pytest.mark.asyncio
async def test_router_failure_emits_one_error_record(accountant, monkeypatch):
    install_transport(monkeypatch, failing_transport())
    mgr, config = make_manager(accountant)
    enable_router(monkeypatch, config)

    assert await mgr._auto_route_role("what does uname do?", record_ids()) == "primary"

    rec = only(accountant, "run-C")
    assert rec.purpose == "router" and rec.outcome == "error"


@pytest.mark.asyncio
async def test_router_cancellation_emits_one_cancelled_record(accountant, monkeypatch):
    install_transport(monkeypatch, cancelling_transport)
    mgr, config = make_manager(accountant)
    enable_router(monkeypatch, config)

    with pytest.raises(asyncio.CancelledError):
        await mgr._auto_route_role("what does uname do?", record_ids())

    rec = only(accountant, "run-C")
    assert rec.purpose == "router" and rec.outcome == "cancelled"


# ---------------------------------------------------------------------------
# fixer -- autonomous/fixer.py
# ---------------------------------------------------------------------------

ISSUE = {"title": "disk full", "severity": "high", "detail": "var is at 100%"}
# No systemd unit token -> validation passes without probing the host.
FIX_JSON = json.dumps({"command": "sync", "rationale": "flush buffers"})


@pytest.mark.asyncio
async def test_fixer_emits_one_record_tagged_fixer(accountant, monkeypatch):
    from claudio_backend.autonomous.fixer import propose_fix

    install_transport(monkeypatch, ok_transport(FIX_JSON))
    sink = sink_for(accountant, "fixer", run_id="run-F", model_role="fast")

    await propose_fix(ISSUE, AppConfig(), usage_sink=sink)

    rec = only(accountant, "run-F")
    assert rec.purpose == "fixer" and rec.run_id == "run-F"
    assert rec.model_role == "fast" and rec.outcome == "ok"


@pytest.mark.asyncio
async def test_fixer_failure_emits_one_error_record(accountant, monkeypatch):
    from claudio_backend.autonomous.fixer import propose_fix
    from claudio_core.llm import LlmCallError

    install_transport(monkeypatch, failing_transport())
    sink = sink_for(accountant, "fixer", run_id="run-F")

    with pytest.raises(LlmCallError):
        await propose_fix(ISSUE, AppConfig(), usage_sink=sink)

    rec = only(accountant, "run-F")
    assert rec.purpose == "fixer" and rec.outcome == "error"


@pytest.mark.asyncio
async def test_fixer_cancellation_emits_one_cancelled_record(accountant, monkeypatch):
    from claudio_backend.autonomous.fixer import propose_fix

    install_transport(monkeypatch, cancelling_transport)
    sink = sink_for(accountant, "fixer", run_id="run-F")

    with pytest.raises(asyncio.CancelledError):
        await propose_fix(ISSUE, AppConfig(), usage_sink=sink)

    rec = only(accountant, "run-F")
    assert rec.purpose == "fixer" and rec.outcome == "cancelled"


@pytest.mark.asyncio
async def test_fixer_without_a_sink_uses_the_default_recorder(accountant, monkeypatch):
    install_transport(monkeypatch, ok_transport(FIX_JSON))
    set_default_recorder(accountant.make_engine_recorder())

    from claudio_backend.autonomous.fixer import propose_fix

    await propose_fix(ISSUE, AppConfig())

    assert only(accountant, "").purpose == "fixer"


# ---------------------------------------------------------------------------
# consolidation -- autonomous/consolidation.py (two call sites)
# ---------------------------------------------------------------------------

MERGE_JSON = json.dumps({"rocm": "amd-gpu", "amd-gpu": "amd-gpu"})
SKILL_TEXT = "Guidance on AMD GPUs\n---BODY---\nCheck `rocm-smi` first."


@pytest.mark.asyncio
async def test_consolidation_merge_emits_one_record(accountant, monkeypatch):
    from claudio_backend.autonomous.consolidation import _merge_categories

    install_transport(monkeypatch, ok_transport(MERGE_JSON))
    sink = sink_for(accountant, "consolidation", run_id="run-X", model_role="fast")

    mapping = await _merge_categories(
        ["rocm", "amd-gpu"], {"rocm": "gpu driver"}, AppConfig(), usage_sink=sink
    )

    assert mapping["rocm"] == "amd-gpu"
    rec = only(accountant, "run-X")
    assert rec.purpose == "consolidation" and rec.run_id == "run-X"
    assert rec.model_role == "fast" and rec.outcome == "ok"


@pytest.mark.asyncio
async def test_consolidation_merge_failure_emits_one_error_record(accountant, monkeypatch):
    from claudio_backend.autonomous.consolidation import _merge_categories

    install_transport(monkeypatch, failing_transport())
    sink = sink_for(accountant, "consolidation", run_id="run-X")

    mapping = await _merge_categories(
        ["rocm", "amd-gpu"], {}, AppConfig(), usage_sink=sink
    )

    assert mapping == {"rocm": "rocm", "amd-gpu": "amd-gpu"}
    rec = only(accountant, "run-X")
    assert rec.purpose == "consolidation" and rec.outcome == "error"


@pytest.mark.asyncio
async def test_consolidation_merge_cancellation_emits_one_record(accountant, monkeypatch):
    from claudio_backend.autonomous.consolidation import _merge_categories

    install_transport(monkeypatch, cancelling_transport)
    sink = sink_for(accountant, "consolidation", run_id="run-X")

    with pytest.raises(asyncio.CancelledError):
        await _merge_categories(
            ["rocm", "amd-gpu"], {}, AppConfig(), usage_sink=sink
        )

    rec = only(accountant, "run-X")
    assert rec.purpose == "consolidation" and rec.outcome == "cancelled"


@pytest.mark.asyncio
async def test_consolidation_draft_emits_one_record(accountant, monkeypatch):
    from claudio_backend.autonomous.consolidation import _draft_skill

    install_transport(monkeypatch, ok_transport(SKILL_TEXT))
    sink = sink_for(accountant, "consolidation", run_id="run-X", model_role="fast")

    description, body = await _draft_skill(
        "amd-gpu", [{"title": "rocm needs a reboot"}], None, AppConfig(),
        usage_sink=sink,
    )

    assert description and "rocm-smi" in body
    rec = only(accountant, "run-X")
    assert rec.purpose == "consolidation" and rec.outcome == "ok"
    assert rec.input_tokens == 120 and rec.cached_input_tokens == 40


@pytest.mark.asyncio
async def test_consolidation_draft_failure_emits_one_error_record(accountant, monkeypatch):
    from claudio_backend.autonomous.consolidation import _draft_skill
    from claudio_core.llm import LlmCallError

    install_transport(monkeypatch, failing_transport())
    sink = sink_for(accountant, "consolidation", run_id="run-X")

    with pytest.raises(LlmCallError):
        await _draft_skill(
            "amd-gpu", [{"title": "t"}], None, AppConfig(), usage_sink=sink
        )

    rec = only(accountant, "run-X")
    assert rec.purpose == "consolidation" and rec.outcome == "error"


@pytest.mark.asyncio
async def test_consolidation_draft_cancellation_emits_one_record(accountant, monkeypatch):
    from claudio_backend.autonomous.consolidation import _draft_skill

    install_transport(monkeypatch, cancelling_transport)
    sink = sink_for(accountant, "consolidation", run_id="run-X")

    with pytest.raises(asyncio.CancelledError):
        await _draft_skill(
            "amd-gpu", [{"title": "t"}], None, AppConfig(), usage_sink=sink
        )

    rec = only(accountant, "run-X")
    assert rec.purpose == "consolidation" and rec.outcome == "cancelled"


# ---------------------------------------------------------------------------
# probe -- POST /config/llm/test
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_probe_emits_one_record_tagged_probe(accountant, monkeypatch):
    from claudio_backend.api.config import run_llm_probe

    install_transport(monkeypatch, ok_transport("OK"))
    sink = sink_for(accountant, "probe", model_role="llm")

    assert await run_llm_probe(
        base_url=BASE_URL, model="m", api_key="k", usage_sink=sink
    ) == "OK"

    rec = only(accountant, "")
    assert rec.purpose == "probe" and rec.model_role == "llm"
    assert rec.outcome == "ok"


@pytest.mark.asyncio
async def test_probe_failure_emits_one_error_record(accountant, monkeypatch):
    from claudio_backend.api.config import run_llm_probe
    from claudio_core.llm import LlmCallError

    install_transport(monkeypatch, failing_transport())
    sink = sink_for(accountant, "probe")

    with pytest.raises(LlmCallError):
        await run_llm_probe(base_url=BASE_URL, model="m", usage_sink=sink)

    rec = only(accountant, "")
    assert rec.purpose == "probe" and rec.outcome == "error"


@pytest.mark.asyncio
async def test_probe_cancellation_emits_one_cancelled_record(accountant, monkeypatch):
    from claudio_backend.api.config import run_llm_probe

    install_transport(monkeypatch, cancelling_transport)
    sink = sink_for(accountant, "probe")

    with pytest.raises(asyncio.CancelledError):
        await run_llm_probe(base_url=BASE_URL, model="m", usage_sink=sink)

    rec = only(accountant, "")
    assert rec.purpose == "probe" and rec.outcome == "cancelled"


@pytest.mark.asyncio
async def test_probe_without_a_sink_uses_the_default_recorder(accountant, monkeypatch):
    install_transport(monkeypatch, ok_transport("OK"))
    set_default_recorder(accountant.make_engine_recorder())

    from claudio_backend.api.config import run_llm_probe

    await run_llm_probe(base_url=BASE_URL, model="m")

    assert only(accountant, "").purpose == "probe"


# ---------------------------------------------------------------------------
# daemon -- the engine SDK boundary with the daemon call context
# ---------------------------------------------------------------------------


class StubCompletions:
    """Stand-in for ``AsyncOpenAI().chat.completions`` (no network)."""

    def __init__(self, behaviour) -> None:
        self.behaviour = behaviour

    async def create(self, *args, **kwargs):
        return await self.behaviour(**kwargs)


class StubAsyncOpenAI:
    def __init__(self, behaviour):
        self._behaviour = behaviour
        self.chat = SimpleNamespace(completions=StubCompletions(behaviour))


def daemon_client(monkeypatch, behaviour):
    """Build the real accounting-wrapped client over a stubbed SDK."""
    from claudio_backend.engine import reasoning_llm

    monkeypatch.setattr(
        reasoning_llm,
        "AsyncOpenAI",
        lambda *a, **k: StubAsyncOpenAI(behaviour),
    )
    return reasoning_llm.make_openai_client(
        BASE_URL,
        "local",
        {"purpose": "daemon", "model": "m", "session_id": "daemon:nightly"},
    )


async def _ok(**kwargs):
    return SimpleNamespace(usage=None)


async def _boom(**kwargs):
    raise RuntimeError("daemon call exploded")


async def _cancel(**kwargs):
    raise asyncio.CancelledError()


@pytest.mark.asyncio
async def test_daemon_emits_one_record_tagged_daemon(accountant, monkeypatch):
    set_default_recorder(accountant.make_engine_recorder())
    client = daemon_client(monkeypatch, _ok)

    await client.chat.completions.create(model="m", messages=[])

    rec = only(accountant, "")
    assert rec.purpose == "daemon"
    assert rec.session_id == "daemon:nightly"
    assert rec.outcome == "ok"


@pytest.mark.asyncio
async def test_daemon_failure_emits_one_error_record(accountant, monkeypatch):
    set_default_recorder(accountant.make_engine_recorder())
    client = daemon_client(monkeypatch, _boom)

    with pytest.raises(RuntimeError):
        await client.chat.completions.create(model="m", messages=[])

    rec = only(accountant, "")
    assert rec.purpose == "daemon" and rec.outcome == "error"


@pytest.mark.asyncio
async def test_daemon_cancellation_emits_one_cancelled_record(accountant, monkeypatch):
    set_default_recorder(accountant.make_engine_recorder())
    client = daemon_client(monkeypatch, _cancel)

    with pytest.raises(asyncio.CancelledError):
        await client.chat.completions.create(model="m", messages=[])

    rec = only(accountant, "")
    assert rec.purpose == "daemon" and rec.outcome == "cancelled"


# ---------------------------------------------------------------------------
# vocabulary
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_emitted_purpose_is_in_the_known_vocabulary(accountant, monkeypatch):
    """No auxiliary path may invent a purpose outside accounting.PURPOSES."""
    install_transport(monkeypatch, ok_transport("OK"))
    set_default_recorder(accountant.make_engine_recorder())

    from claudio_backend.api.config import run_llm_probe

    await run_llm_probe(base_url=BASE_URL, model="m")
    rec = only(accountant, "")
    assert rec.purpose in PURPOSES
