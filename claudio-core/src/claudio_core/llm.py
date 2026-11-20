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
import time
from typing import Any, Callable

import httpx

from .accounting import get_default_recorder


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
    purpose: str = "other",
    usage_sink: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    """Return the assistant text from one chat-completion request.

    ``base_url`` may be given with or without the ``/v1`` suffix; both are
    accepted because local servers differ. Raises :class:`LlmCallError` on any
    failure -- callers that treat the result as optional should catch it.

    ``usage_sink`` (issue #58, Increment 0) is called exactly once per logical
    call -- on success *and* on failure -- with a plain dict::

        {"purpose", "model", "started_at", "finished_at", "duration_ms",
         "outcome": "ok"|"error", "error_code", "attempt",
         "usage": {"input_tokens", "output_tokens", "cached_input_tokens",
                   "reasoning_tokens"} | None,
         "measured": bool}

    so every auxiliary call (compaction, router, learning, fixer, ...) lands
    in the unified ledger without changing this function's return type. The
    ``max_tokens`` rename retry is one logical call with ``attempt=2``, not two
    records. A sink that raises is swallowed: telemetry must never break a
    completion.
    """
    if not base_url:
        _emit(usage_sink, purpose, model, time.time(), time.time(), 0.0,
              "error", "no_base_url", 1, None)
        raise LlmCallError("No base_url configured for the LLM endpoint")

    url = _join(base_url)
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
    }
    # ``api-key`` is sent alongside the standard bearer header because some
    # enterprise gateways (e.g. Merck's GPTeal) strip ``Authorization`` and
    # ``x-api-key`` entirely. OpenAI-compatible servers ignore the extra
    # header, so adding it is harmless for local backends.
    key = api_key or "local"
    headers = {"Authorization": f"Bearer {key}", "api-key": key}

    started = time.time()
    attempt = 1
    try:
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(url, json=payload, headers=headers)
                # gpt-5/6-class reasoning models reject ``max_tokens`` and require
                # ``max_completion_tokens``. When the endpoint says so explicitly,
                # rename the field and resend exactly once.
                if resp.status_code == 400 and _rejects_max_tokens(resp):
                    attempt = 2
                    payload["max_completion_tokens"] = payload.pop("max_tokens")
                    resp = await client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            _emit(usage_sink, purpose, model, started, time.time(),
                  (time.time() - started) * 1000.0, "error",
                  exc.__class__.__name__, attempt, None)
            raise LlmCallError(f"LLM request failed: {exc}") from exc

        if resp.status_code >= 400:
            _emit(usage_sink, purpose, model, started, time.time(),
                  (time.time() - started) * 1000.0, "error",
                  f"http_{resp.status_code}", attempt, None)
            raise LlmCallError(
                f"LLM endpoint returned {resp.status_code}: {resp.text[:300]}"
            )

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as exc:
            _emit(usage_sink, purpose, model, started, time.time(),
                  (time.time() - started) * 1000.0, "error",
                  "invalid_json", attempt, None)
            raise LlmCallError(
                f"LLM returned non-JSON response: {resp.text[:200]}"
            ) from exc

        usage = _extract_usage(data)
        try:
            text = _extract_text(data)
        except LlmCallError:
            _emit(usage_sink, purpose, model, started, time.time(),
                  (time.time() - started) * 1000.0, "error",
                  "no_text", attempt, usage)
            raise
        _emit(usage_sink, purpose, model, started, time.time(),
              (time.time() - started) * 1000.0, "ok", "", attempt, usage)
        return text
    except LlmCallError:
        raise
    except Exception as exc:  # noqa: BLE001 - unexpected shape: record, re-raise
        _emit(usage_sink, purpose, model, started, time.time(),
              (time.time() - started) * 1000.0, "error",
              exc.__class__.__name__, attempt, None)
        raise


def _emit(
    sink: Callable[[dict[str, Any]], None] | None,
    purpose: str,
    model: str,
    started_at: float,
    finished_at: float,
    duration_ms: float,
    outcome: str,
    error_code: str,
    attempt: int,
    usage: dict[str, int] | None,
) -> None:
    """Fire the accounting sink once, defensively: telemetry never breaks calls.

    With no explicit sink the process-wide default recorder (installed by the
    supervisor when accounting is wired) is used, so auxiliary calls made
    without a run context -- fixer, consolidation, the config probe -- are
    still accounted for.
    """
    fn = sink if sink is not None else get_default_recorder()
    if fn is None:
        return
    try:
        fn(
            {
                "purpose": purpose,
                "model": model,
                "started_at": started_at,
                "finished_at": finished_at,
                "duration_ms": duration_ms,
                "outcome": outcome,
                "error_code": error_code,
                "attempt": attempt,
                "usage": usage,
                "measured": usage is not None,
            }
        )
    except Exception:  # noqa: BLE001 - a broken sink must not fail the call
        pass


def _extract_usage(data: dict[str, Any]) -> dict[str, int] | None:
    """Pull provider usage out of a chat-completion body, if it reports any.

    Maps the OpenAI wire fields onto Claudio's accounting names:
    ``prompt_tokens`` -> input, ``completion_tokens`` -> output,
    ``prompt_tokens_details.cached_tokens`` -> cached input, and
    ``completion_tokens_details.reasoning_tokens`` -> reasoning (a *subset*
    of output tokens, kept separate so totals never double-count).
    """
    usage = data.get("usage") if isinstance(data, dict) else None
    if not isinstance(usage, dict):
        return None
    details = usage.get("prompt_tokens_details")
    details = details if isinstance(details, dict) else {}
    cdetails = usage.get("completion_tokens_details")
    cdetails = cdetails if isinstance(cdetails, dict) else {}
    try:
        return {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "cached_input_tokens": int(details.get("cached_tokens") or 0),
            "reasoning_tokens": int(cdetails.get("reasoning_tokens") or 0),
        }
    except (TypeError, ValueError):
        return None


def _join(base_url: str) -> str:
    base = base_url.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return f"{base}/chat/completions"
    return f"{base}/v1/chat/completions"


def _rejects_max_tokens(resp: httpx.Response) -> bool:
    """Return True only when the endpoint explicitly rejects ``max_tokens``.

    Two signals count as an explicit rejection:

    * a structured OpenAI-style error naming ``max_tokens`` as the rejected
      parameter -- ``{"error": {"param": "max_tokens", ...}}``; or
    * a clear message that names ``max_completion_tokens`` as the field the
      endpoint wants used instead.

    Everything else -- a generic 400, a complaint about some other
    parameter, an unparseable body -- returns ``False`` so the caller
    surfaces the original error instead of masking it with a retry. This
    keeps the retry narrow: it fires on the one incompatibility it fixes
    and never on an unrelated failure.
    """
    text = resp.text or ""
    if "max_completion_tokens" in text:
        return True
    try:
        data = resp.json()
    except (json.JSONDecodeError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    error = data.get("error")
    if isinstance(error, dict) and error.get("param") == "max_tokens":
        return True
    return False


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
