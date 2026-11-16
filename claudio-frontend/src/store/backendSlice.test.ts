import { beforeEach, describe, expect, it } from 'vitest'
import {
  BackendProfileError,
  BackendSlice,
  normalizeBackendUrl,
  normalizeBackendUrlOrThrow,
} from './backendSlice'
import type { BackendProfile } from '../types/backend'

/** Minimal localStorage double so persistence can be asserted directly. */
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
  raw(key: string): string | null {
    return this.getItem(key)
  }
  parsed<T>(key: string): T | null {
    const v = this.getItem(key)
    return v === null ? null : (JSON.parse(v) as T)
  }
  clear(): void {
    this.map.clear()
  }
}

const BACKENDS_KEY = 'claudio.backends'
const ACTIVE_KEY = 'claudio.activeBackendId'

let storage: FakeStorage

beforeEach(() => {
  storage = new FakeStorage()
})

describe('normalizeBackendUrl', () => {
  it('strips trailing slashes and surrounding whitespace', () => {
    expect(normalizeBackendUrl('  http://host:8100///  ')).toBe('http://host:8100')
  })

  it('accepts an empty base (same origin)', () => {
    expect(normalizeBackendUrlOrThrow('')).toBe('')
  })

  it('rejects non-http schemes and URLs with a path', () => {
    expect(() => normalizeBackendUrlOrThrow('ftp://host')).toThrow(BackendProfileError)
    expect(() => normalizeBackendUrlOrThrow('not a url')).toThrow(BackendProfileError)
    expect(() => normalizeBackendUrlOrThrow('http://host/api')).toThrow(BackendProfileError)
    expect(() => normalizeBackendUrlOrThrow('http://host?x=1')).toThrow(BackendProfileError)
  })

  it('accepts a bare origin', () => {
    expect(normalizeBackendUrlOrThrow('https://api.claudio.internal')).toBe(
      'https://api.claudio.internal',
    )
  })
})

describe('backendSlice seeding', () => {
  it('seeds a same-origin default profile on a clean install', () => {
    const s = new BackendSlice(storage)
    expect(s.getBackends()).toHaveLength(1)
    expect(s.getActiveBackend()?.url).toBe('')
    expect(s.getActiveBackendId()).toBe(s.getBackends()[0].id)
  })

  it('migrates the legacy claudio.api_base into the default profile', () => {
    storage.setItem('claudio.api_base', 'http://192.168.1.50:8100/')
    const s = new BackendSlice(storage)
    expect(s.getActiveBackend()?.url).toBe('http://192.168.1.50:8100')
  })

  it('survives corrupt persisted JSON', () => {
    storage.setItem(BACKENDS_KEY, '{not json')
    const s = new BackendSlice(storage)
    expect(s.getBackends().length).toBeGreaterThan(0)
  })
})

describe('addBackend', () => {
  it('adds to state and persists to localStorage', () => {
    const s = new BackendSlice(storage)
    const before = s.getBackends().length

    const added = s.addBackend({ name: 'Production Server', url: 'https://api.claudio-prod.internal' })

    expect(s.getBackends()).toHaveLength(before + 1)
    expect(added.id).toBeTruthy()
    expect(added.name).toBe('Production Server')

    const persisted = storage.parsed<BackendProfile[]>(BACKENDS_KEY)!
    expect(persisted.map((b) => b.name)).toContain('Production Server')
    expect(persisted.find((b) => b.id === added.id)?.url).toBe('https://api.claudio-prod.internal')
  })

  it('normalizes the URL and keeps an optional auth token', () => {
    const s = new BackendSlice(storage)
    const added = s.addBackend({
      name: 'Staging',
      url: 'http://staging.local:8100/',
      authToken: 's3cret',
    })
    expect(added.url).toBe('http://staging.local:8100')
    expect(storage.parsed<BackendProfile[]>(BACKENDS_KEY)!.find((b) => b.id === added.id)?.authToken).toBe(
      's3cret',
    )
  })

  it('rejects an empty name and a duplicate id', () => {
    const s = new BackendSlice(storage)
    expect(() => s.addBackend({ name: '  ', url: '' })).toThrow(BackendProfileError)
    s.addBackend({ id: 'dup', name: 'A', url: '' })
    expect(() => s.addBackend({ id: 'dup', name: 'B', url: '' })).toThrow(BackendProfileError)
  })

  it('generates unique ids for each profile', () => {
    const s = new BackendSlice(storage)
    const a = s.addBackend({ name: 'A', url: '' })
    const b = s.addBackend({ name: 'B', url: '' })
    expect(a.id).not.toBe(b.id)
  })
})

describe('removeBackend', () => {
  it('removes from state and persists', () => {
    const s = new BackendSlice(storage)
    const added = s.addBackend({ name: 'Temp', url: 'http://temp.local' })
    expect(s.getBackends()).toHaveLength(2)

    s.removeBackend(added.id)

    expect(s.getBackends().map((b) => b.id)).not.toContain(added.id)
    expect(storage.parsed<BackendProfile[]>(BACKENDS_KEY)!.map((b) => b.id)).not.toContain(added.id)
  })

  it('falls back to the first remaining profile when the active one is removed', () => {
    const s = new BackendSlice(storage)
    const first = s.getBackends()[0]
    const added = s.addBackend({ name: 'Active', url: 'http://a.local' })
    s.setActiveBackend(added.id)
    expect(s.getActiveBackendId()).toBe(added.id)

    s.removeBackend(added.id)

    expect(s.getActiveBackendId()).toBe(first.id)
    expect(storage.parsed<string>(ACTIVE_KEY)).toBe(first.id)
  })

  it('clears the selection when the last profile is removed', () => {
    const s = new BackendSlice(storage)
    s.removeBackend(s.getBackends()[0].id)
    expect(s.getBackends()).toEqual([])
    expect(s.getActiveBackendId()).toBeNull()
    expect(s.getActiveBackend()).toBeNull()
  })

  it('rejects an unknown id', () => {
    const s = new BackendSlice(storage)
    expect(() => s.removeBackend('nope')).toThrow(BackendProfileError)
  })
})

describe('setActiveBackend', () => {
  it('updates state and persists the active id', () => {
    const s = new BackendSlice(storage)
    const added = s.addBackend({ name: 'Second', url: 'http://second.local' })

    s.setActiveBackend(added.id)

    expect(s.getActiveBackendId()).toBe(added.id)
    expect(s.getActiveBackend()?.name).toBe('Second')
    expect(storage.parsed<string>(ACTIVE_KEY)).toBe(added.id)
  })

  it('rejects an unknown id and allows clearing', () => {
    const s = new BackendSlice(storage)
    expect(() => s.setActiveBackend('nope')).toThrow(BackendProfileError)
    s.setActiveBackend(null)
    expect(s.getActiveBackendId()).toBeNull()
    expect(storage.parsed<string>(ACTIVE_KEY)).toBeNull()
  })
})

describe('updateBackend', () => {
  it('patches name, url and token and persists', () => {
    const s = new BackendSlice(storage)
    const added = s.addBackend({ name: 'Old', url: 'http://old.local' })

    s.updateBackend(added.id, { name: 'Renamed', url: 'https://new.local', authToken: 'tok' })
    s.updateBackend(added.id, { authToken: '' })

    const stored = storage.parsed<BackendProfile[]>(BACKENDS_KEY)!.find((b) => b.id === added.id)!
    expect(stored.name).toBe('Renamed')
    expect(stored.url).toBe('https://new.local')
    expect(stored.authToken).toBeUndefined()
  })
})

describe('subscribe', () => {
  it('notifies listeners on add, remove and switch, and stops after unsubscribe', () => {
    const s = new BackendSlice(storage)
    let calls = 0
    const unsubscribe = s.subscribe(() => calls++)
    const before = calls

    const added = s.addBackend({ name: 'X', url: 'http://x.local' })
    s.setActiveBackend(added.id)
    s.removeBackend(added.id)
    expect(calls).toBe(before + 3)

    unsubscribe()
    s.addBackend({ name: 'Y', url: 'http://y.local' })
    expect(calls).toBe(before + 3)
  })
})

describe('reload from storage', () => {
  it('restores the list and the active selection in a fresh instance', () => {
    const s1 = new BackendSlice(storage)
    const added = s1.addBackend({ name: 'Persisted', url: 'http://p.local' })
    s1.setActiveBackend(added.id)

    const s2 = new BackendSlice(storage)
    expect(s2.getBackends().map((b) => b.name)).toContain('Persisted')
    expect(s2.getActiveBackendId()).toBe(added.id)
  })

  it('ignores a persisted active id that no longer exists', () => {
    storage.setItem(BACKENDS_KEY, JSON.stringify([{ id: 'a', name: 'A', url: '' }]))
    storage.setItem(ACTIVE_KEY, JSON.stringify('ghost'))
    const s = new BackendSlice(storage)
    expect(s.getActiveBackendId()).toBe('a')
  })

  it('reset() clears storage and reseeds', () => {
    const s = new BackendSlice(storage)
    s.addBackend({ name: 'Gone', url: 'http://g.local' })
    s.reset()
    expect(s.getBackends()).toHaveLength(1)
    expect(storage.parsed<BackendProfile[]>(BACKENDS_KEY)).toHaveLength(1)
  })
})
