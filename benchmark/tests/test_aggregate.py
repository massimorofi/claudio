"""Tests for ``benchmark/aggregate.py``.

Run from the repo root::

    PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_aggregate.py -q

Every expected number below is hand-computed from the tiny tree built in
``make_tree`` so a regression in the mean/median/winners logic fails loudly.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BENCHMARK_DIR = Path(__file__).resolve().parents[1]
if str(BENCHMARK_DIR) not in sys.path:  # works with or without PYTHONPATH=benchmark
    sys.path.insert(0, str(BENCHMARK_DIR))

import aggregate  # noqa: E402


# --------------------------------------------------------------------------
# fixture helpers
# --------------------------------------------------------------------------
def write_run(
    root: Path,
    category: str,
    backend: str,
    run_id: str,
    *,
    tokens_in: float,
    tokens_out: float,
    wall: float,
    errors: dict | None = None,
    passed: bool = True,
    rubric: dict | None = None,
    engine_mode: str = "native",
    available: bool = True,
    write_judge: bool = True,
    write_capability: bool = True,
) -> Path:
    """Create ``<root>/<category>/<backend>/<run_id>/`` with its artifacts."""
    d = root / category / backend / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({
        "run_id": run_id,
        "backend": backend,
        "category": category,
        "engine_mode": engine_mode,
        "available": available,
        "verification_passed": passed,
        "tokens": {"input": tokens_in, "output": tokens_out,
                   "total": tokens_in + tokens_out},
        "errors": errors or {},
        "wall_clock_seconds": wall,
    }))
    if write_judge:
        (d / "judge.json").write_text(json.dumps({"rubric": rubric or {}}))
    if write_capability:
        (d / "capability.json").write_text(
            json.dumps({"available": available, "engine_mode": engine_mode})
        )
    return d


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """Three backends, two categories, deliberately asymmetric numbers.

    claude_code / bug_fix (3 runs)
        in   = 1000, 2000, 9000   -> mean 4000.0, median 2000.0
        out  =  100,  200,  900   -> mean  400.0, median  200.0
        tot  = 1100, 2200, 9900   -> mean 4400.0, median 2200.0
        wall =   10,   20,   60   -> mean   30.0, median   20.0
        errors total = 6          -> mean_per_run 2.0
        pass = T, T, F            -> pass_rate 2/3 = 0.6667
        quality      = 4, 4, 7    -> mean 5.0, median 4.0
        intelligence = 2, 6, 7    -> mean 5.0, median 6.0
        coding       = 6, 9, 9    -> mean 8.0, median 9.0
    google_adk / feature (2 runs)
        in = 2000, 4000 -> mean/median 3000.0 ; out = 500, 1000 -> 750.0
        total = 2500, 5000 -> 3750.0 ; wall = 40, 20 -> 30.0
        errors total 2 -> mean_per_run 1.0 ; pass_rate 1.0
        rubric all dims mean 4.0
    opencode / bug_fix (1 run)
        total 600 ; wall 5 ; errors total 5 ; pass_rate 1.0
        rubric q=9, i=2, c=2 -> rubric_mean (9+2+2)/3 = 4.3333
    """
    for i, (tin, tout, wall, cmd_err, ok, q, iq, cg) in enumerate(
        [(1000, 100, 10, 0, True, 4, 2, 6),
         (2000, 200, 20, 0, True, 4, 6, 9),
         (9000, 900, 60, 6, False, 7, 7, 9)]
    ):
        write_run(tree_root(tmp_path), "bug_fix", "claude_code", f"cc-run{i + 1}",
                  tokens_in=tin, tokens_out=tout, wall=wall,
                  errors={"command_errors": cmd_err, "tool_errors": 0,
                          "retries": 0, "timeouts": 0},
                  passed=ok,
                  rubric={"quality": q, "intelligence": iq, "coding": cg},
                  engine_mode="cli_bridge")
    write_run(tree_root(tmp_path), "feature", "google_adk", "adk-run1",
              tokens_in=2000, tokens_out=500, wall=40,
              errors={"command_errors": 1, "tool_errors": 1},
              rubric={"quality": 3, "intelligence": 3, "coding": 3})
    write_run(tree_root(tmp_path), "feature", "google_adk", "adk-run2",
              tokens_in=4000, tokens_out=1000, wall=20,
              rubric={"quality": 5, "intelligence": 5, "coding": 5})
    write_run(tree_root(tmp_path), "bug_fix", "opencode", "oc-run1",
              tokens_in=500, tokens_out=100, wall=5,
              errors={"tool_errors": 5},
              rubric={"quality": 9, "intelligence": 2, "coding": 2},
              engine_mode="cli_bridge")
    return tree_root(tmp_path)


def tree_root(tmp_path: Path) -> Path:
    return tmp_path / "results"


# --------------------------------------------------------------------------
# backends block
# --------------------------------------------------------------------------
def test_backend_keys_and_shape(tree: Path) -> None:
    summary = aggregate.build_summary(aggregate.discover_runs(tree))
    assert set(summary["backends"]) == {"claude_code", "google_adk", "opencode"}
    for backend in summary["backends"].values():
        assert set(backend) >= {"available", "engine_mode", "n_runs", "pass_rate",
                                "tokens", "errors", "wall_clock", "rubric"}
        assert set(backend["tokens"]) == {"input", "output", "total"}
        assert set(backend["tokens"]["total"]) == {"mean", "median"}
        assert set(backend["errors"]) == {"command_errors", "tool_errors", "retries",
                                          "timeouts", "total", "mean_per_run"}
        assert set(backend["rubric"]) == {"quality", "intelligence", "coding"}
        assert 0.0 <= backend["pass_rate"] <= 1.0


def test_claude_code_means_medians_and_pass_rate(tree: Path) -> None:
    b = aggregate.build_summary(aggregate.discover_runs(tree))["backends"]["claude_code"]
    assert b["n_runs"] == 3
    assert b["available"] is True
    assert b["engine_mode"] == "cli_bridge"
    assert b["pass_rate"] == 0.6667
    assert b["tokens"]["input"] == {"mean": 4000.0, "median": 2000.0}
    assert b["tokens"]["output"] == {"mean": 400.0, "median": 200.0}
    assert b["tokens"]["total"] == {"mean": 4400.0, "median": 2200.0}
    assert b["wall_clock"] == {"mean": 30.0, "median": 20.0}
    assert b["errors"] == {"command_errors": 6, "tool_errors": 0, "retries": 0,
                           "timeouts": 0, "total": 6, "mean_per_run": 2.0}
    assert b["rubric"]["quality"] == {"mean": 5.0, "median": 4.0}
    assert b["rubric"]["intelligence"] == {"mean": 5.0, "median": 6.0}
    assert b["rubric"]["coding"] == {"mean": 8.0, "median": 9.0}


def test_google_adk_and_opencode(tree: Path) -> None:
    backends = aggregate.build_summary(aggregate.discover_runs(tree))["backends"]
    adk = backends["google_adk"]
    assert adk["n_runs"] == 2 and adk["pass_rate"] == 1.0
    assert adk["tokens"]["total"] == {"mean": 3750.0, "median": 3750.0}
    assert adk["errors"]["total"] == 2 and adk["errors"]["mean_per_run"] == 1.0
    assert adk["rubric"]["quality"] == {"mean": 4.0, "median": 4.0}

    oc = backends["opencode"]
    assert oc["n_runs"] == 1 and oc["pass_rate"] == 1.0
    assert oc["tokens"]["total"] == {"mean": 600.0, "median": 600.0}
    assert oc["errors"] == {"command_errors": 0, "tool_errors": 5, "retries": 0,
                            "timeouts": 0, "total": 5, "mean_per_run": 5.0}


# --------------------------------------------------------------------------
# categories block
# --------------------------------------------------------------------------
def test_categories_block(tree: Path) -> None:
    cats = aggregate.build_summary(aggregate.discover_runs(tree))["categories"]
    assert set(cats) == {"bug_fix", "feature"}
    assert set(cats["bug_fix"]) == {"claude_code", "opencode"}
    assert cats["bug_fix"]["claude_code"] == {
        "n_runs": 3, "pass_rate": 0.6667, "tokens_total_median": 2200.0, "rubric_mean": 6.0}
    assert cats["bug_fix"]["opencode"] == {
        "n_runs": 1, "pass_rate": 1.0, "tokens_total_median": 600.0, "rubric_mean": 4.3333}
    assert cats["feature"]["google_adk"] == {
        "n_runs": 2, "pass_rate": 1.0, "tokens_total_median": 3750.0, "rubric_mean": 4.0}


# --------------------------------------------------------------------------
# winners block
# --------------------------------------------------------------------------
def test_winners_block(tree: Path) -> None:
    summary = aggregate.build_summary(aggregate.discover_runs(tree))
    assert set(summary["winners"]) == {"tokens", "quality", "errors", "intelligence", "coding"}
    # cheapest tokens -> opencode (600); best quality -> opencode (9.0);
    # fewest errors/run -> google_adk (1.0); best intelligence & coding -> claude_code.
    assert summary["winners"] == {
        "tokens": "opencode",
        "quality": "opencode",
        "errors": "google_adk",
        "intelligence": "claude_code",
        "coding": "claude_code",
    }


def test_unavailable_backend_cannot_win(tmp_path: Path) -> None:
    root = tree_root(tmp_path)
    write_run(root, "bug_fix", "opencode", "oc", tokens_in=1, tokens_out=1, wall=1,
              rubric={"quality": 10, "intelligence": 10, "coding": 10}, available=False)
    write_run(root, "bug_fix", "claude_code", "cc", tokens_in=5000, tokens_out=500,
              wall=9, rubric={"quality": 1, "intelligence": 1, "coding": 1})
    summary = aggregate.build_summary(aggregate.discover_runs(root))
    assert summary["backends"]["opencode"]["available"] is False
    assert set(summary["winners"].values()) == {"claude_code"}


# --------------------------------------------------------------------------
# robustness + CLI
# --------------------------------------------------------------------------
def test_missing_judge_and_capability_default_to_zero(tmp_path: Path) -> None:
    root = tree_root(tmp_path)
    write_run(root, "bug_fix", "google_adk", "bare", tokens_in=100, tokens_out=50,
              wall=3, write_judge=False, write_capability=False)
    b = aggregate.build_summary(aggregate.discover_runs(root))["backends"]["google_adk"]
    assert b["engine_mode"] == "native"          # from run.json
    assert b["rubric"]["quality"] == {"mean": 0.0, "median": 0.0}
    assert b["tokens"]["total"] == {"mean": 150.0, "median": 150.0}


def test_total_tokens_default_to_input_plus_output(tmp_path: Path) -> None:
    root = tree_root(tmp_path)
    d = write_run(root, "feature", "opencode", "no_total", tokens_in=700, tokens_out=300,
                  wall=2, rubric={"quality": 3, "intelligence": 3, "coding": 3})
    run = json.loads((d / "run.json").read_text())
    del run["tokens"]["total"]
    (d / "run.json").write_text(json.dumps(run))
    b = aggregate.build_summary(aggregate.discover_runs(root))["backends"]["opencode"]
    assert b["tokens"]["total"] == {"mean": 1000.0, "median": 1000.0}


def test_markdown_table_lists_backends_and_winners(tree: Path) -> None:
    summary = aggregate.build_summary(aggregate.discover_runs(tree))
    md = aggregate.render_markdown(summary)
    assert "| backend |" in md and "| metric | backend |" in md
    for name in summary["backends"]:
        assert f"| {name} " in md
    assert "| tokens | opencode |" in md


def test_cli_writes_summary_and_prints_table(tree: Path, capsys) -> None:
    out = tree / "summary.json"
    assert aggregate.main(["--results", str(tree), "--out", str(out)]) == 0
    payload = json.loads(out.read_text())
    assert payload["backends"].keys() == {"claude_code", "google_adk", "opencode"}
    assert payload["generated_at"]
    stdout = capsys.readouterr().out
    assert "| backend |" in stdout and "pass rate" in stdout


def test_cli_fails_on_empty_results(tmp_path: Path, capsys) -> None:
    assert aggregate.main(["--results", str(tmp_path / "nope")]) == 1
