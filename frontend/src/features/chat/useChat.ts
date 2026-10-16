import { useCallback, useEffect, useReducer, useRef } from 'react'
import { api } from '../../api/client'
import { openChatStream, resumeRunStream, type SseHandle } from '../../api/sse'
import type { ActiveRunResponse, ContextUsage } from '../../api/types'
import {
  chatReducer,
  initialChatState,
  type ChatState,
  type Item,
  type PlanItem,
} from './chatState'

export interface UseChatResult {
  state: ChatState
  send: (message: string) => void
  stop: () => void
  approve: (approvalId: string) => void
  deny: (approvalId: string) => void
  clear: () => void
  loadSession: (sessionId: string) => Promise<void>
  resumeActiveRun: (sessionId: string) => Promise<boolean>
  compactSession: () => Promise<{
    ok: boolean
    used_tokens?: number
    free_tokens?: number
    error?: string
  }>
}

export function useChat(
  sessionId: string | null,
  approvalMode: string,
  onSessionCreated: (id: string) => void,
): UseChatResult {
  const [state, dispatch] = useReducer(chatReducer, initialChatState)
  const streamRef = useRef<SseHandle | null>(null)
  const sessionRef = useRef<string | null>(sessionId)

  useEffect(() => {
    sessionRef.current = sessionId
  }, [sessionId])

  const send = useCallback(
    (message: string) => {
      if (!message.trim()) return
      dispatch({ type: 'SEND_USER', text: message })
      const handle = openChatStream(
        {
          session_id: sessionRef.current,
          message,
          approval_mode: approvalMode,
        },
        {
          onEvent: (ev) => {
            if (ev.event === 'run_started') {
              const data = ev.data as { session_id?: string }
              if (data?.session_id && !sessionRef.current) {
                sessionRef.current = data.session_id
                onSessionCreated(data.session_id)
              }
            }
            dispatch({ type: 'SSE', ev })
          },
          onClose: () => {
            dispatch({ type: 'STOPPED' })
          },
          onError: (err) => {
            dispatch({
              type: 'SSE',
              ev: {
                event: 'run_failed',
                data: {
                  code: 'connection_error',
                  message: (err as Error).message ?? 'Connection lost',
                  retryable: true,
                },
              },
            })
          },
        },
      )
      streamRef.current = handle
    },
    [approvalMode, onSessionCreated],
  )

  const stop = useCallback(() => {
    const runId = state.runId
    streamRef.current?.close()
    streamRef.current = null
    if (runId) {
      api.post(`/chat/runs/${runId}/cancel`).catch(() => undefined)
    }
    dispatch({ type: 'STOPPED' })
  }, [state.runId])

  const approve = useCallback(async (approvalId: string) => {
    try {
      await api.post(`/approvals/${approvalId}`, { decision: 'approve' })
      dispatch({ type: 'RESOLVE_APPROVAL', approvalId, decision: 'approved' })
    } catch (e) {
      dispatch({
        type: 'SSE',
        ev: {
          event: 'run_failed',
          data: {
            code: 'approval_error',
            message: (e as Error).message,
            retryable: false,
          },
        },
      })
    }
  }, [])

  const deny = useCallback(async (approvalId: string) => {
    try {
      await api.post(`/approvals/${approvalId}`, { decision: 'deny' })
      dispatch({ type: 'RESOLVE_APPROVAL', approvalId, decision: 'denied' })
    } catch (e) {
      dispatch({
        type: 'SSE',
        ev: {
          event: 'run_failed',
          data: {
            code: 'approval_error',
            message: (e as Error).message,
            retryable: false,
          },
        },
      })
    }
  }, [])

  const clear = useCallback(() => {
    streamRef.current?.close()
    streamRef.current = null
    sessionRef.current = null
    dispatch({ type: 'CLEAR' })
  }, [])

  const loadSession = useCallback(async (sid: string) => {
    streamRef.current?.close()
    streamRef.current = null
    sessionRef.current = sid
    const [msgs, row] = await Promise.all([
      api.get<{ messages: { type?: string; role?: string; content: string }[] }>(
        `/sessions/${sid}/messages`,
      ),
      api.get<Record<string, unknown>>(`/sessions/${sid}`).catch(() => null),
    ])
    const items: Item[] = []
    // callId -> index of the pending tool item, so a later tool_result can close it.
    const toolIndex: Record<string, number> = {}
    ;(msgs.messages ?? []).forEach((m, i) => {
      const r = m as unknown as Record<string, unknown>
      const t = m.type ?? m.role
      if (t === 'turn_status') {
        items.push({
          kind: 'turn_status',
          id: `ts-${i}`,
          status: (r.status as 'completed' | 'failed' | 'interrupted') ?? 'completed',
          filesWritten: (r.files_written as string[]) ?? [],
          commandsRun: (r.commands_run as number) ?? 0,
          durationSeconds: (r.duration_seconds as number) ?? 0,
          error: (r.error_message as string) ?? undefined,
        })
        return
      }
      if (t === 'tool_call') {
        const callId = (r.call_id as string) ?? `c-${i}`
        toolIndex[callId] = items.length
        items.push({
          kind: 'tool',
          id: `tc-${i}`,
          callId,
          name: (r.name as string) ?? 'tool',
          args: (r.arguments as string) ?? '',
          pending: true,
        })
        return
      }
      if (t === 'tool_result') {
        const callId = (r.call_id as string) ?? ''
        const idx = toolIndex[callId]
        if (idx != null) {
          const it = items[idx]
          if (it.kind === 'tool') {
            items[idx] = {
              ...it,
              ok: !(r.is_error as boolean),
              output: (r.output as string) ?? '',
              pending: false,
            }
          }
        }
        return
      }
      const isUser = t === 'user'
      items.push({
        kind: isUser ? ('user' as const) : ('assistant' as const),
        id: `h-${i}`,
        text: typeof m.content === 'string' ? m.content : JSON.stringify(m.content),
      })
    })
    // Seed the plan from the session row's stored todos (Phase 2).
    let plan: PlanItem[] | null = null
    const todosRaw = row?.todos
    if (typeof todosRaw === 'string' && todosRaw) {
      try {
        const parsed = JSON.parse(todosRaw)
        if (Array.isArray(parsed) && parsed.length > 0) plan = parsed as PlanItem[]
      } catch {
        /* malformed todos; leave plan empty */
      }
    }
    // Seed the context gauge from the persisted last-turn size (Phase 4).
    const context = (row?.context_usage as ContextUsage) ?? null
    dispatch({ type: 'LOAD_HISTORY', items, plan, context })
  }, [])

  const compactSession = useCallback(async () => {
    const sid = sessionRef.current
    if (!sid) return { ok: false, error: 'No active session' }
    try {
      const res = await api.post<{
        ok: boolean
        used_tokens?: number
        free_tokens?: number
        error?: string
        context_usage?: ContextUsage
      }>(`/sessions/${sid}/compact`)
      if (res.ok && res.context_usage) {
        dispatch({ type: 'SSE', ev: { event: 'context_usage', data: res.context_usage } })
      }
      return {
        ok: res.ok,
        used_tokens: res.used_tokens,
        free_tokens: res.free_tokens,
        error: res.error,
      }
    } catch (e) {
      return { ok: false, error: (e as Error).message }
    }
  }, [])

  /**
   * Reattach to a run that is still in flight for the given session (e.g. after a
   * page reload). Returns true if a live stream was attached.
   */
  const resumeActiveRun = useCallback(async (sid: string): Promise<boolean> => {
    try {
      const info = await api.get<ActiveRunResponse>(`/sessions/${sid}/active_run`)
      if (!info.active || !info.run_id) return false
      sessionRef.current = sid
      const handle = resumeRunStream(info.run_id, 0, {
        onEvent: (ev) => dispatch({ type: 'SSE', ev }),
        onClose: () => dispatch({ type: 'STOPPED' }),
        onError: (err) => {
          dispatch({
            type: 'SSE',
            ev: {
              event: 'run_failed',
              data: {
                code: 'connection_error',
                message: (err as Error).message ?? 'Connection lost',
                retryable: true,
              },
            },
          })
        },
      })
      streamRef.current = handle
      return true
    } catch {
      return false
    }
  }, [])

  useEffect(() => {
    return () => {
      streamRef.current?.close()
    }
  }, [])

  return { state, send, stop, approve, deny, clear, loadSession, resumeActiveRun, compactSession }
}
