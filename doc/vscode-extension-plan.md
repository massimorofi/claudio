# Claudio VS Code Extension — Implementation Plan

A native-feeling VS Code extension that brings Claudio's **chat** and **configuration**
into the editor and binds the agent to the **open workspace folder**, so coding with
Claudio feels like coding with the editor itself — the agent reads and writes the
files you have open, you see every edit as a diff, and you approve destructive
actions without leaving the keyboard.

This document is the build brief for a **new project** (`claudio-vscode/`). It is
written to be handed to Claude Code and executed phase by phase.

---

## 1. Framing: a new client, not a new backend

Claudio already exposes everything the extension needs over HTTP + SSE
(`claudio-backend`, `http://127.0.0.1:8100/api/v1`). The chat, the config, the
approval round-trip, the file tools, the run lifecycle — all of it is live and
contract-tested. **The extension does not change the backend.** It is a third
client surface, alongside the React SPA:

```
                 ┌──────────────────────────────────────────────┐
   VS Code ─────▶│  claudio-vscode (NEW)                        │
   (this plan)   │  ┌────────────────┐      ┌────────────────┐  │
                 │  │ Extension Host │◀────▶│  Webview (React)│ │
                 │  │ (Node/TS)      │ msg  │ chat + config UI│ │
                 │  │ owns backend   │      │ renderer + input│ │
                 │  │ connection     │      └────────────────┘  │
                 │  └───────┬────────┘                          │
                 └──────────┼──────────────────────────────────┘
                            │ HTTP + SSE  (Bearer token)
                            ▼
                 ┌──────────────────────────┐
                 │  claudio-backend (EXISTING)│  ← no changes
                 └──────────────────────────┘
```

The two pillars the user asked for map cleanly:

| Pillar | Where it lives | Reused from |
|---|---|---|
| **Chat** | Webview (React) + host SSE relay | `claudio-frontend/src/features/chat/` |
| **Configuration** | Webview (React) config tab | `claudio-frontend/src/features/config/` |
| **VS Code workspace integration** | Extension host (NEW) | — (this is the new work) |

The third row is where the "seamless coding" feeling comes from, and it is the
genuinely new part. The first two are largely a reuse-and-adapt exercise.

---

## 2. What "seamless for coding" means, concretely

Before architecture, the target experience, stated as observable behaviour:

1. **The open folder *is* the workspace.** The agent's `read_file` / `write_file` /
   `run_command` operate on the folder you opened in VS Code, not a separate
   `data/workspace`. You never think about the distinction.
2. **You see edits as they happen.** When the agent writes a file, it opens in the
   editor; when it proposes a destructive write, you see a **diff** of current vs
   proposed *before* you approve.
3. **Selection is first-class context.** Select code → "Add to Claudio" → it's in
   the next message with file path and line range. The active file is always
   one keystroke away.
4. **The explorer stays true.** Files the agent creates appear in the explorer
   immediately; deleted ones disappear.
5. **State is glanceable.** A status-bar item shows run state, cost, and context
   usage at a glance; click to jump into the chat.
6. **Keyboard-first.** Open chat, send selection, stop a run, approve/deny — all
   bound to shortcuts.
7. **Nothing surprising.** The agent can't touch files outside the workspace
   without it being visible and gated (the backend already enforces this; the
   extension surfaces it).

---

## 3. Architecture

### 3.1 The host owns the connection; the webview renders

The single most important decision: **the extension host (Node) owns the backend
connection (HTTP + SSE), not the webview.**

Why: the "seamless coding" features (open the file the agent wrote, show a diff
before approval, update the status bar, fire notifications) are *reactions to the
event stream*, and only the extension host can drive the VS Code API. If the
webview held the SSE connection, every file operation would need a round-trip back
to the host. With the host holding the stream, one event drives both the renderer
and the editor from the same place.

```
User types "refactor X" in webview
        │  postMessage {type:'chat.send', message, files}
        ▼
Extension Host
        │  POST /chat/stream  ── reads SSE frames ──┐
        │                                          │
        │  for each event:                         │
        │   ├─ relay to webview  ────────────────▶ Webview reduces → renders
        │   │   (token, reasoning, tool_call,       (same chatState reducer
        │   │    tool_result, approval_required,     the SPA already uses)
        │   │    context_usage, run_completed)
        │   │
        │   └─ VS Code action (host-side):
        │       • tool_call write_file  → open file / stage diff
        │       • approval_required     → open diff view + status "awaiting approval"
        │       • run_completed         → notification + status reset
        │       • context_usage         → status bar context %
        ▼
```

The webview is a **renderer + input surface**. It reuses the React chat's
`chatState.ts` reducer verbatim — the host relays raw SSE events, the webview
reduces them exactly as the SPA did. Only the *transport* changes: instead of the
webview opening the SSE stream itself, the host opens it and forwards frames.

### 3.2 Chat surface: webview, not the native Chat Participant API

The native VS Code **Chat Participant** API (`chatParticipants` contribution) is
the tempting choice for "feels like VS Code chat," but it is a poor fit for
Claudio:

| Requirement | Native Chat API | Webview |
|---|---|---|
| Live tool cards with status | ✗ markdown-only stream | ✓ |
| Approve/deny buttons mid-stream | awkward (button→command, no data) | ✓ |
| Context gauge / plan tracker | ✗ | ✓ |
| Reasoning blocks | partial | ✓ |
| Diff-on-approval | ✗ | ✓ |
| Reuse existing React chat | ✗ | ✓ |

The Claude Code extension itself uses a webview panel for exactly these reasons.
We follow that model: a **webview view docked in the Secondary Side Bar**, which
VS Code renders natively (theming, resizing, docking) and which gives full
control over the rich UI. The "native feel" comes from the docking, the input box,
slash commands, and the file picker — all of which the webview + host can provide.

### 3.3 Two config layers, kept distinct

There are two configuration surfaces and they must not be conflated:

| Layer | What | Where edited | Storage |
|---|---|---|---|
| **Extension settings** | backend URL, token, auto-start, workspace-binding mode, keybindings | VS Code Settings UI | `claudio.*` in settings.json / `context.secrets` |
| **Backend config** | LLM, agent, MCP, autonomous, self_improvement | the **Config tab** in the webview | `claudio-backend/config/config.yaml` via `PUT /config` |

The extension settings are about *how the extension talks to the backend*. The
backend config is the agent's own configuration, edited through the same
`PUT /config` merge-patch the SPA uses. The Config tab is the SPA's `ConfigView`
adapted to go through the bridge.

---

## 4. The workspace binding (the crux)

This is the part that makes Claudio "work with the VS Code workspace," and it is
the one place a naive design breaks. Verified facts that make it work:

- `agent.workspace_dir` is **not** in `BACKEND_RESTART_KEYS` or
  `AGENT_RESTART_KEYS` — changing it needs **no restart**.
- `PUT /config` is a **merge-patch** and calls `supervisor.reload_config()`,
  which reassigns `run_manager.config` (and the engine, compactor, etc.) live.
- The workspace is resolved **per run**: `resolve_workspace_dir(config)` →
  `RunRequest.workspace` → `ToolContext.workspace`. File tools are confined to it;
  `run_command` runs with `cwd = ctx.workspace`.

**Therefore:** on activation (and whenever the workspace folder changes), the
extension sets the backend's workspace to the VS Code folder:

```ts
// src/workspace/binding.ts
export async function bindWorkspaceToBackend(
  client: BackendClient,
  vscodeFolder: vscode.WorkspaceFolder,
): Promise<void> {
  // Set an ABSOLUTE path. resolve_workspace_dir() uses absolute paths as-is,
  // which sidesteps the cwd-dependence gotcha entirely.
  await client.put('/config', {
    agent: { workspace_dir: vscodeFolder.uri.fsPath },
  });
}
```

Re-bind on `vscode.workspace.onDidChangeWorkspaceFolders`. Re-bind after any
backend restart (the file is the source of truth, but re-assert on connect).

**Multi-window caveat (be honest about it).** The backend holds **one** global
`workspace_dir`. If the user opens two VS Code windows on two projects, the
most-recently-activated window's folder wins for *all* sessions. For v1 this is
acceptable and documented. The clean fix is a **per-session workspace** on the
backend (each session carries its own `workspace_dir`, set at session creation) —
that is a backend enhancement, listed in §11 Future Work, *not* required for v1.

**Backup-path note.** The file tools write backups to
`<workspace>/../file-history/<session_id>`. With the workspace set to the project
folder, backups land in the **parent** of the project, outside it. Surface this in
the extension docs; if it's undesirable, the operator can set an absolute
`workspace_dir` whose parent is a suitable backup location.

---

## 5. Tech stack & toolchain

| Concern | Choice | Rationale |
|---|---|---|
| Extension host | TypeScript, Node 20 | VS Code extension API |
| Host bundler | **esbuild** | fast, standard for extensions |
| Webview | **React 18 + TypeScript** | reuse `claudio-frontend` components |
| Webview bundler | **Vite** (reuse the SPA's config, adapted) | the SPA already uses Vite |
| HTTP client | `fetch` (Node 20 native) in the host | mirrors the SPA's `client.ts` |
| SSE parsing | streaming `fetch` + a small SSE frame parser in the **host** | host owns the stream |
| Packaging | `@vscode/vsce` | produces `.vsix` |
| Host tests | `@vscode/test-electron` | integration |
| Webview tests | `vitest` | matches the SPA |

**Build layout:** two build pipelines compiled into the extension's `dist/`:
`esbuild` → `dist/extension.js` (host), `vite build` → `dist/webview/` (webview
assets). A single `npm run build` runs both; `npm run watch` runs both in watch
mode. The webview's Vite `base` must be `./` (relative) so the extension can load
assets via `webview.asWebviewUri`.

**CSP:** the webview sets a Content-Security-Policy that allows `default-src 'none'`,
`style-src` from the webview's own assets + `'unsafe-inline'` (Tailwind),
`script-src` from the webview's own assets, `img-src` data: + local, and
`connect-src http://127.0.0.1:8100` (the backend). Since the host owns the
network, the webview may not need `connect-src` at all — confirm during Phase 2.

---

## 6. Project structure

```
claudio-vscode/
├── package.json                  extension manifest (contributes: commands,
│                                 viewsContainers, views, configuration,
│                                 keybindings, menus)
├── tsconfig.json
├── esbuild.js                    host bundler config
├── vite.webview.config.ts        webview bundler config (base: './')
├── .vscodeignore                 exclude src/ from the .vsix
├── src/                          ── EXTENSION HOST (Node/TS) ──
│   ├── extension.ts              activate/deactivate, wire everything
│   ├── backend/
│   │   ├── client.ts             typed HTTP client (get/post/put), error unwrap
│   │   ├── sse.ts                SSE frame parser over streaming fetch
│   │   └── supervisor.ts         health check, start/stop the daemon, wait-ready
│   ├── workspace/
│   │   ├── binding.ts            VS Code folder → backend workspace_dir (PUT)
│   │   ├── selection.ts          capture active editor selection / file
│   │   ├── files.ts              open file, reveal in explorer, attachments
│   │   └── diff.ts               diff view for proposed writes / approvals
│   ├── chat/
│   │   ├── ChatController.ts     owns the run: send, stream, relay, stop
│   │   └── approvals.ts        approval round-trip + diff-on-approval
│   ├── config/
│   │   └── ConfigController.ts   config get/put/validate/test via bridge
│   ├── bridge/
│   │   ├── protocol.ts           ★ the postMessage message types (shared)
│   │   └── Bridge.ts             send/receive, request/response correlation
│   ├── status/
│   │   └── StatusBar.ts          run state, cost, context %
│   └── settings.ts               read claudio.* settings + secrets
├── webview/                      ── WEBVIEW (React) ──
│   ├── index.html
│   ├── src/
│   │   ├── main.tsx              React bootstrap
│   │   ├── App.tsx               Chat / Config tab shell
│   │   ├── bridge/
│   │   │   ├── client.ts         webview-side bridge (postMessage wrappers)
│   │   │   └── useBridgeChat.ts  replaces useChat: feeds reducer from relayed events
│   │   ├── chat/                 adapted from claudio-frontend features/chat
│   │   │   ├── ChatView.tsx
│   │   │   ├── ToolCard.tsx
│   │   │   ├── ApprovalCard.tsx
│   │   │   ├── ContextGauge.tsx
│   │   │   ├── PlanTracker.tsx
│   │   │   └── chatState.ts      ★ reused verbatim (event → Item reducer)
│   │   ├── config/
│   │   │   └── ConfigView.tsx    adapted from claudio-frontend features/config
│   │   └── api/
│   │       └── types.ts          ★ copied contract mirror (keep in sync)
│   └── vite.config.ts
├── media/
│   ├── icon.png                  activity-bar icon
│   └── ...
└── README.md                     extension-specific install/use doc
```

The `★` files are the load-bearing reuse points: `chatState.ts` (the reducer),
`types.ts` (the contract), and `protocol.ts` (the new bridge contract).

---

## 7. The bridge protocol

A single typed union of messages, shared between host and webview
(`src/bridge/protocol.ts`). Every message has a `type` and a payload;
request/response pairs carry a `requestId` for correlation.

```ts
// webview → host
type WebviewToHost =
  | { type: 'ready' }
  | { type: 'chat.send'; requestId: string; message: string; files?: FileRef[] }
  | { type: 'chat.stop'; requestId: string }
  | { type: 'approval.decide'; requestId: string; approvalId: string;
      decision: 'approve' | 'deny'; reason?: string }
  | { type: 'session.list'; requestId: string }
  | { type: 'session.load'; requestId: string; sessionId: string }
  | { type: 'session.clear'; requestId: string; sessionId: string }
  | { type: 'session.compact'; requestId: string; sessionId: string }
  | { type: 'config.get'; requestId: string }
  | { type: 'config.put'; requestId: string; patch: Record<string, unknown> }
  | { type: 'config.validate'; requestId: string; candidate: Record<string, unknown> }
  | { type: 'config.llmTest'; requestId: string; llm: Record<string, unknown> }
  | { type: 'config.history'; requestId: string }
  | { type: 'config.restore'; requestId: string; id: number }
  | { type: 'file.open'; path: string }            // open in editor
  | { type: 'file.pick'; requestId: string }       // quick-pick a file to attach
  | { type: 'selection.get'; requestId: string };

// host → webview
type HostToWebview =
  | { type: 'init'; baseUrl: string; token: string; workspace: string;
      activeFile: string | null }
  | { type: 'chat.event'; event: StreamEvent }     // relayed SSE frame
  | { type: 'chat.started'; runId: string }
  | { type: 'chat.done' }
  | { type: 'response'; requestId: string; ok: boolean; data?: unknown;
      error?: string }
  | { type: 'workspace.changed'; workspace: string }
  | { type: 'selection.changed'; text: string; file: string; range: string }
  | { type: 'status'; state: string; costUsd: number; contextPercent: number };
```

The host is the single owner of the backend client and the auth token. The webview
never sees the token beyond the `init` handoff (and in v1 the webview doesn't make
backend calls at all — the host proxies everything).

---

## 8. Phased implementation plan

Each phase is independently shippable and has a concrete acceptance test. Build
and run with F5 (Extension Development Host) after every phase.

### Phase 0 — Scaffolding & toolchain
**Goal:** an installable extension that activates and shows a webview.
- `package.json` manifest: `name`, `publisher`, `engines.vscode`,
  `activationEvents: ["onStartupFinished"]`, `main: ./dist/extension.js`.
- `contributes`:
  - `viewsContainers` → a container in the **Secondary Side Bar** (`auxbar`).
  - `views` → `claudio.chat` (type `webview`).
  - `commands`: `claudio.openChat`, `claudio.openConfig`, `claudio.stopRun`,
    `claudio.addSelection`, `claudio.newSession`.
  - `configuration`: `claudio.backend.url` (default `http://127.0.0.1:8100`),
    `claudio.backend.token`, `claudio.backend.path` (repo root),
    `claudio.backend.autoStart` (bool), `claudio.workspace.bind` (bool).
  - `keybindings`: open chat (`ctrl+alt+c` / `cmd+alt+c`), stop run, add selection.
- `esbuild.js` (host) + `vite.webview.config.ts` (webview, `base: './'`).
- `src/extension.ts`: `activate()` creates the webview view provider; renders a
  placeholder. `deactivate()` cleans up.
- **Acceptance:** F5 → extension activates, the Claudio view appears in the
  Secondary Side Bar, the placeholder webview renders, `npm run build` is clean.

### Phase 1 — Backend client + lifecycle
**Goal:** the extension talks to a running backend and can manage the daemon.
- `backend/client.ts`: typed `get/post/put`, Bearer header, unwrap the error
  envelope into a typed `ApiError` (mirror the SPA's `client.ts`).
- `backend/sse.ts`: parse `text/event-stream` frames (`id:`/`event:`/`data:`)
  from a streaming `fetch` response body.
- `backend/supervisor.ts`: `health()` → `GET /healthz`; `ensureRunning()` → if
  down and `autoStart`, spawn the daemon (`backend-start.sh` or `uvicorn` with
  the venv), poll `/healthz` until ready with a timeout; `stop()`.
- `settings.ts`: read `claudio.*`; store the token in `context.secrets` (NOT
  plain settings).
- **Acceptance:** with the backend running, the extension shows connected state and
  fetches `/version` + `/agent/status`; with it stopped and `autoStart=true`, the
  extension starts it and reaches ready.

### Phase 2 — Chat webview (reuse the React chat)
**Goal:** full chat: send, stream, approvals, stop, context gauge.
- Scaffold `webview/` React app; copy `chatState.ts` and `types.ts` from the SPA.
- `bridge/` (both sides): `postMessage` wrappers + request/response correlation.
- `useBridgeChat.ts`: replaces the SPA's `useChat` — sends `chat.send` to the
  host, feeds the `chatState` reducer from relayed `chat.event` frames.
- `ChatController.ts` (host): on `chat.send` → `POST /chat/stream`, read SSE,
  relay each event to the webview, track run state.
- Render: `ChatView`, `ToolCard`, `ApprovalCard`, `ContextGauge`, `PlanTracker`
  (adapted from the SPA). Approve/deny → `approval.decide` → `POST /approvals/{id}`.
- Stop → `chat.stop` → `POST /chat/runs/{id}/cancel`.
- **Acceptance:** send a message, see tokens stream, see tool calls/results,
  approve a gated tool, see the run complete; stop mid-run works.

### Phase 3 — Workspace binding + seamless file ops
**Goal:** the agent operates on the VS Code folder and its edits are visible.
- `workspace/binding.ts`: on activate + `onDidChangeWorkspaceFolders`, `PUT
  /config {agent:{workspace_dir: <folder.fsPath>}}` (absolute path).
- `workspace/selection.ts`: capture active editor selection + file path; the
  "Add selection to chat" command inserts it with `file:lines` context.
- `workspace/diff.ts`:
  - On `approval_required` for `write_file`: parse the proposed `content` from
    the (JSON-string) `arguments`, open `vscode.diff` (current file ↔ proposed),
    so the user reviews before approving.
  - On `tool_result` for `write_file` (after approval): open/reveal the file.
- Reveal created files in the explorer; VS Code's watcher handles the rest.
- **Acceptance:** ask the agent to create/edit a file in the project; the file
  appears in the explorer; a diff is shown before approval; `run_command` runs in
  the project directory (verify with a `pwd`-style command).

### Phase 4 — Config view (reuse the React config)
**Goal:** edit the backend config from the extension.
- `webview/src/config/ConfigView.tsx`: adapted from the SPA's `ConfigView` —
  LLM / agent / MCP / autonomous / self_improvement forms, `react-hook-form` +
  `zod`.
- `ConfigController.ts` (host): `config.get` → `GET /config`; `config.put` →
  `PUT /config` (merge-patch); `config.validate` → `POST /config/validate`;
  `config.llmTest` → `POST /config/llm/test`; `config.history` /
  `config.restore`.
- After a save that changes the agent config, re-assert the workspace binding.
- **Acceptance:** change the LLM model / agent settings in the Config tab,
  validate, test the connection, save; the change takes effect on the next run.

### Phase 5 — Seamless-coding polish
**Goal:** the loop feels native.
- **Slash commands** in the chat input: `/stop`, `/approve`, `/deny`,
  `/compact`, `/clear`, `/new`, `/config`.
- **Status bar** (`StatusBar.ts`): run state, cost, context %, current tool;
  click → open chat.
- **Notifications**: run completed / failed / approval-required (VS Code
  `window.showInformationMessage` / `showErrorMessage`).
- **`@`-mention file picker**: VS Code `QuickPick` over workspace files to attach
  as context.
- **Keybindings** finalised: open chat, send selection, stop, approve, deny.
- **Optional:** mirror the agent's `run_command` into the integrated terminal for
  live visibility (the backend still runs it; the terminal is a mirror).
- **Acceptance:** select code → ask → watch the edit appear as a diff → approve →
  continue, without leaving the keyboard; status bar reflects state throughout.

### Phase 6 — Packaging, testing, docs
**Goal:** shippable.
- `@vscode/vsce package` → `.vsix`; `vscode:prepublish` runs the build.
- Host tests (`@vscode/test-electron`): binding, diff-on-approval, relay.
- Webview tests (`vitest`): reducer, bridge, config form validation.
- Reuse the backend contract tests for the event shapes the extension consumes.
- Extension `README.md`: install, configure (URL/token/auto-start), use, the
  multi-window caveat, the backup-path note.
- Icons, light/dark theming, accessibility (focus order, ARIA on the webview).
- **Acceptance:** `vsce package` produces an installable `.vsix`; install in a
  clean VS Code, configure, run the full loop; tests pass.

---

## 9. Security notes

- **Token storage.** The backend token goes in `context.secrets` (VS Code's secret
  storage), never in plain `settings.json`.
- **Approval gate stays.** The extension does not weaken the backend's approval
  model. Destructive tools still pause for `approval_required`; the extension
  surfaces the diff and the approve/deny, it does not auto-approve.
- **Workspace confinement.** The backend already confines file tools to the
  workspace; the extension reinforces this by binding the workspace to the
  project folder and surfacing out-of-workspace attempts.
- **Self-improvement.** If `self_improvement.auto_promote` is enabled, the
  extension should warn (the operator-approval gate is bypassed). Default off.
- **Localhost only.** The default backend URL is `127.0.0.1`. If pointed at a
  remote host, the token is the only barrier — surface that in the docs.

---

## 10. Testing strategy

| Layer | Tool | What it covers |
|---|---|---|
| Contract | existing backend `test_contract_shapes.py` / `test_event_protocol.py` | the event shapes the extension consumes |
| Host unit | `vitest` (pure logic: binding, diff parsing, relay) | host logic without VS Code |
| Host integration | `@vscode/test-electron` | activation, commands, diff view, explorer |
| Webview unit | `vitest` + `@testing-library/react` | reducer, bridge, config form |
| End-to-end | manual script in the Extension Development Host | the full seamless loop |

Keep the webview's `types.ts` in sync with `claudio_core/events.py` — the same
discipline the SPA already enforces.

---

## 11. Risks & future work

| Risk / gap | Mitigation |
|---|---|
| **Multi-window workspace** — one global `workspace_dir`, last window wins | v1: document it. Future: **per-session `workspace_dir`** on the backend (set at session creation). |
| **React reuse drift** — the SPA evolves and the vendored webview copy diverges | Keep `chatState.ts` + `types.ts` as the shared contract; a sync script or a shared package later. |
| **Webview CSP** blocking assets | Set CSP carefully in Phase 0; verify asset loading early. |
| **Backend must be running** | `autoStart` + health check + clear error if the Python env is missing. |
| **Backup path outside project** (`<workspace>/../file-history`) | Document; operator can choose an absolute `workspace_dir` with a suitable parent. |
| **Native Chat API temptation** | Deliberately not used for the main surface (see §3.2). Could be added later as a thin *additional* input surface backed by the same host controller. |

**Future work (out of scope for v1):**
- Per-session workspace binding on the backend (fixes multi-window).
- Inline diff/accept in the editor gutter (Copilot-style) for agent edits.
- Diagnostics surfacing: after an agent edit, show VS Code errors/warnings inline.
- A native Chat Participant surface for the plain-text chat box.
- Multi-root workspace support (pick which root is the agent workspace).

---

## 12. What is reused vs new (summary)

| Reused unchanged | Adapted | New |
|---|---|---|
| Entire `claudio-backend` (no code change) | `chatState.ts` (reducer) | Extension host (`src/`) |
| `claudio_core/events.py` contract | `types.ts` (contract mirror) | Bridge protocol |
| React chat rendering components | `ConfigView.tsx` | Workspace binding + diff |
| React config components | `client.ts` pattern | Status bar, slash commands |
| `backend-start.sh` / `backend-stop.sh` | Vite config (webview base) | Packaging + tests |

The backend is the stable foundation; the extension is a thin, native integration
layer on top of it. That is what keeps this tractable: the hard problems (the
agent, the tools, the approval machinery, the event protocol) are already solved
and tested. The extension's job is to make them *feel native in the editor*.
