"""A minimal OpenAI-compatible completion client.

Compaction, title generation and other one-shot jobs need a model call that is
*not* an agent run. Routing those through the agent engine would drag in tool
schemas, conversation state and pause semantics that have no meaning for a
single prompt. This module talks the OpenAI ``/chat/completions`` wire format
directly over httpx, which keeps it framework-free and therefore usable from
``claudio_core``.

The endpoint is deliberately explicit rather than discovered: Claudio always
points at a local server whose base URL the operator configured.
"""
from __future__ import annotations

import json
from typing import Any

import httpx


class LlmCallError(Exception):
    """The completion request failed at the transport or protocol level."""


async def openai_chat_completion(
    *,
    base_url: str | None,
    model: str,
    messages: list[dict[str, Any]],
    api_key: str = "local",
    temperature: float = 0.2,
    max_tokens: int = 2048,
    timeout: float = 300.0,
) -> str:
    """Return the assistant text from one chat-completion request.

    ``base_url`` may be given with or without the ``/v1`` suffix; both are
    accepted because local servers differ. Raises :class:`LlmCallError` on any
    failure -- callers that treat the result as optional should catch it.
    """
    if not base_url:
        raise LlmCallError("No base_url configured for the LLM endpoint")

    url = _join(base_url)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {api_key or 'local'}"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=payload, headers=headers)
    except httpx.HTTPError as exc:
        raise LlmCallError(f"LLM request failed: {exc}") from exc

    if resp.status_code >= 400:
        raise LlmCallError(
            f"LLM endpoint returned {resp.status_code}: {resp.text[:300]}"
        )

    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError) as exc:
        raise LlmCallError(f"LLM returned non-JSON response: {resp.text[:200]}") from exc

    return _extract_text(data)


def _join(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _extract_text(data: dict[str, Any]) -> str:
    choices = data.get("choices") or []
    if not choices:
        raise LlmCallError("LLM response contained no choices")
    message = choices[0].get("message") or {}
    text = message.get("content")
    if isinstance(text, list):
        # Some servers return content as a block list.
        parts = [
            b.get("text", "")
            for b in text
            if isinstance(b, dict) and b.get("type") in ("text", "output_text")
        ]
        text = "".join(parts)
    if not text:
        raise LlmCallError("LLM response contained no text")
    return str(text)


__all__ = ["openai_chat_completion", "LlmCallError"]
