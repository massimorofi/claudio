"""Tests for MCP code-graph capability detection (prompt policy, vendor-free).

The code-navigation policy must be derived from what a connected MCP server
ADVERTISES -- its instructions text, its tool names and descriptions -- or from
an explicit operator override, never from a vendor string baked into Claudio.
These tests pin that behaviour, including the two directions that matter: a
server with a vendor-y id that advertises nothing gets no policy, and a server
with any id that advertises a code graph gets a policy naming ITS id and ITS
real tool names.

Synchronous only (no pytest-asyncio), no network, no pydantic.

Run:  cd claudio-core && PYTHONPATH="$(pwd)/src" python3 -m pytest tests/test_mcp_capability.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

try:  # self-contained: bootstrap src/ when the package is not installed
    import claudio_core  # noqa: F401
except ImportError:  # pragma: no cover - depends on how pytest is invoked
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    import claudio_core  # noqa: F401

from claudio_core.instructions import render_code_discovery_policy
from claudio_core.mcp_capability import (
    CAPABILITY_CODE_GRAPH,
    CODE_GRAPH_LEXICON,
    CodeGraphServer,
    detect,
    render_code_graph_policy,
)

VENDOR = "gor" + "tex"  # built, never written, so the module stays vendor-free

SKILL_REGISTRY = {
    "id": "SkillMD.com",
    "tools": ["SkillMD_com_skillmd_search", "SkillMD_com_skillmd_get"],
    "resources": [],
    "instructions": "Search the SkillMD registry of agent skills.",
    "tool_descriptions": {
        "SkillMD_com_skillmd_search": "Search skills by keyword",
        "SkillMD_com_skillmd_get": "Fetch one skill by slug",
    },
}

CODEGRAPH = {
    "id": "codegraph",
    "tools": ["codegraph_find_callers", "codegraph_symbol_search"],
    "resources": ["codegraph://repos"],
    "tool_descriptions": {
        "codegraph_find_callers": "List the callers of a symbol",
    },
}


# ---------------------------------------------------------------- detection --


def test_no_servers_detects_nothing_and_renders_empty():
    assert detect([]) == ()
    assert detect(None) == ()
    assert render_code_graph_policy(detect([])) == ""
    assert render_code_discovery_policy([]) == ""
    assert render_code_discovery_policy(None) == ""


def test_non_code_graph_server_gets_no_policy():
    """A skill registry advertises search, not a code graph: no policy."""
    assert detect([SKILL_REGISTRY]) == ()
    assert render_code_discovery_policy([SKILL_REGISTRY]) == ""


def test_lexicon_match_on_tool_descriptions_renders_policy():
    entry = {
        "id": "indexd",
        "tools": ["indexd_lookup", "indexd_trace"],
        "tool_descriptions": {
            "indexd_lookup": "Find a symbol definition and its callers",
            "indexd_trace": "Report the change impact of editing a file",
        },
    }
    servers = detect([entry])
    assert [s.id for s in servers] == ["indexd"]
    assert servers[0].source == "tools"
    policy = render_code_graph_policy(servers)
    assert "Codebase lookup policy" in policy
    # The real, prefixed tool names from the catalog -- not a generic hint.
    assert "indexd_lookup" in policy
    assert "indexd_trace" in policy
    assert "indexd" in policy


def test_explicit_capability_override_wins_with_no_lexicon_match():
    entry = {"id": "acme-mcp", "capability": CAPABILITY_CODE_GRAPH, "tools": ["acme_ask"]}
    servers = detect([entry])
    assert len(servers) == 1
    assert servers[0].source == "capability"
    assert servers[0].evidence  # the override is recorded as the reason
    policy = render_code_graph_policy(servers)
    assert "acme-mcp" in policy and "acme_ask" in policy


def test_capability_override_is_case_insensitive():
    assert detect([{"id": "acme", "capability": "Code_Graph"}])[0].source == "capability"
    assert detect([{"id": "acme", "capability": "filesystem"}]) == ()


def test_instructions_text_alone_qualifies_a_server():
    servers = detect([
        {"id": "atlas", "tools": ["atlas_query"],
         "instructions": "Atlas serves a call graph of the repository: callers, "
                         "callees and dependencies for every symbol."}
    ])
    assert [s.source for s in servers] == ["instructions"]
    assert "atlas_query" in render_code_graph_policy(servers)


def test_one_lexicon_hit_is_below_the_threshold():
    # A single stray word is noise, not a code graph.
    assert detect([{"id": "misc", "tools": ["misc_symbols"]}]) == ()


def test_malformed_entries_are_ignored_not_fatal():
    servers = detect([None, "nope", {}, {"tools": ["x"]}, CODEGRAPH])
    assert [s.id for s in servers] == ["codegraph"]


def test_detected_server_carries_id_tools_descriptions_and_evidence():
    servers = detect([CODEGRAPH])
    assert len(servers) == 1
    server = servers[0]
    assert isinstance(server, CodeGraphServer)
    assert server.id == "codegraph"
    assert server.tools == ("codegraph_find_callers", "codegraph_symbol_search")
    assert server.tool_descriptions["codegraph_find_callers"] == "List the callers of a symbol"
    assert server.evidence and any("callers" in e for e in server.evidence)


# ---------------------------------------------------------------- rendering --


def test_rendered_policy_names_the_configured_id_and_no_vendor():
    policy = render_code_graph_policy(detect([CODEGRAPH]))
    assert "`codegraph`" in policy
    assert VENDOR not in policy.lower()
    # Intent carried over from the old hand-written block.
    for expectation in ("FIRST", "read_file", "empty result", "query you used"):
        assert expectation.lower() in policy.lower(), expectation


def test_operator_named_server_is_rendered_with_that_id():
    """The id in the prompt comes from config, not from Claudio.

    An operator who literally ids their server with the vendor name gets that
    name back -- proof the renderer copies the configured id rather than
    hard-coding one.
    """
    entry = {
        "id": VENDOR,
        "tools": [f"{VENDOR}_find_callers", f"{VENDOR}_list_dependencies"],
        "resources": [],
    }
    servers = detect([entry])
    assert [s.id for s in servers] == [VENDOR]
    policy = render_code_graph_policy(servers)
    assert f"`{VENDOR}`" in policy
    assert f"{VENDOR}_find_callers" in policy


def test_multiple_servers_each_get_their_own_tool_section():
    other = {"id": "second-graph", "capability": CAPABILITY_CODE_GRAPH,
             "tools": ["second_graph_callees"]}
    policy = render_code_graph_policy(detect([CODEGRAPH, other]))
    assert "`codegraph`" in policy and "`second-graph`" in policy
    assert "second_graph_callees" in policy


def test_policy_without_a_tool_catalog_says_so():
    policy = render_code_graph_policy(detect([
        {"id": "quiet", "capability": CAPABILITY_CODE_GRAPH}]))
    assert "`quiet`" in policy
    assert "No tool catalog was published" in policy


def test_lexicon_and_module_are_vendor_free():
    assert VENDOR not in " ".join(CODE_GRAPH_LEXICON).lower()
    source = Path(render_code_graph_policy.__code__.co_filename).read_text(encoding="utf-8")
    assert VENDOR not in source.lower()


# ------------------------------------------------- still wired into the prompt --


def _config(workspace: Path):
    """Minimal stand-in for AppConfig (no pydantic in these tests)."""
    from types import SimpleNamespace

    return SimpleNamespace(agent=SimpleNamespace(
        skills_enabled=False,
        tools=SimpleNamespace(enabled=["read_file"], disabled=[]),
        default_workspace_dir=str(workspace),
        subagents={},
        approval_mode="off",
        max_turns=40,
        identity="You are Claudio.",
        environment_block="",
    ))


def test_build_static_instructions_still_renders_the_policy(tmp_path):
    from claudio_core.instructions import build_static_instructions

    prompt = build_static_instructions(
        _config(tmp_path), "s1", mcp_entries=[CODEGRAPH], workspace=tmp_path,
    )
    assert "Codebase lookup policy" in prompt
    assert "codegraph_find_callers" in prompt
    # The policy block itself is vendor-free. (The separate readiness block is
    # rendered by codegraph.py, which is out of scope for this change.)
    assert VENDOR not in _policy_block(prompt).lower()


def _policy_block(prompt: str) -> str:
    start = prompt.index("# Codebase lookup policy")
    rest = prompt[start + 1:]
    end = rest.find("\n# ", 1)
    return rest if end == -1 else rest[:end]


def test_build_static_instructions_omits_the_policy_without_a_code_graph(tmp_path):
    from claudio_core.instructions import build_static_instructions

    prompt = build_static_instructions(
        _config(tmp_path), "s1", mcp_entries=[SKILL_REGISTRY], workspace=tmp_path,
    )
    assert "Codebase lookup policy" not in prompt
