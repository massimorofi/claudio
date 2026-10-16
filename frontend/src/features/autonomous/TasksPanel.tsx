import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Plus,
  Play,
  Loader2,
  RefreshCw,
  Pencil,
  Trash2,
  X,
  ChevronDown,
  ChevronRight,
} from 'lucide-react'
import { api } from '../../api/client'
import type { Task, TaskCreate, TaskRun, TaskReport } from '../../api/types'

const VERDICT_BADGE: Record<string, string> = {
  ok: 'bg-green-100 text-green-700 border-green-200',
  warning: 'bg-amber-100 text-amber-700 border-amber-200',
  critical: 'bg-red-100 text-red-700 border-red-200',
  skipped: 'bg-black/10 text-black/50 border-black/10',
}

const NOTIFY_OPTIONS = ['ok', 'warning', 'critical', 'failure']

function badge(v: string | null | undefined) {
  return VERDICT_BADGE[v ?? ''] ?? VERDICT_BADGE.warning
}

export default function TasksPanel() {
  const queryClient = useQueryClient()
  const [editing, setEditing] = useState<Task | 'new' | null>(null)

  const tasksQ = useQuery({
    queryKey: ['tasks'],
    queryFn: () => api.get<{ tasks: Task[] }>('/autonomous/tasks'),
    refetchInterval: 15000,
  })

  const toggleM = useMutation({
    mutationFn: ({ key, enabled }: { key: string; enabled: boolean }) =>
      api.post<{ task: Task }>(`/autonomous/tasks/${key}/${enabled ? 'enable' : 'disable'}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tasks'] }),
  })

  const deleteM = useMutation({
    mutationFn: (key: string) => api.delete<{ deleted: string }>(`/autonomous/tasks/${key}`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['tasks'] }),
  })

  const tasks = tasksQ.data?.tasks ?? []

  return (
    <div className="max-w-3xl space-y-3">
      <div className="flex items-center gap-2">
        <span className="text-sm font-semibold text-black/70">Scheduled tasks</span>
        <button
          onClick={() => tasksQ.refetch()}
          className="rounded-lg p-1.5 text-black/40 hover:bg-black/5"
          title="Refresh"
        >
          <RefreshCw size={15} />
        </button>
        <button
          onClick={() => setEditing('new')}
          className="ml-auto inline-flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90"
        >
          <Plus size={14} /> New task
        </button>
      </div>

      {editing && (
        <TaskForm
          initial={editing === 'new' ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null)
            queryClient.invalidateQueries({ queryKey: ['tasks'] })
          }}
        />
      )}

      {tasks.length === 0 ? (
        <div className="rounded-lg border border-black/10 bg-white p-6 text-center text-sm text-black/40">
          No tasks yet. Create one to schedule a recurring playbook.
        </div>
      ) : (
        <div className="space-y-2">
          {tasks.map((task) => (
            <TaskRow
              key={task.key}
              task={task}
              onToggle={(enabled) => toggleM.mutate({ key: task.key, enabled })}
              onEdit={() => setEditing(task)}
              onDelete={() => {
                if (confirm(`Delete task "${task.name}"?`)) deleteM.mutate(task.key)
              }}
            />
          ))}
        </div>
      )}
    </div>
  )
}

function TaskRow({
  task,
  onToggle,
  onEdit,
  onDelete,
}: {
  task: Task
  onToggle: (enabled: boolean) => void
  onEdit: () => void
  onDelete: () => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [runResult, setRunResult] = useState<TaskReport | null>(null)

  const runM = useMutation({
    mutationFn: () => api.post<{ report: TaskReport }>(`/autonomous/tasks/${task.key}/run`),
    onSuccess: (data) => setRunResult(data.report),
  })

  return (
    <div className="rounded-lg border border-black/10 bg-white p-3">
      <div className="flex items-center gap-2">
        <button
          onClick={() => setExpanded((e) => !e)}
          className="rounded p-0.5 text-black/40 hover:bg-black/5"
          title={expanded ? 'Collapse' : 'Run history'}
        >
          {expanded ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
        </button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold text-black/80">{task.name}</span>
            <code className="rounded bg-black/5 px-1.5 py-0.5 font-mono text-[11px] text-black/50">
              {task.key}
            </code>
            {task.last_verdict && (
              <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${badge(task.last_verdict)}`}>
                {task.last_verdict}
              </span>
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-black/50">
            <span>
              <code className="font-mono">{task.schedule}</code>
            </span>
            <span>{task.report_only ? 'report only' : 'can act'}</span>
            {task.next_run && <span>next: {formatWhen(task.next_run)}</span>}
          </div>
        </div>
        <label className="flex items-center gap-1.5 text-xs text-black/60">
          <input
            type="checkbox"
            checked={task.enabled}
            onChange={(e) => onToggle(e.target.checked)}
          />
          enabled
        </label>
        <button
          onClick={() => runM.mutate()}
          disabled={runM.isPending}
          className="inline-flex items-center gap-1 rounded-lg bg-brand px-2.5 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          title="Run now"
        >
          {runM.isPending ? <Loader2 size={12} className="animate-spin" /> : <Play size={12} />}
          Run
        </button>
        <button
          onClick={onEdit}
          className="rounded-lg p-1.5 text-black/40 hover:bg-black/5"
          title="Edit"
        >
          <Pencil size={14} />
        </button>
        <button
          onClick={onDelete}
          className="rounded-lg p-1.5 text-red-400 hover:bg-red-50"
          title="Delete"
        >
          <Trash2 size={14} />
        </button>
      </div>

      {runResult && (
        <div
          className={`mt-2 rounded-lg border p-2 text-sm ${badge(runResult.verdict)}`}
        >
          <span className="font-semibold">{runResult.verdict}</span> — {runResult.summary}
        </div>
      )}

      {expanded && <RunHistory taskKey={task.key} />}
    </div>
  )
}

function RunHistory({ taskKey }: { taskKey: string }) {
  const runsQ = useQuery({
    queryKey: ['task-runs', taskKey],
    queryFn: () => api.get<{ runs: TaskRun[] }>(`/autonomous/tasks/${taskKey}/runs?limit=20`),
  })
  const runs = runsQ.data?.runs ?? []
  if (runs.length === 0) {
    return <div className="mt-2 text-xs text-black/40">No runs recorded yet.</div>
  }
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="border-b border-black/10 text-black/50">
          <tr>
            <th className="px-2 py-1.5">Started</th>
            <th className="px-2 py-1.5">Verdict</th>
            <th className="px-2 py-1.5">Trigger</th>
            <th className="px-2 py-1.5">Summary</th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className="border-b border-black/5">
              <td className="whitespace-nowrap px-2 py-1.5">{formatWhen(r.started_at)}</td>
              <td className="px-2 py-1.5">
                <span className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${badge(r.verdict)}`}>
                  {r.verdict ?? '—'}
                </span>
              </td>
              <td className="px-2 py-1.5 text-black/50">{r.trigger}</td>
              <td className="px-2 py-1.5 text-black/70">{r.summary ?? ''}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function TaskForm({
  initial,
  onClose,
  onSaved,
}: {
  initial: Task | null
  onClose: () => void
  onSaved: () => void
}) {
  const isEdit = initial !== null
  const [key, setKey] = useState(initial?.key ?? '')
  const [name, setName] = useState(initial?.name ?? '')
  const [prompt, setPrompt] = useState(initial?.prompt ?? '')
  const [schedule, setSchedule] = useState(initial?.schedule ?? '0 3 * * *')
  const [reportOnly, setReportOnly] = useState(initial?.report_only ?? true)
  const [notifyOn, setNotifyOn] = useState<string[]>(
    initial?.notify_on ?? ['warning', 'critical', 'failure'],
  )
  const [enabled, setEnabled] = useState(initial?.enabled ?? true)
  const [error, setError] = useState<string | null>(null)

  const createM = useMutation({
    mutationFn: (payload: TaskCreate) => api.post<{ task: Task }>('/autonomous/tasks', payload),
    onSuccess: onSaved,
    onError: (e) => setError((e as Error)?.message ?? 'Could not create task.'),
  })

  const updateM = useMutation({
    mutationFn: (payload: Partial<TaskCreate>) =>
      api.put<{ task: Task }>(`/autonomous/tasks/${initial!.key}`, payload),
    onSuccess: onSaved,
    onError: (e) => setError((e as Error)?.message ?? 'Could not update task.'),
  })

  const busy = createM.isPending || updateM.isPending

  const toggleNotify = (v: string) => {
    setNotifyOn((cur) => (cur.includes(v) ? cur.filter((x) => x !== v) : [...cur, v]))
  }

  const submit = () => {
    setError(null)
    if (!isEdit && !key) {
      setError('A key is required.')
      return
    }
    const payload = {
      name,
      prompt,
      schedule,
      notify_on: notifyOn,
      report_only: reportOnly,
      enabled,
    }
    if (isEdit) {
      updateM.mutate(payload)
    } else {
      createM.mutate({ ...payload, key })
    }
  }

  return (
    <div className="rounded-lg border border-brand/30 bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-sm font-semibold text-black/70">
          {isEdit ? 'Edit task' : 'New task'}
        </div>
        <button onClick={onClose} className="rounded p-1 text-black/40 hover:bg-black/5">
          <X size={15} />
        </button>
      </div>
      <div className="space-y-3">
        {!isEdit && (
          <Field label="Key (lowercase, hyphens)">
            <input
              value={key}
              onChange={(e) => setKey(e.target.value)}
              placeholder="nightly-cleanup"
              className="w-full rounded-md border border-black/15 bg-white px-2.5 py-1.5 text-sm outline-none focus:border-brand"
            />
          </Field>
        )}
        <Field label="Name">
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full rounded-md border border-black/15 bg-white px-2.5 py-1.5 text-sm outline-none focus:border-brand"
          />
        </Field>
        <Field label="Cron schedule (UTC)">
          <input
            value={schedule}
            onChange={(e) => setSchedule(e.target.value)}
            placeholder="0 3 * * *"
            className="w-full rounded-md border border-black/15 bg-white px-2.5 py-1.5 font-mono text-sm outline-none focus:border-brand"
          />
        </Field>
        <Field label="Prompt / playbook">
          <textarea
            value={prompt}
            onChange={(e) => setPrompt(e.target.value)}
            rows={4}
            placeholder="What should the agent do on this schedule?"
            className="w-full rounded-md border border-black/15 bg-white px-2.5 py-1.5 text-sm outline-none focus:border-brand"
          />
        </Field>
        <div className="flex flex-wrap items-center gap-4">
          <label className="flex items-center gap-1.5 text-xs text-black/60">
            <input
              type="checkbox"
              checked={reportOnly}
              onChange={(e) => setReportOnly(e.target.checked)}
            />
            Report only (no mutations)
          </label>
          <label className="flex items-center gap-1.5 text-xs text-black/60">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => setEnabled(e.target.checked)}
            />
            Enabled
          </label>
        </div>
        <Field label="Notify on">
          <div className="flex flex-wrap gap-3">
            {NOTIFY_OPTIONS.map((opt) => (
              <label key={opt} className="flex items-center gap-1.5 text-xs text-black/60">
                <input
                  type="checkbox"
                  checked={notifyOn.includes(opt)}
                  onChange={() => toggleNotify(opt)}
                />
                {opt}
              </label>
            ))}
          </div>
        </Field>
        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-700">
            {error}
          </div>
        )}
        <div className="flex items-center gap-2">
          <button
            onClick={submit}
            disabled={busy}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : null}
            {isEdit ? 'Save changes' : 'Create task'}
          </button>
          <button
            onClick={onClose}
            className="rounded-lg border border-black/15 bg-white px-3 py-1.5 text-xs font-medium text-black/60 hover:bg-black/5"
          >
            Cancel
          </button>
        </div>
      </div>
    </div>
  )
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <label className="mb-1 block text-xs font-medium text-black/60">{label}</label>
      {children}
    </div>
  )
}

function formatWhen(iso: string | null | undefined): string {
  if (!iso) return '—'
  try {
    const d = new Date(iso)
    if (isNaN(d.getTime())) return iso
    return d.toLocaleString()
  } catch {
    return iso
  }
}
