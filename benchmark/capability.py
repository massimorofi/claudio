#!/usr/bin/env python3
"""Build the committed capability report for the Claudio benchmark harness.

``build_capability()`` is :func:`benchmark.probes.probe_all` plus two things the
raw probe lacks:

* a per-backend ``fallback`` string -- ``"none"`` when the backend can really
  run here, ``"stub_driver"`` when the benchmark must fall back to the offline
  stub driver instead of the real engine;
* a ``notes`` list explaining, in the artifact itself, what was and was not
  proven by the probe.

Running the module writes ``benchmark/results/capability.json``::

    python3 benchmark/capability.py            # writes the artifact
    python3 benchmark/capability.py --stdout   # print, do not write

The file is written with ``indent=2`` and ``sort_keys=True`` and ends in a
newline, so two consecutive runs differ **only** in ``generated_at``. That is
what makes the artifact safe to commit: a rerun is a one-line diff.

Stdlib-only, offline, never raises for a missing binary or credential.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# ``benchmark/`` may or may not be on sys.path (``PYTHONPATH=benchmark python3 -m
# benchmark.capability`` vs. plain ``python3 benchmark/capability.py``); make the
# sibling ``probes`` module importable either way.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

try:  # normal import (PYTHONPATH=benchmark, or run as a script)
    from probes import BACKENDS, FALLBACK_NONE, FALLBACK_STUB, probe_all
except ImportError:  # pragma: no cover - executed as ``python3 -m benchmark.capability``
    from benchmark.probes import (  # type: ignore
        BACKENDS,
        FALLBACK_NONE,
        FALLBACK_STUB,
        probe_all,
    )

#: Default location of the committed artifact.
DEFAULT_OUTPUT = _HERE / "results" / "capability.json"


def build_capability(environ: dict | None = None) -> dict:
    """Capability report for ``google_adk``, ``claude_code`` and ``opencode``.

    Returns the :func:`probe_all` mapping with, for every backend, an added
    ``fallback`` field (``"none"`` or ``"stub_driver"``), and a top-level
    ``notes`` list. Deterministic apart from ``generated_at``.
    """
    report = probe_all(environ)
    backends = report.get("backends") or {}

    available: list[str] = []
    stubbed: list[str] = []
    for name in BACKENDS:
        entry = backends.setdefault(name, {"available": False, "missing": ["not probed"]})
        ok = bool(entry.get("available"))
        entry["available"] = ok
        entry["fallback"] = FALLBACK_NONE if ok else FALLBACK_STUB
        if not isinstance(entry.get("missing"), list):
            entry["missing"] = [str(entry.get("missing"))]
        (available if ok else stubbed).append(name)

    notes = list(report.get("notes") or [])
    notes.append(
        f"{len(available)} of {len(BACKENDS)} backends are runnable in this "
        f"environment: " + (", ".join(available) if available else "none") + "."
    )
    if stubbed:
        notes.append(
            "Unavailable backends fall back to '" + FALLBACK_STUB + "': the "
            "benchmark harness drives them with a deterministic stub instead of "
            "the real CLI/library, so their numbers measure harness overhead "
            "only and are not comparable to a real-engine run."
        )
    notes.append(
        "Regenerate with: python3 benchmark/capability.py "
        "(idempotent - only generated_at changes)."
    )
    report["notes"] = notes
    return report


def render(report: dict) -> str:
    """Serialize deterministically: sorted keys, 2-space indent, trailing NL."""
    return json.dumps(report, indent=2, sort_keys=True) + "\n"


def write_report(path: Path | str | None = None, environ: dict | None = None) -> Path:
    """Write the artifact (creating parent dirs) and return its path."""
    target = Path(path) if path else DEFAULT_OUTPUT
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(build_capability(environ)), encoding="utf-8")
    return target


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--stdout" in argv:
        sys.stdout.write(render(build_capability()))
        return 0
    out = argv[0] if argv and not argv[0].startswith("-") else None
    target = write_report(out)
    sys.stderr.write(f"wrote {target}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
