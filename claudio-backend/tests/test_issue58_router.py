"""Issue #58 Increment 2 -- deterministic router on the manager side.

Asserts the router's LLM call disappears for unambiguous tool work: with
auto-routing enabled and a deterministic-first config, a request that names a
file or an action returns ``primary`` with zero calls to the fast-profile
classifier. Ambiguous requests still reach the classifier.

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_issue58_router.py -q
"""
from __future__ import annotations

import asyncio

import claudio_backend.runs.manager as manager_mod
from claudio_core.settings import AppConfig
from claudio_backend.runs.manager import RunManager


def _mgr(monkeypatch, calls):
    async def fake_completion(**kwargs):
        calls.append(kwargs)
        return "FAST"

    monkeypatch.setattr(manager_mod, "openai_chat_completion", fake_completion)
    config = AppConfig.model_validate(
        {
            "llm": {"model": "primary-m", "base_url": "http://p/v1"},
            "model_profiles": {
                "fastp": {"model": "fast-m", "base_url": "http://f/v1"}
            },
            "roles": {"fast": "fastp"},
            "agent": {
                "auto_routing_enabled": True,
                "router_deterministic_first": True,
            },
        }
    )
    mgr = RunManager.__new__(RunManager)
    mgr.config = config
    mgr.profile_health = None
    from claudio_core.accounting import CallAccountant

    mgr.accountant = CallAccountant()
    return mgr


def test_tool_request_skips_router_llm(tmp_path, monkeypatch):
    calls = []
    mgr = _mgr(monkeypatch, calls)
    role = asyncio.run(mgr._auto_route_role("fix the bug in src/app.py"))
    assert role == "primary"
    assert calls == []  # zero LLM calls -- the deterministic path decided


def test_ambiguous_request_reaches_classifier(tmp_path, monkeypatch):
    calls = []
    mgr = _mgr(monkeypatch, calls)
    role = asyncio.run(mgr._auto_route_role("hello there"))
    # "hello there" is ambiguous -> the LLM classifier is consulted.
    assert len(calls) == 1
    assert role in ("fast", "primary")


def test_deterministic_first_disabled_uses_llm(tmp_path, monkeypatch):
    calls = []
    mgr = _mgr(monkeypatch, calls)
    mgr.config = AppConfig.model_validate(
        {
            "llm": {"model": "primary-m", "base_url": "http://p/v1"},
            "model_profiles": {
                "fastp": {"model": "fast-m", "base_url": "http://f/v1"}
            },
            "roles": {"fast": "fastp"},
            "agent": {
                "auto_routing_enabled": True,
                "router_deterministic_first": False,
            },
        }
    )
    asyncio.run(mgr._auto_route_role("fix the bug in src/app.py"))
    assert len(calls) == 1  # deterministic pre-check off -> LLM runs
