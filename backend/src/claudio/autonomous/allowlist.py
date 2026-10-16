"""Command allowlist: the positive gate for every daemon-initiated elevated command.

The daemon never runs a command that isn't on this list. Where the catastrophic
blocklist in :mod:`claudio.autonomous.fixer` is a *denylist* backstop for freeform
strings a human reviews, this is an *allowlist*: only exact, reviewed commands pass.
Combined with scoped sudo (``sudo -n`` on an exact argv list) and the OS sudoers
file, it forms the hard boundary the spec §4 requires.

The list is config-driven: ``autonomous.command_allowlist`` overrides the built-in
defaults when set. Every entry is validated at config load (see
:func:`validate_command_entry`) so a typo'd or dangerous entry fails fast rather
than at 3am when a task tries to use it.

Service restarts are gated separately by ``autonomous.recovery.restart_allowlist``
(see :func:`validate_service_name`): the command allowlist covers the fixed
maintenance commands, while the set of restartable units is its own list so it can
be tuned without rewriting every fixed command.
"""
from __future__ import annotations

import re

# Built-in allowlist: exact command strings the daemon may run elevated.
# These MUST mirror the sudoers file (see deploy/sudoers.d-claudio). The
# ``sudoers_snippet`` helper below generates the matching lines.
DEFAULT_ALLOWLIST: list[str] = [
    "/usr/bin/apt-get update",
    "/usr/bin/apt-get -y autoremove",
    "/usr/bin/apt-get -y upgrade",
    "/usr/bin/journalctl --vacuum-size=200M",
    "/usr/bin/docker system prune -f",
    "/opt/claudio/scripts/cleanup_tmp.sh",
]

# Characters that signal shell-metacharacter smuggling. A legitimate allowlisted
# command never contains these; their presence means someone is trying to chain a
# second command or interpolate a substitution.
_SHELL_METACHARS = set(";|&$><`\n\r")

# A systemd unit name: a conservative subset of the systemd naming rules.
_SERVICE_NAME_RE = re.compile(r"^[a-z0-9@._-]+$")

# A journalctl vacuum size such as 200M / 1G / 512M.
_VACUUM_SIZE_RE = re.compile(r"^\d+[MGT]$")


def validate_command_entry(entry: str) -> str:
    """Validate one allowlist entry. Returns the stripped entry or raises ValueError.

    An entry must be an absolute path to a binary (with optional fixed args),
    contain no shell metacharacters, and not match the catastrophic blocklist.
    """
    if not isinstance(entry, str):
        raise ValueError(f"allowlist entry must be a string, got {type(entry).__name__}")
    s = entry.strip()
    if not s:
        raise ValueError("allowlist entry is empty")
    first = s.split(None, 1)[0]
    if not first.startswith("/"):
        raise ValueError(f"allowlist entry must start with an absolute path: {entry!r}")
    bad = _SHELL_METACHARS & set(s)
    if bad:
        raise ValueError(
            f"allowlist entry contains forbidden shell characters {sorted(bad)}: {entry!r}"
        )
    # Deferred import: fixer imports settings, and settings validates allowlist
    # entries, so importing fixer at module top would be circular. By the time a
    # validator runs, the settings module is fully loaded.
    from .fixer import _is_catastrophic

    if _is_catastrophic(s):
        raise ValueError(f"allowlist entry matches a catastrophic pattern: {entry!r}")
    return s


def effective_allowlist(config) -> list[str]:
    """Return the effective allowlist: the config override if set, else defaults."""
    a = getattr(config, "autonomous", None)
    override = getattr(a, "command_allowlist", None) if a else None
    if override:
        return list(override)
    return list(DEFAULT_ALLOWLIST)


def _normalize(command: str) -> str:
    """Collapse runs of whitespace so comparison is exact but whitespace-tolerant."""
    return " ".join(command.split())


def is_allowed(command: str, config) -> bool:
    """Exact-match a command string against the effective allowlist.

    ``command`` is the inner command WITHOUT the ``sudo`` prefix (the caller adds
    ``sudo -n`` at exec time). Matching is on the whitespace-normalized string so
    the daemon's argv-joined command lines up with the stored entries.
    """
    if not isinstance(command, str):
        return False
    target = _normalize(command)
    return any(_normalize(allowed) == target for allowed in effective_allowlist(config))


def validate_service_name(name: str, config) -> str:
    """Validate a service/unit name for restart. Returns the name or raises ValueError.

    The name must match a safe unit-name pattern AND be present in the configured
    ``recovery.restart_allowlist``. A unit that isn't listed cannot be restarted
    by the daemon, regardless of what the sudoers file happens to allow.
    """
    if not isinstance(name, str) or not _SERVICE_NAME_RE.match(name):
        raise ValueError(f"invalid service name: {name!r}")
    a = getattr(config, "autonomous", None)
    restart_allowlist = getattr(getattr(a, "recovery", None), "restart_allowlist", None) or []
    if name not in restart_allowlist:
        raise ValueError(f"service {name!r} is not in the restart allowlist")
    return name


def validate_vacuum_size(size: str) -> str:
    """Validate a journalctl vacuum size string (e.g. ``200M``). Returns or raises."""
    if not isinstance(size, str) or not _VACUUM_SIZE_RE.match(size):
        raise ValueError(f"invalid vacuum size {size!r} (expected e.g. '200M')")
    return size


def sudoers_snippet(config) -> str:
    """Generate the ``/etc/sudoers.d/claudio`` lines for the effective allowlist.

    Keeps the OS-level sudo rules in sync with the code-level allowlist. The
    restart commands are generated from ``recovery.restart_allowlist`` (one exact
    line per unit); the fixed commands come straight from the effective allowlist.
    """
    a = getattr(config, "autonomous", None)
    restart_allowlist = getattr(getattr(a, "recovery", None), "restart_allowlist", None) or []
    lines = [
        "# Claudio scoped sudo — generated from the command allowlist.",
        "# Review before installing. Validate with: visudo -cf /etc/sudoers.d/claudio",
        "# Exact commands only; the daemon runs as the 'claudio' user.",
    ]
    seen: set[str] = set()
    for cmd in effective_allowlist(config):
        norm = _normalize(cmd)
        if norm in seen:
            continue
        seen.add(norm)
        lines.append(f"claudio ALL=(root) NOPASSWD: {norm}")
    for unit in restart_allowlist:
        cmd = _normalize(f"/usr/bin/systemctl restart {unit}")
        if cmd in seen:
            continue
        seen.add(cmd)
        lines.append(f"claudio ALL=(root) NOPASSWD: {cmd}")
    return "\n".join(lines) + "\n"
