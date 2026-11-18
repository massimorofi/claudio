"""Tests for the plan/act audit (Fix B: catch "planned it", never did it).

The regression this guards is concrete: an agent wrote "let me check Gortex" in
its narration, issued no graph call, and reported results as if the graph had
been consulted. These tests pin both halves -- the false claim must be caught,
and an honest disclosure must NOT be.

Run:  cd claudio-core && .venv/bin/python -m pytest tests/test_planact.py -q
"""
from __future__ import annotations

from claudio_core.instructions import render_nudges
from claudio_core.planact import (
    GORTEX_FAMILY,
    AuditReport,
    audit_run,
    called_families,
    mentions_family,
    planned_families,
    render_planact_nudge,
)

GRAPH = GORTEX_FAMILY.name


# --------------------------------------------------------------- intent parsing --

def test_commitment_language_is_a_plan():
    for text in (
        "Let me check Gortex for the callers.",
        "I'll query the code graph first.",
        "Now I will use Gortex to map dependencies.",
        "I should run a change impact analysis.",
    ):
        assert GRAPH in planned_families(text), text


def test_honest_disclosure_is_not_a_plan():
    # These must stay silent, or the nudge becomes noise the model ignores.
    for text in (
        "Gortex was unavailable, so I used the file tools.",
        "I did not query the graph.",
        "No Gortex call was made in this run.",
        "Gortex does not cover this workspace; falling back to read_file.",
        "I could not use Gortex because the index was empty.",
    ):
        assert GRAPH not in planned_families(text), text


def test_bare_mention_without_intent_is_not_a_plan():
    assert GRAPH not in planned_families("The Gortex guide is indexed in doc/.")


def test_negation_is_per_sentence_not_per_document():
    text = "Let me check Gortex. The search tool was unavailable."
    assert GRAPH in planned_families(text)


# ------------------------------------------------------------ satisfied vs not --

def test_any_graph_call_discharges_the_family():
    assert called_families(["Gortex_search", "read_file"]) == {GRAPH}
    assert called_families(["read_file"]) == set()


def test_audit_reports_unfulfilled_promise():
    report = audit_run("Let me check Gortex for the callers.", ["read_file", "run_command"])
    assert report.unfulfilled == {GRAPH}
    assert not report.clean


def test_audit_clean_when_the_call_happened():
    report = audit_run("Let me check Gortex for the callers.", ["Gortex_search"])
    assert report.clean and report.satisfied == {GRAPH}


def test_audit_clean_when_nothing_was_promised():
    assert audit_run("I read the files directly.", ["read_file"]).clean


# ------------------------------------------------------------------- the nudge --

def test_nudge_names_the_family_and_the_fix():
    report = audit_run("Let me check Gortex.", ["read_file"])
    nudge = render_planact_nudge(report)
    assert "PLAN/ACT MISMATCH" in nudge
    assert "Gortex" in nudge
    assert "Do not describe the graph as a source" in nudge
    assert nudge.startswith("<system-reminder>")


def test_nudge_empty_when_clean():
    assert render_planact_nudge(audit_run("nothing promised", ["read_file"])) == ""


def test_nudge_renders_via_instructions():
    out = render_nudges(unfulfilled_tools=[GRAPH])
    assert "PLAN/ACT MISMATCH" in out
    assert render_nudges() == ""


def test_audit_report_remedy_lookup():
    report = AuditReport(unfulfilled={GRAPH})
    assert "Gortex" in report.remedy_for(GRAPH)
    assert report.remedy_for("unknown family") != ""
