"""Tests for code-graph readiness, learned from MCP -- never from a file.

The behaviour under test is the one that was missing: an agent must be told
whether the connected code graph actually covers its workspace, and it must
never read "empty result" as "absent symbol" when the truth is "not indexed".

Coverage arrives in-band, as the plain dict the code-graph server reports on its
own catalog entry. There is no manifest, no shared mount and no path to configure,
so these tests touch no filesystem and no network.

Run:  cd claudio-core && PYTHONPATH="$(pwd)/src" python3 -m pytest tests/test_codegraph.py -q
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

# Self-contained bootstrap: work with a bare interpreter, no installed package,
# no pydantic models, no fixtures that need a plugin.
SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from claudio_core.codegraph import (  # noqa: E402
    STATUS_COVERED,
    STATUS_NOT_COVERED,
    STATUS_UNKNOWN,
    GraphReadiness,
    readiness_from_status,
    render_readiness_block,
    status_unknown,
)
from claudio_core.instructions import render_code_graph_status  # noqa: E402

WS = "/work/repos/clams"

# A catalog entry that advertises a code graph (two lexicon hits: "callers",
# "symbol") and reports its own coverage in-band.
GRAPH_WITH_STATUS = [
    {
        "id": "graph",
        "tools": ["graph_find_callers", "graph_symbol_search"],
        "resources": [],
        "status": {
            "tracked_repos": ["/work/repos/clams"],
            "failed_repos": [],
            "generated_at": "2026-11-27T00:00:00Z",
        },
    }
]
GRAPH_WITHOUT_STATUS = [
    {"id": "graph", "tools": ["graph_find_callers", "graph_symbol_search"], "resources": []}
]
NOT_A_GRAPH_SERVER = [{"id": "weather", "tools": ["forecast"], "resources": []}]


def status(tracked, failed=(), generated_at="2026-11-27T00:00:00Z"):
    return {"tracked_repos": list(tracked), "failed_repos": list(failed),
            "generated_at": generated_at}


# --------------------------------------------------------------- readiness_from_status --

def test_covered_when_workspace_is_inside_a_tracked_repo():
    r = readiness_from_status(WS, status(["/work/repos/clams"]))
    assert r.status == STATUS_COVERED
    assert r.usable is True
    assert r.repo == "/work/repos/clams"


def test_covered_for_a_nested_workspace_and_longest_repo_wins():
    r = readiness_from_status("/work/repos/clams/src/app",
                              status(["/work/repos", "/work/repos/clams"]))
    assert r.status == STATUS_COVERED and r.repo == "/work/repos/clams"


def test_not_covered_when_the_server_reports_other_repos():
    r = readiness_from_status("/work/elsewhere",
                              status(["/work/repos/clams"], ["/work/gone:absent"]))
    assert r.status == STATUS_NOT_COVERED
    assert r.usable is False
    assert "/work/gone:absent" in r.failed


def test_not_covered_when_the_server_explicitly_tracks_nothing():
    r = readiness_from_status(WS, status([]))
    assert r.status == STATUS_NOT_COVERED


def test_unknown_from_none():
    # The whole point: no report is NOT evidence that the workspace is unindexed.
    r = readiness_from_status(WS, None)
    assert r.status == STATUS_UNKNOWN
    assert r.usable is False


def test_unknown_from_garbage_when_status_is_not_a_dict():
    for garbage in ("a string", 42, ["/work/repos/clams"], ["not", "a", "dict"], object(), b"bytes"):
        r = readiness_from_status(WS, garbage)
        assert r.status == STATUS_UNKNOWN, garbage


def test_unknown_from_dict_without_a_tracked_repos_key():
    assert readiness_from_status(WS, {"generated_at": "x"}).status == STATUS_UNKNOWN


def test_malformed_values_degrade_instead_of_raising():
    r = readiness_from_status(WS, {"tracked_repos": "a-string-not-a-list",
                                   "failed_repos": None})
    assert r.status in (STATUS_UNKNOWN, STATUS_NOT_COVERED)
    assert r.tracked == () and r.failed == ()


def test_readiness_from_status_never_raises_on_any_input():
    for bad in (None, {}, [], "x", 0, 1.5, set(), {"tracked_repos": [None, 1]},
                {"tracked_repos": [""]}, {"tracked_repos": ["/"], "failed_repos": "x"}):
        assert isinstance(readiness_from_status(WS, bad), GraphReadiness)


def test_status_unknown_helper_builds_the_fail_safe_verdict():
    r = status_unknown(WS)
    assert r.status == STATUS_UNKNOWN and r.workspace == WS and not r.usable


# --------------------------------------------------------------------------- render --

def test_render_covered_says_results_are_trustworthy():
    block = render_readiness_block(readiness_from_status(WS, status([WS])))
    assert "covers your current workspace" in block and WS in block


def test_render_not_covered_carries_the_not_indexed_warning():
    block = render_readiness_block(
        readiness_from_status("/work/elsewhere", status(["/work/repos/clams"])))
    # The exact trap: an empty graph result must not become a finding about code.
    assert "not indexed" in block
    assert "does not exist" in block
    assert "Never report an empty graph result as a finding" in block
    assert "file tools" in block
    assert "Do not claim you queried the graph" in block


def test_render_unknown_never_overclaims_and_asks_for_verification():
    block = render_readiness_block(readiness_from_status(WS, None))
    assert "unknown" in block
    assert "does NOT cover" not in block          # must not claim absence
    assert "verify" in block.lower() or "Before trusting an empty result" in block


def test_rendered_text_never_names_a_vendor():
    cases = [
        readiness_from_status(WS, status([WS])),
        readiness_from_status(WS, status(["/other"], ["/gone:absent"])),
        readiness_from_status(WS, None),
        readiness_from_status(WS, "garbage"),
        status_unknown(WS),
    ]
    for r in cases:
        lowered = render_readiness_block(r).lower()
        assert "gortex" not in lowered
        assert "manifest" not in lowered


# ------------------------------------------------------------------ prompt wiring --

def test_render_returns_empty_when_no_code_graph_server_in_the_catalog():
    assert render_code_graph_status(Path(WS), []) == ""
    assert render_code_graph_status(Path(WS), None) == ""
    assert render_code_graph_status(Path(WS), NOT_A_GRAPH_SERVER) == ""


def test_render_emits_a_block_when_a_code_graph_server_is_connected():
    assert render_code_graph_status(Path(WS), GRAPH_WITH_STATUS) != ""


def test_connected_server_without_a_report_renders_unknown_not_absence():
    block = render_code_graph_status(Path(WS), GRAPH_WITHOUT_STATUS)
    assert "unknown" in block and "does NOT cover" not in block


def test_rendered_block_follows_the_servers_own_report():
    assert "covers your current workspace" in render_code_graph_status(Path(WS), GRAPH_WITH_STATUS)
    other = [dict(GRAPH_WITH_STATUS[0], status=status(["/somewhere/else"]))]
    assert "not indexed" in render_code_graph_status(Path(WS), other)


def test_render_code_graph_status_takes_no_manifest_path():
    params = inspect.signature(render_code_graph_status).parameters
    assert list(params) == ["workspace", "mcp_entries"]
    assert not any("manifest" in name for name in params)


def test_no_file_side_channel_names_survive_anywhere():
    import claudio_core.codegraph as cg
    import claudio_core.instructions as ins
    for module in (cg, ins):
        source = Path(module.__file__).read_text().lower()
        assert "gortex" not in source
        assert "manifest" not in source
