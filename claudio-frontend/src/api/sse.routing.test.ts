import { beforeEach, describe, expect, it, vi } from 'vitest'

/**
 * Epic #40 verification: switching the active backend must re-route event
 * streams as well as REST calls. `src/api/sse.ts` builds its URLs with
 * `apiUrl()` from `src/api/connection.ts`, which resolves the active profile,
 * so stubbing fetch-event-source lets us assert the target directly.
 */
const opened: string[] = []

vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: (url: string) => {
    opened.push(url)
    return Promise.resolve()
  },
}))

class FakeStorage {
  private map = new Map<string, string>()
  getItem(key: string): string | null {
    return this.map.get(key) ?? null
  }
  setItem(key: string, value: string): void {
    this.map.set(key, value)
  }
  removeItem(key: string): void {
    this.map.delete(key)
  }
}

let storage: FakeStorage

async function freshModules() {
  vi.resetModules()
  vi.stubGlobal('localStorage', storage)
  const store = await import('../store/backendSlice')
  const sse = await import('./sse')
  return { store, sse }
}

beforeEach(() => {
  storage = new FakeStorage()
  opened.length = 0
  vi.stubGlobal('localStorage', storage)
})

describe('SSE follows the active backend (#40)', () => {
  it('opens the chat stream against the selected backend and re-targets after a switch', async () => {
    const { store, sse } = await freshModules()

    sse.openChatStream({ session_id: null, message: 'hi' }, { onEvent: () => {} })
    expect(opened[0]).toBe('/api/v1/chat/stream')

    const prod = store.backendStore.addBackend({
      name: 'Prod',
      url: 'https://api.claudio-prod.internal',
    })
    store.backendStore.setActiveBackend(prod.id)

    sse.openChatStream({ session_id: null, message: 'hi' }, { onEvent: () => {} })
    expect(opened[1]).toBe('https://api.claudio-prod.internal/api/v1/chat/stream')

    // The resumable GET stream used for reconnects re-targets too.
    sse.resumeRunStream('run-1', 3, { onEvent: () => {} })
    expect(opened[2]).toBe(
      'https://api.claudio-prod.internal/api/v1/chat/runs/run-1/stream?last_event_id=3',
    )
  })
})
