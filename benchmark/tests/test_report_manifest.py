"""Tests for the ``--manifest`` filter of ``benchmark/report.py``.

``benchmark/results`` keeps ``<backend>/<task>/`` directories for tasks that were
dropped from ``benchmark/tasks.json``. Those stale runs must not leak into the
per-task detail table of ``BENCHMARK_REPORT.md``, so ``report.py`` resolves the
manifest ids (``load_manifest_ids``) and passes them into the run discovery for
that table. ``--manifest none`` / ``off`` turns the filter back off.

Run: ``PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_report_manifest.py -q``
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

BENCHMARK_DIR = Path(__file__).resolve().parents[1]
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))

import report  # noqa: E402

KEEP_TASK = "keep-this-task"        # listed in the manifest
STALE_TASK = "stale-removed-task"   # present on disk, absent from the manifest
BACKEND = "claude_code"


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def make_manifest(root: Path, *ids: str) -> Path:
    """Write a ``tasks.json``-shaped manifest listing *ids*; returns its path."""
    path = root / "tasks.json"
    _write(path, {"tasks": [{"id": task} for task in ids]})
    return path


def add_trial(root: Path, task_id: str, *, backend: str = BACKEND, trial: int = 1) -> Path:
    """Write one ``<results>/<backend>/<task>/trial_<n>/{run,judge}.json`` pair."""
    base = root / backend / task_id / f"trial_{trial}"
    _write(base / "run.json", {
        "task_id": task_id,
        "backend": backend,
        "trial": trial,
        "engine_mode": "real",
        "ok": True,
        "tokens": 1200 + 100 * trial,
        "latency_ms": 900 + 50 * trial,
        "errors": [],
        "evidence": [f"run.json:tokens={1200 + 100 * trial}"],
    })
    _write(base / "judge.json", {
        "task_id": task_id,
        "backend": backend,
        "trial": trial,
        "judge_mode": "heuristic",
        "scores": {"quality": 4.0, "intelligence": 3.5, "coding_ability": 4.5},
        "issues": [],
        "cited_evidence": [f"workspace/{backend}/{task_id}/patch.diff"],
    })
    return base


@pytest.fixture()
def results_with_stale(tmp_path: Path) -> dict[str, Path]:
    """A results dir holding one manifest task and one stale (non-manifest) task."""
    root = tmp_path / "results"
    make_manifest(tmp_path, KEEP_TASK)
    add_trial(root, KEEP_TASK)
    add_trial(root, KEEP_TASK, backend="opencode", trial=2)
    add_trial(root, STALE_TASK)
    return {"root": root, "manifest": tmp_path / "tasks.json", "tmp": tmp_path}


def render(tmp_path: Path, root: Path, *extra: str) -> str:
    """Run the CLI end to end into a temp file and return the rendered markdown."""
    out = tmp_path / "RENDERED.md"
    code = report.main(["--results", str(root), "--out", str(out), *extra])
    assert code == 0, "report.main() must exit 0"
    assert out.is_file(), f"{out} was not written"
    return out.read_text(encoding="utf-8")


# --- the flag itself ----------------------------------------------------------


def test_help_lists_manifest_flag() -> None:
    help_text = report.build_parser().format_help()
    assert "--manifest" in help_text


def test_manifest_defaults_to_benchmark_tasks_json() -> None:
    assert report.parse_args([]).manifest == str(BENCHMARK_DIR / "tasks.json")
    assert report.DEFAULT_TASK_MANIFEST == BENCHMARK_DIR / "tasks.json"
    ids = report.load_manifest_ids(report.DEFAULT_TASK_MANIFEST)
    assert ids and "rate-limiter-offbyone" in ids


def test_load_manifest_ids_always_returns_a_set(tmp_path: Path) -> None:
    assert report.load_manifest_ids(make_manifest(tmp_path, KEEP_TASK)) == {KEEP_TASK}
    # unusable input yields an empty set, never None (that is load_task_manifest)
    assert report.load_manifest_ids(tmp_path / "missing.json") == set()
    assert report.load_manifest_ids("none") == set()
    assert report.load_manifest_ids("off") == set()
    assert report.load_manifest_ids(None) == set()
    # load_task_manifest keeps the "None means do not filter" contract
    assert report.load_task_manifest("none") is None
    assert report.load_task_manifest("off") is None
    assert report.load_task_manifest(tmp_path / "missing.json") is None


# --- the per-task detail table -----------------------------------------------


def test_stale_task_dir_is_absent_from_rendered_markdown(results_with_stale) -> None:
    """The core requirement: stale (non-manifest) task dirs must not be rendered."""
    text = render(results_with_stale["tmp"], results_with_stale["root"],
                  "--manifest", str(results_with_stale["manifest"]))

    assert STALE_TASK not in text, (
        f"stale task dir {STALE_TASK!r} leaked into the report")
    assert KEEP_TASK in text, (
        f"manifest task {KEEP_TASK!r} is missing from the report")
    # the per-task section really rendered rows, it is not an empty report
    assert "## Per-task breakdown" in text
    assert f"### Task `{KEEP_TASK}`" in text


def test_default_manifest_excludes_stale_task(results_with_stale, monkeypatch) -> None:
    """No flag at all -> benchmark/tasks.json is used, so stale dirs are dropped."""
    tmp = results_with_stale["tmp"]
    manifest = make_manifest(tmp / "default", KEEP_TASK)
    monkeypatch.setattr(report, "DEFAULT_TASK_MANIFEST", manifest)
    monkeypatch.setattr(report, "DEFAULT_TASKS_MANIFEST", manifest)
    text = render(tmp, results_with_stale["root"])
    assert STALE_TASK not in text
    assert KEEP_TASK in text


def test_manifest_none_renders_the_stale_task(results_with_stale) -> None:
    """``--manifest none`` disables the filter, so the stale dir shows up again."""
    text = render(results_with_stale["tmp"], results_with_stale["root"],
                  "--manifest", "none")
    assert STALE_TASK in text
    assert KEEP_TASK in text


def test_manifest_off_disables_the_filter(results_with_stale) -> None:
    text = render(results_with_stale["tmp"], results_with_stale["root"],
                  "--manifest", "off")
    assert STALE_TASK in text


def test_render_report_accepts_manifest_ids(results_with_stale) -> None:
    """``render_report`` filters the detail table via ``load_manifest_ids``."""
    root = results_with_stale["root"]
    ids = report.load_manifest_ids(results_with_stale["manifest"])
    assert report.load_manifest_ids(results_with_stale["manifest"]) == {KEEP_TASK}

    filtered = report.render_report(root, manifest_ids=ids)
    assert STALE_TASK not in filtered
    assert KEEP_TASK in filtered

    unfiltered = report.render_report(root)
    assert STALE_TASK in unfiltered, "no manifest must keep the old render-everything behaviour"


def test_load_trials_receives_the_manifest_ids(results_with_stale) -> None:
    root = results_with_stale["root"]
    ids = report.load_manifest_ids(results_with_stale["manifest"])

    trials, warnings = report.load_trials(root, {}, manifest_ids=ids)
    assert {t["task_id"] for t in trials} == {KEEP_TASK}
    assert all(STALE_TASK not in str(t.get("run_path")) for t in trials)
    assert any("task manifest" in w for w in warnings)

    unfiltered, _ = report.load_trials(root, {})
    assert any(t["task_id"] == STALE_TASK for t in unfiltered)


def test_summary_and_capability_sections_are_unchanged(results_with_stale) -> None:
    """The manifest filter only touches the per-task table, not the other sections.

    The summary/capability *rendering* (headings, table shape) must be identical
    with and without the filter; only the trial counts those sections report on
    may differ, because the stale run is genuinely no longer part of the set.
    """
    root = results_with_stale["root"]
    ids = report.load_manifest_ids(results_with_stale["manifest"])

    filtered = report.render_report(root, manifest_ids=ids)
    unfiltered = report.render_report(root)

    def headings(text: str) -> list[str]:
        return [ln for ln in text.splitlines() if ln.startswith("## ")]

    assert headings(filtered) == headings(unfiltered), (
        "the manifest filter must not add or drop report sections")

    def table_header(text: str, section: str) -> str:
        body = text.split(section, 1)[1]
        return next(ln for ln in body.splitlines() if ln.startswith("|"))

    for section in ("## KPI comparison", "## Token efficiency vs quality"):
        assert table_header(filtered, section) == table_header(unfiltered, section), (
            f"{section} must render the same columns with or without the filter")

    # the per-task table is the part that actually changed
    assert f"### Task `{STALE_TASK}`" in unfiltered
    assert f"### Task `{STALE_TASK}`" not in filtered
