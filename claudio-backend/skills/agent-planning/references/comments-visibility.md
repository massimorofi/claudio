# Comment anchors & sharing



## Interpreting comment anchors

This section applies to hosted plans with `get-plan-feedback` /
`update-visual-plan`. In local-files mode, do not call hosted feedback or update
tools; interpret file/chat feedback directly, edit the MDX files, rerun the
local bridge check/serve/verify command, and report the new local URL.

`get-plan-feedback` returns rich anchors — read them before acting on any comment.

- **Coordinate frames.** `targetX`/`targetY` are percentages *within* the
  element named by `targetSelector`/`targetKind`. Bare `x`/`y` are percentages
  of the whole plan document. `canvasX`/`canvasY` are raw board-world pixels on
  the design canvas (board size given when available).
- **Wireframe pins.** Anchors on wireframes include `targetNodeId` and
  `targetNodePath` (e.g. `card > list > listItem "Acme Inc"`) identifying the
  exact kit node. Use `targetNodeId` directly with wireframe node patch ops;
  use `data-design-id` values from design artboards with
  `update-design-element-style`. Prefer the node id/path over raw coordinates;
  fall back to coordinates plus the focused screenshot (red ring marks the exact
  point) only when no node id is present.
- **Text quotes.** Resolve `textQuote` against current prose using
  `contextBefore`/`contextAfter` for disambiguation. If `ambiguous: true`, ask
  the user — do not guess which occurrence is meant.
- **Detached comments.** `get-plan-feedback` flags threads whose quoted text no
  longer exists as `detached` (in `detachedThreads`). Reconcile these against
  rewritten content — never silently drop them.
- **Routing.** `resolutionTarget` is the only routing signal: act on `agent`,
  treat `human` as context only. `@mentions` are people to notify, never a
  routing signal.
- **Two-axis state.** Mark every ingested comment as consumed
  (`consumedCommentIds` on `update-visual-plan`). Set `status=resolved` only on
  agent-targeted comments you actually addressed; leave human-targeted comments
  open.

## Visibility & Sharing

Use `set-resource-visibility` to change who can see a plan (e.g. public, login,
or org-scoped). Use `share-resource` to grant specific users or roles access
by email or role. Gate visibility before sharing any plan that covers
unreleased or private work — default to the narrowest scope that meets the
review need.
