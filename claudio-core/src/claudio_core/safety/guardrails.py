"""Safety guardrails — framework-free input/output screening.

Upstream these were wired to the OpenAI Agents SDK's ``input_guardrail`` /
``output_guardrail`` decorators. The screening logic itself never needed the
SDK, so it lives here unchanged; the engine decides where in its own pipeline
to call it.

Two guardrails:

* :func:`screen_input` is **advisory**. Prompt-injection patterns in user text
  are logged and reported but never block a run — a false positive here would
  make the tool unusable, and the real defence against injection is the
  approval gate on destructive tools, not text matching.
* :func:`screen_output` **does** block. A leaked API key or private key in
  model output is a concrete, irreversible harm, and the pattern is narrow
  enough that a false positive is rare and cheap to explain.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

#: Prompt-injection fingerprints. Advisory only.
_INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "ignore_previous_instructions",
        re.compile(
            r"ignore\s+(all\s+|any\s+)?(previous|prior|above|earlier)\s+"
            r"(instructions?|prompts?|rules?|guidance)",
            re.IGNORECASE,
        ),
    ),
    (
        "disregard_system_prompt",
        re.compile(
            r"disregard\s+(all\s+)?(your\s+)?(system\s+prompt|instructions|rules)",
            re.IGNORECASE,
        ),
    ),
    (
        "pretend_to_be",
        re.compile(
            r"you\s+are\s+now\s+(a|an)\s+(different|new|unrestricted|jailbroken)",
            re.IGNORECASE,
        ),
    ),
    (
        "reveal_system_prompt",
        re.compile(
            r"(reveal|show|print|repeat|output)\s+(your\s+|the\s+)?"
            r"(system\s+prompt|hidden\s+instructions|initial\s+instructions)",
            re.IGNORECASE,
        ),
    ),
    (
        "role_override",
        re.compile(
            r"new\s+(system\s+)?instructions?\s*:\s*(you|ignore|forget)",
            re.IGNORECASE,
        ),
    ),
    (
        "dan_jailbreak",
        re.compile(r"\b(DAN|DUDE|STAN|AIM)\s+(mode|jailbreak)\b", re.IGNORECASE),
    ),
)

#: Credential shapes that must never leave the system.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_key", re.compile(r"sk-[A-Za-z0-9]{20,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key_block", re.compile(r"-----BEGIN (RSA )?PRIVATE KEY-----")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}")),
    ("generic_bearer", re.compile(r"[A-Za-z0-9_-]{40,}\.[A-Za-z0-9_-]{20,}")),
)


@dataclass(frozen=True)
class GuardrailResult:
    """Outcome of one guardrail pass.

    ``tripwire`` is the decision; ``reason`` explains it for the audit log and
    for the operator-facing message when a run is blocked.
    """

    tripwire: bool
    reason: str = ""
    matched: tuple[str, ...] = field(default_factory=tuple)

    @property
    def passed(self) -> bool:
        return not self.tripwire


def screen_input(text: str) -> GuardrailResult:
    """Screen user input for injection attempts.

    Never trips. Returns the matched pattern names so the caller can log or
    surface them, but the run proceeds regardless.
    """
    if not text:
        return GuardrailResult(tripwire=False)

    matched = [name for name, pattern in _INJECTION_PATTERNS if pattern.search(text)]
    if matched:
        logger.warning(
            "input guardrail: injection patterns matched (%s) — advisory only, run continues",
            ", ".join(matched),
        )
    return GuardrailResult(tripwire=False, matched=tuple(matched))


def screen_output(text: str) -> GuardrailResult:
    """Screen model output for leaked secrets.

    Trips on any credential-shaped string. Empty output also trips: an empty
    final answer is a silent failure that would otherwise be reported as
    success, and the recovery loop treats a trip as "try again".
    """
    if text is None or not text.strip():
        return GuardrailResult(tripwire=True, reason="Model produced empty output.")

    matched = [name for name, pattern in _SECRET_PATTERNS if pattern.search(text)]
    if matched:
        logger.error(
            "output guardrail: secret-like content detected (%s) — blocking",
            ", ".join(matched),
        )
        return GuardrailResult(
            tripwire=True,
            reason=f"Output blocked: contains secret-like content ({', '.join(matched)}).",
            matched=tuple(matched),
        )
    return GuardrailResult(tripwire=False)


def redact_secrets(text: str) -> str:
    """Replace secret-shaped substrings with a redaction marker.

    Used where the text must be kept (logs, persisted transcripts) but must not
    contain the credential.
    """
    if not text:
        return text
    redacted = text
    for _name, pattern in _SECRET_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def screen_tool_arguments(
    tool_name: str, arguments: dict[str, Any], allowlist_check=None
) -> GuardrailResult:
    """Screen a tool call before execution.

    ``allowlist_check`` is an optional callable ``(command: str) -> bool``. When
    supplied for a shell-like tool, a command outside the allowlist trips. This
    keeps the policy decision in core while letting the caller own the policy
    itself (``claudio_core.safety.allowlist``).
    """
    if not isinstance(arguments, dict):
        return GuardrailResult(tripwire=True, reason="Tool arguments were not an object.")

    if allowlist_check is not None and tool_name in {"run_command", "shell", "bash"}:
        command = str(arguments.get("command", ""))
        if command and not allowlist_check(command):
            return GuardrailResult(
                tripwire=True,
                reason=f"Command not in allowlist: {command[:120]}",
            )
    return GuardrailResult(tripwire=False)


__all__ = [
    "GuardrailResult",
    "screen_input",
    "screen_output",
    "redact_secrets",
    "screen_tool_arguments",
]
