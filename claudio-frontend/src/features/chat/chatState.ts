import type {
  StreamEvent,
  TokenEvent,
  ReasoningEvent,
  ToolCallEvent,
  ToolResultEvent,
  ApprovalRequiredEvent,
  RunCompletedEvent,
  RunFailedEvent,
  UsageInfo,
  ContextUsage,
  TurnSummary,
} from '../../api/types'

export type Item =
  | { kind: 'user'; id: string; text: string }
  | { kind: 'assistant'; id: string; text: string }
  | { kind: 'reasoning'; id: string; text: string }
  | {
      kind: 'tool'
      id: string
      callId: string
      name: string
      args: string
      ok?: boolean
      output?: string
      pending: boolean
    }
  | {
      kind: 'approval'
      id: string
      approvalId: string
      toolName: string
      args: string
      reason: string
      resolved?: 'approved' | 'denied'
    }
  | {
      kind: 'turn_status'
      id: string
      status: 'completed' | 'failed' | 'interrupted'
      filesWritten: string[]
      commandsRun: number
      durationSeconds: number
      inputTokens?: number
      outputTokens?: number
      error?: string
    }

export interface PlanItem {
  content: string
  status: 'pending' | 'in_progress' | 'done'
}

export interface ChatState {
  items: Item[]
  streaming: boolean
  runId: string | null
  usage: UsageInfo | null
  error: string | null
  pendingApproval: ApprovalRequiredEvent | null
  /** Latest todo_write plan (Phase 2). Null until the agent writes one. */
  plan: PlanItem[] | null
  /** Latest context usage snapshot (Phase 4). */
  context: ContextUsage | null
  /** Last user message text, for the completion-card Retry (Phase 5). */
  lastUserText: string | null
  /**
   * The SSE connection dropped but the backend run is still going. This is NOT a
   * terminal failure: `streaming` stays true (so the Stop control remains) and
   * no error is shown — a subtle "reconnecting" indicator is rendered instead.
   */
  reconnecting: boolean
  /**
   * Increments each time a mid-run compaction completes (`run_compacted`). The
   * view watches this to fire a one-shot "Context compacted" toast; it is not
   * part of the transcript itself.
   */
  compactedCount: number
}

export const initialChatState: ChatState = {
  items: [],
  streaming: false,
  runId: null,
  usage: null,
  error: null,
  pendingApproval: null,
  plan: null,
  context: null,
  lastUserText: null,
  reconnecting: false,
  compactedCount: 0,
}

let counter = 0
function uid(prefix: string): string {
  counter += 1
  return `${prefix}-${Date.now()}-${counter}`
}

export type ChatAction =
  | { type: 'SEND_USER'; text: string }
  | { type: 'SSE'; ev: StreamEvent }
  | { type: 'SET_STREAMING'; value: boolean }
  | {
      type: 'RESUME_RUN'
      runId: string
    }
  | { type: 'STOPPED' }
  | { type: 'CONNECTION_LOST' }
  | { type: 'RECONNECTED' }
  | { type: 'RESOLVE_APPROVAL'; approvalId: string; decision: 'approved' | 'denied' }
  | { type: 'CLEAR' }
  | {
      type: 'LOAD_HISTORY'
      items: Item[]
      plan?: PlanItem[] | null
      context?: ContextUsage | null
    }

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'SEND_USER':
      return {
        ...state,
        items: [...state.items, { kind: 'user', id: uid('u'), text: action.text }],
        streaming: true,
        error: null,
        lastUserText: action.text,
      }

    case 'SSE':
      return applyEvent(state, action.ev)

    case 'SET_STREAMING':
      return { ...state, streaming: action.value }

    case 'RESUME_RUN':
      // Reattached to a run that was already in flight (page reload / tab
      // switch). The `run_started` event that normally flips `streaming` and
      // `runId` is the first event of the run and is never replayed on a
      // resume, so set them here.
      return { ...state, streaming: true, runId: action.runId, error: null }

    case 'STOPPED':
      return { ...state, streaming: false, reconnecting: false }

    case 'CONNECTION_LOST':
      // Non-terminal: keep streaming/runId/items intact, just flag the drop.
      return { ...state, reconnecting: true }

    case 'RECONNECTED':
      return { ...state, reconnecting: false }

    case 'RESOLVE_APPROVAL':
      return {
        ...state,
        streaming: true,
        pendingApproval: null,
        items: state.items.map((it) =>
          it.kind === 'approval' && it.approvalId === action.approvalId
            ? { ...it, resolved: action.decision }
            : it,
        ),
      }

    case 'CLEAR':
      return { ...initialChatState }

    case 'LOAD_HISTORY':
      return {
        ...initialChatState,
        items: action.items,
        plan: action.plan ?? null,
        context: action.context ?? null,
      }

    default:
      return state
  }
}

function applyEvent(state: ChatState, ev: StreamEvent): ChatState {
  switch (ev.event) {
    case 'run_started': {
      const data = ev.data as { run_id?: string }
      return { ...state, runId: data?.run_id ?? state.runId, streaming: true, error: null }
    }
    case 'token': {
      const text = (ev.data as TokenEvent)?.text ?? ''
      const last = state.items[state.items.length - 1]
      if (last && last.kind === 'assistant') {
        const updated = { ...last, text: last.text + text }
        return { ...state, items: [...state.items.slice(0, -1), updated] }
      }
      return { ...state, items: [...state.items, { kind: 'assistant', id: uid('a'), text }] }
    }
    case 'reasoning': {
      const text = (ev.data as ReasoningEvent)?.text ?? ''
      const last = state.items[state.items.length - 1]
      if (last && last.kind === 'reasoning') {
        const updated = { ...last, text: last.text + text }
        return { ...state, items: [...state.items.slice(0, -1), updated] }
      }
      return { ...state, items: [...state.items, { kind: 'reasoning', id: uid('r'), text }] }
    }
    case 'tool_call': {
      const d = ev.data as ToolCallEvent
      const next: ChatState = {
        ...state,
        items: [
          ...state.items,
          {
            kind: 'tool',
            id: uid('t'),
            callId: d.call_id,
            name: d.name,
            args: d.arguments,
            pending: true,
          },
        ],
      }
      // Show the plan the moment the agent calls todo_write, rather than waiting
      // for the result to come back. The result handler below overwrites this
      // with the authoritative copy a beat later.
      if (d.name === 'todo_write') {
        const plan = parsePlan(d.arguments)
        if (plan) next.plan = plan
      }
      return next
    }
    case 'tool_result': {
      const d = ev.data as ToolResultEvent
      const next: ChatState = {
        ...state,
        items: state.items.map((it) =>
          it.kind === 'tool' && it.callId === d.call_id
            ? { ...it, ok: !d.is_error, output: d.output, pending: false }
            : it,
        ),
      }
      // The todo_write result is the source of truth for the plan (Phase 2).
      if (d.name === 'todo_write' && !d.is_error) {
        const plan = parsePlan(d.output)
        if (plan) next.plan = plan
      }
      return next
    }
    case 'approval_required': {
      const d = ev.data as ApprovalRequiredEvent
      // The run is paused, not finished — keep `streaming` true so the Stop
      // control stays available and the turn never looks silently dead while it
      // waits on the user.
      return {
        ...state,
        streaming: true,
        pendingApproval: d,
        items: [
          ...state.items,
          {
            kind: 'approval',
            id: uid('ap'),
            approvalId: d.approval_id,
            toolName: d.tool,
            args: d.arguments,
            reason: d.reason,
          },
        ],
      }
    }
    case 'context_usage': {
      const d = ev.data as ContextUsage
      return { ...state, context: d }
    }
    case 'run_completed': {
      const d = ev.data as RunCompletedEvent
      const summary: TurnSummary = d.summary ?? {
        files_written: [],
        commands_run: 0,
        duration_seconds: 0,
        outstanding_todos: [],
        deliverable_written: false,
        deliverable_ok: false,
        deliverable_bytes: 0,
        deliverable: [],
        task_complete: false,
      }
      return {
        ...state,
        streaming: false,
        reconnecting: false,
        usage: d.usage ?? state.usage,
        runId: null,
        items: [
          ...state.items,
          {
            kind: 'turn_status',
            id: uid('ts'),
            status: 'completed',
            filesWritten: summary.files_written ?? [],
            commandsRun: summary.commands_run ?? 0,
            durationSeconds: summary.duration_seconds ?? 0,
            inputTokens: d.usage?.input_tokens ?? 0,
            outputTokens: d.usage?.output_tokens ?? 0,
          },
        ],
      }
    }
    case 'run_failed': {
      const d = ev.data as RunFailedEvent
      const err = d.error ?? { code: '', message: '', retryable: false }
      const code = err.code ?? ''
      const status: 'failed' | 'interrupted' =
        code === 'cancelled' || code === 'connection_error' ? 'interrupted' : 'failed'
      return {
        ...state,
        streaming: false,
        reconnecting: false,
        runId: null,
        error: `${err.code}: ${err.message}`,
        items: [
          ...state.items,
          {
            kind: 'turn_status',
            id: uid('ts'),
            status,
            filesWritten: [],
            commandsRun: 0,
            durationSeconds: 0,
            error: err.message,
          },
        ],
      }
    }
    case 'run_restarting': {
      // The failed attempt is being retried (or compacted / fallen back).
      // Discard its partial output so the retry re-streams into a clean slate:
      // keep everything up to and including the last user message. The plan is
      // NOT cleared here -- it is durable task state (persisted in the session's
      // todos), not partial output. Wiping it left the plan panel empty until
      // the next todo_write, which read as "the plan stopped updating".
      let lastUser = -1
      for (let i = state.items.length - 1; i >= 0; i--) {
        if (state.items[i].kind === 'user') {
          lastUser = i
          break
        }
      }
      return {
        ...state,
        items: state.items.slice(0, lastUser + 1),
        streaming: true,
        error: null,
      }
    }
    case 'run_compacted': {
      // Mid-run compaction: the transcript is preserved (unlike run_restarting).
      // The gauge refreshes from the next context_usage event; here we clear any
      // reconnecting flag and bump the counter so the view can toast once.
      return { ...state, reconnecting: false, compactedCount: state.compactedCount + 1 }
    }
    case 'heartbeat':
      return state
    default:
      return state
  }
}

/**
 * Parse a todo_write payload into plan items, or null if it isn't a valid list.
 *
 * Called with both shapes the tool produces: the call arguments, which carry
 * `{items: [...]}` directly, and the tool result, which wraps the same list in
 * `{ok, count, items}`. Only `items` is required — demanding `ok` here would
 * drop the plan at call time, which is exactly when we want to show it.
 */
function parsePlan(raw: string | unknown): PlanItem[] | null {
  if (raw == null) return null
  let parsed: unknown
  try {
    parsed = typeof raw === 'string' ? JSON.parse(raw) : raw
  } catch {
    return null
  }
  let list: unknown = parsed
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    list = (parsed as Record<string, unknown>).items
  }
  // Local models sometimes hand the items array as a JSON *string* rather than
  // a real array. Try to parse that too before giving up, so the call-time plan
  // preview doesn't silently drop a perfectly good list.
  if (typeof list === 'string') {
    try {
      list = JSON.parse(list)
    } catch {
      return null
    }
  }
  if (!Array.isArray(list)) return null
  const items: PlanItem[] = []
  for (const entry of list) {
    if (!entry || typeof entry !== 'object') continue
    const rec = entry as Record<string, unknown>
    // Match the backend's permissive content extraction (planning.py): try the
    // canonical key first, then the aliases the backend accepts.
    let content = ''
    for (const key of ['content', 'title', 'task', 'text', 'description']) {
      const val = rec[key]
      if (typeof val === 'string' && val.trim()) {
        content = val.trim()
        break
      }
    }
    if (!content) continue
    // Coerce an unknown status to 'pending' rather than dropping the item, so
    // the preview matches the normalized list the tool persists.
    const rawStatus = String(rec.status ?? 'pending')
    const status =
      rawStatus === 'in_progress' || rawStatus === 'done' ? rawStatus : 'pending'
    items.push({ content, status })
  }
  return items.length > 0 ? items : null
}
