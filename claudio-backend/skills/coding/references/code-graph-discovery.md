# Code-graph discovery (query recipes)

A **code-graph MCP server** is an optional, externally-run indexer that turns the
repository into a graph and answers structural questions over the MCP Streamable
HTTP transport: symbol lookup, callers/callees, dependencies, routes and
change-impact analysis. Claudio does not ship or start it. It exists only when the
operator has registered a server under `mcp.servers` (`transport:
streamable_http`) and that server connected successfully; the system prompt then
carries a **Codebase lookup policy** naming the configured server id and the real
(prefixed) tool names that server advertised in `tools/list`.

This file holds the longer query recipes; the short rule lives in the skill entry
point.

## When the code graph helps vs. when to read files

Reach for the connected code-graph server's tools first for *orientation and
relationships*:

- "Where is symbol X defined?" → symbol / definition lookup.
- "Who calls this function, and what does it call?" → callers / callees,
  call sites, call hierarchy.
- "What route handles this endpoint, and how does it reach persistence?" → route
  or entry-point lookup, then follow edges.
- "What breaks if I change this?" → change impact / dependents / blast radius.
- "Which modules or files does the index actually cover?" → index coverage or
  status query, if the server advertises one.

Read the actual source directly (`read_file`, `list_dir`, grep) for *exactness*:
before any edit, when a detail is not in the graph, when results conflict or look
stale, and after changes to verify the working tree. The graph describes
structure; it is not the file.

## Empty is not absence

An empty result does **not** prove the code does not exist. It can mean:

- the symbol is not indexed (language or path outside the indexed roots),
- the repository you asked about was never added to the server's tracked set,
- the index is stale while the working tree has moved on.

So: check the coverage/freshness signal the server advertises before you conclude
"no callers". If the index does not cover the code you asked about, **say so**
and fall back to `grep`/`read_file`. Never report "not found" from a graph query
without stating that the graph may not cover it.

## Evidence trail

When you use the code graph, report which tool you called and with which
arguments, and which paths/symbols it returned — not a vague "I checked the
index". That makes the answer reproducible and lets a reviewer see whether the
graph or the file was the source of truth.

## Refer tools by what they advertise

Tool names differ between servers and between versions of the same server. Never
guess a tool name: use the exact names listed under "Query tools advertised by
`<server id>`" in the system prompt (they come from `tools/list`). If a tool you
expected is missing, pick the closest advertised one, or say the server does not
expose that query.

## Readiness

Readiness is a property of the live MCP connection, not of a file on disk. If the
server connected, its tools are in your tool list and the policy block names it.
If it did not connect — wrong URL, rejected bearer token, server down — you get
no policy block and no tools, and the honest answer is "no code-graph server is
connected in this session". Do not add or edit MCP servers to get an answer;
report the gap and use the file tools.

## Setup reminder (operator)

Run the indexer wherever you like (an arbitrary container via the generic overlay
`docker/mcp/codegraph.compose.yaml`, or any host process), register it under
`mcp.servers` with `transport: streamable_http`, its URL, and an
`Authorization: Bearer <token>` header, optionally pinning `capability:
code_graph`, then restart the agent so it rebuilds its MCP toolset. Full runbook:
`docs/mcp-codegraph.md`.
