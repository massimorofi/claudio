// Frontend-side connection settings: where to reach the agent backend.
//
// Kept in localStorage (like the auth token) so the address can be changed from
// the UI at runtime without a rebuild. Every API call reads it fresh, so a change
// takes effect on the next request.
//
// With multi-backend support (#40) the *active backend profile* in
// `src/store/backendSlice.ts` is the source of truth. The legacy single-address
// key below is still read as a fallback (and written for compatibility), and it
// is migrated into the default profile the first time the store loads.
import { getActiveBackend, normalizeBackendUrl, updateBackend } from '../store/backendSlice'

const BASE_KEY = 'claudio.api_base'

// Empty base = same origin. In the docker topology nginx serves the SPA and
// proxies /api to the agent, so the relative path is all that is needed.
const DEFAULT_BASE = ''

export function getBaseUrl(): string {
  const active = getActiveBackend()
  if (active) return normalizeBackendUrl(active.url)
  try {
    return localStorage.getItem(BASE_KEY) ?? DEFAULT_BASE
  } catch {
    return DEFAULT_BASE
  }
}

export function setBaseUrl(url: string): void {
  const base = normalizeBase(url)
  try {
    localStorage.setItem(BASE_KEY, base)
  } catch {
    /* storage unavailable; base stays at its default for this session */
  }
  // Keep the active profile in sync so the connection panel and the backend
  // switcher can never disagree about where traffic goes.
  const active = getActiveBackend()
  if (active) {
    try {
      updateBackend(active.id, { url: base })
    } catch {
      /* invalid URL: the profile keeps its previous address */
    }
  }
}

/** Strip trailing slashes so `${base}/api/v1` never doubles a slash. */
export function normalizeBase(url: string): string {
  return url.trim().replace(/\/+$/, '')
}

/**
 * Build a full API URL for a given /-prefixed path.
 *
 * An empty base yields the relative ``/api/v1/...`` path (same origin). A
 * non-empty base such as ``http://192.168.1.50:8100`` makes the browser call
 * the agent directly instead of going through the proxy; the agent's
 * ``server.cors_origins`` must then include this page's origin.
 */
export function apiUrl(path: string): string {
  return `${normalizeBase(getBaseUrl())}/api/v1${path}`
}
