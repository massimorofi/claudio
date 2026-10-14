"""Web tools: fetch a page and search the web (best-effort, no API key)."""
from __future__ import annotations

import re
from html import unescape

import httpx
from agents import RunContextWrapper

from ..context import ToolContext

_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Claudio/1.0"


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
    ctx: RunContextWrapper[ToolContext],
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
    limit = max_chars or ctx.context.max_output_chars
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


async def web_search(
    ctx: RunContextWrapper[ToolContext],
    query: str,
    max_results: int = 5,
) -> dict:
    """Search the web and return result titles and links (DuckDuckGo, best-effort).

    Args:
        query: The search query.
        max_results: Maximum number of results to return (default 5).
    """
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20.0),
            headers={"User-Agent": _UA},
        ) as client:
            resp = await client.post(
                "https://html.duckduckgo.com/html/",
                data={"q": query},
            )
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"Search request failed: {e}"}
    if resp.status_code != 200:
        return {"ok": False, "error": f"Search returned HTTP {resp.status_code}"}

    results: list[dict] = []
    # Parse result blocks: links with class result__a and snippets result__snippet.
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
    for i, (href, title) in enumerate(links):
        if len(results) >= max_results:
            break
        clean_title = unescape(re.sub(r"<[^>]+>", "", title)).strip()
        snippet = ""
        if i < len(snippets):
            snippet = unescape(re.sub(r"<[^>]+>", "", snippets[i])).strip()
        # DuckDuckGo wraps outbound URLs; try to extract the real target.
        real_url = href
        m = re.search(r"uddg=([^&]+)", href)
        if m:
            from urllib.parse import unquote

            real_url = unquote(m.group(1))
        results.append({"title": clean_title, "url": real_url, "snippet": snippet})
    if not results:
        return {"ok": False, "error": "No results found"}
    return {"ok": True, "query": query, "results": results}
