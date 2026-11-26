"""Offline availability probes for each Claudio engine backend.

Stdlib only. Never touches the network, never shells out, and never imports any
first-party Claudio package, so it runs inside a bare gate container that has
none of the repository's dependencies installed.

Public API
----------
probe_all(environ=None, which=shutil.which) -> dict

Return shape::

    {
        "generated_at": "2026-01-01T00:00:00+00:00",   # ISO-8601 str
        "backends": {
            "google_adk":  {...},
            "claude_code": {...},
            "opencode":    {...},
        },
    }

Each backend entry has exactly these keys::

    backend       str    backend id
    binary        str|None  executable probed with which(), None if none needed
    binary_found  bool   True if the binary was found, or no binary is needed
    module_found  bool   True if the Python module imported, or none is needed
    env_present   list   names of required-ish env vars that are set
    env_missing   list   names of env vars that are unset/blank
    available     bool   binary_found and module_found and bool(env_present)
    reason        str    always non-empty; why available is True or False

Contract: probe_all never raises. A missing binary, an unimportable module, a
missing key, or an internal error in a probe all become available=False plus a
non-empty reason.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
from datetime import datetime, timezone

__all__ = ["probe_all", "BACKENDS"]


# backend id -> (binary or None, python module or None, env var names)
BACKENDS = {
    "google_adk": (None, "google.adk", ("GOOGLE_API_KEY", "GEMINI_API_KEY")),
    "claude_code": ("claude", None, ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL")),
    "opencode": ("opencode", None, ("OPENAI_API_KEY", "OPENAI_BASE_URL")),
}


def _now_iso() -> str:
    """UTC timestamp as an ISO-8601 string; falls back to a fixed marker."""
    try:
        return datetime.now(timezone.utc).isoformat()
    except Exception:  # pragma: no cover - defensive
        return "1970-01-01T00:00:00+00:00"


def _env_get(environ, name):
    """Return a non-blank value for `name` from `environ`, else None."""
    try:
        value = environ.get(name)
    except Exception:
        return None
    if value is None:
        return None
    try:
        text = value.strip()
    except Exception:
        text = str(value)
    return text or None


def _module_found(module_name):
    """True if `module_name` is importable. Never raises."""
    if not module_name:
        return True
    try:
        return importlib.util.find_spec(module_name) is not None
    except Exception:
        return False


def _binary_found(which, binary):
    """(found, path) for `binary` using the `which` callable. Never raises."""
    if not binary:
        return True, None
    try:
        path = which(binary)
    except Exception as exc:
        return False, "which('%s') raised %s" % (binary, type(exc).__name__)
    if path:
        return True, str(path)
    return False, None


def _probe_backend(backend_id, environ, which):
    """Probe one backend. Never raises; always returns a full entry."""
    binary, module_name, env_names = BACKENDS[backend_id]
    entry = {
        "backend": backend_id,
        "binary": binary,
        "binary_found": False,
        "module_found": False,
        "env_present": [],
        "env_missing": [],
        "available": False,
        "reason": "not probed",
    }
    problems = []

    try:
        found, detail = _binary_found(which, binary)
        entry["binary_found"] = found
        if not found:
            if isinstance(detail, str) and detail:
                problems.append(detail)
            else:
                problems.append("executable '%s' not found on PATH" % binary)

        entry["module_found"] = _module_found(module_name)
        if not entry["module_found"]:
            problems.append("python module '%s' is not importable" % module_name)

        for name in env_names:
            if _env_get(environ, name):
                entry["env_present"].append(name)
            else:
                entry["env_missing"].append(name)
        if not entry["env_present"]:
            problems.append("no configuration env var set (need one of: %s)"
                            % ", ".join(env_names))
    except Exception as exc:  # pragma: no cover - defensive
        problems.append("probe failed with %s: %s" % (type(exc).__name__, exc))

    entry["available"] = bool(
        entry["binary_found"] and entry["module_found"] and entry["env_present"]
    )
    if entry["available"]:
        bits = []
        if binary:
            bits.append("found '%s'" % binary)
        if module_name:
            bits.append("module '%s' importable" % module_name)
        bits.append("using " + ", ".join(entry["env_present"]))
        entry["reason"] = "; ".join(bits)
    else:
        entry["reason"] = "; ".join(problems) or "backend unavailable"
    return entry


def probe_all(environ=None, which=shutil.which):
    """Report whether each backend can actually run here. Never raises."""
    if environ is None:
        try:
            environ = os.environ
        except Exception:  # pragma: no cover - defensive
            environ = {}
    backends = {}
    for backend_id in ("google_adk", "claude_code", "opencode"):
        try:
            backends[backend_id] = _probe_backend(backend_id, environ, which)
        except Exception as exc:  # pragma: no cover - defensive
            backends[backend_id] = {
                "backend": backend_id,
                "binary": None,
                "binary_found": False,
                "module_found": False,
                "env_present": [],
                "env_missing": [],
                "available": False,
                "reason": "probe crashed with %s: %s" % (type(exc).__name__, exc),
            }
    return {"generated_at": _now_iso(), "backends": backends}


if __name__ == "__main__":  # pragma: no cover - manual inspection helper
    import json

    print(json.dumps(probe_all(), indent=2, sort_keys=True))
