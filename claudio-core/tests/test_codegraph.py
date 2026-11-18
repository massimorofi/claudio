"""Tests for code-graph readiness (Fix B: make the index precondition visible).

The behaviour under test is the one that was missing: an agent must be told
whether the Gortex index actually covers its workspace, and must never be able to
read "empty result" as "absent symbol" when the truth is "not indexed".

Run:  cd claudio-core && .venv/bin/python -m pytest tests/test_codegraph.py -q
"""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from claudio_core.codegraph import (
    STATUS_COVERED,
    STATUS_NO_MANIFEST,
    STATUS_NOT_COVERED,
    GraphReadiness,
    IndexManifest,
    assess,
    covered_repo,
    probe_index_health,
    read_manifest,
    render_readiness_block,
)


def write_manifest(tmp_path: Path, tracked, failed=()) -> Path:
    p = tmp_path / ".gortex-index.json"
    p.write_text(json.dumps(
        {"generated_at": "2026-11-18T00:00:00Z",
         "tracked_repos": list(tracked), "failed_repos": list(failed)}
    ))
    return p


# ---------------------------------------------------------------- manifest IO --

def test_read_manifest_roundtrip(tmp_path):
    p = write_manifest(tmp_path, ["/repos/clams", "/repos/claudio"])
    m = read_manifest(p)
    assert m is not None
    assert m.tracked_repos == ("/repos/clams", "/repos/claudio")
    assert m.failed_repos == ()
    assert m.healthy is True


def test_missing_manifest_is_none_not_empty(tmp_path):
    # The distinction matters: None means "unknown", not "nothing indexed".
    assert read_manifest(tmp_path / "absent.json") is None


def test_malformed_manifest_is_none(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json")
    assert read_manifest(p) is None
    p.write_text(json.dumps(["a list, not a dict"]))
    assert read_manifest(p) is None


def test_manifest_with_wrong_types_degrades_safely(tmp_path):
    p = tmp_path / "weird.json"
    p.write_text(json.dumps({"tracked_repos": "a-string-not-a-list"}))
    m = read_manifest(p)
    assert m is not None and m.tracked_repos == ()


def test_failed_repo_makes_manifest_unhealthy(tmp_path):
    m = IndexManifest(tracked_repos=("/repos/a",), failed_repos=("/repos/b:absent",))
    assert m.healthy is False


# ------------------------------------------------------------------- coverage --

def test_covered_repo_matches_nested_workspace(tmp_path):
    repo = tmp_path / "clams"
    repo.mkdir()
    ws = repo / "src" / "clams"
    ws.mkdir(parents=True)
    m = IndexManifest(tracked_repos=(str(repo),))
    assert covered_repo(m, ws) == str(repo.resolve())


def test_covered_repo_picks_longest_match(tmp_path):
    parent, nested = tmp_path / "ai", tmp_path / "ai" / "clams"
    nested.mkdir(parents=True)
    m = IndexManifest(tracked_repos=(str(parent), str(nested)))
    assert covered_repo(m, nested) == str(nested.resolve())


def test_covered_repo_returns_none_when_outside(tmp_path):
    repo, other = tmp_path / "clams", tmp_path / "elsewhere"
    repo.mkdir(); other.mkdir()
    assert covered_repo(IndexManifest(tracked_repos=(str(repo),)), other) is None


# --------------------------------------------------------------------- assess --

def test_assess_covered(tmp_path):
    repo = tmp_path / "clams"; repo.mkdir()
    p = write_manifest(tmp_path, [str(repo)])
    r = assess(repo, manifest_path=p)
    assert r.status == STATUS_COVERED and r.usable


def test_assess_not_covered(tmp_path):
    repo, other = tmp_path / "clams", tmp_path / "untracked"
    repo.mkdir(); other.mkdir()
    p = write_manifest(tmp_path, [str(repo)], ["/gone:absent"])
    r = assess(other, manifest_path=p)
    assert r.status == STATUS_NOT_COVERED and not r.usable
    assert "/gone:absent" in r.failed


def test_assess_no_manifest(tmp_path):
    r = assess(tmp_path, manifest_path=tmp_path / "absent.json")
    assert r.status == STATUS_NO_MANIFEST and not r.usable


# ------------------------------------------------------------------- rendering --

def test_render_covered_says_trustworthy(tmp_path):
    r = GraphReadiness(status=STATUS_COVERED, workspace="/w", repo="/repos/clams")
    block = render_readiness_block(r)
    assert "covers your current workspace" in block
    assert "/repos/clams" in block


def test_render_not_covered_forbids_the_false_inference(tmp_path):
    r = GraphReadiness(
        status=STATUS_NOT_COVERED, workspace="/w",
        tracked=("/repos/clams",), failed=("/x:absent",),
    )
    block = render_readiness_block(r)
    # The exact trap: empty graph result must not be reported as a code finding.
    assert "not indexed" in block and "does not exist" in block
    assert "file tools" in block
    assert "Do not claim you queried the graph" in block


def test_render_unknown_never_claims_absent(tmp_path):
    r = GraphReadiness(status=STATUS_NO_MANIFEST, workspace="/w")
    block = render_readiness_block(r)
    assert "unknown" in block
    assert "does NOT cover" not in block  # must not over-claim


# ------------------------------------------------------------------ live probe --

def test_probe_index_health_parses_success():
    def handler(request):
        assert request.headers["Authorization"] == "Bearer tok"
        return httpx.Response(200, json={"status": "ready", "node_count": 13106})

    with httpx.Client(transport=httpx.MockTransport(handler)) as c:
        out = probe_index_health("http://gortex:7411", token="tok", client=c)
    assert out["node_count"] == 13106


@pytest.mark.parametrize("status", [401, 500])
def test_probe_index_health_non_2xx_is_none(status):
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(status))
    ) as c:
        assert probe_index_health("http://gortex:7411", client=c) is None


def test_probe_index_health_transport_error_is_none():
    def boom(request):
        raise httpx.ConnectError("refused")

    with httpx.Client(transport=httpx.MockTransport(boom)) as c:
        assert probe_index_health("http://gortex:7411", client=c) is None


# ------------------------------------------------------- prompt wiring (Fix B3) --
# The modules only matter if the verdict reaches the system prompt.

GORTEX_MCP = [{"id": "gortex", "tools": ["Gortex_search"], "resources": []}]


def _config(tmp_path: Path):
    from claudio_core.settings import AppConfig

    return AppConfig.model_validate(
        {"agent": {"workspace_dir": str(tmp_path), "skills_enabled": False,
                   "tools": {"enabled": ["read_file"], "disabled": []}}}
    )


def test_status_block_absent_without_gortex(tmp_path):
    from claudio_core.instructions import render_code_graph_status

    assert render_code_graph_status(tmp_path, []) == ""
    assert render_code_graph_status(tmp_path, GORTEX_MCP, manifest_path=None) != ""


def test_status_block_absent_without_workspace(tmp_path):
    from claudio_core.instructions import render_code_graph_status

    assert render_code_graph_status(None, GORTEX_MCP) == ""


def test_static_instructions_carry_the_verdict(tmp_path):
    from claudio_core.instructions import build_static_instructions

    repo = tmp_path / "clams"
    repo.mkdir()
    other = tmp_path / "untracked"
    other.mkdir()
    manifest = write_manifest(tmp_path, [str(repo)])

    cfg = _config(tmp_path)
    cfg.agent.gortex_manifest_path = str(manifest)

    covered = build_static_instructions(
        cfg, "s1", mcp_entries=GORTEX_MCP, workspace=repo)
    assert "Code graph status" in covered and "covers your current workspace" in covered

    missed = build_static_instructions(
        cfg, "s1", mcp_entries=GORTEX_MCP, workspace=other)
    assert "does NOT cover your current workspace" in missed


def test_status_block_absent_from_prompt_when_gortex_missing(tmp_path):
    # A deployment without Gortex must not pay tokens for any of this.
    from claudio_core.instructions import build_static_instructions

    cfg = _config(tmp_path)
    prompt = build_static_instructions(cfg, "s1", mcp_entries=[], workspace=tmp_path)
    assert "Code graph status" not in prompt
