# SkillMD MCP — Extension Description

> **Assumption:** `/workspace` was empty (no manifest, source, or README), so this
> describes the one extension actually present in this session: the **SkillMD MCP
> connector** (`skillmds`, remote endpoint `https://api.skillmd.com/mcp`).
> If you meant a different extension, drop its code into `/workspace` and I will
> rewrite this against the real thing.

---

## One-liner

Give your AI agent direct access to the open library of agent skills — search,
read, lint, and install `SKILL.md` skills without leaving the conversation.

---

## Short descriptions (store / manifest fields)

| # | Text | Use |
|---|------|-----|
| 1 | `Search, review, and install AI agent skills without leaving your chat.` | Chrome/Edge `manifest.json` description |
| 2 | `1.1M+ safety-reviewed SKILL.md files for Claude, Cursor, Codex and 60+ agents.` | Marketplace tagline |
| 3 | `Turn "find me a skill for reviewing pull requests" into an installed skill.` | Social / promo |


---

## Long description

**SkillMD MCP** connects your AI agent directly to SkillMD, the open library of
AI agent skills. Instead of copying and pasting instructions from a browser, you
ask for what you need in plain language and the agent finds it, reads it, checks
it, and puts it where your tools expect it.

The registry indexes **1,132,729 skills** from **1,947 verified publishers**,
covering **60+ agents** including Claude Code, Cursor, Codex, Claude Desktop,
Windsurf and VS Code. Every skill is a plain-text `SKILL.md` file — readable,
auditable, and version-pinned to a source commit — so nothing executes blindly in
your environment.

**No account required.** Everything works unauthenticated. Only the saved-skills
lookup needs a signed-in user.

### What it does

- **Search by task, not by filename.** Describe the job — "add a Dockerfile
  optimizer", "review pull requests for security issues" — and get ranked matches
  with category, publisher, and safety verdict.
- **Read before you install.** Pull the full `SKILL.md` body plus provenance:
  source repository, pinned commit, license, and the result of SkillMD's safety
  review.
- **Lint skills.** The local (`npx`) server lints skills and installs them
  straight to disk, so a malformed file fails loudly instead of confusing the
  model. The hosted endpoint exposes discovery and retrieval; the local package
  adds the write-to-disk and lint path.
- **Install to disk.** Fetch a skill and every companion file in one call, ready
  to write into your project or skills directory.
- **Discover what's working.** Trending and leaderboard views over real installs,
  saves and ratings, filterable by category and time window; recommendations
  seeded from a skill you already like.

### Tools exposed to the agent

| Tool | Purpose |
|------|---------|
| `skillmd_search` | Keyword / natural-language search. Filters: `category`, `verified_only`, `type` (single or pack), `min_rating`, `limit`. |
| `skillmd_get` | Full record for one skill: description, `SKILL.md` body, provenance (source repo + pinned commit), license, safety verdict, companion-file listing. |
| `skillmd_download` | Full contents of any public skill — the `SKILL.md` plus every companion file for packs, ready to write to disk. |
| `skillmd_recommend` | Related skills in the same category as a given slug, or overall trending when no slug is given. |
| `skillmd_trending` | Leaderboard: installs, saves and ratings. Filters: `range` (`30d` / `all`), `category`, `limit`. |
| `load_mcp_resource` | Loads resources exposed by the SkillMD MCP server. |

### Install

One command, for every project:

```bash
claude mcp add skillmd -- npx -y skillmds@latest
```

Add it to a single repo only with `--scope project`. Remote URL for any client
that supports remote MCP servers (nothing to install):

```
https://api.skillmd.com/mcp
```

Generic client config:

```json
{
  "mcpServers": {
    "skillmds": { "command": "npx", "args": ["-y", "skillmds@latest"] }
  }
}
```

Requires Node 18 or later. The server identifies itself as `skillmd` during the
handshake. Pass nothing else — any argument routes to the `skillmd` CLI instead
of starting the server.

### Configuration

| Variable | Required | Default | Notes |
|----------|----------|---------|-------|
| `SKILLMD_TOKEN` | No | — | Only `skillmd_list_saved` needs a signed-in user. Everything else works unauthenticated. |
| `SKILLMD_API` | No | `https://api.skillmd.com` | Point at a different (e.g. self-hosted) registry. |

### Example prompts

- "Find me a skill for reviewing pull requests."
- "Is there a safety-reviewed skill that optimizes Dockerfiles?"
- "What's trending in DevOps this week?"
- "I like `obra/superpowers` — what else is in that category?"
- "Download `anthropic/docx` and write it into `.claude/skills/`."

### Categories

Coding & Dev Tools · AI & ML · DevOps & Infra · Web & Frontend ·
Integrations & APIs · Productivity · Security · Docs & Writing

### Security posture

Skills are plain-text instructions, not executable code — nothing runs on fetch.
Each skill carries a safety verdict and provenance (source repository and pinned
commit), so you can audit what you are adding and to exactly which version.
Treat third-party skill content as untrusted guidance regardless: read it before
you rely on it, and never let it override your own access permissions.

---

## Verification

- Registry figures above (1,132,729 skills indexed · 1,947 verified publishers ·
  60+ agents) were read from skillmd.com on 2026-10-27 and move constantly.
- Tool list was read from the live MCP tool surface in this session, not from
  documentation.
- Install commands and env vars were read from the SkillMD MCP setup page.
