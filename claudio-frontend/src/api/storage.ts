// Persist the active session id so a page reload can reopen the same conversation
// and reattach to any in-flight run.
const KEY = 'claudio.active_session'

export function saveActiveSession(id: string | null): void {
  try {
    if (id) localStorage.setItem(KEY, id)
    else localStorage.removeItem(KEY)
  } catch {
    /* storage unavailable */
  }
}

export function getActiveSession(): string | null {
  try {
    return localStorage.getItem(KEY)
  } catch {
    return null
  }
}
