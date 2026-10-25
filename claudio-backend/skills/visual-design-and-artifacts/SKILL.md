---
name: visual-design-and-artifacts
description: Summary of all skills for visual and interactive output design — distinctive
  frontend/aesthetic direction, artifact visual design and components, data visualization,
  live sketch whiteboards, self-contained HTML playgrounds, pushing a design system
  to a design tool, and canvas creation. Use when asked to design a UI, choose a visual
  style, avoid generic/templated looks, build a dataviz, sketch a wireframe, make
  an interactive playground, or sync a design system.
license: mixed — see Credits
metadata:
  last_reviewed: '2026-10-25'
  sources:
  - owner: frontend-design
    name: frontend-design
    license: unknown
  - owner: built-in
    name: prototype
    license: unknown
  - owner: built-in
    name: artifact-design
    license: unknown
  - owner: built-in
    name: artifact-components
    license: unknown
  - owner: built-in
    name: dataviz
    license: unknown
  - owner: built-in
    name: whiteboard
    license: unknown
  - owner: playground
    name: playground
    license: unknown
  - owner: built-in
    name: design-sync
    license: unknown
  - owner: GitHub app
    name: create-canvas
    license: unknown
---

# Visual Design & Interactive Artifacts

The recurring theme across these skills: **visual output needs an intentional
point of view**, not a default theme. Every one of them exists because the
default output looks like everything else.

## Constituent skills (9)

| Skill | Origin | What it contributes |
|---|---|---|
| frontend-design | frontend-design | Distinctive aesthetic direction, typography, avoiding templated defaults |
| prototype | built-in | Turn an idea into a clickable proof-of-concept Artifact (intake → assumptions → build → iterate) |
| artifact-design | built-in | Design guidance and fundamentals for Artifacts |
| artifact-components | built-in | Reusable visual components for Artifacts |
| dataviz | built-in | Data visualization principles (chart choice, encoding, colour) |
| whiteboard | built-in | Shared live sketch canvas, wireframe-fidelity diagrams |
| playground | playground | Self-contained interactive HTML playgrounds with live preview and copy-out prompts |
| design-sync | built-in | Push a React design system to claude.ai/design (converter + uploader) |
| create-canvas | GitHub app | Canvas creation |

## Consolidated guidance

1. **Decide the aesthetic before writing markup.** Pick palette, type pairing,
   spacing scale and a single distinctive element first; then build. Building
   first and styling later produces generic output.
2. **Typography carries the design.** Two families maximum, a real scale,
   deliberate weight contrast. Default system-font stacks read as "unfinished".
3. **Refuse the template look.** If the result could belong to any SaaS
   landing page from the last five years, it has no direction. Change one
   bold thing: layout, colour, or type.
4. **Charts: choose the encoding before the library.** Quantity → position or
   length, not area; avoid pie charts for more than a couple of categories;
   colour encodes meaning, never decoration.
5. **Sketch before you build.** For spatial/layout questions, a whiteboard
   pass at wireframe fidelity is cheaper than a rendered iteration.
6. **Playgrounds are self-contained by contract.** Single HTML file, no
   external dependencies, live preview, and the prompt that regenerates it
   is part of the deliverable.
7. **Components over one-offs.** Extract repeated visual patterns into
   components; a design system that is only in your head drifts.
8. **Sync is a conversion, not a copy.** Pushing a design system to a
   design tool requires token mapping — verify the converted tokens, don't
   assume fidelity.

## When to use this skill

"Design a page that doesn't look generic", "make a chart of…", "sketch a
wireframe", "build an interactive playground for X", "push our design system".

## Credits

This skill merges guidance from the following source skills, each the work of its respective owner and used under its original license. Preserve this attribution when reusing or re-merging.

| Owner | Skill | License |
|---|---|---|
| frontend-design | frontend-design | unknown |
| built-in | prototype | unknown |
| built-in | artifact-design | unknown |
| built-in | artifact-components | unknown |
| built-in | dataviz | unknown |
| built-in | whiteboard | unknown |
| playground | playground | unknown |
| built-in | design-sync | unknown |
| GitHub app | create-canvas | unknown |
