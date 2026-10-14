import { useCallback, useEffect, useReducer, useRef } from 'react'
import { api } from '../../api/client'
import { openChatStream, resumeRunStream, type SseHandle } from '../../api/sse'
import type { ActiveRunResponse } from '../../api/types'
import { chatReducer, initialChatState, type ChatState, type Item } from './chatState'

export interface UseChatResult {
  state: ChatState
  send: (message: string) => void
  stop: () => void
  approve: (approvalId: string) => void
  deny: (approvalId: string) => void
  clear: () => void
  loadSession: (sessionId: string) => Promise<void>
  resumeActiveRun: (sessionId: string) => Promise<boolean>
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
    const msgs = await api.get<{ messages: { type?: string; role?: string; content: string }[] }>(
      `/sessions/${sid}/messages`,
    )
    const items: Item[] = (msgs.messages ?? []).map((m, i) => {
      const isUser = (m.type ?? m.role) === 'user'
      return {
        kind: isUser ? ('user' as const) : ('assistant' as const),
        id: `h-${i}`,
        text: typeof m.content === 'string' ? m.content : JSON.stringify(m.content),
      }
    })
    dispatch({ type: 'LOAD_HISTORY', items })
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

  return { state, send, stop, approve, deny, clear, loadSession, resumeActiveRun }
}
