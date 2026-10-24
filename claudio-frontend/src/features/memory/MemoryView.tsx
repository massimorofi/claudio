import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  RefreshCw,
  Search,
  Brain,
  FileText,
  History,
  Check,
  X,
  AlertTriangle,
} from 'lucide-react'
import { api } from '../../api/client'
import type {
  Learning,
  LearningCategory,
  LearningStats,
  SkillDraft,
  ConsolidationRecord,
} from '../../api/types'

type Tab = 'learnings' | 'drafts' | 'history'

export default function MemoryView() {
  const [tab, setTab] = useState<Tab>('learnings')

  const statsQ = useQuery({
    queryKey: ['learning-stats'],
    queryFn: () => api.get<LearningStats>('/learning/stats'),
    refetchInterval: 15000,
  })
  const stats = statsQ.data

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-4xl px-4 py-6 space-y-5">
        <header className="flex items-start justify-between gap-4">
          <div>
            <h1 className="flex items-center gap-2 text-lg font-semibold text-black/80">
              <Brain size={20} className="text-brand" />
              Memory
            </h1>
            <p className="mt-1 text-sm text-black/50">
              Episodic lessons captured from runs, distilled into skills.
            </p>
          </div>
        </header>

        {stats && (
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <StatTile label="Total" value={stats.total} />
            <StatTile label="Raw" value={stats.raw} />
            <StatTile label="Processed" value={stats.processed} />
            <StatTile label="Promoted" value={stats.promoted} />
          </div>
        )}

        <div className="flex gap-1 border-b border-black/10">
          <TabButton active={tab === 'learnings'} onClick={() => setTab('learnings')}>
            Learnings
          </TabButton>
          <TabButton active={tab === 'drafts'} onClick={() => setTab('drafts')}>
            Skill Drafts
          </TabButton>
          <TabButton active={tab === 'history'} onClick={() => setTab('history')}>
            Consolidation
          </TabButton>
        </div>

        {tab === 'learnings' && <LearningsTab />}
        {tab === 'drafts' && <DraftsTab />}
        {tab === 'history' && <HistoryTab />}
      </div>
    </div>
  )
}

function StatTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-black/10 bg-white px-3 py-2.5">
      <div className="text-2xl font-semibold text-black/80">{value}</div>
      <div className="text-xs text-black/50">{label}</div>
    </div>
  )
}

function TabButton({
  active,
  onClick,
  children,
}: {
  active: boolean
  onClick: () => void
  children: React.ReactNode
}) {
  return (
    <button
      onClick={onClick}
      className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
        active
          ? 'border-brand text-brand'
          : 'border-transparent text-black/50 hover:text-black/70'
      }`}
    >
      {children}
    </button>
  )
}

// ---- Learnings tab ----------------------------------------------------

function LearningsTab() {
  const [status, setStatus] = useState('')
  const [category, setCategory] = useState('')
  const [q, setQ] = useState('')
  const [applied, setApplied] = useState({ status: '', category: '', q: '' })

  const catsQ = useQuery({
    queryKey: ['learning-categories'],
    queryFn: () => api.get<{ categories: LearningCategory[] }>('/learning/categories'),
  })

  const learnQ = useQuery({
    queryKey: ['learnings', applied],
    queryFn: () => {
      const params = new URLSearchParams()
      params.set('limit', '200')
      if (applied.status) params.set('status', applied.status)
      if (applied.category) params.set('category', applied.category)
      if (applied.q) params.set('q', applied.q)
      return api.get<{ learnings: Learning[] }>(`/learning?${params.toString()}`)
    },
  })

  const rows = learnQ.data?.learnings ?? []
  const cats = catsQ.data?.categories ?? []

  const apply = () => setApplied({ status, category, q })
  const clear = () => {
    setStatus('')
    setCategory('')
    setQ('')
    setApplied({ status: '', category: '', q: '' })
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-end gap-2">
        <label className="flex flex-col gap-1 text-xs text-black/60">
          Status
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-md border border-black/15 bg-white px-2 py-1 text-sm outline-none focus:border-brand"
          >
            <option value="">All</option>
            <option value="raw">Raw</option>
            <option value="processed">Processed</option>
            <option value="promoted">Promoted</option>
            <option value="rejected">Rejected</option>
          </select>
        </label>
        <label className="flex flex-col gap-1 text-xs text-black/60">
          Category
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="rounded-md border border-black/15 bg-white px-2 py-1 text-sm outline-none focus:border-brand"
          >
            <option value="">All</option>
            {cats.map((c) => (
              <option key={c.category} value={c.category}>
                {c.category} ({c.n})
              </option>
            ))}
          </select>
        </label>
        <label className="flex flex-1 flex-col gap-1 text-xs text-black/60">
          Search
          <div className="relative">
            <Search
              size={14}
              className="pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-black/30"
            />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && apply()}
              placeholder="full-text search past errors"
              className="w-full rounded-md border border-black/15 bg-white py-1 pl-7 pr-2 text-sm outline-none focus:border-brand"
            />
          </div>
        </label>
        <button
          onClick={apply}
          className="rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
        >
          Apply
        </button>
        <button
          onClick={() => learnQ.refetch()}
          className="rounded-lg p-1.5 text-black/40 hover:bg-black/5"
          title="Refresh"
        >
          <RefreshCw size={15} />
        </button>
      </div>

      {rows.length === 0 ? (
        <Empty>No learnings match. Capture runs after each session once self-improvement is enabled.</Empty>
      ) : (
        <div className="space-y-2">
          {rows.map((l) => (
            <LearningCard key={l.id} learning={l} />
          ))}
        </div>
      )}
    </div>
  )
}

function LearningCard({ learning: l }: { learning: Learning }) {
  return (
    <div className="rounded-lg border border-black/10 bg-white p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-black/80">{l.title}</span>
            {l.critical && (
              <span className="inline-flex items-center gap-1 rounded bg-red-50 px-1.5 py-0.5 text-[10px] font-medium text-red-600">
                <AlertTriangle size={11} /> critical
              </span>
            )}
            {l.validated && (
              <span className="rounded bg-green-50 px-1.5 py-0.5 text-[10px] font-medium text-green-700">
                validated
              </span>
            )}
          </div>
          <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-xs text-black/50">
            <span>
              <span className="text-black/40">category:</span> {l.category}
            </span>
            <span>
              <span className="text-black/40">recurrence:</span> {l.recurrence_count}
            </span>
            <span>
              <span className="text-black/40">status:</span> {l.status}
            </span>
            {l.promoted_skill && (
              <span>
                <span className="text-black/40">skill:</span> {l.promoted_skill}
              </span>
            )}
          </div>
        </div>
      </div>
      {(l.error_description || l.root_cause || l.fix_applied) && (
        <div className="mt-2 space-y-1 text-xs">
          {l.error_description && <Field label="Error" value={l.error_description} />}
          {l.root_cause && <Field label="Root cause" value={l.root_cause} />}
          {l.fix_applied && <Field label="Fix" value={l.fix_applied} />}
        </div>
      )}
    </div>
  )
}

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span className="font-medium text-black/50">{label}: </span>
      <span className="text-black/70">{value}</span>
    </div>
  )
}

// ---- Drafts tab -------------------------------------------------------

function DraftsTab() {
  const [status, setStatus] = useState('pending')
  const qc = useQueryClient()

  const draftsQ = useQuery({
    queryKey: ['skill-drafts', status],
    queryFn: () =>
      api.get<{ drafts: SkillDraft[] }>(
        `/skill-drafts${status ? `?status=${status}` : ''}`,
      ),
  })

  const approveM = useMutation({
    mutationFn: (id: number) =>
      api.post<{ result: { skill_name: string; path: string } }>(
        `/skill-drafts/${id}/approve`,
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skill-drafts'] })
      qc.invalidateQueries({ queryKey: ['learning-stats'] })
      qc.invalidateQueries({ queryKey: ['learnings'] })
    },
  })

  const rejectM = useMutation({
    mutationFn: ({ id, note }: { id: number; note: string }) =>
      api.post(`/skill-drafts/${id}/reject`, { note }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['skill-drafts'] })
      qc.invalidateQueries({ queryKey: ['learning-stats'] })
    },
  })

  const drafts = draftsQ.data?.drafts ?? []

  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2">
        <label className="flex flex-col gap-1 text-xs text-black/60">
          Status
          <select
            value={status}
            onChange={(e) => setStatus(e.target.value)}
            className="rounded-md border border-black/15 bg-white px-2 py-1 text-sm outline-none focus:border-brand"
          >
            <option value="pending">Pending</option>
            <option value="approved">Approved</option>
            <option value="rejected">Rejected</option>
            <option value="">All</option>
          </select>
        </label>
        <button
          onClick={() => draftsQ.refetch()}
          className="ml-auto rounded-lg p-1.5 text-black/40 hover:bg-black/5"
          title="Refresh"
        >
          <RefreshCw size={15} />
        </button>
      </div>

      {approveM.isError && (
        <Banner kind="error">{(approveM.error as Error).message}</Banner>
      )}

      {drafts.length === 0 ? (
        <Empty>No {status || ''} drafts. Run a consolidation pass to create some.</Empty>
      ) : (
        <div className="space-y-2">
          {drafts.map((d) => (
            <DraftCard
              key={d.id}
              draft={d}
              busy={approveM.isPending || rejectM.isPending}
              onApprove={() => approveM.mutate(d.id)}
              onReject={() =>
                rejectM.mutate({
                  id: d.id,
                  note: window.prompt('Reason for rejecting (optional):') ?? '',
                })
              }
            />
          ))}
        </div>
      )}
    </div>
  )
}

function DraftCard({
  draft: d,
  busy,
  onApprove,
  onReject,
}: {
  draft: SkillDraft
  busy: boolean
  onApprove: () => void
  onReject: () => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-lg border border-black/10 bg-white p-3">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <FileText size={15} className="text-black/40" />
            <span className="font-mono text-sm font-medium text-black/80">
              {d.skill_name}
            </span>
            <span className="rounded bg-black/5 px-1.5 py-0.5 text-[10px] text-black/50">
              {d.status}
            </span>
          </div>
          <p className="mt-1 text-sm text-black/60">{d.description}</p>
          <div className="mt-1 text-xs text-black/40">
            {d.source_learning_ids?.length ?? 0} source learnings
          </div>
        </div>
        {d.status === 'pending' && (
          <div className="flex shrink-0 gap-2">
            <button
              onClick={onApprove}
              disabled={busy}
              className="inline-flex items-center gap-1 rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              <Check size={14} /> Approve
            </button>
            <button
              onClick={onReject}
              disabled={busy}
              className="inline-flex items-center gap-1 rounded-lg border border-black/15 bg-white px-3 py-1.5 text-xs font-medium text-black/60 hover:bg-black/5 disabled:opacity-50"
            >
              <X size={14} /> Reject
            </button>
          </div>
        )}
      </div>
      <button
        onClick={() => setOpen((o) => !o)}
        className="mt-2 text-xs font-medium text-brand hover:underline"
      >
        {open ? 'Hide body' : 'Show body'}
      </button>
      {open && (
        <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded-md bg-black/[0.03] p-3 font-mono text-[12px] text-black/70">
          {d.body}
        </pre>
      )}
    </div>
  )
}

// ---- History tab ------------------------------------------------------

function HistoryTab() {
  const qc = useQueryClient()
  const histQ = useQuery({
    queryKey: ['consolidations'],
    queryFn: () =>
      api.get<{ consolidations: ConsolidationRecord[] }>('/learning/consolidations'),
  })

  const runM = useMutation({
    mutationFn: () => api.post('/autonomous/consolidation/run'),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['consolidations'] })
      qc.invalidateQueries({ queryKey: ['learning-stats'] })
      qc.invalidateQueries({ queryKey: ['skill-drafts'] })
      qc.invalidateQueries({ queryKey: ['learnings'] })
    },
  })

  const rows = histQ.data?.consolidations ?? []

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-sm text-black/50">Each consolidation pass distills recurring learnings into skill drafts.</p>
        <button
          onClick={() => runM.mutate()}
          disabled={runM.isPending}
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          <RefreshCw size={14} className={runM.isPending ? 'animate-spin' : ''} />
          {runM.isPending ? 'Running…' : 'Run consolidation now'}
        </button>
      </div>
      {runM.isError && <Banner kind="error">{(runM.error as Error).message}</Banner>}

      {rows.length === 0 ? (
        <Empty>No consolidation passes yet.</Empty>
      ) : (
        <div className="space-y-2">
          {rows.map((r) => (
            <details
              key={r.id}
              className="rounded-lg border border-black/10 bg-white p-3"
            >
              <summary className="cursor-pointer text-sm">
                <span className="font-medium text-black/80">{r.summary}</span>
                <span className="ml-2 text-xs text-black/40">
                  {formatWhen(r.created_at)}
                </span>
              </summary>
              <pre className="mt-2 max-h-96 overflow-auto whitespace-pre-wrap rounded-md bg-black/[0.03] p-3 font-mono text-[12px] text-black/70">
                {r.report}
              </pre>
            </details>
          ))}
        </div>
      )}
    </div>
  )
}

function Empty({ children }: { children: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-black/10 bg-white p-6 text-center text-sm text-black/40">
      {children}
    </div>
  )
}

function Banner({ kind, children }: { kind: 'error' | 'info'; children: React.ReactNode }) {
  const cls =
    kind === 'error'
      ? 'bg-red-50 text-red-700 border-red-200'
      : 'bg-blue-50 text-blue-700 border-blue-200'
  return (
    <div className={`rounded-lg border px-3 py-2 text-sm ${cls}`}>{children}</div>
  )
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
