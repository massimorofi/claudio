"""Code-graph wiring tests: the pure helpers, against a realistic MCP catalog.

This file deliberately does **not** import ``google.adk`` or
``claudio_backend.engine.adk_engine`` -- ADK is not installed in the fast gate,
and it does not read ``claudio-backend/config/config.yaml`` either (that file is
gitignored, which is why the old issue-54 tests cannot run here). What it does
test is the *data contract* the engine now fills in and the framework-free
helpers consume, using a fake catalog shaped exactly like the one
``_ensure_mcp_locked`` builds:

    {"id", "tools", "resources", "instructions", "tool_descriptions",
     "capability", "status"}

The 21 tool names below are what a code-intelligence server of this shape
advertises (prefixed with the operator's configured server id, the way ADK
prefixes function declarations). Everything is derived from what the server
advertises -- there is no vendor string in Claudio to match against, and these
tests fail if one ever appears.

Run:  cd claudio-backend && python3 -m pytest tests/test_mcp_codegraph_wiring.py -q
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

CORE_SRC = Path(__file__).resolve().parents[2] / "claudio-core" / "src"
try:  # self-contained: bootstrap core src/ when the package is not installed
    import claudio_core  # noqa: F401
except ImportError:  # pragma: no cover - depends on how pytest is invoked
    sys.path.insert(0, str(CORE_SRC))
    import claudio_core  # noqa: F401

from claudio_core.codegraph import (
    STATUS_COVERED,
    STATUS_NOT_COVERED,
    STATUS_UNKNOWN,
    readiness_from_status,
)
from claudio_core.instructions import (
    render_code_discovery_policy,
    render_code_graph_status,
)
from claudio_core.mcp_capability import CAPABILITY_CODE_GRAPH, detect
from claudio_core.mcp_status import parse_status_payload, pick_status_tool

VENDOR = "gor" + "tex"  # built, never written, so nothing here needs the name

SERVER_ID = "codegraph"
WORKSPACE = "/srv/work/api/src/app"

#: The 21 tools a code-graph server of this shape advertises, already prefixed
#: with the configured server id (this is what the model can actually call).
RAW_TOOLS = [
    "find_symbols",
    "get_definition",
    "get_references",
    "find_callers",
    "find_callees",
    "list_dependencies",
    "list_dependents",
    "change_impact",
    "get_call_graph",
    "get_architecture_map",
    "search_code",
    "read_file_region",
    "list_repos",
    "get_workspace_capabilities",
    "trace_data_flow",
    "find_usages",
    "get_symbol_summary",
    "cross_references",
    "blame_history",
    "reindex_workspace",
    "watch_paths",
]
TOOL_NAMES = [f"{SERVER_ID}_{t}" for t in RAW_TOOLS]

TOOL_DESCRIPTIONS: dict[str, str] = {
    f"{SERVER_ID}_find_symbols": "Find symbol definitions by name across indexed repos",
    f"{SERVER_ID}_get_definition": "Return the definition site of a symbol",
    f"{SERVER_ID}_get_references": "Return all references to a symbol",
    f"{SERVER_ID}_find_callers": "List the callers of a function or method",
    f"{SERVER_ID}_find_callees": "List the callees of a function or method",
    f"{SERVER_ID}_list_dependencies": "List the packages a module depends on",
    f"{SERVER_ID}_list_dependents": "List what depends on a module",
    f"{SERVER_ID}_change_impact": "Change impact analysis for a symbol or file",
    f"{SERVER_ID}_get_call_graph": "Return the call graph around a symbol",
    f"{SERVER_ID}_get_architecture_map": "Architecture map of a repository",
    f"{SERVER_ID}_search_code": "Full-text code search across the index",
    f"{SERVER_ID}_read_file_region": "Read a region of a file from the index",
    f"{SERVER_ID}_list_repos": "List the repositories known to the server",
    f"{SERVER_ID}_get_workspace_capabilities": (
        "Report index coverage and workspace capabilities: tracked repositories, "
        "failed repositories and when the index was generated"
    ),
    f"{SERVER_ID}_trace_data_flow": "Trace data flow between two symbols",
    f"{SERVER_ID}_find_usages": "Find usages of a symbol",
    f"{SERVER_ID}_get_symbol_summary": "Summarize a symbol and its neighbours",
    f"{SERVER_ID}_cross_references": "Cross-references for a source position",
    f"{SERVER_ID}_blame_history": "Line history for a file",
    f"{SERVER_ID}_reindex_workspace": "Rebuild the index for a workspace",
    f"{SERVER_ID}_watch_paths": "Watch paths for source changes",
}

SERVER_INSTRUCTIONS = (
    "This server answers structural questions about code using a prebuilt code "
    "graph. Prefer graph tools for navigation; call "
    "get_workspace_capabilities first to see which repositories are indexed."
)

STATUS_ANSWER = {
    "tracked_repos": ["/srv/work/api", "/srv/work/web"],
    "failed_repos": ["/srv/work/legacy"],
    "generated_at": "2026-11-27T09:12:03Z",
}


def catalog_entry(**overrides: Any) -> dict[str, Any]:
    """A catalog entry exactly as ``_ensure_mcp_locked`` now builds one."""
    entry: dict[str, Any] = {
        "id": SERVER_ID,
        "tools": list(TOOL_NAMES),
        "resources": ["registry://repos"],
        "instructions": SERVER_INSTRUCTIONS,
        "tool_descriptions": dict(TOOL_DESCRIPTIONS),
        "capability": "",
        "status": None,
    }
    entry.update(overrides)
    return entry


SKILL_REGISTRY_ENTRY = {
    "id": "SkillMD.com",
    "tools": ["SkillMD_com_skillmd_search", "SkillMD_com_skillmd_get"],
    "resources": [],
    "instructions": "Search the SkillMD registry of Agent Skills.",
    "tool_descriptions": {
        "SkillMD_com_skillmd_search": "Search the SkillMD registry of Agent Skills.",
        "SkillMD_com_skillmd_get": "Fetch one skill by owner/name slug.",
    },
    "capability": "",
    "status": None,
}


# --------------------------------------------------------------------------
# the fake catalog is the shape the engine produces
# --------------------------------------------------------------------------


def test_catalog_has_21_advertised_tools_and_all_metadata_keys():
    entry = catalog_entry()
    assert len(entry["tools"]) == 21
    assert len(set(entry["tools"])) == 21
    assert set(entry) == {
        "id",
        "tools",
        "resources",
        "instructions",
        "tool_descriptions",
        "capability",
        "status",
    }
    assert set(entry["tool_descriptions"]) == set(entry["tools"])


# --------------------------------------------------------------------------
# step 1: find the status tool from what is advertised
# --------------------------------------------------------------------------


def test_status_tool_is_found_in_a_realistic_catalog():
    entry = catalog_entry()
    assert (
        pick_status_tool(entry["tools"], entry["tool_descriptions"])
        == f"{SERVER_ID}_get_workspace_capabilities"
    )


def test_status_tool_is_found_from_the_name_when_descriptions_are_missing():
    # Producers that cannot supply descriptions still work.
    assert pick_status_tool(TOOL_NAMES, {}) == f"{SERVER_ID}_get_workspace_capabilities"


def test_skill_registry_catalog_has_no_status_tool():
    assert (
        pick_status_tool(
            SKILL_REGISTRY_ENTRY["tools"], SKILL_REGISTRY_ENTRY["tool_descriptions"]
        )
        is None
    )


def test_mutating_reindex_tool_does_not_win():
    entry = catalog_entry()
    assert "reindex_workspace" in " ".join(entry["tools"])
    assert pick_status_tool(
        [f"{SERVER_ID}_reindex_workspace", f"{SERVER_ID}_index_status"], {}
    ) == f"{SERVER_ID}_index_status"


# --------------------------------------------------------------------------
# step 2: parse what that tool answers
# --------------------------------------------------------------------------


def test_bare_json_answer_parses():
    assert parse_status_payload(json.dumps(STATUS_ANSWER)) == STATUS_ANSWER


def test_fenced_and_log_prefixed_answers_parse():
    fenced = "```json\n" + json.dumps(STATUS_ANSWER) + "\n```"
    assert parse_status_payload(fenced) == STATUS_ANSWER
    logged = (
        "2026-11-27 09:12:03 INFO  index build finished\n"
        "2026-11-27 09:12:03 WARN  /srv/work/legacy: timeout\n"
        + json.dumps(STATUS_ANSWER)
    )
    assert parse_status_payload(logged) == STATUS_ANSWER


def test_content_block_answer_parses():
    raw = [{"type": "text", "text": json.dumps(STATUS_ANSWER)}]
    assert parse_status_payload(raw) == STATUS_ANSWER


def test_unusable_answers_are_none_and_render_as_unknown():
    for raw in ("", "capabilities: unknown", "[1,2]", {"unrelated": True}, None):
        assert parse_status_payload(raw) is None


# --------------------------------------------------------------------------
# step 3: capability detection from the same catalog
# --------------------------------------------------------------------------


def test_catalog_is_detected_as_a_code_graph():
    servers = detect([catalog_entry()])
    assert len(servers) == 1
    assert servers[0].id == SERVER_ID
    assert servers[0].source in ("tools", "instructions")
    assert servers[0].tools == tuple(TOOL_NAMES)


def test_operator_override_qualifies_a_quiet_server():
    quiet = {
        "id": "internal-tool",
        "tools": ["internal_tool_a", "internal_tool_b"],
        "resources": [],
        "capability": CAPABILITY_CODE_GRAPH,
    }
    servers = detect([quiet])
    assert [s.id for s in servers] == ["internal-tool"]
    assert servers[0].source == "capability"


def test_skill_registry_is_not_a_code_graph():
    assert not detect([SKILL_REGISTRY_ENTRY])


# --------------------------------------------------------------------------
# step 4: readiness from the probed status
# --------------------------------------------------------------------------


def test_readiness_covered_when_the_probe_succeeded():
    entry = catalog_entry(status=parse_status_payload(json.dumps(STATUS_ANSWER)))
    readiness = readiness_from_status(WORKSPACE, entry["status"])
    assert readiness.status == STATUS_COVERED
    assert readiness.repo == "/srv/work/api"
    assert readiness.generated_at == "2026-11-27T09:12:03Z"


def test_readiness_not_covered_for_an_untracked_workspace():
    entry = catalog_entry(status=dict(STATUS_ANSWER))
    readiness = readiness_from_status("/srv/work/other", entry["status"])
    assert readiness.status == STATUS_NOT_COVERED


def test_readiness_unknown_when_the_probe_found_nothing():
    entry = catalog_entry(status=None)
    assert readiness_from_status(WORKSPACE, entry["status"]).status == STATUS_UNKNOWN


# --------------------------------------------------------------------------
# step 5: what the prompt says
# --------------------------------------------------------------------------


def test_prompt_reports_covered_workspace_with_real_paths():
    entry = catalog_entry(status=dict(STATUS_ANSWER))
    block = render_code_graph_status(Path(WORKSPACE), [entry])
    assert "# Code graph status" in block
    assert "/srv/work/api" in block
    assert "2026-11-27T09:12:03Z" in block
    assert "unknown" not in block.lower()


def test_prompt_says_unknown_when_the_probe_failed():
    entry = catalog_entry(status=None)
    block = render_code_graph_status(Path(WORKSPACE), [entry])
    assert "unknown" in block
    assert "did not report" in block


def test_policy_names_the_configured_id_and_real_tool_names():
    policy = render_code_discovery_policy([catalog_entry()])
    assert SERVER_ID in policy
    assert f"{SERVER_ID}_find_callers" in policy
    assert f"{SERVER_ID}_get_workspace_capabilities" in policy


def test_no_policy_and_no_status_block_without_a_code_graph():
    entries = [SKILL_REGISTRY_ENTRY]
    assert render_code_discovery_policy(entries) == ""
    assert render_code_graph_status(Path(WORKSPACE), entries) == ""


# --------------------------------------------------------------------------
# the invariant this whole change protects
# --------------------------------------------------------------------------


def test_helpers_are_vendor_free():
    for name in ("mcp_status.py", "mcp_capability.py", "codegraph.py", "instructions.py"):
        src = (Path(claudio_core.__file__).parent / name).read_text(encoding="utf-8")
        assert VENDOR not in src.lower(), f"{name} contains a vendor string"


def test_helpers_do_not_import_the_agent_framework():
    src = (Path(claudio_core.__file__).parent / "mcp_status.py").read_text()
    imports = [
        line.strip()
        for line in src.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    joined = " ".join(imports).lower()
    for banned in ("pydantic", "google", "adk", "claudio_backend", "mcp"):
        assert banned not in joined, f"mcp_status.py must stay framework-free: {banned}"
