"""Validation tests for the curated skill set in ``claudio-backend/skills/``.

These tests check the contract that ``doc/skills-refresh-prompt.md`` produces:
one merged skill per core area, each valid under Claudio's skill loader, with a
credits manifest and freshness metadata. They are content-agnostic — they
validate structure and metadata, not the exact wording of any skill — so they
stay green across refreshes as long as the refresh keeps the contract.

Run:  cd claudio-backend && .venv/bin/python -m pytest tests/test_skills.py -q
"""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import pytest
import yaml

from claudio_core.skill_loader import scan_skills

BACKEND_BASE = Path(__file__).resolve().parents[1]
SKILLS_DIR = BACKEND_BASE / "skills"
PROMPT_PATH = BACKEND_BASE.parent / "doc" / "skills-refresh-prompt.md"

# The seven core areas, each merged into a single skill named by its slug.
AREA_SLUGS = [
    "agent-planning",
    "coding",
    "software-architecture",
    "linux",
    "agent-tools-usage",
    "debugging",
    "information-gathering",
]

# Claudio's own operational self-management skills (renamed from super-* to
# claudio-*). These are a distinct set from the seven merged area skills: they
# describe how Claudio checks, configures and schedules himself.
CLAUDIO_OPERATIONAL_SLUGS = [
    "claudio-autonomy",
    "claudio-self-check",
    "claudio-self-config",
    "claudio-self-scheduling",
]

# Frontmatter keys Claudio's loader accepts.
ALLOWED_FRONTMATTER_KEYS = {
    "name",
    "description",
    "license",
    "compatibility",
    "metadata",
    "allowed-tools",
}

# A merged skill must carry a review date no older than this to count as fresh.
MAX_FRESHNESS_DAYS = 365
# A merged skill must have real content, not a stub.
MIN_BODY_CHARS = 500


def _read_frontmatter(skill_md: Path) -> dict:
    """Parse the YAML frontmatter of a SKILL.md (mirrors the loader's split)."""
    text = skill_md.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md must start with YAML frontmatter")
    closing = text.find("\n---\n", 4)
    if closing < 0:
        raise ValueError("YAML frontmatter is not terminated")
    fm = yaml.safe_load(text[4:closing])
    if not isinstance(fm, dict):
        raise ValueError("YAML frontmatter must be a mapping")
    return fm


def _installed_area_slugs() -> list[str]:
    """Area slugs that are actually present as folders in the skills dir."""
    if not SKILLS_DIR.is_dir():
        return []
    present = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}
    return [slug for slug in AREA_SLUGS if slug in present]


def _installed_claudio_slugs() -> list[str]:
    """claudio-* operational slugs actually present as folders."""
    if not SKILLS_DIR.is_dir():
        return []
    present = {p.name for p in SKILLS_DIR.iterdir() if p.is_dir()}
    return [slug for slug in CLAUDIO_OPERATIONAL_SLUGS if slug in present]


def _require_skills() -> None:
    """Skip the data-dependent tests when no skills have been installed yet."""
    if not _installed_area_slugs():
        pytest.skip(
            "No merged skills installed yet — run the refresh prompt in "
            "doc/skills-refresh-prompt.md, then re-run these tests."
        )


# --------------------------------------------------------------------------
# Always-run checks (do not depend on skills being installed)
# --------------------------------------------------------------------------

def test_skills_dir_exists() -> None:
    assert SKILLS_DIR.is_dir(), f"skills directory missing: {SKILLS_DIR}"


def test_refresh_prompt_exists() -> None:
    assert PROMPT_PATH.is_file(), f"refresh prompt missing: {PROMPT_PATH}"


def test_refresh_prompt_covers_all_areas() -> None:
    text = PROMPT_PATH.read_text(encoding="utf-8")
    for slug in AREA_SLUGS:
        assert slug in text, f"refresh prompt does not mention area slug '{slug}'"


def test_refresh_prompt_defines_merge_and_validation() -> None:
    text = PROMPT_PATH.read_text(encoding="utf-8").lower()
    for keyword in ("merge", "superset", "dedup", "validate", "obsolete", "manifest"):
        assert keyword in text, f"refresh prompt missing '{keyword}' guidance"


# --------------------------------------------------------------------------
# Structural validation against Claudio's real loader
# --------------------------------------------------------------------------

def test_installed_skills_valid() -> None:
    """Every installed skill folder passes Claudio's loader (no broken skills)."""
    _require_skills()
    scan = scan_skills(SKILLS_DIR)
    broken = [e for e in scan.entries if not e.get("valid")]
    assert not broken, f"invalid skills: {broken}"


def test_all_seven_areas_installed() -> None:
    _require_skills()
    scan = scan_skills(SKILLS_DIR)
    missing = [slug for slug in AREA_SLUGS if slug not in scan.skills]
    assert not missing, f"missing core area skills: {missing}"


def test_skill_name_matches_folder() -> None:
    _require_skills()
    scan = scan_skills(SKILLS_DIR)
    for name, skill in scan.skills.items():
        assert skill.name == skill.directory.name, (
            f"skill name '{skill.name}' != folder '{skill.directory.name}'"
        )


def test_skill_frontmatter_keys_allowed() -> None:
    _require_skills()
    for slug in _installed_area_slugs():
        fm = _read_frontmatter(SKILLS_DIR / slug / "SKILL.md")
        unknown = set(fm) - ALLOWED_FRONTMATTER_KEYS
        assert not unknown, f"{slug}: disallowed frontmatter keys {unknown}"


def test_skill_description_present() -> None:
    _require_skills()
    for slug in _installed_area_slugs():
        fm = _read_frontmatter(SKILLS_DIR / slug / "SKILL.md")
        desc = fm.get("description")
        assert isinstance(desc, str) and desc.strip(), f"{slug}: empty description"
        assert len(desc) <= 1024, f"{slug}: description exceeds 1024 chars"


# --------------------------------------------------------------------------
# Claudio operational self-management skills (claudio-*)
# --------------------------------------------------------------------------

def _require_claudio_skills() -> None:
    """Skip the claudio-* tests when none of those skills are installed."""
    if not _installed_claudio_slugs():
        pytest.skip("No claudio-* operational skills installed.")


def test_claudio_operational_skills_installed() -> None:
    """All four claudio-* self-management skills are present and load."""
    _require_claudio_skills()
    scan = scan_skills(SKILLS_DIR)
    missing = [s for s in CLAUDIO_OPERATIONAL_SLUGS if s not in scan.skills]
    assert not missing, f"missing claudio-* skills: {missing}"


def test_claudio_operational_name_matches_folder() -> None:
    _require_claudio_skills()
    scan = scan_skills(SKILLS_DIR)
    for slug in _installed_claudio_slugs():
        sk = scan.skills[slug]
        assert sk.name == sk.directory.name, (
            f"{slug}: skill name '{sk.name}' != folder '{sk.directory.name}'"
        )


def test_claudio_operational_description_present() -> None:
    _require_claudio_skills()
    for slug in _installed_claudio_slugs():
        fm = _read_frontmatter(SKILLS_DIR / slug / "SKILL.md")
        desc = fm.get("description")
        assert isinstance(desc, str) and desc.strip(), f"{slug}: empty description"
        assert len(desc) <= 1024, f"{slug}: description exceeds 1024 chars"


def test_claudio_operational_frontmatter_keys_allowed() -> None:
    _require_claudio_skills()
    for slug in _installed_claudio_slugs():
        fm = _read_frontmatter(SKILLS_DIR / slug / "SKILL.md")
        unknown = set(fm) - ALLOWED_FRONTMATTER_KEYS
        assert not unknown, f"{slug}: disallowed frontmatter keys {unknown}"


def test_claudio_operational_no_super_prefix_remains() -> None:
    """The rename from super-* to claudio-* left no 'super' branding behind."""
    _require_claudio_skills()
    for slug in _installed_claudio_slugs():
        text = (SKILLS_DIR / slug / "SKILL.md").read_text(encoding="utf-8")
        assert "super-" not in text, f"{slug}: still contains 'super-'"
        assert "Super" not in text, f"{slug}: still contains 'Super'"


# --------------------------------------------------------------------------
# Merged-skill content checks
# --------------------------------------------------------------------------

def test_skill_has_credits_section() -> None:
    """Each merged skill must carry a Credits / Sources attribution section."""
    _require_skills()
    for slug in _installed_area_slugs():
        body = (SKILLS_DIR / slug / "SKILL.md").read_text(encoding="utf-8").lower()
        assert "credit" in body or "source" in body, (
            f"{slug}: no Credits/Sources section"
        )


def test_skill_is_substantive() -> None:
    _require_skills()
    for slug in _installed_area_slugs():
        body = (SKILLS_DIR / slug / "SKILL.md").read_text(encoding="utf-8")
        assert len(body) >= MIN_BODY_CHARS, (
            f"{slug}: body too short ({len(body)} chars) to be a merged skill"
        )


def test_skill_metadata_has_sources() -> None:
    """The merge must record its sources in frontmatter metadata."""
    _require_skills()
    for slug in _installed_area_slugs():
        fm = _read_frontmatter(SKILLS_DIR / slug / "SKILL.md")
        meta = fm.get("metadata")
        assert isinstance(meta, dict), f"{slug}: metadata must be a mapping"
        sources = meta.get("sources")
        assert isinstance(sources, list) and sources, f"{slug}: metadata.sources empty"
        for src in sources:
            assert isinstance(src, dict) and src.get("owner") and src.get("name"), (
                f"{slug}: each source needs owner and name"
            )


def test_skill_freshness() -> None:
    """Each merged skill must record a recent review date."""
    _require_skills()
    today = dt.date.today()
    for slug in _installed_area_slugs():
        fm = _read_frontmatter(SKILLS_DIR / slug / "SKILL.md")
        meta = fm.get("metadata") or {}
        reviewed = meta.get("last_reviewed")
        assert reviewed, f"{slug}: metadata.last_reviewed missing"
        try:
            reviewed_date = dt.date.fromisoformat(str(reviewed))
        except ValueError:
            pytest.fail(f"{slug}: last_reviewed '{reviewed}' is not YYYY-MM-DD")
        age = (today - reviewed_date).days
        assert 0 <= age <= MAX_FRESHNESS_DAYS, (
            f"{slug}: last reviewed {age} days ago (max {MAX_FRESHNESS_DAYS}) — refresh it"
        )


# --------------------------------------------------------------------------
# Manifest checks
# --------------------------------------------------------------------------

def test_manifest_exists() -> None:
    _require_skills()
    manifest = SKILLS_DIR / "SKILLS_MANIFEST.md"
    assert manifest.is_file(), f"manifest missing: {manifest}"


def test_manifest_lists_every_area_with_credits() -> None:
    _require_skills()
    manifest = (SKILLS_DIR / "SKILLS_MANIFEST.md").read_text(encoding="utf-8")
    low = manifest.lower()
    assert "license" in low, "manifest does not record licenses"
    assert "skillmd.com/skills/" in manifest, "manifest has no source URLs"
    for slug in _installed_area_slugs():
        assert slug in manifest, f"manifest does not list area '{slug}'"
