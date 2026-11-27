"""The plan/act audit driven by the *live* MCP catalog (Fix B, vendor-free).

Why this file exists
--------------------
The audit used to know one product by name: the family that catches "I said I
would query the graph and then did not" was built from a hard-coded vendor
string, so it silently did nothing for every other code-graph server and named
a connector the deployment might not even run.

These tests pin the replacement contract: families are **derived** from the
catalog the servers advertise (``mcp_capability.detect``), one per connected
server, named by the configured server id and satisfied only by that server's
real tool names. Nothing here names a vendor, and nothing here is async or needs
pydantic -- the audit lives in the framework-free half of Claudio.

Run:  cd claudio-core && PYTHONPATH="$(pwd)/src" python3 -m pytest tests/test_planact_derived.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

# Self-contained bootstrap: works with or without an installed claudio-core.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from claudio_core.planact import (  # noqa: E402
    BUILTIN_FAMILIES,
    CODE_GRAPH_FAMILY,
    audit_run,
    derive_families,
    planned_families,
    render_planact_nudge,
)

GRAPH = CODE_GRAPH_FAMILY.name

# A catalog in exactly the shape the engine advertises: the server id is whatever
# the operator configured, the tool names are the real prefixed ones.
CATALOG = [
    {
        "id": "graph-index",
        "capability": "code_graph",
        "tools": [
            "mcp__graph_index__find_callers",
            "mcp__graph_index__change_impact",
        ],
        "instructions": "Answers structural questions about indexed code.",
    }
]

FAMILIES = derive_families(CATALOG) + BUILTIN_FAMILIES


def _audit(text: str, called: list[str]):
    return audit_run(text, called, FAMILIES)


# ----------------------------------------------------------- derived families --
def test_one_family_per_detected_server_named_by_configured_id():
    families = derive_families(CATALOG)
    assert [f.name for f in families] == ["graph-index"]
    family = families[0]
    # Triggers: the server's real prefixed tool names plus generic narration.
    assert "mcp__graph_index__find_callers" in family.triggers
    assert "code graph" in family.triggers
    assert "callers" in family.triggers
    # Satisfaction: the tools this server actually exposes.
    assert family.matches_tool("mcp__graph_index__find_callers")
    assert not family.matches_tool("read_file")


def test_detection_is_by_advertised_capability_not_by_id():
    # No capability override, no vendor name: enough of the server's own tool
    # names / descriptions carry generic code-graph vocabulary, so it qualifies
    # purely on what it advertises.
    entries = [
        {
            "id": "codescope",
            "tools": [
                "mcp__codescope__search_symbols",
                "mcp__codescope__list_dependencies",
            ],
            "tool_descriptions": {
                "mcp__codescope__search_symbols": "Find symbols across the codebase",
            },
        }
    ]
    families = derive_families(entries)
    assert [f.name for f in families] == ["codescope"]


def test_catalog_with_no_code_graph_server_derives_nothing():
    entries = [{"id": "weather", "tools": ["mcp__weather__get_forecast"]}]
    assert derive_families(entries) == ()


# --------------------------------------------------- promise kept: clean audit --
def test_promise_plus_call_to_a_prefixed_tool_is_clean():
    report = _audit(
        "Let me check the code graph for the callers of this function.",
        ["mcp__graph_index__find_callers"],
    )
    assert report.clean, report.unfulfilled
    assert render_planact_nudge(report) == ""


def test_promise_named_by_server_id_and_its_tool_is_clean():
    report = _audit(
        "I'll query graph-index with mcp__graph_index__change_impact first.",
        ["mcp__graph_index__change_impact"],
    )
    assert report.clean, report.unfulfilled


# ------------------------------------------------- promise broken: unfulfilled --
def test_promise_with_no_call_is_unfulfilled_and_names_the_server_id():
    report = _audit("Let me check the code graph for the callers.", ["read_file"])
    assert "graph-index" in report.unfulfilled
    nudge = render_planact_nudge(report)
    assert "PLAN/ACT MISMATCH" in nudge
    # The nudge names the connector from config, not a product we guessed.
    assert "graph-index" in nudge


def test_an_unrelated_call_does_not_satisfy_the_derived_server():
    # "Only audited against tools that server actually exposes": a web search is
    # not a graph call, however confident the narration sounds.
    report = _audit(
        "Let me check the code graph for the callers.",
        ["web_search", "read_file"],
    )
    assert "graph-index" in report.unfulfilled


def test_promise_of_one_server_is_not_paid_by_another():
    entries = [
        {"id": "graph-index", "capability": "code_graph",
         "tools": ["mcp__graph_index__find_callers"]},
        {"id": "codescope", "capability": "code_graph",
         "tools": ["mcp__codescope__find_usages"]},
    ]
    families = derive_families(entries) + BUILTIN_FAMILIES
    report = audit_run(
        "Let me query graph-index for the callers.",
        ["mcp__codescope__find_usages"],
        families,
    )
    assert "graph-index" in report.unfulfilled
    assert "codescope" not in report.unfulfilled


# ------------------------------------------- not connected: nothing to audit --
def test_a_server_that_is_not_connected_is_not_audited():
    # The narration names a connector that is not in the catalog. No family is
    # derived for it, so it can never be flagged -- no false positive against a
    # tool the model does not have.
    report = _audit(
        "Let me query the legacy-graph server for the callers.",
        ["mcp__graph_index__find_callers"],
    )
    assert "legacy-graph" not in report.unfulfilled
    assert "graph-index" not in report.unfulfilled


def test_honest_disclosure_about_the_derived_server_stays_clean():
    report = _audit(
        "The code graph was unavailable, so I used the file tools.",
        ["read_file"],
    )
    assert report.clean, report.unfulfilled


# ------------------------------------- generic narration with no catalog at all --
def test_generic_triggers_work_with_no_catalog():
    # No catalog wired in: the builtin family still catches the classic lie.
    assert derive_families(None) == ()
    report = audit_run("Let me check the code graph for the callers.", [])
    assert report.unfulfilled == {GRAPH}
    assert "code graph" in render_planact_nudge(report)


def test_generic_triggers_cover_callers_index_and_symbol_prose():
    for text in (
        "Let me look up the callers of this function.",
        "I should run a change impact analysis.",
        "I'll check the index coverage first.",
        "Now I will search for the symbol definitions.",
    ):
        assert GRAPH in planned_families(text), text


def test_generic_triggers_are_satisfied_by_any_graphish_tool_name():
    # Without a catalog the audit can only match vocabulary, and it does.
    report = audit_run(
        "Let me check the code graph for the callers.",
        ["mcp__anything__graph_find_callers"],
    )
    assert report.clean
