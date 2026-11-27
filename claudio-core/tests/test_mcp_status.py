"""Tests for reading an MCP server's own index/workspace status report.

The readiness block in the system prompt must be built from what the connected
server *advertises and answers*, never from a vendor name baked into Claudio.
These tests pin the two framework-free halves of that:

* ``pick_status_tool`` finds the status tool by generic vocabulary only (a
  ``workspace``/``capabilities``-style name qualifies, a skill-registry tool
  does not, an empty catalog gives ``None``).
* ``parse_status_payload`` survives the shapes a real MCP answer arrives in
  (bare JSON, fenced JSON, JSON behind log lines) and yields ``None`` -- which
  renders as "status unknown" -- for anything else.

Synchronous only (no pytest-asyncio), no network, no pydantic, no agent framework.

Run:  cd claudio-core && PYTHONPATH="$(pwd)/src" python3 -m pytest tests/test_mcp_status.py -q
"""
from __future__ import annotations

import sys
from pathlib import Path

try:  # self-contained: bootstrap src/ when the package is not installed
    import claudio_core  # noqa: F401
except ImportError:  # pragma: no cover - depends on how pytest is invoked
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    import claudio_core  # noqa: F401

from claudio_core.mcp_status import (
    STATUS_KEYS,
    STATUS_LEXICON,
    parse_status_payload,
    pick_status_tool,
    to_text,
)

VENDOR = "gor" + "tex"  # built, never written, so the module stays vendor-free

PAYLOAD = {
    "tracked_repos": ["/srv/work/api", "/srv/work/web"],
    "failed_repos": ["/srv/work/legacy"],
    "generated_at": "2026-11-27T09:12:03Z",
}

# A realistic skill-registry connector: nothing here reports index status.
SKILL_REGISTRY_TOOLS = [
    "SkillMD_com_skillmd_search",
    "SkillMD_com_skillmd_get",
    "SkillMD_com_skillmd_trending",
]
SKILL_REGISTRY_DESCRIPTIONS = {
    "SkillMD_com_skillmd_search": "Search the SkillMD registry of Agent Skills.",
    "SkillMD_com_skillmd_get": "Fetch one skill by owner/name slug.",
    "SkillMD_com_skillmd_trending": "Top skills on the leaderboard.",
}


# --------------------------------------------------------------------------
# lexicon hygiene
# --------------------------------------------------------------------------


def test_lexicon_is_generic_words_only():
    assert set(STATUS_LEXICON) == {
        "index",
        "coverage",
        "workspace",
        "status",
        "capabilities",
    }
    joined = " ".join(STATUS_LEXICON).lower()
    assert VENDOR not in joined
    assert all(not term.startswith(VENDOR) for term in STATUS_LEXICON)


def test_status_keys_match_the_readiness_contract():
    assert STATUS_KEYS == ("tracked_repos", "failed_repos", "generated_at")


# --------------------------------------------------------------------------
# pick_status_tool
# --------------------------------------------------------------------------


def test_picks_workspace_capabilities_style_tool():
    tools = [
        "cg_search_symbol",
        "cg_find_callers",
        "cg_get_workspace_capabilities",
        "cg_change_impact",
    ]
    assert pick_status_tool(tools, {}) == "cg_get_workspace_capabilities"


def test_picks_index_status_tool_from_description_alone():
    # The name says nothing; the advertised description does.
    tools = ["cg_probe", "cg_search_symbol"]
    descriptions = {
        "cg_probe": "Report index coverage: which repositories are tracked.",
        "cg_search_symbol": "Find symbol definitions across the graph.",
    }
    assert pick_status_tool(tools, descriptions) == "cg_probe"


def test_picks_plain_status_tool():
    tools = ["cg_status", "cg_search_symbol", "cg_find_callers"]
    assert pick_status_tool(tools, {}) == "cg_status"


def test_prefers_reporting_tool_over_mutating_one():
    tools = ["cg_reindex_workspace", "cg_workspace_status"]
    assert pick_status_tool(tools, {}) == "cg_workspace_status"


def test_ignores_skill_registry_tool():
    assert pick_status_tool(SKILL_REGISTRY_TOOLS, SKILL_REGISTRY_DESCRIPTIONS) is None


def test_none_when_empty_or_missing():
    assert pick_status_tool([], {}) is None
    assert pick_status_tool(None, None) is None
    assert pick_status_tool([], {"x": "index status"}) is None


def test_none_when_nothing_advertises_status():
    tools = ["fs_read_file", "fs_write_file", "web_search"]
    assert pick_status_tool(tools, {}) is None


def test_accepts_tool_objects_and_mapping_input():
    class Fake:
        def __init__(self, name):
            self.name = name

    tools = [Fake("cg_list_repos"), Fake("cg_index_coverage")]
    assert pick_status_tool(tools, {}) == "cg_index_coverage"
    # A dict of name -> description works as the tool list too.
    assert (
        pick_status_tool({"cg_index_coverage": "", "cg_list_repos": ""}, {})
        == "cg_index_coverage"
    )


def test_first_best_wins_on_a_tie_so_selection_is_stable():
    tools = ["cg_index_status", "cg_status_index"]
    assert pick_status_tool(tools, {}) == "cg_index_status"


# --------------------------------------------------------------------------
# parse_status_payload
# --------------------------------------------------------------------------


def test_bare_json():
    import json

    assert parse_status_payload(json.dumps(PAYLOAD)) == PAYLOAD


def test_json_inside_a_code_fence():
    import json

    text = "Here is the current index state:\n\n```json\n" + json.dumps(PAYLOAD) + "\n```\n"
    assert parse_status_payload(text) == PAYLOAD


def test_json_without_a_language_tag_in_the_fence():
    import json

    text = "```" + json.dumps(PAYLOAD) + "```"
    assert parse_status_payload(text) == PAYLOAD


def test_json_prefixed_by_log_lines():
    import json

    text = (
        "2026-11-27 09:12:03 INFO  watcher started\n"
        "2026-11-27 09:12:04 WARN  repo /srv/work/legacy failed: timeout\n"
        + json.dumps(PAYLOAD)
        + "\n"
    )
    assert parse_status_payload(text) == PAYLOAD


def test_prose_around_json():
    import json

    text = "The index reports " + json.dumps(PAYLOAD) + " right now."
    assert parse_status_payload(text) == PAYLOAD


def test_mcp_content_block_list():
    import json

    raw = [{"type": "text", "text": json.dumps(PAYLOAD)}]
    assert parse_status_payload(raw) == PAYLOAD


def test_wrapped_in_result_envelope():
    assert parse_status_payload({"result": {"data": dict(PAYLOAD)}}) == PAYLOAD


def test_already_parsed_dict():
    assert parse_status_payload(dict(PAYLOAD)) == PAYLOAD


def test_partial_payload_keeps_only_reported_keys():
    parsed = parse_status_payload({"tracked_repos": ["/a"]})
    assert parsed == {"tracked_repos": ["/a"]}
    assert "generated_at" not in parsed


def test_non_string_scalars_are_coerced():
    parsed = parse_status_payload(
        {"tracked_repos": ["/a", 2, None], "generated_at": 1738000000}
    )
    assert parsed["tracked_repos"] == ["/a", "2"]
    assert parsed["generated_at"] == "1738000000"


def test_garbage_is_none():
    for raw in ("", None, "   ", "no json here", "index status: unavailable"):
        assert parse_status_payload(raw) is None


def test_non_dict_json_is_none():
    for raw in ("[1, 2, 3]", '"just a string"', "42", "true", ["a", "b"], 42):
        assert parse_status_payload(raw) is None


def test_json_without_status_keys_is_none():
    assert parse_status_payload({"repos": ["/a"], "ok": True}) is None
    assert parse_status_payload({"result": {"tool": "search", "hits": 3}}) is None


def test_truncated_json_is_none():
    assert parse_status_payload('{"tracked_repos": ["/a", ') is None


def test_bytes_payload():
    import json

    assert parse_status_payload(json.dumps(PAYLOAD).encode()) == PAYLOAD


def test_never_raises_on_odd_input():
    for raw in (object(), {"a": object()}, [object()], b"\xff\xfe", 3.5):
        assert parse_status_payload(raw) is None or isinstance(
            parse_status_payload(raw), dict
        )


# --------------------------------------------------------------------------
# to_text
# --------------------------------------------------------------------------


def test_to_text_flattens_common_shapes():
    assert to_text("x") == "x"
    assert to_text(b"x") == "x"
    assert to_text([{"type": "text", "text": "a"}, {"type": "text", "text": "b"}]) == "a\nb"
    assert to_text(None) == ""


def test_to_text_is_vendor_free_source():
    src = (Path(claudio_core.__file__).parent / "mcp_status.py").read_text()
    assert VENDOR not in src.lower()
