import { useCallback, useEffect, useReducer, useRef } from 'react'
import { api, ApiRequestError } from '../../api/client'
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
  send: (message: string, files?: string[]) => void
  stop: () => void
  approve: (approvalId: string) => void
  deny: (approvalId: string) => void
  clear: () => void
  loadSession: (sessionId: string) => Promise<void>
  resumeActiveRun: (sessionId: string) => Promise<boolean>
  compactSession: () => Promise<{
    ok: boolean
    requested?: boolean
    used_tokens?: number
    free_tokens?: number
    error?: string
  }>
}

// A dropped SSE connection is reconciled by polling active_run with exponential
// backoff. The backend sends a heartbeat every 15s, so a gap larger than that
// means the stream is dead even if the browser never fired an error.
const MAX_REATTACH_ATTEMPTS = 5
const STALE_STREAM_MS = 30000

function reattachDelay(attempt: number): number {
  // 1s, 2s, 4s, 8s, 16s
  return Math.min(1000 * 2 ** attempt, 16000)
}

export function useChat(
  sessionId: string | null,
  approvalMode: string,
  onSessionCreated: (id: string) => void,
): UseChatResult {
  const [state, dispatch] = useReducer(chatReducer, initialChatState)
  const streamRef = useRef<SseHandle | null>(null)
  const sessionRef = useRef<string | null>(sessionId)
  // Last SSE event id (the backend's per-run sequence number) we processed.
  const lastEventIdRef = useRef<string | undefined>(undefined)
  // Timestamp of the last event received — used to detect a stalled stream.
  const lastEventAtRef = useRef<number>(Date.now())
  // Guards against starting overlapping reattach loops.
  const reattachingRef = useRef<boolean>(false)
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useEffect(() => {
    sessionRef.current = sessionId
  }, [sessionId])

  const clearTimer = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current)
      timerRef.current = null
    }
  }, [])

  const loadSession = useCallback(async (sid: string) => {
    streamRef.current?.close()
    streamRef.current = null
    sessionRef.current = sid
    let msgs: { messages?: { type?: string; role?: string; content: string }[] }
    try {
      msgs = await api.get<{ messages: { type?: string; role?: string; content: string }[] }>(
        `/sessions/${sid}/messages`,
      )
    } catch (e) {
      // A missing session (404) is gone — drop the saved pointer. Any other
      // error (network blip, 5xx) is transient: keep the saved session so a
      // later retry can restore it, and surface the error non-destructively.
      if (e instanceof ApiRequestError && e.status === 404) {
        // handled by caller (ChatView) which clears the saved session id.
      }
      throw e
    }
    const row = await api.get<Record<string, unknown>>(`/sessions/${sid}`).catch(() => null)
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
          inputTokens: (r.input_tokens as number) ?? 0,
          outputTokens: (r.output_tokens as number) ?? 0,
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

  /**
   * Reconcile after a dropped stream: find the session's live run and reattach to
   * it. If the run already finished while we were disconnected, reload the
   * session to pick up the authoritative final transcript. Retries with
   * backoff while the network is down; gives up (and surfaces an error) after a
   * bounded number of attempts.
   */
  const reattach = useCallback(
    async (sid: string, attempt: number) => {
      if (reattachingRef.current) return
      reattachingRef.current = true
      try {
        const info = await api.get<ActiveRunResponse>(`/sessions/${sid}/active_run`)
        reattachingRef.current = false
        if (info.active && info.run_id) {
          // The resumed stream replays from the current tail, so `run_started`
          // is never seen again — set streaming/runId explicitly or the Stop
          // control never appears.
          dispatch({ type: 'RESUME_RUN', runId: info.run_id })
          const startFrom =
            lastEventIdRef.current ?? String(info.last_seq ?? 0)
          const handle = resumeRunStream(info.run_id, Number(startFrom) || 0, {
            onEvent: (ev) => {
              if (ev.id) lastEventIdRef.current = ev.id
              lastEventAtRef.current = Date.now()
              dispatch({ type: 'SSE', ev })
              dispatch({ type: 'RECONNECTED' })
            },
            onClose: () => dispatch({ type: 'STOPPED' }),
            onError: () => {
              // This reattached stream dropped too — try again.
              reattach(sid, attempt + 1)
            },
          })
          streamRef.current = handle
          return
        }
        // No active run: the run ended while we were disconnected. Reload the
        // session so the UI reflects the final state.
        await loadSession(sid)
      } catch {
        reattachingRef.current = false
        if (attempt < MAX_REATTACH_ATTEMPTS) {
          clearTimer()
          timerRef.current = setTimeout(() => reattach(sid, attempt + 1), reattachDelay(attempt))
        } else {
          dispatch({
            type: 'SSE',
            ev: {
              event: 'run_failed',
              data: {
                code: 'connection_error',
                message: 'Connection lost — could not reconnect',
                retryable: false,
              },
            },
          })
        }
      }
    },
    [loadSession, clearTimer],
  )

  const send = useCallback(
    (message: string, files?: string[]) => {
      if (!message.trim()) return
      dispatch({ type: 'SEND_USER', text: message })
      lastEventIdRef.current = undefined
      lastEventAtRef.current = Date.now()
      const handle = openChatStream(
        {
          session_id: sessionRef.current,
          message,
          approval_mode: approvalMode,
          files: files && files.length ? files : undefined,
        },
        {
          onEvent: (ev) => {
            if (ev.id) lastEventIdRef.current = ev.id
            lastEventAtRef.current = Date.now()
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
          onError: () => {
            // A dropped stream is not a failed run. Flag it (non-terminal) and
            // reconcile against the backend; only surface a hard error if the
            // reattach loop itself gives up.
            dispatch({ type: 'CONNECTION_LOST' })
            const sid = sessionRef.current
            if (sid) {
              reattach(sid, 0)
            } else {
              dispatch({
                type: 'SSE',
                ev: {
                  event: 'run_failed',
                  data: {
                    code: 'connection_error',
                    message: 'Connection lost',
                    retryable: false,
                  },
                },
              })
            }
          },
        },
      )
      streamRef.current = handle
    },
    [approvalMode, onSessionCreated, reattach],
  )

  const stop = useCallback(() => {
    const runId = state.runId
    clearTimer()
    reattachingRef.current = false
    streamRef.current?.close()
    streamRef.current = null
    if (runId) {
      api.post(`/chat/runs/${runId}/cancel`).catch(() => undefined)
    }
    dispatch({ type: 'STOPPED' })
  }, [state.runId, clearTimer])

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
    clearTimer()
    reattachingRef.current = false
    streamRef.current?.close()
    streamRef.current = null
    sessionRef.current = null
    lastEventIdRef.current = undefined
    dispatch({ type: 'CLEAR' })
  }, [clearTimer])

  const compactSession = useCallback(async () => {
    const sid = sessionRef.current
    if (!sid) return { ok: false, error: 'No active session' }
    try {
      const res = await api.post<{
        ok: boolean
        requested?: boolean
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
        requested: res.requested,
        used_tokens: res.used_tokens,
        free_tokens: res.free_tokens,
        error: res.error,
      }
    } catch (e) {
      return { ok: false, error: (e as Error).message }
    }
  }, [])

  /**
   * Reattach to a run that is still in flight for the given session (e.g. after
   * a page reload). Returns true if a live stream was attached.
   */
  const resumeActiveRun = useCallback(
    async (sid: string): Promise<boolean> => {
      try {
        const info = await api.get<ActiveRunResponse>(`/sessions/${sid}/active_run`)
        if (!info.active || !info.run_id) return false
        sessionRef.current = sid
        // The resumed stream replays from the current tail, so `run_started`
        // is never seen again — set streaming/runId explicitly or the Stop
        // control never appears after a reload or tab switch.
        dispatch({ type: 'RESUME_RUN', runId: info.run_id })
        // Resume from the run's current sequence position, not 0 — replaying
        // from the start would duplicate assistant text already loaded from
        // the transcript.
        const startFrom = String(info.last_seq ?? 0)
        const handle = resumeRunStream(info.run_id, Number(startFrom) || 0, {
          onEvent: (ev) => {
            if (ev.id) lastEventIdRef.current = ev.id
            lastEventAtRef.current = Date.now()
            dispatch({ type: 'SSE', ev })
          },
          onClose: () => dispatch({ type: 'STOPPED' }),
          onError: () => {
            dispatch({ type: 'CONNECTION_LOST' })
            reattach(sid, 0)
          },
        })
        streamRef.current = handle
        return true
      } catch {
        return false
      }
    },
    [reattach],
  )

  // Reconcile when the app regains focus or connectivity: a phone screen that
  // slept can leave a stalled stream that never fired an error. If we think a
  // run is streaming but haven't received anything for a while, reattach.
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState !== 'visible') return
      const sid = sessionRef.current
      if (!sid) return
      const stalled = Date.now() - lastEventAtRef.current > STALE_STREAM_MS
      if (stalled) {
        dispatch({ type: 'CONNECTION_LOST' })
        reattach(sid, 0)
      }
    }
    const onOnline = () => {
      const sid = sessionRef.current
      if (!sid) return
      const stalled = Date.now() - lastEventAtRef.current > STALE_STREAM_MS
      if (stalled) {
        dispatch({ type: 'CONNECTION_LOST' })
        reattach(sid, 0)
      }
    }
    document.addEventListener('visibilitychange', onVisible)
    window.addEventListener('online', onOnline)
    return () => {
      document.removeEventListener('visibilitychange', onVisible)
      window.removeEventListener('online', onOnline)
    }
  }, [reattach])

  useEffect(() => {
    return () => {
      streamRef.current?.close()
      if (timerRef.current) clearTimeout(timerRef.current)
    }
  }, [])

  return { state, send, stop, approve, deny, clear, loadSession, resumeActiveRun, compactSession }
}
