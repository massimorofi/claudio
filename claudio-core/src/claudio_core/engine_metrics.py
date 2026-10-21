"""Read the inference engine's own metrics instead of estimating them.

The OpenAI-compatible chat-completions response carries a non-standard
``timings`` object (per-request prefill/decode throughput, cache hits,
speculation stats). The OpenAI Agents SDK parses the response into its own
``ModelResponse`` and drops every field outside the OpenAI schema, so that
``timings`` never reaches us — and on the streamed path it cannot be recovered
from the SDK's objects at all.

The engine's Prometheus ``/metrics`` endpoint is the authoritative source for
the same numbers, computed by the engine itself rather than guessed from wall
clock or a chars/token heuristic. We read it directly and surface the real
throughput and cache behaviour in monitoring.

The endpoint lives at the server root (``http://host:port/metrics``), not under
the ``/v1`` API prefix, so the URL is derived by dropping the path from the
configured ``base_url``.
"""
from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

logger = logging.getLogger(__name__)

# Prometheus metric name -> the key we expose. Only the names we actually use are
# mapped; unknown metrics are ignored so an engine upgrade cannot break parsing.
_FIELDS: dict[str, str] = {
    "llamacpp:prompt_tokens_seconds": "prefill_tok_s",
    "llamacpp:predicted_tokens_seconds": "decode_tok_s",
    "llamacpp:prompt_tokens_total": "prompt_tokens_processed",
    "llamacpp:prompt_seconds_total": "prompt_seconds_total",
    "llamacpp:tokens_predicted_total": "tokens_predicted",
    "llamacpp:tokens_predicted_seconds_total": "predicted_seconds_total",
    "llamacpp:kv_cache_usage_ratio": "kv_cache_usage_ratio",
    "halogen:requests_total": "engine_requests_total",
    "halogen:prompt_tokens_cached_total": "prompt_tokens_cached",
    "halogen:draft_tokens_total": "draft_tokens_total",
    "halogen:draft_tokens_accepted_total": "draft_tokens_accepted",
}


def metrics_url(base_url: str) -> str:
    """Derive the engine's ``/metrics`` URL from a ``base_url``.

    ``http://host:8731/v1`` -> ``http://host:8731/metrics``. Only the scheme
    and host:port are kept; the ``/v1`` (or any other) path is replaced.
    """
    parts = urlsplit(base_url)
    return urlunsplit((parts.scheme, parts.netloc, "/metrics", "", ""))


def parse_prometheus(text: str) -> dict[str, float]:
    """Parse the subset of Prometheus text format we need.

    Handles ``name value`` and ``name{labels} value`` lines; skips ``#`` comment
    / HELP / TYPE lines. Returns only the mapped metrics that were present.
    """
    out: dict[str, float] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.rsplit(" ", 1)
        if len(parts) != 2:
            continue
        name, raw = parts
        base_name = name.split("{", 1)[0]
        key = _FIELDS.get(base_name)
        if key is None:
            continue
        try:
            out[key] = float(raw)
        except ValueError:
            continue
    return out


def _derive(out: dict[str, float]) -> dict[str, Any]:
    """Add computed ratios the raw counters do not give directly."""
    processed = out.get("prompt_tokens_processed", 0.0)
    cached = out.get("prompt_tokens_cached", 0.0)
    total = processed + cached
    if total > 0:
        # Fraction of prompt tokens served from the prefix cache rather than
        # recomputed. High values are expected for an agent that resends a
        # growing conversation every turn.
        out["prompt_cache_hit_ratio"] = round(cached / total, 4)
    drafted = out.get("draft_tokens_total", 0.0)
    accepted = out.get("draft_tokens_accepted", 0.0)
    if drafted > 0:
        out["draft_accept_ratio"] = round(accepted / drafted, 4)
    # Lifetime-average throughput from the cumulative counters. The per-scrape
    # gauges (prefill_tok_s / decode_tok_s) read 0 whenever nothing ran between
    # scrapes; these lifetime averages stay stable and answer "what does this
    # engine actually do" regardless of the current idle state.
    prompt_secs = out.get("prompt_seconds_total", 0.0)
    if prompt_secs > 0:
        out["lifetime_prefill_tok_s"] = round(processed / prompt_secs, 1)
    pred_secs = out.get("predicted_seconds_total", 0.0)
    if pred_secs > 0:
        out["lifetime_decode_tok_s"] = round(out.get("tokens_predicted", 0.0) / pred_secs, 1)
    return out


async def fetch_engine_metrics(base_url: str, timeout: float = 3.0) -> dict[str, Any]:
    """Fetch and parse the engine's ``/metrics``.

    Returns a dict of real engine metrics, or ``{}`` if the endpoint is
    unreachable or unparseable — callers must treat an empty result as "engine
    metrics unavailable", never as zeros.
    """
    url = metrics_url(base_url)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            text = resp.text
    except Exception as exc:  # noqa: BLE001 - metrics must never break the caller
        logger.warning("engine metrics fetch failed (%s): %s", url, exc)
        return {}
    parsed = parse_prometheus(text)
    if not parsed:
        logger.warning("engine metrics at %s contained no known metrics", url)
        return {}
    return _derive(parsed)


class EngineMetrics:
    """Caches the last successful engine-metrics read.

    The engine may be briefly unreachable; holding the last good snapshot lets
    the monitoring view keep showing real (slightly stale) numbers instead of
    blanks, with ``available`` telling the UI whether the values are live.
    """

    def __init__(self, base_url: str, timeout: float = 3.0) -> None:
        self.base_url = base_url
        self.timeout = timeout
        self._last: dict[str, Any] = {}
        self._ok = False

    async def refresh(self) -> dict[str, Any]:
        data = await fetch_engine_metrics(self.base_url, self.timeout)
        if data:
            self._last = data
            self._ok = True
        else:
            self._ok = False
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        out = dict(self._last)
        out["engine_metrics_available"] = self._ok
        return out
