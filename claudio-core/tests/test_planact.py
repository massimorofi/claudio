"""Tests for the plan/act audit (Fix B: catch "planned it", never did it).

The regression this guards is concrete: an agent wrote "let me check the code
graph" in its narration, issued no graph call, and reported results as if the
graph had been consulted. These tests pin both halves -- the false claim must be
caught, and an honest disclosure must NOT be.

The family is described by capability vocabulary only. Nothing here names a
vendor: a server whose tool names use different vocabulary is matched through
its own catalog entry, in band, which is what ``code_graph_family`` is for.

Run:  cd claudio-core && .venv/bin/python -m pytest tests/test_planact.py -q
"""
from __future__ import annotations

from claudio_core.instructions import render_nudges
from claudio_core.planact import (
    BUILTIN_FAMILIES,
    CODE_GRAPH_FAMILY,
    AuditReport,
    audit_run,
    called_families,
    code_graph_family,
    derive_families,
    mentions_family,
    planned_families,
    render_planact_nudge,
)

GRAPH = CODE_GRAPH_FAMILY.name

# The whole point of the C4 change: the audit must work for any code-graph MCP
# server, so no builtin may name a product. Pinned here so a vendor string can
# never creep back into the shipped families.
VENDOR_STRINGS = ("gortex", "c4", "vendor-co", "acme-corp")


def test_builtin_families_carry_no_vendor_name():
    for family in BUILTIN_FAMILIES:
        blob = " ".join(
            [family.name, family.remedy, *family.triggers, *family.tool_patterns]
        ).lower()
        for vendor in VENDOR_STRINGS:
            assert vendor not in blob, (family.name, vendor)


def test_derive_families_is_empty_without_a_catalog():
    # No catalog means no per-server families; the builtins still work.
    assert derive_families(None) == ()
    assert derive_families([]) == ()
    assert derive_families([{"id": "web", "tools": ["fetch_url"]}]) == ()


def test_derive_families_survives_a_broken_catalog():
    for junk in (None, [], {}, [None], "not-a-list", [{"id": None}], [1, 2]):
        assert derive_families(junk) == ()


# --------------------------------------------------------------- intent parsing --
def test_commitment_language_is_a_plan():
    for text in (
        "Let me check the code graph for the callers.",
        "I'll query the code graph first.",
        "Now I will use the graph tool to map dependencies.",
        "I should run a change impact analysis.",
    ):
        assert GRAPH in planned_families(text), text


def test_honest_disclosure_is_not_a_plan():
    # These must stay silent, or the nudge becomes noise the model ignores.
    for text in (
        "The code graph was unavailable, so I used the file tools.",
        "I did not query the graph.",
        "No code graph call was made in this run.",
        "The code graph does not cover this workspace; falling back to read_file.",
        "I could not use the code graph because the index was empty.",
    ):
        assert GRAPH not in planned_families(text), text


def test_bare_mention_without_intent_is_not_a_plan():
    assert GRAPH not in planned_families("The code graph guide is indexed in doc/.")


def test_negation_is_per_sentence_not_per_document():
    text = "Let me check the code graph. The search tool was unavailable."
    assert GRAPH in planned_families(text)


# ------------------------------------------------------------ satisfied vs not --
def test_tool_names_are_matched_by_generic_vocabulary():
    assert called_families(["mcp__vendorx__search_symbols"]) == {GRAPH}
    assert called_families(["graph_find_callers"]) == {GRAPH}
    assert called_families(["read_file"]) == set()


def test_catalog_widens_matching_in_band_without_naming_a_vendor():
    # A server whose tool names carry no structural vocabulary is still matched,
    # because its own catalog entry supplies the patterns.
    entries = [{"id": "vendorx", "capability": "code_graph",
                "tools": ["mcp__vendorx__do_thing"]}]
    assert called_families(["mcp__vendorx__do_thing"]) == set()
    family = code_graph_family(entries)
    assert called_families(["mcp__vendorx__do_thing"], (family,)) == {GRAPH}
    assert family.name == CODE_GRAPH_FAMILY.name


def test_code_graph_family_survives_a_broken_catalog():
    for junk in (None, [], {}, {"id": None}, [None], "not-a-list"):
        assert code_graph_family(junk).name == CODE_GRAPH_FAMILY.name


def test_audit_report_clean_when_called():
    report = audit_run("Let me check the code graph for the callers.",
                       ["mcp__vendorx__get_callers"])
    assert report.clean
    assert AuditReport().clean


def test_audit_report_flags_unfulfilled_commitment():
    report = audit_run("I'll query the code graph first.", ["read_file"])
    assert report.unfulfilled == {GRAPH}
    assert not report.clean


def test_audit_report_ignores_unrelated_prose():
    report = audit_run("I'll use the file tools.", ["read_file"])
    assert report.clean


def test_nudge_text_is_actionable():
    report = audit_run("Let me check the code graph.", [])
    nudge = render_planact_nudge(report)
    assert nudge.startswith("<system-reminder>")
    assert "code graph" in nudge
    assert "PLAN/ACT MISMATCH" in nudge
    assert "Do not describe the graph as a source" in nudge


def test_nudge_empty_when_clean():
    assert render_planact_nudge(audit_run("nothing promised", ["read_file"])) == ""


def test_audit_report_remedy_lookup():
    report = AuditReport(unfulfilled={GRAPH})
    assert "code-graph" in report.remedy_for(GRAPH)
    assert report.remedy_for("unknown family") != ""


def test_mentions_family_helper():
    assert mentions_family("Let me check the code graph.", CODE_GRAPH_FAMILY)
    assert not mentions_family("The code graph was unavailable.", CODE_GRAPH_FAMILY)


# --------------------------------------------------------------------- nudges --
def test_render_nudges_stays_empty_without_a_gap():
    assert render_nudges() == ""
    assert render_nudges(unfulfilled_tools=[]) == ""


def test_render_nudges_surfaces_the_gap():
    out = render_nudges(unfulfilled_tools=[GRAPH])
    assert "PLAN/ACT MISMATCH" in out
    assert "code graph" in out
