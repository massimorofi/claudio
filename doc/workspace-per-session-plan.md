# Change Plan — Per-Session Workspaces

**Status:** Draft for review · 2026-10-25
**Scope:** Claudio backend, core, web UI, VS Code extension
**Author:** Claudio (drafted from operator requirements)

---

## 1. Problem

`agent.workspace_dir` is a **single global, mutable config value**. Every request
resolves the workspace from the live config (`resolve_workspace_dir(supervisor.config)`),
so there is exactly one workspace per backend process and it can be changed at any time.

The VS Code extension binds its opened folder by writing that global key
(`claudio-vscode/src/workspace/binding.ts:15-22`, a `PUT /config`). This produces:

| # | Symptom | Cause |
|---|---------|-------|
| 1 | Resuming a session in the Web UI operates on the **wrong tree** — the last folder any VS Code window bound, not the folder the session was created in | Workspace is global, not stored on the session |
| 2 | Two VS Code windows fight; last writer wins, the other silently works on the wrong tree | Single global slot |
| 3 | A Web UI config save clobbers the workspace; `ConfigController.rebindWorkspace()` exists only to re-assert it | Config used as a context side-channel |
| 4 | Config history is full of `agent.workspace_dir` churn (20 versions, mostly workspace) | Every bind is a config write |
| 5 | Claudio runtime state (`.claudio/adk-*.db`) lands **inside the user's repo** | Runtime state keyed off the workspace root |
| 6 | `workspace_dir` accepts any string, unvalidated | No validation in `AppConfig` |

Neither `claudio_sessions` nor `claudio_runs` has a workspace column (verified against
the live DB). The session schema is at `claudio-core/src/claudio_core/db.py:17-31`.

## 2. Target model

**The workspace is a property of a session, fixed at session creation.**
It is not a global setting.

| Actor | Behaviour |
|-------|-----------|
| VS Code extension | Creates its session with `workspace = <folder opened in VS Code>` |
| Web UI, opening an existing session | Uses **that session's** workspace. No choice, no surprise |
| Web UI, starting a new session | Uses the **default** workspace, with a picker to override before the first message |
| Autonomous / scheduled jobs | Not session-bound → use the default workspace |

`agent.workspace_dir` is **demoted, not removed**: it becomes the *default for new
sessions*. Renamed to `default_workspace_dir` to make that explicit.

## 3. Non-goals (for this change)

- Multi-root VS Code workspaces. Today `primaryWorkspaceFolder()` takes
  `workspaceFolders[0]` only. Stays that way; called out in §9.
- Per-directory permissions inside a workspace. The sandbox stays "one root".
- Remote / network workspaces.
- Changing the file tools' sandboxing model itself.

---

## 4. Phase 1 — Schema (backend, no behaviour change)

Add the column using the **existing migration pattern** in
`claudio-core/src/claudio_core/db.py:260-277` (`PRAGMA table_info` → `ALTER TABLE`),
so an existing database upgrades in place.

```sql
ALTER TABLE claudio_sessions ADD COLUMN workspace_dir TEXT NOT NULL DEFAULT '';
ALTER TABLE claudio_sessions ADD COLUMN workspace_origin TEXT NOT NULL DEFAULT 'default';
-- 'default' | 'vscode' | 'webui'
```

**Why two columns:** `workspace_dir` is the mechanical value; `workspace_origin` is
provenance, so the UI can say "started in VS Code" and so a stale path can be
distinguished from a deliberately chosen one.

**Also add a workspace registry** (needed for the picker — a `SELECT DISTINCT`
over sessions only shows the past and cannot add a never-used folder):

```sql
CREATE TABLE IF NOT EXISTS claudio_workspaces (
    id          TEXT PRIMARY KEY,          -- uuid, stable identity survives renames
    path        TEXT NOT NULL UNIQUE,
    label       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    last_used_at TEXT NOT NULL DEFAULT ''
);
```

**DB methods** (`claudio-core/src/claudio_core/db.py`):

```python
async def create_session(self, session_id, title="", *, workspace_dir=None,
                        workspace_origin="default") -> dict
async def get_session_workspace(self, session_id) -> str | None
async def register_workspace(self, path, label="") -> dict
async def touch_workspace(self, workspace_id) -> None
async def list_workspaces(self) -> list[dict]
```

`create_session` keeps `workspace_dir` optional so **every existing caller keeps
working unchanged** during Phase 1.
---

## 5. Phase 2 — Validation (this is a security change, not hygiene)

The file tools are sandboxed **to the workspace**. Therefore "choose a workspace"
means "choose what the agent can read and write". Today `workspace_dir` is an
unvalidated string. Before any picker exists, add a validator.

New helper in `claudio-backend/src/claudio_backend/paths.py`:

```python
def validate_workspace_dir(raw: str, *, must_exist: bool = True) -> Path:
    """Resolve and validate a candidate workspace root."""
```

Rules:

1. Non-empty string after `expanduser()`.
2. Resolve to an absolute path (`Path.resolve()`), following symlinks deliberately.
3. Must be an existing directory when `must_exist=True`.
4. **Refuse dangerous roots:** `/`, `/home`, `/etc`, `/usr`, `/var`, `/bin`,
   `/boot`, `/root`, and any direct child of `/` that is a system directory.
5. Refuse the backend's own data directory (`data/`) as a workspace root — it
   holds the DB and would be self-referential.
6. New config key `agent.workspace_allowed_roots: list[str]` (default
   `["/home/briggen/Dev"]`). A candidate must be inside one allowed root.
   An empty allowlist disables the check (dev escape hatch).

Wire it into `AppConfig` validation so a bad value is rejected at
`PUT /config` time with `422 validation_error`, not discovered at first run.

**Note on the current machine:** `/home/briggen/Dev` is a symlink to
`/run/media/briggen/DEV`. Validation must compare resolved paths, or the same
directory will appear as two different workspaces. This is why `resolve()` is
step 2 and why the registry stores the resolved path.

---

## 6. Phase 3 — Bind the workspace at session creation

**`POST /chat/stream`** (`claudio-backend/src/claudio_backend/api/chat.py:66-80`)
currently does:

```python
if not session_id:
    session_id = str(uuid.uuid4())
    await supervisor.db.create_session(session_id, title=message[:60])
```

Becomes:

```python
if not session_id:
    session_id = str(uuid.uuid4())
    ws = validate_workspace_dir(
        body.get("workspace_dir") or config.agent.default_workspace_dir
    )
    await supervisor.db.create_session(
        session_id,
        title=message[:60],
        workspace_dir=str(ws),
        workspace_origin=body.get("workspace_origin", "webui"),
    )
else:
    existing = await supervisor.db.get_session(session_id)
    # ... existing checks ...
    ws = validate_workspace_dir(existing["workspace_dir"], must_exist=True)
```

**Key rule:** on resume, the session's stored workspace **always wins**. If the
stored path no longer exists, return a clear error — never silently fall back to
the default. A silent fallback is exactly the bug being fixed: a transcript whose
file paths refer to one tree while the tools operate on another.

```
409 workspace_unavailable
  "Session 'x' was created in /path/that/no/longer/exists.
   Start a new session, or restore the folder."
```

**Run plumbing:** `RunManager._build_request`
(`claudio-backend/src/claudio_backend/runs/manager.py:376`) already passes
`workspace=` into `RunRequest` and `build_instructions`. Change it from
`resolve_workspace_dir(self.config)` to the workspace carried on the
`RunRecord`, which is populated from the session. The plumbing is already
explicit — only the source of the value changes.

Same for `manager.py:890` (the `files_written` containment check).
---

## 7. Phase 4 — Stop binding the workspace via `PUT /config`

This is the change that removes the race and the history pollution.

**`claudio-vscode/src/workspace/binding.ts`** — replace the config write with a
session-scoped call:

```ts
// BEFORE
await client.put('/config', { agent: { workspace_dir: folder.uri.fsPath } })

// AFTER
await client.post('/sessions', {
  workspace_dir: folder.uri.fsPath,
  origin: 'vscode',
})
```

The extension should **create its own session** rather than mutate shared state.
Each VS Code window then owns its sessions and cannot interfere with another window.

**`claudio-vscode/src/config/ConfigController.ts:52-60`** — delete
`rebindWorkspace()` and its two call sites in `put()` and `restore()`. It exists
only to undo the clobbering that Phase 4 removes. Deleting it is the signal the
model is fixed.

**`claudio-vscode/src/extension.ts`**

- `bindIfPossible()` (~line 114) → becomes `ensureSession()`: create-or-resume
  the window's session with the folder path.
- `onDidChangeWorkspaceFolders` (~line 226) → **start a new session** bound to
  the new folder rather than re-binding the existing one. Continuing an existing
  conversation against a different tree is the same confusion in a new costume.
  Post `workspace.changed` to the webview as today.

**Back-compat shim:** keep `agent.workspace_dir` accepted by `PUT /config` for one
release so an older extension does not hard-fail, but log a deprecation warning.

---

## 8. Phase 5 — Move runtime state out of the workspace

Observed today: `.claudio/adk-<uuid>.db` created in the repo root. If the
workspace is the user's code repo, Claudio's own state pollutes it — and it only
escapes `git status` by accident, via the `*.db` pattern at `.gitignore:44`.

**Rule: Claudio never writes its own state inside a workspace.**

| State | Today | Target |
|-------|-------|--------|
| ADK session DBs | `<workspace>/.claudio/` | `data/workspaces/<workspace_id>/adk-*.db` |
| file-history backups | split between `code/ai/file-history/` and `data/file-history/` | `data/workspaces/<workspace_id>/file-history/` |
| SQLite DB | `data/claudio.db` | unchanged |

The two divergent `file-history` locations should be reconciled to one during this
phase — confirm which one `rewind_files` actually reads before moving it.

Add `.claudio/` to `.gitignore` explicitly (not relying on `*.db`) as a
belt-and-braces measure for any stragglers.
---

## 9. Phase 6 — Web UI: picker and visible workspace

**New endpoints**

```
GET  /api/v1/workspaces              → list registered workspaces
POST /api/v1/workspaces              → add { path, label }   (validated)
DELETE /api/v1/workspaces/{id}       → remove from registry (does not touch disk)
GET  /api/v1/workspaces/suggest      → browse dirs, for the "add" dialog
```

**Picker behaviour (new session only)**

- Shown **before the first message**, not after. Once a session exists its
  workspace is frozen — offering to change it later reintroduces the bug.
- Default selection = `default_workspace_dir`.
- Recent workspaces from the registry, most-recent-first.
- "Add workspace…" opens a directory browser backed by
  `GET /workspaces/suggest`, filtered by `workspace_allowed_roots`.
- After the first message the control becomes a **read-only badge**.

**The badge matters more than the picker.** Every session header shows the
workspace path, always, in a colour that stands out. Cheap, and it removes an
entire class of "which tree am I talking to?" confusion. On resume, if the
session came from VS Code, say so: `~/Dev/code/ai/claudio · from VS Code`.

**Existing Web UI surfaces that assume one workspace** — the Files tab, the
workspace tree/download endpoints (`api/workspace.py:36`), and the file-picker
in the composer — all take a `session_id` parameter from Phase 3 onward. They
must not keep resolving from global config.

---

## 10. Migration

Run order matters. Do not skip step 1.

**Step 1 — Reset the current bad value first.**
The live config currently points at the repo root. Before anything else:

```bash
curl -X PUT -H "Authorization: Bearer <token>" \
     -H "Content-Type: application/json" \
     -d '{"agent":{"workspace_dir":"./data/workspace"}}' \
     http://127.0.0.1:8100/api/v1/config
```

`workspace_dir` is not in `BACKEND_RESTART_KEYS` (only `server.host`,
`server.port`) and `agent` is not in `AGENT_RESTART_KEYS` (only `mcp`), so this
takes effect immediately via `reload_config()`. No restart needed.

**Step 2 — Schema migration.** Idempotent `ALTER TABLE` + `CREATE TABLE IF NOT
EXISTS`, run at startup as the existing migrations do.

**Step 3 — Backfill existing sessions.**

```sql
UPDATE claudio_sessions
SET workspace_dir = <default>, workspace_origin = 'default'
WHERE workspace_dir = '' OR workspace_dir IS NULL;
```

Existing sessions were created under an ambiguous workspace. The backfill is a
best guess; the `workspace_origin = 'default'` marker lets the UI show it as
"workspace: assumed" so nobody trusts it blindly.

**Step 4 — Seed the registry** from `SELECT DISTINCT workspace_dir FROM
claudio_sessions` plus the default, so the picker starts populated.

**Step 5 — Clean up the polluted repo.** Remove `.claudio/` from the repo root
once nothing writes there. Note: the news briefing written earlier today was
deleted and is in `.Trash-1000`, with backups under
`code/ai/file-history/ce5c4353-…/`.

---

## 11. Rollback

Each phase is independently revertible, which is the reason for the ordering.

| Phase | Rollback |
|-------|----------|
| 1 Schema | Columns are additive with defaults; old code ignores them. No rollback needed. |
| 2 Validation | Rejects values that were previously accepted. Rollback = remove validator. |
| 3 Session binding | Old code reads global config; keep writing `default_workspace_dir` on every session create so old code still works. |
| 4 VS Code | Keep the `PUT /config` shim for one release; flip a flag to re-enable it. |
| 5 Runtime state | Move back by pointing the state path at the workspace again. |
| 6 Web UI | UI-only; revert the frontend. |

The one-way door is **Step 3 of migration (backfill)** — once old sessions are
stamped with an assumed workspace, you cannot recover what they actually were.
Take a copy of `claudio.db` before running it.

---

## 12. Test plan

**Unit**

- `validate_workspace_dir`: relative path, absolute path, `~` expansion, trailing
  slash, symlinked path (the `/home/briggen/Dev` → `/run/media/briggen/DEV`
  case), non-existent path, a file instead of a directory, `/`, `/home`,
  disallowed root, allowed root, empty allowlist = disabled.
- `create_session` with and without `workspace_dir` (back-compat).
- Registry: add, duplicate path rejected (UNIQUE), touch updates `last_used_at`.

**Integration**

- Create session with workspace A → resume it → tools resolve to A even after
  `PUT /config` changes the default to B. **This is the core regression test.**
- Resume a session whose `workspace_dir` was deleted → `409 workspace_unavailable`,
  not a silent fallback.
- Two concurrent sessions on different workspaces → each file operation lands in
  its own tree.
- `GET /workspace/tree?session_id=X` returns X's tree, not the global one.

**End-to-end (the scenarios you described)**

1. Open VS Code in folder A → chat → files written to A.
2. Open the Web UI, open that session → same workspace A, badge shows "from VS Code".
3. Web UI, new session → default workspace, picker visible before first message.
4. Web UI, new session, pick folder C via the picker → operates on C.
5. Open a second VS Code window in folder D → window A is unaffected.

**Migration test:** run the migration against a copy of the current
`claudio.db` and assert row counts and backfilled values.

---

## 13. Open decisions — need your call before implementation

1. **Allowlist default.** Should `workspace_allowed_roots` default to
   `["/home/briggen/Dev"]` (safe, restrictive) or `[]` (unrestricted, current
   behaviour)? I recommend the allowlist — the picker makes the blast radius real.

2. **Session identity on folder rename.** Path-keyed identity breaks if a folder
   is renamed. The registry has a UUID, but sessions store the *path*. Do we
   store the `workspace_id` on the session instead, resolving through the
   registry? More correct, slightly more plumbing. I'd start with path and
   revisit if renames actually bite.

3. **What happens to an in-flight run when the VS Code folder changes?**
   My plan says "start a new session". Alternative: block the change until the
   run finishes. The first is simpler and matches how people actually use it.

4. **Autonomous jobs and workspaces.** `task_runner`, `service` and `maintenance`
   are not session-bound and would use the default workspace. If you ever want a
   scheduled job to operate on a specific project, the task record needs its own
   `workspace_id`. Out of scope now — flag it so it isn't a surprise later.

5. **Does the Web UI need to create a session *before* the first message?**
   The picker UX is cleaner if it does (pick workspace → session exists →
   badge). Today the session is created lazily on first message. I'd change it
   to explicit creation; it also makes "new session" a real action.

6. **Naming.** `default_workspace_dir` is clear but a breaking rename of a config
   key. Acceptable, or keep `workspace_dir` and just document the demotion?

---

## 14. Sequencing and effort

| Phase | Touches | Risk | Effort |
|-------|---------|------|--------|
| 1 Schema + DB methods | core/db.py | Low | S |
| 2 Validation | core/settings.py, backend/paths.py | Low–Med (rejects previously-valid input) | S |
| 3 Session binding | chat.py, runs/manager.py, core/db.py | **High** — core behaviour | M |
| 4 VS Code stops config-binding | binding.ts, ConfigController.ts, extension.ts | Med | S–M |
| 5 Runtime state out of workspace | supervisor, ADK wiring, file-history | Med | M |
| 6 Web UI picker + badge | new endpoints, ConfigView, session UI | Low | M |
| Migration + backfill | migration script | Med (one-way) | S |

**Recommended delivery order:** 1 → 2 → 5 → 3 → 4 → 6.

Rationale for putting **5 before 3**: moving runtime state out of the repo is
independent, immediately useful, and fixes the visible pollution without waiting
for the harder session work. Ship the obvious win first.

Phase 3 and 4 are coupled — 4 without 3 leaves the race; 3 without 4 leaves the
extension clobbering the new per-session value. Land them together or behind a
flag that flips both.

---

## 15. Recommendation

Do **Phase 1 + 2 + 5** first as one small, safe PR. It fixes the repo pollution
and closes the validation hole without touching behaviour. Then do **3 + 4** as
the real change, with the core regression test ("resume keeps its workspace even
after the default changes") written *before* the implementation.

Phase 6 is the part users see, but it is the least load-bearing. Resist doing it
first.
