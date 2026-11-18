"""Plan/act auditing: did the agent actually call the tools it said it would?

Why this exists
---------------
A stated intention to use a tool and the tool call itself are unrelated events
unless something connects them. Nothing did. An agent could write "let me check
Gortex" in its narration, never issue a single graph call, mark the plan item
done, and report a result -- and the transcript looked fine. That is exactly what
happened on a documentation task where the plan said "via Gortex + source reads"
and only file tools were ever called.

This module makes the gap *computable*. Given the assistant's own text and the
set of tools actually invoked, it reports families of tools that were named with
intent but never called. The engine turns that into a nudge, so the model sees
its own omission while the run is still going, and the run manager can log it
afterwards.

Design notes
------------
- **Families, not tool names.** Tool names are unstable across MCP servers and
  versions (the Gortex guide says so explicitly). A family is a prose trigger
  ("gortex", "code graph") plus patterns that match the real tool names.
- **Intent required.** Mentioning a tool in a sentence is not a plan. Negations
  and disclosures ("Gortex was unavailable", "I did not query the graph") must
  not trigger a nudge, or the audit becomes noise the model learns to ignore.
- **Satisfied means called.** Once any tool in the family has run, the family is
  no longer outstanding, regardless of what was said earlier.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

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


# The family this exists for, plus two adjacent ones that fail the same way.
GORTEX_FAMILY = ToolFamily(
    name="the Gortex code graph",
    triggers=("gortex", "code graph", "graph tool", "callers", "callees",
              "change impact", "impact analysis"),
    tool_patterns=(r"gortex", r"^graph"),
    remedy=(
        "Issue an actual Gortex tool call now, or state plainly that you did not "
        "use Gortex and that your findings come from the file tools instead. "
        "Do not describe the graph as a source you consulted."
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

BUILTIN_FAMILIES: tuple[ToolFamily, ...] = (GORTEX_FAMILY, WEB_SEARCH_FAMILY)


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
    "GORTEX_FAMILY",
    "WEB_SEARCH_FAMILY",
    "BUILTIN_FAMILIES",
    "AuditReport",
    "mentions_family",
    "called_families",
    "planned_families",
    "audit_run",
    "render_planact_nudge",
]
