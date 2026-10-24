"""Fix proposal + execution for the autonomous action layer.

The agent reads a detected issue and proposes ONE specific shell command that
should fix it, with an explanation and a risk assessment. The operator reviews
the *exact* command and accepts or dismisses it. On accept the command runs (as
the backend's user), is recorded in the audit log, and the health check is
re-run so the operator can see whether the issue is actually resolved.

Safety model (the operator chose "agent proposes each command"):
  * The human reviews the literal command string before it runs — that review is
    the primary gate.
  * A small catastrophic blocklist is a backstop: commands that are unambiguously
    destructive to the whole system are refused even if approved. This is a net
    under the human review, not a substitute for it.
  * Every execution is written to the audit log with its exit code and output.

The proposal call goes through ``claudio_core.openai_chat_completion`` — a
framework-free chat-completions client — rather than a bound SDK client, so this
module carries no agent-framework dependency.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, asdict
from typing import Any

from claudio_core.llm import openai_chat_completion
from claudio_core.settings import AppConfig

logger = logging.getLogger(__name__)

# Commands that are refused outright regardless of approval. This is a backstop
# against the worst hallucinations, not a complete safety story — the operator's
# review of the exact command is the real gate.
_CATASTROPHIC_PATTERNS = [
    r"\brm\s+-[a-z]*r[a-z]*f\s+/(\s|$)",   # rm -rf /
    r"\brm\s+-[a-z]*f[a-z]*r\s+/(\s|$)",   # rm -fr /
    r"\bmkfs(\.\w+)?\b",                    # mkfs.*
    r"\bdd\b.*\bof=/dev/[a-z]+\b",          # dd of=/dev/sdX
    r":\(\)\s*\{\s*:\|:\&\s*\}\s*;",       # fork bomb
    r"\bchmod\s+-R\s+777\s+/(\s|$)",        # chmod -R 777 /
    r"\bshutdown\b",
    r"\binit\s+0\b",
    r">\s*/dev/sd[a-z]\b",                  # redirect onto a raw disk
]
_CATASTROPHIC_RE = [re.compile(p) for p in _CATASTROPHIC_PATTERNS]

# Unit tokens (``foo.service``, ``snap.x.y.service``, ``foo@bar.timer`` ...) that
# appear in a systemctl-style command. Used to check each named unit actually
# exists on this host before we offer the command as a fix.
_UNIT_TOKEN_RE = re.compile(
    r"\b([\w@.\-]+\.(?:service|socket|timer|mount|target|slice|path|device))\b"
)


@dataclass
class FixProposal:
    command: str
    explanation: str
    risk: str = "low"          # low | medium | high
    reversible: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _is_catastrophic(command: str) -> bool:
    return any(rx.search(command) for rx in _CATASTROPHIC_RE)


async def _loaded_units() -> set[str]:
    """Units systemd currently knows about (including not-found-but-loaded ones).

    ``systemctl cat`` only answers for units that have a unit *file* on disk.
    A stale failed unit whose package was removed has no file, so ``cat`` fails
    even though the unit is very much present in systemd's state and is exactly
    what ``reset-failed`` must target. ``list-units --all`` covers that case.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "systemctl", "list-units", "--all", "--no-legend", "--plain", "--no-pager",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
    except (OSError, asyncio.TimeoutError):
        return set()
    units: set[str] = set()
    for line in out.decode(errors="replace").splitlines():
        m = _UNIT_TOKEN_RE.search(line)
        if m:
            units.add(m.group(1))
    return units


async def _validate_command(command: str) -> tuple[bool, str]:
    """Check a proposed command can actually run on this system.

    Catches the common failure mode where the model proposes a command that
    references a non-existent systemd unit (or other missing resource), which
    would fail the moment it is run. We probe each unit named in the command
    with ``systemctl cat`` (read-only, no privileges needed) and report any
    that don't exist.

    A unit that is *loaded* in systemd counts as existing even when it has no
    unit file: otherwise the one command that clears a stale failed unit
    (``systemctl reset-failed <unit>``) is rejected precisely when it is
    needed.

    Returns ``(ok, reason)``. ``ok`` is False only when we can positively
    identify a missing resource; commands we can't validate (no units named,
    or the probe itself failing) pass through as ok so we never block a
    legitimate fix on a probe limitation.
    """
    units = _UNIT_TOKEN_RE.findall(command)
    if not units:
        return True, ""

    loaded = await _loaded_units()

    missing: list[str] = []
    for unit in sorted(set(units)):
        if unit in loaded:
            continue
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl",
                "cat",
                unit,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode != 0:
                missing.append(unit)
        except (OSError, asyncio.TimeoutError):
            # Can't probe (systemctl unavailable / hung) — don't block.
            return True, ""

    if missing:
        return False, (
            "command references systemd unit(s) that do not exist on this host: "
            + ", ".join(missing)
        )
    return True, ""


def _ensure_sudo_stdin(command: str) -> str:
    """Make a leading ``sudo`` read its password from stdin (``-S``).

    The agent proposes a command like ``sudo journalctl --vacuum-size=100M``.
    Run non-interactively, plain ``sudo`` demands a TTY and fails. ``-S`` tells
    sudo to read the password from stdin instead, which is where we pipe it.
    Commands that don't start with sudo are returned unchanged.
    """
    s = command.lstrip()
    if s.startswith("sudo "):
        rest = s[5:].lstrip()
        first = rest.split(None, 1)[0] if rest else ""
        if first == "-S":  # already has it
            return command
        return f"sudo -S {rest}"
    return command


def _extract_json(text: str) -> dict[str, Any]:
    """Parse a JSON object out of a model response that may be fenced or padded."""
    text = text.strip()
    # Strip common markdown fences.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the first {...} block.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError("model did not return a JSON object")


_PROMPT = """You are a Linux server repair assistant. A health check found this issue on an Ubuntu host.

Issue title: {title}
Severity: {severity}
Evidence:
{detail}
{tried}
Propose the shell commands that will actually clear this issue. Think about what the evidence shows, not what usually fixes a problem that looks like this.

Respond with ONLY a JSON object, no prose, in this exact shape:
{{"command": "<the exact shell command(s)>", "explanation": "<what it does and why it fixes this issue>", "risk": "<low|medium|high>", "reversible": <true or false>}}

Rules:
- Put the whole remediation in "command". Several steps joined with " && " are allowed and preferred when one command cannot finish the job. Never chain anything destructive.
- Fix the WHOLE problem, not the symptom named in the title. For a failed systemd unit that means: stop the unit, stop and mask whatever triggers it (its .timer and .path units, and any unit that Wants=/Requires= it), then clear the failure with ``systemctl reset-failed <unit>``. Masking only the .service leaves the .timer/.path firing and the failure returns.
- If the evidence contains a warning from the tool you just ran (e.g. "its triggering units are still active: X, Y"), treat it as the specification for the next step and address those exact units.
- If the unit is reported "not-found", it is still loaded in systemd and ``reset-failed`` is the correct way to clear it. Do not avoid the unit name just because its package was removed.
- Only reference systemd units, files, paths and services that actually exist on this host. Do not invent unit names.
- If no safe automated fix exists, set risk to "high" and explain that a human should handle it manually.
"""


MAX_FIX_ATTEMPTS = 3


def _format_tried(tried: list[tuple[str, str]]) -> str:
    """Render the already-attempted commands for the prompt (empty if none)."""
    if not tried:
        return ""
    lines = [
        "",
        "Previously attempted fixes that did NOT work. Do NOT repeat any of these,",
        "and do not propose a command that is essentially the same — pick a",
        "genuinely different approach:",
    ]
    for cmd, why in tried:
        lines.append(f"- {cmd if cmd else '(empty)'} — {why}")
    lines.append("")
    return "\n".join(lines)


async def propose_fix(
    issue: dict[str, Any],
    config: AppConfig,
    tried: list[tuple[str, str]] | None = None,
) -> FixProposal:
    """Propose a valid fix command, retrying with feedback until one passes.

    A single generation can come back with a command that references a unit that
    doesn't exist, is catastrophic, or repeats something already tried. Rather
    than stopping, we feed the rejection reason back to the model and ask for a
    different command, up to ``max_fix_attempts`` times. ``tried`` seeds the
    rejection history with commands already run on this issue in earlier rounds,
    so a fresh proposal doesn't repeat a fix that already failed.

    Returns the first proposal that passes validation. Raises if none does.
    """
    history: list[tuple[str, str]] = list(tried or [])
    max_attempts = int(getattr(config.autonomous, "max_fix_attempts", MAX_FIX_ATTEMPTS))
    if max_attempts < 1:
        max_attempts = 1
    last_error = "no attempt made"
    for _attempt in range(max_attempts):
        prompt = _PROMPT.format(
            title=issue.get("title", ""),
            severity=issue.get("severity", ""),
            detail=issue.get("detail", "") or issue.get("summary", ""),
            tried=_format_tried(history),
        )
        raw = await openai_chat_completion(
            base_url=config.llm.base_url,
            model=config.llm.model,
            api_key=config.llm.api_key or "local",
            messages=[
                {"role": "system", "content": "You propose safe, specific Linux remediation commands and nothing else."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1024,
            timeout=float(config.llm.timeout_seconds),
        )
        data = _extract_json(raw)
        command = str(data.get("command", "")).strip()
        if not command:
            last_error = "proposal contained no command"
            history.append(("", last_error))
            continue
        if _is_catastrophic(command):
            last_error = f"refused catastrophic command: {command}"
            history.append((command, last_error))
            continue
        valid, reason = await _validate_command(command)
        if not valid:
            last_error = reason
            history.append((command, reason))
            continue
        if any(command == prior for prior, _ in history):
            last_error = "model repeated an already-tried command"
            history.append((command, last_error))
            continue
        risk = str(data.get("risk", "low")).lower()
        if risk not in ("low", "medium", "high"):
            risk = "medium"
        reversible = bool(data.get("reversible", True))
        return FixProposal(
            command=command,
            explanation=str(data.get("explanation", "")).strip(),
            risk=risk,
            reversible=reversible,
        )
    raise ValueError(
        f"could not produce a valid fix after {max_attempts} attempts: {last_error}"
    )


async def execute_fix(
    command: str,
    password: str | None = None,
    timeout: float = 120.0,
) -> dict[str, Any]:
    """Run the approved command and capture its result. No interpretation — the
    operator already approved this exact string.

    If ``password`` is given and the command starts with ``sudo``, the command is
    rewritten to ``sudo -S`` and the password is fed through stdin so sudo can
    authenticate without a TTY. The password is never logged, never stored, and
    never written into the returned output: ``sudo -S`` reads it from stdin
    without echoing it back.
    """
    if _is_catastrophic(command):
        return {
            "ok": False,
            "exit_code": None,
            "stdout": "",
            "stderr": f"Refused: command matches a catastrophic pattern: {command}",
            "duration_ms": 0,
        }
    loop = asyncio.get_running_loop()
    start = loop.time()

    # Only feed a password when the command actually starts with sudo; otherwise
    # the password would just sit in the command's stdin (or worse, be consumed
    # by a command that reads stdin for its own reasons).
    feed: bytes | None = None
    run_cmd = command
    if password:
        if command.lstrip().startswith("sudo "):
            run_cmd = _ensure_sudo_stdin(command)
            feed = (password + "\n").encode()
        # No leading sudo: ignore the password rather than risk leaking it into
        # an unrelated stdin.

    try:
        proc = await asyncio.create_subprocess_shell(
            run_cmd,
            stdin=asyncio.subprocess.PIPE if feed is not None else None,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as e:
        return {
            "ok": False,
            "exit_code": None,
            "stdout": "",
            "stderr": f"Failed to start command: {e}",
            "duration_ms": int((loop.time() - start) * 1000),
        }
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(input=feed), timeout=timeout
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return {
            "ok": False,
            "exit_code": None,
            "stdout": "",
            "stderr": f"Command timed out after {timeout}s",
            "duration_ms": int((loop.time() - start) * 1000),
        }
    out = stdout.decode(errors="replace") if stdout else ""
    err = stderr.decode(errors="replace") if stderr else ""
    # Keep the audit/output bounded.
    max_chars = 8000
    if len(out) > max_chars:
        out = out[:max_chars] + f"\n[clipped: {len(out) - max_chars} chars omitted]"
    if len(err) > max_chars:
        err = err[:max_chars] + f"\n[clipped: {len(err) - max_chars} chars omitted]"
    return {
        "ok": proc.returncode == 0,
        "exit_code": proc.returncode,
        "stdout": out,
        "stderr": err,
        "duration_ms": int((loop.time() - start) * 1000),
    }
