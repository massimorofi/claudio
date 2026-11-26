"""Tests for the blind benchmark judge (stdlib-only, offline).

    PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_judge.py -q
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
if str(BENCH) not in sys.path:  # lets the file run without PYTHONPATH too
    sys.path.insert(0, str(BENCH))

import judge as J  # noqa: E402

AXES = ("quality", "intelligence", "coding")


# --------------------------------------------------------------------------- #
# fixtures / helpers
# --------------------------------------------------------------------------- #


def make_run(
    root: Path,
    backend: str = "claude_code",
    task: str = "fix_slugify",
    trial: int = 1,
    *,
    verification_passed: bool = True,
    diff: str | None = None,
    verify: str | None = None,
    errors: dict | None = None,
    changed_files: list[str] | None = None,
    timed_out: bool = False,
) -> Path:
    """Materialise a minimal but realistic ``<backend>/<task>/trial_<k>`` run dir."""
    run_dir = root / backend / task / "trial_{0}".format(trial)
    run_dir.mkdir(parents=True, exist_ok=True)
    diff_text = (
        diff
        if diff is not None
        else (
            "--- a/app/text.py\n+++ b/app/text.py\n"
            "@@ -1,2 +1,2 @@\n-def slugify(s):\n-    return s\n"
            "+def slugify(s):\n+    return '-'.join(s.lower().split())\n"
        )
    )
    (run_dir / "prompt.txt").write_text("slugify() mishandles punctuation. Make tests pass.\n")
    (run_dir / "diff.patch").write_text(diff_text)
    (run_dir / "verify.txt").write_text(
        verify
        if verify is not None
        else "command: python3 -m unittest discover -s tests -t . -q\nexit_status: 0\n\nOK\n"
    )
    (run_dir / "stdout.txt").write_text("")
    (run_dir / "stderr.txt").write_text("")
    (run_dir / "session.jsonl").write_text("")
    (run_dir / "run.json").write_text(
        json.dumps(
            {
                "backend": backend,
                "task_id": task,
                "trial": trial,
                "engine_mode": "stub",
                "exit_status": "timeout" if timed_out else "ok",
                "timed_out": timed_out,
                "engine_exit_code": 0,
                "verify_exit_code": 0 if verification_passed else 1,
                "verification_passed": verification_passed,
                "diff_lines": len(diff_text.splitlines()),
                "changed_files": changed_files
                if changed_files is not None
                else ["app/text.py"],
                "errors": errors
                or {"command_errors": 0, "tool_errors": 0, "retries": 0, "timeouts": 0},
                "tokens": {"input": 10, "output": 5, "cache_read": 0, "cache_write": 0, "total": 15},
            },
            indent=2,
        )
    )
    return run_dir


@pytest.fixture
def results_root(tmp_path: Path) -> Path:
    """A three-backend results tree with one trial each."""
    root = tmp_path / "results"
    for backend in ("claude_code", "google_adk", "opencode"):
        make_run(root, backend)
    return root


# --------------------------------------------------------------------------- #
# evidence
# --------------------------------------------------------------------------- #


def test_build_evidence_packs_all_four_sections(tmp_path):
    run_dir = make_run(tmp_path)
    ev = J.build_evidence(run_dir, label="Engine A")
    assert isinstance(ev, str)
    assert "TASK PROMPT" in ev and "DIFF.PATCH" in ev
    assert "VERIFICATION OUTPUT" in ev and "ERROR SUMMARY" in ev
    assert "slugify() mishandles punctuation" in ev
    assert "verification_passed: yes" in ev


def test_build_evidence_is_anonymized(tmp_path):
    run_dir = make_run(tmp_path, backend="google_adk")
    ev = J.build_evidence(run_dir, label="Engine B")
    assert "google_adk" not in ev
    assert "Engine B" in ev


def test_build_evidence_respects_diff_budget(tmp_path):
    huge = "+line\n" * 5000
    run_dir = make_run(tmp_path, diff=huge)
    ev = J.build_evidence(run_dir, label="Engine A")
    assert "truncated" in ev
    assert len(ev) < len(huge) + 2000


def test_evidence_sha_is_stable_and_64_hex(tmp_path):
    run_dir = make_run(tmp_path)
    a = J.build_evidence(run_dir, label="Engine A")
    b = J.build_evidence(run_dir, label="Engine A")
    assert a == b
    assert re.fullmatch(r"[0-9a-f]{64}", a.sha256)
    assert a.sha256 == J.evidence_sha(str(b))
    # a different run must hash differently
    other = J.build_evidence(make_run(tmp_path, task="fix_sum_list"), label="Engine A")
    assert other.sha256 != a.sha256


# --------------------------------------------------------------------------- #
# anonymization
# --------------------------------------------------------------------------- #


def test_anonymize_is_stable_across_calls_and_orders():
    backends = ["claude_code", "google_adk", "opencode"]
    first = J.anonymize(backends)
    assert first == J.anonymize(backends)
    assert first == J.anonymize(list(reversed(backends)))
    assert first == J.anonymize(tuple(sorted(backends, reverse=True)))


def test_anonymize_gives_distinct_abc_labels():
    mapping = J.anonymize(["claude_code", "google_adk", "opencode"])
    assert sorted(mapping.values()) == ["A", "B", "C"]
    assert len(mapping) == 3


def test_anonymize_uses_the_documented_seed():
    import random

    order = sorted(["claude_code", "google_adk", "opencode"])
    random.Random(J.ANON_SEED).shuffle(order)
    assert J.anonymize(["opencode", "claude_code", "google_adk"]) == {
        order[0]: "A",
        order[1]: "B",
        order[2]: "C",
    }


def test_judge_key_roundtrip_is_auditable(tmp_path, results_root):
    mapping = J.anonymize(J.discover_backends(results_root))
    key_path = J.write_judge_key(results_root, mapping)
    assert key_path.is_file()
    payload = json.loads(key_path.read_text())
    assert payload["seed"] == "claudio-bench-2026"
    assert payload["mapping"] == J.invert_anonymization(mapping)
    assert J.read_judge_key(results_root) == mapping


def test_judge_key_written_by_cli(tmp_path, results_root):
    out = subprocess.run(
        [sys.executable, str(BENCH / "judge.py"), "--results", str(results_root), "--quiet"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert (results_root / "judge_key.json").is_file()


# --------------------------------------------------------------------------- #
# determinism
# --------------------------------------------------------------------------- #


def test_heuristic_scores_are_deterministic(tmp_path):
    ev = J.build_evidence(make_run(tmp_path), label="Engine A")
    first = J.heuristic_scores(ev)
    for _ in range(5):
        assert J.heuristic_scores(ev) == first


def test_same_evidence_gives_same_scores_across_dirs(tmp_path):
    a = make_run(tmp_path, "claude_code", "fix_slugify")
    b = make_run(tmp_path, "google_adk", "fix_slugify")
    ev_a = J.build_evidence(a, label="Engine A")
    ev_b = J.build_evidence(b, label="Engine A")
    assert ev_a == ev_b  # identical inputs -> identical evidence
    assert J.heuristic_scores(ev_a) == J.heuristic_scores(ev_b)


def test_full_run_is_reproducible(tmp_path, results_root):
    def snapshot():
        return {
            str(p.relative_to(results_root)): p.read_text()
            for p in sorted(results_root.glob("*/*/trial_*/judge.json"))
        }

    J.judge_results(results_root)
    snap1 = snapshot()
    J.judge_results(results_root)
    assert snapshot() == snap1


# --------------------------------------------------------------------------- #
# score bounds + anchoring
# --------------------------------------------------------------------------- #

_VARIANTS = {
    "clean": {},
    "failing": {"verification_passed": False},
    "timeout": {"verification_passed": False, "timed_out": True},
    "noisy": {
        "errors": {"command_errors": 3, "tool_errors": 4, "retries": 5, "timeouts": 0},
        "changed_files": ["a.py", "b.py", "c.py", "d.py", "e.py", "f.py", "g.py", "h.py", "i.py"],
    },
    "markers": {"diff": "+def f():\n+    # TODO finish this\n+    raise NotImplementedError\n"},
    "stray_artifacts": {"changed_files": ["app/x.py", "__pycache__/x.pyc", "y.pyc"]},
    "huge_diff": {"diff": "+x\n" * 2000},
    "empty_diff": {"diff": ""},
}


@pytest.mark.parametrize("name", sorted(_VARIANTS))
def test_scores_are_ints_within_1_and_5(tmp_path, name):
    run_dir = make_run(tmp_path, **_VARIANTS[name])
    record = J.judge(run_dir, results_root=tmp_path, mapping={"claude_code": "A"}, write=False)
    assert set(record["scores"]) == set(AXES)
    for axis in AXES:
        value = record["scores"][axis]
        assert isinstance(value, int) and not isinstance(value, bool)
        assert 1 <= value <= 5


@pytest.mark.parametrize("name", sorted(_VARIANTS))
def test_judge_json_schema(tmp_path, name):
    run_dir = make_run(tmp_path, **_VARIANTS[name])
    record = J.judge(run_dir, results_root=tmp_path, mapping={"claude_code": "A"})
    assert set(record) == {"backend_anon", "mode", "scores", "justifications", "evidence_sha"}
    assert record["backend_anon"] == "A"
    assert record["mode"] in ("llm", "heuristic")
    assert set(record["justifications"]) == set(AXES)
    assert all(isinstance(v, str) and v for v in record["justifications"].values())
    assert re.fullmatch(r"[0-9a-f]{64}", record["evidence_sha"])
    on_disk = json.loads((run_dir / "judge.json").read_text())
    assert on_disk == record


def test_broken_code_cannot_be_scored_as_working(tmp_path):
    """The verification anchor: a failed pytest run caps Coding/Quality at 2."""
    run_dir = make_run(
        tmp_path,
        verification_passed=False,
        diff="+def slugify(s):\n+    return s.strip()\n",
        verify="FAILED tests/test_text.py::test_slugify\n\n1 failed, 0 passed\n",
    )
    ev = J.build_evidence(run_dir, label="Engine A")
    scores = J.heuristic_scores(ev)["scores"]
    assert scores["coding"] <= 2 and scores["quality"] <= 2

    # even a rogue external judge cannot promote broken code past the anchor
    anchored = J.anchor_to_verification({"quality": 5, "intelligence": 5, "coding": 5}, J.parse_evidence(ev))
    assert anchored["coding"] <= 2 and anchored["quality"] <= 2


def test_passing_run_scores_higher_than_failing_one(tmp_path):
    good = J.heuristic_scores(J.build_evidence(make_run(tmp_path, "claude_code"), "Engine A"))
    bad = J.heuristic_scores(
        J.build_evidence(make_run(tmp_path, "google_adk", verification_passed=False), "Engine A")
    )
    assert good["scores"]["coding"] > bad["scores"]["coding"]


# --------------------------------------------------------------------------- #
# scope discipline
# --------------------------------------------------------------------------- #


def test_scope_penalty_is_monotonic():
    assert J.scope_penalty(0) == 0
    assert J.scope_penalty(1) == 1
    assert J.scope_penalty(2) == 1
    assert J.scope_penalty(3) == 2
    assert J.scope_penalty(0, n_files=9) == 1
    assert J.scope_penalty(3, n_files=9) == 3


def test_stray_artifacts_score_below_a_clean_run(tmp_path):
    """Scope discipline is a real signal: committed junk must cost points."""
    clean = J.heuristic_scores(
        J.build_evidence(make_run(tmp_path, "claude_code", "fix_slugify"), "Engine A")
    )
    sloppy = J.heuristic_scores(
        J.build_evidence(
            make_run(
                tmp_path,
                "google_adk",
                "fix_slugify",
                changed_files=[
                    "app/text.py",
                    "app/__pycache__/text.cpython-312.pyc",
                    "tests/__pycache__/test_text.cpython-312.pyc",
                    "leftover.pyc",
                ],
            ),
            "Engine A",
        )
    )
    for axis in AXES:
        assert sloppy["scores"][axis] <= clean["scores"][axis]
    assert sloppy["scores"]["coding"] < clean["scores"]["coding"]
    assert "scope discipline" in sloppy["justifications"]["intelligence"]


def test_scope_penalty_never_breaks_bounds(tmp_path):
    run_dir = make_run(
        tmp_path,
        changed_files=["a.pyc"] * 5 + ["b.log"] * 5 + ["c.py"],
    )
    record = J.judge(run_dir, results_root=tmp_path, mapping={"claude_code": "A"}, write=False)
    assert all(1 <= v <= 5 for v in record["scores"].values())


# --------------------------------------------------------------------------- #
# rubric
# --------------------------------------------------------------------------- #


def test_rubric_is_fixed_and_anchored_on_verification():
    first, second = J.rubric_prompt(), J.rubric_prompt()
    assert first == second
    for axis in ("Quality", "Intelligence", "Coding Ability"):
        assert axis in first
    assert "1-5" in first
    assert "verification" in first.lower()
    assert "MUST be 1 or 2" in first


# --------------------------------------------------------------------------- #
# modes
# --------------------------------------------------------------------------- #


def test_default_mode_is_heuristic(tmp_path, results_root, monkeypatch):
    monkeypatch.delenv("BENCHMARK_JUDGE_CMD", raising=False)
    record = J.judge(results_root / "claude_code" / "fix_slugify" / "trial_1", results_root)
    assert record["mode"] == "heuristic"


def test_llm_mode_uses_external_command(tmp_path, results_root, monkeypatch):
    fake = tmp_path / "fake_judge.py"
    fake.write_text(
        "import json,sys\n"
        "d=json.load(sys.stdin)\n"
        "assert 'BENCHMARK EVIDENCE' in d['evidence']\n"
        "assert 'rubric' in d\n"
        "print(json.dumps({'quality':4,'intelligence':3,'coding':5,"
        "'justifications':{'quality':'q','intelligence':'i','coding':'c'}}))\n"
    )
    monkeypatch.setenv("BENCHMARK_JUDGE_CMD", "{0} {1}".format(sys.executable, fake))
    record = J.judge(results_root / "claude_code" / "fix_slugify" / "trial_1", results_root)
    assert record["mode"] == "llm"
    assert record["scores"] == {"quality": 4, "intelligence": 3, "coding": 5}
    assert record["justifications"] == {"quality": "q", "intelligence": "i", "coding": "c"}


def test_broken_external_command_falls_back_to_heuristic(tmp_path, results_root, monkeypatch):
    monkeypatch.setenv("BENCHMARK_JUDGE_CMD", "exit 3")
    record = J.judge(results_root / "claude_code" / "fix_slugify" / "trial_1", results_root)
    assert record["mode"] == "heuristic"
    assert all(1 <= v <= 5 for v in record["scores"].values())


def test_nonsense_external_output_falls_back(tmp_path, results_root, monkeypatch):
    monkeypatch.setenv("BENCHMARK_JUDGE_CMD", "echo 'not json at all'")
    record = J.judge(results_root / "opencode" / "fix_slugify" / "trial_1", results_root)
    assert record["mode"] == "heuristic"


def test_out_of_range_external_scores_are_clamped(tmp_path, results_root, monkeypatch):
    monkeypatch.setenv(
        "BENCHMARK_JUDGE_CMD",
        "echo '{\"quality\": 99, \"intelligence\": -4, \"coding\": 3.6}'",
    )
    record = J.judge(results_root / "google_adk" / "fix_slugify" / "trial_1", results_root)
    assert record["mode"] == "llm"
    assert record["scores"] == {"quality": 5, "intelligence": 1, "coding": 4}


# --------------------------------------------------------------------------- #
# CLI over the committed results tree
# --------------------------------------------------------------------------- #


def test_cli_scores_every_committed_run():
    repo_results = BENCH / "results"
    if not repo_results.is_dir():
        pytest.skip("no committed results tree")
    runs = sorted(repo_results.glob("*/*/trial_*/run.json"))
    assert runs, "results tree has run.json but none matched the glob"
    out = subprocess.run(
        [sys.executable, str(BENCH / "judge.py"), "--results", str(repo_results), "--quiet"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    assert "judged={0}".format(len(runs)) in out.stdout
    judges = sorted(repo_results.glob("*/*/trial_*/judge.json"))
    assert len(judges) == len(runs)
    assert (repo_results / "judge_key.json").is_file()
