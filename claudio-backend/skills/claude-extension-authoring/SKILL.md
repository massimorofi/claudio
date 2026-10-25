---
name: claude-extension-authoring
description: One-surface summary of every skill for extending Claude Code itself —
  authoring skills, slash commands, subagents, hooks, plugin scaffolding, plugin settings,
  hookify rules, automation recommendations and first-time setup. Use when asked to
  create or modify a skill, write a slash command, build a plugin or "mod", add a
  hook, decide which automations a repo needs, or onboard/import a project into Claude
  Code.
license: mixed — see Credits
metadata:
  last_reviewed: '2026-10-25'
  sources:
  - owner: plugin-dev
    name: skill-development
    license: unknown
  - owner: skill-creator
    name: skill-creator
    license: unknown
  - owner: built-in
    name: run-skill-generator
    license: unknown
  - owner: built-in
    name: workflow-authoring
    license: unknown
  - owner: example-plugin
    name: example-skill
    license: unknown
  - owner: example-plugin
    name: example-command
    license: unknown
  - owner: plugin-dev
    name: plugin-structure
    license: unknown
  - owner: plugin-dev
    name: plugin-settings
    license: unknown
  - owner: built-in
    name: plugin-authoring
    license: unknown
  - owner: plugin-dev
    name: agent-development
    license: unknown
  - owner: plugin-dev
    name: command-development
    license: unknown
  - owner: plugin-dev
    name: hook-development
    license: unknown
  - owner: hookify
    name: writing-hookify-rules
    license: unknown
  - owner: claude-code-setup
    name: claude-automation-recommender
    license: unknown
  - owner: built-in
    name: setup-claude
    license: unknown
  - owner: built-in
    name: import-to-claude-code
    license: unknown
  - owner: Copilot CLI
    name: agent-customization
    license: unknown
  - owner: Copilot CLI
    name: migrate-customizations
    license: unknown
  - owner: GitHub app
    name: factory-creator
    license: unknown
---

# Claude Code Extension Authoring

This area covers the *meta* skills: everything about teaching Claude Code new
capabilities rather than doing the end-user task itself.

## Constituent skills (19)

| Skill | Origin | What it contributes |
|---|---|---|
| skill-development | plugin-dev | Skill structure, description writing, progressive disclosure |
| skill-creator | skill-creator | Create, modify and **benchmark** skills; run evals on skill performance |
| run-skill-generator | built-in | Authors the per-project `run-<unit>` skill: how to build, launch and drive this repo's app |
| workflow-authoring | built-in | Authoring multi-step workflows |
| example-skill | example-plugin | Reference template for a plugin skill |
| example-command | example-plugin | Example user-invoked slash command + frontmatter options |
| plugin-structure | plugin-dev | Scaffolding: `plugin.json`, component layout, `${CLAUDE_PLUGIN_ROOT}` |
| plugin-settings | plugin-dev | State via `.local.md` files, YAML frontmatter, per-project config |
| plugin-authoring | built-in | Make a "mod": live pane, status line, toast, slash command, hook on tool calls/prompts |
| agent-development | plugin-dev | Subagents: frontmatter, descriptions, tools, examples, colors |
| command-development | plugin-dev | Slash commands: arguments, frontmatter, file references, organization |
| hook-development | plugin-dev | PreToolUse/PostToolUse/Stop, validation, event-driven automation |
| writing-hookify-rules | hookify | Rule-file syntax and patterns for the hookify plugin |
| claude-automation-recommender | claude-code-setup | Analyzes a codebase, recommends hooks/subagents/skills/plugins/MCP |
| setup-claude | built-in | First-time project configuration |
| import-to-claude-code | built-in | Bring an existing project under Claude Code |
| agent-customization | Copilot CLI | General agent customization bundles |
| migrate-customizations | Copilot CLI | Moving customization bundles between hosts |
| factory-creator | GitHub app | Generating a factory of agents/skills |

## Consolidated guidance

1. **Description is the trigger.** A skill is only found if its `description`
   names the user's phrasing. Write it as "Use when the user says X, asks for Y,
   mentions Z", not as an abstract title.
2. **Progressive disclosure.** Keep `SKILL.md` short; push detail into sibling
   reference files that are read on demand. Never front-load everything.
3. **One concern per unit.** Skills, commands, agents and hooks each have their
   own frontmatter and lifecycle. Do not mix a hook's validation logic into a
   skill's prose.
4. **Paths are relative to the plugin root.** Use `${CLAUDE_PLUGIN_ROOT}` so the
   plugin works wherever it is installed; never hardcode absolute paths.
5. **Hooks guard, skills instruct.** Use PreToolUse/PostToolUse/Stop for hard
   validation and side effects; use skills for judgment-based guidance.
6. **Measure before you ship.** Follow `skill-creator`: run evals on the new
   skill against realistic prompts before declaring it done.
7. **Recommend per repo, not globally.** `claude-automation-recommender`
   pattern: inspect the actual codebase first, then propose the smallest set of
   automations that changes behaviour.
8. **Settings live with the project.** Per-project config in `.local.md` with
   YAML frontmatter keeps state out of the code and out of the plugin.

## When to use this skill

Any request of the form: "make a skill for…", "add a slash command…", "write a
hook that…", "package this as a plugin", "what should I automate in this repo?".

## Credits

This skill merges guidance from the following source skills, each the work of its respective owner and used under its original license. Preserve this attribution when reusing or re-merging.

| Owner | Skill | License |
|---|---|---|
| plugin-dev | skill-development | unknown |
| skill-creator | skill-creator | unknown |
| built-in | run-skill-generator | unknown |
| built-in | workflow-authoring | unknown |
| example-plugin | example-skill | unknown |
| example-plugin | example-command | unknown |
| plugin-dev | plugin-structure | unknown |
| plugin-dev | plugin-settings | unknown |
| built-in | plugin-authoring | unknown |
| plugin-dev | agent-development | unknown |
| plugin-dev | command-development | unknown |
| plugin-dev | hook-development | unknown |
| hookify | writing-hookify-rules | unknown |
| claude-code-setup | claude-automation-recommender | unknown |
| built-in | setup-claude | unknown |
| built-in | import-to-claude-code | unknown |
| Copilot CLI | agent-customization | unknown |
| Copilot CLI | migrate-customizations | unknown |
| GitHub app | factory-creator | unknown |
