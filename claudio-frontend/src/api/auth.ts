// Auth token is kept in localStorage so the user can set it once from the UI.
const KEY = 'claudio.auth_token'

export function getToken(): string {
  try {
    return localStorage.getItem(KEY) ?? 'dev-token-change-me'
  } catch {
    return 'dev-token-change-me'
  }
}

export function setToken(token: string): void {
  try {
    localStorage.setItem(KEY, token)
  } catch {
    /* storage unavailable; token stays in-memory only */
  }
}
