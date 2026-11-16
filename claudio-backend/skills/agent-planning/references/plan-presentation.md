# Plan presentation (visual surfaces, wireframes, docs)



## Create A Structured Agent-Native Plan — Never Inline

The deliverable is ALWAYS a structured Agent-Native Plan, not a chat-only plan.
The hosted Plan MCP connector (`plan` server, or legacy `agent-native-plans`) is
the default collaboration and commenting surface; it is not a reason to reject
the planning pattern as an external dependency or rented layer. Plans are
portable source artifacts (`plan.mdx`, optional `canvas.mdx` /
`prototype.mdx`, JSON, and HTML export), and ownership-sensitive workflows can
use local-files mode or a self-hosted/custom Plan app URL without abandoning the
skill's review discipline. Do not advise the user to skip `/visual-plan` because
the default surface is hosted; choose the right Plan mode for the user's
ownership, privacy, sharing, and branding needs.

By default, create the plan via the Plan MCP connector and NEVER hand it over as
inline chat content — no Markdown prose, ASCII sketch, table, or fenced
wireframe. If the `plan` (or legacy `agent-native-plans`) tools are not visible,
discover them through the host's `tool_search` first; if they are still missing,
STOP and give the user the client-specific reconnect step rather than improvising
an inline plan. Before publishing, or whenever a connector or auth error appears,
READ `references/connection.md` in this skill directory — it is the single source
of truth for the never-inline rule, connector discovery, and the per-client
reconnect steps. Local-files privacy mode (after Tool Guidance) is the exception.

## Visual Surface Choice

Choose the surface before creating the plan or after reading the source plan. Do
not add visual chrome by default:

For UI/product plans, the top canvas is usually the primary review surface. Put
the first meaningful wireframes there, not buried as document-body blocks. Use
multiple canvas artboards when states matter, such as the default view, an
overflow menu or popover, a side panel, loading, or error. Put short annotations
beside frames with `targetId` plus `placement`; keep implementation details,
tradeoffs, file maps, data contracts, risks, and verification in the document
body below the canvas.

When the user asks for a flow, storyboard, journey, wireframe, canvas, or "what
this looks like", treat that as a canvas-first request. Make one artboard per
user-visible state, connect only adjacent transitions, and use short canvas
annotations for the product notes. Do not substitute a document-body `diagram`
block for the requested storyboard just because HTML diagrams are faster to
write; diagrams belong below the canvas for backend mechanics, architecture, or
data-flow explanation.

Keep product wireframes and explanatory/meta diagrams separate. Start with pure
screens that look like the app state under discussion, without callout prose or
architecture notes embedded inside the UI. Put arrows, labels, contracts, data
flow, and mode explanations in separate annotations, separate canvas diagrams,
or the document body.

When the plan touches an existing app, inspect the current shell/components
before drawing. The first artboard should look like the real app at the same
density: existing sidebars, toolbar placement, overflow menus, app chrome, and
framework agent chrome stay in their real places. Model secondary surfaces as
separate states, such as a top-right overflow popover, sheet, panel, loading
state, or separate AgentSidebar, rather than inventing a permanent inspector or
folding framework chrome into the product UI.

- **No visual surface** for architecture-only, backend-only, data migration,
  copy-only, or otherwise non-visual plans. Do not use the top canvas for
  architecture diagrams, dependency maps, file plans, API contracts, or
  data-flow-only reviews. Use a strong document with local inline diagrams
  only when relationships need a visual explanation, usually one spatial diagram
  per recommendation or decision. Prefer grouped regions, layers, quadrants,
  matrices, or before/after panels over a single-axis chain unless the
  relationship is truly sequential.
- **Canvas only** for one static screen, a before/after comparison, a component
  state, a small popover, or a visual direction that does not require clicking.
  Put those wireframes in `content.canvas` and omit `content.prototype`.
- **Canvas + prototype** for multi-step UI flows, onboarding, wizards,
  review/approval flows, navigation changes, or anything where the reviewer
  needs to operate the behavior. Keep the static wireframes in
  `content.canvas`, add the aligned functional prototype in
  `content.prototype`, and rely on the top visual tabs to switch between them.
- **Prototype-first** when the user asks to operate the UI or when interaction is
  the main question. Use `create-prototype-plan`, which still preserves static
  mocks where useful.

For mixed canvas + prototype plans, reuse the same real labels, app statuses,
and screen ids across both surfaces. The canvas is the inspectable static reference;
the prototype is the interactive version of that same flow, not a separate
design direction.

## Wireframe quality — read `references/wireframe.md`

UI recap/plan wireframes must meet a strict quality bar — full-width chrome,
pinned bottom bars, real product content, before/after comparability, the right
`surface` preset, `--wf-*` tokens instead of hex, and no `<html>`/`<style>`/font
tags. Before authoring ANY wireframe / `<Screen>` / `WireframeBlock`, READ
`references/wireframe.md` in this skill directory — it is the single source of
truth for HTML wireframe quality, shared word for word with `/visual-plan`
and `/visual-recap`. Do not author wireframes from memory.

## Canvas — read `references/canvas.md`

The canvas is the single source of truth for static UI mockups: the `surface`
locks each artboard's footprint, mixed surfaces lay out
in lanes, annotations are plain-text designer notes anchored by
`targetId`/`placement`, and edits are surgical `contentPatches`. Before
authoring or editing ANY canvas, artboard, or annotation, READ
`references/canvas.md` in this skill directory — it is the single source of truth
for canvas/artboard mechanics. Do not author canvas layouts from memory.
Canvas artboards use the same HTML wireframe path as document-body
`WireframeBlock` screens: author `<Screen surface="..." html={...} />` with a
semantic HTML fragment. Do not author fresh kit-tree children such as
`<FrameScreen>`, `<Card>`, `<Row>`, or `<Btn>` inside canvas `<Screen>` tags;
those are legacy compatibility markup for old plans and produce brittle canvas
layouts.

## Document quality — read `references/document-quality.md`

The document is a serious technical plan, not marketing: outcome-first,
prose-first, self-contained, built from the right native blocks, with open
questions in a single bottom `question-form` and a pre-handoff visual check.
Before authoring the plan document, READ `references/document-quality.md` in this
skill directory — it is the single source of truth for the document quality bar.
Do not write the document from memory.

## Good vs. bad exemplar — read `references/exemplar.md`

For a worked example of the bar — a great UI-first plan and `/visual-plan`, plus
the anti-patterns to avoid — READ `references/exemplar.md` in this skill
directory before authoring a plan.

## Local-Files Privacy Mode — read `references/local-files.md`

When the user wants no hosted Plan database writes — no DB writes, no Plan MCP
publish, fully local/offline/private planning, repo-owned source-controlled
artifacts, or `AGENT_NATIVE_PLANS_MODE=local-files` — do not call any hosted Plan
tool except the schema-only `get-plan-blocks` catalog lookup. Author a local MDX
folder and
preview it with `plan local check` / `plan local serve` / `plan local verify`.
Before using local-files mode, READ `references/local-files.md` in this skill
directory — it is the single source of truth for the full contract (catalog
lookup, MDX folder layout, the local bridge commands, and the hosted tools you
must not call). Carry forward only the code-research and plan-composition
guidance from Core Workflow; everything hosted is replaced by the local bridge.
