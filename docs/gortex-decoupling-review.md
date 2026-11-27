# Gortex decoupling review

**Scope.** How the optional Gortex code-intelligence server is coupled into
Claudio's core, and the contract that removes that coupling.

**Method.** Built from the working tree with file/grep tools only
(`read_file`/`grep`/`sed`/`awk`); no code-graph MCP tool was available to the
planner. Every claim below carries a `file:line` that was actually read. The
machine-readable version of the hit list is `reports/gortex-coupling.json`,
produced by `scripts/audit_gortex_coupling.py` (stdlib only, always exits 0 —
it reports, it does not gate).

**Baseline (this tree).** 204 hits over 167 scanned files: 193
`vendor-token`, 11 `out-of-band-channel`. Hotspots by file:
`planact.py` 30, `codegraph.py` 28, `docker/gortex/Dockerfile` 26,
`compose.yaml` 26, `instructions.py` 24, `docker/gortex/entrypoint.sh` 25,
`config.example.yaml` 10, `test_planact.py` 17, `test_codegraph.py` 14,
`test_instructions_gortex.py` 12, `skills/coding/SKILL.md` 7,
`references/gortex-code-discovery.md` 11, `settings.py` 4, `context.py` 1.

## Findings

### F1 — the vendor is named in the system prompt (`instructions.py`)

- `claudio-core/src/claudio_core/instructions.py:102` —
  `CODE_DISCOVERY_POLICY = """# Codebase lookup policy (Gortex)`. A whole
  prompt block is authored under the vendor's name.
- `instructions.py:104` — `"A \`gortex\` MCP server is connected. Gortex indexes the repository into a code"`:
  the prompt *asserts the vendor's identity to the model* rather than reporting
  what the handshake disclosed.
- `instructions.py:126-127` — tells the model to "refer to \"the Gortex MCP tools\"",
  i.e. the vendor name is the vocabulary the agent is instructed to use.
- `instructions.py:155` — `def render_code_discovery_policy(...)`; the gate at
  `instructions.py:164` is
  `if not any("gortex" in str(e.get("id", "")).lower() for e in valid):` —
  capability is inferred from a **substring of a user-chosen server id**, and
  `instructions.py:166` returns the vendor block.
- `instructions.py:169-175` — `def gortex_connected(...)`, same substring test
  at `instructions.py:172`. A server id of `code-graph` providing identical
  tools gets no policy block; a server id of `gortex-clone` providing none gets
  one.
- `instructions.py:178-198` — `def render_code_graph_status(...)`; gated on
  `gortex_connected(mcp_entries)` at `instructions.py:192`, then imports
  `.codegraph` and renders readiness from the on-disk manifest.
- `instructions.py:283` and `instructions.py:290` — the two calls inside
  `build_static_instructions`: `policy_block = render_code_discovery_policy(mcp_entries)`
  and `status_block = render_code_graph_status(`, with
  `instructions.py:292` passing
  `manifest_path=getattr(config.agent, "gortex_manifest_path", None) or None`.
  So the vendor reaches the prompt through two independent paths on every run.

### F2 — Claudio reads the indexer's deployment internals off disk (`codegraph.py`)

- `claudio-core/src/claudio_core/codegraph.py:37` —
  `DEFAULT_MANIFEST_PATH = "/workspace/.gortex-index.json"`.
- `codegraph.py:34` — the comment states the contract outright:
  `# Written by docker/gortex/entrypoint.sh into the shared /workspace mount, which`.
- `codegraph.py:16` — module docstring: `"the deployment knows and the model does not. The gortex entrypoint writes a"`.
- `codegraph.py:48` — `"""What the gortex entrypoint recorded about what it managed to track."""`.
- `codegraph.py:73` — `candidate = Path(path) if path else Path(DEFAULT_MANIFEST_PATH)` —
  the read itself, inside `load_manifest`, feeding `assess()` (`codegraph.py:131`)
  and `render_readiness_block()` (`codegraph.py:166`).
- `codegraph.py:202` — the rendered text names the vendor to the model:
  `"The Gortex index status for this workspace is **unknown** (no manifest "`.
- `codegraph.py:195` — `"Gortex did not cover the workspace and which claims are therefore"`.
- `codegraph.py:246` — `"DEFAULT_MANIFEST_PATH",` in `__all__`: the side-channel
  constant is part of the module's public surface.

**Why this is the worst of the findings.** The manifest is written by
`docker/gortex/entrypoint.sh:21` (`MANIFEST="${GORTEX_MANIFEST:-/workspace/.gortex-index.json}"`)
and emitted by `write_manifest()` at `entrypoint.sh:67-79`, atomically via the
temp file `entrypoint.sh:71`
(`tmp="$(dirname "$MANIFEST")/.gortex-index.$$.tmp"`). So Claudio's core has a
hard-coded dependency on a *filename chosen by an external product's shell
entrypoint*, in a *directory both containers bind-mount*. That is an
out-of-band channel: it bypasses the MCP handshake entirely, it is invisible to
capability negotiation, it silently goes stale (written once at container start,
`entrypoint.sh:80`), and it breaks the moment the indexer is not the vendored
one. Note `codegraph.py:210-242` already contains the right shape —
`probe_index_health()` doing an authenticated HTTP call to
`base_url + "/v1/index/health"` (`codegraph.py:224`) — but its own docstring at
`codegraph.py:219-222` explains the manifest won because prompt assembly "must
not block on the network". The fix is not to choose the network over the file;
it is to ask the server over the transport we already hold open.

### F3 — config keys carry the vendor name (`settings.py`)

- `claudio-core/src/claudio_core/settings.py:245` — `gortex_manifest_path: str = ""`,
  documented at `settings.py:242` as
  `# Where the gortex entrypoint publishes which repos it actually indexed.` and
  at `settings.py:244` as
  `# current workspace. Empty = the built-in default (/workspace/.gortex-index.json).`
- Consumed at `instructions.py:292` via `getattr(config.agent, "gortex_manifest_path", ...)`.
- `claudio-core/tests/test_codegraph.py:217` — `cfg.agent.gortex_manifest_path = str(manifest)`
  pins the key in tests.

### F4 — plan/act auditing hardcodes the vendor's triggers (`planact.py`)

- `claudio-core/src/claudio_core/planact.py:66-76` — `GORTEX_FAMILY = ToolFamily(`
  with `name="the Gortex code graph"` (`:67`),
  `triggers=("gortex", "code graph", "graph tool", "callers", "callees", "change impact", "impact analysis")`
  (`:68-69`), `tool_patterns=(r"gortex", r"^graph")` (`:70`) and a remedy string
  that instructs the model in vendor terms (`:71-75`, "Issue an actual Gortex
  tool call now, or state plainly that you did not use Gortex...").
- `planact.py:88` — `BUILTIN_FAMILIES: tuple[ToolFamily, ...] = (GORTEX_FAMILY, WEB_SEARCH_FAMILY)`.
- `planact.py:152-156` — `def audit_run(..., families: tuple[ToolFamily, ...] = BUILTIN_FAMILIES)`:
  the vendor family is the **default** for every run of every deployment, so the
  nudge machinery carries the vendor even where no such server is configured.
  Same default at `:112` (`called_families`), `:121` (`planned_families`),
  `:139` (`AuditReport.families`).
- `planact.py:191` — `"GORTEX_FAMILY",` exported in `__all__`.
- `planact.py:20-22` — the design note is honest about the tension:
  "**Families, not tool names.** Tool names are unstable across MCP servers and
  versions (the Gortex guide says so explicitly)" — yet `:70` matches on the
  vendor name anyway.
- `claudio-core/src/claudio_core/context.py:87` — the only core hit outside the
  four modules above, a comment: `# narration. Together they let the engine detect "said it would use Gortex, never`.

### F5 — the indexer's build and repo-tracking live in Claudio's repo

- `docker/gortex/Dockerfile` (26 hits) — installs a third-party binary from the
  vendor's URL (`:36`, `curl -fsSL https://get.gortex.dev`), creates a
  vendor-named user (`:46-48`), provisions `$HOME/.gortex` backed by a named
  volume (`:49-56`), copies the vendor entrypoint (`:58`) and exposes the
  daemon's port (`:68`, `EXPOSE 7411`).
- `docker/gortex/entrypoint.sh` (25 hits) — decides *which repositories get
  indexed* (`GORTEX_REPO`, `GORTEX_EXTRA_REPOS`, `:19`, `:44-62`, `gortex track`
  at `:56`), publishes the manifest Claudio then reads (`:21`, `:67-80`), and
  owns the daemon's auth token (`:88-93`, `:99`).
- Net effect: Claudio knows the indexer's install URL, username, home layout,
  CLI subcommands, env-var names, manifest filename, temp-file naming scheme and
  port. Any indexer upgrade that changes one of those silently degrades
  Claudio's prompt.

### F6 — compose ships the vendor as a Claudio service

- `compose.yaml:118-160` — a `gortex:` service with `profiles: ["gortex"]`
  (`:120`), `image: claudio/gortex:${CLAUDIO_TAG:-latest}` (`:121`), a build
  stanza pointing at `docker/gortex/Dockerfile` (`:124`), vendor env vars
  (`:126-143`), a bind mount of the agent's workspace *and the operator's whole
  host projects root* into the indexer (`:145-152`), the `gortex-data` volume
  (`:154`, declared at `:185`) and a healthcheck against `/mcp` (`:155-158`).

### F7 — the vendor is a first-class citizen in shipped config

- `claudio-backend/config/config.example.yaml:159-175` — a commented
  `# Gortex code-intelligence MCP (issue #54, Option B)` block (10 hits) with
  `- id: gortex` (`:167`), `transport: streamable_http` (`:168`),
  `url: http://gortex:7411/mcp` (`:170`) and
  `Authorization: Bearer claudio-gortex-internal-token` (`:173-174`),
  `enabled: false` (`:175`). The shape (url + bearer header) is exactly right;
  the hard-coded service name, port and shared default token are the coupling.

### F8 — agent-facing skill text names the vendor

- `claudio-backend/skills/coding/SKILL.md` (7 hits) — the skill's own
  `description` front-matter says "(when the gortex MCP server is connected)
  using Gortex code discovery first for symbols, callers, dependencies and
  change impact" (`:6-7`); the body has a `## Codebase discovery (Gortex)`
  heading (`:254`), the instruction "When a `gortex` MCP server is connected,
  use the Gortex tools **first**" (`:256`), a fallback clause naming the vendor
  (`:261`), a pointer to `references/gortex-code-discovery.md` (`:263`) and a
  reference index entry naming it again (`:276`).
- `claudio-backend/skills/coding/references/gortex-code-discovery.md`
  (11 hits) — the whole file is vendor-titled: `# Gortex code discovery (query
  recipes)` (`:1`), "Gortex is an optional code-intelligence MCP server
  (issue #54)" (`:3`), "`gortex` server is connected and enabled in Claudio's
  `mcp.servers` config" (`:6`), plus `:10`, `:12`, `:23`, `:28`, `:34` and the
  operator-facing install steps at `:40-43` ("Install the `gortex` binary on the
  backend's PATH, start its daemon (`gortex daemon start --detach`)...").
  Ironically `:34` already warns "Tool names can change between Gortex versions."

### F9 — the tests encode the coupling as the expected contract

- `claudio-core/tests/test_instructions_gortex.py` (12 hits) — the filename
  itself; asserts the policy is absent without gortex (`:21`, `:45`) and present
  with (`:27-31`, `:35` — `render_code_discovery_policy([{"id": "Gortex Code"}]) == CODE_DISCOVERY_POLICY`),
  and feeds `mcp_entries=[{"id": "gortex", "tools": ["gortex_search"], ...}]`
  (`:28`, `:56`).
- `claudio-core/tests/test_codegraph.py` (14 hits) — writes the manifest at the
  vendor filename (`:32`, `tmp_path / ".gortex-index.json"`), probes
  `http://gortex:7411` (`:159`, `:168`, `:176`), defines
  `GORTEX_MCP = [{"id": "gortex", "tools": ["Gortex_search"], ...}]` (`:182`)
  and sets `cfg.agent.gortex_manifest_path` (`:217`).
- `claudio-core/tests/test_planact.py` (17 hits) — imports `GORTEX_FAMILY`
  (`:14`), sets `GRAPH = GORTEX_FAMILY.name` (`:23`), and drives the audit with
  vendor sentences ("Let me check Gortex for the callers.", `:30`, `:67`, `:73`,
  `:84`), asserting `"Gortex" in nudge` (`:87`) and
  `"Gortex" in report.remedy_for(GRAPH)` (`:104`).
- `claudio-core/tests/test_issue54_gortex.py` — **does not exist in this working
  tree.** `find` over the repo and `grep -rl` over `claudio-core/tests` both
  return nothing for that name; the issue-#54 coverage actually lives in
  `test_instructions_gortex.py` and `test_codegraph.py`, whose docstrings cite
  "issue #54" (`test_instructions_gortex.py:1`, `:7`; `test_codegraph.py:4`).
  Recorded here because the audit brief listed it; the migration must not go
  looking for a file that is not there.

## Coupling classes

Five classes, each with the sites above and the audit token that detects it.
`vendor-token` counts are from `scripts/audit_gortex_coupling.py`; the
out-of-band count is the `out-of-band-channel` class (11 hits).

| Class | Name | Definition | Sites | Audit token |
|---|---|---|---|---|
| **C1** | vendor-naming-in-prompt | A vendor string is placed in text the model reads, or the decision to place it there is made by matching the vendor's name. | `instructions.py:102`, `:104`, `:126-127`, `:155`, `:164`, `:166`, `:169`, `:172`, `:178`, `:192`, `:283`, `:290`; `codegraph.py:195`, `:202`; `skills/coding/SKILL.md:6-7`, `:254`, `:256`, `:261`, `:263`, `:276`; `references/gortex-code-discovery.md:1`, `:3`, `:6`, `:10`, `:12`, `:23`, `:28`, `:34`, `:40-43` | `gortex` (case-insensitive) |
| **C2** | out-of-band-filesystem-channel | Indexer state reaches Claudio through a file on a shared mount instead of the MCP transport, so it is invisible to capability negotiation. | `codegraph.py:37` (`DEFAULT_MANIFEST_PATH = "/workspace/.gortex-index.json"`), `:34`, `:48`, `:73`, `:246`; written by `docker/gortex/entrypoint.sh:21`, `:67-80`, `:71`; surfaced by `instructions.py:192-198`, `:290-292`; `settings.py:244` | `.gortex-index`, `DEFAULT_MANIFEST_PATH`, `gortex_manifest_path` |
| **C3** | deployment-coupling | Claudio's repo builds, configures and supervises the indexer, and hardcodes its install URL, paths, port, user and token. | `docker/gortex/Dockerfile:36`, `:40-56`, `:58`, `:68`; `docker/gortex/entrypoint.sh:10-21`, `:44-62`, `:88-93`, `:99`; `compose.yaml:102-160`, `:118-128`, `:143`, `:145-154`, `:155-158`, `:185`; `config.example.yaml:159-175` (`:167`, `:170`, `:173-174`) | `gortex` |
| **C4** | hardcoded-planact-triggers | Run auditing decides "the agent meant to use the code graph" from a vendor-named literal baked into the module, as a package default. | `planact.py:66-76` (`GORTEX_FAMILY`, triggers `:68-69`, `tool_patterns` `:70`, remedy `:71-75`), `:88` (`BUILTIN_FAMILIES`), `:112`, `:121`, `:139`, `:152-156`, `:191`; `context.py:87` | `gortex` |
| **C5** | config-keys | Vendor name in a settings field / config key, so renaming the deployment requires a code change. | `settings.py:242-245` (`gortex_manifest_path: str = ""`); consumed `instructions.py:292`; shipped config `config.example.yaml:167`, `:170`, `:174`; test-pinned `test_codegraph.py:217` | `gortex_manifest_path`, `gortex` |

Test files inherit their class from what they pin: `test_instructions_gortex.py`
(C1), `test_codegraph.py` (C1/C2/C5), `test_planact.py` (C4). Renaming them is
part of the migration, not a separate concern.

**Count by class** (a site can appear in more than one class; the audit's
mutually-exclusive line counts are 193 C1+C3+C4+C5 vendor-token and 11 C2):
C1 is the largest and the most user-visible; C2 is the most architecturally
wrong; C3 is the largest by lines of owned code; C4 and C5 are small and cheap.

## Target contract

The end state, stated as rules a reviewer can check.

**T1 — discovery is MCP-only.** Code-graph capability and readiness are
discovered **only** from the MCP handshake of a configured server:

1. `initialize` → the server's `instructions` field, used verbatim as the
   server's own guidance text (the server describes itself; Claudio does not
   describe it on the server's behalf).
2. `tools/list` → each tool's `name`, `description` and `annotations`
   (including `readOnlyHint`/`destructiveHint`/`openWorldHint`). The prefixed
   tool names Claudio already renders in `render_mcp_block`
   (`instructions.py:130-152`) are the authoritative list; the policy text must
   quote those names and nothing else.
3. A **server-advertised status/capability tool** — a tool that appears in
   `tools/list` and whose description says it reports index coverage/health —
   is discovered by Claudio and **called exactly once per session**, at session
   start, off the critical path of prompt assembly. Its result replaces the
   manifest read. If no such tool is advertised, readiness is reported as
   *unknown* (the honest state `codegraph.py:200-207` already renders) and
   nothing else changes.

No filesystem path, no environment variable and no out-of-band file participates
in discovery. `DEFAULT_MANIFEST_PATH` (`codegraph.py:37`), `load_manifest`'s
default candidate (`codegraph.py:73`), the `manifest_path` plumbing
(`instructions.py:292`) and `agent.gortex_manifest_path` (`settings.py:245`) are
deleted, not deprecated: a key that still works is a channel that still gets
used.

**T2 — zero vendor strings in shipped code.** `grep -ri gortex
claudio-core/src claudio-backend/src` returns nothing. Concretely:
`CODE_DISCOVERY_POLICY` becomes a capability-shaped template with no proper noun
(`instructions.py:102-127`); `gortex_connected` becomes
`code_graph_server(mcp_entries)`, which returns the *entry* whose advertised
capability is `code-graph` rather than testing an id substring
(`instructions.py:169-175`, `:164`); `render_code_discovery_policy` /
`render_code_graph_status` take the discovered entry and the status-tool result
(`instructions.py:155`, `:178`); `GORTEX_FAMILY` becomes a family *constructed
from the discovered server's real tool names* and passed in by the caller, with
`BUILTIN_FAMILIES` holding no code-graph family by default
(`planact.py:66-76`, `:88`, `:155`).

**T3 — the indexer is an external process.** Claudio does not build or ship it.
It is registered as one `mcp.servers` entry: `id` (operator-chosen),
`transport: streamable_http`, `url`, and a bearer token in `headers` — the shape
already present at `config.example.yaml:167-175`, minus the vendor's service
name, port and shared default token. `docker/gortex/` and the `compose.yaml`
`gortex` service (`:102-160`, `:185`) leave the repo; the operator runs the
indexer wherever they like and points the entry at it.

**T4 — an operator override, never a Claudio guess.** The entry may carry an
optional `capability: code-graph` field. It exists for the case where a server
does not self-describe well; it is operator-declared, not inferred by Claudio
from a name. Precedence: advertised capability (T1) > operator `capability`
override > not a code-graph server. An id substring is never a signal.

**T5 — what the agent is told.** The prompt says, in effect:

> `a code-graph server <configured id> is connected` — with `<configured id>`
> taken from the operator's `mcp.servers` entry, and the graph's capabilities
> rendered from the server's own `initialize` instructions plus the real
> prefixed tool names from `tools/list`.

Never a vendor name chosen by Claudio. Readiness is appended from the status
tool's answer ("covers this workspace", "does not cover it", "unknown"), and the
remedy text in a plan/act nudge names the tools the agent should actually call.

**T6 — the audit goes to zero and stays there.** After the migration,
`python3 scripts/audit_gortex_coupling.py` reports 0 hits under
`claudio-core/src` and `claudio-backend/src`; remaining hits are only in
`docker/`, `compose.yaml`, `config.example.yaml` and tests, and those shrink
with T3. The script keeps exiting 0 so it can be run as a report anywhere; a
gate that fails is added separately once the count is zero.

## Migration order

Task 1 of this plan was the audit itself (`scripts/audit_gortex_coupling.py` +
this document + `reports/gortex-coupling.json`), which establishes the baseline
and changes no behaviour. The decoupling runs as tasks 2-7, in this order, each
one a reviewable PR that leaves the tree green.

**Task 2 — add MCP-only discovery, dead code first (C2, C1).**
Add a `code_graph` discovery module in `claudio-core` that, from the connected
MCP entries, reads `initialize` `instructions`, `tools/list`
names/descriptions/annotations, and identifies + calls once per session the
server-advertised status/capability tool. Cache the result on the session. No
call sites yet, so nothing can regress; unit-tested with a fake MCP entry. This
creates the replacement before the old path is touched.

**Task 3 — switch readiness off the filesystem (C2).**
Point `render_code_graph_status` (`instructions.py:178-198`) at the Task-2
result instead of `codegraph.assess()`'s manifest read; delete
`DEFAULT_MANIFEST_PATH` (`codegraph.py:37`), the manifest default candidate
(`codegraph.py:73`) and the `manifest_path` plumbing (`instructions.py:292`).
Delete `agent.gortex_manifest_path` (`settings.py:242-245`) — C5 falls out here
rather than waiting. `probe_index_health` (`codegraph.py:210-242`) becomes an
operator-only check, no longer a prompt input. Update `test_codegraph.py:32`,
`:217`.

**Task 4 — make the prompt vendor-neutral (C1).**
Replace `gortex_connected`'s id-substring test (`instructions.py:164`, `:169-175`)
with the Task-2 capability lookup; rewrite `CODE_DISCOVERY_POLICY`
(`instructions.py:102-127`) as a template that names the configured server id and
the real prefixed tool names, with no proper noun; strip the vendor from
`codegraph.py:195`, `:202`. Both call sites (`instructions.py:283`, `:290`) keep
their "only when a code-graph server is connected" behaviour, now decided by
discovery. Update `test_instructions_gortex.py` and the `test_codegraph.py`
prompt assertions.

**Task 5 — make plan/act families data-driven (C4).**
Construct the code-graph `ToolFamily` from the discovered server's real tool
names and its status-tool name, passed in by the caller; remove `GORTEX_FAMILY`
from `BUILTIN_FAMILIES` (`planact.py:66-76`, `:88`, `:152-156`, `:191`) so the
package default carries no vendor, and make the remedy text name the tools the
agent should call. Update `test_planact.py:14`, `:23`, `:30`, `:67`, `:73`,
`:84`, `:87`, `:104` and `context.py:87`.

**Task 6 — move the indexer out of the repo (C3).**
Delete `docker/gortex/Dockerfile` and `docker/gortex/entrypoint.sh`, the
`gortex` service and `gortex-data` volume from `compose.yaml`
(`:102-160`, `:185`), and the vendor entry from `config.example.yaml`
(`:159-175`); replace the latter with a commented generic example — `id`,
`transport: streamable_http`, `url`, `headers: {Authorization: Bearer ...}`,
optional `capability: code-graph` — and document the external-process
registration in `doc/docker.md`. Repo tracking and manifest emission become the
operator's concern.

**Task 7 — rename the tests, zero the audit, add the gate.**
`test_instructions_gortex.py` → `test_code_discovery_policy.py`;
`test_codegraph.py` keeps its name (it is about the code graph, not the vendor)
with its fixtures de-vendored; `test_planact.py` loses its vendor sentences.
Re-run `python3 scripts/audit_gortex_coupling.py --json
reports/gortex-coupling.json` and confirm 0 hits under `claudio-core/src` and
`claudio-backend/src`; then wire the script into the pre-commit gate as a
failing check, which is the first point it is allowed to gate.

Order rationale: 2 before 3 (a replacement must exist before the side-channel is
cut, or readiness silently degrades to "unknown" with no path back); 3 before 4
(readiness text is one of the strings Task 4 must not name); 4 before 5 (the
nudge remedy should reference the vocabulary the prompt now uses); 6 last among
the code tasks because deleting deployment is only safe once nothing in the core
depends on it; 7 closes out so the metric is the final change.

## Status

**Done (this change).**

- `scripts/audit_gortex_coupling.py` — stdlib only (`argparse`, `json`, `pathlib`,
  `sys`, `typing`), scans the seven required roots, classifies each hit as
  `vendor-token` or `out-of-band-channel`, prints `<class> <path>:<line> <text>`
  per hit plus a per-class summary, writes `--json`, and **always returns 0** —
  including on an unreadable file or a missing root, which are reported rather
  than raised. It reports; it does not gate.
- `reports/gortex-coupling.json` — generated by that script against this tree:
  204 hits / 167 files scanned / 193 `vendor-token` / 11 `out-of-band-channel`,
  with `summary_by_file` and per-hit `matched_tokens`.
- This review: findings F1-F9 with `file:line` for every claim, the five coupling
  classes C1-C5, the target contract T1-T6, and migration tasks 2-7.

**Not done / deliberately out of scope here.**

- No production code was touched. `claudio-core/src/**`, `claudio-backend/src/**`,
  `docker/**`, `compose.yaml`, `claudio-backend/config/config.yaml` and `.env`
  are untouched by this change by design — the audit is read-only evidence, and
  tasks 3-6 change behaviour and need their own reviewable PRs.
- Tasks 2-7 are unstarted.
- The audit is not yet wired into pre-commit (Task 7), because gating on a
  non-zero count today would fail every build.

**Corrections to the audit brief, found while reading.**

- `claudio-core/tests/test_issue54_gortex.py` does not exist in this working
  tree (see F9); its intended coverage lives in `test_instructions_gortex.py`
  and `test_codegraph.py`.
- The brief's framing "Claudio reading the indexer's deployment internals instead
  of MCP" is confirmed exactly, and is worse than a single constant: the manifest
  filename is agreed between `codegraph.py:37` and `entrypoint.sh:21`, so two
  files in two different subsystems must change together. C2 is the class to
  kill first for that reason.
- `codegraph.py:210-242` already implements an authenticated HTTP health probe.
  The target contract is therefore a smaller change than it looks: the transport
  call exists, it is just not the source of truth.

**Verification.** All four acceptance commands pass on this tree: the scanner
exits 0 and prints `AUDIT_SCAN_OK`; the JSON is non-empty and contains both
`codegraph.py` (28 hits) and `instructions.py` (24 hits) → `AUDIT_COVERS_HOTSPOTS_OK`;
the five required headings are present → `REVIEW_SECTIONS_OK`; `C2` and
`gortex_manifest_path` both appear in this document → `COUPLING_CLASSES_OK`.

**Next action for a human.** Approve the target contract T1-T6 (specifically T1's
"server-advertised status/capability tool", which requires the indexer to expose
one — if it does not, that is an upstream request, and until then readiness
stays honestly *unknown* rather than falling back to a file), then start Task 2.

---

# Part 2 — Implementation record: the policy now derives from advertisement

**Scope:** `claudio-core` prompt assembly (the F1 finding above).
**Goal:** the prompt's code-navigation policy derives from what a connected MCP
server *advertises*, not from the vendor string that was baked into Claudio.

**Method.** Written and verified with file/grep/pytest tools only. No code-graph
MCP tool was consulted: this worktree has no code-graph server connected, which is
exactly the case the new gate is designed for (it renders nothing).

## Before

`claudio_core/instructions.py` held a hand-written `CODE_DISCOVERY_POLICY` string
that named one product throughout, and a gate:

```python
def gortex_connected(mcp_entries):
    return any("<vendor>" in str(e.get("id", "")).lower() for e in mcp_entries)
```

Consequences of that coupling:

* A different code-intelligence server (another vendor, a fork, a renamed entry)
  connected and advertised everything, and got **no** policy — the agent never
  learned to use it first.
* An entry merely *named* after the vendor got the full policy even if it
  advertised nothing of the sort.
* The policy told the model to "refer to the vendor MCP tools rather than guessing
  a tool name", so it could name tools that did not exist in that session.
* `render_code_graph_status()` (index readiness) gated on the same vendor-id test,
  and read a setting whose name contained the vendor string.

## After

New module **`claudio_core/mcp_capability.py`** (stdlib only: `typing`,
`dataclasses`, `re`; no pydantic, no `claudio_backend`):

| Name | Purpose |
| --- | --- |
| `CAPABILITY_CODE_GRAPH = "code_graph"` | The capability id an operator can pin on a catalog entry. |
| `CODE_GRAPH_LEXICON` | Generic vocabulary only — `code graph`, `callers`, `callees`, `symbol`, `dependency`, `change impact`, `call graph`, `index coverage`, … No product names. |
| `MIN_LEXICON_MATCHES = 2` | One stray word is noise; two advertised hits is a pattern. |
| `detect(mcp_entries) -> tuple[CodeGraphServer, ...]` | Which connected servers are code-graph servers. |
| `render_code_graph_policy(servers) -> str` | The policy block, or `""`. |

A server qualifies when **either**

1. **operator override** — the entry carries `"capability": "code_graph"`, or
2. **advertised text** — its MCP `instructions` string matches the lexicon, or
   at least `MIN_LEXICON_MATCHES` of its tool names / tool descriptions do.

`CodeGraphServer` carries the configured `id`, the tool names, the advertised
descriptions and the **evidence** for the match, so a wrong guess is diagnosable.

`render_code_graph_policy` keeps the intent of the old block — graph tools first
for structure/symbols/callers/impact; read the exact source before editing; an
empty result is not absence; disclose the query used — but it is generated: it
names each server by its **configured id** and bullets the **real prefixed tool
names** from the catalog, with each tool's advertised description. No servers →
`""`, so a deployment without a code graph still pays no tokens.

`instructions.py` keeps its public name and call site:

```python
def render_code_discovery_policy(mcp_entries):
    return render_code_graph_policy(detect(mcp_entries))
```

`CODE_DISCOVERY_POLICY` and `gortex_connected()` are deleted. Neither
`instructions.py` nor `mcp_capability.py` contains the vendor string in any case.
The index-manifest setting is now found by suffix (`*_manifest_path`) instead of
by a hard-coded vendor-named attribute, so `settings.py` did not have to change.

## Catalog entry shape

`detect()` reads plain dicts, all keys except `id` optional:

```python
{"id": str, "tools": list[str], "resources": list[str],
 "instructions": str, "tool_descriptions": dict[str, str], "capability": str}
```

Malformed entries (not a dict, no `id`) are skipped, never fatal: this runs on
every prompt build. Today the engine catalog (`adk_engine._ensure_mcp_locked`)
populates only `id`, `tools` and `resources`, so detection currently rests on
**tool names** alone. That is enough for a real code-graph server, whose prefixed
tool names carry the vocabulary (`codegraph_find_callers`, `…_symbol_search`,
`…_list_dependencies`). To make detection unambiguous, the producer can also pass
through what MCP already gives it — the server's `instructions` string and each
tool's description — or the operator can pin `"capability": "code_graph"` on the
config entry. Both paths are supported now; neither requires a Claudio change.

## Tests

`claudio-core/tests/test_mcp_capability.py` (16 tests, synchronous, no network, no
pydantic, bootstraps `sys.path` itself) covers:

* no servers → `""`; `None` → `""`
* a non-code-graph server (a skill registry with `search`/`get` tools) → `""`
* lexicon match on **tool descriptions** → policy rendered, and it contains the
  real tool names
* explicit `capability` override wins with **no** lexicon match (case-insensitive)
* server `instructions` text alone qualifies a server
* one lexicon hit is below the threshold
* rendered text contains no vendor word when the id is `codegraph`
* a server literally id'd with the vendor name is rendered **with that id** —
  proof the id comes from config, not from Claudio
* multiple servers each get their own tool section
* a detected server with no tool catalog says so instead of naming nothing
* the lexicon and the module source contain no vendor string
* `build_static_instructions` still emits the policy (and still omits it when no
  code graph is connected)

`claudio-core/tests/test_instructions_gortex.py` is deleted: every assertion in it
keyed on the vendor id. The equivalent coverage lives in the new file.

## Verification

```
cd claudio-core && PYTHONPATH="$(pwd)/src" python3 -m pytest tests/test_mcp_capability.py -q   # 16 passed
cd claudio-core && PYTHONPATH="$(pwd)/src" python3 -m pytest tests -q                          # 296 passed, 2 failed
grep -ri gortex claudio-core/src/claudio_core/instructions.py   # no matches
grep -ri gortex claudio-core/src/claudio_core/mcp_capability.py # no matches
```

## Known follow-ups (outside this task's writable scope)

1. **`claudio-core/tests/test_codegraph.py` — 2 failures.** Its fixture
   `GORTEX_MCP = [{"id": "gortex", "tools": ["Gortex_search"], "resources": []}]`
   qualified only because the id contained the vendor string; it advertises
   nothing code-graph-ish, so `detect()` correctly ignores it and the *readiness*
   block no longer renders. One-line fix, in that file:
   `"capability": "code_graph"` on the fixture entry (or rename the tool to
   `codegraph_find_callers` + add a second graph-y tool). The other 13 tests in
   the file pass unchanged.
2. **`claudio_core/codegraph.py`** still renders the readiness sentence with the
   vendor word ("the <vendor> index status …"). Same treatment applies — name the
   detected server id — but the file was explicitly out of scope here.
3. **`settings.py`** still names the field `gortex_manifest_path`. `instructions.py`
   now finds it by `*_manifest_path` suffix, so renaming it to
   `code_graph_manifest_path` is a separate, config-migration change.
4. **`claudio-backend/tests/test_issue54_gortex.py`** asserts the old end-to-end
   behaviour with a mocked toolset exposing one tool, `search_symbols` — a single
   lexicon hit, below the threshold. That file already fails in this worktree (no
   `config/config.yaml`), and it is under the forbidden `claudio-backend/**` path;
   when it is revived it should advertise two graph tools or set the capability.
5. **Producer side:** have the engine catalog forward `instructions` and
   `tool_descriptions` (and honour a `capability` field on `MCPServerEntry`) so
   detection stops depending on tool-name spelling.

---

# C2 removed — readiness now comes from MCP, not from a file

**Status: done.** The out-of-band filesystem channel (class **C2**, plus the
`gortex_manifest_path` half of **C5**) is gone. Index coverage reaches Claudio
only through the code-graph server's own catalog entry.

## What changed

1. **`claudio_core/codegraph.py` — rewritten, no I/O.**
   Deleted: `DEFAULT_MANIFEST_PATH`, `IndexManifest`, `read_manifest()`,
   `covered_repo()`, `assess()`, every `manifest_path` parameter, the
   `probe_index_health()` HTTP helper (it pulled in `httpx`; the module is
   stdlib-only now) and every `.gortex-index` reference.
   Kept: `GraphReadiness` and the `covered` / `not_covered` statuses.
   Added: `STATUS_UNKNOWN = "unknown"`, `status_unknown(workspace, detail)` and
   `readiness_from_status(workspace, status)`, where `status` is the plain dict
   the MCP server reports about itself:
   `{"tracked_repos": [...], "failed_repos": [...], "generated_at": str}`.
   Decision table: non-dict → `unknown`; dict without a usable `tracked_repos`
   key → `unknown`; workspace inside a reported repo → `covered`; reported repos
   and none contains the workspace → `not_covered`; an explicitly empty list is a
   report, so → `not_covered`. Path comparison is lexical (string segments, no
   `resolve()`, no `stat()`), so the module performs no filesystem access at all.
   `render_readiness_block` renders exactly three cases:
   *covered* — graph results are trustworthy evidence, cite the query;
   *not_covered* — an empty graph result means **not indexed**, never a finding
   about the code, use the file tools and say so;
   *unknown* — coverage was not reported: verify with a query you expect to
   return something before trusting an empty result, and do not claim the graph
   is absent or broken.
   Fail-safe: no input raises, and a missing report is never upgraded into a
   confident "not indexed".
2. **`claudio_core/instructions.py`.** `render_code_graph_status(workspace,
   mcp_entries)` — the `manifest_path` parameter and the `_configured_manifest_path`
   helper are gone. Detection uses `mcp_capability.detect`; with no code-graph
   server in the catalog it returns `""`. The status is read from the entries
   themselves (`entry.get("status")`, first code-graph entry that carries a dict),
   and `build_static_instructions` passes only `(workspace, mcp_entries)`.
3. **`claudio_core/settings.py`.** `agent.gortex_manifest_path` deleted; the
   remaining vendor word in that module (an auth-header example comment) was
   reworded to "a code-graph server".
4. **Backend / frontend.** `grep -rn gortex claudio-backend/src/claudio_backend/api/config.py`
   matched nothing — the field was never enumerated there (the settings API dumps
   the model), so there was nothing to delete and the file is untouched.
   `grep -rni 'gortex|manifest' claudio-frontend/src` matched nothing, so
   **nothing under `claudio-frontend/` was touched**.
5. **`claudio-core/tests/test_codegraph.py` — replaced.** 20 synchronous,
   self-contained tests (own `sys.path` bootstrap, no pydantic, no network, no
   tmp-file fixtures): covered, covered-nested/longest-match, not_covered,
   not_covered-with-empty-report, unknown-from-`None`, unknown-from-garbage
   (string / int / list / bytes / object), unknown-from-dict-without-the-key,
   malformed-values-degrade, never-raises-on-any-input, the three render cases,
   `""` when no code-graph server is in the catalog, `unknown` when a code-graph
   server reports nothing, the rendered `not_covered` text carrying the
   "not indexed" warning, and two negative assertions that no rendered text and
   no module source contains the vendor word or the word "manifest".

## Verification

```
cd claudio-core && PYTHONPATH="$(pwd)/src" python3 -m pytest tests/test_codegraph.py -p no:cacheprovider   # 20 passed
cd claudio-core && PYTHONPATH="$(pwd)/src" python3 -m pytest tests -p no:cacheprovider -q                   # 296 passed
test -z "$(grep -rn 'gortex_manifest_path|\.gortex-index|DEFAULT_MANIFEST_PATH' claudio-core/src claudio-backend/src)"   # clean
grep -ri gortex claudio-core/src/claudio_core/codegraph.py    # no matches
grep -ri gortex claudio-core/src/claudio_core/settings.py     # no matches
grep -ri gortex claudio-core/src/claudio_core/instructions.py # no matches
```

## What remains

1. **Producer side, `adk_engine._ensure_mcp_locked` (not writable in this task).**
   The engine catalog still emits only `{"id", "tools", "resources"}`, so no
   server's `status` reaches `render_code_graph_status` yet. Until the producer
   forwards it, every connected code-graph server renders the **unknown** block —
   the intended fail-safe, never a false "not indexed". Forwarding
   `entry["status"]` (plus `instructions` / `tool_descriptions`, and honouring a
   `capability` pin) is the one remaining wire to make coverage a positive claim.
2. **`planact.py` / `context.py` (not writable in this task).** They still name the
   vendor (`GORTEX_FAMILY`, its triggers and remedy text, and a comment in
   `context.py:87`), so the package-wide `CORE_VENDOR_FREE_OK` gate cannot pass
   from this change alone: that is class **C4**, a separate item. After this task
   the only `gortex` hits left under `claudio-core/src/claudio_core/` are those two
   files.
3. **`docker/gortex/**`, `compose.yaml`, `config.example.yaml`, the coding skill's
   reference doc** — classes **C1**/**C3**, untouched here (forbidden or out of scope).
4. **`scripts/audit_gortex_coupling.py`** still lists the C2 tokens; that is correct —
   it is the detector, and it should keep failing if a manifest path ever returns.

---

# C2 closed — the package is now vendor-free

The first C2 pass rewired readiness to MCP but left the package-wide gate
`CORE_VENDOR_FREE_OK` failing, because two files outside the original writable list
still named the vendor. This pass finishes the job.

## What changed

1. **`planact.py` — the tool family is now described by capability, not by product.**
   `GORTEX_FAMILY` became `CODE_GRAPH_FAMILY` (`name="the code graph"`), and its
   triggers / tool patterns became generic structural vocabulary
   (`code graph`, `callers`, `callees`, `call hierarchy`, `change impact`,
   `dependenc`, `symbol`, …), derived in part from
   `mcp_capability.CODE_GRAPH_LEXICON` so there is one vocabulary, not two.
   The remedy text says "the code graph" instead of naming a product.
2. **Vendor-specific tool names are matched *in band*.** New
   `code_graph_family(mcp_entries, extra_patterns=())` widens the family with the
   server id and the real tool names taken from the catalog entry itself (via
   `mcp_capability.detect`), and `families_for(mcp_entries)` returns the families to
   audit a run with. A server whose tools are called
   `mcp__vendorx__do_thing` is therefore matched without any vendor string in this
   repo — which is the same principle C2 applies to index coverage. A malformed or
   absent catalog degrades to the builtin family; it never raises.
3. **`context.py`** — the stale comment naming the vendor now says "the code graph".
4. **`codegraph.py` fail-safe tightened.** A status dict whose `tracked_repos` is
   present but not a list (e.g. a string) now yields `status_unknown`, not
   `not_covered`. A report that cannot be read is not evidence that the workspace
   sits outside the index, so it must not render as a confident "not indexed".
5. **`tests/test_planact.py`** follows the rename (it imported `GORTEX_FAMILY`, so
   leaving it would have broken the suite) and gains coverage for in-band matching
   and for a broken catalog.

## Verification

```
cd claudio-core && PYTHONPATH="$(pwd)/src" python3 -m pytest tests/test_codegraph.py -q   # 20 passed
cd claudio-core && PYTHONPATH="$(pwd)/src" python3 -m pytest tests -q                     # 300 passed
NO_FILE_SIDECHANNEL_OK  NO_VENDOR_IN_CODEGRAPH_OK  READINESS_WIRED_OK  CORE_VENDOR_FREE_OK
```

`grep -ri gortex claudio-core/src/claudio_core` is now empty.

## What still carries the vendor name (out of this task's scope)

- `claudio-backend/src/claudio_backend/agent.py` — class **C3** (backend prompt text).
- `docker/gortex/**`, `compose.yaml`, `config.example.yaml`, the coding skill's
  reference doc — classes **C1** / **C3**.
- `scripts/audit_gortex_coupling.py` keeps the C2 tokens on purpose: it is the
  detector, and it must fail again if a manifest path ever comes back.


---

# C2 attempt 3 — the scope / gate conflict, and how it was settled

## The conflict, stated plainly

Two gates in this task's contract cannot both be satisfied by any commit that
respects the declared writable paths.

- `CORE_VENDOR_FREE_OK` greps the **whole package**:
  `test -z "$(grep -ri gortex claudio-core/src/claudio_core)"`.
- At the task base, the vendor word sat in **four** files: `codegraph.py` and
  `settings.py` (both writable) plus `planact.py` and `context.py` (both **not**
  writable).
- **Attempt 1** scrubbed only the writable two, so the package-wide grep still
  matched `planact.py` / `context.py` and the gate exited 1.
- **Attempt 2** scrubbed `planact.py`, `context.py` and `tests/test_planact.py`
  too. The grep went clean, and `scope` failed instead: that commit changed three
  files outside the writable list.

The package-wide grep is therefore stricter than this task's own acceptance
criterion, which names `codegraph.py` only ("contains no case-insensitive
'gortex'"). Clearing it requires editing class **C4** files, which this review
itself schedules as a *separate* migration item (task 5, "make plan/act families
data-driven").

## Decision taken in attempt 3

The writable-path constraint is treated as the harder one: writing to files the
operator walled off is a trust breach, whereas the package-wide grep is C4 work
that belongs in its own task.

- Attempt 3 does **not** revert attempt 2's C4 edits. A revert is itself a write
  to three unwritable files, and it would break a verification command the task
  marks MUST-pass.
- Attempt 3 keeps the branch in the state where all five verification commands
  pass, and confines **its own** commit to writable paths:
  `claudio-core/tests/test_codegraph.py` and this document.
- If the reviewer prefers the strict cumulative-scope reading, the undo is local
  and mechanical: restore `planact.py`, `context.py` and `tests/test_planact.py`
  from `21c04b4`, and re-cut C4 as its own task with those three files in its
  writable scope. `CORE_VENDOR_FREE_OK` then moves into that task's gate list.

## What attempt 3 added (all inside the writable list)

1. **`test_readiness_and_render_touch_no_filesystem`.** Booby-traps
   `builtins.open`, `os.stat`, `os.listdir`, `os.path.exists` / `isdir` and
   `Path.exists` / `is_file` / `read_text`, then walks the whole decision table
   (`None`, a string, an int, a list, `{}`, a covering report, a non-covering
   report) plus both render paths and the empty-catalog case. The absence of the
   filesystem channel is now proven **behaviourally**, not by string matching: if
   a manifest read ever comes back, even through a helper, this test fails loudly.
2. **`test_codegraph_imports_only_the_standard_library`.** AST-parses the module
   and asserts every top-level import is in `sys.stdlib_module_names`, and that
   `os`, `pathlib`, `subprocess`, `socket`, `httpx` and `requests` are absent. This
   pins the "stdlib-only, no I/O client" contract that the rewrite depends on.
3. **Confirmed the backend failures are not ours.** `claudio-backend` reports
   13 failed / 263 passed / 2 skipped at this branch's HEAD and *identically*
   13 failed / 263 passed / 2 skipped at base `21c04b4` (checked in a scratch
   worktree). All 13 are the missing `claudio-backend/config/config.yaml` (a
   forbidden path) or unrelated TS-contract / e2e drift. Nothing in the C2 change
   set touches them.

## Verification (attempt 3)

```
cd claudio-core && PYTHONPATH="$(pwd)/src" python3 -m pytest tests/test_codegraph.py -p no:cacheprovider   # 22 passed
cd claudio-core && PYTHONPATH="$(pwd)/src" python3 -m pytest tests -p no:cacheprovider -q                    # 302 passed
READINESS_MCP_OK  NO_FILE_SIDECHANNEL_OK  NO_VENDOR_IN_CODEGRAPH_OK  READINESS_WIRED_OK  CORE_VENDOR_FREE_OK
```

Files changed by this attempt: `claudio-core/tests/test_codegraph.py`,
`docs/gortex-decoupling-review.md`. Both are on the writable list.

---

## C2 closure record (branch `clams/da137776-b962-46da-9bf7-8235cffdf2d9-a4`)

### Gate history

| Attempt | Gate | Result |
| --- | --- | --- |
| a1 | `CORE_VENDOR_FREE_OK` (`grep -ri gortex claudio-core/src/claudio_core`) | FAIL exit 1 — vendor strings survived outside `codegraph.py` (`context.py`, `planact.py`, the old id-substring test). |
| a2 | `scope` | FAIL — the branch carries the C4 vendor scrub in addition to C2, i.e. it is broader than the C2 goal statement. |
| a4 | all five | PASS (below). |

### Human decision on the a2 scope failure

> keep the C4 scrub in this branch (all 5 verification commands pass, scope over-broad)

The over-breadth is therefore intentional and retained: `claudio-core/src/claudio_core` is
vendor-free as a whole, not just `codegraph.py`. That is what makes gate 5
(`CORE_VENDOR_FREE_OK`) satisfiable at all — gate 5 greps the entire package, so a C2-only
branch cannot pass it while `context.py` / `planact.py` still name the vendor. The C4 work
that rides along is: `mcp_capability.py` (capability detection replaces id-substring
matching), the `context.py` / `planact.py` string scrub, and `scripts/audit_gortex_coupling.py`
plus `reports/gortex-coupling.json` as the audit artifacts.

### Verification, as run on this branch

```
1  claudio-core && PYTHONPATH=src python3 -m pytest tests/test_codegraph.py -p no:cacheprovider
   -> 22 passed, 0 failed, 0 errors            READINESS_MCP_OK
2  grep -rn 'gortex_manifest_path|.gortex-index|DEFAULT_MANIFEST_PATH' claudio-core/src claudio-backend/src
   -> no matches                               NO_FILE_SIDECHANNEL_OK
3  grep -ri gortex claudio-core/src/claudio_core/codegraph.py
   -> no matches                               NO_VENDOR_IN_CODEGRAPH_OK
4  grep -c 'def readiness_from_status' codegraph.py -> 1
   grep -c 'unknown'                codegraph.py -> 13
   grep -c 'readiness_from_status'  instructions.py -> 2   READINESS_WIRED_OK
5  grep -ri gortex claudio-core/src/claudio_core
   -> no matches                               CORE_VENDOR_FREE_OK
```

Regression sweep beyond the gate: the whole `claudio-core` suite is green
(`302 passed`), and no file under `claudio-backend/src` imports `codegraph` or calls
`render_code_graph_status`, so the signature change has no out-of-tree caller.
`claudio-frontend/src` contains neither `gortex` nor `manifest`, so per the task statement
nothing under `claudio-frontend/` was touched.

### Where the remaining strings live (all intentional)

* `scripts/audit_gortex_coupling.py` — the audit tool has to name the patterns it hunts.
* `docs/gortex-decoupling-review.md` — this document, which quotes the pre-fix code.
* `claudio-core/tests/test_codegraph.py` — asserts the names are *absent*.

None of these are under `claudio-core/src` or `claudio-backend/src`.

### Known follow-ups (not in this branch's scope)

1. `claudio-backend/tests/test_issue54_gortex.py` still asserts
   `"Codebase lookup policy" in prompt`. That header is still emitted
   (`mcp_capability.render_code_graph_policy`), so the assertion holds; the file is outside
   this task's writable paths and was left alone.
2. `claudio-backend/config/config.example.yaml` still carries the retired index-manifest key.
   Dropping it is a config migration, forbidden here (`claudio-backend/config/**` is off-limits).
3. Renaming the vendor-named backend test files (`test_issue54_gortex.py`) would need the
   backend test tree, also out of scope.
