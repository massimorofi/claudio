"""Web tools: fetch a page and search the web (best-effort, no API key)."""
from __future__ import annotations

import base64
import re
from html import unescape

import httpx

from ..context import ToolContext

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


async def _search_duckduckgo(query: str, max_results: int) -> list[dict]:
    """DuckDuckGo HTML endpoint. Returns a list of result dicts (may be empty)."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(12.0, connect=5.0),
        headers={"User-Agent": _UA},
    ) as client:
        resp = await client.post("https://html.duckduckgo.com/html/", data={"q": query})
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")

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


async def _search_bing(query: str, max_results: int) -> list[dict]:
    """Bing search. Returns a list of result dicts (may be empty)."""
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(12.0, connect=5.0),
        headers={
            "User-Agent": _BROWSER_UA,
            "Accept-Language": "en-US,en;q=0.9",
        },
    ) as client:
        resp = await client.get(
            "https://www.bing.com/search",
            params={"q": query, "count": max(1, min(max_results, 10)), "setlang": "en"},
        )
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code}")

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

# Tried in order; the first provider that returns results wins. Bing first because
# DuckDuckGo's HTML endpoint is frequently blocked/unreachable on many networks.
_SEARCH_PROVIDERS = [_search_bing, _search_duckduckgo]


async def web_search(
    ctx: ToolContext,
    query: str,
    max_results: int = 5,
) -> dict:
    """Search the web and return result titles and links (best-effort, no API key).

    Tries several providers in turn and returns results from the first that
    succeeds, so a single blocked or failing provider does not break searching.

    Args:
        query: The search query.
        max_results: Maximum number of results to return (default 5).
    """
    errors: list[str] = []
    for provider in _SEARCH_PROVIDERS:
        name = provider.__name__.replace("_search_", "")
        try:
            results = await provider(query, max_results)
        except httpx.HTTPError as e:
            errors.append(f"{name}: {type(e).__name__}: {e or 'no detail'}")
            continue
        except Exception as e:  # noqa: BLE001 — provider failures are expected
            errors.append(f"{name}: {type(e).__name__}: {e or 'no detail'}")
            continue
        if results:
            return {"ok": True, "query": query, "results": results}
        errors.append(f"{name}: no results")
    return {
        "ok": False,
        "error": "Web search failed (all providers): " + "; ".join(errors),
    }
