"""Tests for ``benchmark/report.py``.

Run: ``PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_report.py -q``
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import report

REQUIRED_HEADINGS = (
    "## Executive summary",
    "## KPI comparison",
    "## Per-task breakdown",
    "## Failure modes",
    "## Token efficiency vs quality",
    "## Caveats",
    "## Real-backend runbook",
)

BACKEND_IDS = ("claude_code", "opencode", "google_adk")

TASKS = ("fix-flaky-test", "summarise-long-doc")


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def build_results(root: Path, *, engine_modes=None, judge_mode="heuristic") -> Path:
    """Write a complete, self-consistent stub-mode results tree."""
    engine_modes = engine_modes or {b: "stub" for b in BACKEND_IDS}
    availability = {b: engine_modes[b] == "real" for b in BACKEND_IDS}
    reasons = {
        "claude_code": "cli_not_found: `claude` is not installed in this image",
        "opencode": "cli_not_found: `opencode` is not installed in this image",
        "google_adk": "dependency_missing: `google-adk` package not importable",
    }
    tokens = {"claude_code": 1200, "opencode": 900, "google_adk": 640}
    scores = {
        "claude_code": {"quality": 4.0, "intelligence": 3.5, "coding_ability": 4.5},
        "opencode": {"quality": 3.0, "intelligence": 3.0, "coding_ability": 3.5},
        "google_adk": {"quality": 2.0, "intelligence": 2.5, "coding_ability": 2.0},
    }

    _write(root / "capability.json", {
        "generated_at": "2026-11-25T22:00:00Z",
        "backends": {
            b: {"available": availability[b], "engine_mode": engine_modes[b],
                "reason": reasons[b], "model": None if not availability[b] else f"model-{b}"}
            for b in BACKEND_IDS
        },
    })

    trials = []
    for backend in BACKEND_IDS:
        for task in TASKS:
            for trial in (1, 2):
                run = {
                    "task_id": task,
                    "backend": backend,
                    "trial": trial,
                    "engine_mode": engine_modes[backend],
                    "ok": not (backend == "opencode" and task == "fix-flaky-test"),
                    "tokens": tokens[backend] + 100 * trial,
                    "latency_ms": 900 + 50 * trial,
                    "model": None,
                    "config": {"permission_mode": "default", "temperature": 0.0},
                    "errors": ([] if engine_modes[backend] == "real"
                               else [f"{reasons[backend]} (trial {trial})"]),
                    "timeouts": 1 if (backend == "google_adk" and trial == 2) else 0,
                    "evidence": [f"run.json:tokens={tokens[backend] + 100 * trial}"],
                }
                judge = {
                    "task_id": task,
                    "backend": backend,
                    "trial": trial,
                    "judge_mode": judge_mode,
                    "scores": dict(scores[backend]),
                    "issues": (["no regression test added"] if task == "fix-flaky-test" else []),
                    "cited_evidence": [f"workspace/{backend}/{task}/patch.diff",
                                       f"run.json:tokens={tokens[backend] + 100 * trial}"],
                }
                base = root / backend / task / f"trial-{trial}"
                _write(base / "run.json", run)
                _write(base / "judge.json", judge)
                trials.append(run)

    _write(root / "summary.json", {
        "schema_version": 1,
        "generated_at": "2026-11-25T22:05:00Z",
        "trials": 2,
        "judge_mode": judge_mode,
        "workload_types": ["code generation", "multi-step tool use", "reasoning / planning"],
        "backends": {
            b: {"available": availability[b], "engine_mode": engine_modes[b], "reason": reasons[b]}
            for b in BACKEND_IDS
        },
    })
    return root


@pytest.fixture()
def stub_results(tmp_path: Path) -> Path:
    return build_results(tmp_path / "results")


@pytest.fixture()
def real_results(tmp_path: Path) -> Path:
    return build_results(
        tmp_path / "real",
        engine_modes={"claude_code": "real", "opencode": "real", "google_adk": "stub"},
    )


# --- headings / backends -----------------------------------------------------


def test_all_required_headings_present(stub_results: Path) -> None:
    text = report.render_report(stub_results)
    missing = [h for h in REQUIRED_HEADINGS if h not in text]
    assert not missing, f"missing headings: {missing}"


def test_all_backend_ids_present(stub_results: Path) -> None:
    text = report.render_report(stub_results)
    for backend in BACKEND_IDS:
        assert backend in text, f"backend id {backend!r} missing from report"


def test_headings_and_backends_with_no_results_at_all(tmp_path: Path) -> None:
    text = report.render_report(tmp_path / "does-not-exist")
    for heading in REQUIRED_HEADINGS:
        assert heading in text
    for backend in BACKEND_IDS:
        assert backend in text


def test_headings_order_is_stable(stub_results: Path) -> None:
    text = report.render_report(stub_results)
    positions = [text.index(h) for h in REQUIRED_HEADINGS]
    assert positions == sorted(positions)


# --- KPI table ---------------------------------------------------------------


def test_kpi_table_lists_all_five_kpis(stub_results: Path) -> None:
    text = report.render_report(stub_results)
    section = text.split("## KPI comparison", 1)[1].split("## Per-task breakdown", 1)[0]
    for kpi in report.KPIS:
        assert kpi["label"] in section, f"KPI {kpi['label']!r} missing from KPI comparison"
    assert section.count("|") > 10, "KPI comparison must be a markdown table"


def test_kpi_table_has_a_column_per_backend(stub_results: Path) -> None:
    text = report.render_report(stub_results)
    header = [ln for ln in text.splitlines()
              if ln.startswith("| KPI |")][0]
    for backend in BACKEND_IDS:
        assert f"`{backend}`" in header


def test_stub_numbers_are_never_presented_as_real(stub_results: Path) -> None:
    text = report.render_report(stub_results)
    assert "STUB-MODE DATA" in text
    assert "Not real backend results" in text
    # every stub backend value is tagged, and no winner is claimed
    assert "(stub)" in text
    assert "No winner can be declared" in text
    assert "undetermined - no real-backend data" in text


def test_real_backends_get_winners_and_recommendations(real_results: Path) -> None:
    text = report.render_report(real_results)
    assert "No winner can be declared" not in text
    assert "undetermined - no real-backend data" not in text
    exec_section = text.split("## Executive summary", 1)[1].split("## KPI comparison", 1)[0]
    assert "claude_code" in exec_section
    # google_adk stayed stub, so it must still be called out as such
    assert "google_adk" in text and "STUB-MODE DATA" in text


def test_stub_backend_cannot_win_a_kpi(real_results: Path) -> None:
    agg = report.aggregate(report.load_trials(real_results, report._backend_map(
        report._read_json(real_results / "capability.json")))[0])
    stub_only = {b for b in BACKEND_IDS if report.is_stub(agg[b], {}, b)}
    assert "google_adk" in stub_only
    winners = report.kpi_winners(agg, stub_only)
    assert all(w != "google_adk" for w in winners.values())


# --- per-task / failures / efficiency ----------------------------------------


def test_per_task_breakdown_lists_task_backend_rows(stub_results: Path) -> None:
    text = report.render_report(stub_results)
    section = text.split("## Per-task breakdown", 1)[1].split("## Failure modes", 1)[0]
    for task in TASKS:
        assert task in section
    for backend in BACKEND_IDS:
        assert backend in section
    for column in ("result", "tokens", "errors", "rubric scores", "cited evidence"):
        assert column in section
    assert "PASS" in section and "FAIL" in section


def test_failure_modes_report_error_classes_and_timeouts(stub_results: Path) -> None:
    text = report.render_report(stub_results)
    section = text.split("## Failure modes", 1)[1].split("## Token efficiency", 1)[0]
    assert "cli_not_found" in section
    assert "timeout" in section
    for backend in BACKEND_IDS:
        assert backend in section


def test_token_efficiency_vs_quality_table(stub_results: Path) -> None:
    text = report.render_report(stub_results)
    section = text.split("## Token efficiency vs quality", 1)[1].split("## Caveats", 1)[0]
    assert "quality per 1k tokens" in section
    assert "not comparable" in section
    for backend in BACKEND_IDS:
        assert backend in section


# --- caveats -----------------------------------------------------------------


def test_caveats_cover_sample_size_modes_models_and_judge(stub_results: Path) -> None:
    text = report.render_report(stub_results)
    section = text.split("## Caveats", 1)[1].split("## Real-backend runbook", 1)[0]
    assert "Sample size" in section
    assert "N = 12" in section          # 3 backends x 2 tasks x 2 trials
    assert "engine_mode" in section and "stub" in section
    assert "Model and configuration differences" in section
    assert "heuristic" in section
    assert "llm" in section             # both judge modes are explained


def test_caveats_reflect_llm_judge_mode(tmp_path: Path) -> None:
    root = build_results(tmp_path / "r", judge_mode="llm")
    text = report.render_report(root)
    section = text.split("## Caveats", 1)[1]
    assert "llm" in section


# --- runbook -----------------------------------------------------------------


def test_runbook_has_exact_argv_for_every_adapter(real_results: Path) -> None:
    text = report.render_report(real_results)
    runbook = text.split("## Real-backend runbook", 1)[1]
    # claude code
    assert "claude -p --output-format stream-json --verbose" in runbook
    assert "--permission-mode" in runbook
    assert "ANTHROPIC_BASE_URL" in runbook and "ANTHROPIC_API_KEY" in runbook
    # opencode
    assert "opencode run" in runbook
    assert "--format json --thinking" in runbook
    assert "OPENAI_API_KEY" in runbook and "OPENAI_BASE_URL" in runbook
    # google adk (in-process)
    assert "create_engine" in runbook
    assert "google_adk" in runbook and "adk" in runbook
    assert "claudio_backend.engine" in runbook


def test_runbook_includes_rerun_commands_with_three_trials(real_results: Path) -> None:
    text = report.render_report(real_results)
    runbook = text.split("## Real-backend runbook", 1)[1]
    for backend in BACKEND_IDS:
        assert f"--backend {backend}" in runbook.replace("  ", " ")
    assert "--trials 3" in runbook
    assert "benchmark/report.py --results benchmark/results --out BENCHMARK_REPORT.md" in runbook


# --- cli ---------------------------------------------------------------------


def test_cli_exits_zero_and_writes_file(tmp_path: Path, capsys) -> None:
    root = build_results(tmp_path / "results")
    out = tmp_path / "REPORT.md"
    code = report.main(["--results", str(root), "--out", str(out)])
    assert code == 0
    text = out.read_text(encoding="utf-8")
    for heading in REQUIRED_HEADINGS:
        assert heading in text
    for backend in BACKEND_IDS:
        assert backend in text


def test_cli_exits_zero_when_results_missing(tmp_path: Path) -> None:
    out = tmp_path / "REPORT.md"
    assert report.main(["--results", str(tmp_path / "nope"), "--out", str(out)]) == 0
    assert out.exists()


def test_cli_survives_corrupt_artifacts(tmp_path: Path) -> None:
    root = build_results(tmp_path / "results")
    (root / "summary.json").write_text("{not json", encoding="utf-8")
    broken = root / "claude_code" / "fix-flaky-test" / "trial-1"
    (broken / "run.json").write_text("[oops", encoding="utf-8")
    out = tmp_path / "REPORT.md"
    # ``--tasks none``: this tree's task ids are synthetic, so the default
    # benchmark/tasks.json manifest would skip them before they could be read.
    assert report.main(["--results", str(root), "--out", str(out), "--tasks", "none"]) == 0
    text = out.read_text(encoding="utf-8")
    assert "unreadable" in text  # loader reports what it skipped
    for heading in REQUIRED_HEADINGS:
        assert heading in text


def test_summary_embedded_trials_are_used_as_fallback(tmp_path: Path) -> None:
    root = tmp_path / "results"
    root.mkdir()
    _write(root / "summary.json", {
        "judge_mode": "heuristic",
        "backends": {"claude_code": {"available": True, "engine_mode": "real"}},
        "trials": [{
            "backend": "claude_code", "task_id": "t1", "engine_mode": "real",
            "tokens": 500, "passed": True,
            "judge": {"scores": {"quality": 4, "intelligence": 4, "coding_ability": 5},
                      "cited_evidence": ["patch.diff"]},
        }],
    })
    text = report.render_report(root)
    assert "t1" in text
    assert "claude_code" in text


def test_judge_issues_count_toward_the_issues_kpi(real_results: Path) -> None:
    cap = report._backend_map(report._read_json(real_results / "capability.json"))
    trials, _ = report.load_trials(real_results, cap)
    agg = report.aggregate(trials)
    # every fix-flaky-test trial carries exactly one judge issue
    assert agg["claude_code"]["kpis"]["issues_per_task"] == 1.0
    assert agg["claude_code"]["tasks"]["fix-flaky-test"]["issues"] == ["no regression test added"] * 2


def test_rubric_scale_differences_are_normalised(tmp_path: Path) -> None:
    root = tmp_path / "results"
    _write(root / "capability.json", {"backends": {"opencode": {"available": True, "engine_mode": "real"}}})
    _write(root / "opencode" / "t1" / "trial-1" / "run.json",
           {"backend": "opencode", "task_id": "t1", "engine_mode": "real", "tokens": 100, "ok": True})
    _write(root / "opencode" / "t1" / "trial-1" / "judge.json",
           {"judge_mode": "llm", "scores": {"quality": 80, "reasoning": 5, "code_quality": 0.9}})
    agg = report.aggregate(report.load_trials(root, report._backend_map(
        report._read_json(root / "capability.json")))[0])
    assert agg["opencode"]["kpis"]["quality"] == 0.8
    assert agg["opencode"]["kpis"]["intelligence"] == 1.0   # 5 on a 0-5 scale
    assert agg["opencode"]["kpis"]["coding_ability"] == 0.9

# --- task manifest filter (--tasks) -------------------------------------------
#
# ``benchmark/results`` keeps ``<backend>/<task>/`` dirs for tasks that were
# dropped from ``benchmark/tasks.json``. Those stale runs must produce no rows
# and must not move a KPI winner, so the report filters them against the
# manifest the same way ``aggregate.py`` does.

STALE_TASK = "fix_slugify"          # present on disk, absent from the manifest


def make_manifest(root: Path, *ids: str) -> Path:
    """Write a ``tasks.json``-shaped manifest listing *ids*; returns its path."""
    path = root / "tasks.json"
    _write(path, {"tasks": [{"id": task} for task in ids]})
    return path


def add_stale_run(root: Path) -> Path:
    """Add one ``run.json`` for a task that is *not* in the manifest."""
    path = root / "opencode" / STALE_TASK / "trial-1" / "run.json"
    _write(path, {"task_id": STALE_TASK, "backend": "opencode", "trial": 1,
                  "engine_mode": "real", "ok": True, "tokens": 999999})
    return path


def _cap(root: Path) -> dict:
    return report._backend_map(report._read_json(root / "capability.json"))


def test_load_task_manifest_reads_ids(tmp_path: Path) -> None:
    assert report.load_task_manifest(make_manifest(tmp_path, *TASKS)) == set(TASKS)
    assert report.load_task_manifest(tmp_path / "missing.json") is None
    assert report.load_task_manifest(Path("none")) is None
    assert report.load_task_manifest(None) is None


def test_task_id_from_path_follows_the_results_layout(tmp_path: Path) -> None:
    run_path = add_stale_run(tmp_path)
    assert report.task_id_from_path(run_path, tmp_path) == STALE_TASK


def test_load_trials_skips_task_dir_not_in_manifest(tmp_path: Path) -> None:
    root = build_results(tmp_path / "results")
    add_stale_run(root)
    ids = report.load_task_manifest(make_manifest(tmp_path, *TASKS))

    trials, warnings = report.load_trials(root, _cap(root), ids)
    assert {t["task_id"] for t in trials} == set(TASKS)
    assert all(STALE_TASK not in str(t["run_path"]) for t in trials)
    assert any("task manifest" in w for w in warnings)

    # without a manifest the stale dir comes back, so the filter is what excludes it
    unfiltered, _ = report.load_trials(root, _cap(root))
    assert len(unfiltered) == len(trials) + 1
    assert any(t["task_id"] == STALE_TASK for t in unfiltered)


def test_render_report_omits_non_manifest_task(tmp_path: Path) -> None:
    root = build_results(tmp_path / "results")
    add_stale_run(root)
    ids = report.load_task_manifest(make_manifest(tmp_path, *TASKS))

    text = report.render_report(root, ids)
    assert STALE_TASK not in text
    for task in TASKS:
        assert task in text
    # and it is rendered when the filter is off
    assert STALE_TASK in report.render_report(root)


def test_cli_tasks_option_excludes_stale_task(tmp_path: Path) -> None:
    root = build_results(tmp_path / "results")
    add_stale_run(root)
    out = tmp_path / "REPORT.md"
    manifest = make_manifest(tmp_path, *TASKS)

    assert report.main(["--results", str(root), "--out", str(out),
                        "--tasks", str(manifest)]) == 0
    assert STALE_TASK not in out.read_text(encoding="utf-8")

    assert report.main(["--results", str(root), "--out", str(out),
                        "--tasks", "none"]) == 0
    assert STALE_TASK in out.read_text(encoding="utf-8")


def test_cli_defaults_to_benchmark_tasks_json() -> None:
    assert report.DEFAULT_TASKS_MANIFEST == Path(report.__file__).resolve().parent / "tasks.json"
    ids = report.load_task_manifest(report.DEFAULT_TASKS_MANIFEST)
    assert ids and "rate-limiter-offbyone" in ids


def test_committed_report_contains_only_manifest_tasks(tmp_path: Path) -> None:
    """The committed tree renders no task dir that is absent from tasks.json."""
    real = Path(__file__).resolve().parents[1] / "results"
    if not real.is_dir():
        pytest.skip("committed results tree is absent")
    ids = report.load_task_manifest(report.DEFAULT_TASKS_MANIFEST)
    trials, _ = report.load_trials(real, report._backend_map(
        report._read_json(real / "capability.json")), ids)
    assert trials, "committed results should render something"
    path_ids = {report.task_id_from_path(t["run_path"], real) for t in trials}
    assert path_ids <= ids
    text = report.render_report(real, ids)
    for stale in ("fix_slugify", "add_config_loader", "fix_csv_top_row",
                  "fix_parse_ints", "fix_deadline_parse", "fix_retry_backoff",
                  "fix_sum_list"):
        assert stale not in text


def test_summary_embedded_trials_are_filtered_too(tmp_path: Path) -> None:
    """A stale ``summary.json`` cannot leak a removed task through the fallback."""
    root = tmp_path / "results"
    root.mkdir()
    _write(root / "summary.json", {
        "judge_mode": "heuristic",
        "backends": {"claude_code": {"available": True, "engine_mode": "real"}},
        "trials": [
            {"backend": "claude_code", "task_id": "rate-limiter-offbyone",
             "engine_mode": "real", "tokens": 500, "passed": True},
            {"backend": "claude_code", "task_id": STALE_TASK,
             "engine_mode": "real", "tokens": 900, "passed": True},
        ],
    })
    ids = report.load_task_manifest(make_manifest(tmp_path, "rate-limiter-offbyone"))
    trials, _ = report.trials_from_summary(
        report._read_json(root / "summary.json"), {}, ids)
    assert [t["task_id"] for t in trials] == ["rate-limiter-offbyone"]
    # unfiltered, both embedded trials survive
    both, _ = report.trials_from_summary(report._read_json(root / "summary.json"), {})
    assert len(both) == 2
