"""Input/output guardrails (Claudio §3.6.6).

Guardrails run in parallel with the agent (SDK-native). They are a fast safety net,
not a deep classifier: cheap heuristics that trip a wire on clear violations.
"""
from __future__ import annotations

import re

from agents import (
    Agent,
    GuardrailFunctionOutput,
    RunContextWrapper,
    input_guardrail,
    output_guardrail,
)

from .context import ToolContext

# Prompt-injection heuristics (deliberately conservative — high-confidence patterns).
_INJECTION_PATTERNS = [
    re.compile(r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(all\s+)?(previous|prior|your)\s+instructions", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(in\s+)?(developer|dan|jailbreak)\s+mode", re.IGNORECASE),
    re.compile(r"reveal\s+your\s+(system\s+prompt|hidden\s+instructions)", re.IGNORECASE),
]

# Secret-leak patterns for the output guardrail.
_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"-----BEGIN\s+(RSA\s+)?PRIVATE\s+KEY-----"),
]

MAX_INPUT_CHARS = 100_000


@input_guardrail(name="input_safety")
async def input_safety_guardrail(
    ctx: RunContextWrapper[ToolContext],
    agent: Agent[ToolContext],
    input: str | list,
) -> GuardrailFunctionOutput:
    """Screen the user's message for length and obvious injection attempts."""
    text = input if isinstance(input, str) else " ".join(str(x) for x in input)

    if len(text) > MAX_INPUT_CHARS:
        return GuardrailFunctionOutput(
            output_info={"reason": "input_too_long", "length": len(text)},
            tripwire_triggered=True,
        )

    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            return GuardrailFunctionOutput(
                output_info={"reason": "injection_detected", "pattern": pattern.pattern},
                tripwire_triggered=True,
            )

    return GuardrailFunctionOutput(
        output_info={"reason": "ok"},
        tripwire_triggered=False,
    )


@output_guardrail(name="output_safety")
async def output_safety_guardrail(
    ctx: RunContextWrapper[ToolContext],
    agent: Agent[ToolContext],
    output,
) -> GuardrailFunctionOutput:
    """Sanity-check the final output: non-empty and free of leaked secrets."""
    text = output if isinstance(output, str) else str(output)

    if not text or not text.strip():
        return GuardrailFunctionOutput(
            output_info={"reason": "empty_output"},
            tripwire_triggered=True,
        )

    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            return GuardrailFunctionOutput(
                output_info={"reason": "secret_leak_detected", "pattern": pattern.pattern},
                tripwire_triggered=True,
            )

    return GuardrailFunctionOutput(
        output_info={"reason": "ok"},
        tripwire_triggered=False,
    )
