"""Issue #58 -- read fast paths: one call instead of a grep-then-read chain.

The cheap wins in the plan are the calls that stop a turn from existing: a
multi-file read that costs one turn instead of N, and a search that returns its
own context instead of forcing a follow-up read. Those tools are also the ones
the Increment 1 scheduler is allowed to fan out, so this file pins both halves:
the fast paths behave, and they stay on the parallel side of the boundary.

Run:  cd claudio-backend && PYTHONPATH=src:../claudio-core/src \\
        python -m pytest tests/test_issue58_fastpaths.py -q
"""
from __future__ import annotations

import asyncio

from claudio_core.context import RunConcurrency, ToolContext
from claudio_core.settings import AppConfig
from claudio_core.tool_batch import is_parallel_safe, reduce_tool_context, run_tool_batch
from claudio_core.tools.files import batch_read, list_dir
from claudio_core.tools.registry import TOOL_META
from claudio_core.tools.search import locate_symbol, search_with_context

FAST_PATHS = ("batch_read", "search_with_context", "locate_symbol")


def _ctx(tmp_path, bound=4):
    return ToolContext(
        config=AppConfig(),
        session_id="s1",
        workspace=tmp_path,
        approval_mode="off",
        db=None,
        run_id="r1",
        concurrency=RunConcurrency.create(bound),
    )


def _tree(tmp_path):
    (tmp_path / "a.py").write_text("def alpha():\n    return 1\n\n\ndef beta():\n    return 2\n")
    (tmp_path / "b.py").write_text("class Gamma:\n    def alpha(self):\n        pass\n")
    sub = tmp_path / "pkg"
    sub.mkdir(exist_ok=True)
    (sub / "c.py").write_text("NEEDLE = 42\n")
    return tmp_path


# ---------------------------------------------------------------------------
# the tools themselves
# ---------------------------------------------------------------------------


def test_batch_read_returns_every_file_in_order(tmp_path):
    _tree(tmp_path)
    ctx = _ctx(tmp_path)
    res = asyncio.run(batch_read(ctx, ["a.py", "b.py", "pkg/c.py"]))
    assert res["ok"] is True
    assert [f["path"] for f in res["files"]] == ["a.py", "b.py", "pkg/c.py"]
    assert all(f["ok"] for f in res["files"])
    assert "def alpha" in res["files"][0]["content"]
    assert res["used_chars"] <= res["budget_chars"]


def test_batch_read_reports_missing_files_without_failing_the_batch(tmp_path):
    _tree(tmp_path)
    ctx = _ctx(tmp_path)
    res = asyncio.run(batch_read(ctx, ["a.py", "nope.py"]))
    assert res["ok"] is True
    assert res["files"][0]["ok"] is True
    assert res["files"][1]["ok"] is False and res["files"][1]["error"] == "no such file"


def test_batch_read_shares_one_budget(tmp_path):
    _tree(tmp_path)
    (tmp_path / "big.txt").write_text("x" * 5000)
    ctx = _ctx(tmp_path)
    res = asyncio.run(batch_read(ctx, ["big.txt", "big.txt"], max_total_chars=1000))
    assert res["budget_chars"] == 1000
    # The clip marker can run a little past the budget; the point is the batch
    # is bounded by it and the second file is refused, not silently truncated.
    assert res["used_chars"] <= 1000 + 64
    assert res["files"][1].get("error") == "budget exhausted"


def test_batch_read_rejects_empty_list(tmp_path):
    ctx = _ctx(tmp_path)
    assert asyncio.run(batch_read(ctx, [])).get("ok") is False


def test_search_with_context_carries_the_lines_it_found(tmp_path):
    _tree(tmp_path)
    ctx = _ctx(tmp_path)
    res = asyncio.run(search_with_context(ctx, "NEEDLE"))
    assert res["ok"] is True and res["match_count"] >= 1
    hit = res["matches"][0]
    assert hit["file"].endswith("c.py") and hit["line"] == 1
    assert "NEEDLE" in hit["excerpt"]


def test_search_with_context_glob_and_regex(tmp_path):
    _tree(tmp_path)
    ctx = _ctx(tmp_path)
    res = asyncio.run(search_with_context(ctx, r"def \w+", regex=True, glob="**/*.py"))
    assert res["ok"] is True
    assert res["match_count"] >= 3
    bad = asyncio.run(search_with_context(ctx, "(", regex=True))
    assert bad["ok"] is False and "Invalid regex" in bad["error"]


def test_locate_symbol_finds_definitions_not_call_sites(tmp_path):
    _tree(tmp_path)
    (tmp_path / "d.py").write_text("alpha()  # call site only\n")
    ctx = _ctx(tmp_path)
    res = asyncio.run(locate_symbol(ctx, "alpha"))
    assert res["ok"] is True
    files = " ".join(str(m.get("file", "")) for m in res["definitions"])
    assert "a.py" in files
    assert "d.py" not in files, "call site reported as a definition"


# ---------------------------------------------------------------------------
# fast paths and the parallel scheduler
# ---------------------------------------------------------------------------


def test_fast_paths_are_on_the_parallel_side_of_the_boundary(tmp_path):
    ctx = _ctx(tmp_path)
    for name in FAST_PATHS:
        assert TOOL_META[name].concurrency == "read_only"
        assert is_parallel_safe(ctx, name)


def test_fast_paths_run_concurrently_in_one_batch(tmp_path):
    _tree(tmp_path)
    ctx = _ctx(tmp_path)
    reqs = [
        {"name": "batch_read", "args": {"paths": ["a.py", "b.py"]}},
        {"name": "search_with_context", "args": {"pattern": "NEEDLE"}},
        {"name": "locate_symbol", "args": {"symbol": "alpha"}},
        {"name": "list_dir", "args": {"path": "."}},
    ]
    out = asyncio.run(run_tool_batch(ctx, reqs))
    assert [r.index for r in out.results] == [0, 1, 2, 3]
    assert all(r.status == "ok" for r in out.results), [r.payload for r in out.results]
    assert out.parallel_count == 4 and out.sequential_count == 0
    # The reduced block is canonical and reproducible.
    reduced = reduce_tool_context(out.results, turn_id="t")
    again = reduce_tool_context(list(reversed(out.results)), turn_id="t")
    assert reduced.text == again.text
    assert reduced.payload["counts"]["ok"] == 4


def test_fast_paths_are_registered_as_tools():
    from claudio_core.tools.registry import TOOL_FUNCTIONS

    for name in FAST_PATHS:
        assert name in TOOL_FUNCTIONS and name in TOOL_META
