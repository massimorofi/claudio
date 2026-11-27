#!/usr/bin/env python3
"""Audit how deeply the Gortex vendor is coupled into Claudio.

This is a *reporting* tool, not a gate: it always exits 0. It exists so that the
decoupling work described in ``docs/gortex-decoupling-review.md`` can be
measured before and after each step, and so that a future regression (a vendor
string reappearing in ``claudio-core/src``) is visible in one command.

What it scans
-------------
Roots (relative to the repository root, missing roots are skipped and reported):

  claudio-core/src, claudio-core/tests, claudio-backend/src,
  claudio-backend/skills, docker/, compose.yaml,
  claudio-backend/config/config.example.yaml

What it looks for
-----------------
  vendor-token         the case-insensitive vendor token ``gortex``
  out-of-band-channel  filesystem / config side-channels that bypass the MCP
                       handshake: ``.gortex-index``, ``gortex_manifest_path``,
                       ``DEFAULT_MANIFEST_PATH``

A line matching an out-of-band token is classified as ``out-of-band-channel``
(the more specific class) and also records ``gortex`` in ``matched_tokens``, so
per-class counts sum to the total number of hits without double-printing.

Output
------
One line per hit::

    <class> <path>:<line> <text>

followed by a per-class summary count. ``--json PATH`` writes the same data to
a JSON document.

Stdlib only; no third-party imports.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

# --- scan configuration -----------------------------------------------------

VENDOR_TOKEN = "gortex"

# Out-of-band channels: ways the indexer's state reaches Claudio that are NOT
# the MCP handshake. These are the coupling this audit cares about most, because
# they are invisible to a capability negotiation.
OUT_OF_BAND_TOKENS = (
    ".gortex-index",
    "gortex_manifest_path",
    "DEFAULT_MANIFEST_PATH",
)

CLASS_VENDOR = "vendor-token"
CLASS_OOB = "out-of-band-channel"

DEFAULT_ROOTS = (
    "claudio-core/src",
    "claudio-core/tests",
    "claudio-backend/src",
    "claudio-backend/skills",
    "docker",
    "compose.yaml",
    "claudio-backend/config/config.example.yaml",
)

SKIP_DIR_NAMES = {
    ".git", "__pycache__", ".venv", "venv", "node_modules", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "dist", "build", ".idea", ".pytest_tmp",
}

MAX_TEXT_LEN = 400


def _iter_files(roots: Iterable[Path]) -> tuple[list[Path], list[str]]:
    """Return (files, missing_root_descriptions)."""
    files: list[Path] = []
    missing: list[str] = []
    seen: set[Path] = set()
    for root in roots:
        if not root.exists():
            missing.append(str(root))
            continue
        if root.is_file():
            if root not in seen:
                seen.add(root)
                files.append(root)
            continue
        for path in sorted(root.rglob("*")):
            if any(part in SKIP_DIR_NAMES for part in path.parts):
                continue
            if not path.is_file():
                continue
            if path.suffix.lower() in {".pyc", ".so", ".pyd", ".png", ".jpg",
                                        ".gif", ".ico", ".pdf", ".zip", ".gz"}:
                continue
            if path not in seen:
                seen.add(path)
                files.append(path)
    return files, missing


def _classify(line: str) -> tuple[str | None, list[str]]:
    """Return (class, matched tokens) for one source line."""
    lowered = line.lower()
    matched: list[str] = []
    oob_hits = [tok for tok in OUT_OF_BAND_TOKENS if tok.lower() in lowered]
    vendor_hit = VENDOR_TOKEN in lowered
    if oob_hits:
        matched.extend(oob_hits)
    if vendor_hit:
        matched.append(VENDOR_TOKEN)
    if oob_hits:
        return CLASS_OOB, matched
    if vendor_hit:
        return CLASS_VENDOR, matched
    return None, []


def scan(repo_root: Path, roots: Iterable[str]) -> dict[str, Any]:
    """Scan ``repo_root`` and return the report as a JSON-serialisable dict."""
    root_paths = [repo_root / r for r in roots]
    files, missing = _iter_files(root_paths)
    hits: list[dict[str, Any]] = []
    for path in files:
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:  # unreadable file must not abort the report
            missing.append(f"{path} (unreadable: {exc.__class__.__name__})")
            continue
        for lineno, line in enumerate(raw.splitlines(), start=1):
            klass, matched = _classify(line)
            if klass is None:
                continue
            text = line.strip()
            if len(text) > MAX_TEXT_LEN:
                text = text[:MAX_TEXT_LEN] + "..."
            try:
                rel = path.relative_to(repo_root).as_posix()
            except ValueError:
                rel = path.as_posix()
            hits.append({
                "class": klass,
                "path": rel,
                "line": lineno,
                "text": text,
                "matched_tokens": matched,
            })
    counts: dict[str, int] = {CLASS_VENDOR: 0, CLASS_OOB: 0}
    per_file: dict[str, int] = {}
    for h in hits:
        counts[h["class"]] = counts.get(h["class"], 0) + 1
        per_file[h["path"]] = per_file.get(h["path"], 0) + 1
    return {
        "tool": "audit_gortex_coupling",
        "version": 1,
        "repo_root": repo_root.as_posix(),
        "roots_requested": list(roots),
        "roots_missing_or_skipped": missing,
        "files_scanned": len(files),
        "total_hits": len(hits),
        "summary_by_class": counts,
        "summary_by_file": dict(sorted(per_file.items())),
        "hits": hits,
    }


def render_text(report: dict[str, Any]) -> str:
    out: list[str] = []
    for h in report["hits"]:
        out.append(f"{h['class']} {h['path']}:{h['line']} {h['text']}")
    out.append("")
    out.append(f"files scanned: {report['files_scanned']}")
    out.append(f"total hits:    {report['total_hits']}")
    for klass in (CLASS_VENDOR, CLASS_OOB):
        out.append(f"{klass}: {report['summary_by_class'].get(klass, 0)}")
    if report["roots_missing_or_skipped"]:
        out.append("missing/skipped roots: "
                   + ", ".join(report["roots_missing_or_skipped"]))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report Gortex coupling in the Claudio tree (never gates).")
    parser.add_argument(
        "--root", default=".",
        help="repository root to scan (default: current directory)")
    parser.add_argument(
        "--json", dest="json_path", default=None,
        help="also write the machine-readable report to this path")
    parser.add_argument(
        "--quiet", action="store_true",
        help="do not print the text report to stdout")
    args = parser.parse_args(argv)

    repo_root = Path(args.root).resolve()
    try:
        report = scan(repo_root, DEFAULT_ROOTS)
    except Exception as exc:  # noqa: BLE001 - a report tool must never gate
        print(f"audit_gortex_coupling: scan failed: {exc}", file=sys.stderr)
        return 0

    if not args.quiet:
        print(render_text(report))

    if args.json_path:
        out_path = Path(args.json_path)
        try:
            if out_path.parent and str(out_path.parent) not in ("", "."):
                out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(report, indent=2, sort_keys=False) + "\n",
                encoding="utf-8")
            print(f"wrote {out_path} ({report['total_hits']} hits, "
                  f"{report['files_scanned']} files scanned)")
        except OSError as exc:
            print(f"audit_gortex_coupling: could not write {out_path}: {exc}",
                  file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
