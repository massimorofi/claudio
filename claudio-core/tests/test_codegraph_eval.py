"""Tests for the code-graph invocation eval.

The eval script (``scripts/eval_codegraph_invocation.py``) is the instrument that
proves the decoupled integration is seamless: a code-graph MCP server is detected
from what it advertises, the prompt names only tools the session really has, the
readiness verdict comes from the server's own status report, plan/act auditing
catches "said graph, called nothing", and no rendered block ever says a vendor's
name.

This test does not re-implement any of that: it imports the eval module's own
check functions and asserts the offline suite is green. So the gate and the
report can never drift apart -- if a check regresses, both fail together.

Run:
    cd claudio-core && PYTHONPATH="$(pwd)/src" python3 -m pytest tests/test_codegraph_eval.py -q
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# Self-contained bootstrap: bare interpreter, no installed package, no pydantic,
# no plugin-provided fixtures.
HERE = Path(__file__).resolve()
CORE_SRC = HERE.parents[1] / "src"
REPO_ROOT = HERE.parents[2]
SCRIPTS = REPO_ROOT / "scripts"

for path in (str(CORE_SRC), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)


def _load_eval_module():
    """Import the eval script by path (it is a script, not an installed module)."""
    name = "eval_codegraph_invocation"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / "eval_codegraph_invocation.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {SCRIPTS / 'eval_codegraph_invocation.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ev = _load_eval_module()


def test_offline_suite_reports_zero_failures():
    results = ev.run_offline_checks()
    failed = [r for r in results if not r["pass"]]
    assert results, "the offline suite ran no checks"
    assert len(results) >= 8, f"expected a broad suite, got {len(results)} checks"
    assert not failed, "failed checks: " + "; ".join(f"{r['name']}: {r['detail']}" for r in failed)


def test_zero_vendor_strings_in_every_rendered_block():
    blocks = ev.rendered_blocks(ev.codegraph_catalog(), ev.FIXTURE)
    assert blocks, "no blocks rendered"
    named = {name: text for name, text in blocks.items() if text}
    assert named, "every rendered block was empty"
    for name, text in named.items():
        assert not ev.find_vendor_strings(text), f"vendor string in the {name} block"


def test_policy_block_names_the_advertised_tools():
    catalog = ev.codegraph_catalog()
    block = ev.mcp_capability.render_code_graph_policy(ev.mcp_capability.detect(catalog))
    assert block
    for tool in catalog[0]["tools"]:
        assert tool in block, f"{tool} is advertised but not named in the policy block"


def test_readiness_from_server_status_is_covered():
    from claudio_core import codegraph

    catalog = ev.codegraph_catalog()
    readiness = codegraph.readiness_from_status(str(ev.FIXTURE), catalog[0]["status"])
    assert readiness.status == codegraph.STATUS_COVERED
    assert readiness.repo == str(ev.FIXTURE)


def test_non_codegraph_catalog_produces_no_graph_block():
    from claudio_core import mcp_capability, planact

    catalog = ev.non_codegraph_catalog()
    servers = mcp_capability.detect(catalog)
    assert servers == ()
    assert mcp_capability.render_code_graph_policy(servers) == ""
    assert planact.derive_families(catalog) == ()


def test_planact_flags_narrated_but_not_called():
    catalog = ev.codegraph_catalog()
    families = ev.planact.derive_families(catalog) + ev.planact.BUILTIN_FAMILIES
    narrated, called = ev.audit_prompt_pair(catalog)

    gap = ev.planact.audit_run(narrated["prompt"], narrated["called"], families)
    assert gap.unfulfilled, "a narrated-but-not-called run must be flagged"
    assert ev.planact.render_planact_nudge(gap)

    clean = ev.planact.audit_run(called["prompt"], called["called"], families)
    assert clean.clean, f"called run must audit clean, got {sorted(clean.unfulfilled)}"
    assert ev.planact.render_planact_nudge(clean) == ""


def test_scripted_prompts_are_code_tasks_and_vendor_free():
    assert len(ev.CODE_TASK_PROMPTS) >= 6, "the eval needs at least six code-task prompts"
    for task in ev.CODE_TASK_PROMPTS:
        assert task["prompt"].strip()
        assert not ev.find_vendor_strings(task["prompt"]), task["id"]


def test_live_mode_is_guarded_and_skips(monkeypatch):
    for var in ("CLAUDIO_LIVE_EVAL", "CODEGRAPH_EVAL_URL", "CODEGRAPH_EVAL_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    payload = ev.run_live()
    assert payload.get("skipped") is True
    assert payload.get("reason")


def test_live_mode_skips_when_gate_open_but_unconfigured(monkeypatch):
    monkeypatch.setenv("CLAUDIO_LIVE_EVAL", "1")
    for var in ("CODEGRAPH_EVAL_URL", "CODEGRAPH_EVAL_TOKEN", "CLAUDIO_LIVE_BASE_URL",
                "CLAUDIO_LIVE_TOKEN", "CLAUDIO_BACKEND_URL", "CLAUDIO_BACKEND_TOKEN",
                "CLAUDIO_API_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    payload = ev.run_live()
    assert payload.get("skipped") is True
    assert "CODEGRAPH_EVAL_URL" in payload["reason"]


def test_fixture_workspace_exists_with_expected_files():
    assert ev.FIXTURE.is_dir(), f"missing fixture workspace {ev.FIXTURE}"
    files = sorted(p.name for p in ev.FIXTURE.rglob("*") if p.is_file())
    for expected in ("app.py", "service.py", "repository.py", "routes.py"):
        assert expected in files, f"fixture is missing {expected}: {files}"
    assert any(name.endswith(".md") for name in files), "fixture needs a docs markdown file"
