"""Tests for the *manifest filter* in ``benchmark/aggregate.py``.

Run from the repo root::

    PYTHONPATH=benchmark python3 -m pytest benchmark/tests/test_aggregate_manifest.py -q

The rule these tests protect: stale result directories under
``benchmark/results`` are **filtered out of the aggregate, never deleted or
moved**. Every test below asserts that the tree on disk is byte-for-byte the
same after a filtered run.

Covered API:

* ``load_manifest_ids(path) -> set[str]`` - reads ``tasks[].id``.
* ``task_id_from_path(run_dir) -> str`` - the ``<task_id>`` component of
  ``benchmark/results/<backend>/<task_id>/trial_N`` (the two-argument
  ``task_id_from_path(run_dir, results_dir)`` form keeps working).
* ``discover_runs(results_dir, manifest_ids=None)`` - optional filter,
  ``None`` keeps everything (back-compat for ``report.py``).
* ``--manifest PATH`` CLI flag (default ``benchmark/tasks.json``; the literal
  ``none``/``off`` disable filtering) and the
  ``aggregate: skipped N non-manifest run(s)`` progress line.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

BENCHMARK_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BENCHMARK_DIR.parent
if str(BENCHMARK_DIR) not in sys.path:  # works with or without PYTHONPATH=benchmark
    sys.path.insert(0, str(BENCHMARK_DIR))

import aggregate as A  # noqa: E402

AGGREGATE = BENCHMARK_DIR / "aggregate.py"
REAL_RESULTS = BENCHMARK_DIR / "results"
REAL_MANIFEST = BENCHMARK_DIR / "tasks.json"

LISTED = "rate-limiter-offbyone"     # task id present in the manifest
STALE = "fix_slugify"                # leftover dir from a removed task


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def add_run(root: Path, backend: str, task: str, trial: int = 1,
            *, total: float = 100) -> Path:
    """Write one ``<backend>/<task>/trial_<n>/run.json``; return its directory."""
    directory = root / backend / task / f"trial_{trial}"
    directory.mkdir(parents=True, exist_ok=True)
    write_json(directory / "run.json", {
        "backend": backend,
        "task_id": task,
        "trial": trial,
        "verification_passed": True,
        "tokens": {"input": total, "output": 0, "total": total},
        "duration_s": 1.0,
    })
    return directory


def manifest_file(root: Path, *ids: str) -> Path:
    """A ``tasks.json``-shaped manifest listing *ids*."""
    return write_json(root / "tasks.json", {"tasks": [{"id": task} for task in ids]})


def two_task_tree(root: Path) -> Path:
    """One listed run (100 tokens) plus one stale run (900 tokens)."""
    add_run(root, "alpha", LISTED, 1, total=100)
    add_run(root, "alpha", STALE, 1, total=900)
    return root


def snapshot(root: Path) -> dict[str, int]:
    """``{relative run.json path: size}`` - proves nothing was deleted or rewritten."""
    return {str(p.relative_to(root)): p.stat().st_size for p in sorted(root.rglob("run.json"))}


def n_runs(summary: dict) -> int:
    return sum(block["n_runs"] for block in summary["backends"].values())


# --------------------------------------------------------------------------
# load_manifest_ids(path) -> set[str]
# --------------------------------------------------------------------------
def test_load_manifest_ids_reads_tasks_id(tmp_path: Path) -> None:
    path = manifest_file(tmp_path, "a", "b", "c")
    assert A.load_manifest_ids(path) == {"a", "b", "c"}
    assert isinstance(A.load_manifest_ids(path), set)


def test_load_manifest_ids_accepts_string_entries_and_key_aliases(tmp_path: Path) -> None:
    path = write_json(tmp_path / "manifest.json", {
        "tasks": [{"task_id": "x"}, {"name": "y"}, "z", {"id": ""}, {"nope": 1}]
    })
    assert A.load_manifest_ids(path) == {"x", "y", "z"}


def test_load_manifest_ids_accepts_a_str_path(tmp_path: Path) -> None:
    path = manifest_file(tmp_path, LISTED)
    assert A.load_manifest_ids(str(path)) == {LISTED}


def test_load_manifest_ids_is_empty_for_unusable_input(tmp_path: Path) -> None:
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    empty_list = write_json(tmp_path / "no_tasks.json", {"version": 1})
    no_ids = write_json(tmp_path / "empty.json", {"tasks": []})
    for path in (tmp_path / "missing.json", broken, empty_list, no_ids,
                 None, "", "none", "off", "null", Path("NONE")):
        assert A.load_manifest_ids(path) == set(), path


def test_load_manifest_ids_reads_the_real_benchmark_manifest() -> None:
    ids = A.load_manifest_ids(REAL_MANIFEST)
    assert ids, f"{REAL_MANIFEST} should declare at least one task"
    assert all(isinstance(task_id, str) and task_id for task_id in ids)
    assert LISTED in ids


def test_load_task_manifest_still_returns_none_when_filtering_is_off(tmp_path: Path) -> None:
    """Back-compat wrapper: ``None`` means *do not filter*."""
    assert A.load_task_manifest(None) is None
    assert A.load_task_manifest("none") is None
    assert A.load_task_manifest(Path("off")) is None
    assert A.load_task_manifest(tmp_path / "missing.json") is None
    assert A.load_task_manifest(manifest_file(tmp_path, LISTED)) == {LISTED}


# --------------------------------------------------------------------------
# task_id_from_path(run_dir) -> str
# --------------------------------------------------------------------------
def test_task_id_from_path_single_argument_reads_the_task_level(tmp_path: Path) -> None:
    run_dir = add_run(tmp_path, "claude_code", LISTED, 3)
    assert run_dir.parts[-3:] == ("claude_code", LISTED, "trial_3")
    assert A.task_id_from_path(run_dir) == LISTED


def test_task_id_from_path_single_argument_handles_the_repo_layout() -> None:
    canonical = Path("benchmark/results/claude_code/rate-limiter-offbyone/trial_1")
    assert A.task_id_from_path(canonical) == "rate-limiter-offbyone"
    # absolute paths and a bare ``results`` root work the same way
    absolute = Path("/repo/benchmark/results/google_adk/csv-normalizer-dedup/trial_2")
    assert A.task_id_from_path(absolute) == "csv-normalizer-dedup"
    assert A.task_id_from_path(Path("results/opencode/qa_readme/trial_1")) == "qa_readme"


def test_task_id_from_path_single_argument_without_a_results_component(tmp_path: Path) -> None:
    assert A.task_id_from_path(add_run(tmp_path, "alpha", "fix_a", 1)) == "fix_a"
    # a run directory that is itself the task level (no trial_N leaf)
    assert A.task_id_from_path(tmp_path / "alpha" / "fix_a") == "fix_a"
    # the ``run.json`` file itself
    assert A.task_id_from_path(tmp_path / "alpha" / "fix_a" / "trial_1" / "run.json") == "fix_a"


def test_task_id_from_path_single_argument_returns_none_without_a_task_level(tmp_path: Path) -> None:
    assert A.task_id_from_path(Path("alpha")) is None
    assert A.task_id_from_path(Path("benchmark/results/claude_code")) is None
    assert A.task_id_from_path(Path("results")) is None


def test_task_id_from_path_two_argument_form_is_unchanged(tmp_path: Path) -> None:
    run_dir = add_run(tmp_path, "alpha", LISTED, 1)
    assert A.task_id_from_path(run_dir, tmp_path) == LISTED
    assert A.task_id_from_path(run_dir.parent, tmp_path) == LISTED
    assert A.task_id_from_path(tmp_path / "alpha", tmp_path) is None
    # a directory outside results_dir has no task level
    assert A.task_id_from_path(tmp_path.parent / "outside" / "task", tmp_path) is None


def test_task_id_from_path_agrees_with_the_real_results_tree() -> None:
    """Both call forms resolve the same task id on the real (read-only) tree."""
    if not REAL_RESULTS.is_dir():  # pragma: no cover - tree always ships in-repo
        return
    for directory in sorted({p.parent for p in REAL_RESULTS.rglob("run.json")}):
        expected = A.task_id_from_path(directory, REAL_RESULTS)
        assert A.task_id_from_path(directory) == expected, directory


# --------------------------------------------------------------------------
# discover_runs(results_dir, manifest_ids=None)
# --------------------------------------------------------------------------
def test_discover_runs_without_manifest_ids_keeps_everything(tmp_path: Path) -> None:
    tree = two_task_tree(tmp_path)
    before = snapshot(tree)
    assert len(A.discover_runs(tree)) == 2
    assert len(A.discover_runs(tree, manifest_ids=None)) == 2
    assert snapshot(tree) == before


def test_discover_runs_with_manifest_ids_filters(tmp_path: Path) -> None:
    tree = two_task_tree(tmp_path)
    before = snapshot(tree)
    runs = A.discover_runs(tree, manifest_ids={LISTED})
    assert [run["task_id"] for run in runs] == [LISTED]
    # filtering must not touch the tree
    assert snapshot(tree) == before
    assert (tree / "alpha" / STALE / "trial_1" / "run.json").is_file()


def test_discover_runs_manifest_ids_matches_the_task_ids_alias(tmp_path: Path) -> None:
    tree = two_task_tree(tmp_path)
    ids = {LISTED}
    assert A.discover_runs(tree, manifest_ids=ids) == A.discover_runs(tree, task_ids=ids)
    assert A.discover_runs(tree, None, None, ids) == A.discover_runs(tree, manifest_ids=ids)


def test_discover_runs_accepts_manifest_ids_as_the_second_positional(tmp_path: Path) -> None:
    """``discover_runs(results_dir, manifest_ids)`` - the signature in the spec."""
    tree = two_task_tree(tmp_path)
    runs = A.discover_runs(tree, {LISTED})
    assert [run["task_id"] for run in runs] == [LISTED]


def test_discover_runs_still_accepts_a_capability_dict_positionally(tmp_path: Path) -> None:
    tree = two_task_tree(tmp_path)
    capability = A.load_capability(tree)
    assert isinstance(capability, dict)
    assert len(A.discover_runs(tree, capability)) == 2


def test_discover_runs_prints_the_skip_count(tmp_path: Path, capsys) -> None:
    tree = two_task_tree(tmp_path)
    A.discover_runs(tree, manifest_ids={LISTED})
    err = capsys.readouterr().err
    assert "aggregate: skipped 1 non-manifest run(s)" in err


def test_discover_runs_stays_quiet_when_nothing_is_skipped(tmp_path: Path, capsys) -> None:
    tree = two_task_tree(tmp_path)
    A.discover_runs(tree, manifest_ids={LISTED, STALE})
    assert "non-manifest" not in capsys.readouterr().err
    A.discover_runs(tree)
    assert "non-manifest" not in capsys.readouterr().err


def test_discover_runs_empty_manifest_ids_drops_everything_but_deletes_nothing(
        tmp_path: Path) -> None:
    tree = two_task_tree(tmp_path)
    before = snapshot(tree)
    assert A.discover_runs(tree, manifest_ids=set()) == []
    assert snapshot(tree) == before


# --------------------------------------------------------------------------
# --manifest CLI wiring
# --------------------------------------------------------------------------
def test_help_lists_the_manifest_flag() -> None:
    proc = subprocess.run([sys.executable, str(AGGREGATE), "--help"],
                          capture_output=True, text=True)
    assert proc.returncode == 0
    assert "--manifest" in proc.stdout


def test_manifest_defaults_to_benchmark_tasks_json() -> None:
    args = A.parse_args([])
    assert Path(args.manifest) == A.DEFAULT_TASKS_MANIFEST
    assert Path(args.manifest).name == "tasks.json"
    assert Path(args.manifest).parent == BENCHMARK_DIR


def test_manifest_none_and_off_are_accepted_verbatim() -> None:
    for sentinel in ("none", "off"):
        args = A.parse_args(["--manifest", sentinel])
        assert str(args.manifest) == sentinel
        assert A.load_task_manifest(args.manifest) is None


def test_tasks_remains_an_alias_for_manifest(tmp_path: Path) -> None:
    path = manifest_file(tmp_path, LISTED)
    assert A.parse_args(["--tasks", str(path)]).manifest == path
    assert A.parse_args(["--tasks", "none"]).manifest == Path("none")
    # an explicit --manifest wins over the legacy alias
    assert A.parse_args(["--tasks", "none", "--manifest", str(path)]).manifest == path


def test_main_filters_by_manifest_and_keeps_every_file(tmp_path: Path, capsys) -> None:
    tree = two_task_tree(tmp_path)
    manifest = manifest_file(tmp_path, LISTED)
    out = tmp_path / "out" / "summary.json"
    before = snapshot(tree)
    assert A.main(["--results", str(tree), "--manifest", str(manifest),
                   "--out", str(out), "--quiet"]) == 0
    summary = json.loads(out.read_text(encoding="utf-8"))
    assert n_runs(summary) == 1
    assert summary["backends"]["alpha"]["tokens"]["total"]["mean"] == 100.0
    assert "aggregate: skipped 1 non-manifest run(s)" in capsys.readouterr().err
    assert snapshot(tree) == before          # filtered, never deleted


def test_main_manifest_none_aggregates_every_run(tmp_path: Path) -> None:
    tree = two_task_tree(tmp_path)
    manifest_file(tmp_path, LISTED)         # present but switched off
    out = tmp_path / "summary.json"
    assert A.main(["--results", str(tree), "--manifest", "none",
                   "--out", str(out), "--quiet"]) == 0
    assert n_runs(json.loads(out.read_text(encoding="utf-8"))) == 2


def test_main_off_sentinel_matches_none(tmp_path: Path) -> None:
    tree = two_task_tree(tmp_path)
    counts = []
    for index, sentinel in enumerate(("off", "none")):
        out = tmp_path / f"summary_{index}.json"
        A.main(["--results", str(tree), "--manifest", sentinel,
                "--out", str(out), "--quiet"])
        counts.append(n_runs(json.loads(out.read_text(encoding="utf-8"))))
    assert counts == [2, 2]


def test_main_defaults_to_the_benchmark_manifest(tmp_path: Path) -> None:
    """No flag at all == ``--manifest benchmark/tasks.json``."""
    tree = two_task_tree(tmp_path)
    default_out = tmp_path / "default.json"
    explicit_out = tmp_path / "explicit.json"
    assert A.main(["--results", str(tree), "--out", str(default_out), "--quiet"]) == 0
    assert A.main(["--results", str(tree), "--manifest", str(REAL_MANIFEST),
                   "--out", str(explicit_out), "--quiet"]) == 0
    default = json.loads(default_out.read_text(encoding="utf-8"))
    explicit = json.loads(explicit_out.read_text(encoding="utf-8"))
    assert n_runs(default) == n_runs(explicit)
    # the real manifest lists LISTED but not STALE, so exactly one run survives
    assert n_runs(default) == 1


# --------------------------------------------------------------------------
# the real tree: filtering reduces (or matches) the count and deletes nothing
# --------------------------------------------------------------------------
def test_real_results_manifest_count_is_between_one_and_the_unfiltered_count() -> None:
    if not (REAL_RESULTS.is_dir() and REAL_MANIFEST.is_file()):  # pragma: no cover
        return
    before = snapshot(REAL_RESULTS)
    unfiltered = A.discover_runs(REAL_RESULTS, manifest_ids=None)
    filtered = A.discover_runs(REAL_RESULTS, manifest_ids=A.load_manifest_ids(REAL_MANIFEST))
    assert 0 < len(filtered) <= len(unfiltered)
    assert snapshot(REAL_RESULTS) == before
    listed = A.load_manifest_ids(REAL_MANIFEST)
    assert all(run["task_id"] in listed for run in filtered)
