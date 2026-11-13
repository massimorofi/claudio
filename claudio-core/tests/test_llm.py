"""Tests for the OpenAI-compatible completion client, focused on the
``max_tokens`` -> ``max_completion_tokens`` retry added for issue #33.

The contract under test: a 400 that explicitly rejects ``max_tokens``
(a structured ``error.param`` or a message naming ``max_completion_tokens``)
triggers exactly one retry with the renamed field and returns the
successful response; any other 400 is surfaced without a retry, and the
original request's other parameters are preserved across the retry.
"""
from __future__ import annotations

import json

import httpx
import pytest

from claudio_core import llm
from claudio_core.llm import LlmCallError, openai_chat_completion


# ---- recognition helper --------------------------------------------------

def _resp(status: int, body: str) -> httpx.Response:
    return httpx.Response(
        status,
        text=body,
        request=httpx.Request("POST", "http://x/v1/chat/completions"),
    )


def test_rejects_max_tokens_structured_param():
    body = json.dumps(
        {
            "error": {
                "message": "Unsupported parameter: 'max_tokens' for this model",
                "param": "max_tokens",
                "type": "invalid_request_error",
            }
        }
    )
    assert llm._rejects_max_tokens(_resp(400, body)) is True


def test_rejects_max_tokens_message_names_replacement_field():
    body = "Error: this model requires max_completion_tokens, not max_tokens"
    assert llm._rejects_max_tokens(_resp(400, body)) is True


def test_rejects_max_tokens_unrelated_param_is_false():
    body = json.dumps(
        {"error": {"message": "temperature must be between 0 and 2", "param": "temperature"}}
    )
    assert llm._rejects_max_tokens(_resp(400, body)) is False


def test_rejects_max_tokens_generic_error_is_false():
    body = json.dumps({"error": {"message": "invalid api key", "param": None}})
    assert llm._rejects_max_tokens(_resp(400, body)) is False


def test_rejects_max_tokens_non_json_body_is_false():
    assert llm._rejects_max_tokens(_resp(400, "<html>400 Bad Request</html>")) is False


def test_rejects_max_tokens_empty_body_is_false():
    assert llm._rejects_max_tokens(_resp(400, "")) is False


# ---- retry flow --------------------------------------------------------

def _success_body() -> str:
    return json.dumps({"choices": [{"message": {"content": "hello"}}]})


class _FakeClient:
    """Stand-in for ``httpx.AsyncClient`` returning queued responses.

    The function under test opens the client once and may call ``post``
    twice (original + retry), so the queue is consumed across calls and
    every request body is recorded for assertions.
    """

    def __init__(self, responses):
        self._responses = list(responses)
        self.requests: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def post(self, url, json=None, headers=None):
        # Snapshot the payload: the function mutates the same dict between the
        # original request and the retry, so a stored reference would not
        # reflect what was actually sent on the first call.
        self.requests.append(dict(json) if json is not None else None)
        status, body = self._responses[len(self.requests) - 1]
        return httpx.Response(status, text=body, request=httpx.Request("POST", url))


def _install(monkeypatch, responses) -> _FakeClient:
    client = _FakeClient(responses)
    monkeypatch.setattr(llm.httpx, "AsyncClient", lambda *a, **k: client)
    return client


async def test_retry_on_recognized_structured_rejection(monkeypatch):
    rejection = json.dumps(
        {"error": {"message": "Unsupported parameter: 'max_tokens'", "param": "max_tokens"}}
    )
    client = _install(monkeypatch, [(400, rejection), (200, _success_body())])
    text = await openai_chat_completion(
        base_url="http://x/v1",
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=99,
    )
    assert text == "hello"
    assert len(client.requests) == 2
    # First request used max_tokens; the retry renamed it.
    assert client.requests[0]["max_tokens"] == 99
    assert "max_tokens" not in client.requests[1]
    assert client.requests[1]["max_completion_tokens"] == 99


async def test_retry_on_recognized_message_rejection(monkeypatch):
    rejection = "Unsupported parameter: use max_completion_tokens instead."
    client = _install(monkeypatch, [(400, rejection), (200, _success_body())])
    text = await openai_chat_completion(
        base_url="http://x/v1",
        model="m",
        messages=[{"role": "user", "content": "hi"}],
        max_tokens=42,
    )
    assert text == "hello"
    assert len(client.requests) == 2
    assert client.requests[1]["max_completion_tokens"] == 42


async def test_no_retry_on_unrelated_400(monkeypatch):
    unrelated = json.dumps({"error": {"message": "invalid api key", "param": None}})
    client = _install(monkeypatch, [(400, unrelated)])
    with pytest.raises(LlmCallError):
        await openai_chat_completion(
            base_url="http://x/v1",
            model="m",
            messages=[{"role": "user", "content": "hi"}],
        )
    assert len(client.requests) == 1


async def test_parameters_preserved_on_retry(monkeypatch):
    rejection = json.dumps(
        {"error": {"message": "use max_completion_tokens", "param": "max_tokens"}}
    )
    client = _install(monkeypatch, [(400, rejection), (200, _success_body())])
    await openai_chat_completion(
        base_url="http://x/v1",
        model="my-model",
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.7,
        max_tokens=50,
    )
    second = client.requests[1]
    assert second["model"] == "my-model"
    assert second["temperature"] == 0.7
    assert second["messages"] == [{"role": "user", "content": "hi"}]
    assert second["stream"] is False
    assert second["max_completion_tokens"] == 50


async def test_retry_happens_exactly_once(monkeypatch):
    # Even if the retry is also rejected, we must not loop: exactly two posts.
    rejection = json.dumps({"error": {"message": "still bad", "param": "max_tokens"}})
    client = _install(monkeypatch, [(400, rejection), (400, rejection)])
    with pytest.raises(LlmCallError):
        await openai_chat_completion(
            base_url="http://x/v1",
            model="m",
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=10,
        )
    assert len(client.requests) == 2
