"""Tests for skill token compression (issue #53).

The goal of the reorg: shrink the always-loaded body of the biggest skills by
moving specialist detail into on-demand ``references/*.md`` (loaded via
``read_skill_resource``), fix topic contamination (visual/brand/canvas material
out of ``software-architecture``), and wire Gortex-first discovery into ``coding``
— all without losing information.

These tests are structural (char budgets, not exact tokens) so they stay green
across future refreshes while still catching a regression that re-bloats a body
or drops a reference.

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_issue53_skill_compression.py -q
"""
from __future__ import annotations

from pathlib import Path

import pytest

from claudio_core.skill_loader import _resolve_resource, scan_skills

BACKEND_BASE = Path(__file__).resolve().parents[1]
SKILLS_DIR = BACKEND_BASE / "skills"
DOC = BACKEND_BASE.parent / "doc"

TOP5 = ["coding", "agent-planning", "software-architecture",
        "agent-tools-usage", "information-gathering"]

# Each compressed entry-point body must stay well under its pre-compression size
# (the largest was ~77k chars). 25k chars is a generous ceiling that still fails
# if a big section is moved back into the body.
MAX_BODY_CHARS = 25_000


def _body(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8")
    c = text.find("\n---\n", 4)
    return text[c + 5:] if c >= 0 else text


def test_top5_bodies_within_budget():
    for slug in TOP5:
        body = _body(SKILLS_DIR / slug / "SKILL.md")
        assert len(body) < MAX_BODY_CHARS, (
            f"{slug}: entry-point body is {len(body)} chars (>= {MAX_BODY_CHARS}); "
            "move specialist detail to references/*.md"
        )


def test_every_reference_resolves_through_loader():
    scan = scan_skills(SKILLS_DIR)
    checked = 0
    for skill in scan.skills.values():
        for ref in sorted(Path(skill.directory).glob("references/*.md")):
            rel = str(ref.relative_to(skill.directory))
            _resolve_resource(skill, rel)  # raises if it escapes / is too big
            checked += 1
    assert checked >= 20, f"expected many references, found {checked}"


def test_references_index_points_at_existing_files():
    """Every `references/<file>.md` named in a SKILL.md body must exist on disk."""
    import re
    for skill_md in SKILLS_DIR.glob("*/SKILL.md"):
        body = skill_md.read_text(encoding="utf-8")
        for rel in set(re.findall(r"references/[A-Za-z0-9._/-]+\.md", body)):
            target = skill_md.parent / rel
            # cross-skill pointers like read_skill_resource("other", "references/x")
            if not target.exists():
                # allow pointers that name another skill's reference
                assert "read_skill_resource(" in body and skill_md.parent.name not in rel
                # find the owning skill folder mentioned near the pointer
                owners = [d.name for d in SKILLS_DIR.iterdir()
                          if d.is_dir() and (d / rel).exists()]
                assert owners, f"{skill_md.parent.name}: dangling reference {rel}"


def test_software_architecture_no_longer_contains_visual_material():
    body = _body(SKILLS_DIR / "software-architecture" / "SKILL.md").lower()
    # content markers unique to the moved visual/brand/canvas material (not the
    # explanatory "this moved out" note, which may name the topics).
    for marker in ("brand.primary", "derived 12-token palette",
                   "design philosophy creation", "expressed visually", "onboard.py"):
        assert marker not in body, f"software-architecture still contains {marker!r}"
    # but the real architecture content is still present
    assert "architecture decision workflow" in body
    assert "pattern selection" in body


def test_visual_material_moved_to_visual_design_references():
    vd = SKILLS_DIR / "visual-design-and-artifacts"
    assert (vd / "references" / "design-system-branding.md").is_file()
    assert (vd / "references" / "canvas-design-philosophy.md").is_file()
    branding = (vd / "references" / "design-system-branding.md").read_text().lower()
    assert "brand.primary" in branding or "palette" in branding
    canvas = (vd / "references" / "canvas-design-philosophy.md").read_text().lower()
    assert "canvas" in canvas


def test_coding_skill_wires_gortex_discovery():
    coding = SKILLS_DIR / "coding" / "SKILL.md"
    body = coding.read_text(encoding="utf-8")
    assert "gortex" in body.lower()
    assert (SKILLS_DIR / "coding" / "references" / "gortex-code-discovery.md").is_file()
    # the description mentions gortex so the always-on catalog routes to it
    assert "gortex" in _frontmatter_desc(coding).lower()


def _frontmatter_desc(skill_md: Path) -> str:
    import yaml
    text = skill_md.read_text(encoding="utf-8")
    c = text.find("\n---\n", 4)
    return str((yaml.safe_load(text[4:c]) or {}).get("description", ""))


def test_refresh_prompt_documents_token_discipline():
    text = (DOC / "skills-refresh-prompt.md").read_text(encoding="utf-8").lower()
    for kw in ("references/", "read_skill_resource", "entry point", "on-demand"):
        assert kw in text, f"refresh prompt missing {kw!r} guidance"
