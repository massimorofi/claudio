import { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  Check,
  Loader2,
  Pencil,
  Plus,
  Server,
  Trash2,
  X,
  AlertTriangle,
} from 'lucide-react'
import {
  useActiveBackend,
  useBackendActions,
  useBackends,
} from '../store/useBackends'
import type { BackendProfile } from '../types/backend'
import { apiClient } from '../services/apiClient'
import { setToken } from '../api/auth'

/**
 * Backend switcher (#40).
 *
 * Lets one frontend talk to many independent `claudio-backend` instances: pick a
 * profile and every subsequent REST call and SSE stream is routed to it, because
 * `src/services/apiClient.ts` and `src/api/connection.ts` resolve the base URL
 * from the active profile at call time.
 *
 * Switching also isolates state: the react-query cache is dropped (so nothing
 * fetched from the previous backend bleeds over), a profile-specific token is
 * applied when present, and the new backend is probed so the user sees whether
 * the switch actually connected.
 */

const inputCls =
  'w-full rounded-lg border border-black/15 bg-white px-3 py-2 text-sm outline-none focus:border-brand'

type Status = { kind: 'idle' | 'checking' | 'ok' | 'error'; text?: string }

interface Draft {
  id?: string
  name: string
  url: string
  authToken: string
}

const emptyDraft: Draft = { name: '', url: '', authToken: '' }

export default function BackendSwitcher() {
  const backends = useBackends()
  const active = useActiveBackend()
  const { add, update, remove, setActive } = useBackendActions()
  const queryClient = useQueryClient()

  const [managing, setManaging] = useState(false)
  const [draft, setDraft] = useState<Draft | null>(null)
  const [status, setStatus] = useState<Status>({ kind: 'idle' })
  const [error, setError] = useState<string | null>(null)

  // Probe the active backend whenever it changes (also covers a switch made in
  // another tab, since the store persists and notifies subscribers).
  useEffect(() => {
    if (!active) {
      setStatus({ kind: 'idle' })
      return
    }
    let cancelled = false
    setStatus({ kind: 'checking' })
    apiClient
      .get<{ backend?: string }>('/version')
      .then((v) => {
        if (!cancelled) setStatus({ kind: 'ok', text: `backend ${v.backend ?? 'unknown'}` })
      })
      .catch((e: Error) => {
        if (!cancelled) setStatus({ kind: 'error', text: e.message || 'unreachable' })
      })
    return () => {
      cancelled = true
    }
  }, [active?.id, active?.url])

  const switchTo = (id: string) => {
    if (id === active?.id) return
    setError(null)
    const profile = backends.find((b) => b.id === id)
    setActive(id)
    // Per-backend credentials win over the global token.
    if (profile?.authToken) setToken(profile.authToken)
    // Drop every cached query: it was fetched from a different backend.
    queryClient.clear()
  }

  const saveDraft = () => {
    if (!draft) return
    setError(null)
    try {
      if (draft.id) {
        update(draft.id, {
          name: draft.name,
          url: draft.url,
          authToken: draft.authToken || undefined,
        })
      } else {
        add({
          name: draft.name,
          url: draft.url,
          authToken: draft.authToken || undefined,
        })
      }
      setDraft(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  const removeProfile = (b: BackendProfile) => {
    setError(null)
    try {
      remove(b.id)
      queryClient.clear()
      if (draft?.id === b.id) setDraft(null)
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="border-t border-black/10 px-3 py-3">
      <label className="mb-1 flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wide text-black/40">
        <Server size={12} /> Backend
      </label>

      <div className="flex items-center gap-1">
        <select
          value={active?.id ?? ''}
          onChange={(e) => switchTo(e.target.value)}
          className="min-w-0 flex-1 rounded-lg border border-black/15 bg-white px-2 py-1.5 text-sm"
          title={active ? `${active.name} — ${active.url || 'same origin'}` : 'No backend'}
        >
          {backends.length === 0 && <option value="">No backends configured</option>}
          {backends.map((b) => (
            <option key={b.id} value={b.id}>
              {b.name}
            </option>
          ))}
        </select>
        <button
          onClick={() => {
            setManaging((m) => !m)
            setDraft(null)
          }}
          title="Manage backends"
          className={`rounded-lg border p-1.5 ${
            managing
              ? 'border-brand bg-brand text-white'
              : 'border-black/15 text-black/50 hover:bg-black/5'
          }`}
        >
          {managing ? <X size={14} /> : <Plus size={14} />}
        </button>
      </div>

      {status.kind !== 'idle' && (
        <div
          className={`mt-1.5 flex items-start gap-1 text-[11px] ${
            status.kind === 'ok'
              ? 'text-green-700'
              : status.kind === 'error'
                ? 'text-red-700'
                : 'text-black/40'
          }`}
        >
          {status.kind === 'checking' ? (
            <Loader2 size={11} className="mt-px animate-spin" />
          ) : status.kind === 'error' ? (
            <AlertTriangle size={11} className="mt-px shrink-0" />
          ) : (
            <Check size={11} className="mt-px shrink-0" />
          )}
          <span className="min-w-0 break-words">
            {status.kind === 'checking' ? 'Connecting…' : status.text}
          </span>
        </div>
      )}

      {managing && (
        <div className="mt-2 rounded-lg border border-black/10 bg-black/[0.02] p-2">
          <div className="mb-1 text-[11px] font-medium uppercase tracking-wide text-black/40">
            Configured backends
          </div>
          <ul className="flex flex-col gap-1">
            {backends.map((b) => (
              <li key={b.id} className="flex items-center gap-1 text-xs">
                <span className="min-w-0 flex-1 truncate" title={b.url || 'same origin'}>
                  {b.name}
                  {b.id === active?.id && <span className="ml-1 text-brand">•</span>}
                </span>
                <button
                  onClick={() =>
                    setDraft({ id: b.id, name: b.name, url: b.url, authToken: b.authToken ?? '' })
                  }
                  title="Edit"
                  className="rounded p-1 text-black/40 hover:bg-black/5"
                >
                  <Pencil size={12} />
                </button>
                <button
                  onClick={() => removeProfile(b)}
                  title="Remove"
                  className="rounded p-1 text-black/40 hover:bg-red-50 hover:text-red-600"
                >
                  <Trash2 size={12} />
                </button>
              </li>
            ))}
          </ul>

          {draft ? (
            <div className="mt-2 flex flex-col gap-1.5 border-t border-black/10 pt-2">
              <input
                className={inputCls}
                placeholder="Name, e.g. Production Server"
                value={draft.name}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
              />
              <input
                className={inputCls}
                placeholder="http://host:8100  (empty = same origin)"
                value={draft.url}
                onChange={(e) => setDraft({ ...draft, url: e.target.value })}
              />
              <input
                className={inputCls}
                type="password"
                placeholder="Auth token (optional)"
                value={draft.authToken}
                onChange={(e) => setDraft({ ...draft, authToken: e.target.value })}
              />
              {error && <div className="text-[11px] text-red-700">{error}</div>}
              <div className="flex gap-2">
                <button
                  onClick={saveDraft}
                  className="flex items-center gap-1 rounded-lg bg-brand px-2.5 py-1 text-xs font-semibold text-white"
                >
                  <Check size={12} /> {draft.id ? 'Save' : 'Add'}
                </button>
                <button
                  onClick={() => {
                    setDraft(null)
                    setError(null)
                  }}
                  className="rounded-lg border border-black/15 px-2.5 py-1 text-xs"
                >
                  Cancel
                </button>
              </div>
            </div>
          ) : (
            <button
              onClick={() => setDraft({ ...emptyDraft })}
              className="mt-2 flex items-center gap-1 text-xs font-medium text-brand"
            >
              <Plus size={12} /> Add backend
            </button>
          )}
        </div>
      )}
    </div>
  )
}
