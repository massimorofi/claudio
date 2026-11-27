"""Read an MCP server's own index/workspace status report.

Why this module exists
----------------------
The prompt has to say something true about whether the connected code-graph
server actually covers the current workspace. ``claudio_core.codegraph`` already
knows how to turn a status *dict* into that verdict, and it is honest about a
missing report (it renders ``unknown``). What was missing is the report itself:
nobody ever asked the server. This module supplies the two framework-free pieces
needed to ask:

``pick_status_tool``      find the advertised tool that answers "what do you
                          have indexed?", using only generic vocabulary.
``parse_status_payload``  pull the status object out of whatever text that tool
                          answered with, tolerating fences and log noise.

Selection is by *what the tool says it does*, never by a vendor name: the
lexicon is five ordinary words (index, coverage, workspace, status,
capabilities) plus an equally ordinary set of reporting verbs used only to
break ties. A deployment whose server calls the tool
``get_workspace_capabilities``, ``index_status``, ``coverage_report`` or
``status`` gets the same behaviour; a server that advertises nothing
resembling a status report gets ``None``, which renders as ``unknown``.

Contract
--------
Standard library only (``json`` / ``re`` / ``typing``). No pydantic, no agent
framework, no network, no vendor strings. Nothing here raises: an unusable
answer is a ``None``, and ``None`` is what the readiness block already knows how
to render.

The status dict shape agreed with ``codegraph.readiness_from_status``::

    {"tracked_repos": [...], "failed_repos": [...], "generated_at": str}
"""
from __future__ import annotations

import json
import re
from typing import Any, Iterable, Mapping

__all__ = [
    "STATUS_LEXICON",
    "REPORTING_HINTS",
    "STATUS_KEYS",
    "pick_status_tool",
    "parse_status_payload",
    "to_text",
]

#: The only vocabulary used to decide whether a tool reports index/workspace
#: status. Generic words, deliberately: they describe the *question answered*,
#: not the product answering it.
STATUS_LEXICON: tuple[str, ...] = (
    "index",
    "coverage",
    "workspace",
    "status",
    "capabilities",
)

#: Words that mark a tool as *reporting* rather than mutating. Used only as a
#: tie-break (and never on its own), so ``get_workspace_status`` outranks a
#: hypothetical ``reindex_workspace`` while neither is named in code.
REPORTING_HINTS: tuple[str, ...] = (
    "status",
    "capabilities",
    "coverage",
    "health",
    "summary",
    "report",
    "info",
    "describe",
    "show",
    "list",
    "get",
    "current",
    "state",
)

#: The keys that identify a real status object. A JSON blob carrying none of
#: these is some other tool's answer, not a status report.
STATUS_KEYS: tuple[str, ...] = ("tracked_repos", "failed_repos", "generated_at")

# Word-boundary match so "status" does not fire inside "statutory".
_WORD_RES = {term: re.compile(r"\b" + re.escape(term) + r"\w*\b", re.IGNORECASE)
             for term in STATUS_LEXICON}

# Split a (possibly prefixed) tool name into comparable words:
# "server_get_index_status" / "serverGetIndexStatus" -> get, index, status.
_SPLIT_RE = re.compile(r"[^0-9a-zA-Z]+")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _tokens(name: str) -> list[str]:
    """Words in a tool name, camelCase- and separator-aware."""
    if not name:
        return []
    parts: list[str] = []
    for chunk in _SPLIT_RE.split(str(name)):
        if not chunk:
            continue
        parts.extend(_CAMEL_RE.split(chunk))
    return [p.lower() for p in parts if p]


def _as_str_map(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        return {}
    return {str(k): ("" if v is None else str(v)) for k, v in value.items()}


def _as_names(tools: Any) -> list[str]:
    """Normalize the tool list; accept names or objects carrying ``name``."""
    if not tools:
        return []
    if isinstance(tools, Mapping):
        return [str(k) for k in tools.keys()]
    if isinstance(tools, str):
        return [tools]
    if isinstance(tools, Iterable):
        names: list[str] = []
        for item in tools:
            if isinstance(item, str):
                names.append(item)
            else:
                name = getattr(item, "name", None)
                if name:
                    names.append(str(name))
        return names
    return []


def _name_score(name: str) -> int:
    """Primary-lexicon hits in a tool name (2 points each, distinct terms)."""
    tokens = set(_tokens(name))
    score = 0
    for term, pattern in _WORD_RES.items():
        if term in tokens or pattern.search(str(name)):
            score += 2
    return score


def _desc_score(text: str) -> int:
    """Primary-lexicon hits in a description (1 point per distinct term)."""
    if not text:
        return 0
    return sum(1 for pattern in _WORD_RES.values() if pattern.search(text))


def pick_status_tool(tools: Any, tool_descriptions: Any = None) -> str | None:
    """Return the advertised tool that reports index/workspace status.

    Args:
        tools: Tool names (as the model sees them, prefix included) or objects
            with a ``name`` attribute.
        tool_descriptions: ``name -> advertised description`` map, when known.

    Returns:
        The best-matching tool name, or ``None`` when nothing advertises a
        status/capability report. Selection uses only :data:`STATUS_LEXICON`
        words from the name and description, with :data:`REPORTING_HINTS` as a
        tie-break; no vendor or product name is ever consulted.
    """
    descriptions = _as_str_map(tool_descriptions)
    names = _as_names(tools)

    best_name: str | None = None
    best_score = 0
    for name in names:
        score = _name_score(name) + _desc_score(descriptions.get(name, ""))
        if score <= 0:
            continue
        # Tie-break only: a name that reads like a report beats one that does
        # not ("get_workspace_status" over "reindex_workspace").
        if any(hint in _tokens(name) for hint in REPORTING_HINTS):
            score += 1
        if score > best_score:
            best_name, best_score = name, score
    return best_name


def to_text(value: Any) -> str:
    """Best-effort flattening of an MCP tool result into text.

    Handles the shapes a real ``tools/call`` answer arrives in: a plain string,
    bytes, a list of content blocks (``{"type": "text", "text": ...}`` or
    objects with a ``text`` attribute), a dict carrying ``content`` /
    ``structuredContent``, or an object with those attributes. Anything else
    becomes its ``repr`` so the parser still gets a chance to find JSON in it.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8", "replace")
        except Exception:  # pragma: no cover - decode with replace cannot raise
            return ""
    if isinstance(value, Mapping):
        for key in ("structuredContent", "structured_content", "content", "text"):
            if key in value:
                text = to_text(value[key])
                if text:
                    return text
        return json.dumps(value, default=str)
    if isinstance(value, Iterable):
        parts = [to_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    for attr in ("structuredContent", "structured_content", "content", "text"):
        inner = getattr(value, attr, None)
        if inner is not None:
            text = to_text(inner)
            if text:
                return text
    return str(value)


def _is_status_dict(obj: Any) -> bool:
    return isinstance(obj, Mapping) and any(key in obj for key in STATUS_KEYS)


def _find_status_object(obj: Any, _depth: int = 0) -> Mapping[str, Any] | None:
    """Locate the status object, looking through the usual wrappers.

    A server may answer bare, or wrapped in ``{"result": {...}}`` /
    ``{"data": {"status": {...}}}``. The search is shallow and read-only: it
    walks mappings and lists up to a small depth and never mutates anything.
    """
    if _depth > 4:
        return None
    if _is_status_dict(obj):
        return obj
    if isinstance(obj, Mapping):
        for value in obj.values():
            found = _find_status_object(value, _depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_status_object(item, _depth + 1)
            if found is not None:
                return found
    return None


def _str_list(value: Any) -> list[str]:
    """Coerce a reported repository list; a non-list passes through untouched.

    Leaving a wrong-typed value in place is deliberate: the consumer renders
    ``unknown`` for it rather than silently reading it as "nothing indexed".
    """
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value if isinstance(item, (str, int, float))]
    return value


def _coerce(obj: Mapping[str, Any]) -> dict[str, Any]:
    status: dict[str, Any] = {}
    for key in STATUS_KEYS:
        if key in obj:
            status[key] = _str_list(obj[key]) if key != "generated_at" else (
                "" if obj[key] is None else str(obj[key])
            )
    return status


def _candidates(text: str) -> list[str]:
    """Substrings that could each be the JSON object, most likely first."""
    out: list[str] = []
    # 1. Whatever is inside a ``` fence (with or without a language tag).
    for match in re.finditer(r"```[ \t]*[a-zA-Z0-9_-]*[ \t]*\n?(.*?)```", text, re.DOTALL):
        body = match.group(1).strip()
        if body:
            out.append(body)
    # 2. The whole text (covers a bare JSON answer).
    out.append(text.strip())
    # 3. Every brace position (covers JSON preceded by log lines).
    for match in re.finditer(r"\{", text):
        out.append(text[match.start():])
    return out


def parse_status_payload(raw: Any) -> dict[str, Any] | None:
    """Extract the index/workspace status object from an MCP text payload.

    Accepts a bare JSON object, JSON inside a code fence, or JSON preceded by
    log/prose lines, and any of those wrapped in a ``content`` block list. The
    object must carry at least one of :data:`STATUS_KEYS` to count as a status
    report.

    Returns:
        A dict with the recognized keys (``tracked_repos``, ``failed_repos``,
        ``generated_at`` -- only those the server actually reported), or ``None``
        when nothing parseable was found. ``None`` is the "status unknown"
        signal; this function never raises.
    """
    if isinstance(raw, Mapping):
        found = _find_status_object(raw)
        return _coerce(found) if found is not None else None

    text = to_text(raw)
    if not text or not text.strip():
        return None

    decoder = json.JSONDecoder()
    for candidate in _candidates(text):
        try:
            obj, _end = decoder.raw_decode(candidate)
        except Exception:
            continue
        found = _find_status_object(obj)
        if found is not None:
            status = _coerce(found)
            if status:
                return status
    return None
