"""Tests for the web_search tool: relevance gate, bot-wall detection, and
keyed-provider selection. These cover the failure mode where a keyless
provider returns HTTP 200 with cached, unrelated junk and the old tool
accepted it as success.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from pydantic import ValidationError

from claudio_core.context import ToolContext
from claudio_core.settings import AppConfig, SearchConfig
from claudio_core.tools import web as W


def _ctx(cfg: AppConfig) -> ToolContext:
    return ToolContext(
        config=cfg,
        session_id="test",
        workspace=Path("."),
        approval_mode="off",
        db=None,
    )


# ---- relevance -------------------------------------------------------


def test_relevance_zero_for_unrelated_results():
    junk = [
        {"title": "Olive Oil Shop Sofia", "url": "http://x", "snippet": "best olive oil in Bulgaria"},
    ]
    assert W._relevance("quantum computing breakthrough", junk) == 0.0


def test_relevance_high_for_matching_results():
    good = [
        {"title": "Eiffel Tower - Wikipedia", "url": "http://w", "snippet": "The Eiffel Tower is 330 m tall"},
    ]
    assert W._relevance("Eiffel Tower height", good) >= 0.5


def test_relevance_ignores_stopwords():
    # A query of only stopwords has nothing to check; do not penalise.
    assert W._relevance("the of and a", [{"title": "anything", "url": "", "snippet": ""}]) == 1.0


def test_query_tokens_drops_stopwords_and_short():
    toks = W._query_tokens("what is the capital of France")
    assert toks == ["capital", "france"]


# ---- bot-wall detection ----------------------------------------------


def test_looks_blocked_catches_captcha():
    assert W._looks_blocked("Unfortunately, bots use DuckDuckGo too. Select all squares containing a duck.")
    assert W._looks_blocked("g-recaptcha ... are you a robot?")


def test_looks_blocked_passes_normal_page():
    assert not W._looks_blocked("Here are the search results for your query about Paris.")
    assert not W._looks_blocked("")


# ---- provider selection ----------------------------------------------


def test_select_providers_auto_without_keys_is_scraping_only():
    cfg = SearchConfig(provider="auto")
    names = [f.__name__ for f in W._select_providers(cfg)]
    assert names == ["_search_bing", "_search_duckduckgo"]


def test_select_providers_auto_with_key_prefers_keyed(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "k")
    cfg = SearchConfig(provider="auto")
    names = [f.__name__ for f in W._select_providers(cfg)]
    assert names[0] == "_search_brave"
    assert "_search_bing" in names  # scraping still present as fallback


def test_select_providers_explicit_uses_only_that_one():
    cfg = SearchConfig(provider="tavily", tavily_api_key="k")
    names = [f.__name__ for f in W._select_providers(cfg)]
    assert names == ["_search_tavily"]


def test_search_config_rejects_unknown_provider():
    with pytest.raises(ValidationError):
        SearchConfig(provider="google")


def test_search_config_rejects_out_of_range_relevance():
    with pytest.raises(ValidationError):
        SearchConfig(min_relevance=1.5)


# ---- web_search gating -----------------------------------------------


def test_web_search_accepts_relevant_provider():
    async def relevant(query, max_results, cfg):
        return [{"title": "Paris France capital city", "url": "http://p", "snippet": "Paris is the capital of France"}]

    orig = W._select_providers
    W._select_providers = lambda cfg: [relevant]
    try:
        r = asyncio.run(W.web_search(_ctx(AppConfig()), "capital of France", max_results=3))
    finally:
        W._select_providers = orig
    assert r["ok"] is True
    assert r["provider"] == "relevant"
    assert r["relevance"] > 0


def test_web_search_rejects_junk_and_reports_degraded():
    async def junk(query, max_results, cfg):
        return [{"title": "Radio Capital Italia", "url": "http://r", "snippet": "notizie italiane"}]

    orig = W._select_providers
    W._select_providers = lambda cfg: [junk]
    try:
        r = asyncio.run(W.web_search(_ctx(AppConfig(search=SearchConfig(min_relevance=0.2))), "quantum tunneling", max_results=3))
    finally:
        W._select_providers = orig
    assert r["ok"] is False
    assert r["degraded"] is True
    assert "relevance gate" in r["error"]


def test_web_search_falls_through_blocked_to_good():
    async def blocked(query, max_results, cfg):
        raise W.BotWallError("captcha wall")

    async def good(query, max_results, cfg):
        return [{"title": "Paris travel guide", "url": "http://g", "snippet": "visiting Paris France"}]

    orig = W._select_providers
    W._select_providers = lambda cfg: [blocked, good]
    try:
        r = asyncio.run(W.web_search(_ctx(AppConfig()), "capital of France", max_results=3))
    finally:
        W._select_providers = orig
    assert r["ok"] is True
    assert r["provider"] == "good"


def test_web_search_all_blocked_is_degraded():
    async def blocked(query, max_results, cfg):
        raise W.BotWallError("blocked")

    orig = W._select_providers
    W._select_providers = lambda cfg: [blocked]
    try:
        r = asyncio.run(W.web_search(_ctx(AppConfig()), "anything", max_results=3))
    finally:
        W._select_providers = orig
    assert r["ok"] is False
    assert r["degraded"] is True
    assert "blocked" in r["error"]


def test_web_search_gate_disabled_when_zero():
    # min_relevance 0 disables the gate: junk is accepted (matches "no gate" intent).
    async def junk(query, max_results, cfg):
        return [{"title": "totally unrelated", "url": "http://x", "snippet": "nothing matching"}]

    orig = W._select_providers
    W._select_providers = lambda cfg: [junk]
    try:
        r = asyncio.run(W.web_search(_ctx(AppConfig(search=SearchConfig(min_relevance=0.0))), "quantum", max_results=3))
    finally:
        W._select_providers = orig
    assert r["ok"] is True
