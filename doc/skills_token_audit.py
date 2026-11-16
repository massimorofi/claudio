#!/usr/bin/env python3
"""Audit the token cost of the curated skill set (issue #53).

Claudio puts only skill *names + descriptions* into the always-on system prompt;
full bodies load on demand via ``load_skill``, and ``references/*.md`` load only
when the agent calls ``read_skill_resource``. This script measures all three
layers with the same tokenizer the audit used (tiktoken ``o200k_base``) so the
before/after numbers are reproducible and comparable.

Frontmatter is stripped exactly like ``claudio_core.skill_loader`` so the body
count matches what ``load_skill`` actually returns.

Usage:
    claudio-backend/.venv/bin/python doc/skills_token_audit.py [skills_dir]
"""
from __future__ import annotations

import sys
from pathlib import Path

import tiktoken
import yaml

ENC = tiktoken.get_encoding("o200k_base")


def ntok(text: str) -> int:
    return len(ENC.encode(text, disallowed_special=()))


def parse_skill(manifest: Path):
    source = manifest.read_text(encoding="utf-8")
    if not source.startswith("---\n"):
        return manifest.parent.name, "", source.strip()
    closing = source.find("\n---\n", 4)
    if closing < 0:
        return manifest.parent.name, "", source.strip()
    fm = yaml.safe_load(source[4:closing]) or {}
    body = source[closing + 5:].strip()
    return str(fm.get("name", manifest.parent.name)), str(fm.get("description", "")), body


def main(skills_dir: Path) -> int:
    rows = []
    for skill_md in sorted(skills_dir.glob("*/SKILL.md")):
        name, desc, body = parse_skill(skill_md)
        refs = sorted(skill_md.parent.glob("references/*.md"))
        ref_tok = sum(ntok(p.read_text(encoding="utf-8")) for p in refs)
        rows.append((name, ntok(desc), ntok(body), len(refs), ref_tok))

    rows.sort(key=lambda r: -r[2])
    print(f"{'skill':32}{'desc tok':>10}{'body tok':>10}{'refs':>6}{'ref tok':>9}")
    print("-" * 67)
    for name, d, b, nr, rt in rows:
        print(f"{name:32}{d:>10}{b:>10}{nr:>6}{rt:>9}")
    tot_d = sum(r[1] for r in rows)
    tot_b = sum(r[2] for r in rows)
    tot_r = sum(r[4] for r in rows)
    n_refs = sum(r[3] for r in rows)
    print("-" * 67)
    print(f"{'TOTAL':32}{tot_d:>10}{tot_b:>10}{n_refs:>6}{tot_r:>9}")
    print(f"\nAlways-on catalog (descriptions + framing): ~{tot_d + 162} tokens")
    print(f"Single-load sum of all bodies:              {tot_b} tokens")
    print(f"On-demand references (paid only if read):   {tot_r} tokens")
    return 0


if __name__ == "__main__":
    default = Path(__file__).resolve().parent.parent / "claudio-backend" / "skills"
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else default
    raise SystemExit(main(target))
