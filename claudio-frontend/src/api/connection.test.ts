import { beforeEach, describe, expect, it, vi } from 'vitest'
import { normalizeBase } from './connection'

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

/**
 * `src/api/connection.ts` reads the app-wide slice created at import time, so
 * each test installs a fresh localStorage double and re-imports both modules
 * together — that way the connection helper and the slice under test share one
 * store instance, exactly as they do in the app.
 */
async function freshModules() {
  vi.resetModules()
  vi.stubGlobal('localStorage', storage)
  const store = await import('../store/backendSlice')
  const conn = await import('./connection')
  return { conn, store }
}

beforeEach(() => {
  storage = new FakeStorage()
  vi.stubGlobal('localStorage', storage)
})

describe('connection bridge to the active backend (#40)', () => {
  it('migrates a legacy saved base into the default profile', async () => {
    storage.setItem('claudio.api_base', 'http://192.168.1.50:8100/')
    const { conn } = await freshModules()
    expect(conn.getBaseUrl()).toBe('http://192.168.1.50:8100')
    expect(conn.apiUrl('/sessions')).toBe('http://192.168.1.50:8100/api/v1/sessions')
  })

  it('returns a same-origin relative path when nothing is configured', async () => {
    const { conn } = await freshModules()
    expect(conn.getBaseUrl()).toBe('')
    expect(conn.apiUrl('/config')).toBe('/api/v1/config')
  })

  it('follows the active backend profile after a switch', async () => {
    const { conn, store } = await freshModules()
    const prod = store.backendStore.addBackend({
      name: 'Prod',
      url: 'https://api.claudio-prod.internal',
    })
    store.backendStore.setActiveBackend(prod.id)
    expect(conn.getBaseUrl()).toBe('https://api.claudio-prod.internal')
    expect(conn.apiUrl('/health')).toBe('https://api.claudio-prod.internal/api/v1/health')

    store.backendStore.setActiveBackend(store.backendStore.getBackends()[0].id)
    expect(conn.getBaseUrl()).toBe('')
  })

  it('keeps the active profile and the legacy key in sync on save', async () => {
    const { conn, store } = await freshModules()
    conn.setBaseUrl('http://manual.local:8100/')
    expect(conn.getBaseUrl()).toBe('http://manual.local:8100')
    expect(storage.getItem('claudio.api_base')).toBe('http://manual.local:8100')
    // The active profile was updated too, so the switcher shows the same address.
    expect(store.backendStore.getActiveBackend()?.url).toBe('http://manual.local:8100')
  })

  it('leaves the profile untouched when the typed URL is invalid', async () => {
    const { conn, store } = await freshModules()
    const before = store.backendStore.getActiveBackend()?.url
    expect(() => conn.setBaseUrl('not a url')).not.toThrow()
    expect(store.backendStore.getActiveBackend()?.url).toBe(before)
  })
})

describe('normalizeBase', () => {
  it('strips trailing slashes', () => {
    expect(normalizeBase('http://a.b//')).toBe('http://a.b')
  })
})
