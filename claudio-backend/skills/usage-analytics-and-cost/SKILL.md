---
name: usage-analytics-and-cost
description: Summary of all skills for measuring and diagnosing how the assistant
  is actually being used — personal usage and impact reports mined from local session
  transcripts, explorable HTML session reports (tokens, cache, subagents, skills,
  expensive prompts), usage explanation, and diagnosing unexpected agent behavior
  from JSONL debug logs. Use when asked how much usage or spend there has been, to
  justify spend or prove value, to find what is expensive or slow, to audit token
  and cache behaviour, or to explain why a request was slow or why tools, subagents,
  skills or instructions did not load.
license: mixed — see Credits
metadata:
  last_reviewed: '2026-10-25'
  sources:
  - owner: receipts
    name: receipts
    license: unknown
  - owner: session-report
    name: session-report
    license: unknown
  - owner: built-in
    name: explain-usage
    license: unknown
  - owner: Copilot CLI
    name: troubleshoot
    license: unknown
---

# Usage Analytics, Cost & Impact

All three skills read the same raw material — **local session transcripts**
— and answer different questions. The shared insight: the data is already
on disk, so analysis costs almost nothing and needs no new instrumentation.

## Constituent skills (4)

| Skill | Origin | What it contributes |
|---|---|---|
| receipts | receipts | Personal usage & impact report from local session transcripts; cross-references local git history; writes markdown + self-contained HTML |
| session-report | session-report | Explorable HTML report of session usage: tokens, cache, subagents, skills, expensive prompts |
| explain-usage | built-in | Explains usage figures |
| troubleshoot | Copilot CLI | Investigates unexpected agent behavior from JSONL debug logs: why something happened, why a request was slow, why tools/subagents were used or skipped, why instructions/skills/agents did not load |

## Consolidated guidance

1. **Mine local data, don't add instrumentation.** Transcripts at
   `~/.claude/projects/**/*.jsonl` already contain everything; scanning
   them locally is cheap and requires no API calls.
2. **Keep the model out of the heavy lifting.** `receipts` pattern: do the
   mining with local file I/O and regex, and make the only model call a
   final write-up over a small (~10–20KB) summary. Cost stays constant
   regardless of history size.
3. **Cross-reference to catch self-deception.** Comparing session activity
   against `git log` reveals whether the recorded work actually produced
   commits — the honest check on an impact report.
4. **Find the tail, not the average.** The expensive prompts, the
   cache-miss patterns and the wasteful subagent fan-outs live in the tail;
   averages hide them.
5. **Cache behaviour is the biggest cost lever.** Cache hit/miss rates
   usually explain cost swings better than anything else — check them first.
6. **Separate impact from volume.** "Many sessions" is not "valuable
   work". An impact report needs outcomes (what shipped), not just counts.
7. **Publish something explorable.** An HTML report someone can drill into
   answers follow-up questions without a new analysis run.
8. **State the period explicitly.** Any usage figure without its date range
   and scope is meaningless and gets misread.

## When to use this skill

"How much have I been using this?", "justify the spend", "what's costing
so much?", "show my activity report", "which prompts are expensive?".

## Credits

This skill merges guidance from the following source skills, each the work of its respective owner and used under its original license. Preserve this attribution when reusing or re-merging.

| Owner | Skill | License |
|---|---|---|
| receipts | receipts | unknown |
| session-report | session-report | unknown |
| built-in | explain-usage | unknown |
| Copilot CLI | troubleshoot | unknown |
