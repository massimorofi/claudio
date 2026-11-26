"""Tests for ``benchmark/aggregate.py``.

Run from the repo root::

    PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_aggregate.py -q

Every expected number below is hand-computed from the tiny synthetic tree built
by ``make_tree`` (see ``TREE`` for the inputs), so a regression in the
mean/median/pass-rate/winners logic fails loudly instead of drifting.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BENCHMARK_DIR = Path(__file__).resolve().parents[1]
if str(BENCHMARK_DIR) not in sys.path:  # works with or without PYTHONPATH=benchmark
    sys.path.insert(0, str(BENCHMARK_DIR))

import aggregate as A  # noqa: E402

REAL_RESULTS = BENCHMARK_DIR / "results"


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def add_run(
    root: Path,
    backend: str,
    task: str,
    trial: int = 1,
    *,
    used_input: float = 0,
    output: float = 0,
    total: float | None = None,
    errors: dict | None = None,
    wall: float = 0.0,
    passed: bool = True,
    rubric: tuple[float, float, float] | None = None,
    engine_mode: str | None = None,
    category: str | None = None,
) -> Path:
    """Write one ``<backend>/<task>/trial_<n>/`` run directory; returns its path."""
    directory = root / backend / task / f"trial_{trial}"
    run: dict = {
        "backend": backend,
        "task_id": task,
        "trial": trial,
        "verification_passed": passed,
        "tokens": {"input": used_input, "output": output, "total": total},
        "errors": {
            "command_errors": 0,
            "tool_errors": 0,
            "retries": 0,
            "timeouts": 0,
            **(errors or {}),
        },
        "duration_s": wall,
    }
    if engine_mode is not None:
        run["engine_mode"] = engine_mode
    if category is not None:
        run["category"] = category
    write_json(directory / "run.json", run)
    if rubric is not None:
        quality, intelligence, coding = rubric
        write_json(
            directory / "judge.json",
            {"mode": "heuristic", "scores": {
                "quality": quality, "intelligence": intelligence, "coding": coding}},
        )
    return directory


# The synthetic tree: 6 runs over 3 backends and 5 tasks.
#
#   alpha (available, native)      fix_login t1, fix_login t2, add_report t1
#   beta  (available, cli_bridge)  refactor_parser t1, qa_overview t1
#   gamma (UNavailable, stub)      fix_gamma t1
#
# gamma is deliberately the cheapest on tokens so the tests prove that an
# unavailable backend cannot win.
TREE = {
    "alpha": {"available": True, "engine_mode": "native"},
    "beta": {"available": True, "engine_mode": "cli_bridge"},
    "gamma": {"available": False, "engine_mode": "stub"},
}


def make_tree(root: Path) -> Path:
    write_json(root / "capability.json", {"backends": TREE})
    add_run(root, "alpha", "fix_login", 1, used_input=60, output=40, wall=1.0,
            errors={"command_errors": 1}, rubric=(5, 5, 5))
    add_run(root, "alpha", "fix_login", 2, used_input=120, output=80, wall=2.0,
            errors={"tool_errors": 2, "retries": 1}, rubric=(3, 3, 3))
    add_run(root, "alpha", "add_report", 1, used_input=400, output=200, wall=6.0,
            passed=False, errors={"timeouts": 1}, rubric=(1, 1, 1))
    add_run(root, "beta", "refactor_parser", 1, total=500, wall=3.0, rubric=(4, 2, 4))
    add_run(root, "beta", "qa_overview", 1, total=700, wall=5.0, rubric=(4, 2, 4))
    add_run(root, "gamma", "fix_gamma", 1, total=10, wall=0.5, rubric=(5, 5, 5))
    return root


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    return make_tree(tmp_path / "results")


@pytest.fixture
def summary(tree: Path) -> dict:
    runs = A.discover_runs(tree, A.load_capability(tree))
    return A.build_summary(runs, generated_at="2026-01-01T00:00:00Z")


# --------------------------------------------------------------------------
# discovery / normalisation
# --------------------------------------------------------------------------
def test_discovers_every_run_directory(tree: Path) -> None:
    runs = A.discover_runs(tree, A.load_capability(tree))
    assert len(runs) == 6
    assert {(r["backend"], r["task_id"], r["run_id"]) for r in runs} == {
        ("alpha", "fix_login", "trial_1"),
        ("alpha", "fix_login", "trial_2"),
        ("alpha", "add_report", "trial_1"),
        ("beta", "refactor_parser", "trial_1"),
        ("beta", "qa_overview", "trial_1"),
        ("gamma", "fix_gamma", "trial_1"),
    }


def test_capability_report_drives_availability_and_mode(tree: Path) -> None:
    capability = A.load_capability(tree)
    runs = {r["backend"]: r for r in A.discover_runs(tree, capability)}
    assert runs["alpha"]["available"] is True
    assert runs["alpha"]["engine_mode"] == "native"
    assert runs["gamma"]["available"] is False
    assert runs["gamma"]["engine_mode"] == "stub"


def test_total_tokens_default_to_input_plus_output(tree: Path) -> None:
    runs = {(r["task_id"], r["run_id"]): r
            for r in A.discover_runs(tree, A.load_capability(tree))}
    assert runs[("fix_login", "trial_1")]["tokens"] == {
        "input": 60.0, "output": 40.0, "total": 100.0}
    assert runs[("refactor_parser", "trial_1")]["tokens"]["total"] == 500.0


def test_category_is_inferred_from_task_id() -> None:
    assert A.infer_category("fix_login") == "bug_fix"
    assert A.infer_category("add_report") == "feature"
    assert A.infer_category("refactor_parser") == "refactor_debug"
    assert A.infer_category("qa_overview") == "codebase_qa"
    assert A.infer_category("mystery_task") == A.FALLBACK_CATEGORY
    assert A.infer_category("mystery_task", "feature") == "feature"
    assert A.infer_category("mystery_task", None, {"mystery_task": "codebase_qa"}) == "codebase_qa"


def test_run_json_category_wins_over_inference(tmp_path: Path) -> None:
    add_run(tmp_path, "alpha", "fix_login", 1, total=10, category="codebase_qa")
    runs = A.discover_runs(tmp_path, A.load_capability(tmp_path))
    assert runs[0]["category"] == "codebase_qa"


def test_missing_judge_file_means_no_rubric_signal(tmp_path: Path) -> None:
    add_run(tmp_path, "solo", "fix_solo", 1, total=42, wall=1.0)
    summary = A.build_summary(A.discover_runs(tmp_path, A.load_capability(tmp_path)))
    block = summary["backends"]["solo"]
    assert block["rubric"]["quality"] == {"mean": 0.0, "median": 0.0}
    assert block["n_runs"] == 1 and block["pass_rate"] == 1.0
    assert summary["winners"]["quality"] == ""  # nothing judged, nobody wins


# --------------------------------------------------------------------------
# exact backend statistics
# --------------------------------------------------------------------------
def test_backend_pass_rate_and_run_counts(summary: dict) -> None:
    assert summary["n_runs"] == 6
    assert summary["backends"]["alpha"]["n_runs"] == 3
    assert summary["backends"]["alpha"]["pass_rate"] == pytest.approx(2 / 3, abs=1e-4)
    assert summary["backends"]["beta"]["pass_rate"] == 1.0
    assert summary["backends"]["gamma"]["pass_rate"] == 1.0
    assert summary["backends"]["beta"]["engine_mode"] == "cli_bridge"


def test_backend_token_means_and_medians(summary: dict) -> None:
    tokens = summary["backends"]["alpha"]["tokens"]
    # totals 100, 200, 600
    assert tokens["total"]["mean"] == 300.0
    assert tokens["total"]["median"] == 200.0
    # inputs 60, 120, 400 -> mean 193.3333, median 120
    assert tokens["input"]["mean"] == pytest.approx(580 / 3, abs=1e-4)
    assert tokens["input"]["median"] == 120.0
    # outputs 40, 80, 200 -> mean 106.6667, median 80
    assert tokens["output"]["mean"] == pytest.approx(320 / 3, abs=1e-4)
    assert tokens["output"]["median"] == 80.0
    # beta totals 500, 700 -> mean == median == 600
    assert summary["backends"]["beta"]["tokens"]["total"] == {"mean": 600.0, "median": 600.0}


def test_backend_error_block_is_exact(summary: dict) -> None:
    errors = summary["backends"]["alpha"]["errors"]
    assert errors == {
        "command_errors": 1,
        "tool_errors": 2,
        "retries": 1,
        "timeouts": 1,
        "total": 5,
        "mean_per_run": pytest.approx(5 / 3, abs=1e-4),
    }
    assert summary["backends"]["beta"]["errors"]["total"] == 0
    assert summary["backends"]["beta"]["errors"]["mean_per_run"] == 0.0


def test_backend_wall_clock_is_exact(summary: dict) -> None:
    # alpha walls 1.0, 2.0, 6.0
    assert summary["backends"]["alpha"]["wall_clock"] == {"mean": 3.0, "median": 2.0}
    # beta walls 3.0, 5.0
    assert summary["backends"]["beta"]["wall_clock"] == {"mean": 4.0, "median": 4.0}


def test_backend_rubric_means_and_medians(summary: dict) -> None:
    alpha = summary["backends"]["alpha"]["rubric"]
    # quality 5, 3, 1 -> mean 3, median 3 (same for intelligence and coding)
    assert alpha["quality"] == {"mean": 3.0, "median": 3.0}
    assert alpha["intelligence"] == {"mean": 3.0, "median": 3.0}
    assert alpha["coding"] == {"mean": 3.0, "median": 3.0}
    beta = summary["backends"]["beta"]["rubric"]
    assert beta["quality"] == {"mean": 4.0, "median": 4.0}
    assert beta["intelligence"] == {"mean": 2.0, "median": 2.0}
    assert beta["coding"] == {"mean": 4.0, "median": 4.0}


# --------------------------------------------------------------------------
# categories
# --------------------------------------------------------------------------
def test_categories_block_is_exact(summary: dict) -> None:
    categories = summary["categories"]
    assert set(categories) >= set(A.CATEGORY_ORDER)
    # alpha bug_fix: totals 100 + 200 -> median 150, both passed, rubric 5,5,5,3,3,3 -> 4
    assert categories["bug_fix"]["alpha"] == {
        "n_runs": 2,
        "pass_rate": 1.0,
        "tokens_total_median": 150.0,
        "rubric_mean": 4.0,
    }
    # gamma bug_fix: single run, total 10, rubric all 5
    assert categories["bug_fix"]["gamma"] == {
        "n_runs": 1,
        "pass_rate": 1.0,
        "tokens_total_median": 10.0,
        "rubric_mean": 5.0,
    }
    # alpha feature: the only failing run, total 600, rubric all 1
    assert categories["feature"]["alpha"] == {
        "n_runs": 1,
        "pass_rate": 0.0,
        "tokens_total_median": 600.0,
        "rubric_mean": 1.0,
    }
    # beta refactor_debug / codebase_qa: rubric 4,2,4 -> mean 10/3
    assert categories["refactor_debug"]["beta"] == {
        "n_runs": 1,
        "pass_rate": 1.0,
        "tokens_total_median": 500.0,
        "rubric_mean": pytest.approx(10 / 3, abs=1e-4),
    }
    assert categories["codebase_qa"]["beta"]["tokens_total_median"] == 700.0
    # beta has no feature runs, so the backend is simply absent from that category
    assert "beta" not in categories["feature"]


def test_canonical_categories_are_always_present(tmp_path: Path) -> None:
    add_run(tmp_path, "alpha", "fix_login", 1, total=10, rubric=(4, 4, 4))
    categories = A.build_summary(A.discover_runs(tmp_path, A.load_capability(tmp_path)))["categories"]
    assert set(categories) == set(A.CATEGORY_ORDER)
    assert categories["refactor_debug"] == {}
    assert categories["codebase_qa"] == {}
    assert list(categories)[0] == "bug_fix"  # canonical order first


# --------------------------------------------------------------------------
# winners
# --------------------------------------------------------------------------
def test_winners_block_is_exact(summary: dict) -> None:
    assert summary["winners"] == {
        "tokens": "alpha",        # lowest mean total tokens among available backends
        "quality": "beta",        # 4.0 beats alpha's 3.0
        "errors": "beta",         # 0.0 errors per run
        "intelligence": "alpha",  # 3.0 beats beta's 2.0
        "coding": "beta",         # 4.0 beats alpha's 3.0
    }


def test_unavailable_backend_cannot_win(tmp_path: Path) -> None:
    write_json(tmp_path / "capability.json", {"backends": {
        "cheap_but_offline": {"available": False, "engine_mode": "stub"},
        "online": {"available": True, "engine_mode": "native"},
    }})
    add_run(tmp_path, "cheap_but_offline", "fix_a", 1, total=1, rubric=(5, 5, 5))
    add_run(tmp_path, "online", "fix_a", 1, total=999, rubric=(1, 1, 1))
    winners = A.build_summary(
        A.discover_runs(tmp_path, A.load_capability(tmp_path)))["winners"]
    assert set(winners.values()) == {"online"}


def test_winners_break_ties_alphabetically(tmp_path: Path) -> None:
    for backend in ("zeta", "alpha"):
        add_run(tmp_path, backend, "fix_a", 1, total=100, rubric=(4, 4, 4))
    winners = A.build_summary(
        A.discover_runs(tmp_path, A.load_capability(tmp_path)))["winners"]
    assert set(winners) == {"tokens", "quality", "errors", "intelligence", "coding"}
    assert set(winners.values()) == {"alpha"}


# --------------------------------------------------------------------------
# markdown + CLI
# --------------------------------------------------------------------------
def test_markdown_contains_tables_and_winners(summary: dict) -> None:
    text = A.render_markdown(summary)
    assert "| backend |" in text and "| --- |" in text
    for row in ("| alpha |", "| beta |", "| gamma |"):
        assert row in text
    assert "| bug_fix |" in text and "| codebase_qa |" in text
    assert "**Winners**" in text and "tokens: `alpha`" in text
    assert "66.7%" in text  # alpha pass rate rendered as a percentage


def test_main_writes_summary_and_prints_table(tmp_path: Path, capsys) -> None:
    tree = make_tree(tmp_path / "results")
    out = tmp_path / "nested" / "summary.json"
    assert A.main(["--results", str(tree), "--out", str(out)]) == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["n_runs"] == 6
    assert set(payload["backends"]) == {"alpha", "beta", "gamma"}
    stdout = capsys.readouterr().out
    assert "| backend |" in stdout and "Winners" in stdout


def test_main_fails_without_any_run(tmp_path: Path) -> None:
    assert A.main(["--results", str(tmp_path), "--out", str(tmp_path / "s.json")]) == 1


@pytest.mark.skipif(not REAL_RESULTS.is_dir(), reason="committed results tree is absent")
def test_committed_results_tree_satisfies_the_contract(tmp_path: Path) -> None:
    out = tmp_path / "summary.json"
    assert A.main(["--results", str(REAL_RESULTS), "--out", str(out), "--quiet"]) == 0
    summary = json.loads(out.read_text(encoding="utf-8"))
    backends = summary["backends"]
    assert set(backends) == {"google_adk", "claude_code", "opencode"}
    for block in backends.values():
        assert 0.0 <= block["pass_rate"] <= 1.0
        assert block["n_runs"] >= 1
        assert {"input", "output", "total"} <= set(block["tokens"])
    assert set(summary["categories"]) >= set(A.CATEGORY_ORDER)
    assert set(summary["winners"]) == {"tokens", "quality", "errors", "intelligence", "coding"}


# --------------------------------------------------------------------------
# schema contract (mirrors the acceptance criteria for summary.json)
# --------------------------------------------------------------------------
BACKEND_KEYS = {"available", "engine_mode", "n_runs", "pass_rate",
                "tokens", "errors", "wall_clock", "rubric"}
ERROR_KEYS = {"command_errors", "tool_errors", "retries", "timeouts",
              "total", "mean_per_run"}
MEAN_MEDIAN = {"mean", "median"}
CATEGORY_KEYS = {"n_runs", "pass_rate", "tokens_total_median", "rubric_mean"}
WINNER_KEYS = {"tokens", "quality", "errors", "intelligence", "coding"}


def assert_backend_shape(block: dict) -> None:
    assert set(block) == BACKEND_KEYS
    assert isinstance(block["available"], bool)
    assert isinstance(block["n_runs"], int) and block["n_runs"] >= 1
    assert isinstance(block["pass_rate"], float)
    assert 0.0 <= block["pass_rate"] <= 1.0
    assert set(block["tokens"]) == {"input", "output", "total"}
    for stat in block["tokens"].values():
        assert set(stat) == MEAN_MEDIAN
    assert set(block["errors"]) == ERROR_KEYS
    assert set(block["wall_clock"]) == MEAN_MEDIAN
    assert set(block["rubric"]) == {"quality", "intelligence", "coding"}
    for stat in block["rubric"].values():
        assert set(stat) == MEAN_MEDIAN


def test_summary_top_level_schema(summary: dict) -> None:
    assert set(summary) == {"generated_at", "n_runs", "backends", "categories", "winners"}
    assert summary["generated_at"] == "2026-01-01T00:00:00Z"


def test_backend_blocks_match_the_schema(summary: dict) -> None:
    for block in summary["backends"].values():
        assert_backend_shape(block)


def test_category_blocks_match_the_schema(summary: dict) -> None:
    assert set(summary["categories"]) >= set(A.CATEGORY_ORDER)
    for per_backend in summary["categories"].values():
        for entry in per_backend.values():
            assert set(entry) == CATEGORY_KEYS
            assert 0.0 <= entry["pass_rate"] <= 1.0


def test_winners_block_matches_the_schema(summary: dict) -> None:
    assert set(summary["winners"]) == WINNER_KEYS


def test_category_map_file_is_honoured(tmp_path: Path) -> None:
    add_run(tmp_path, "alpha", "mystery_task", 1, total=100, rubric=(3, 3, 3))
    mapping = tmp_path / "categories.json"
    write_json(mapping, {"mystery_task": "codebase_qa"})
    out = tmp_path / "summary.json"
    assert A.main(["--results", str(tmp_path), "--out", str(out),
                   "--category-map", str(mapping), "--quiet"]) == 0
    summary = json.loads(out.read_text(encoding="utf-8"))
    assert summary["categories"]["codebase_qa"]["alpha"]["n_runs"] == 1
    assert summary["categories"]["bug_fix"] == {}


# --------------------------------------------------------------------------
# rubric sampling
# --------------------------------------------------------------------------
def test_unjudged_runs_do_not_dilute_rubric_means(tmp_path: Path) -> None:
    """A run without ``judge.json`` counts as a run but contributes no rubric sample."""
    add_run(tmp_path, "mix", "fix_a", 1, total=100, rubric=(4, 4, 4))
    add_run(tmp_path, "mix", "fix_a", 2, total=200)          # no judge.json
    summary = A.build_summary(A.discover_runs(tmp_path, A.load_capability(tmp_path)))
    block = summary["backends"]["mix"]
    assert block["n_runs"] == 2
    assert block["tokens"]["total"] == {"mean": 150.0, "median": 150.0}
    # mean over the judged run only, not (4 + 0) / 2
    assert block["rubric"]["quality"] == {"mean": 4.0, "median": 4.0}
    assert summary["categories"]["bug_fix"]["mix"]["rubric_mean"] == 4.0
