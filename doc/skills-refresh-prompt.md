# Skills refresh prompt

A reusable prompt for building and refreshing the curated skill set that lives
in `claudio-backend/skills/`. Paste the **Prompt** section below to an agent
that has the SkillMD MCP server connected (or that can reach the public HTTP
API). It builds **one merged skill per core area**, writes a credits manifest,
and commits the result. Re-run it any time you want to refresh.

The skills are sourced from the open SkillMD registry
([skillmd.com](https://skillmd.com)). Every skill is attributed to its author
under its original license — that attribution is preserved through every merge
and recorded in the manifest.

---

## The strategy (v2 — merged per area)

Instead of installing individual skills, each refresh builds **one merged skill
per core area**:

1. **Select** the best 5 skills from SkillMD for the area (criteria below).
2. **Include the local skill**: if a merged skill for that area already exists
   in `claudio-backend/skills/`, read it and add it to the merge set. This is
   how manual edits and prior refreshes flow forward — nothing you hand-edit is
   lost on the next refresh.
3. **Merge** all sources into a single **superset** skill: keep every distinct
   useful instruction, and collapse sections that are exactly the same into one.
4. **Validate** the merged skill — both structurally (it must load) and for
   content freshness (the info must not be obsolete).
5. **Write** it to `claudio-backend/skills/<area-slug>/SKILL.md`.

After all areas: write the credits manifest, then commit.

### The seven core areas and their slugs

| Area | Merged skill name / folder |
|---|---|
| Agent planning | `agent-planning` |
| Coding | `coding` |
| Software architecture | `software-architecture` |
| Linux skills | `linux` |
| Agent tools usage | `agent-tools-usage` |
| Debugging | `debugging` |
| Information gathering | `information-gathering` |

---

## Usage

1. Make sure the SkillMD MCP server is configured (transport `streamable_http`,
   URL `https://api.skillmd.com/mcp`) — or that the agent can reach the HTTP
   API directly.
2. Paste the prompt below into the agent.
3. Review the merged skills and the manifest it produces.
4. Run the test suite: `cd claudio-backend && .venv/bin/python -m pytest tests/test_skills.py -q`.
5. Commit when satisfied.

---

## Prompt

> You are building and refreshing the curated Agent Skills set for this project.
>
> **Target directory:** `claudio-backend/skills/` (the directory Claudio scans
> for installed skills; see `doc/skills-and-workspace.md`).
>
> **Source:** the open SkillMD registry at `https://skillmd.com`, a
> safety-reviewed library of `SKILL.md` files. Use its MCP tools if available
> (`skillmd_search`, `skillmd_get`, `skillmd_download`); otherwise use the
> public HTTP API:
>
> - Search: `GET https://api.skillmd.com/v1/search?q=<query>`
>   → `{ items: [{ slug, type, title, description, verified, agents, category,
>   avg_rating, rating_count, raw_url }] }`
> - Detail: `GET https://api.skillmd.com/v1/skills/<owner>/<name>`
> - Raw SKILL.md: `GET https://api.skillmd.com/api/skills/<owner>/<name>/raw`
>
> Work through these **seven core areas**, one at a time:
>
> 1. **Agent planning** (`agent-planning`) — task/sprint planning,
>    orchestration, decomposition. Queries: `agent planning`, `task planning`,
>    `orchestration`, `planner`.
> 2. **Coding** (`coding`) — writing, reviewing, refactoring code.
>    Queries: `coding`, `code review`, `refactoring`, `programming`.
> 3. **Software architecture** (`software-architecture`) — system design,
>    architecture patterns. Queries: `software architecture`, `system design`,
>    `architecture`.
> 4. **Linux skills** (`linux`) — shell, system administration, command-line
>    work. Queries: `linux`, `bash`, `shell scripting`, `system administration`.
> 5. **Agent tools usage** (`agent-tools-usage`) — effective tool/function use
>    by agents. Queries: `tool use`, `agent tools`, `function calling`,
>    `tool calling`.
> 6. **Debugging** (`debugging`) — diagnosing and fixing defects.
>    Queries: `debugging`, `debug`, `troubleshooting`, `root cause`.
> 7. **Information gathering** (`information-gathering`) — research, search,
>    summarization. Queries: `research`, `web search`, `information gathering`,
>    `summarization`.
>
> For **each area**, do the following:
>
> ### A. Select the best 5
>
> Search the area's queries and pick the **top 5** skills by these criteria
> (quality over quantity):
>
> - Prefer skills marked **verified** / **Passed SkillMD review** / **Audited**.
> - Prefer **`No network calls`** skills (safer, self-contained).
> - Prefer `avg_rating >= 4.0` with a non-trivial `rating_count`.
> - Read each skill's description and skim its content; reject anything
>   low-quality, off-topic, or unsafe.
> - Do not pick near-duplicates of each other.
>
> ### B. Include the existing local skill
>
> If `claudio-backend/skills/<area-slug>/SKILL.md` already exists, read it and
> **add it to the merge set** alongside the 5 selected skills. Its content —
> including any manual edits made since the last refresh — must be carried into
> the new merged skill. Do not discard it.
>
> ### C. Merge into one superset skill
>
> Merge all sources (the 5 selected + the existing local skill, if any) into a
> single skill:
>
> - **Superset:** include every distinct, useful instruction from every source.
> - **Dedup exact matches:** where two sources contain an identical section or
>   instruction, keep one copy. (This is what makes repeated refreshes stable —
>   re-merging the same content does not duplicate it.)
> - **Organize** the merged content under clear subtopic headings.
> - **Preserve all credits** from every source (see the Credits section below).
> - **Single frontmatter** for the merged skill:
>   - `name`: the area slug (e.g. `agent-planning`) — lowercase, hyphenated,
>     equal to the folder name.
>   - `description`: one concise sentence (≤ 1024 chars) describing the whole
>     merged skill.
>   - `license`: the license (if sources share one; otherwise note "mixed —
>     see Credits").
>   - `metadata`: a mapping with `last_reviewed` (today's date, `YYYY-MM-DD`)
>     and `sources` (a list of `{owner, name, license, url}` for every merged
>     source).
>
> ### D. Validate
>
> Validate the merged skill on two axes:
>
> 1. **Structural (must load):** the `SKILL.md` starts with `---\n` YAML
>    frontmatter; `name` is lowercase-hyphenated and equals the folder name;
>    `description` is present and ≤ 1024 chars; only the allowed frontmatter
>    keys are used (`name`, `description`, `license`, `compatibility`,
>    `metadata`, `allowed-tools`).
> 2. **Content freshness (not obsolete):** read the merged content and check
>    that the commands, tool names, library/API versions, file paths, and URLs
>    it references are still current. Remove or update anything obsolete
>    (deprecated commands, dead links, superseded APIs). Record the review in
>    `metadata.last_reviewed`.
>
> If a merged skill fails structural validation, fix it before writing. If it
> contains obsolete content, update it; if you cannot verify a claim, mark it
> clearly rather than leaving stale info presented as current.
>
> ### E. Write
>
> Write the merged skill to `claudio-backend/skills/<area-slug>/SKILL.md`.
>
> ---
>
> After all seven areas:
>
> ### Credits manifest
>
> Create `claudio-backend/skills/SKILLS_MANIFEST.md` listing every merged
> area skill with:
>
> - **Area** and **merged skill name**
> - **Sources merged** — for each: owner (SkillMD/GitHub username), skill name,
>   source page URL (`https://skillmd.com/skills/<owner>/<name>`), the original
>   source repository if the skill names one, and its license
> - **Credits / attribution** — author names and any copyright/attribution line
>   present in the sources
> - **Last reviewed** date
>
> Give the manifest a short header noting the refresh date and that all skills
> are attributed to their authors under their original licenses.
>
> ### Commit
>
> Commit the changed `claudio-backend/skills/` directory and the manifest with
> a clear message describing the refresh.
>
> **Refresh behavior:** this prompt is meant to be re-run. On each refresh,
> re-search each area, merge the new selections with whatever is already on
> disk (step B), update the manifest, and commit. Manual edits you make to a
> merged skill are carried forward by step B and only removed if a later
> refresh's validation marks them obsolete.

---

## Notes

- The seven areas are fixed; tune the per-area queries if a category is thin or
  returns noise.
- The merge is a union with exact-duplicate collapse — it is intentionally
  additive so manual edits survive, but it is not a substitute for reviewing
  the merged output before committing.
- After refreshing, restart the backend so the new skills are picked up, and
  check `GET /api/v1/skills` to confirm every folder validates.
- The MCP server card is at
  `https://skillmd.com/.well-known/mcp/server-card.json` (tools:
  `skillmd_search`, `skillmd_get`, `skillmd_download`, `skillmd_trending`,
  `skillmd_recommend`).
- The companion test suite is `claudio-backend/tests/test_skills.py`; run it
  after each refresh to verify the merged skills and manifest.
