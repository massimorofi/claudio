# Skills & Workspace

Where the two on-disk directories live, how each path is resolved, the format
each one expects, and how the curated skill set is refreshed.

> Cross-refs: system narrative in [architecture.md](./architecture.md);
> two-tier loading wiring in [backend.md](./backend.md); skill loader module in
> [core.md](./core.md); all settings in [configuration.md](./configuration.md).

---

## Skills Directory

- **Location:** `claudio-backend/skills/` (cwd-independent).
- **Format:** one folder per skill with a `SKILL.md` (YAML frontmatter + markdown).
- **Validation:** `claudio_core/skill_loader.py` enforces name, description and
  allowed frontmatter keys.
- **Two-tier loading:** a catalog (names + one-line descriptions, always-on in
  the system prompt) plus the full body, pulled on demand via `load_skill`.
- **API:** `GET /api/v1/skills` returns the resolved path + per-skill
  validation status.

### Canonical location & resolution

| | |
|---|---|
| Config key | `agent.skills_dir` |
| Default value | `./skills` |
| Resolves to | `claudio-backend/skills/` |
| Toggle | `agent.skills_enabled` (`true` / `false`) |

A **relative** `skills_dir` is anchored to the **backend base directory** — the
folder that contains `src/` — computed from the module's own location, **not**
the process working directory:

```python
backend_base = Path(__file__).resolve().parents[3]   # -> claudio-backend/
return (backend_base / p).resolve()                 # -> claudio-backend/skills/
```

So `./skills` always means `claudio-backend/skills/`, no matter where the
backend was launched from. An **absolute** `skills_dir` (e.g.
`/opt/claudio/skills`) is used as-is.

### Skill folder format

A skill is **one folder** containing a `SKILL.md`. Only the **direct child
folders** of the skills directory are scanned (one level deep):

```
claudio-backend/skills/
├── my-skill/
│   ├── SKILL.md          # required
│   └── ...               # optional bundled scripts / reference files
└── another-skill/
    └── SKILL.md
```

`SKILL.md` = YAML frontmatter + markdown instructions:

```markdown
---
name: my-skill
description: One sentence on when this skill is relevant.
license: MIT            # optional
compatibility: ...      # optional
metadata: {}           # optional mapping
allowed-tools: ...     # optional string
---

Full instructions go here, in markdown. Reference any bundled files by a
path relative to this skill's own directory.
```

### Validation rules (enforced by `skill_loader.py`)

- `name` must be lowercase and hyphenated (`^[a-z0-9]+(?:-[a-z0-9]+)*$`) **and**
  match the folder name exactly.
- `description` is required and must be ≤ 1024 characters.
- Only these frontmatter keys are allowed: `name`, `description`, `license`,
  `compatibility`, `metadata`, `allowed-tools`. Anything else is rejected.
- `SKILL.md` must start with `---\n`, the frontmatter must be terminated by
  `\n---\n`, and the frontmatter must be ≤ 8192 bytes.
- Duplicate skill names across folders are rejected.
- A folder that fails any of these is reported as `valid: false` with the reason
  (visible via the API) rather than silently dropped.

Discovery never imports or executes anything inside a skill folder — it only
reads `SKILL.md`. Bundled scripts are run by the agent through `run_command`
using the skill's absolute directory.

### Verifying the skills directory

`GET /api/v1/skills` returns the resolved `directory` plus every skill with its
`valid` status:

```bash
curl -s -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8100/api/v1/skills
```

---

## Workspace

- **Location:** `data/workspace/` at the repo root (when launched from root).
- **Cwd-dependent** — launch from repo root or use an absolute path.
- The agent writes deliverables here; `.claudio/` holds ADK session state.
- **Backups:** `data/file-history/<session_id>/`.

| | |
|---|---|
| Config key | `agent.workspace_dir` |
| Live value | `./data/workspace` |
| Resolves to | `<repo>/data/workspace/` |

Unlike the skills directory, the workspace is resolved with
`Path(workspace_dir).resolve()`, which resolves a relative path against the
**process working directory** (cwd), not the module location. `backend-start.sh`
launches uvicorn without changing directory, so the workspace lands wherever you
invoked the script from.

---

## 3. The Resolution Asymmetry (Gotcha)

The two directories resolve relative paths **differently**:

| Directory | Relative path anchored to | cwd-dependent? |
|---|---|---|
| Skills (`./skills`) | Backend base (`claudio-backend/`) | **No** |
| Workspace (`./data/workspace`) | Process cwd | **Yes** |

This means the workspace location **moves** if you launch the backend from a
different directory. Launching uvicorn from `claudio-backend/` instead of the
repo root would put the workspace at `claudio-backend/data/workspace` rather
than `<repo>/data/workspace`. To avoid surprises, either always launch from the
repo root, or set `workspace_dir` to an **absolute** path in
`claudio-backend/config/config.yaml`.

---

## Refreshing Skills

The curated skill set is sourced from the open SkillMD registry
([skillmd.com](https://skillmd.com)) and merged into **one skill per core
area**. The full, reusable refresh prompt lives in
[`skills-refresh-prompt.md`](./skills-refresh-prompt.md) — keep that file in
place: `claudio-backend/tests/test_skills.py` reads it to validate the
contract (area coverage, merge/validation guidance).

### The strategy (v2 — merged per area)

Instead of installing individual skills, each refresh builds **one merged skill
per core area**:

1. **Select** the best 5 skills from SkillMD for the area.
2. **Include the local skill**: if a merged skill for that area already exists
   in `claudio-backend/skills/`, read it and add it to the merge set, so manual
   edits and prior refreshes flow forward.
3. **Merge** all sources into a single **superset** skill: keep every distinct
   useful instruction, collapse identical sections into one.
4. **Validate** — structurally (it must load) and for content freshness (the
   info must not be obsolete).
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

Plus the `claudio-*` self-management skills (`claudio-autonomy`,
`claudio-self-check`, `claudio-self-config`, `claudio-self-scheduling`).

### Usage

1. Make sure the SkillMD MCP server is configured (transport `streamable_http`,
   URL `https://api.skillmd.com/mcp`) — or that the agent can reach the HTTP
   API directly.
2. Paste the prompt from `skills-refresh-prompt.md` into the agent.
3. Review the merged skills and the manifest it produces.
4. Run the test suite: `cd claudio-backend && .venv/bin/python -m pytest tests/test_skills.py -q`.
5. Commit when satisfied.

---

## Skill Authoring

When authoring a skill by hand (rather than via the refresh prompt), follow the
frontmatter schema and validation rules in the **Skill folder format** and
**Validation rules** sections above:

- `name` lowercase-hyphenated and equal to the folder name.
- `description` required, ≤ 1024 chars.
- Allowed keys only: `name`, `description`, `license`, `compatibility`,
  `metadata`, `allowed-tools`.
- `allowed-tools` declares which tools the skill expects to use; keep it accurate
  so the agent doesn't reach for tools it shouldn't.
- Reference bundled scripts/files by a path relative to the skill's own folder.
- After adding or editing a skill on disk, verify it with `GET /api/v1/skills`
  (the scan runs per request, so no backend restart is needed).
