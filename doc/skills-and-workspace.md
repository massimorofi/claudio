# Skills directory and workspace

Where the two on-disk directories live, how each path is resolved, and the
format each one expects. The original pre-refactor skills directory was lost
and is **not recoverable from this repo's git history** (it was never
committed, and the `_upstream/` tree that held it is gone too), so this
document is the reference for recreating it.

---

## 1. Skills directory

**Canonical location: `claudio-backend/skills/`**

| | |
|---|---|
| Config key | `agent.skills_dir` |
| Default value | `./skills` |
| Live value | `./skills` |
| Resolves to | `claudio-backend/skills/` |
| Toggle | `agent.skills_enabled` (`true` / `false`) |

### How the path is resolved

A **relative** `skills_dir` is anchored to the **backend base directory** —
the folder that contains `src/` — computed from the module's own location,
**not** the process working directory. See
[`_resolve_skills_dir`](../claudio-backend/src/claudio_backend/api/management.py):

```python
backend_base = Path(__file__).resolve().parents[3]   # -> claudio-backend/
return (backend_base / p).resolve()                 # -> claudio-backend/skills/
```

So `./skills` always means `claudio-backend/skills/`, no matter where the
backend was launched from. An **absolute** `skills_dir` (e.g.
`/opt/claudio/skills`) is used as-is.

> Note: this differs from the workspace, which resolves against the process
> cwd. See §2 and the asymmetry note below.

### Skill folder format

A skill is **one folder** containing a `SKILL.md`. Only the **direct child
folders** of the skills directory are scanned (one level deep); each must
look like this:

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

Validation rules enforced by
[`skill_loader.py`](../claudio-core/src/claudio_core/skill_loader.py):

- `name` must be lowercase and hyphenated (`^[a-z0-9]+(?:-[a-z0-9]+)*$`)
  **and** match the folder name exactly.
- `description` is required and must be ≤ 1024 characters.
- Only these frontmatter keys are allowed: `name`, `description`,
  `license`, `compatibility`, `metadata`, `allowed-tools`. Anything else
  is rejected.
- `SKILL.md` must start with `---\n`, the frontmatter must be terminated
  by `\n---\n`, and the frontmatter must be ≤ 8192 bytes.
- Duplicate skill names across folders are rejected.
- A folder that fails any of these is reported as `valid: false` with the
  reason (visible via the API) rather than silently dropped.

Discovery never imports or executes anything inside a skill folder — it
only reads `SKILL.md`. Bundled scripts are run by the agent through
`run_command` using the skill's absolute directory.

### Verifying the skills directory

`GET /api/v1/skills` returns the resolved `directory` plus every skill
with its `valid` status, so you can confirm the path and spot broken
folders:

```bash
curl -s -H "Authorization: Bearer <token>" \
  http://127.0.0.1:8100/api/v1/skills
```

---

## 2. Workspace

**Canonical location: `data/workspace/` at the repo root**
(when the backend is launched from the repo root).

| | |
|---|---|
| Config key | `agent.workspace_dir` |
| Live value | `./data/workspace` |
| Resolves to | `<repo>/data/workspace/` |

This is where the agent writes its deliverables. Inside it you will also
find `.claudio/` (ADK session state). Per-session file backups live in a
separate `data/file-history/` directory.

### How the path is resolved

Unlike the skills directory, the workspace is resolved with
`Path(workspace_dir).resolve()`, which resolves a relative path against the
**process working directory** (cwd), not the module location:

```python
workspace = Path(self.config.agent.workspace_dir).resolve()
```

`backend-start.sh` launches uvicorn without changing directory, so the
workspace lands wherever you invoked the script from. Run it from the repo
root and `./data/workspace` → `<repo>/data/workspace`.

---

## 3. The resolution asymmetry (gotcha)

The two directories resolve relative paths **differently**:

| Directory | Relative path anchored to | cwd-dependent? |
|---|---|---|
| Skills (`./skills`) | backend base (`claudio-backend/`) | **No** |
| Workspace (`./data/workspace`) | process cwd | **Yes** |

This means the workspace location **moves** if you launch the backend from
a different directory. Launching uvicorn from `claudio-backend/` instead of
the repo root would put the workspace at `claudio-backend/data/workspace`
rather than `<repo>/data/workspace`. To avoid surprises, either always
launch from the repo root, or set `workspace_dir` to an **absolute** path
in `claudio-backend/config/config.yaml`.

---

## 4. Config file location

Both settings live in **`claudio-backend/config/config.yaml`** (the live
config; `BASE_DIR` = `claudio-backend/`). The relevant block:

```yaml
agent:
  skills_enabled: true
  skills_dir: ./skills          # -> claudio-backend/skills/
  workspace_dir: ./data/workspace   # -> <repo>/data/workspace (cwd-dependent)
```

The config file is gitignored (it holds the LLM key and auth token); a
fresh clone falls back to the built-in defaults in
[`settings.py`](../claudio-core/src/claudio_core/settings.py).

---

## 5. Skills ARE injected into the agent prompt

This was once a gap; it is now closed. At runtime the engine scans the
resolved skills directory with `scan_skills` and passes the resulting
`entries` into `build_static_instructions(..., skill_entries=...)`, so the
Tier-1 skills catalog (names + one-line descriptions) is rendered into the
agent's system prompt every turn. The three read-only skill tools
(`list_skills`, `load_skill`, `read_skill_resource`) are registered on the
agent so it can pull a skill's full body on demand. The scan runs per
request, so a skill added or edited on disk is picked up without restarting
the backend. See the **Skills** section of
[`backend.md`](./backend.md) for the full two-tier loading mechanism.

Skills are also surfaced through `GET /api/v1/skills` (the UI), and the
self-improvement loop can draft and promote new skills into this same
directory — see the **Self-improvement loop** section of
[`backend.md`](./backend.md).
