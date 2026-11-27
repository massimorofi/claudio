#!/usr/bin/env python3
"""Static audit: every LLM call site must be purpose-tagged in the call ledger.

Issue #58 (Increment 0a(b)-ii). The unified ledger only proves anything if
*every* model call reaches it exactly once with a known ``purpose``. The
primary agent turn is covered by the engine recorder; this script watches the
rest -- router, compaction, learning, fixer, consolidation, the daemon and the
``/config/llm/test`` probe -- so a new call site cannot be merged untagged.

A call site is one of:

* ``openai_chat_completion(...)``   -- the framework-free one-shot client
  (claudio_core.llm), used by every auxiliary helper.
* ``...chat.completions.create(...)`` -- the raw OpenAI SDK boundary the
  engine (and therefore the daemon agent) goes through.

A site is **tagged** when its own call carries ``usage_sink=`` / ``recorder=``,
or when the enclosing function routes through the process-wide default
recorder (``get_default_recorder`` / ``_claudio_call_context``), which is the
documented path for calls that outlive a run.

A site is **bad-purpose** when any literal ``purpose=`` in the file is not in
``claudio_core.accounting.PURPOSES``.

Stdlib-only by design (``ast`` + ``pathlib``): it must run in CI and in a bare
container with no dependencies installed.

Usage (repo root or anywhere):
    python3 scripts/audit_llm_call_sites.py

Exit code 0 only when every audited site is tagged and every purpose literal is
known. Prints one line per site plus a final ``audited=N untagged=0`` summary.
"""
from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Directories scanned for call sites (first-party source only, no tests).
DEFAULT_TARGETS = (
    REPO_ROOT / "claudio-core" / "src",
    REPO_ROOT / "claudio-backend" / "src",
)

#: The two wire boundaries a model call can be made through.
ONE_SHOT_NAMES = {"openai_chat_completion"}
SDK_METHODS = {"create"}

#: Sink keywords that mean "this call hands its usage to the ledger".
SINK_KEYWORDS = {"usage_sink", "recorder", "accounting_sink"}

#: Text markers of a default-recorder fallback inside the enclosing function.
DEFAULT_RECORDER_MARKERS = (
    "get_default_recorder",
    "set_default_recorder",
    "_claudio_call_context",
    "make_engine_recorder",
)


def load_purposes() -> tuple[str, ...]:
    """Read ``PURPOSES`` out of accounting.py by parsing it (no import needed).

    Parsing instead of importing keeps this script free of any sys.path /
    package-install assumption: it audits a checkout, it does not run it.
    """
    path = REPO_ROOT / "claudio-core" / "src" / "claudio_core" / "accounting.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "PURPOSES":
                    values = []
                    for elt in getattr(node.value, "elts", []):
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            values.append(elt.value)
                    if values:
                        return tuple(values)
    raise SystemExit(f"could not read PURPOSES from {path}")


def _callee_name(func: ast.AST) -> tuple[str, str]:
    """Classify a call's callee as ("one_shot"|"sdk"|"", display name)."""
    if isinstance(func, ast.Name):
        return ("one_shot", func.id) if func.id in ONE_SHOT_NAMES else ("", func.id)
    if isinstance(func, ast.Attribute):
        # Rebuild the dotted prefix, e.g. "self._openai_client.chat.completions".
        parts = [func.attr]
        node: ast.AST = func.value
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        prefix = ".".join(reversed(parts))
        if func.attr in ONE_SHOT_NAMES:
            return "one_shot", prefix
        if func.attr in SDK_METHODS and "chat.completions" in prefix:
            return "sdk", prefix
        return "", prefix
    return "", ""


class Visitor(ast.NodeVisitor):
    """Collect every model call site with its enclosing function and keywords."""

    def __init__(self, path: Path, purposes: tuple[str, ...]) -> None:
        self.path = path
        self.purposes = purposes
        self.source = path.read_text(encoding="utf-8")
        self.sites: list[dict] = []
        self.bad_purposes: list[dict] = []
        self._stack: list[ast.AST] = []

    # -- enclosing scope ---------------------------------------------------

    def visit_AsyncFunctionDef(self, node):  # noqa: N802
        self._scope(node)

    def visit_FunctionDef(self, node):  # noqa: N802
        self._scope(node)

    def _scope(self, node):
        self._stack.append(node)
        self.generic_visit(node)
        self._stack.pop()

    # -- call sites --------------------------------------------------------

    def visit_Call(self, node):  # noqa: N802
        kind, display = _callee_name(node.func)
        kwargs = {kw.arg for kw in node.keywords if kw.arg is not None}
        if kind:
            purpose = _literal_keyword(node, "purpose")
            self.sites.append(
                {
                    "path": self.path,
                    "line": node.lineno,
                    "kind": kind,
                    "callee": display,
                    "kwargs": kwargs,
                    "purpose": purpose,
                    "scope": self._stack[-1] if self._stack else None,
                }
            )
        self.generic_visit(node)


def _literal_keyword(node: ast.Call, name: str) -> str | None:
    for kw in node.keywords:
        if kw.arg == name and isinstance(kw.value, ast.Constant):
            return kw.value.value if isinstance(kw.value.value, str) else None
    return None


def _purpose_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """Every literal ``purpose="..."`` anywhere in the file, with its line."""
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "purpose" and isinstance(kw.value, ast.Constant):
                    if isinstance(kw.value.value, str):
                        out.append((node.lineno, kw.value.value))
    return out


def _scope_source(site: dict) -> str:
    """Source text of the function enclosing a call site (\"\" at module level)."""
    scope = site.get("scope")
    if scope is None:
        return ""
    path = site["path"]
    lines = path.read_text(encoding="utf-8").splitlines()
    start = max(1, getattr(scope, "lineno", 1))
    end = min(len(lines), getattr(scope, "end_lineno", start))
    return "\n".join(lines[start - 1 : end])


def classify(site: dict, purposes: tuple[str, ...]) -> tuple[str, str, str]:
    """Return ``(status, purpose, sink)`` for one audited call site.

    ``status`` is ``tagged`` or ``UNTAGGED``; ``sink`` names how the call reaches
    the ledger, which is what the report line prints.
    """
    kwargs = site["kwargs"]
    purpose = site["purpose"] or ""
    if site["kind"] == "one_shot":
        if "usage_sink" in kwargs:
            sink = "usage_sink="
        elif "recorder" in kwargs:
            sink = "recorder="
        else:
            sink = ""
    else:  # raw SDK boundary: the recorder is attached to the client context
        sink = "" if "recorder" in kwargs else ""
    if not sink:
        body = _scope_source(site)
        if any(marker in body for marker in DEFAULT_RECORDER_MARKERS):
            sink = "default-recorder"
        elif kwargs & SINK_KEYWORDS:
            sink = ",".join(sorted(kwargs & SINK_KEYWORDS))
    if not sink:
        return "UNTAGGED", purpose, "-"
    if purpose and purpose not in purposes:
        return "BAD_PURPOSE", purpose, sink
    if not purpose:
        # The tag is set by the sink the call hands over (make_llm_sink(purpose=...))
        # or by the enclosing helper's own purpose= literal; either way the
        # emission is accounted, so report the resolved tag rather than blank.
        purpose = _scope_purpose(site, purposes) or "(from sink)"
    return "tagged", purpose, sink


def _scope_purpose(site: dict, purposes: tuple[str, ...]) -> str:
    """Best-effort purpose for a call whose own ``purpose=`` is dynamic/absent."""
    body = _scope_source(site)
    found = [p for p in purposes if f'purpose="{p}"' in body or f"purpose='{p}'" in body]
    return found[0] if len(found) == 1 else ""


def audit_file(path: Path, purposes: tuple[str, ...]) -> list[dict]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    visitor = Visitor(path, purposes)
    visitor.visit(tree)
    rows = []
    for site in visitor.sites:
        status, purpose, sink = classify(site, purposes)
        rows.append(
            {
                "path": path,
                "line": site["line"],
                "callee": site["callee"],
                "status": status,
                "purpose": purpose,
                "sink": sink,
            }
        )
    for line, value in _purpose_literals(tree):
        if value not in purposes:
            rows.append(
                {
                    "path": path,
                    "line": line,
                    "callee": "purpose= literal",
                    "status": "BAD_PURPOSE",
                    "purpose": value,
                    "sink": "-",
                }
            )
    return rows


def iter_python_files(targets: tuple[Path, ...]):
    for root in targets:
        if not root.exists():
            continue
        for path in sorted(root.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            yield path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "paths",
        nargs="*",
        help="files or directories to audit (default: claudio-core/src, claudio-backend/src)",
    )
    args = parser.parse_args(argv)

    purposes = load_purposes()
    if args.paths:
        targets = [Path(p) for p in args.paths]
    else:
        targets = list(DEFAULT_TARGETS)

    files: list[Path] = []
    for target in targets:
        if target.is_dir():
            files.extend(iter_python_files((target,)))
        elif target.suffix == ".py":
            files.append(target)

    rows: list[dict] = []
    for path in files:
        try:
            rows.extend(audit_file(path, purposes))
        except SyntaxError as exc:  # a file that cannot be parsed is a finding
            rows.append(
                {
                    "path": path,
                    "line": getattr(exc, "lineno", 0) or 0,
                    "callee": "<unparsable>",
                    "status": "UNTAGGED",
                    "purpose": "-",
                    "sink": f"syntax_error: {exc.msg}",
                }
            )

    root = REPO_ROOT
    for row in rows:
        try:
            shown = row["path"].relative_to(root)
        except ValueError:
            shown = row["path"]
        print(
            f"{shown}:{row['line']} {row['callee']} purpose={row['purpose']} "
            f"sink={row['sink']} {row['status']}"
        )

    untagged = sum(1 for r in rows if r["status"] == "UNTAGGED")
    bad = sum(1 for r in rows if r["status"] == "BAD_PURPOSE")
    print(
        f"audited={len(rows)} untagged={untagged} bad_purpose={bad} "
        f"purposes_known={len(purposes)}"
    )
    return 1 if (untagged or bad) else 0


if __name__ == "__main__":
    sys.exit(main())
