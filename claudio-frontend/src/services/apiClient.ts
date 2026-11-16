import { backendStore, normalizeBackendUrl } from '../store/backendSlice'
import type { BackendProfile } from '../types/backend'
import { ApiRequestError } from '../api/client'
import { getToken } from '../api/auth'
import type { ErrorEnvelope } from '../api/types'

/**
 * Dynamic API client (#48).
 *
 * Every request resolves its base URL from the *active backend profile* in the
 * store at call time, so switching backends re-routes all subsequent traffic
 * without recreating the client. An empty base yields the relative
 * `/api/v1/...` path (same origin), which is what the docker/nginx topology
 * needs; a full origin makes the browser call that agent directly.
 *
 * Reads the store, never writes it — mutation belongs to `backendSlice` (#47).
 */

/** The slice surface this client needs; lets tests inject an isolated store. */
export interface BackendSource {
  getActiveBackend(): BackendProfile | null
}

/** Origin of the active backend, normalized and without a trailing slash. */
export function resolveBaseUrl(source: BackendSource = backendStore): string {
  const active = source.getActiveBackend()
  return normalizeBackendUrl(active?.url ?? '')
}

/** Build a full API URL for a `/-`prefixed path against the active backend. */
export function apiUrl(path: string, source: BackendSource = backendStore): string {
  return `${resolveBaseUrl(source)}/api/v1${path}`
}

/**
 * Bearer token for the active backend: the profile's own token when it has one,
 * otherwise the global token set from the connection panel.
 */
export function authHeaders(source: BackendSource = backendStore): Record<string, string> {
  const active = source.getActiveBackend()
  return { Authorization: `Bearer ${active?.authToken || getToken()}` }
}

export interface RequestOptions {
  signal?: AbortSignal
  headers?: Record<string, string>
}

function parseBody(text: string): unknown {
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return null
  }
}

async function send(
  method: string,
  path: string,
  body: unknown,
  opts: RequestOptions,
  source: BackendSource,
): Promise<Response> {
  const headers: Record<string, string> = {
    ...authHeaders(source),
    ...opts.headers,
  }
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  try {
    return await fetch(apiUrl(path, source), {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: opts.signal,
    })
  } catch (e) {
    throw new ApiRequestError(
      0,
      'network_error',
      `Cannot reach backend: ${(e as Error).message}`,
      true,
    )
  }
}

function toError(res: Response, text: string, fallback: string): ApiRequestError {
  const env = parseBody(text) as ErrorEnvelope | null
  const err = env?.error
  return new ApiRequestError(
    res.status,
    err?.code ?? `http_${res.status}`,
    err?.message ?? res.statusText ?? fallback,
    err?.retryable ?? res.status >= 500,
    err?.details,
  )
}

/**
 * Create a client bound to a backend source. The default export is bound to the
 * app-wide slice; tests pass their own slice instance.
 */
export function createApiClient(source: BackendSource = backendStore) {
  async function request<T>(
    method: string,
    path: string,
    body?: unknown,
    opts: RequestOptions = {},
  ): Promise<T> {
    const res = await send(method, path, body, opts, source)
    const text = await res.text()
    if (!res.ok) throw toError(res, text, 'Request failed')
    return parseBody(text) as T
  }

  async function download(method: string, path: string, body?: unknown): Promise<Blob> {
    const res = await send(method, path, body, {}, source)
    if (!res.ok) throw toError(res, await res.text(), 'Download failed')
    return await res.blob()
  }

  /** Multipart upload: Content-Type is left to the browser so the boundary is right. */
  async function upload<T>(path: string, file: File, opts: RequestOptions = {}): Promise<T> {
    const form = new FormData()
    form.append('file', file)
    let res: Response
    try {
      res = await fetch(apiUrl(path, source), {
        method: 'POST',
        headers: authHeaders(source),
        body: form,
        signal: opts.signal,
      })
    } catch (e) {
      throw new ApiRequestError(
        0,
        'network_error',
        `Cannot reach backend: ${(e as Error).message}`,
        true,
      )
    }
    const text = await res.text()
    if (!res.ok) throw toError(res, text, 'Upload failed')
    return parseBody(text) as T
  }

  return {
    /** The profile every request from this call onwards targets. */
    activeBackend: (): BackendProfile | null => source.getActiveBackend(),
    baseUrl: (): string => resolveBaseUrl(source),
    url: (path: string): string => apiUrl(path, source),
    get: <T>(path: string, opts?: RequestOptions) => request<T>('GET', path, undefined, opts),
    post: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
      request<T>('POST', path, body ?? {}, opts),
    put: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
      request<T>('PUT', path, body ?? {}, opts),
    patch: <T>(path: string, body?: unknown, opts?: RequestOptions) =>
      request<T>('PATCH', path, body ?? {}, opts),
    delete: <T>(path: string, opts?: RequestOptions) =>
      request<T>('DELETE', path, undefined, opts),
    download: {
      get: (path: string) => download('GET', path, undefined),
      post: (path: string, body?: unknown) => download('POST', path, body ?? {}),
    },
    upload,
  }
}

/** The app-wide dynamic client. */
export const apiClient = createApiClient()
export type ApiClient = typeof apiClient
