#!/usr/bin/env python3
"""Does the code-graph integration actually get used, and does it stay anonymous?

Two questions, two modes.

``--offline`` (default, runs in a bare gate: stdlib + ``claudio_core`` only)
    Build a realistic MCP catalog for the fixture workspace
    (``evals/fixtures/sample_repo``), then run the *real* renderers over a
    scripted set of code-task prompts and assert the properties the integration
    promises:

      * the capability detector fires on the code-graph catalog and the policy
        block is rendered,
      * the block names the real prefixed tool names the session advertises,
      * readiness derived from the server's own status payload is ``covered``,
      * no rendered block contains a vendor string,
      * plan/act auditing flags a narrated-but-not-called run and clears a
        called run,
      * a non-code-graph catalog renders NO graph block (no false trigger).

    Writes ``reports/codegraph-eval-offline.json`` and ``reports/codegraph-eval.md``.

``--live`` (optional, never required)
    Only runs with ``CLAUDIO_LIVE_EVAL=1`` *and* a configured backend URL + token.
    Sends the scripted prompts to a live Claudio session working on the fixture
    and records which tools the agent actually called, then computes the
    graph-tool invocation rate and the fallback rate to the file tools. Writes
    ``reports/codegraph-eval-live.json``. Unconfigured -> ``{"skipped": true,
    "reason": ...}``.

Contract
--------
Standard library only. **Always exits 0** -- this is a measurement instrument,
not a gate; a failing check is data written to the report, never a shell failure.
Nothing here imports pydantic, an agent framework or ``claudio_backend``.

Usage
-----
    python3 scripts/eval_codegraph_invocation.py --offline
    CLAUDIO_LIVE_EVAL=1 CODEGRAPH_EVAL_URL=... CODEGRAPH_EVAL_TOKEN=... \\
        python3 scripts/eval_codegraph_invocation.py --live
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, Mapping

ROOT = Path(__file__).resolve().parents[1]
CORE_SRC = ROOT / "claudio-core" / "src"
FIXTURE = ROOT / "evals" / "fixtures" / "sample_repo"
REPORTS = ROOT / "reports"
OFFLINE_JSON = REPORTS / "codegraph-eval-offline.json"
OFFLINE_MD = REPORTS / "codegraph-eval.md"
LIVE_JSON = REPORTS / "codegraph-eval-live.json"

if str(CORE_SRC) not in sys.path:
    sys.path.insert(0, str(CORE_SRC))

from claudio_core import codegraph, mcp_capability, planact  # noqa: E402

#: Tokens that must never reach the model. ``gortex`` is the vendor this whole
#: decoupling exercise was about; the others are the names a code-intelligence
#: vendor could plausibly leave behind in a copied prompt.
VENDOR_TOKENS: tuple[str, ...] = ("gortex", "sourcegraph", "kodark")

#: The server id used by the fixture catalog. Deliberately generic: it describes
#: what the server does, which is the point of the exercise.
SERVER_ID = "codegraph"

#: Tool names exactly as a streamable-http transport would present them: prefixed
#: with the configured server id, so the prompt can only ever name a tool the
#: session can actually call.
PREFIXED_TOOLS: tuple[str, ...] = (
    f"mcp__{SERVER_ID}__find_symbol",
    f"mcp__{SERVER_ID}__find_callers",
    f"mcp__{SERVER_ID}__find_callees",
    f"mcp__{SERVER_ID}__change_impact",
    f"mcp__{SERVER_ID}__dependency_graph",
    f"mcp__{SERVER_ID}__index_status",
)

TOOL_DESCRIPTIONS: dict[str, str] = {
    f"mcp__{SERVER_ID}__find_symbol":
        "Find a symbol definition in the indexed codebase and return its file and line.",
    f"mcp__{SERVER_ID}__find_callers":
        "Return every caller of a function or method -- who calls this code.",
    f"mcp__{SERVER_ID}__find_callees":
        "Return the callees of a function: the call graph reachable from a symbol.",
    f"mcp__{SERVER_ID}__change_impact":
        "Change impact analysis: the blast radius of editing a symbol.",
    f"mcp__{SERVER_ID}__dependency_graph":
        "Module dependencies and dependents for a file in the code graph.",
    f"mcp__{SERVER_ID}__index_status":
        "Report which repositories are indexed, index coverage and freshness.",
}


def codegraph_catalog(workspace: Path | str | None = None) -> list[dict[str, Any]]:
    """A realistic catalog for one connected code-graph server.

    Shape is exactly what the renderers consume: ``id``, prefixed ``tools``,
    ``tool_descriptions``, the server's own MCP ``instructions`` and -- the part
    that replaced the old on-disk manifest -- a ``status`` payload the server
    reported about itself, covering the fixture workspace.
    """
    ws = str(workspace or FIXTURE)
    return [
        {
            "id": SERVER_ID,
            "transport": "streamable_http",
            "tools": list(PREFIXED_TOOLS),
            "tool_descriptions": dict(TOOL_DESCRIPTIONS),
            "resources": [],
            "instructions": (
                "This server indexes repositories into a code graph and answers "
                "structural questions: symbol definitions, callers, callees, "
                "dependencies and change impact for the indexed codebase."
            ),
            "status": {
                "tracked_repos": [ws],
                "failed_repos": [],
                "generated_at": "2026-11-27T00:00:00Z",
            },
        }
    ]


def non_codegraph_catalog() -> list[dict[str, Any]]:
    """A catalog with two connected servers that are *not* code-graph servers.

    Used to prove the policy block cannot fire on an unrelated connector: one
    server whose tools are about the calendar, one whose tools are about issues.
    Neither advertises two lexicon hits, so detection must stay silent.
    """
    return [
        {
            "id": "calendar",
            "tools": ["mcp__calendar__list_events", "mcp__calendar__create_event"],
            "tool_descriptions": {
                "mcp__calendar__list_events": "List the events on a calendar.",
                "mcp__calendar__create_event": "Create a calendar event.",
            },
            "instructions": "Book meetings and read the operator's diary.",
        },
        {
            "id": "tracker",
            "tools": ["mcp__tracker__list_issues", "mcp__tracker__comment"],
            "tool_descriptions": {
                "mcp__tracker__list_issues": "List open issues in a project.",
                "mcp__tracker__comment": "Add a comment to an issue.",
            },
            "instructions": "Read and write project tickets.",
        },
    ]


#: The scripted code-task prompts. Each is a task an agent would genuinely run
#: against ``evals/fixtures/sample_repo``, phrased the way a narrating agent
#: phrases it, because these strings feed both the prompt renderers and the
#: plan/act auditor. >= 6 by contract.
CODE_TASK_PROMPTS: tuple[dict[str, str], ...] = (
    {
        "id": "callers-of-order-summary",
        "prompt": (
            "Who calls order_summary in this repo? I will query the code graph "
            "for its callers first, then read the exact source."
        ),
        "focus": "callers",
    },
    {
        "id": "impact-of-discount-change",
        "prompt": (
            "I am changing apply_discount. Let me run change impact analysis on "
            "the code graph to see the blast radius before editing."
        ),
        "focus": "impact",
    },
    {
        "id": "route-to-store-path",
        "prompt": (
            "Trace the call graph from post_checkout down to the store: I will "
            "use the graph tool to list callees at each hop."
        ),
        "focus": "callees",
    },
    {
        "id": "symbol-lookup",
        "prompt": (
            "Where is OrderRepository.find_by_customer defined? I will look it "
            "up with a symbol query on the code index rather than grepping."
        ),
        "focus": "symbol",
    },
    {
        "id": "module-dependencies",
        "prompt": (
            "What depends on config.py? I will ask the code graph for its "
            "dependents and the module dependency map."
        ),
        "focus": "dependencies",
    },
    {
        "id": "coverage-check",
        "prompt": (
            "Before trusting an empty result I will check index coverage with "
            "the status tool, then query the code graph for the entry points."
        ),
        "focus": "coverage",
    },
    {
        "id": "data-flow",
        "prompt": (
            "Follow the data flow from get_customer_orders into the repository; "
            "I will use the code graph call hierarchy first."
        ),
        "focus": "data flow",
    },
)


class Checks:
    """Accumulate named pass/fail results with a short detail string."""

    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def add(self, name: str, passed: bool, detail: str = "") -> bool:
        self.items.append({"name": name, "pass": bool(passed), "detail": detail})
        return bool(passed)

    @property
    def passed(self) -> int:
        return sum(1 for item in self.items if item["pass"])

    @property
    def failed(self) -> int:
        return sum(1 for item in self.items if not item["pass"])

    def to_json(self) -> dict[str, Any]:
        return {
            "checks": self.items,
            "passed": self.passed,
            "failed": self.failed,
        }


def find_vendor_strings(text: str) -> tuple[str, ...]:
    """Vendor tokens present in ``text`` (case-insensitive)."""
    low = (text or "").lower()
    return tuple(token for token in VENDOR_TOKENS if token in low)


def rendered_blocks(catalog: list[dict[str, Any]], workspace: Path | str) -> dict[str, str]:
    """Every piece of prompt text the code-graph integration can emit."""
    servers = mcp_capability.detect(catalog)
    blocks: dict[str, str] = {
        "policy": mcp_capability.render_code_graph_policy(servers),
    }
    status = (catalog[0] or {}).get("status") if catalog else None
    readiness = codegraph.readiness_from_status(str(workspace), status)
    blocks["readiness"] = codegraph.render_readiness_block(readiness)
    families = planact.derive_families(catalog) + planact.BUILTIN_FAMILIES
    narrated = audit_prompt_pair(catalog)[0]
    blocks["nudge"] = planact.render_planact_nudge(
        planact.audit_run(narrated["prompt"], ["read_file"], families)
    )
    return blocks


def audit_prompt_pair(catalog: list[dict[str, Any]]) -> tuple[dict[str, str], dict[str, str]]:
    """(narrated-but-not-called, narrated-and-called) prompt pairs for the audit."""
    graph_tool = PREFIXED_TOOLS[1]
    return (
        {
            "id": "narrated-not-called",
            "prompt": (
                "I will query the code graph for the callers of order_summary, "
                "then read the file to confirm the signature."
            ),
            "called": ["read_file", "run_command"],
            "graph_tool": graph_tool,
        },
        {
            "id": "narrated-and-called",
            "prompt": (
                "I will query the code graph for the callers of order_summary, "
                "then read the file to confirm the signature."
            ),
            "called": ["read_file", graph_tool],
            "graph_tool": graph_tool,
        },
    )


# --- offline checks ---------------------------------------------------------
# Each returns {"name", "pass", "detail"}. They are importable so the core test
# suite can assert on them without re-implementing the fixtures.


def _result(name: str, passed: bool, detail: str = "") -> dict[str, Any]:
    return {"name": name, "pass": bool(passed), "detail": detail}


def check_policy_block_present() -> dict[str, Any]:
    """The code-graph catalog produces a non-empty policy block."""
    servers = mcp_capability.detect(codegraph_catalog())
    block = mcp_capability.render_code_graph_policy(servers)
    return _result(
        "policy_block_present",
        bool(block) and "Codebase lookup policy" in block,
        f"{len(servers)} server(s) detected, block is {len(block)} chars",
    )


def check_block_names_real_tools() -> dict[str, Any]:
    """Every prefixed tool name in the catalog is quoted in the policy block."""
    catalog = codegraph_catalog()
    block = mcp_capability.render_code_graph_policy(mcp_capability.detect(catalog))
    missing = [tool for tool in catalog[0]["tools"] if tool not in block]
    return _result(
        "block_names_real_tools",
        not missing,
        f"all {len(PREFIXED_TOOLS)} advertised tool names appear in the block"
        if not missing
        else f"missing from block: {missing}",
    )


def check_block_names_configured_server_id() -> dict[str, Any]:
    """The block calls the server by its configured id, not by a guessed product."""
    block = mcp_capability.render_code_graph_policy(
        mcp_capability.detect(codegraph_catalog())
    )
    return _result(
        "block_names_configured_server_id",
        f"`{SERVER_ID}`" in block,
        f"configured id {SERVER_ID!r} quoted in the policy block",
    )


def check_readiness_covered() -> dict[str, Any]:
    """Readiness from the server's own status payload says the fixture is covered."""
    catalog = codegraph_catalog()
    readiness = codegraph.readiness_from_status(str(FIXTURE), catalog[0]["status"])
    block = codegraph.render_readiness_block(readiness)
    ok = (
        readiness.status == codegraph.STATUS_COVERED
        and readiness.repo == str(FIXTURE)
        and "Code graph status" in block
    )
    return _result(
        "readiness_is_covered",
        ok,
        f"status={readiness.status} repo={readiness.repo!r} block={len(block)} chars",
    )


def check_readiness_unknown_without_status() -> dict[str, Any]:
    """A server that reports no status degrades to unknown, never to 'not indexed'."""
    entry = dict(codegraph_catalog()[0])
    entry.pop("status", None)
    readiness = codegraph.readiness_from_status(str(FIXTURE), entry.get("status"))
    block = codegraph.render_readiness_block(readiness)
    # "unknown" must be said as unknown: the confident negative headline that the
    # not_covered verdict uses must not appear.
    confident_negative = "does NOT cover" in block
    return _result(
        "readiness_unknown_without_status",
        readiness.status == codegraph.STATUS_UNKNOWN
        and "unknown" in block.lower()
        and not confident_negative,
        f"status={readiness.status} detail={readiness.detail!r}",
    )


def check_zero_vendor_strings() -> dict[str, Any]:
    """No rendered block -- policy, readiness or nudge -- contains a vendor token."""
    blocks = rendered_blocks(codegraph_catalog(), FIXTURE)
    offenders = {
        name: find_vendor_strings(text) for name, text in blocks.items() if text
    }
    dirty = {name: hits for name, hits in offenders.items() if hits}
    return _result(
        "zero_vendor_strings",
        not dirty,
        f"scanned {len([t for t in blocks.values() if t])} rendered blocks, no vendor token"
        if not dirty
        else f"vendor strings found: {dirty}",
    )


def check_prompts_vendor_free() -> dict[str, Any]:
    """The scripted prompts themselves carry no vendor name (they are user text)."""
    hits = {p["id"]: find_vendor_strings(p["prompt"]) for p in CODE_TASK_PROMPTS}
    dirty = {k: v for k, v in hits.items() if v}
    return _result(
        "prompts_vendor_free",
        not dirty,
        f"{len(CODE_TASK_PROMPTS)} scripted prompts, none names a vendor"
        if not dirty
        else str(dirty),
    )


def check_planact_flags_narrated_not_called() -> dict[str, Any]:
    """Narrating a graph query without calling a graph tool is flagged."""
    catalog = codegraph_catalog()
    families = planact.derive_families(catalog) + planact.BUILTIN_FAMILIES
    narrated, _ = audit_prompt_pair(catalog)
    report = planact.audit_run(narrated["prompt"], narrated["called"], families)
    nudge = planact.render_planact_nudge(report)
    ok = bool(report.unfulfilled) and not report.clean and bool(nudge)
    return _result(
        "planact_flags_narrated_not_called",
        ok,
        f"planned={sorted(report.planned)} unfulfilled={sorted(report.unfulfilled)} "
        f"nudge={len(nudge)} chars",
    )


def check_planact_clears_called_run() -> dict[str, Any]:
    """The same narration with a real graph tool call audits clean."""
    catalog = codegraph_catalog()
    families = planact.derive_families(catalog) + planact.BUILTIN_FAMILIES
    _, called = audit_prompt_pair(catalog)
    report = planact.audit_run(called["prompt"], called["called"], families)
    nudge = planact.render_planact_nudge(report)
    return _result(
        "planact_clears_called_run",
        report.clean and not report.unfulfilled and nudge == "",
        f"satisfied={sorted(report.satisfied)} unfulfilled={sorted(report.unfulfilled)}",
    )


def check_derived_family_uses_server_id() -> dict[str, Any]:
    """The derived family is named after the configured id, not a vendor."""
    families = planact.derive_families(codegraph_catalog())
    names = [f.name for f in families]
    graph_tool = PREFIXED_TOOLS[1]
    satisfied = any(f.matches_tool(graph_tool) for f in families)
    return _result(
        "derived_family_uses_server_id",
        SERVER_ID in names and satisfied and not find_vendor_strings(" ".join(names)),
        f"families={names}; advertised tool {graph_tool!r} satisfies it",
    )


def check_non_codegraph_catalog_renders_no_block() -> dict[str, Any]:
    """An unrelated catalog must not trigger any code-graph prompt text."""
    catalog = non_codegraph_catalog()
    servers = mcp_capability.detect(catalog)
    policy = mcp_capability.render_code_graph_policy(servers)
    derived = planact.derive_families(catalog)
    return _result(
        "non_codegraph_catalog_renders_no_block",
        policy == "" and servers == () and derived == (),
        f"detect -> {len(servers)} servers, policy -> {len(policy)} chars, "
        f"derived families -> {len(derived)}",
    )


def check_fixture_shape() -> dict[str, Any]:
    """The fixture is present, tiny, and has the call graph the eval claims."""
    if not FIXTURE.is_dir():
        return _result("fixture_shape", False, f"{FIXTURE} is missing")
    files = sorted(p for p in FIXTURE.rglob("*") if p.is_file())
    py = [p for p in files if p.suffix == ".py"]
    md = [p for p in files if p.suffix == ".md"]
    edges = import_edges(py)
    needed = {("app", "routes"), ("app", "service"), ("routes", "service"), ("service", "repository")}
    missing = sorted(needed - edges)
    ok = len(files) >= 8 and bool(md) and not missing
    return _result(
        "fixture_shape",
        ok,
        f"{len(py)} python files, {len(md)} markdown, edges={sorted(edges)}"
        + (f" missing={missing}" if missing else ""),
    )


def import_edges(py_files: Iterable[Path]) -> set[tuple[str, str]]:
    """(source module, imported sibling module) pairs, read with ``ast`` only."""
    names = {p.stem for p in py_files if p.name != "__init__.py"}
    edges: set[tuple[str, str]] = set()
    for path in py_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, OSError):
            continue
        source = path.stem
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.ImportFrom):
                if node.level == 1:
                    targets = [alias.name for alias in node.names]
                    if node.module:
                        targets.append(node.module)
            elif isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            for target in targets:
                head = target.split(".")[0]
                if head in names and head != source:
                    edges.add((source, head))
    return edges


OFFLINE_CHECKS = (
    check_policy_block_present,
    check_block_names_real_tools,
    check_block_names_configured_server_id,
    check_readiness_covered,
    check_readiness_unknown_without_status,
    check_zero_vendor_strings,
    check_prompts_vendor_free,
    check_planact_flags_narrated_not_called,
    check_planact_clears_called_run,
    check_derived_family_uses_server_id,
    check_non_codegraph_catalog_renders_no_block,
    check_fixture_shape,
)


def run_offline_checks() -> list[dict[str, Any]]:
    """Run every offline check; a raising check is a failed check, never a crash."""
    results: list[dict[str, Any]] = []
    for check in OFFLINE_CHECKS:
        try:
            results.append(check())
        except Exception as exc:  # a broken check must still be reported
            results.append(
                {"name": check.__name__, "pass": False, "detail": f"raised {type(exc).__name__}: {exc}"}
            )
    return results


def render_markdown(results: list[dict[str, Any]]) -> str:
    """The same numbers as a human-readable table."""
    passed = sum(1 for r in results if r["pass"])
    failed = len(results) - passed
    lines = [
        "# Code-graph invocation eval",
        "",
        f"- mode: `offline` (stdlib + `claudio_core`, no network, no agent)",
        f"- workspace under test: `evals/fixtures/sample_repo`",
        f"- catalog under test: server id `{SERVER_ID}`, {len(PREFIXED_TOOLS)} prefixed tools, "
        f"status payload tracking the fixture",
        f"- scripted code-task prompts: {len(CODE_TASK_PROMPTS)}",
        f"- **{passed} passed, {failed} failed**",
        "",
        "Renderers exercised for real: `mcp_capability.detect` / "
        "`render_code_graph_policy`, `codegraph.readiness_from_status` / "
        "`render_readiness_block`, `planact.derive_families` / `audit_run` / "
        "`render_planact_nudge`.",
        "",
        "| check | pass | detail |",
        "| --- | --- | --- |",
    ]
    for r in results:
        detail = str(r.get("detail", "")).replace("|", "/").replace("\n", " ")
        lines.append(f"| `{r['name']}` | {'PASS' if r['pass'] else 'FAIL'} | {detail} |")
    lines += [
        "",
        "## Scripted prompts",
        "",
        "| id | prompt |",
        "| --- | --- |",
    ]
    for p in CODE_TASK_PROMPTS:
        lines.append(f"| `{p['id']}` | {p['prompt']} |")
    lines += [
        "",
        "## Live mode",
        "",
        "`python3 scripts/eval_codegraph_invocation.py --live` runs the same prompts "
        "against a real session on the fixture, but only when `CLAUDIO_LIVE_EVAL=1` "
        "and a backend URL + token are configured; otherwise it writes "
        "`{\"skipped\": true, \"reason\": ...}` to `reports/codegraph-eval-live.json`. "
        "See `docs/gortex-decoupling-review.md` (Close-out) for the exact variables.",
        "",
    ]
    return "\n".join(lines)


def write_offline_reports(results: list[dict[str, Any]]) -> dict[str, Any]:
    payload = {
        "checks": results,
        "passed": sum(1 for r in results if r["pass"]),
        "failed": sum(1 for r in results if not r["pass"]),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    OFFLINE_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    OFFLINE_MD.write_text(render_markdown(results), encoding="utf-8")
    return payload


# --- live mode --------------------------------------------------------------

FILE_TOOLS = ("read_file", "run_command", "grep", "glob", "list_dir", "write_file", "edit_file")


def live_config() -> tuple[str | None, str | None]:
    """(base_url, token) for the live eval, or (None, None) when unconfigured."""
    url = (
        os.environ.get("CODEGRAPH_EVAL_URL")
        or os.environ.get("CLAUDIO_LIVE_BASE_URL")
        or os.environ.get("CLAUDIO_BACKEND_URL")
        or ""
    ).strip().rstrip("/")
    token = (
        os.environ.get("CODEGRAPH_EVAL_TOKEN")
        or os.environ.get("CLAUDIO_LIVE_TOKEN")
        or os.environ.get("CLAUDIO_BACKEND_TOKEN")
        or os.environ.get("CLAUDIO_API_TOKEN")
        or ""
    ).strip()
    return (url or None, token or None)


def _post(url: str, token: str, payload: dict[str, Any], timeout: int = 180) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "authorization": f"Bearer {token}",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator URL
        return json.loads(response.read().decode("utf-8") or "{}")


def _get(url: str, token: str, timeout: int = 60) -> dict[str, Any]:
    request = urllib.request.Request(
        url, headers={"authorization": f"Bearer {token}"}, method="GET"
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - operator URL
        return json.loads(response.read().decode("utf-8") or "{}")


def tool_names_from_events(events: Iterable[Mapping[str, Any]]) -> list[str]:
    """Tool names the run actually invoked, read off the event stream."""
    names: list[str] = []
    for event in events or ():
        if not isinstance(event, Mapping):
            continue
        if str(event.get("event") or event.get("type") or "") != "tool_call":
            continue
        data = event.get("data") if isinstance(event.get("data"), Mapping) else event
        for key in ("tool", "name", "tool_name"):
            value = data.get(key)
            if value:
                names.append(str(value))
                break
    return names


def is_graph_tool(name: str, catalog: list[dict[str, Any]]) -> bool:
    """True when ``name`` belongs to a connected code-graph server's family."""
    families = planact.derive_families(catalog) + planact.BUILTIN_FAMILIES
    return any(family.matches_tool(name) for family in families)


def run_live() -> dict[str, Any]:
    """Send the scripted prompts to a live session and count graph-tool usage."""
    if os.environ.get("CLAUDIO_LIVE_EVAL", "").strip() != "1":
        return {
            "skipped": True,
            "reason": "live eval not requested: set CLAUDIO_LIVE_EVAL=1 to run it",
        }
    base_url, token = live_config()
    if not base_url or not token:
        return {
            "skipped": True,
            "reason": (
                "live eval configured off: set CODEGRAPH_EVAL_URL (backend base URL) "
                "and CODEGRAPH_EVAL_TOKEN (bearer token) alongside CLAUDIO_LIVE_EVAL=1"
            ),
        }

    catalog = codegraph_catalog(FIXTURE)
    graph_tools = set(PREFIXED_TOOLS)
    results: list[dict[str, Any]] = []
    errors = 0
    for task in CODE_TASK_PROMPTS:
        entry: dict[str, Any] = {"id": task["id"], "prompt": task["prompt"]}
        try:
            body = {"message": task["prompt"], "workspace_dir": str(FIXTURE)}
            answer = _post(f"{base_url}/chat", token, body)
            run_id = answer.get("run_id") or ""
            called: list[str] = []
            if run_id:
                try:
                    run = _get(f"{base_url}/chat/runs/{run_id}", token)
                    called = tool_names_from_events(run.get("events") or [])
                except (urllib.error.URLError, OSError, ValueError) as exc:
                    entry["run_error"] = str(exc)
            entry["tools_called"] = called
            entry["graph_tools_called"] = [t for t in called if is_graph_tool(t, catalog)]
            entry["file_tools_called"] = [t for t in called if t in FILE_TOOLS]
            entry["used_graph"] = bool(entry["graph_tools_called"])
            entry["fell_back_to_files"] = bool(entry["file_tools_called"]) and not entry["used_graph"]
        except (urllib.error.URLError, OSError, ValueError) as exc:
            errors += 1
            entry["error"] = f"{type(exc).__name__}: {exc}"
            entry["tools_called"] = []
            entry["used_graph"] = False
            entry["fell_back_to_files"] = False
        results.append(entry)

    n = len(results) or 1
    used = sum(1 for r in results if r.get("used_graph"))
    fell = sum(1 for r in results if r.get("fell_back_to_files"))
    return {
        "skipped": False,
        "workspace": str(FIXTURE),
        "server_id": SERVER_ID,
        "graph_tool_names": sorted(graph_tools),
        "prompts": len(CODE_TASK_PROMPTS),
        "errors": errors,
        "graph_invocation_rate": round(used / n, 4),
        "file_fallback_rate": round(fell / n, 4),
        "runs": results,
    }


def write_live_report(payload: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    LIVE_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--offline", action="store_true", help="run the offline checks (default)")
    parser.add_argument("--live", action="store_true", help="run the guarded live eval")
    parser.add_argument("--json-only", action="store_true", help="suppress stdout")
    args = parser.parse_args(argv)

    if args.live:
        payload = run_live()
        write_live_report(payload)
        if not args.json_only:
            if payload.get("skipped"):
                print(f"live eval skipped: {payload.get('reason')}")
            else:
                print(
                    f"live eval: {payload['prompts']} prompts, "
                    f"graph invocation rate {payload['graph_invocation_rate']}, "
                    f"file fallback rate {payload['file_fallback_rate']}"
                )
        return 0

    results = run_offline_checks()
    payload = write_offline_reports(results)
    if not args.json_only:
        for item in results:
            print(f"[{'PASS' if item['pass'] else 'FAIL'}] {item['name']}: {item['detail']}")
        print(f"offline eval: {payload['passed']} passed, {payload['failed']} failed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
