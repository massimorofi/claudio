"""Web tools: fetch a page and search the web.

Search prefers a keyed search API (Brave / Serper / Tavily) over keyless
HTML scraping: keyed APIs return stable, structured JSON and are not subject
to the CAPTCHA bot-walls that silently break scraping. Keyless scraping is
kept only as a fallback, and every provider's output is checked for a
bot-block page and gated on relevance to the query, so a blocked provider
cannot pass cached, geo-localized junk off as a successful result.
"""
from __future__ import annotations

import base64
import os
import re
from html import unescape

import httpx

from ..context import ToolContext
from ..settings import SearchConfig

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Claudio/1.0"
# A realistic browser UA: some search endpoints reject non-browser clients.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _strip_html(html: str) -> str:
    # Drop scripts/styles entirely.
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
    # Block-level tags become newlines.
    text = re.sub(r"<(br|/p|/div|/h[1-6]|/li|/tr)[^>]*>", "\n", text, flags=re.IGNORECASE)
    # Remove remaining tags.
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text)
    # Collapse whitespace.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def web_fetch(
    ctx: ToolContext,
    url: str,
    max_chars: int | None = None,
) -> dict:
    """Fetch a web page and return its readable text content.

    Args:
        url: The URL to fetch (http or https).
        max_chars: Maximum characters of text to return (default: tool output limit).
    """
    if not url.startswith(("http://", "https://")):
        return {"ok": False, "error": "URL must start with http:// or https://"}
    limit = max_chars or ctx.max_output_chars
    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": _UA},
        ) as client:
            resp = await client.get(url)
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"Fetch failed: {e}"}
    if resp.status_code >= 400:
        return {"ok": False, "error": f"HTTP {resp.status_code} for {url}"}
    content_type = resp.headers.get("content-type", "")
    text = resp.text
    if "html" in content_type:
        text = _strip_html(text)
    if len(text) > limit:
        text = text[:limit] + f"\n[clipped: {len(text) - limit} chars omitted]"
    return {"ok": True, "url": str(resp.url), "content": text}


def _clean(text: str) -> str:
    text = unescape(re.sub(r"<[^>]+>", " ", text))
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# Search: keyed APIs preferred, scraping as fallback, gated on relevance.
# --------------------------------------------------------------------------


class BotWallError(RuntimeError):
    """A provider served a CAPTCHA / bot-block page instead of results."""


# Substrings that mark a provider response as a bot-block rather than real
# results. Matched case-insensitively against the raw response body. These are
# the signatures of the failure mode where a keyless provider, once it has
# bot-detected the scraper, returns a wall (or cached junk) with HTTP 200.
# Kept captcha-specific on purpose: generic phrases like "access denied" can
# appear in legitimate result pages, and a real block already shows up as a
# non-200 status code, which the providers check separately.
_BOT_WALL_MARKERS = (
    "select all squares",
    "select the squares",
    "unfortunately, bots use",
    "bots use duckduckgo",
    "are you a robot",
    "are you a human",
    "verify you are human",
    "verify you're human",
    "confirm you are not a robot",
    "confirm you are not a bot",
    "g-recaptcha",
    "grecaptcha",
    "cf-challenge",
    "cf_chl_",
    "blocked your ip",
)


def _looks_blocked(text: str) -> bool:
    """True if the response body looks like a CAPTCHA / bot-wall page."""
    if not text:
        return False
    low = text.lower()
    return any(marker in low for marker in _BOT_WALL_MARKERS)


# Common English filler words, dropped before scoring so they neither inflate
# nor deflate the relevance signal.
_STOPWORDS = frozenset(
    """a an the and or but if then than so as of in on at to for from by with
    is are was were be been being do does did have has had not no nor
    this that these those there here they them their theirs we our ours you your
    yours he his him she her hers it its i me my mine
    what which who whom when where why how all any both each few more most
    other some such only own same too very can will just also into over after
    before about against between during up down out off again further once""".split()
)


def _query_tokens(query: str) -> list[str]:
    """Meaningful query terms: lowercased, stopwords and 1-char tokens removed."""
    words = re.findall(r"[a-z0-9']+", (query or "").lower())
    return [w for w in words if len(w) >= 2 and w not in _STOPWORDS]


def _relevance(query: str, results: list[dict]) -> float:
    """Best query-term coverage across results, in 0..1.

    For each result we measure the fraction of meaningful query terms that
    appear in its title + snippet, and return the maximum over all results.
    A score near 0 means nothing the provider returned shares any content
    with the query — the signature of a blocked provider returning cached
    junk. This is a cheap backstop, not a ranking model: it catches the
    "olive-oil shops returned for a France query" failure, not subtle
    relevance gradations.
    """
    tokens = _query_tokens(query)
    if not tokens:
        # Nothing meaningful to check against; do not penalise the provider.
        return 1.0
    best = 0.0
    for r in results:
        hay = f"{r.get('title', '')} {r.get('snippet', '')}".lower()
        if not hay.strip():
            continue
        matched = sum(1 for t in tokens if t in hay)
        score = matched / len(tokens)
        if score > best:
            best = score
    return best


def _resolve_key(configured: str, env_name: str) -> str:
    """A search API key: the configured value first, then a plain env var."""
    val = (configured or "").strip()
    if val:
        return val
    return (os.environ.get(env_name) or "").strip()


async def _search_brave(query: str, max_results: int, cfg: SearchConfig) -> list[dict]:
    """Brave Search API — structured JSON, no CAPTCHA wall."""
    key = _resolve_key(cfg.brave_api_key, "BRAVE_API_KEY")
    if not key:
        raise RuntimeError("no Brave API key configured")
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(cfg.timeout_seconds, connect=5.0),
        headers={
            "X-Subscription-Token": key,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        },
    ) as client:
        resp = await client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={
                "q": query,
                "count": max(1, min(max_results, 20)),
                "search_lang": cfg.language,
                "country": cfg.country,
                "safesearch": "moderate",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as e:
        raise RuntimeError(f"invalid JSON from Brave: {e}")
    results: list[dict] = []
    for item in (data.get("web") or {}).get("results", []) or []:
        results.append(
            {
                "title": _clean(item.get("title") or ""),
                "url": item.get("url") or "",
                "snippet": _clean(item.get("description") or ""),
            }
        )
    return results[:max_results]


async def _search_serper(query: str, max_results: int, cfg: SearchConfig) -> list[dict]:
    """Serper (Google SERP) API — structured JSON, no CAPTCHA wall."""
    key = _resolve_key(cfg.serper_api_key, "SERPER_API_KEY")
    if not key:
        raise RuntimeError("no Serper API key configured")
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(cfg.timeout_seconds, connect=5.0),
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
    ) as client:
        resp = await client.post(
            "https://google.serper.dev/search",
            json={
                "q": query,
                "num": max(1, min(max_results, 10)),
                "hl": cfg.language,
                "gl": cfg.country,
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as e:
        raise RuntimeError(f"invalid JSON from Serper: {e}")
    results: list[dict] = []
    for item in data.get("organic", []) or []:
        results.append(
            {
                "title": _clean(item.get("title") or ""),
                "url": item.get("link") or "",
                "snippet": _clean(item.get("snippet") or ""),
            }
        )
    return results[:max_results]


async def _search_tavily(query: str, max_results: int, cfg: SearchConfig) -> list[dict]:
    """Tavily search API — structured JSON, tuned for agents."""
    key = _resolve_key(cfg.tavily_api_key, "TAVILY_API_KEY")
    if not key:
        raise RuntimeError("no Tavily API key configured")
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(max(cfg.timeout_seconds, 20), connect=5.0),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
    ) as client:
        resp = await client.post(
            "https://api.tavily.com/search",
            json={
                "query": query,
                "max_results": max(1, min(max_results, 10)),
                "search_depth": "basic",
                "topic": "general",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    try:
        data = resp.json()
    except ValueError as e:
        raise RuntimeError(f"invalid JSON from Tavily: {e}")
    results: list[dict] = []
    for item in data.get("results", []) or []:
        results.append(
            {
                "title": _clean(item.get("title") or ""),
                "url": item.get("url") or "",
                "snippet": _clean(item.get("content") or ""),
            }
        )
    return results[:max_results]


async def _search_duckduckgo(query: str, max_results: int, cfg: SearchConfig) -> list[dict]:
    """DuckDuckGo HTML endpoint (keyless fallback)."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(cfg.timeout_seconds, connect=5.0),
        headers={
            "User-Agent": _UA,
            "Accept-Language": "en-US,en;q=0.9",
        },
    ) as client:
        resp = await client.post(
            "https://html.duckduckgo.com/html/",
            data={
                "q": query,
                "hl": cfg.language,
                "kl": f"{cfg.country.lower()}-{cfg.language}",
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    if _looks_blocked(resp.text):
        raise BotWallError("DuckDuckGo served a bot-block / CAPTCHA page")

    links = re.findall(
        r'<a[^>]+class="[^"]*result__a[^"]*"[^>]+href="([^"]+)"[^>]*>(.*?)</a>',
        resp.text,
        flags=re.DOTALL,
    )
    snippets = re.findall(
        r'<a[^>]+class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>',
        resp.text,
        flags=re.DOTALL,
    )
    results: list[dict] = []
    for i, (href, title) in enumerate(links):
        if len(results) >= max_results:
            break
        # DuckDuckGo wraps outbound URLs; extract the real target.
        real_url = href
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            from urllib.parse import unquote

            real_url = unquote(m.group(1))
        snippet = _clean(snippets[i]) if i < len(snippets) else ""
        results.append({"title": _clean(title), "url": real_url, "snippet": snippet})
    return results


def _decode_bing_url(href: str) -> str:
    """Bing wraps results as .../ck/a?...&u=a1<base64>; decode to the real URL."""
    m = re.search(r"[?&]u=([^&]+)", href)
    if not m:
        return href
    val = m.group(1)
    if val.startswith("a1"):
        val = val[2:]
    val = val.replace("-", "+").replace("_", "/")
    val += "=" * (-len(val) % 4)
    try:
        return base64.b64decode(val).decode("utf-8", "replace")
    except Exception:
        return href


async def _search_bing(query: str, max_results: int, cfg: SearchConfig) -> list[dict]:
    """Bing search (keyless fallback)."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(cfg.timeout_seconds, connect=5.0),
        headers={
            "User-Agent": _BROWSER_UA,
            "Accept-Language": "en-US,en;q=0.9",
        },
    ) as client:
        resp = await client.get(
            "https://www.bing.com/search",
            params={
                "q": query,
                "count": max(1, min(max_results, 10)),
                "setlang": cfg.language,
                "cc": cfg.country.lower(),
            },
        )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")
    if _looks_blocked(resp.text):
        raise BotWallError("Bing served a bot-block / CAPTCHA page")

    # Organic results: <h2><a href="...">TITLE</a></h2> followed by
    # <div class="b_caption"><p ...>SNIPPET</p></div>.
    pattern = re.compile(
        r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>\s*</h2>'
        r'(?:\s*<div class="b_caption"[^>]*>\s*<p[^>]*>(.*?)</p>)?',
        flags=re.DOTALL,
    )
    results: list[dict] = []
    seen: set[str] = set()
    for href, title, snippet in pattern.findall(resp.text):
        title = _clean(title)
        if not title:
            continue
        url = _decode_bing_url(unescape(href))
        if not url or url in seen:
            continue
        seen.add(url)
        results.append(
            {
                "title": title,
                "url": url,
                "snippet": _clean(snippet) if snippet else "",
            }
        )
        if len(results) >= max_results:
            break
    return results

# name -> (callable, config attr, plain env var). Keyed providers first in the
# order they are preferred in auto mode.
_KEYED_PROVIDERS = {
    "brave": (_search_brave, "brave_api_key", "BRAVE_API_KEY"),
    "serper": (_search_serper, "serper_api_key", "SERPER_API_KEY"),
    "tavily": (_search_tavily, "tavily_api_key", "TAVILY_API_KEY"),
}


def _select_providers(cfg: SearchConfig) -> list:
    """Ordered list of provider callables to try for this config.

    ``auto`` tries every keyed provider that actually has a key, then the
    keyless scraping fallback. An explicit provider name uses only that one.
    """
    provider = (cfg.provider or "auto").strip().lower()
    if provider == "scraping":
        return [_search_bing, _search_duckduckgo]
    if provider != "auto":
        return [_KEYED_PROVIDERS[provider][0]]
    selected = []
    for name in ("brave", "serper", "tavily"):
        fn, cfg_attr, env_name = _KEYED_PROVIDERS[name]
        if _resolve_key(getattr(cfg, cfg_attr, ""), env_name):
            selected.append(fn)
    selected.extend([_search_bing, _search_duckduckgo])
    return selected


async def web_search(
    ctx: ToolContext,
    query: str,
    max_results: int = 5,
) -> dict:
    """Search the web and return result titles, links and snippets.

    Prefers a keyed search API (Brave / Serper / Tavily) when a key is
    configured; otherwise falls back to keyless scraping. Each provider's
    results are checked for a bot-block page and gated on relevance to the
    query, so a blocked provider that returns cached, unrelated content is
    treated as failed and the next provider is tried. If no provider yields
    relevant results the call returns ``ok=false`` with a ``degraded`` flag
    rather than passing junk through as success.

    Args:
        query: The search query.
        max_results: Maximum number of results to return (default 5).
    """
    cfg = ctx.config.search
    errors: list[str] = []
    for provider in _select_providers(cfg):
        name = provider.__name__.replace("_search_", "")
        try:
            results = await provider(query, max_results, cfg)
        except BotWallError as e:
            errors.append(f"{name}: blocked ({e})")
            continue
        except httpx.HTTPError as e:
            errors.append(f"{name}: {type(e).__name__}: {e or 'no detail'}")
            continue
        except Exception as e:  # noqa: BLE001 — provider failures are expected
            errors.append(f"{name}: {type(e).__name__}: {e or 'no detail'}")
            continue
        if not results:
            errors.append(f"{name}: no results")
            continue
        score = _relevance(query, results)
        if cfg.min_relevance > 0 and score < cfg.min_relevance:
            errors.append(
                f"{name}: results failed relevance gate "
                f"(score {score:.2f} < {cfg.min_relevance:.2f})"
            )
            continue
        return {
            "ok": True,
            "query": query,
            "provider": name,
            "relevance": round(score, 3),
            "results": results,
        }
    return {
        "ok": False,
        "degraded": True,
        "error": "Web search failed (all providers): " + "; ".join(errors),
    }
