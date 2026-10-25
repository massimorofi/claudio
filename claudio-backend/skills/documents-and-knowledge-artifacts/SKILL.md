---
name: documents-and-knowledge-artifacts
description: Summary of all skills for producing and maintaining documents — office
  file formats (PDF, DOCX, XLSX, PPTX), LaTeX, explanatory and report artifacts, data
  tables, meeting notes, project status artifacts and CLAUDE.md/project documentation
  quality. Use when asked to generate or edit a PDF/Word/Excel/PowerPoint file, write
  a LaTeX document, produce an explainer or report, capture meeting notes, publish
  a project status page, or audit and improve project documentation.
license: mixed — see Credits
metadata:
  last_reviewed: '2026-10-25'
  sources:
  - owner: built-in
    name: pdf
    license: unknown
  - owner: built-in
    name: docx
    license: unknown
  - owner: built-in
    name: xlsx
    license: unknown
  - owner: built-in
    name: pptx
    license: unknown
  - owner: GitHub app
    name: latex
    license: unknown
  - owner: built-in
    name: artifact-explainer
    license: unknown
  - owner: built-in
    name: artifact-report
    license: unknown
  - owner: built-in
    name: artifact-data-table
    license: unknown
  - owner: built-in
    name: artifact-capabilities
    license: unknown
  - owner: built-in
    name: meeting-notes
    license: unknown
  - owner: project-artifact
    name: project-artifact
    license: unknown
  - owner: claude-md-management
    name: claude-md-improver
    license: unknown
  - owner: MS Python ext
    name: pylance-docs
    license: unknown
---

# Documents, Reports & Knowledge Artifacts

Two halves: **file-format production** (getting bytes into the right
container) and **knowledge curation** (keeping written project truth
accurate and useful). Both fail the same way — assumed fidelity that nobody
checks.

## Constituent skills (13)

| Skill | Origin | What it contributes |
|---|---|---|
| pdf | built-in | Read/generate PDF documents |
| docx | built-in | Read/generate Word documents |
| xlsx | built-in | Read/generate spreadsheets |
| pptx | built-in | Read/generate slide decks |
| latex | GitHub app | LaTeX typesetting |
| artifact-explainer | built-in | Explanatory artifact (concepts, walkthroughs) |
| artifact-report | built-in | Structured report artifact |
| artifact-data-table | built-in | Tabular data artifact |
| artifact-capabilities | built-in | What an artifact can do / how to use it |
| meeting-notes | built-in | Capturing and structuring meeting notes |
| project-artifact | project-artifact | Tabbed project status: overview, success criteria, workstreams, risks |
| claude-md-improver | claude-md-management | Audit CLAUDE.md, score against templates, output a report |
| pylance-docs | MS Python ext | Reference documentation lookup |

## Consolidated guidance

1. **Open the output, don't trust the generator.** After producing any
   PDF/DOCX/XLSX/PPTX, actually open or re-parse it. Format libraries fail
   silently on encoding, merged cells, pagination and fonts.
2. **Structure before prose.** Decide the section skeleton (and for
   `project-artifact`: overview / success criteria / workstreams / risks)
   before writing. A report written without a skeleton is a wall of text.
3. **Success criteria are mandatory, not decorative.** A status artifact
   without explicit, checkable success criteria cannot report progress.
4. **Tables need a schema.** Define columns, types and sort order first;
   a data table with ambiguous columns is worse than prose.
5. **Meeting notes: decisions > discussion.** Capture decisions, owners and
   dates. Verbatim recap is the least useful form.
6. **CLAUDE.md is a living contract.** Audit it like code: score against a
   template, flag stale or contradicted instructions, and produce a report
   of what to change rather than silently rewriting.
7. **Match the container to the reader.** A deck (pptx) for a decision
   meeting, a report for record, a spreadsheet for analysis, LaTeX for
   formal/published text. Choosing the wrong format loses the audience.
8. **Cite sources for claims.** Especially in explainers and reports —
   unverified claims in a polished document are more dangerous than no document.

## When to use this skill

"Make a PDF/Word/Excel of…", "write this in LaTeX", "produce a report /
explainer", "take meeting notes", "how good is our CLAUDE.md?", "publish a
project status page".

## Credits

This skill merges guidance from the following source skills, each the work of its respective owner and used under its original license. Preserve this attribution when reusing or re-merging.

| Owner | Skill | License |
|---|---|---|
| built-in | pdf | unknown |
| built-in | docx | unknown |
| built-in | xlsx | unknown |
| built-in | pptx | unknown |
| GitHub app | latex | unknown |
| built-in | artifact-explainer | unknown |
| built-in | artifact-report | unknown |
| built-in | artifact-data-table | unknown |
| built-in | artifact-capabilities | unknown |
| built-in | meeting-notes | unknown |
| project-artifact | project-artifact | unknown |
| claude-md-management | claude-md-improver | unknown |
| MS Python ext | pylance-docs | unknown |
