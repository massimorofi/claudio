import { useEffect, useRef, useState } from 'react'
import { FolderOpen, Plus, ChevronRight, X, Check } from 'lucide-react'
import { api } from '../../api/client'
import type { WorkspaceRecord, WorkspaceSuggestDir } from '../../api/types'

interface Props {
  value: string
  onChange: (path: string) => void
  disabled?: boolean
}

/**
 * Workspace picker shown before the first message of a new chat. Lets the user
 * choose one of the registered workspaces or add a new one by browsing the
 * filesystem. Once a session exists its workspace is frozen, so this control is
 * replaced by a read-only badge (see ``WorkspaceBadge``).
 */
export default function WorkspacePicker({ value, onChange, disabled }: Props) {
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState<'pick' | 'browse'>('pick')
  const [recent, setRecent] = useState<WorkspaceRecord[]>([])
  const [browsePath, setBrowsePath] = useState<string>('')
  const [dirs, setDirs] = useState<WorkspaceSuggestDir[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const rootRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (!open) return
    api
      .get<{ workspaces: WorkspaceRecord[] }>('/workspaces')
      .then((r) => setRecent(r.workspaces ?? []))
      .catch(() => undefined)
  }, [open])

  useEffect(() => {
    if (!open) return
    const onDocClick = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) {
        setOpen(false)
        setMode('pick')
      }
    }
    document.addEventListener('mousedown', onDocClick)
    return () => document.removeEventListener('mousedown', onDocClick)
  }, [open])

  const loadBrowse = async (path: string) => {
    setLoading(true)
    setError(null)
    try {
      const r = await api.get<{ dirs: WorkspaceSuggestDir[] }>(
        `/workspaces/suggest${path ? `?path=${encodeURIComponent(path)}` : ''}`,
      )
      setDirs(r.dirs ?? [])
      setBrowsePath(path)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'could not list folders')
    } finally {
      setLoading(false)
    }
  }

  const startBrowse = () => {
    setMode('browse')
    loadBrowse('')
  }

  const choose = (path: string) => {
    onChange(path)
    setOpen(false)
    setMode('pick')
  }

  const addWorkspace = async (path: string) => {
    try {
      await api.post('/workspaces', { path })
      const r = await api.get<{ workspaces: WorkspaceRecord[] }>('/workspaces')
      setRecent(r.workspaces ?? [])
      choose(path)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'could not add workspace')
    }
  }

  return (
    <div className="relative" ref={rootRef}>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 rounded-lg border border-black/15 bg-white px-2.5 py-1 text-xs text-black/70 hover:bg-black/5 disabled:opacity-40"
        title="Workspace this chat will operate on"
      >
        <FolderOpen size={13} />
        <span className="max-w-[220px] truncate">
          {value ? value.split('/').filter(Boolean).pop() || value : 'Choose workspace'}
        </span>
        <ChevronRight size={12} className="text-black/30" />
      </button>

      {open && (
        <div className="absolute left-0 top-full z-40 mt-1 w-80 rounded-xl border border-black/10 bg-white p-2 shadow-lg">
          {mode === 'pick' ? (
            <>
              <div className="mb-1 px-1 text-[11px] font-medium uppercase tracking-wide text-black/40">
                Recent workspaces
              </div>
              <div className="scroll-thin max-h-48 overflow-y-auto space-y-0.5">
                {recent.length === 0 && (
                  <div className="px-2 py-3 text-xs text-black/40">
                    No workspaces yet. Add one to get started.
                  </div>
                )}
                {recent.map((w) => (
                  <button
                    key={w.id}
                    onClick={() => choose(w.path)}
                    className={`flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-black/5 ${
                      w.path === value ? 'bg-brand/10 text-brand' : ''
                    }`}
                  >
                    <FolderOpen size={14} className="shrink-0 text-black/40" />
                    <span className="flex-1 truncate">{w.label || w.path}</span>
                    {w.path === value && <Check size={14} className="text-brand" />}
                  </button>
                ))}
              </div>
              <button
                onClick={startBrowse}
                className="mt-1 flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-sm text-brand hover:bg-brand/5"
              >
                <Plus size={14} /> Add workspace…
              </button>
            </>
          ) : (
            <>
              <div className="mb-1 flex items-center justify-between px-1">
                <span className="text-[11px] font-medium uppercase tracking-wide text-black/40">
                  Browse folders
                </span>
                <button
                  onClick={() => setMode('pick')}
                  className="text-black/40 hover:text-black/70"
                  title="Back"
                >
                  <X size={14} />
                </button>
              </div>
              <div className="mb-1 truncate rounded bg-black/5 px-2 py-1 text-xs text-black/60">
                {browsePath || '/'}
              </div>
              {error && (
                <div className="mb-1 rounded bg-red-50 px-2 py-1 text-xs text-red-700">
                  {error}
                </div>
              )}
              <div className="scroll-thin max-h-48 overflow-y-auto space-y-0.5">
                {loading && (
                  <div className="px-2 py-3 text-xs text-black/40">Loading…</div>
                )}
                {dirs.map((d) => (
                  <button
                    key={d.path}
                    onClick={() => loadBrowse(d.path)}
                    className="flex w-full items-center gap-2 rounded-lg px-2 py-1.5 text-left text-sm hover:bg-black/5"
                  >
                    <FolderOpen size={14} className="shrink-0 text-black/40" />
                    <span className="flex-1 truncate">{d.name}</span>
                    <ChevronRight size={13} className="text-black/25" />
                  </button>
                ))}
                {!loading && dirs.length === 0 && (
                  <div className="px-2 py-3 text-xs text-black/40">
                    No subfolders here.
                  </div>
                )}
              </div>
              <button
                onClick={() => addWorkspace(browsePath)}
                disabled={!browsePath}
                className="mt-1 flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-2 py-1.5 text-sm font-semibold text-white disabled:opacity-40"
              >
                <Check size={14} /> Select this folder
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}

/**
 * Read-only workspace badge shown in the session header once a session exists.
 * Surfaces the workspace path and, when it came from VS Code, that origin — so
 * "which tree am I talking to?" is always answerable at a glance.
 */
export function WorkspaceBadge({
  path,
  origin,
}: {
  path: string
  origin?: string
}) {
  if (!path) return null
  const label = path.split('/').filter(Boolean).pop() || path
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-full bg-brand/10 px-2.5 py-1 text-xs font-medium text-brand"
      title={path}
    >
      <FolderOpen size={12} />
      <span className="max-w-[220px] truncate">{label}</span>
      {origin && origin !== 'default' && origin !== 'webui' && (
        <span className="text-brand/70">· from {origin}</span>
      )}
    </span>
  )
}
