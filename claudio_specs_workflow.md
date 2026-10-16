# Claudio — Execution Plan / Workflow View — Implementation Spec

Status: **IMPLEMENTED** (Phases 1, 2, 4, 5 shipped). Phase 3 — the floating workflow chip — was **dropped** after real use: it occluded the chat it floated over and duplicated the plan tracker without adding value. What shipped is the docked plan tracker, the workflow panel, the context gauge and the completion card; see `claudio_specs.md` §10 for the as-built record. The Phase 3 sections below are retained as the original design record only. Companion to `claudio_specs_agent.md`.

This spec describes adding a visible **execution plan / workflow** to the Claudio
chat, matching the feature observed in the strixper AI-Chat, plus a richer
todo-based plan tracker that leverages Claudio's existing `todo_write` tool,
plus a floating workflow chip that keeps live progress visible without covering
the chat (Phase 3), a context usage gauge that doubles as the manual compaction
trigger (Phase 4), and an always-visible completion status that makes every
request's outcome explicit even when it produced no prose (Phase 5).

---

## 1. Background — what strixper actually shows

Verified against the strixper source (`/tmp/strixper`):

- The "workflow" is a **collapsible "Actions" panel** rendered under each
  assistant answer (`frontend/src/components/AIChatTab.jsx`, `StepsBlock`).
- It lists every tool call the agent made this turn as a numbered step:

  ```
  🔧 Actions (4)
    1. read_file (path=config.yaml)
    2. run_command (command=ls -la)   ⠋   ← spinner while running
    3. web_search (query=...)         ✓
    4. write_file (path=out.md)       ✓
  ```

- **Backend** (`backend/app/routers/agent.py`): emits a `tool` SSE event with
  `phase:"call"` (`name` + a short `detail` = first 3 args as `k=v`) when a tool
  starts, and `phase:"result"` (`name` + output truncated to 1200 chars) when it
  returns.
- **Frontend** accumulates those events into a `steps` array and renders the
  numbered list, collapsed by default, with a spinner on the in-flight step and a
  "Working…" line while streaming.
- There is **no separate structured plan object**. "Plan first, then act" is just
  instruction text in the system prompt. The "workflow" is the live tool-call
  trace consolidated into one compact block.

### 1.1 The `_tool_detail` summary strixper uses

```python
def _tool_detail(raw) -> str:
    args = raw.get("arguments", "")
    parsed = json.loads(args) if args else {}
    if isinstance(parsed, dict) and parsed:
        return ", ".join(f"{k}={v}" for k, v in list(parsed.items())[:3])
    return ""
```

A short, human-readable one-liner: the first three argument pairs. For Claudio's
tools this reads naturally (`read_file → path=…`, `run_command → command=…`,
`web_search → query=…`).

---

## 2. What Claudio already has

The raw data for this feature already exists end-to-end:

| Concern | Claudio today | File |
|---|---|---|
| Tool-call events | `tool_call` (call_id, name, arguments) and `tool_result` (call_id, name, output, is_error) SSE events | `runs/events.py`, `api/types.ts` |
| Tool state | `tool` items with `pending`, `args`, `ok`, `output` | `features/chat/chatState.ts` |
| Per-tool rendering | A collapsible `ToolCard` per call showing full args + result | `features/chat/ToolCard.tsx` |
| Todo list | `todo_write` tool: `(ctx, items:[{content,status}]) → {ok,count,items}` | `agent/tools/planning.py` |
| Todo nag | `TURNS_SINCE_WRITE_NAG` reminder in the system prompt | `agent/tools/planning.py`, `agent/instructions.py` |

**The gap is purely presentation.** Claudio scatters one card per tool call
through the transcript; strixper consolidates the whole turn into one compact
numbered panel. For a 20-step task that is the difference between a tidy
"Workflow (20)" block and 20 cards interleaved with text.

No backend change is required for Phase 1.

---

## 3. Phase 1 — Consolidated "Workflow" panel (strixper parity)

**Goal:** replace the scattered per-call `ToolCard`s with a single collapsible
"Workflow" panel per assistant turn that lists the turn's tool calls as a
numbered, status-bearing step list.

### 3.1 New component: `WorkflowPanel`

File: `frontend/src/features/chat/WorkflowPanel.tsx`

Props:

```ts
interface WorkflowStep {
  callId: string
  name: string
  detail: string        // one-line summary of args (see §3.2)
  pending: boolean
  ok?: boolean
  output?: string
}

interface WorkflowPanelProps {
  steps: WorkflowStep[]
  streaming: boolean
}
```

Behavior:

- Collapsed by default. Header: `🔧 Workflow (N steps)` + chevron
  (`ChevronDown`/`ChevronRight` from lucide-react), styled like the existing
  `ReasoningBlock` so an agentic reply looks like any other reply until opened.
- When expanded, an ordered list (`<ol>`), one row per step:
  - `N. <name>` (mono, brand color) + `(<detail>)` in muted text.
  - Status icon: `Loader2` (animate-spin) while `pending`; `CircleCheck` (green)
    when `ok`; `CircleX` (red) when `!ok`.
  - The full `output` is available on a per-step expand (reuse the `pretty()`
    helper currently in `ToolCard.tsx`).
- While `streaming` is true, a trailing italic "Working…" line.
- Auto-scroll behavior must respect the existing smart auto-scroll in
  `ChatView` (do not yank the view when the user has scrolled up).

### 3.2 Helper: `toolDetail()`

File: `frontend/src/features/chat/workflow.ts` (or colocated in `WorkflowPanel.tsx`)

```ts
export function toolDetail(args: string): string {
  let parsed: unknown
  try {
    parsed = args ? JSON.parse(args) : {}
  } catch {
    return ''
  }
  if (parsed && typeof parsed === 'object') {
    const entries = Object.entries(parsed as Record<string, unknown>).slice(0, 3)
    return entries.map(([k, v]) => `${k}=${formatValue(v)}`).join(', ')
  }
  return ''
}
```

`formatValue(v)`: stringify scalars directly; for objects/arrays use a compact
`JSON.stringify` truncated to ~60 chars with an ellipsis. Keep the line short —
it is a summary, not the full payload (the full args remain available on expand).

### 3.3 Group the turn's tool calls

File: `frontend/src/features/chat/ChatView.tsx`

Claudio's `state.items` is a flat interleaved list
(`user`, `reasoning`, `assistant`, `tool`, `approval`, …). Introduce a grouping
step that segments the items into **turns**: a turn is everything after a `user`
item up to (but not including) the next `user` item.

For each turn:

- Collect the `tool` items → render one `WorkflowPanel` (the numbered trace).
- Render the turn's `reasoning` and `assistant` text as today.
- Render `approval` items inline in the step list (see §3.5).
- Place the `WorkflowPanel` **after** the turn's final assistant text (strixper
  puts Actions under the answer). If the turn has no assistant text yet (still
  streaming), render the panel at the end of the streaming area.

Implementation note: keep the grouping as a small pure function
(`groupIntoTurns(items): Turn[]`) so it is unit-testable and does not complicate
the render path.

### 3.4 Replace vs. keep the per-call ToolCard

Recommendation: **replace** the scattered `ToolCard`s with the single
`WorkflowPanel`. The panel's per-step expand shows the same args/output, so the
standalone cards become redundant. Keep `ToolCard.tsx` in the repo (it is reused
by the expand) or fold its markup into `WorkflowPanel`.

If a reviewer prefers minimal change, an acceptable interim is to keep both:
the compact `WorkflowPanel` at the top of the turn plus the existing cards below.
This is noisier and not the target design.

### 3.5 Approvals interleaved

An `approval_required` event can pause the workflow mid-turn. Render the approval
as a step in the list (with its own approved/denied state) rather than as a
separate floating card, so the numbered trace stays contiguous. The existing
`ApprovalCard` can be embedded as the expanded body of that step.

### 3.6 Backend (Phase 1)

**No changes required.** The `tool_call` / `tool_result` events already carry
`name`, `arguments`, `output`, `is_error`.

Optional nicety (not required): compute a `detail` field server-side in
`runs/events.py` so the summary is produced once and is consistent across
clients. If added, extend `ToolCallEvent` with `detail: string`. This is purely
additive and does not break existing consumers.

### 3.7 Phase 1 acceptance criteria

- [ ] A multi-tool turn renders one collapsed "Workflow (N steps)" block, not N
      scattered cards.
- [ ] Each step shows `N. name (detail)` and a correct status icon.
- [ ] The in-flight step shows a spinner; the panel shows "Working…" while
      streaming.
- [ ] Expanding a step reveals full args and output.
- [ ] Approvals appear inline in the step list with approved/denied state.
- [ ] After a page reload / `loadSession`, the workflow reconstructs from the
      stored tool items (JSONL transcript).
- [ ] Smart auto-scroll is preserved (upward scroll is not overridden by new
      steps).

---

## 4. Phase 2 — Real execution-plan tracker (todo-based)

**Goal:** show a genuine plan with statuses, not just a trace. Claudio already
has `todo_write` (which strixper lacks), so this is the *more capable* version
of the feature.

### 4.1 Concept

The agent maintains a todo list via `todo_write` (`items: [{content, status}]`).
Render the **latest** todo list as a pinned checklist at the top of the streaming
turn, updating live as the agent updates it:

```
📋 Plan
  ☑ Read the config file
  ◐ Run the test suite          ← in progress
  ☐ Write the summary
  ☐ Update the docs
```

Statuses map to checkbox glyphs: pending `☐`, in-progress `◐`, completed `☑`.
(Adjust to whatever `status` values `todo_write` accepts; see
`agent/tools/planning.py`.)

### 4.2 Data flow

- The latest `todo_write` `tool_result` (or the call arguments) is the source of
  truth for the plan. Track it in `chatState`: add a `plan: TodoItem[] | null`
  field updated whenever a `todo_write` tool result arrives.
- The plan persists across the turn and is shown pinned while `streaming`, and
  remains visible (collapsed) after completion.
- On `run_restarting` (retry/compaction/fallback), the plan resets with the
  discarded items (the reducer already truncates to the last user message).

### 4.3 New component: `PlanTracker`

File: `frontend/src/features/chat/PlanTracker.tsx`

Props:

```ts
interface PlanItem {
  content: string
  status: 'pending' | 'in_progress' | 'completed'  // match todo_write's vocabulary
}
interface PlanTrackerProps {
  items: PlanItem[]
  streaming: boolean
}
```

Behavior:

- Pinned at the top of the current streaming turn (above the reasoning/answer).
- Collapsible; shows a progress summary in the header
  (e.g. `Plan · 2/4 done`).
- Each item: checkbox glyph + content. Completed items get a subtle strikethrough
  or muted color.
- When the list is empty (agent never called `todo_write`), render nothing.

### 4.4 Prompt support

To make the plan actually appear, the agent should `todo_write` at the start of
multi-step work. Claudio already has the `TURNS_SINCE_WRITE_NAG` nudge. Phase 2
adds a short instruction to `BASE_IDENTITY` / `instructions.py`:

> For any multi-step task, start by writing a todo list with `todo_write`, then
> update item statuses as you complete each step.

Keep the instruction short; the nag already covers the "update it" side.

### 4.5 Relationship to Phase 1

Phase 2's `PlanTracker` (the *intent* — what the agent plans to do) and Phase
1's `WorkflowPanel` (the *record* — what the agent actually did) are
complementary:

- `PlanTracker` pinned at the top: the plan and progress.
- `WorkflowPanel` under the answer: the executed tool-call trace.

Both can coexist. If only one is built, Phase 1 is the direct strixper parity;
Phase 2 is the richer view.

### 4.6 Phase 2 acceptance criteria

- [ ] When the agent calls `todo_write`, a pinned checklist appears.
- [ ] The checklist updates live as the agent updates statuses.
- [ ] The header shows a progress summary (done/total).
- [ ] The plan persists after the turn completes and on reload.
- [ ] The plan resets correctly on `run_restarting`.
- [ ] No plan UI appears for single-step answers that never call `todo_write`.

---

## 5. Phase 3 — Floating workflow chip

**Goal:** keep the live plan visible *while the answer streams past*, without
covering the chat. A small floating chip, **collapsed by default**, that
expands on demand into a compact vertical step diagram with the in-execution
step highlighted in green.

This phase builds directly on Phase 2: the chip is a second renderer of the
same `plan` state, so the data layer must exist first.

### 5.1 Pill-first scope (the deliberate constraint)

The chip's value is glanceability, and its cost is occlusion. The way to keep
the cost near zero is **pill-first**:

- **Collapsed (default):** a small pill, ~120×32px, fixed in a corner.
  Occludes almost nothing.
- **Expanded (on click):** a compact vertical step diagram, ~220px wide.

The collapsed pill is the always-on view; the diagram is on-demand. We do
**not** ship an always-expanded overlay.

### 5.2 Linear chain, not a branching graph

The diagram is a **vertical step chain**, not a branching DAG. Claudio's
runtime is a dynamic linear trace — the agent does not declare a branch
structure upfront, and a graph that redraws as the agent re-plans mid-run is
jittery and misleading. A vertical chain is honest to the data and needs no
new backend structure. (If a real DAG is ever wanted, that is a backend
change to emit a plan graph — out of scope here.)

### 5.3 Visual states

Collapsed pill:

```
╭──────────────────────╮
│ ◉ 2/4 done      ▸  │   ← pulsing dot while streaming
╰──────────────────────╯
```

- A small progress ring (or `done/total` count) + a **pulsing green dot**
  while `streaming` is true.
- Static (non-pulsing) when idle.
- Clicking the pill expands the diagram.

Expanded diagram:

```
╭─ Workflow ─────────── ✕ ─╮
│  ✓ Read the config file  │  ← done: green check, muted text
│  ✓ Run the test suite    │
│  ◉ Deploy to staging     │  ← in execution: green pulse, bold
│  ○ Update the docs       │  ← pending: grey
│  ─────────────────────── │
│  ⚠ step 3 awaiting       │  ← approval state, if any
│     approval             │
╰──────────────────────────╯
```

Status encoding (icon + text, **never color alone** — color-blind safe):

| State | Icon | Color |
|---|---|---|
| completed | `✓` | green, muted text |
| in execution | `◉` (pulse) | green, bold |
| pending | `○` | grey |
| failed | `✗` | red |
| awaiting approval | `⚠` | amber |

### 5.4 Data wiring

No backend change. The chip is a pure function of state already in
`chatState`:

- **Nodes** = the latest `todo_write` plan (Phase 2's `plan` field).
- **Green "in execution" node** = the live `tool_call` that has no matching
  `tool_result` yet (already tracked as `pending` in the reducer).
- **Progress count** = completed items / total items in the plan.

The chip subscribes to the same SSE stream `useChat` already consumes and
re-renders on each event. No new event types are required.

### 5.5 New component: `WorkflowChip`

File: `frontend/src/features/chat/WorkflowChip.tsx`

Props:

```ts
interface WorkflowChipProps {
  plan: PlanItem[] | null
  activeStep: string | null   // label of the current pending tool call
  streaming: boolean
}
```

Behavior:

- `position: fixed`, top-right of the chat area, high `z-index` (above the
  transcript, below any modal/approval dialog — verify the stacking order).
- **Draggable** by its header. Persist position in `localStorage`
  (`claudio.workflowchip.pos`), same pattern as the sidebar collapse key in
  `App.tsx`.
- Toggle button in the chat header to show/hide the chip explicitly.
- **Auto-collapse** when the turn ends (`streaming` goes false).
- Persist collapsed/expanded state in `localStorage`
  (`claudio.workflowchip.collapsed`).
- Respect `prefers-reduced-motion`: no pulse animation when the user has
  requested reduced motion.

### 5.6 Edge cases

| Case | Handling |
|---|---|
| No plan (agent never called `todo_write`) | Chip shows a plain "working" pulse (no nodes), or hides entirely if nothing is running. |
| `run_restarting` | Chip resets with the truncated items (same as Phase 2 plan reset). |
| Page reload mid-run | Reconstruct from `loadSession`; chip renders from the reconstructed plan. |
| Approval pending | Chip shows the amber "awaiting approval" row. |
| Plan grows mid-run | Chip re-renders; keep the active step scrolled into view within the chip. |
| Small screens | Chip may be hidden below a breakpoint, or docked to a corner with reduced size. |
| Drag off-screen | Clamp position to the viewport on drag and on window resize. |

### 5.7 Phase 3 acceptance criteria

- [ ] Collapsed pill shows progress ring + count; pulses green while streaming.
- [ ] Expanded diagram shows the vertical step chain with correct per-step status.
- [ ] The in-execution step pulses green and tracks the live tool call.
- [ ] Pill is draggable; position survives reload; clamped to viewport.
- [ ] Collapsed/expanded state and position persist via `localStorage`.
- [ ] Auto-collapses when the turn ends.
- [ ] No chip appears for chats with no plan and no active work.
- [ ] Status is readable without color (icon + text).
- [ ] Pulse disabled under `prefers-reduced-motion`.
- [ ] Chip never blocks: text selection, scrolling, and clicks in the chat all work with the chip present.

### 5.8 Fallback if occlusion is a problem

If the floating chip turns out to bother users in practice, the fallback that
keeps ~90% of the value with **zero overlay** is a **sticky bar inside the
chat scroll container** (top of the scroll area, `position: sticky`): always
visible, never floating over content. Same data wiring, same component,
different positioning. Decide between floating vs. sticky after using the
chip for real.

---

## 6. Phase 4 — Context usage tracker & manual compaction

**Goal:** a small circular gauge that shows, at a glance, how much of the
context window is used and how much is free — and doubles as the manual
trigger for compaction. The push on the gauge is the user's "compact now"
control.

### 6.1 What already exists (the data is there)

| Concern | Today | Where |
|---|---|---|
| Context window size | `agent.context_window` (32768) | `settings.py` |
| Reserve held back | `agent.compaction_reserve` (8192) | `settings.py` |
| Auto-compact threshold | `context_window - reserve` | `Compactor.should_compact` |
| Last known context size | `last_context_tokens` on the session row | `manager._update_session_totals` |
| Token estimate fallback | `_estimate_items_tokens` (chars // 4) | `manager.py` |
| Compaction routine | `Compactor.compact(session, model, session_id)` | `compaction.py` |
| Circuit breaker | disabled after 3 failures | `compaction.py` |
| Active-run lookup | `run_manager.get_active_run_for_session` | `manager.py` |

What is missing is (a) a *live* per-turn context number pushed to the client,
and (b) a *manual* compaction entry point. Both are small.

### 6.2 The gauge

A donut ring (SVG `stroke-dasharray`), ~28–36px, in the chat header:

```
   ╭─────╮
   │ 38% │   ← arc = used; center = % free (or free tokens)
   ╰─────╯
```

- **Arc** = percent of the *usable* window consumed
  (usable = `context_window − reserve`).
- **Center** = free tokens remaining (e.g. `9.4k free`) or percent free —
  user preference.
- **Color bands** (of usable): green < 60%, amber 60–85%, red > 85%.
- **Threshold tick** on the ring at the auto-compact point
  (`context_window − reserve`), so the user sees when auto-compaction will
  fire.
- The reserve itself is drawn as a distinct outer band so "free" never looks
  like it includes the reserve.
- `prefers-reduced-motion`: no animated sweep; static arc.

### 6.3 The push = manual compaction

The gauge is a button. Clicking it does **not** compact instantly — it opens
a one-line confirm popover:

```
Context 82% used · 4,400 tokens free
[ Compact now ]   [ Cancel ]
```

On **Compact now**:

1. `POST /api/sessions/{session_id}/compact`.
2. On success: the gauge animates down to the new value; a toast reports the
   delta (`24,100 → 3,200 tokens`).
3. On failure: toast the reason (see §6.7).

The confirm step is deliberate: compaction rewrites the session history into a
summary and is not undoable. (If a one-click trigger is preferred later, it is
a one-line change — but default to confirm.)

While a run is active the gauge is **disabled** (tooltip: "finish the current
run first") — compacting mid-run races the run's appends to the session.

### 6.4 New SSE event: `context_usage`

Emitted so the gauge updates live without polling:

```json
{
  "event": "context_usage",
  "data": {
    "used_tokens": 12345,
    "context_window": 32768,
    "usable_tokens": 24576,
    "free_tokens": 12231,
    "percent_used": 50.2,
    "compact_threshold": 24576,
    "source": "model"
  }
}
```

`source` is `"model"` when the provider reported `input_tokens`, `"estimate"`
when the chars//4 fallback was used (common on local providers).

Emitted at three points:

1. **Run start**, right after the proactive `_maybe_compact` — so the gauge is
   populated immediately.
2. **After each model turn** (each `response.completed`), using that
   response's reported `input_tokens` (the full context sent for that turn),
   falling back to `_estimate_items_tokens(await session.get_items())` when the
   provider reports 0.
3. **After a manual compaction** (from the endpoint), so every open tab
   updates.

### 6.5 Backend changes

1. **`runs/manager.py`** — emit `context_usage`:
   - A small helper `_context_usage_payload(used: int, config) -> dict` that
     computes `usable`, `free`, `percent`, `compact_threshold` from
     `config.agent.context_window` / `compaction_reserve`.
   - Emit it after `_maybe_compact` in `_run_with_recovery`.
   - In `_run_stream`, on each `response.completed` raw event, compute `used`
     (model-reported or estimate) and emit.
2. **`api/sessions.py`** — `POST /sessions/{session_id}/compact`:
   - `require_auth`.
   - If `run_manager.get_active_run_for_session(session_id)` → `409 busy`.
   - `await supervisor.compactor.compact(session, _make_model(config), session_id)`.
   - On success: update `last_context_tokens` on the session row; return
     `{ok: true, used_tokens, free_tokens}`.
   - On failure (breaker tripped / nothing to compact): return
     `{ok: false, error: ...}` (or a 4xx).
   - Reuse the existing `Compactor` instance the RunManager holds (do not
     construct a second one — the circuit-breaker state must be shared).
3. **Config** — no new required keys. `context_window` and `compaction_reserve`
   are the source of truth. (Optional: a `compact_threshold_percent` to
   override the reserve-based threshold; not required for Phase 4.)

### 6.6 Frontend changes

1. **`api/types.ts`** — add `ContextUsage` and the `context_usage` event to
   the `StreamEvent` union.
2. **`chatState.ts`** — add `context: ContextUsage | null` to `ChatState`;
   handle `context_usage` in `applyEvent`.
3. **`ContextGauge.tsx`** — the ring component:
   - Props: `{ usage: ContextUsage | null; disabled: boolean; onCompact: () => void }`.
   - Renders the SVG donut + center label + threshold tick + color band.
   - Click → confirm popover → `onCompact`.
   - Disabled while `streaming` is true.
4. **Placement** — chat header, top-right. If the Phase 3 chip is also present,
   the gauge sits to the left of the chip (or the chip's header hosts the ring —
   see §5). Either way it is always visible and never overlaps the transcript
   body.
5. **On load** — seed the gauge from `last_context_tokens` on the session row
   (already returned by `GET /sessions/{id}`) so it shows a value before the
   first `context_usage` arrives.

### 6.7 Edge cases

| Case | Handling |
|---|---|
| Provider reports 0 usage | Fall back to `_estimate_items_tokens`; mark `source: "estimate"`. |
| Circuit breaker tripped (auto-compact disabled) | Gauge shows an "auto off" indicator; manual compact returns the breaker error in a toast. |
| Nothing to compact (< 4 items) | `compact()` returns false → toast "nothing to compact". |
| Manual compact while a run is active | `409 busy` → toast "finish the current run first". |
| Estimate vs model-reported drift | The next `context_usage` corrects the gauge; the estimate is only a stopgap. |
| Multiple tabs open | Each tab receives `context_usage`; a manual compact from one tab is seen by the others on their next event. A compact attempt while another tab's run is active → 409. |
| "Free" definition | Free is measured against the *usable* window (`window − reserve`), since the reserve is held for the model's response. The reserve is drawn as a separate band so free never appears to include it. |
| Compaction fails mid-way | Existing circuit breaker counts the failure; after 3, auto-compact disables and the gauge reflects it. |

### 6.8 Phase 4 acceptance criteria

- [ ] Gauge shows used/free against the usable window, with the compact-threshold tick visible.
- [ ] Color bands (green/amber/red) match the thresholds.
- [ ] `context_usage` arrives at run start and after each turn; the gauge updates live.
- [ ] Estimate fallback kicks in when the provider reports 0, with `source: "estimate"`.
- [ ] Clicking the gauge opens a confirm; confirming compacts and animates the gauge down.
- [ ] Manual compact is blocked (409 + toast) while a run is active.
- [ ] Manual compact updates `last_context_tokens` and propagates to other tabs.
- [ ] Circuit-breaker "auto off" state is shown on the gauge.
- [ ] Gauge is seeded from `last_context_tokens` on page load.
- [ ] `prefers-reduced-motion` disables the animated sweep.

---

## 7. Phase 5 — Completion status feedback

**Goal:** every request ends with an explicit, always-visible status marker —
done / failed / interrupted — that states what was produced (files written,
actions taken), even when the assistant produced no prose. This closes the
"did it finish, or did it silently fail?" gap.

### 7.1 The gap

Today the end of a turn is signaled only by the assistant's final text. When
the agent's answer is an *action* rather than prose — it wrote `tetris.html`
and said nothing — the chat shows the user message and then nothing. The
`run_completed` event fires, but the UI has no visible "done" bookend, so
the user cannot tell completed from failed from interrupted without scrolling
or guessing.

### 7.2 What already exists

| Concern | Today | Where |
|---|---|---|
| Run terminal events | `run_completed` / `run_failed` SSE | `manager.py` |
| Cancel → failed | `cancel_run` → `run_failed` code `cancelled` | `manager.py` |
| Connection lost → failed | frontend synthesizes `run_failed` `connection_error` | `useChat.ts` |
| Tool-call visibility | `tool_call` events carry `name` + `arguments` | `events.py` |
| Transcript mirror | JSONL per session; user + assistant entries only | `store.py` |
| Last-run stats | `cost_usd`, `input/output_tokens` on the run row | `manager.py` |

What is missing: a persisted per-turn **status record** and a **rendered
status card** that appears regardless of assistant text.

### 7.3 The completion card

A slim status bar rendered at the end of every turn, after the assistant's
final text (or in its place when there is none):

```
✓ Completed · wrote tetris.html · 14.2s
```

Failed:

```
✗ Failed · context overflow · 8.1s          [ Retry ]
```

Interrupted:

```
⏸ Interrupted · 5.2s                        [ Retry ]
```

- **Status** (icon + word, never color alone): ✓ Completed (green),
  ✗ Failed (red), ⏸ Interrupted (amber).
- **What was produced**: files written (paths), and a count of commands run
  — derived from the turn's `write_file` / `run_command` tool calls. This is
  the part that makes an action-only turn legible.
- **Duration**: wall-clock seconds for the request.
- **Retry**: shown only for failed / interrupted; re-sends the last user
  message. Disabled while another run is active.
- File paths are clickable, opening the file in the Workspace view.

### 7.4 Backend changes

1. **Accumulate produced artifacts during the run** (`runs/manager.py`):
   - In `_run_stream`, when a `tool_call` event is published with name
     `write_file` (and `edit` / `create_file` variants), parse the `path`
     argument and append it to `record.files_written`.
   - Count `run_command` calls into `record.commands_run`.
2. **Persist a `turn_status` entry** at each terminal state:
   - `_finalize_completed` → `session_store.append(session_id, {type:
     "turn_status", status: "completed", files_written: [...],
     commands_run: N, duration_seconds, cost_usd})`.
   - `_finalize_failed` → `{type: "turn_status", status: "failed",
     error_code, error_message, duration_seconds}`.
   - `_finalize_cancelled` → `{type: "turn_status", status: "interrupted",
     duration_seconds}`.
3. **Enrich `run_completed`** with a `summary` field:
   `{files_written: [...], commands_run: N, duration_seconds}` so the live
   UI renders the card without waiting for a reload. (Additive; existing
   consumers ignore it.)
4. **Duration source**: compute `duration_seconds` from the run's start to
   its terminal state (add a `started_at` to `RunRecord`, or use the DB
   `created_at` / `finished_at`).

### 7.5 Frontend changes

1. **`api/types.ts`** — add `TurnStatus` and the `turn_status` entry type;
   add `summary` to `RunCompletedEvent`.
2. **`chatState.ts`** — add a `turn_status` item kind; set it on
   `run_completed` / `run_failed` (live) and on `LOAD_HISTORY` (from stored
   `turn_status` entries).
3. **`CompletionStatus.tsx`** — the card:
   - Props: `{ status: 'completed' | 'failed' | 'interrupted'; filesWritten:
     string[]; commandsRun: number; durationSeconds: number; error?: string;
     onRetry?: () => void }`.
   - Renders the icon + label + produced-artifacts line + duration + Retry.
   - Collapses the file list if > 3 ("+ 4 more").
4. **Always render** — the card renders at the end of every turn even when
   the assistant text is empty. This is the core requirement: the absence of
   prose must never mean the absence of a status.
5. **Retry** — `onRetry` calls `send(lastUserMessage)`; the last user text is
   tracked in state. Disabled while `streaming` is true.

### 7.6 Edge cases

| Case | Handling |
|---|---|
| Empty assistant text + files written | Card: "Completed · wrote N files: …". |
| Empty assistant text + nothing produced | Card: "Completed · no output" — still explicit. |
| Failed | Card: error code/message + Retry. |
| Cancelled by user | Card: "Interrupted" + Retry. |
| Connection lost mid-run | Frontend synthesizes `run_failed` → "Interrupted" + Retry. |
| Many files written | Collapse the list beyond 3 with "+N more". |
| Old transcripts (no `turn_status`) | No card shown for those turns (graceful; new runs always write one). |
| Retry while a run is active | Retry disabled until the active run finishes. |
| Approval-paused turn | Not a terminal state; no completion card until the resumed run reaches a terminal state. |

### 7.7 Phase 5 acceptance criteria

- [ ] Every completed turn shows a "✓ Completed" card, even with empty assistant text.
- [ ] The card lists files written (paths) and commands run.
- [ ] Failed turns show "✗ Failed" + error + a working Retry button.
- [ ] Cancelled turns show "⏸ Interrupted" + Retry.
- [ ] `run_completed` carries the `summary` and the live card renders from it.
- [ ] A `turn_status` entry is persisted and the card reconstructs on reload.
- [ ] Retry re-sends the last user message and is disabled while streaming.
- [ ] Status is readable without color (icon + word).

---

## 8. Cross-cutting edge cases

| Case | Handling |
|---|---|
| Page reload mid-run | Reconstruct workflow + plan from the stored JSONL transcript via `loadSession` (already mirrors tool items). |
| `run_restarting` (retry / compaction / fallback) | Partial steps and plan are discarded; both panels reset with the truncated items. |
| Approval pause mid-workflow | Approval rendered inline as a step with approved/denied state. |
| Very long output per step | Truncate the inline summary; full output available on expand (existing `max_output_chars` clipping applies server-side). |
| Smart auto-scroll | New steps must not override the user's scroll position (existing `stickRef` logic in `ChatView`). |
| Empty turn (no tools) | No WorkflowPanel; no PlanTracker. |

---

## 9. Testing

- **Unit (`tests/` frontend or vitest):**
  - `toolDetail()` — normal args, empty args, non-JSON args, >3 args, nested
    values.
  - `groupIntoTurns()` — single turn, multiple turns, trailing user message,
    interleaved approvals.
  - Reducer: `plan` field updated on `todo_write` result; reset on
    `run_restarting`.
  - `_context_usage_payload` — model-reported vs estimate fallback, free/
    percent math against the usable window, threshold value.
  - Reducer: `context` field updated on `context_usage`; seeded on load.
  - `POST /sessions/{id}/compact` — success updates `last_context_tokens`;
    409 when a run is active; breaker-tripped returns the error.
  - `turn_status` persistence — completed / failed / interrupted entries
    written at each terminal state and reconstructed on reload;
    `run_completed.summary` carries `files_written` / `commands_run`.
- **Component:**
  - `WorkflowPanel` collapsed/expanded, pending/ok/error states, streaming
    "Working…" line.
  - `PlanTracker` empty vs populated, progress summary, status glyphs.
  - `ContextGauge` — used/free arc, color bands, threshold tick, disabled
    while streaming, reduced-motion.
  - `CompletionStatus` — completed / failed / interrupted states, file list
    collapse, Retry enabled/disabled.
- **Manual / e2e:** run a multi-step task in the UI and confirm the workflow
  consolidates, the spinner tracks the live step, the plan updates, the
  context gauge moves, and the completion card appears for an action-only
  turn (e.g. "create a file" with no prose).

---

## 10. Recommended sequencing

1. **Phase 1** first — it is the exact feature observed in strixper, requires no
   backend change, and is self-contained (one new component + a grouping function
   in `ChatView`).
2. **Phase 2** second — adds the richer todo-based plan view on top; depends on
   the agent actually using `todo_write`, so pair it with the short prompt
   instruction.
3. **Phase 3** third — the floating chip is a second renderer of the Phase 2
   `plan` state. Building the data layer first makes the chip a thin, cheap
   addition rather than a parallel implementation. Do not build it before
   Phase 2 exists.
4. **Phase 4** fourth — the context gauge needs two small backend additions (the
   `context_usage` event and the manual-compact endpoint) plus the gauge
   component. It reads only token counts, not the plan, so it is independent of
   Phases 1–3 and can be built in parallel with them if desired.
5. **Phase 5** fifth — the completion card needs the `turn_status` persistence
   and the `run_completed.summary` enrichment on the backend, plus the card
   component. It is the bookend to every turn and the most directly useful of
   the five for the "did it actually finish?" problem, so it is a strong
   candidate to build early — it can even precede Phases 3 and 4.

Phases 1–3 are frontend-only for the core behavior. Phases 4 and 5 each add a
small backend touch: the `context_usage` event and the
`POST /sessions/{id}/compact` endpoint (Phase 4); the `turn_status` transcript
entry and the `run_completed.summary` field (Phase 5). None of these break
existing behavior.
