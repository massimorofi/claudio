"""Validation for ``benchmark/tasks.json``.

Checks three things:

1. the manifest schema (keys, types, kebab-case ids, rubric size, ...),
2. coverage (all four categories present, at least six tasks, at least four
   tasks flagged ``seed_must_fail``),
3. that every ``seed_must_fail`` task really fails on its untouched fixture: the
   fixture is copied into ``tmp_path`` and the verification command must exit
   non-zero there.

Run with: ``PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_tasks_manifest.py -q``
"""

from __future__ import annotations

import json
import os
import py_compile
import re
import shutil
import subprocess
from pathlib import Path

import pytest

BENCH_DIR = Path(__file__).resolve().parent.parent
TASKS_PATH = BENCH_DIR / "tasks.json"
FIXTURES_DIR = BENCH_DIR / "fixtures"

CATEGORIES = {"bug_fix", "feature", "refactor_debug", "codebase_qa"}
DIFFICULTIES = {"easy", "medium", "hard"}
KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
REQUIRED_KEYS = (
    "id",
    "category",
    "difficulty",
    "prompt",
    "fixture_dir",
    "setup",
    "verification",
    "seed_must_fail",
    "max_seconds",
    "rubric",
)
# The sandbox has no network and no repo dependencies; verifications must be
# plain python3 / pytest / git / grep one-liners with relative paths only.
BANNED_FRAGMENTS = ("http://", "https://", "curl", "wget", "pip install", "git clone")
ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:/|~/)(?=[^\s])")
IGNORED = shutil.ignore_patterns("__pycache__", "*.pyc", "*.pyo", ".pytest_cache")


def _load_tasks():
    payload = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        assert payload.get("tasks"), "manifest dict must carry a non-empty 'tasks' list"
        return payload["tasks"]
    return payload


TASKS = _load_tasks()
BY_ID = {t["id"]: t for t in TASKS if isinstance(t, dict) and "id" in t}
SEED_IDS = [t["id"] for t in TASKS if isinstance(t, dict) and t.get("seed_must_fail")]


def test_manifest_is_present_and_non_empty():
    assert TASKS_PATH.is_file(), f"missing {TASKS_PATH}"
    assert len(TASKS) >= 6, f"need >=6 tasks, found {len(TASKS)}"


def test_manifest_is_marked_locked():
    payload = json.loads(TASKS_PATH.read_text(encoding="utf-8"))
    assert isinstance(payload, dict), "manifest must be an object with a tasks list"
    assert payload.get("locked") is True, "manifest must be marked locked"
    assert isinstance(payload.get("version"), int) and payload["version"] >= 1


def test_ids_are_kebab_case_and_unique():
    seen = set()
    for task in TASKS:
        task_id = task["id"]
        assert KEBAB.match(task_id), f"id {task_id!r} is not kebab-case"
        assert task_id not in seen, f"duplicate id {task_id!r}"
        seen.add(task_id)
    assert len(seen) == len(TASKS)


def test_all_four_categories_are_covered():
    found = {task["category"] for task in TASKS}
    assert found <= CATEGORIES, f"unknown categories: {sorted(found - CATEGORIES)}"
    missing = CATEGORIES - found
    assert not missing, f"categories not covered: {sorted(missing)}"


def test_at_least_four_seed_must_fail_tasks():
    assert len(SEED_IDS) >= 4, f"only {len(SEED_IDS)} tasks flagged seed_must_fail"


@pytest.mark.parametrize("task_id", sorted(BY_ID))
def test_task_schema(task_id):
    task = BY_ID[task_id]
    for key in REQUIRED_KEYS:
        assert key in task, f"{task_id}: missing key {key!r}"
    assert task["category"] in CATEGORIES, f"{task_id}: bad category"
    assert task["difficulty"] in DIFFICULTIES, f"{task_id}: bad difficulty"
    assert isinstance(task["prompt"], str) and len(task["prompt"].strip()) >= 40, (
        f"{task_id}: prompt must be a substantive string"
    )
    assert task["fixture_dir"] == f"benchmark/fixtures/{task_id}", (
        f"{task_id}: fixture_dir must be benchmark/fixtures/<id>"
    )
    assert isinstance(task["setup"], str), f"{task_id}: setup must be a string ('' allowed)"
    assert isinstance(task["verification"], str) and task["verification"].strip(), (
        f"{task_id}: verification must be a non-empty shell command"
    )
    assert isinstance(task["seed_must_fail"], bool), f"{task_id}: seed_must_fail must be bool"
    assert isinstance(task["max_seconds"], int) and 30 <= task["max_seconds"] <= 3600, (
        f"{task_id}: max_seconds must be an int in [30, 3600]"
    )
    rubric = task["rubric"]
    assert isinstance(rubric, list) and 1 <= len(rubric) <= 5, f"{task_id}: rubric size"
    assert all(isinstance(a, str) and a.strip() for a in rubric), f"{task_id}: rubric anchors"


@pytest.mark.parametrize("task_id", sorted(BY_ID))
def test_commands_are_relative_and_offline(task_id):
    task = BY_ID[task_id]
    for field in ("verification", "setup"):
        command = task[field]
        if not command:
            continue
        for banned in BANNED_FRAGMENTS:
            assert banned not in command, f"{task_id}: {field} uses {banned!r}"
        assert not ABSOLUTE_PATH.search(command), (
            f"{task_id}: {field} must not reference absolute paths: {command}"
        )


@pytest.mark.parametrize("task_id", sorted(BY_ID))
def test_fixture_dir_has_two_to_four_python_or_text_files(task_id, tmp_path):
    fixture = BY_ID[task_id]["fixture_dir"]
    root = BENCH_DIR.parent / fixture
    assert root.is_dir(), f"{task_id}: fixture dir {fixture} does not exist"
    files = [p for p in root.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    assert 2 <= len(files) <= 4, f"{task_id}: expected 2-4 fixture files, found {len(files)}"
    kinds = {p.suffix for p in files}
    assert kinds & {".py", ".md", ".txt"}, f"{task_id}: fixture files look wrong: {kinds}"
    for path in files:
        if path.suffix == ".py":
            py_compile.compile(
                str(path), cfile=str(tmp_path / (path.stem + ".pyc")), doraise=True
            )


def _run_in_copy(command: str, workdir: Path) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONPATH"] = str(workdir)
    env["COLUMNS"] = "120"
    return subprocess.run(
        command,
        shell=True,
        cwd=str(workdir),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=240,
    )


@pytest.mark.parametrize("task_id", sorted(SEED_IDS))
def test_verification_fails_on_untouched_seed(task_id, tmp_path):
    """Prove the task is real: the seed fixture must NOT satisfy verification."""
    task = BY_ID[task_id]
    source = BENCH_DIR.parent / task["fixture_dir"]
    workdir = tmp_path / "sandbox"
    shutil.copytree(str(source), str(workdir), ignore=IGNORED)
    assert any(workdir.iterdir()), f"{task_id}: fixture copied nothing"
    result = _run_in_copy(task["verification"], workdir)
    assert result.returncode != 0, (
        f"{task_id}: verification passed on the untouched seed "
        f"(exit {result.returncode}) - the task is trivially solved.\n"
        f"command: {task['verification']}\n"
        f"output:\n{result.stdout[-2000:]}"
    )
