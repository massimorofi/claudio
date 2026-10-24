"""Promotion: turn an approved skill draft into a live ``SKILL.md``.

This is the safety gate the self-improvement plan leans on. A draft is only
written into the live skills directory after it has been *validated* to parse
as a real skill — we render the candidate ``SKILL.md`` into a throwaway temp
directory and run the same ``scan_skills`` the engine uses, so a malformed
draft can never land in the catalog and silently drop out of the prompt (or
worse, land and mislead it).

When the target skill already exists (an update, not a new skill) the existing
folder is copied to a timestamped backup first, so a bad auto-merge is always
revertible.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from claudio_core.db import Database
from claudio_core.settings import AppConfig
from claudio_core.skill_loader import scan_skills

from ..engine.adk_engine import resolve_skills_dir
from ..paths import backend_base

logger = logging.getLogger(__name__)

# Skill names must be lowercase-hyphenated and match the directory name
# (skill_loader._SKILL_NAME). We enforce the same contract here before writing.
import re

_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def render_skill_md(name: str, description: str, body: str) -> str:
    """Render a ``SKILL.md`` from its parts.

    The frontmatter is emitted with ``yaml.safe_dump`` so any characters in the
    description are escaped correctly; the body follows after the closing
    fence. The result is exactly what ``_parse_manifest`` expects.
    """
    front = yaml.safe_dump(
        {"name": name, "description": description},
        sort_keys=False,
        allow_unicode=True,
    )
    return f"---\n{front}---\n\n{body.strip()}\n"


def validate_skill_md(name: str, description: str, body: str) -> tuple[bool, str]:
    """Validate a candidate skill by round-tripping it through ``scan_skills``.

    We write the rendered ``SKILL.md`` into a temp dir laid out as
    ``<tmp>/<name>/SKILL.md`` and scan it. If it parses as a valid skill we
    return ``(True, "")``; otherwise ``(False, reason)``. This catches every
    contract violation the loader enforces (name/dir mismatch, bad frontmatter,
    oversized description) before anything touches the real catalog.
    """
    if not name or not _NAME_RE.match(name):
        return False, "skill name must be lowercase and hyphenated"
    if not description or not description.strip():
        return False, "description is required"
    if len(description) > 1024:
        return False, "description exceeds 1024 characters"
    if not body or not body.strip():
        return False, "skill body is empty"
    content = render_skill_md(name, description, body)
    tmp = Path(tempfile.mkdtemp(prefix="claudio-skill-validate-"))
    try:
        skill_dir = tmp / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        scan = scan_skills(tmp)
        skill = scan.skills.get(name)
        if skill is None:
            entry = next(
                (e for e in scan.entries if e.get("name") == name), None
            )
            reason = (entry or {}).get("error", "skill failed validation")
            return False, reason
        return True, ""
    except Exception as exc:  # noqa: BLE001 - validation failure is the answer
        return False, f"validation error: {exc}"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _backup_existing(target: Path) -> str | None:
    """Copy an existing skill folder to a timestamped backup outside the scan dir.

    Backups live under ``<backend>/data/skill-backups/`` — deliberately NOT
    inside the skills directory, because ``scan_skills`` reads every direct
    child folder and would report the backups as broken skills. Returns the
    backup path, or None if there was nothing to back up.
    """
    if not target.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_root = backend_base() / "data" / "skill-backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    backup_path = backup_root / f"{target.name}-{stamp}"
    shutil.copytree(target, backup_path)
    return str(backup_path)


async def promote_draft(
    db: Database,
    config: AppConfig,
    draft_id: int,
    actor: str = "operator",
) -> dict[str, Any]:
    """Validate and write a skill draft into the live skills directory.

    Raises ``ValueError`` if the draft is missing or fails validation, leaving
    the draft untouched (still pending) so the operator can see why. On
    success the draft is marked ``approved`` and the skill is live on the next
    run (the engine re-scans the skills dir per request).
    """
    draft = await db.get_skill_draft(draft_id)
    if draft is None:
        raise ValueError(f"skill draft {draft_id} not found")

    name = draft["skill_name"]
    description = draft["description"]
    body = draft["body"]

    ok, reason = validate_skill_md(name, description, body)
    if not ok:
        await db.update_skill_draft(
            draft_id, status="rejected", review_note=f"validation failed: {reason}"
        )
        await db.add_audit(
            action="skill_promotion_rejected_validation",
            command=name,
            detail=reason,
            actor=actor,
        )
        raise ValueError(f"skill draft failed validation: {reason}")

    skills_dir = resolve_skills_dir(config)
    target = skills_dir / name
    backup_path = _backup_existing(target)

    # Write atomically: stage in a temp dir on the same filesystem, then move
    # the folder into place so a half-written skill never appears in the scan.
    staging = Path(tempfile.mkdtemp(prefix=f"claudio-{name}-", dir=str(skills_dir)))
    try:
        staged_skill = staging / name
        staged_skill.mkdir(parents=True, exist_ok=True)
        (staged_skill / "SKILL.md").write_text(
            render_skill_md(name, description, body), encoding="utf-8"
        )
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(staged_skill), str(target))
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    await db.update_skill_draft(
        draft_id,
        status="approved",
        review_note="promoted to live skills directory",
    )
    # Mark the source learnings promoted too, so they stop showing as pending.
    for lid in draft.get("source_learning_ids") or []:
        try:
            await db.update_learning_status(int(lid), "promoted", promoted_skill=name)
        except (TypeError, ValueError):
            pass
    await db.add_audit(
        action="skill_promoted",
        command=name,
        detail=f"backup={backup_path or 'none'}",
        actor=actor,
    )
    return {
        "draft_id": draft_id,
        "skill_name": name,
        "path": str(target),
        "backup": backup_path,
    }


async def reject_draft(
    db: Database, draft_id: int, note: str = "", actor: str = "operator"
) -> dict[str, Any]:
    """Reject a pending draft without writing anything."""
    draft = await db.get_skill_draft(draft_id)
    if draft is None:
        raise ValueError(f"skill draft {draft_id} not found")
    await db.update_skill_draft(draft_id, status="rejected", review_note=note)
    await db.add_audit(
        action="skill_draft_rejected",
        command=draft.get("skill_name", ""),
        detail=note,
        actor=actor,
    )
    return {"draft_id": draft_id, "status": "rejected"}
