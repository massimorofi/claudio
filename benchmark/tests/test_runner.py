"""Tests for benchmark/runner.py.

Hermetic: everything happens under ``tmp_path``, no network, one task and one
backend per test. They prove the two properties the runner exists for:

* sandbox isolation -- the engine only ever edits a throwaway directory outside
  the repo, so the source tree is byte-for-byte untouched;
* timeout handling -- a run that outlives ``max_seconds`` is killed and
  reported as ``timed_out`` instead of hanging the suite.
"""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BENCH))
import runner  # noqa: E402
import stub_engine  # noqa: E402

TASK = {
    "id": "inventory-qa",
    "category": "codebase_qa",
    "difficulty": "easy",
    "prompt": "Write findings.txt as specified in QUESTION.md.",
    "fixture_dir": "fixture",
    "setup": "",
    "verification": (
        'test -f findings.txt && grep -qx "dead_code: titleize" findings.txt'
        ' && grep -qx "util_functions: 4" findings.txt'
    ),
    "seed_must_fail": True,
    "max_seconds": 30,
}


def _git(repo, *args):
    return subprocess.run(["git", "-C", str(repo)] + list(args),
                          capture_output=True, text=True)


def _make_repo(tmp_path, with_capability=True, binary=None, task=None):
    """A stand-in for the checkout: a git repo holding one fixture task."""
    task = task or TASK
    repo = tmp_path / "repo"
    (repo / "benchmark" / "fixture").mkdir(parents=True)
    for rel, text in stub_engine.SEEDS[TASK["id"]].items():
        target = repo / "benchmark" / "fixture" / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    (repo / "keep.txt").write_text("untouched\n")
    tasks = {"version": 1, "locked": True, "tasks": [task]}
    (repo / "tasks.json").write_text(json.dumps(tasks, indent=2))
    cap = {"backends": {"claude_code": {"available": False, "binary": "claude"}}}
    if with_capability and binary is not None:
        cap = {"backends": {"claude_code": {"available": True, "binary": binary}}}
    (repo / "capability.json").write_text(json.dumps(cap, indent=2))
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@local")
    _git(repo, "config", "user.name", "test")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    return repo


def _tree_hash(repo):
    """Stable digest of every tracked file in the repo."""
    digest = hashlib.sha256()
    listing = _git(repo, "ls-files").stdout.splitlines()
    for name in sorted(listing):
        digest.update(name.encode())
        digest.update((repo / name).read_bytes())
    return digest.hexdigest()


def test_stub_run_is_sandboxed_and_repo_untouched(tmp_path):
    repo = _make_repo(tmp_path)
    out = tmp_path / "results"
    before = _tree_hash(repo)

    code = runner.main([
        "--repo-root", str(repo),
        "--tasks", str(repo / "tasks.json"),
        "--capability", str(repo / "capability.json"),
        "--backends", "claude_code",
        "--trials", "1",
        "--out", str(out),
        "--no-commit",
    ])
    assert code == 0

    box = out / "claude_code" / TASK["id"] / "trial_1"
    for name in ("run.json", "session.jsonl", "stdout.txt", "stderr.txt",
                 "verification.txt", "diff.patch"):
        assert (box / name).is_file(), "missing artifact " + name

    run = json.loads((box / "run.json").read_text())
    assert runner.RUN_KEYS <= set(run)
    assert run["backend"] == "claude_code"
    assert run["task_id"] == TASK["id"]
    assert run["trial"] == 1
    assert run["engine_mode"] == "stub"
    assert run["exit_status"] == "ok"
    assert run["timed_out"] is False
    assert run["verification_passed"] is True
    assert set(run["tokens"]) >= {"input", "output", "cache_read", "total"}
    assert run["tokens"]["total"] == (run["tokens"]["input"]
                                      + run["tokens"]["output"]
                                      + run["tokens"]["cache_read"]
                                      + run["tokens"]["cache_write"])
    assert set(run["errors"]) == {"command_errors", "tool_errors",
                                  "retries", "timeouts"}
    # the canned solution shows up as a real diff
    assert (box / "diff.patch").read_text().startswith("diff --git")

    # --- sandbox isolation ------------------------------------------------
    sandbox = Path(run["sandbox"])
    assert repo.resolve() not in sandbox.parents and sandbox != repo.resolve()
    assert not sandbox.exists(), "sandbox should be cleaned up"
    # the engine wrote findings.txt in the sandbox, never in the repo
    assert not (repo / "benchmark" / "fixture" / "findings.txt").exists()
    # repo tree is byte-identical and git-clean
    assert _tree_hash(repo) == before
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""


def test_real_engine_timeout_is_enforced(tmp_path):
    slow = tmp_path / "slow_cli.sh"
    slow.write_text("#!/bin/sh\nsleep 30\n")
    slow.chmod(0o755)

    repo = _make_repo(tmp_path, binary=str(slow),
                      task=dict(TASK, max_seconds=1))
    out = tmp_path / "results"

    code = runner.main([
        "--repo-root", str(repo),
        "--tasks", str(repo / "tasks.json"),
        "--capability", str(repo / "capability.json"),
        "--backends", "claude_code",
        "--trials", "1",
        "--out", str(out),
        "--no-commit",
    ])
    assert code == 0

    box = out / "claude_code" / TASK["id"] / "trial_1"
    run = json.loads((box / "run.json").read_text())
    assert run["engine_mode"] == "real"
    assert run["timed_out"] is True
    assert run["exit_status"] == "timeout"
    assert run["errors"]["timeouts"] == 1
    assert run["verification_passed"] is False
    assert run["duration_s"] < 20, "the run should have been killed at ~1s"
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""


def test_make_sandbox_refuses_to_live_inside_the_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with pytest.raises(RuntimeError):
        runner.make_sandbox(repo, "claude_code", "inventory-qa", 1,
                            parent=str(repo))
    assert list(repo.iterdir()) == [], "the refused sandbox must be removed"
