import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { RefreshCw } from 'lucide-react'
import { api } from '../../api/client'

interface AuditRow {
  id: number
  created_at: string
  actor: string
  action: string
  command: string | null
  exit_code: number | null
  detail: string | null
}

interface Filters {
  action: string
  actor: string
  since: string
  until: string
}

const EMPTY: Filters = { action: '', actor: '', since: '', until: '' }

export default function AuditPanel() {
  const [filters, setFilters] = useState<Filters>(EMPTY)
  // Snapshot the filters at query time so typing doesn't fire a request per keystroke.
  const [applied, setApplied] = useState<Filters>(EMPTY)

  const auditQ = useQuery({
    queryKey: ['audit', applied],
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('limit', '200')
      if (applied.action) params.set('action', applied.action)
      if (applied.actor) params.set('actor', applied.actor)
      if (applied.since) params.set('since', toIso(applied.since))
      if (applied.until) params.set('until', toIsoEnd(applied.until))
      return api.get<{ audit: AuditRow[] }>(`/autonomous/audit?${params.toString()}`)
    },
  })

  const rows = auditQ.data?.audit ?? []
  const hasFilters =
    applied.action || applied.actor || applied.since || applied.until

  const set = (patch: Partial<Filters>) => setFilters((f) => ({ ...f, ...patch }))

  const apply = () => setApplied({ ...filters })
  const clear = () => {
    setFilters(EMPTY)
    setApplied(EMPTY)
  }

  return (
    <div className="max-w-4xl space-y-3">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-xs text-black/60">
          Action
          <input
            value={filters.action}
            onChange={(e) => set({ action: e.target.value })}
            placeholder="command_executed"
            className="w-44 rounded-md border border-black/15 bg-white px-2 py-1 text-sm outline-none focus:border-brand"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-black/60">
          Actor
          <input
            value={filters.actor}
            onChange={(e) => set({ actor: e.target.value })}
            placeholder="operator"
            className="w-36 rounded-md border border-black/15 bg-white px-2 py-1 text-sm outline-none focus:border-brand"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-black/60">
          Since
          <input
            type="date"
            value={filters.since}
            onChange={(e) => set({ since: e.target.value })}
            className="rounded-md border border-black/15 bg-white px-2 py-1 text-sm outline-none focus:border-brand"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-black/60">
          Until
          <input
            type="date"
            value={filters.until}
            onChange={(e) => set({ until: e.target.value })}
            className="rounded-md border border-black/15 bg-white px-2 py-1 text-sm outline-none focus:border-brand"
          />
        </label>
        <button
          onClick={apply}
          className="rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
        >
          Apply
        </button>
        {hasFilters && (
          <button
            onClick={clear}
            className="rounded-lg border border-black/15 bg-white px-3 py-1.5 text-xs font-medium text-black/60 hover:bg-black/5"
          >
            Clear
          </button>
        )}
        <button
          onClick={() => auditQ.refetch()}
          className="ml-auto rounded-lg p-1.5 text-black/40 hover:bg-black/5"
          title="Refresh"
        >
          <RefreshCw size={15} />
        </button>
      </div>

      {rows.length === 0 ? (
        <div className="rounded-lg border border-black/10 bg-white p-6 text-center text-sm text-black/40">
          No audit entries match.
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-black/10 bg-white">
          <table className="w-full text-left text-xs">
            <thead className="border-b border-black/10 text-black/50">
              <tr>
                <th className="px-2 py-2">When</th>
                <th className="px-2 py-2">Actor</th>
                <th className="px-2 py-2">Action</th>
                <th className="px-2 py-2">Command</th>
                <th className="px-2 py-2">Exit</th>
                <th className="px-2 py-2">Detail</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id} className="border-b border-black/5 align-top">
                  <td className="whitespace-nowrap px-2 py-1.5 text-black/60">
                    {formatWhen(r.created_at)}
                  </td>
                  <td className="px-2 py-1.5 text-black/60">{r.actor}</td>
                  <td className="px-2 py-1.5 font-medium text-black/80">{r.action}</td>
                  <td className="px-2 py-1.5">
                    {r.command ? (
                      <code className="break-all font-mono text-[11px] text-black/70">
                        {r.command}
                      </code>
                    ) : (
                      <span className="text-black/30">—</span>
                    )}
                  </td>
                  <td className="px-2 py-1.5">
                    {r.exit_code == null ? (
                      <span className="text-black/30">—</span>
                    ) : (
                      <span
                        className={
                          r.exit_code === 0 ? 'text-green-600' : 'text-red-600'
                        }
                      >
                        {r.exit_code}
                      </span>
                    )}
                  </td>
                  <td className="px-2 py-1.5 text-black/60">{r.detail ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}

// A date-only input maps to the start of that day; "until" to the end of it,
// so a same-day range includes the whole day.
function toIso(dateStr: string): string {
  if (!dateStr) return ''
  return `${dateStr}T00:00:00`
}

function toIsoEnd(dateStr: string): string {
  if (!dateStr) return ''
  return `${dateStr}T23:59:59`
}

function formatWhen(iso: string | null): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return iso
    return d.toLocaleString()
  } catch {
    return iso
  }
}
