"""Deterministic model-role routing (issue #58, Increment 2).

The auto-router used to spend one fast-profile LLM call on *every* run just to
decide FAST vs PRIMARY. The plan's principle is that deterministic work should
not invoke a model: a request that plainly needs tools -- it names a file,
contains a code fence, or asks to write/edit/run something -- does not need a
model to learn it is not a one-line chat answer.

``deterministic_route`` makes that conservative call: it returns ``"primary"``
only when the request carries unambiguous structured indicators of tool work,
and ``"unknown"`` otherwise (the caller then falls back to the LLM classifier,
or to the default role). It deliberately does NOT try to classify free-form
natural language by keyword -- that is the brittle guessing the plan warns
against. The effect is that the router LLM call disappears for the common
coding/tool case while genuinely ambiguous short questions keep the model's
judgment.
"""
from __future__ import annotations

import re

# Structured signals that a request needs tools / planning / multi-step work.
# These are explicit, not fuzzy intent guesses.
_TOOL_INDICATORS = (
    "```",                       # code fence
    "write_file", "edit_file", "read_file", "run_command", "batch_read",
    "search_with_context", "locate_symbol",
)
# Verbs that, in a request addressed to an agent with a workspace, imply an
# action on files/commands rather than a conversational answer.
_ACTION_VERBS = re.compile(
    r"\b(create|write|edit|modify|refactor|implement|fix|delete|rename|move|"
    r"copy|install|run|execute|build|deploy|test|debug|add|remove|update)\b",
    re.IGNORECASE,
)
# A path-like token: a slash-bearing or extension-bearing filename.
_PATH_LIKE = re.compile(
    r"(?:^|[\s'\"`(])((?:[\w.\-/]+\.[A-Za-z]{1,6})|(?:[\w\-]+/)+[\w.\-]+)"
)


def deterministic_route(message: str) -> str:
    """Return ``"primary"`` for unambiguous tool work, else ``"unknown"``.

    Never returns ``"fast"`` -- deciding a request is a trivial no-tool answer
    from text alone is the brittle part, so that stays with the model.
    """
    if not message or not message.strip():
        return "unknown"
    text = message
    lowered = text.lower()
    for marker in _TOOL_INDICATORS:
        if marker in lowered:
            return "primary"
    # Multi-line requests are almost never a one-step chat answer.
    if text.count("\n") >= 2:
        return "primary"
    if _PATH_LIKE.search(text):
        return "primary"
    if _ACTION_VERBS.search(text):
        return "primary"
    return "unknown"


__all__ = ["deterministic_route"]
