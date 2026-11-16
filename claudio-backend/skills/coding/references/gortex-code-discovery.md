# Gortex code discovery (query recipes)

Gortex is an optional code-intelligence MCP server (issue #54) that indexes the
repository into a graph and exposes search, symbol lookup, callers/callees,
dependencies, routes and change-impact analysis. It is only present when a
`gortex` server is connected and enabled in Claudio's `mcp.servers` config; the
system prompt then carries a **Codebase lookup policy**. This file holds the
longer query recipes; the short rule lives in the skill entry point.

## When Gortex helps vs. when to read files

Reach for the Gortex tools first for *orientation and relationships*:

- "Where is symbol X defined?" → symbol search.
- "Who calls this function / what does it call?" → callers / callees.
- "What route handles this endpoint, and how does it reach persistence?" → route
  lookup, then follow edges.
- "What breaks if I change this?" → change-impact / dependents.
- "Which tests cover this file?" → test-coverage query.

Read the actual source directly (regular file tools) for *exactness*: before any
edit, when a detail is not in the graph, when results conflict or look stale, and
after changes to verify the working tree. Gortex is navigation, not proof of the
current tree.

## Evidence trail

When you use Gortex, report which query you ran and which paths/symbols it
returned — not a vague "I checked the index". If the index is unavailable or
stale, say so and fall back to `read_file`/`list_dir`/`grep`.

## Refer tools generically

Tool names can change between Gortex versions. Prefer "use the Gortex MCP tools"
over hard-coding a tool name unless you have seen the exact name in the
connected-tools list in the system prompt.

## Setup reminder (operator)

Install the `gortex` binary on the backend's PATH, start its daemon
(`gortex daemon start --detach`), index the repo, set the `gortex` server
`enabled: true`, then `POST /agent/restart`. See `doc/configuration.md`
(§ mcp → Gortex code discovery).
