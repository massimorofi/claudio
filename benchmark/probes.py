#!/usr/bin/env python3
"""Offline capability probes for the three Claudio backend engines.

The backend selects an engine at startup (``claudio_backend.engine.factory``):
``adk``/``google_adk`` (the library engine), ``claude_code`` and ``opencode``
(two CLI engines). A benchmark run is only comparable to production if we know
which of those three can actually execute *here and now*. This module answers
that question without touching the network.

Design constraints (enforced by ``benchmark/tests/test_capability.py``):

* **stdlib only** -- ``os``, ``shutil``, ``importlib.util``, ``sys``, ``json``,
  ``datetime``. Nothing under ``claudio_core`` / ``claudio_backend`` is
  imported: pulling in the real engine package would drag in ``google-adk``
  and pydantic settings, which defeats the point of a cheap probe.
* **offline** -- no socket, no HTTP, and deliberately **no ``--version``
  subprocess**: a CLI that hangs or prints a build hash would make the
  artifact non-deterministic. Availability is decided from on-PATH lookups,
  importable-module lookups and environment variables only.
* **never raises** -- every probe is wrapped; a failure becomes a
  ``{"available": False, "missing": [...]}`` entry with a reason.
* **deterministic** -- given the same ``environ``, ``PATH`` and installed
  packages, ``probe_all()`` returns byte-identical output.

Public API:

``probe_all(environ=None) -> dict``
    ``{"generated_at": ..., "backends": {name: {...}}, "notes": [...]}`` with
    exactly the keys ``google_adk``, ``claude_code``, ``opencode``.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from datetime import datetime, timezone

#: Canonical backend names used by every artifact in ``benchmark/``.
BACKENDS: tuple[str, ...] = ("google_adk", "claude_code", "opencode")

#: Env var that overrides the engine selection at runtime.
ENGINE_SELECTOR_ENV = "CLAUDIO_ENGINE"

#: Fallback labels written into the capability report.
FALLBACK_NONE = "none"
FALLBACK_STUB = "stub_driver"

# --------------------------------------------------------------------------- #
# Per-backend metadata
# --------------------------------------------------------------------------- #
# Every value below was read out of this worktree; the source is cited inline.
#   binary "claude"    -> claudio-core/src/claudio_core/settings.py
#                         (ClaudeCodeEngineConfig.binary)
#   binary "opencode"  -> claudio-core/src/claudio_core/settings.py
#                         (OpenCodeEngineConfig.binary)
#   ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL
#                      -> claudio-backend/src/claudio_backend/engine/
#                         claude_code/adapter.py
#   OPENAI_API_KEY     -> claudio-backend/src/claudio_backend/engine/
#                         opencode/adapter.py (generated opencode.json uses
#                         "{env:OPENAI_API_KEY}")
#   CLAUDIO__LLM__API_KEY
#                      -> claudio-core/src/claudio_core/settings.py
#                         (ENV_PREFIX "CLAUDIO__" + secret path "llm.api_key")
BACKEND_SPECS: dict[str, dict] = {
    "google_adk": {
        "kind": "adk",
        "driver": "library (google-adk)",
        "binary": None,
        "binary_env": None,
        "import_name": "google.adk",
        "config_field": "engine.kind (default)",
        "required_env": (
            "CLAUDIO__LLM__API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
        ),
        "optional_env": (
            "OPENAI_BASE_URL",
            "ANTHROPIC_BASE_URL",
            ENGINE_SELECTOR_ENV,
        ),
    },
    "claude_code": {
        "kind": "claude_code",
        "driver": "CLI (claude -p --output-format stream-json)",
        "binary": "claude",
        "binary_env": "CLAUDIO__ENGINE__CLAUDE_CODE__BINARY",
        "import_name": None,
        "config_field": "engine.claude_code.binary",
        "required_env": (
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_AUTH_TOKEN",
            "CLAUDIO__LLM__API_KEY",
        ),
        "optional_env": (
            "ANTHROPIC_BASE_URL",
            "CLAUDIO__ENGINE__CLAUDE_CODE__ALLOW_OPENAI_ENDPOINT",
            ENGINE_SELECTOR_ENV,
        ),
    },
    "opencode": {
        "kind": "opencode",
        "driver": "CLI (opencode run -m <provider>/<model>)",
        "binary": "opencode",
        "binary_env": "CLAUDIO__ENGINE__OPENCODE__BINARY",
        "import_name": None,
        "config_field": "engine.opencode.binary",
        "required_env": (
            "OPENAI_API_KEY",
            "OPENAI_AUTH_TOKEN",
            "CLAUDIO__LLM__API_KEY",
        ),
        "optional_env": (
            "OPENAI_BASE_URL",
            ENGINE_SELECTOR_ENV,
        ),
    },
}


# --------------------------------------------------------------------------- #
# Helpers (each one swallows its own errors)
# --------------------------------------------------------------------------- #
def _now_iso() -> str:
    """UTC timestamp, second resolution (the only non-deterministic field)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _env(environ: dict | None) -> dict:
    """Return a plain dict of the environment to probe against."""
    if environ is None:
        return dict(os.environ)
    return {str(k): str(v) for k, v in dict(environ).items()}


def _has_value(environ: dict, name: str) -> bool:
    """True when ``name`` is set to something non-empty and not a placeholder."""
    value = environ.get(name, "")
    if value is None:
        return False
    value = str(value).strip()
    if not value:
        return False
    return value.lower() not in {"changeme", "none", "null", "todo", "your-key-here"}


def _lookup_binary(binary: str | None, environ: dict) -> str | None:
    """Absolute path of ``binary``, or None. Never raises.

    ``environ`` (not necessarily ``os.environ``) supplies PATH so a caller can
    probe a hypothetical environment -- which is what the tests do.
    """
    if not binary:
        return None
    try:
        path = environ.get("PATH") or None
        found = shutil.which(binary, path=path)
        return found
    except Exception:
        return None


def _module_available(import_name: str | None) -> bool:
    """True when ``import_name`` can be found on sys.path (never imports it)."""
    if not import_name:
        return True
    try:
        return importlib.util.find_spec(import_name) is not None
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Probes
# --------------------------------------------------------------------------- #
def probe_backend(name: str, environ: dict | None = None) -> dict:
    """Probe one backend by name. Never raises."""
    environ = _env(environ)
    spec = BACKEND_SPECS.get(name)
    if spec is None:
        return {
            "name": name,
            "available": False,
            "missing": [f"unknown backend {name!r}"],
            "binary": None,
            "binary_path": None,
            "env_vars": {},
            "prerequisites": [f"unknown backend {name!r}"],
            "reason": "unknown backend",
        }

    missing: list[str] = []
    prerequisites: list[str] = []

    # 1. binary on PATH (CLI engines only)
    binary = spec["binary"]
    binary_override = environ.get(spec["binary_env"], "") if spec["binary_env"] else ""
    binary_name = (binary_override.strip() or binary) if binary else None
    binary_path = _lookup_binary(binary_name, environ)
    if binary_name:
        prerequisites.append(f"binary {binary_name!r} on PATH")
        if not binary_path:
            missing.append(f"binary {binary_name!r} not found on PATH")

    # 2. python module importable (library engine only)
    import_name = spec["import_name"]
    if import_name:
        prerequisites.append(f"python module {import_name!r} importable")
        if not _module_available(import_name):
            missing.append(f"python module {import_name!r} not installed")

    # 3. a credential the adapter can use
    required = tuple(spec["required_env"])
    present = [v for v in required if _has_value(environ, v)]
    prerequisites.append(
        "one of " + ", ".join(required) + " set to a non-empty value"
    )
    if not present:
        missing.append("no credential in " + ", ".join(required))

    env_vars = {
        "required": list(required),
        "required_present": present,
        "optional": [
            v for v in spec["optional_env"] if v not in required
        ],
        "present": sorted(v for v in set(required) | set(spec["optional_env"])
                           if _has_value(environ, v)),
    }

    return {
        "name": name,
        "kind": spec["kind"],
        "driver": spec["driver"],
        "config_field": spec["config_field"],
        "binary": binary_name,
        "binary_path": binary_path,
        "binary_env": spec["binary_env"],
        "env_vars": env_vars,
        "prerequisites": prerequisites,
        "missing": missing,
        "available": not missing,
        "reason": "all prerequisites satisfied" if not missing else "; ".join(missing),
    }


def probe_all(environ: dict | None = None) -> dict:
    """Probe every backend. Never raises; always returns all three keys."""
    environ = _env(environ)
    backends: dict[str, dict] = {}
    for name in BACKENDS:
        try:
            backends[name] = probe_backend(name, environ)
        except Exception as exc:  # pragma: no cover - belt and braces
            backends[name] = {
                "name": name,
                "available": False,
                "missing": [f"probe error: {type(exc).__name__}: {exc}"],
                "binary": BACKEND_SPECS.get(name, {}).get("binary"),
                "binary_path": None,
                "env_vars": {},
                "prerequisites": [],
                "reason": f"probe error: {type(exc).__name__}",
            }

    notes = [
        "Probes are offline: PATH lookups, importable-module lookups and "
        "environment variables only. No network call and no CLI subprocess.",
        "A backend is 'available' only when its binary/module prerequisite and "
        "at least one credential env var are present; credentials are never "
        "validated against a live endpoint.",
        "Values are read from the environment of this process unless an "
        "explicit environ mapping is passed to probe_all()/build_capability().",
    ]

    return {
        "generated_at": _now_iso(),
        "python": sys.version.split()[0],
        "backends": backends,
        "notes": notes,
    }


__all__ = ["BACKENDS", "BACKEND_SPECS", "probe_all", "probe_backend"]
