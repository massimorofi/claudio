"""Tests for benchmark/judge.py -- determinism, anonymization, score bounds.

Everything runs offline in a tmp_path fixture: no network, no real results tree.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import judge as J  # noqa: E402

BACKENDS = ["claude_code", "google_adk", "opencode"]


def make_run(
    root: Path,
    backend: str = "claude_code",
    task: str = "fix_slugify",
    trial: int = 1,
    *,
    run: dict | None = None,
    diff: str = "diff --git a/app/text.py b/app/text.py\n+def slugify(s):\n+    return s.lower()\n",
    verify: str = "1 passed in 0.01s\n",
    prompt: str = "Make slugify lowercase the input.",
) -> Path:
    """Create ``root/<backend>/<task>/trial_<k>/`` with the runner's artifacts."""
    run_dir = root / backend / task / "trial_{0}".format(trial)
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "backend": backend,
        "task_id": task,
        "trial": trial,
        "verification_passed": True,
        "verify_exit_code": 0,
        "engine_exit_code": 0,
        "timed_out": False,
        "diff_lines": 3,
        "changed_files": ["app/text.py"],
        "errors": {"command_errors": 0, "tool_errors": 0, "retries": 0, "timeouts": 0},
    }
    payload.update(run or {})
    (run_dir / "run.json").write_text(json.dumps(payload), "utf-8")
    if prompt is not None:
        (run_dir / "prompt.txt").write_text(prompt, "utf-8")
    if diff is not None:
        (run_dir / "diff.patch").write_text(diff, "utf-8")
    if verify is not None:
        (run_dir / "verify.txt").write_text(verify, "utf-8")
    return run_dir


# --------------------------------------------------------------------------- #
# evidence
# --------------------------------------------------------------------------- #


def test_evidence_packs_prompt_diff_verification_and_errors(tmp_path):
    make_run(tmp_path)
    ev = J.build_evidence(tmp_path / "claude_code" / "fix_slugify" / "trial_1", "A")
    assert isinstance(ev, str)
    assert "Make slugify lowercase the input." in ev
    assert "def slugify" in ev
    assert "1 passed in 0.01s" in ev
    for field in (
        "verification_passed:",
        "verify_exit_code:",
        "errors:",
        "files_changed:",
        "suspicious_paths:",
        "todo_markers:",
    ):
        assert field in ev


def test_diff_is_truncated_to_the_fixed_budget(tmp_path):
    make_run(tmp_path, diff="diff --git a/x b/x\n" + "+line\n" * 4000)
    ev = J.build_evidence(tmp_path / "claude_code" / "fix_slugify" / "trial_1", "A")
    assert "[truncated" in ev
    assert len(ev) < J.DIFF_BUDGET + 2000


def test_evidence_never_leaks_the_backend_name(tmp_path):
    make_run(tmp_path)
    ev = J.build_evidence(tmp_path / "claude_code" / "fix_slugify" / "trial_1", "A")
    assert "claude_code" not in ev
    assert "engine_label: A" in ev


def test_evidence_sha_is_stable_and_matches_hashlib(tmp_path):
    make_run(tmp_path)
    run_dir = tmp_path / "claude_code" / "fix_slugify" / "trial_1"
    ev1, ev2 = J.build_evidence(run_dir, "A"), J.build_evidence(run_dir, "A")
    assert ev1 == ev2
    assert J.evidence_sha(ev1) == J.evidence_sha(ev2)
    assert len(J.evidence_sha(ev1)) == 64


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #


def test_same_evidence_gives_same_scores(tmp_path):
    make_run(tmp_path)
    ev = J.build_evidence(tmp_path / "claude_code" / "fix_slugify" / "trial_1", "A")
    first, second = J.heuristic_scores(ev), J.heuristic_scores(ev)
    assert first == second
    assert first["scores"] == second["scores"]
    assert first["justifications"] == second["justifications"]


def test_rejudging_a_run_reproduces_identical_artifacts(tmp_path):
    run_dir = make_run(tmp_path)
    first = J.judge(run_dir)
    written = (run_dir / "judge.json").read_text("utf-8")
    second = J.judge(run_dir)
    assert first == second
    assert written == (run_dir / "judge.json").read_text("utf-8")
    assert first["evidence_sha"] == second["evidence_sha"]


def test_rejudging_the_whole_matrix_is_reproducible(tmp_path):
    for backend in BACKENDS:
        make_run(tmp_path, backend=backend)
    first = {p.name: payload for p, payload in J.judge_results(tmp_path)}
    second = {p.name: payload for p, payload in J.judge_results(tmp_path)}
    assert first == second


# --------------------------------------------------------------------------- #
# anonymization
# --------------------------------------------------------------------------- #


def test_anonymize_is_stable_across_calls_and_input_order():
    forward = J.anonymize(BACKENDS)
    assert J.anonymize(BACKENDS) == forward
    assert J.anonymize(list(reversed(BACKENDS))) == forward
    assert J.anonymize(["opencode", "claude_code", "google_adk"]) == forward
    assert sorted(forward.values()) == ["A", "B", "C"]
    assert len(set(forward.values())) == len(forward)


def test_anonymize_matches_the_documented_seeded_shuffle():
    order = sorted(BACKENDS)
    rng = random.Random("claudio-bench-2026")
    rng.shuffle(order)
    assert J.anonymize(BACKENDS) == {b: lbl for b, lbl in zip(order, "ABC")}


def test_judge_key_is_written_and_decodes_the_labels(tmp_path):
    for backend in BACKENDS:
        make_run(tmp_path, backend=backend)
    J.judge_results(tmp_path)
    key = json.loads((tmp_path / "judge_key.json").read_text("utf-8"))
    assert key["seed"] == "claudio-bench-2026"
    assert key["reverse"] == {label: backend for backend, label in key["mapping"].items()}
    for run_dir, payload in J.judge_results(tmp_path):
        assert key["reverse"][payload["backend_anon"]] == run_dir.parent.parent.name


# --------------------------------------------------------------------------- #
# score bounds and the verification anchor
# --------------------------------------------------------------------------- #

RUN_SHAPES = {
    "clean_pass": {},
    "failed": {"verification_passed": False, "verify_exit_code": 1},
    "timeout": {"timed_out": True, "verification_passed": False, "errors": {"timeouts": 1}},
    "noisy": {
        "errors": {"command_errors": 3, "tool_errors": 2, "retries": 4, "timeouts": 0},
        "changed_files": ["a.py"] + ["p{0}".format(i) for i in range(12)],
    },
    "stray_artifacts": {
        "changed_files": ["app/__pycache__/x.pyc", "app/__pycache__/y.pyc", "app/__pycache__/z.pyc"]
    },
    "huge_diff": {"diff_lines": 5000, "changed_files": ["a.py"] * 20},
}


@pytest.mark.parametrize("shape", sorted(RUN_SHAPES))
def test_scores_are_always_ints_in_1_to_5(tmp_path, shape):
    make_run(tmp_path, run=RUN_SHAPES[shape])
    payload = J.judge(tmp_path / "claude_code" / "fix_slugify" / "trial_1")
    assert set(payload["scores"]) == set(J.AXES)
    for axis, value in payload["scores"].items():
        assert isinstance(value, int) and not isinstance(value, bool), axis
        assert 1 <= value <= 5, (axis, value)
    assert payload["mode"] in ("llm", "heuristic")
    assert set(payload["justifications"]) == set(J.AXES)
    assert all(payload["justifications"].values())
    assert len(payload["evidence_sha"]) == 64


def fake_judge(tmp_path: Path, answer: str) -> str:
    """Path to a tiny judge command that echoes ``answer`` on stdout."""
    script = tmp_path / "fake_judge.py"
    script.write_text(
        "import sys\nsys.stdin.read()\nsys.stdout.write({0!r})\n".format(answer), "utf-8"
    )
    return "{0} {1}".format(sys.executable, script)


def test_broken_code_cannot_be_scored_as_working(tmp_path, monkeypatch):
    make_run(tmp_path, run={"verification_passed": False, "verify_exit_code": 1})
    run_dir = tmp_path / "claude_code" / "fix_slugify" / "trial_1"
    scores = J.heuristic_scores(J.build_evidence(run_dir, "A"))["scores"]
    assert scores["coding"] <= 2 and scores["quality"] <= 2
    # the same cap holds for an external judge that claims a perfect run
    lying = '{"quality": 5, "intelligence": 5, "coding": 5, "justifications": {}}'
    monkeypatch.setenv("BENCHMARK_JUDGE_CMD", fake_judge(tmp_path, lying))
    capped = J.judge(run_dir)
    assert capped["mode"] == "llm"
    assert capped["scores"]["coding"] <= 2 and capped["scores"]["quality"] <= 2


def test_scores_discriminate_clean_from_noisy_runs(tmp_path):
    clean = make_run(tmp_path, task="clean_task", run={"changed_files": ["app/text.py"]})
    noisy = make_run(
        tmp_path,
        task="noisy_task",
        run={
            "changed_files": ["app/text.py"] + ["app/__pycache__/p{0}.pyc".format(i) for i in range(4)],
            "errors": {"command_errors": 2, "tool_errors": 2, "retries": 3, "timeouts": 0},
        },
    )
    good = J.heuristic_scores(J.build_evidence(clean, "A"))["scores"]
    bad = J.heuristic_scores(J.build_evidence(noisy, "A"))["scores"]
    assert good == {"quality": 5, "intelligence": 5, "coding": 5}
    assert all(bad[axis] < good[axis] for axis in J.AXES)


def test_rubric_prompt_has_all_three_anchored_axes():
    rubric = J.rubric_prompt()
    assert rubric == J.rubric_prompt()
    for heading in ("Quality", "Intelligence", "Coding Ability"):
        assert heading in rubric
    for anchor in ("5 =", "1 ="):
        assert anchor in rubric
    assert "MUST be 1 or 2" in rubric


# --------------------------------------------------------------------------- #
# modes
# --------------------------------------------------------------------------- #


def test_llm_mode_is_used_when_the_command_works(tmp_path, monkeypatch):
    make_run(tmp_path)
    answer = json.dumps(
        {
            "quality": 4,
            "intelligence": 5,
            "coding": 4,
            "justifications": {"quality": "q", "intelligence": "i", "coding": "c"},
        }
    )
    monkeypatch.setenv("BENCHMARK_JUDGE_CMD", fake_judge(tmp_path, answer))
    payload = J.judge(tmp_path / "claude_code" / "fix_slugify" / "trial_1")
    assert payload["mode"] == "llm"
    assert payload["scores"] == {"quality": 4, "intelligence": 5, "coding": 4}
    assert payload["justifications"] == {"quality": "q", "intelligence": "i", "coding": "c"}


@pytest.mark.parametrize("cmd", ["false", "exit 3", "printf 'not json'", "printf '{\"x\": 99}'"])
def test_broken_judge_command_falls_back_to_the_heuristic(tmp_path, monkeypatch, cmd):
    make_run(tmp_path)
    monkeypatch.setenv("BENCHMARK_JUDGE_CMD", cmd)
    run_dir = tmp_path / "claude_code" / "fix_slugify" / "trial_1"
    payload = J.judge(run_dir)
    assert payload["mode"] == "heuristic"
    assert payload["scores"] == J.heuristic_scores(J.build_evidence(run_dir, "A"))["scores"]


def test_cli_writes_judge_json_next_to_every_run_json(tmp_path, capsys):
    for backend in BACKENDS:
        make_run(tmp_path, backend=backend)
    assert J.main(["--results", str(tmp_path)]) == 0
    assert len(list(tmp_path.glob("*/*/trial_*/judge.json"))) == len(BACKENDS)
    assert (tmp_path / "judge_key.json").is_file()
    assert "SUMMARY judged={0}".format(len(BACKENDS)) in capsys.readouterr().out


def test_cli_reports_missing_results(tmp_path, capsys):
    assert J.main(["--results", str(tmp_path / "nope")]) == 1
