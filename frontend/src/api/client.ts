import { getToken } from './auth'
import type { ErrorEnvelope } from './types'

export class ApiRequestError extends Error {
  status: number
  code: string
  retryable: boolean
  details?: Record<string, unknown>

  constructor(status: number, code: string, message: string, retryable = false, details?: Record<string, unknown>) {
    super(message)
    this.status = status
    this.code = code
    this.retryable = retryable
    this.details = details
  }
}

const BASE = '/api/v1'

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  opts: { signal?: AbortSignal } = {},
): Promise<T> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${getToken()}`,
  }
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  let res: Response
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: opts.signal,
    })
  } catch (e) {
    throw new ApiRequestError(0, 'network_error', `Cannot reach backend: ${(e as Error).message}`, true)
  }

  const text = await res.text()
  let parsed: unknown = null
  if (text) {
    try {
      parsed = JSON.parse(text)
    } catch {
      parsed = null
    }
  }

  if (!res.ok) {
    const env = parsed as ErrorEnvelope | null
    const err = env?.error
    throw new ApiRequestError(
      res.status,
      err?.code ?? `http_${res.status}`,
      err?.message ?? res.statusText ?? 'Request failed',
      err?.retryable ?? res.status >= 500,
      err?.details,
    )
  }
  return parsed as T
}

export const api = {
  get: <T>(path: string, opts?: { signal?: AbortSignal }) => request<T>('GET', path, undefined, opts),
  post: <T>(path: string, body?: unknown, opts?: { signal?: AbortSignal }) =>
    request<T>('POST', path, body ?? {}, opts),
  put: <T>(path: string, body?: unknown, opts?: { signal?: AbortSignal }) =>
    request<T>('PUT', path, body ?? {}, opts),
  delete: <T>(path: string, opts?: { signal?: AbortSignal }) => request<T>('DELETE', path, undefined, opts),
}
