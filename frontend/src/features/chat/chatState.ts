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

export interface ChatState {
  items: Item[]
  streaming: boolean
  runId: string | null
  usage: UsageInfo | null
  error: string | null
  pendingApproval: ApprovalRequiredEvent | null
}

export const initialChatState: ChatState = {
  items: [],
  streaming: false,
  runId: null,
  usage: null,
  error: null,
  pendingApproval: null,
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
  | { type: 'LOAD_HISTORY'; items: Item[] }

export function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'SEND_USER':
      return {
        ...state,
        items: [...state.items, { kind: 'user', id: uid('u'), text: action.text }],
        streaming: true,
        error: null,
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
      return { ...initialChatState, items: action.items }

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
      return {
        ...state,
        items: state.items.map((it) =>
          it.kind === 'tool' && it.callId === d.call_id
            ? { ...it, ok: !d.is_error, output: d.output, pending: false }
            : it,
        ),
      }
    }
    case 'approval_required': {
      const d = ev.data as ApprovalRequiredEvent
      return {
        ...state,
        streaming: false,
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
    case 'run_completed': {
      const d = ev.data as RunCompletedEvent
      return { ...state, streaming: false, usage: d.usage ?? state.usage, runId: null }
    }
    case 'run_failed': {
      const d = ev.data as RunFailedEvent
      return {
        ...state,
        streaming: false,
        runId: null,
        error: `${d.code}: ${d.message}`,
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
      }
    }
    case 'heartbeat':
      return state
    default:
      return state
  }
}
