import { fetchEventSource } from '@microsoft/fetch-event-source'
import { getToken } from './auth'
import type { EventName, StreamEvent } from './types'

export interface SseHandle {
  close: () => void
}

interface StreamOptions {
  onEvent: (ev: StreamEvent) => void
  onClose?: () => void
  onError?: (err: unknown) => void
}

/**
 * Open a POST SSE stream to /chat/stream. The backend emits a `run_started`
 * event carrying the run_id; if the connection drops we reconnect through the
 * resumable GET /chat/runs/{run_id}/stream endpoint using last_event_id so no
 * events are lost and no duplicate run is started.
 */
export function openChatStream(
  body: {
    session_id: string | null
    message: string
    approval_mode?: string
    files?: string[]
  },
  opts: StreamOptions,
): SseHandle {
  let closed = false
  let runId: string | null = null
  let lastEventId: string | undefined
  const ctrl = new AbortController()

  const handle = (ev: StreamEvent) => {
    if (ev.id) lastEventId = ev.id
    if (ev.event === 'run_started') {
      const data = ev.data as { run_id?: string }
      if (data?.run_id) runId = data.run_id
    }
    opts.onEvent(ev)
  }

  const startPost = () => {
    fetchEventSource('/api/v1/chat/stream', {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${getToken()}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify(body),
      signal: ctrl.signal,
      openWhenHidden: true,
      onopen: async (res) => {
        if (!res.ok) {
          const text = await res.text().catch(() => '')
          throw new Error(`stream_open_failed:${res.status}:${text}`)
        }
      },
      onmessage: (msg) => {
        handle({ event: (msg.event || 'message') as EventName, data: safeParse(msg.data), id: msg.id || undefined })
      },
      onclose: () => {
        // Server closed cleanly. If a run was started, fall back to the resumable
        // GET stream in case the run is still going.
        if (!closed && runId) startGet()
        else opts.onClose?.()
      },
      onerror: (err) => {
        if (closed) return
        // Throwing stops fetch-event-source; the single .catch() below is the
        // sole error callback. Calling onError here too fired it twice per drop.
        throw err
      },
    }).catch(() => {
      if (closed) return
      if (runId) startGet()
      else opts.onError?.(new Error('stream_ended'))
    })
  }

  const startGet = () => {
    if (closed || !runId) return
    const qs = lastEventId ? `?last_event_id=${encodeURIComponent(lastEventId)}` : ''
    fetchEventSource(`/api/v1/chat/runs/${runId}/stream${qs}`, {
      method: 'GET',
      headers: { Authorization: `Bearer ${getToken()}` },
      signal: ctrl.signal,
      openWhenHidden: true,
      onopen: async (res) => {
        if (!res.ok) throw new Error(`stream_open_failed:${res.status}`)
      },
      onmessage: (msg) => {
        handle({ event: (msg.event || 'message') as EventName, data: safeParse(msg.data), id: msg.id || undefined })
      },
      onclose: () => opts.onClose?.(),
      onerror: (err) => {
        if (closed) return
        // Throw to stop fetch-event-source; the .catch() is the sole callback.
        throw err
      },
    }).catch(() => {
      if (!closed) opts.onError?.(new Error('stream_ended'))
    })
  }

  startPost()

  return {
    close: () => {
      closed = true
      ctrl.abort()
    },
  }
}

function safeParse(raw: string): unknown {
  if (!raw) return {}
  try {
    return JSON.parse(raw)
  } catch {
    return { raw }
  }
}

/**
 * Reattach to an already-running run's SSE stream from ``lastEventId``.
 * Used on page load to pick up a run that was started before the reload.
 */
export function resumeRunStream(
  runId: string,
  lastEventId: number,
  opts: StreamOptions,
): SseHandle {
  const ctrl = new AbortController()
  const qs = lastEventId ? `?last_event_id=${encodeURIComponent(String(lastEventId))}` : ''

  fetchEventSource(`/api/v1/chat/runs/${runId}/stream${qs}`, {
    method: 'GET',
    headers: { Authorization: `Bearer ${getToken()}` },
    signal: ctrl.signal,
    openWhenHidden: true,
    onopen: async (res) => {
      if (!res.ok) {
        const text = await res.text().catch(() => '')
        throw new Error(`stream_open_failed:${res.status}:${text}`)
      }
    },
    onmessage: (msg) => {
      opts.onEvent({
        event: (msg.event || 'message') as EventName,
        data: safeParse(msg.data),
        id: msg.id || undefined,
      })
    },
    onclose: () => opts.onClose?.(),
    onerror: (err) => {
      // Throw to stop fetch-event-source; the .catch() is the sole callback.
      throw err
    },
  }).catch(() => {
    opts.onError?.(new Error('stream_ended'))
  })

  return {
    close: () => {
      ctrl.abort()
    },
  }
}
