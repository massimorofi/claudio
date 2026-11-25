"""Tests for benchmark/runner.py -- offline, stdlib-only, no network.

Run with:  PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_runner.py -q

They prove the two properties the benchmark contract depends on:

* sandbox isolation -- every run happens in a ``tempfile.mkdtemp()`` directory
  *outside* the repository, so the worktree / gate tree stays clean;
* timeout handling -- a run that exceeds its task timeout is killed, recorded as
  ``timed_out`` / ``exit_status="timeout"`` and does not hang the suite.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

BENCH = Path(__file__).resolve().parents[1]
if str(BENCH) not in sys.path:
    sys.path.insert(0, str(BENCH))

import runner  # noqa: E402  (path set up above)

REQUIRED_KEYS = {
    "backend",
    "task_id",
    "trial",
    "engine_mode",
    "duration_s",
    "exit_status",
    "timed_out",
    "tokens",
    "errors",
    "verification_passed",
}

TASK = {
    "id": "t_iso",
    "prompt": "Fix sum_values so the tests pass.",
    "target": "app/calc.py",
    "verify": "python3 -m unittest -q tests.test_calc",
    "timeout_s": 20,
    "seed_files": {
        "app/__init__.py": "",
        "app/calc.py": "def sum_values(values):\n    return sum(values) - 1\n",
        "tests/__init__.py": "",
        "tests/test_calc.py": (
            "import unittest\nfrom app.calc import sum_values\n\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_basic(self):\n        self.assertEqual(sum_values([1, 2, 3]), 6)\n"
        ),
    },
    "solution_files": {"app/calc.py": "def sum_values(values):\n    return sum(values)\n"},
}


def _git(args: list[str], cwd: Path) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), env=runner.GIT_ENV, capture_output=True, text=True
    ).stdout


@pytest.fixture()
def fake_repo(tmp_path: Path) -> Path:
    """A throwaway git repo standing in for the project worktree."""
    repo = tmp_path / "repo"
    (repo / "benchmark").mkdir(parents=True)
    _git(["init", "-q"], repo)
    (repo / "README.md").write_text("fake worktree\n", encoding="utf-8")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "seed"], repo)
    return repo


def _write_tasks(tmp_path: Path, task: dict) -> Path:
    path = tmp_path / "tasks.json"
    path.write_text(json.dumps({"tasks": [task]}), encoding="utf-8")
    return path


def _run(fake_repo: Path, tmp_path: Path, task: dict) -> tuple[int, Path]:
    out = fake_repo / "benchmark" / "results"
    rc = runner.main(
        [
            "--tasks", str(_write_tasks(tmp_path, task)),
            "--capability", str(tmp_path / "missing-capability.json"),
            "--backends", "claude_code",
            "--trials", "1",
            "--out", str(out),
        ]
    )
    return rc, out


def test_sandbox_is_outside_repo_and_tree_stays_clean(fake_repo, tmp_path, capsys):
    """1 task x 1 backend: artifacts are written, the worktree is untouched."""
    real_before = _git(["status", "--porcelain"], runner.REPO)
    before = _git(["status", "--porcelain"], fake_repo)
    rc, out = _run(fake_repo, tmp_path, TASK)
    assert rc == 0

    run_dir = out / "claude_code" / "t_iso" / "trial_1"
    record = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))

    assert REQUIRED_KEYS <= set(record)
    assert record["backend"] == "claude_code"
    assert record["task_id"] == "t_iso"
    assert record["trial"] == 1
    assert record["engine_mode"] == "stub"          # no engine binary on a gate
    assert record["exit_status"] == "ok"
    assert record["timed_out"] is False
    assert record["verification_passed"] is True     # canned solution applied
    assert {"input", "output", "total"} <= set(record["tokens"])
    assert record["tokens"]["total"] > 0
    assert {"command_errors", "tool_errors", "retries", "timeouts"} <= set(record["errors"])

    for name in ("stdout.txt", "stderr.txt", "session.jsonl", "diff.patch", "run.json"):
        assert (run_dir / name).is_file(), name
    assert "def sum_values" in (run_dir / "diff.patch").read_text(encoding="utf-8")
    assert '"type":"usage"' in (run_dir / "session.jsonl").read_text(encoding="utf-8")
    assert (run_dir / "stdout.txt").read_text(encoding="utf-8").strip()

    # a per-run summary line was printed
    printed = capsys.readouterr().out
    assert any("backend=claude_code" in line and "task=t_iso" in line for line in printed.splitlines())

    # --- sandbox isolation ------------------------------------------------
    # no sandbox directory was created anywhere inside the repo tree
    assert not [p for p in fake_repo.rglob("*") if p.name.startswith("claudio-bench-")]
    # the only repo change is the results directory we explicitly asked for
    after = _git(["status", "--porcelain", "-uall"], fake_repo).splitlines()
    assert before == ""
    assert after and all(line.split()[-1].startswith("benchmark/results") for line in after)
    # and the real worktree this test file lives in was not modified either
    assert _git(["status", "--porcelain"], runner.REPO) == real_before


def test_sandbox_is_created_outside_repo(fake_repo):
    sandbox = runner.make_sandbox("claude_code", "t_iso", 1)
    try:
        assert sandbox.is_dir()
        assert runner.REPO not in sandbox.parents and sandbox != runner.REPO
        assert sandbox.resolve() != fake_repo.resolve()
    finally:
        import shutil

        shutil.rmtree(sandbox, ignore_errors=True)


def test_timeout_kills_the_engine_and_is_recorded(fake_repo, tmp_path):
    slow = dict(TASK)
    slow["id"] = "t_slow"
    slow["timeout_s"] = 1
    slow["stub"] = {"delay_s": 30}

    rc, out = _run(fake_repo, tmp_path, slow)
    assert rc == 0

    record = json.loads((out / "claude_code" / "t_slow" / "trial_1" / "run.json").read_text(encoding="utf-8"))
    assert record["timed_out"] is True
    assert record["exit_status"] == "timeout"
    assert record["errors"]["timeouts"] == 1
    assert record["verification_passed"] is False
    assert record["duration_s"] < 10          # killed at ~1s, not after the 30s delay
    assert record["duration_s"] >= 0.9


def test_stub_run_is_deterministic(fake_repo, tmp_path):
    _, out = _run(fake_repo, tmp_path, TASK)
    first = json.loads((out / "claude_code" / "t_iso" / "trial_1" / "run.json").read_text())
    session_a = (out / "claude_code" / "t_iso" / "trial_1" / "session.jsonl").read_text()

    _, out2 = _run(fake_repo, tmp_path, TASK)
    second = json.loads((out2 / "claude_code" / "t_iso" / "trial_1" / "run.json").read_text())
    session_b = (out2 / "claude_code" / "t_iso" / "trial_1" / "session.jsonl").read_text()

    assert first["tokens"] == second["tokens"]
    assert first["errors"] == second["errors"]
    assert session_a == session_b
