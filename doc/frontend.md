# claudio-frontend

The React SPA. Everything the UI needs is defined by the HTTP and SSE
contract, which lives in `claudio_core/events.py` on the backend side and
`src/api/types.ts` here. The refactor left the contract stable, so the UI
needed only targeted changes — a tightened event-type mirror, corrected
payload shapes, and a new Memory view for the self-improvement store — not a
rewrite.

- **Stack:** React 18, TypeScript 5.6, Vite 5, Tailwind CSS 4
- **Data:** TanStack Query 5, `react-hook-form`, `zod`
- **Streaming:** `@microsoft/fetch-event-source`
- **Views:** six — chat, config, workspace, monitor, tasks (autonomous), memory
- **Build:** `tsc -b && vite build` — clean

---

## Capabilities

### 1. API client — `src/api/client.ts`

Thin typed wrapper over `fetch`. Base path `/api/v1`. Exports `api.get`,
`api.post`, `api.put`, `api.delete`, plus `uploadFile` (multipart) and
`downloadApi` (binary), which bypass the JSON encoding the plain helper does.

Every non-2xx response is unwrapped from the error envelope and thrown as
`ApiRequestError(status, code, message, retryable, details)`, so callers branch
on the machine-readable `code` rather than parsing messages.

### 2. SSE client — `src/api/sse.ts`

Two entry points.

`openChatStream(body, opts)` — POSTs to `/chat/stream` and reads the event
stream. It captures the `run_id` from the `run_started` event and the
`last_event_id` from every frame. If the connection drops or the server closes
while a run is still going, it transparently falls back to
`GET /chat/runs/{run_id}/stream?last_event_id=N`, so a dropped connection loses
no events and never starts a duplicate run.

`resumeRunStream(runId, lastEventId, opts)` — attaches directly to the GET
stream. Used on page load to pick up a run that was started before the reload.

Both return `{ close }`.

### 3. Type mirror — `src/api/types.ts`

The TypeScript mirror of the backend contract: config shapes, session/run/approval
records, and one interface per SSE event payload (`TokenEvent`,
`ReasoningEvent`, `ToolCallEvent`, `ToolResultEvent`, `ApprovalRequiredEvent`,
`RunCompletedEvent`, `RunFailedEvent`, `RunRestartingEvent`, `ContextUsage`,
`TurnSummary`).

**This file must be kept in sync with `claudio_core/events.py`.** Changing the
protocol on one side without the other produces runtime failures the type checker
cannot see.

`types.ts` now declares an `EventName` string-literal union of all 13 event
names, and `StreamEvent.event` is typed as `EventName` rather than `string`, so
an unknown event name is a compile error rather than a runtime surprise. The
mirror test (`test_event_protocol.py`) asserts the TS `EventName` union and the
core `ALL_EVENT_NAMES` set are identical in **both** directions — a missing or
extra event fails loudly. The payload shapes themselves are checked against the
live HTTP JSON by `test_contract_shapes.py` (SessionSummary, ApprovalRequest,
RunFailedEvent and its nested `error`), catching the class of drift a
compile-time check can't see because the JSON is untyped at the boundary.

### 4. Auth — `src/api/auth.ts`

Bearer token held in `localStorage` under `claudio.auth_token`, set once from the
UI. `getToken()` / `setToken()`. Falls back to the dev default when storage is
unavailable.

### 5. Session persistence — `src/api/storage.ts`

`saveActiveSession` / `getActiveSession` under `claudio.active_session`, so a
page reload reopens the same conversation and can reattach to an in-flight run.

### 6. Chat — `features/chat/`

The substantial view. Six components plus three logic modules.

| File | Role |
|---|---|
| `useChat.ts` | the hook: send, stop, approve, deny, clear, `loadSession`, `resumeActiveRun`, `compactSession` |
| `chatState.ts` | reducer over the event stream → the `Item` list |
| `workflow.ts` | turn/step derivation from items, argument summarisation |
| `ChatView.tsx` | layout, input, scroll, file attach |
| `ToolCard.tsx` | one tool call/result |
| `ApprovalCard.tsx` | a pending approval with approve/deny |
| `ContextGauge.tsx` | the context-usage bar |
| `PlanTracker.tsx` | the todo list |
| `CompletionStatus.tsx` | end-of-turn summary |
| `WorkflowPanel.tsx` | the numbered step trace |

The `Item` union is the core data model: `user`, `assistant`, `reasoning`,
`tool`, `approval`, `turn_status`. The reducer maps SSE events onto these.

**Reconnection logic.** The backend sends a heartbeat every 15 s. If the client
sees a gap larger than `STALE_STREAM_MS = 30000`, it treats the stream as dead
even if the browser never fired an error, and reattaches with exponential backoff
(1 s, 2 s, 4 s, 8 s, 16 s) up to `MAX_REATTACH_ATTEMPTS = 5`.

### 7. Config — `features/config/ConfigView.tsx`

LLM / agent / MCP settings, test connection, config history and restore. Uses
`react-hook-form` + `zod`. Secrets arrive redacted and are restored server-side
on save, so a saved config never leaks a key through the browser.

### 8. Workspace — `features/workspace/WorkspaceView.tsx`

Browse and delete workspace files, download. Backed by `/workspace/tree`,
`/workspace/files`, `/workspace/files/delete`, `/workspace/files/download`.

### 9. Monitor — `features/monitoring/MonitoringView.tsx`

Health, metrics, logs, run history. `/monitoring/health`, `/monitoring/metrics`,
`/monitoring/logs`, `/monitoring/runs`.

### 10. Autonomous — `features/autonomous/`

The daemon cockpit.

| File | Role |
|---|---|
| `AutonomousView.tsx` | dashboard shell |
| `TasksPanel.tsx` | user-defined scheduled tasks: create, edit, enable/disable, run now, history |
| `AuditPanel.tsx` | the audit trail |
| `DaemonConfigPanel.tsx` | health-check, maintenance, recovery and notification settings |

### 11. Memory — `features/memory/MemoryView.tsx`

The self-improvement store browser. Three tabs:

| Tab | Shows |
|---|---|
| **Learnings** | the episodic store — list, filter by status/category, full-text search (`q`), each with its recurrence count, category, validated/critical flags |
| **Drafts** | pending skill drafts with **approve** / **reject** (the promotion gate) |
| **History** | past consolidation passes |

A header strip shows the headline counts (`total` / `raw` / `processed` /
`promoted`) from `GET /learning/stats`, refreshed every 15 s. Approving a
draft calls `POST /skill-drafts/{id}/approve`, which validates and writes it
into the live skills directory; a draft that fails validation returns
`422` and the reason is surfaced. Backed by the `/learning/*` and
`/skill-drafts/*` endpoints (see
[`backend.md`](./backend.md#learning-self-improvement-read-api)).

### 12. Shared components

`components/CodeEditor.tsx` — code display/editing used by the workspace and file
views.

---

## File structure

```
claudio-frontend/
├── index.html
├── package.json
├── vite.config.ts                  dev proxy /api → 127.0.0.1:8100
├── tsconfig.json
├── dist/                           production build output
└── src/
    ├── main.tsx                    app bootstrap, router, QueryClient
    ├── App.tsx                     shell: sidebar + routes
    ├── index.css                   Tailwind entry
    ├── api/
    │   ├── client.ts               fetch wrapper, ApiRequestError
    │   ├── sse.ts                  openChatStream, resumeRunStream
    │   ├── types.ts                ★ contract mirror
    │   ├── auth.ts                 bearer token in localStorage
    │   └── storage.ts              active session in localStorage
    ├── components/
    │   └── CodeEditor.tsx
    └── features/
        ├── chat/
        │   ├── useChat.ts          the chat hook
        │   ├── chatState.ts        event → Item reducer
        │   ├── workflow.ts         turn/step derivation
        │   ├── ChatView.tsx
        │   ├── ToolCard.tsx
        │   ├── ApprovalCard.tsx
        │   ├── ContextGauge.tsx
        │   ├── PlanTracker.tsx
        │   ├── CompletionStatus.tsx
        │   └── WorkflowPanel.tsx
        ├── config/
        │   └── ConfigView.tsx
        ├── workspace/
        │   └── WorkspaceView.tsx
        ├── monitoring/
        │   └── MonitoringView.tsx
        ├── autonomous/
        │   ├── AutonomousView.tsx
        │   ├── TasksPanel.tsx
        │   ├── AuditPanel.tsx
        │   └── DaemonConfigPanel.tsx
        └── memory/
            └── MemoryView.tsx        self-improvement store (learnings/drafts/history)
```

---

## Routes

| Path | View |
|---|---|
| `/chat` | Chat |
| `/config` | Config |
| `/workspace` | Workspace |
| `/monitor` | Monitor |
| `/autonomous` | Tasks (daemon) |
| `/memory` | Memory (self-improvement) |

Responsive: bottom tab bar on mobile, collapsible sidebar on desktop. The
collapse state persists in `localStorage` under `claudio.sidebar.collapsed`.

---

## Endpoints consumed

All under `/api/v1`. Full request/response shapes are in
[`backend.md`](./backend.md).

| Group | Endpoints |
|---|---|
| Chat | `POST /chat/stream`, `POST /chat`, `POST /chat/runs/{id}/cancel`, `GET /chat/runs/{id}`, `GET /chat/runs/{id}/stream` |
| Approvals | `GET /approvals/pending`, `POST /approvals/{id}` |
| Sessions | `GET /sessions`, `GET /sessions/{id}`, `POST /sessions/{id}/compact`, `GET /sessions/{id}/messages`, `GET /sessions/{id}/active_run`, `DELETE /sessions/{id}`, `POST /sessions/{id}/clear` |
| Config | `GET /config`, `GET /config/schema`, `PUT /config`, `POST /config/validate`, `POST /config/llm/test`, `GET /config/history`, `POST /config/restore` |
| Files | `POST /files/upload` |
| Workspace | `GET /workspace/files`, `GET /workspace/tree`, `POST /workspace/files/delete`, `GET /workspace/files/download`, `POST /workspace/files/download` |
| Agent | `POST /agent/start`, `POST /agent/stop`, `POST /agent/restart`, `GET /agent/status`, `GET /agent/tools`, `GET /skills` |
| Monitoring | `GET /monitoring/health`, `GET /monitoring/metrics`, `GET /monitoring/logs`, `GET /monitoring/runs` |
| Autonomous | `GET /autonomous/status`, `GET /autonomous/health`, `GET /autonomous/health/history`, `POST /autonomous/health/run`, `POST /autonomous/maintenance/run`, `POST /autonomous/recovery/run`, `GET /autonomous/issues`, `POST /autonomous/issues/{id}/acknowledge`, `POST /autonomous/issues/{id}/resolve`, `POST /autonomous/issues/{id}/propose-fix`, `GET /autonomous/issues/{id}/suggestions`, `POST /autonomous/suggestions/{id}/accept`, `POST /autonomous/suggestions/{id}/dismiss`, `POST /autonomous/notify/test`, `GET /autonomous/audit`, `GET /autonomous/tasks`, `POST /autonomous/tasks`, `PUT /autonomous/tasks/{key}`, `DELETE /autonomous/tasks/{key}`, `POST /autonomous/tasks/{key}/enable`, `POST /autonomous/tasks/{key}/disable`, `POST /autonomous/tasks/{key}/run`, `GET /autonomous/tasks/{key}/runs` |
| Learning | `GET /learning`, `GET /learning/categories`, `GET /learning/stats`, `GET /learning/consolidations`, `GET /skill-drafts`, `GET /skill-drafts/{id}`, `POST /skill-drafts/{id}/approve`, `POST /skill-drafts/{id}/reject` |
| System | `GET /healthz` |

---

## Development

```bash
cd claudio-frontend
npm install
npm run dev        # http://localhost:5173
npm run build      # tsc -b && vite build → dist/
npm run preview
```

In development Vite proxies `/api` → `http://127.0.0.1:8100` (see
`vite.config.ts`), so the frontend and backend stay independent deployables.

To serve on the network:

```bash
npm run dev -- --host
```

### Production

`npm run build` produces a static `dist/`. Serve it with any static server in
front of the backend, with `/api` routed to the backend's port. No Node runtime
is required in production.
