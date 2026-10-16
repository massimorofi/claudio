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
  | { type: 'STOPPED' }
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

    case 'STOPPED':
      return { ...state, streaming: false }

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
      return {
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
    case 'usage': {
      const d = ev.data as UsageInfo
      return { ...state, usage: d }
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
      }
      return {
        ...state,
        streaming: false,
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
      const code = d.code ?? ''
      const status: 'failed' | 'interrupted' =
        code === 'cancelled' || code === 'connection_error' ? 'interrupted' : 'failed'
      return {
        ...state,
        streaming: false,
        runId: null,
        error: `${d.code}: ${d.message}`,
        items: [
          ...state.items,
          {
            kind: 'turn_status',
            id: uid('ts'),
            status,
            filesWritten: [],
            commandsRun: 0,
            durationSeconds: 0,
            error: d.message,
          },
        ],
      }
    }
    case 'run_restarting': {
      // The failed attempt is being retried (or compacted / fallen back).
      // Discard its partial output so the retry re-streams into a clean slate:
      // keep everything up to and including the last user message.
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
        plan: null,
      }
    }
    case 'heartbeat':
      return state
    default:
      return state
  }
}

/** Parse a todo_write tool result into plan items, or null if not a valid list. */
function parsePlan(output: string | undefined): PlanItem[] | null {
  if (!output) return null
  let parsed: unknown
  try {
    parsed = typeof output === 'string' ? JSON.parse(output) : output
  } catch {
    return null
  }
  if (!parsed || typeof parsed !== 'object') return null
  const obj = parsed as Record<string, unknown>
  if (!obj.ok || !Array.isArray(obj.items)) return null
  const items: PlanItem[] = []
  for (const raw of obj.items) {
    if (!raw || typeof raw !== 'object') continue
    const content = String((raw as Record<string, unknown>).content ?? '').trim()
    const status = String((raw as Record<string, unknown>).status ?? 'pending')
    if (!content) continue
    if (status !== 'pending' && status !== 'in_progress' && status !== 'done') continue
    items.push({ content, status })
  }
  return items.length > 0 ? items : null
}
