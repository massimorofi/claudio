import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createApiClient, apiUrl, resolveBaseUrl } from './apiClient'
import { BackendSlice } from '../store/backendSlice'
import { ApiRequestError } from '../api/client'

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

interface Call {
  url: string
  method: string
  headers: Record<string, string>
  body?: BodyInit | null
}

let calls: Call[]

function stubFetch(status = 200, json: unknown = { ok: true }) {
  calls = []
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      calls.push({
        url: String(input),
        method: init?.method ?? 'GET',
        headers: (init?.headers ?? {}) as Record<string, string>,
        body: init?.body,
      })
      return new Response(typeof json === 'string' ? json : JSON.stringify(json), {
        status,
        headers: { 'Content-Type': 'application/json' },
      })
    }),
  )
}

function makeSlice(): BackendSlice {
  return new BackendSlice(new FakeStorage())
}

beforeEach(() => {
  vi.unstubAllGlobals()
  // Global token fallback used by the auth module when storage is unavailable.
  vi.stubGlobal('localStorage', new FakeStorage())
})

describe('apiUrl / resolveBaseUrl', () => {
  it('uses the active backend origin', () => {
    const slice = makeSlice()
    const b = slice.addBackend({ name: 'Prod', url: 'https://api.claudio-prod.internal' })
    slice.setActiveBackend(b.id)
    expect(resolveBaseUrl(slice)).toBe('https://api.claudio-prod.internal')
    expect(apiUrl('/sessions', slice)).toBe('https://api.claudio-prod.internal/api/v1/sessions')
  })

  it('falls back to a same-origin relative path when the base is empty', () => {
    const slice = makeSlice()
    expect(resolveBaseUrl(slice)).toBe('')
    expect(apiUrl('/config', slice)).toBe('/api/v1/config')
  })
})

describe('dynamic routing (acceptance criterion)', () => {
  it('re-targets subsequent calls when the active backend id changes', async () => {
    const slice = makeSlice()
    const prod = slice.addBackend({ name: 'Prod', url: 'https://prod.internal' })
    const dev = slice.addBackend({ name: 'Dev', url: 'http://dev.local:8100' })
    slice.setActiveBackend(prod.id)
    stubFetch()

    const client = createApiClient(slice)
    await client.get('/sessions')
    expect(calls[0].url).toBe('https://prod.internal/api/v1/sessions')

    // Switching the active backend must re-route everything that follows.
    slice.setActiveBackend(dev.id)
    await client.get('/sessions')
    await client.post('/chat/stream', { message: 'hi' })
    expect(calls[1].url).toBe('http://dev.local:8100/api/v1/sessions')
    expect(calls[2].url).toBe('http://dev.local:8100/api/v1/chat/stream')
    expect(calls[2].method).toBe('POST')
    expect(calls[2].body).toBe(JSON.stringify({ message: 'hi' }))

    // And back to same-origin when the default profile is selected.
    slice.setActiveBackend(slice.getBackends()[0].id)
    await client.get('/config')
    expect(calls[3].url).toBe('/api/v1/config')
  })

  it('exposes the backend each call targets', async () => {
    const slice = makeSlice()
    const b = slice.addBackend({ name: 'Prod', url: 'https://prod.internal' })
    slice.setActiveBackend(b.id)
    stubFetch()
    const client = createApiClient(slice)
    expect(client.activeBackend()?.name).toBe('Prod')
    expect(client.baseUrl()).toBe('https://prod.internal')
    expect(client.url('/health')).toBe('https://prod.internal/api/v1/health')
  })
})

describe('auth', () => {
  it('prefers the per-backend token over the global one', async () => {
    const slice = makeSlice()
    const b = slice.addBackend({ name: 'Prod', url: 'https://prod.internal', authToken: 'per-backend' })
    slice.setActiveBackend(b.id)
    stubFetch()

    await createApiClient(slice).get('/sessions')
    expect(calls[0].headers.Authorization).toBe('Bearer per-backend')
  })

  it('falls back to the global token when the profile has none', async () => {
    const slice = makeSlice()
    const b = slice.addBackend({ name: 'Plain', url: 'https://plain.internal' })
    slice.setActiveBackend(b.id)
    stubFetch()

    await createApiClient(slice).get('/sessions')
    expect(calls[0].headers.Authorization).toMatch(/^Bearer /)
    expect(calls[0].headers.Authorization).not.toBe('Bearer per-backend')
  })
})

describe('error handling', () => {
  it('maps a JSON error envelope to ApiRequestError', async () => {
    const slice = makeSlice()
    stubFetch(422, { error: { code: 'validation_error', message: 'bad path', retryable: false } })
    const client = createApiClient(slice)
    await expect(client.post('/workspaces', { path: '/' })).rejects.toMatchObject({
      status: 422,
      code: 'validation_error',
      message: 'bad path',
    })
  })

  it('maps a network failure to a retryable network_error', async () => {
    const slice = makeSlice()
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    )
    const err = (await createApiClient(slice)
      .get('/sessions')
      .catch((e) => e)) as ApiRequestError
    expect(err).toBeInstanceOf(ApiRequestError)
    expect(err.status).toBe(0)
    expect(err.code).toBe('network_error')
    expect(err.retryable).toBe(true)
  })

  it('treats a 500 as retryable even without an envelope', async () => {
    const slice = makeSlice()
    stubFetch(500, 'boom')
    const err = (await createApiClient(slice).get('/sessions').catch((e) => e)) as ApiRequestError
    expect(err).toBeInstanceOf(ApiRequestError)
    expect(err.status).toBe(500)
    expect(err.retryable).toBe(true)
  })
})
