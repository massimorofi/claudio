import { getToken } from './auth'
import { apiUrl } from './connection'
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
    res = await fetch(apiUrl(path), {
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

/**
 * Upload a single file via multipart/form-data. The plain `request` helper
 * JSON-encodes its body, which is wrong for file uploads, so this builds a
 * FormData and deliberately leaves Content-Type unset — the browser sets it
 * with the correct multipart boundary.
 */
export async function uploadFile<T>(
  path: string,
  file: File,
  opts: { signal?: AbortSignal } = {},
): Promise<T> {
  const form = new FormData()
  form.append('file', file)

  let res: Response
  try {
    res = await fetch(apiUrl(path), {
      method: 'POST',
      headers: { Authorization: `Bearer ${getToken()}` },
      body: form,
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
      err?.message ?? res.statusText ?? 'Upload failed',
      err?.retryable ?? res.status >= 500,
      err?.details,
    )
  }
  return parsed as T
}

/**
 * Fetch a file as a Blob. The plain `request` helper parses JSON, which is wrong
 * for binary downloads, so this keeps the bytes intact.
 */
async function download(method: string, path: string, body?: unknown): Promise<Blob> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${getToken()}`,
  }
  if (body !== undefined) headers['Content-Type'] = 'application/json'

  let res: Response
  try {
    res = await fetch(apiUrl(path), {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch (e) {
    throw new ApiRequestError(0, 'network_error', `Cannot reach backend: ${(e as Error).message}`, true)
  }

  if (!res.ok) {
    const text = await res.text()
    let code = `http_${res.status}`
    let message = res.statusText || 'Request failed'
    try {
      const env = JSON.parse(text)
      if (env?.error) {
        code = env.error.code ?? code
        message = env.error.message ?? message
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiRequestError(res.status, code, message, res.status >= 500)
  }
  return await res.blob()
}

/** Trigger a browser save dialog for a Blob. */
export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  // Revoke on the next tick so the download has started.
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export const downloadApi = {
  get: (path: string) => download('GET', path, undefined),
  post: (path: string, body?: unknown) => download('POST', path, body ?? {}),
}
