import type {
  BackendPatch,
  BackendProfile,
  BackendState,
  NewBackendProfile,
} from '../types/backend'

/**
 * Backend profile store (#47).
 *
 * A tiny observable slice — `backends` + `activeBackendId` — persisted to
 * localStorage. It is deliberately framework-agnostic (no Redux/Zustand in this
 * repo, and the store must stay importable from the HTTP layer without pulling
 * in React), so `src/services/apiClient.ts` can read it directly and React
 * components subscribe through `useBackends.ts`.
 *
 * It imports no other app module: `src/api/connection.ts` depends on this file,
 * so a back-reference would create an import cycle.
 */

const BACKENDS_KEY = 'claudio.backends'
const ACTIVE_KEY = 'claudio.activeBackendId'
/** Legacy single-base key, migrated into the default profile on first load. */
const LEGACY_BASE_KEY = 'claudio.api_base'

/** The profile every install starts with: same-origin, no stored token. */
export const DEFAULT_BACKEND_ID = 'default'

export class BackendProfileError extends Error {}

/** Strip trailing slashes so `${base}/api/v1` never doubles a slash. */
export function normalizeBackendUrl(url: string): string {
  return (url ?? '').trim().replace(/\/+$/, '')
}

/**
 * Validate a profile URL. Empty is legal (same origin); anything else must be a
 * plain http(s) origin with no path, query or fragment, because the client
 * appends `/api/v1/...` to it.
 */
export function normalizeBackendUrlOrThrow(url: string): string {
  const base = normalizeBackendUrl(url)
  if (!base) return ''
  let parsed: URL
  try {
    parsed = new URL(base)
  } catch {
    throw new BackendProfileError(`Not a valid backend URL: ${url}`)
  }
  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    throw new BackendProfileError(`Backend URL must be http(s): ${url}`)
  }
  if (parsed.pathname !== '/' || parsed.search || parsed.hash) {
    throw new BackendProfileError(
      `Backend URL must be an origin without a path (e.g. http://host:8100): ${url}`,
    )
  }
  return base
}

function makeId(): string {
  const c = globalThis.crypto as Crypto | undefined
  if (c && typeof c.randomUUID === 'function') return c.randomUUID()
  return `b-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`
}

interface Storage {
  getItem(key: string): string | null
  setItem(key: string, value: string): void
  removeItem(key: string): void
}

/** localStorage, or an in-memory map when unavailable (SSR, tests, privacy mode). */
function resolveStorage(): Storage {
  try {
    const ls = globalThis.localStorage
    if (ls) return ls
  } catch {
    /* access can throw in sandboxed frames */
  }
  const mem = new Map<string, string>()
  return {
    getItem: (k) => mem.get(k) ?? null,
    setItem: (k, v) => void mem.set(k, v),
    removeItem: (k) => void mem.delete(k),
  }
}

function readJSON<T>(storage: Storage, key: string): T | null {
  try {
    const raw = storage.getItem(key)
    return raw ? (JSON.parse(raw) as T) : null
  } catch {
    return null
  }
}

function sanitizeProfile(raw: unknown): BackendProfile | null {
  if (!raw || typeof raw !== 'object') return null
  const p = raw as Record<string, unknown>
  if (typeof p.id !== 'string' || !p.id) return null
  return {
    id: p.id,
    name: typeof p.name === 'string' && p.name ? p.name : p.id,
    url: normalizeBackendUrl(typeof p.url === 'string' ? p.url : ''),
    ...(typeof p.authToken === 'string' && p.authToken
      ? { authToken: p.authToken }
      : {}),
  }
}

function defaultProfile(storage: Storage): BackendProfile {
  // Carry over the pre-multi-backend single-address setting if one was saved.
  let legacy = ''
  try {
    legacy = normalizeBackendUrl(storage.getItem(LEGACY_BASE_KEY) ?? '')
  } catch {
    /* ignore */
  }
  return { id: DEFAULT_BACKEND_ID, name: 'Local (same origin)', url: legacy }
}

function loadInitial(storage: Storage): BackendState {
  const stored = readJSON<unknown[]>(storage, BACKENDS_KEY)
  let backends: BackendProfile[] = []
  if (Array.isArray(stored)) {
    backends = stored.map(sanitizeProfile).filter((b): b is BackendProfile => b !== null)
  }
  if (backends.length === 0) backends = [defaultProfile(storage)]

  let activeId: string | null = null
  try {
    const raw = storage.getItem(ACTIVE_KEY)
    if (raw) activeId = JSON.parse(raw) as string | null
  } catch {
    activeId = null
  }
  if (!activeId || !backends.some((b) => b.id === activeId)) activeId = backends[0].id

  return { backends, activeBackendId: activeId }
}

type Listener = () => void

/**
 * The slice. One instance per app; exported as `backendStore` below.
 * The class is exported too so tests can build an isolated instance against a
 * fake storage instead of sharing the app-wide singleton.
 */
export class BackendSlice {
  private state: BackendState
  private listeners = new Set<Listener>()
  private storage: Storage

  constructor(storage?: Storage) {
    this.storage = storage ?? resolveStorage()
    this.state = loadInitial(this.storage)
    this.persist()
  }

  // --- reads ---------------------------------------------------------------

  getState(): BackendState {
    return this.state
  }

  getBackends(): BackendProfile[] {
    return this.state.backends
  }

  getBackend(id: string): BackendProfile | undefined {
    return this.state.backends.find((b) => b.id === id)
  }

  /** The selected profile, or `null` when nothing is selected. */
  getActiveBackend(): BackendProfile | null {
    const id = this.state.activeBackendId
    if (!id) return null
    return this.state.backends.find((b) => b.id === id) ?? null
  }

  getActiveBackendId(): string | null {
    return this.state.activeBackendId
  }

  // --- writes --------------------------------------------------------------

  /** Add a profile. Returns the stored profile (with its assigned `id`). */
  addBackend(input: NewBackendProfile): BackendProfile {
    const name = (input.name ?? '').trim()
    if (!name) throw new BackendProfileError('Backend name is required')
    const url = normalizeBackendUrlOrThrow(input.url ?? '')
    const id = (input.id ?? '').trim() || makeId()
    if (this.state.backends.some((b) => b.id === id)) {
      throw new BackendProfileError(`Backend id already exists: ${id}`)
    }
    const profile: BackendProfile = {
      id,
      name,
      url,
      ...(input.authToken ? { authToken: input.authToken } : {}),
    }
    const backends = [...this.state.backends, profile]
    // The first profile added to an empty list becomes active.
    const activeBackendId = this.state.activeBackendId ?? id
    this.commit({ backends, activeBackendId })
    return profile
  }

  /** Patch an existing profile. `id` is immutable. */
  updateBackend(id: string, patch: BackendPatch): BackendProfile {
    const existing = this.state.backends.find((b) => b.id === id)
    if (!existing) throw new BackendProfileError(`Unknown backend: ${id}`)
    const updated: BackendProfile = { ...existing }
    if (patch.name !== undefined) {
      const name = patch.name.trim()
      if (!name) throw new BackendProfileError('Backend name is required')
      updated.name = name
    }
    if (patch.url !== undefined) updated.url = normalizeBackendUrlOrThrow(patch.url)
    if (patch.authToken !== undefined) {
      if (patch.authToken) updated.authToken = patch.authToken
      else delete updated.authToken
    }
    this.commit({
      ...this.state,
      backends: this.state.backends.map((b) => (b.id === id ? updated : b)),
    })
    return updated
  }

  /**
   * Remove a profile. If it was the active one, the selection falls back to the
   * first remaining profile (or `null` when the list becomes empty).
   */
  removeBackend(id: string): void {
    if (!this.state.backends.some((b) => b.id === id)) {
      throw new BackendProfileError(`Unknown backend: ${id}`)
    }
    const backends = this.state.backends.filter((b) => b.id !== id)
    let activeBackendId = this.state.activeBackendId
    if (activeBackendId === id) activeBackendId = backends[0]?.id ?? null
    this.commit({ backends, activeBackendId })
  }

  /** Select a backend. Pass `null` to clear the selection. */
  setActiveBackend(id: string | null): void {
    if (id !== null && !this.state.backends.some((b) => b.id === id)) {
      throw new BackendProfileError(`Unknown backend: ${id}`)
    }
    if (this.state.activeBackendId === id) return
    this.commit({ ...this.state, activeBackendId: id })
  }

  // --- subscription --------------------------------------------------------

  /** Listen for any state change. Returns an unsubscribe function. */
  subscribe(listener: Listener): () => void {
    this.listeners.add(listener)
    return () => this.listeners.delete(listener)
  }

  // --- test / lifecycle helpers -------------------------------------------

  /** Replace the whole state (used by tests and by future import/export). */
  replaceState(state: BackendState): void {
    this.commit({ ...state })
  }

  /** Forget everything and reload from storage. */
  reset(): void {
    try {
      this.storage.removeItem(BACKENDS_KEY)
      this.storage.removeItem(ACTIVE_KEY)
    } catch {
      /* ignore */
    }
    this.state = loadInitial(this.storage)
    this.persist()
  }

  private commit(next: BackendState): void {
    this.state = next
    this.persist()
    for (const listener of [...this.listeners]) listener()
  }

  private persist(): void {
    try {
      this.storage.setItem(BACKENDS_KEY, JSON.stringify(this.state.backends))
      this.storage.setItem(ACTIVE_KEY, JSON.stringify(this.state.activeBackendId))
    } catch {
      /* quota or unavailable storage: state still works for this session */
    }
  }
}

/** The app-wide slice instance. */
export const backendStore = new BackendSlice()

// Functional re-exports so callers can `import { getActiveBackend } from '../store/backendSlice'`.
export const getBackends = () => backendStore.getBackends()
export const getBackend = (id: string) => backendStore.getBackend(id)
export const getActiveBackend = () => backendStore.getActiveBackend()
export const getActiveBackendId = () => backendStore.getActiveBackendId()
export const addBackend = (input: NewBackendProfile) => backendStore.addBackend(input)
export const updateBackend = (id: string, patch: BackendPatch) =>
  backendStore.updateBackend(id, patch)
export const removeBackend = (id: string) => backendStore.removeBackend(id)
export const setActiveBackend = (id: string | null) => backendStore.setActiveBackend(id)
export const subscribeBackends = (listener: Listener) => backendStore.subscribe(listener)
