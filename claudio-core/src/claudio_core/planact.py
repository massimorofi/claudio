"""Plan/act auditing: did the agent actually call the tools it said it would?

Why this exists
---------------
A stated intention to use a tool and the tool call itself are unrelated events
unless something connects them. Nothing did. An agent could write "let me check
the code graph" in its narration, never issue a single graph call, mark the plan
item done, and report a result -- and the transcript looked fine. That is exactly
what happened on a documentation task where the plan said "via the code graph +
source reads" and only file tools were ever called.

This module makes the gap *computable*. Given the assistant's own text and the
set of tools actually invoked, it reports families of tools that were named with
intent but never called. The engine turns that into a nudge, so the model sees
its own omission while the run is still going, and the run manager can log it
afterwards.

Design notes
------------
- **Families, not tool names.** Tool names are unstable across MCP servers and
  versions, so a family is a set of generic prose triggers ("code graph",
  "callers") plus patterns that match the real tool names.
- **No vendor names, anywhere.** The family is described by capability
  vocabulary, never by a product name. When a server's tool names do not use
  that vocabulary, the extra patterns come *in band* from the catalog the server
  itself advertised -- see :func:`code_graph_family` and :func:`derive_families`,
  which builds one family per connected server from its configured id and real
  tool names. Nothing in this module has to be edited when the product changes.
- **Intent required.** Mentioning a tool in a sentence is not a plan. Negations
  and disclosures ("the graph was unavailable", "I did not query the graph") must
  not trigger a nudge, or the audit becomes noise the model learns to ignore.
- **Satisfied means called.** Once any tool in the family has run, the family is
  no longer outstanding, regardless of what was said earlier.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from .mcp_capability import CODE_GRAPH_LEXICON, detect

# Words that, next to a tool-family trigger, indicate the agent intends to act.
INTENT_WORDS = (
    "check", "query", "use", "using", "used", "call", "calling", "consult",
    "look", "search", "ask", "run", "try", "first", "now", "let me", "i'll",
    "i will", "going to", "need to", "should", "start by",
)

# Words that make a mention a *report about the past or a disclosure* rather than
# a plan. Checked per sentence; their presence suppresses the mention.
NEGATIVE_WORDS = (
    "not", "never", "no ", "none", "cannot", "can't", "couldn't", "didn't",
    "doesn't", "wasn't", "weren't", "without", "unavailable", "failed",
    "couldn", "unable", "skipped", "instead", "fell back", "fallback",
    "does not", "did not", "is not", "are not",
)


@dataclass(frozen=True)
class ToolFamily:
    """A named group of tools, matched by prose trigger and by tool name."""

    name: str
    triggers: tuple[str, ...]
    tool_patterns: tuple[str, ...]
    remedy: str

    def matches_tool(self, tool_name: str) -> bool:
        lowered = (tool_name or "").lower()
        return any(re.search(p, lowered) for p in self.tool_patterns)


#: Generic vocabulary that only a code-navigation / code-intelligence server is
#: likely to advertise. Deliberately vendor-free: no product names, no company
#: names, just the structural concepts the tools answer questions about.
CODE_GRAPH_TRIGGERS: tuple[str, ...] = (
    "code graph", "graph tool", "graph query", "graph results", "call graph",
    "callers", "callees", "call hierarchy", "change impact", "impact analysis",
    "code intelligence", "code index", "index", "index coverage",
    "code navigation", "dependenc", "symbol",
)

#: Patterns matched against *tool names* (already lower-cased) to decide whether a
#: call satisfied the family. Generic structural vocabulary only; anything
#: vendor-specific is added in band by :func:`code_graph_family`.
CODE_GRAPH_TOOL_PATTERNS: tuple[str, ...] = tuple(
    r"\b" + re.escape(term) for term in sorted(CODE_GRAPH_LEXICON, key=len, reverse=True)
) + (r"graph", r"callers", r"callees", r"dependenc", r"symbol", r"impact")

# The family this exists for, plus an adjacent one that fails the same way.
CODE_GRAPH_FAMILY = ToolFamily(
    name="the code graph",
    triggers=CODE_GRAPH_TRIGGERS,
    tool_patterns=CODE_GRAPH_TOOL_PATTERNS,
    remedy=(
        "Issue an actual code-graph tool call now, or state plainly that you did "
        "not use the graph and that your findings come from the file tools "
        "instead. Do not describe the graph as a source you consulted."
    ),
)

WEB_SEARCH_FAMILY = ToolFamily(
    name="web search",
    triggers=("web search", "search the web", "search the internet", "google"),
    tool_patterns=(r"web_search", r"^search$"),
    remedy=(
        "Call the search tool, or say that you answered from existing knowledge "
        "and did not search."
    ),
)

BUILTIN_FAMILIES: tuple[ToolFamily, ...] = (CODE_GRAPH_FAMILY, WEB_SEARCH_FAMILY)


def code_graph_family(
    mcp_entries: "Iterable[Mapping[str, Any]] | None" = None,
    extra_patterns: "Iterable[str]" = (),
) -> ToolFamily:
    """The code-graph family, widened with what the connected servers advertise.

    The builtin patterns cover the vocabulary a code-navigation server normally
    uses. A server whose tool names use some other vocabulary is still matched,
    because the extra patterns come *in band* from its own catalog entry (server
    id and real tool names) via :func:`claudio_core.mcp_capability.detect` --
    never from a vendor name written into this file.

    Args:
        mcp_entries: The same plain-dict catalog entries used everywhere else
            (``{"id": ..., "tools": [...], ...}`). ``None`` or a malformed
            catalog simply yields the builtin family; nothing raises.
        extra_patterns: Operator-supplied regexes, appended last.
    """
    patterns = list(CODE_GRAPH_TOOL_PATTERNS)
    try:
        servers = detect(mcp_entries or ())
    except Exception:  # a broken catalog must never break a run
        servers = ()
    for server in servers:
        for name in (server.id, *server.tools):
            token = str(name).strip().lower()
            if token and token not in patterns:
                patterns.append(re.escape(token))
    for pattern in extra_patterns:
        text = str(pattern).strip()
        if text:
            patterns.append(text)
    return ToolFamily(
        name=CODE_GRAPH_FAMILY.name,
        triggers=CODE_GRAPH_FAMILY.triggers,
        tool_patterns=tuple(patterns),
        remedy=CODE_GRAPH_FAMILY.remedy,
    )


def derive_families(
    mcp_entries: "Iterable[Mapping[str, Any]] | None" = None,
) -> tuple[ToolFamily, ...]:
    """One family per *connected* code-graph server, built from its own catalog.

    This is what makes the audit vendor-agnostic. Nothing here knows who provides
    the graph: :func:`claudio_core.mcp_capability.detect` answers "does this
    server advertise code-graph capability?" and everything the family is made of
    comes out of that server's catalog entry:

    - ``name`` is the **configured server id**, so the nudge tells the model which
      connector it promised and skipped ("graph-index"), not a product we guessed.
    - ``triggers`` are the server's **real prefixed tool names** (the strings the
      model actually writes in narration when it reads its own tool list) plus the
      generic, vendor-free narration vocabulary in :data:`CODE_GRAPH_TRIGGERS`.
    - ``tool_patterns`` are those same real tool names (plus the id), so a promise
      is satisfied only by a call to a tool this server actually exposes -- a call
      to some unrelated tool cannot quietly close the gap.

    A server that is not in the catalog produces no family, so promising a
    connector that is not connected is never audited against tools that do not
    exist (no false positive). A ``None`` or malformed catalog yields ``()``.

    Callers normally use ``derive_families(entries) + BUILTIN_FAMILIES``: the
    builtins keep catching generic prose ("let me check the code graph") when no
    catalog is wired in, the derived families add the per-server precision.
    """
    try:
        servers = detect(mcp_entries or ())
    except Exception:  # a broken catalog must never break a run
        return ()

    families: list[ToolFamily] = []
    seen: set[str] = set()
    for server in servers:
        server_id = str(server.id or "").strip()
        if not server_id or server_id.lower() in seen:
            continue
        seen.add(server_id.lower())

        tool_names = [
            str(t).strip().lower() for t in server.tools if str(t).strip()
        ]
        # Narration triggers: the tool names the model can see in its own tool
        # list, then the vendor-free vocabulary. Lower-cased because sentences are
        # lower-cased before matching.
        triggers: list[str] = []
        for token in (*tool_names, server_id.lower(), *CODE_GRAPH_TRIGGERS):
            if token and token not in triggers:
                triggers.append(token)

        # Satisfaction patterns: only what this server really exposes. If the
        # catalog carries no tool names we cannot be precise, so fall back to the
        # generic structural vocabulary rather than a family nothing can satisfy.
        patterns = [re.escape(t) for t in tool_names]
        id_token = re.escape(server_id.lower())
        if id_token and id_token not in patterns:
            patterns.append(id_token)
        if not tool_names:
            patterns.extend(CODE_GRAPH_TOOL_PATTERNS)

        families.append(
            ToolFamily(
                name=server_id,
                triggers=tuple(triggers),
                tool_patterns=tuple(patterns),
                remedy=(
                    f"Issue an actual tool call on the '{server_id}' server now "
                    f"(it is connected and advertises those tools), or state "
                    f"plainly that you did not use it and say where your findings "
                    f"actually came from. Do not describe '{server_id}' as a "
                    f"source you consulted."
                ),
            )
        )
    return tuple(families)


def families_for(
    mcp_entries: "Iterable[Mapping[str, Any]] | None" = None,
) -> tuple[ToolFamily, ...]:
    """The families to audit a run with, given the connected MCP catalog.

    Per-server families derived from the catalog first (they are the precise
    ones), then the builtin families so generic prose is still caught when no
    catalog is wired in.
    """
    return derive_families(mcp_entries) + BUILTIN_FAMILIES


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?;\n])\s+", text or "") if s.strip()]


def mentions_family(sentence: str, family: ToolFamily) -> bool:
    """True when this sentence plans an action involving ``family``.

    Requires a trigger term, an intent word, and no negation. Deliberately
    conservative: a false negative just means no nudge, a false positive trains
    the model to ignore nudges.
    """
    low = sentence.lower()
    if not any(t in low for t in family.triggers):
        return False
    if any(n in low for n in NEGATIVE_WORDS):
        return False
    return any(i in low for i in INTENT_WORDS)


def called_families(
    called_tools: set[str] | list[str],
    families: tuple[ToolFamily, ...] = BUILTIN_FAMILIES,
) -> set[str]:
    """Families with at least one real tool call behind them."""
    called = set(called_tools or ())
    return {f.name for f in families if any(f.matches_tool(t) for t in called)}


def planned_families(
    assistant_text: str,
    families: tuple[ToolFamily, ...] = BUILTIN_FAMILIES,
) -> set[str]:
    """Families the narration says the agent intends to use."""
    out: set[str] = set()
    for sentence in _sentences(assistant_text):
        for family in families:
            if family.name not in out and mentions_family(sentence, family):
                out.add(family.name)
    return out


@dataclass
class AuditReport:
    """What was said vs what was done."""

    planned: set[str] = field(default_factory=set)
    satisfied: set[str] = field(default_factory=set)
    unfulfilled: set[str] = field(default_factory=set)
    families: tuple[ToolFamily, ...] = BUILTIN_FAMILIES

    @property
    def clean(self) -> bool:
        return not self.unfulfilled

    def remedy_for(self, family_name: str) -> str:
        for f in self.families:
            if f.name == family_name:
                return f.remedy
        return "Either make the call, or correct the claim."


def audit_run(
    assistant_text: str,
    called_tools: set[str] | list[str],
    families: tuple[ToolFamily, ...] = BUILTIN_FAMILIES,
) -> AuditReport:
    """Compare planned tool families against the tools actually invoked."""
    planned = planned_families(assistant_text, families)
    satisfied = called_families(called_tools, families)
    return AuditReport(
        planned=planned,
        satisfied=satisfied,
        unfulfilled=planned - satisfied,
        families=families,
    )


def render_planact_nudge(report: AuditReport) -> str:
    """The system-reminder for an outstanding plan/act gap. Empty when clean."""
    if report.clean:
        return ""
    lines = []
    for name in sorted(report.unfulfilled):
        lines.append(
            f"- You said you would use {name}, but no call to it appears in "
            f"this run. {report.remedy_for(name)}"
        )
    return (
        "<system-reminder>\n"
        "PLAN/ACT MISMATCH: your narration names a tool you have not actually "
        "called.\n"
        + "\n".join(lines)
        + "\nCorrect the record now. Claiming a source you did not consult is "
        "worse than admitting the fallback.\n"
        "</system-reminder>"
    )


__all__ = [
    "ToolFamily",
    "CODE_GRAPH_FAMILY",
    "WEB_SEARCH_FAMILY",
    "BUILTIN_FAMILIES",
    "code_graph_family",
    "derive_families",
    "families_for",
    "AuditReport",
    "mentions_family",
    "called_families",
    "planned_families",
    "audit_run",
    "render_planact_nudge",
]
