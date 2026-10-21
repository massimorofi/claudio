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
import CodeEditor from '../../components/CodeEditor'
import type {
  Task,
  TaskCreate,
  TaskRun,
  TaskReport,
  TaskType,
  AutonomousStatus,
  AppConfig,
  HealthCheckCfg,
  MaintenanceCfg,
} from '../../api/types'

const VERDICT_BADGE: Record<string, string> = {
  ok: 'bg-green-100 text-green-700 border-green-200',
  warning: 'bg-amber-100 text-amber-700 border-red-200',
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

  const statusQ = useQuery({
    queryKey: ['autonomous-status'],
    queryFn: () => api.get<AutonomousStatus>('/autonomous/status'),
    refetchInterval: 15000,
  })
  const status = statusQ.data

  // Full config, so the built-in checks can be edited (their schedule and
  // script live in the autonomous config blocks, not the tasks table).
  const configQ = useQuery({
    queryKey: ['config'],
    queryFn: () => api.get<{ config: AppConfig }>('/config'),
  })
  const config = configQ.data?.config

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

      <div className="space-y-2">
        <div className="text-xs font-semibold uppercase tracking-wide text-black/40">
          Built-in scheduled checks
        </div>
        <BuiltInRow
          section="health_check"
          name="Health check"
          scheduleLabel={`every ${status?.interval_minutes ?? 10} min`}
          nextRun={status?.next_run_at}
          enabled={status?.health_check_enabled ?? false}
          cfg={config?.autonomous?.health_check}
          runEndpoint="/autonomous/health/run"
        />
        <BuiltInRow
          section="maintenance"
          name="Maintenance"
          scheduleLabel={status?.maintenance_schedule ?? '0 3 * * *'}
          nextRun={status?.maintenance_next_run}
          enabled={status?.maintenance_enabled ?? false}
          cfg={config?.autonomous?.maintenance}
          runEndpoint="/autonomous/maintenance/run"
        />
      </div>

      <div className="space-y-2">
        <div className="text-xs font-semibold uppercase tracking-wide text-black/40">
          Your tasks
        </div>
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
                onToggle={(enabled) =>
                  toggleTask(queryClient, task.key, enabled)
                }
                onEdit={() => setEditing(task)}
                onDelete={() => {
                  if (confirm(`Delete task "${task.name}"?`)) deleteTask(queryClient, task.key)
                }}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

// ---- Built-in check row (view + inline edit) ----

function BuiltInRow({
  section,
  name,
  scheduleLabel,
  nextRun,
  enabled,
  cfg,
  runEndpoint,
}: {
  section: 'health_check' | 'maintenance'
  name: string
  scheduleLabel: string
  nextRun: string | null | undefined
  enabled: boolean
  cfg: HealthCheckCfg | MaintenanceCfg | undefined
  runEndpoint: string
}) {
  const [editing, setEditing] = useState(false)
  const [runResult, setRunResult] = useState<TaskReport | null>(null)

  const runM = useMutation({
    mutationFn: () => api.post<{ report: TaskReport }>(runEndpoint),
    onSuccess: (data) => setRunResult(data.report),
  })

  if (editing) {
    return (
      <BuiltInEditForm
        section={section}
        name={name}
        cfg={cfg}
        onClose={() => setEditing(false)}
      />
    )
  }

  const hasScript = Boolean((cfg?.script ?? '').trim())

  return (
    <div className="rounded-lg border border-black/10 bg-white p-3">
      <div className="flex items-center gap-2">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-black/80">{name}</span>
            <span className="rounded-full border border-black/10 bg-black/5 px-2 py-0.5 text-[10px] font-semibold text-black/50">
              built-in
            </span>
            {hasScript && (
              <span className="rounded-full border border-purple-200 bg-purple-100 px-2 py-0.5 text-[10px] font-semibold text-purple-700">
                script
              </span>
            )}
          </div>
          <div className="mt-0.5 flex flex-wrap gap-x-3 text-xs text-black/50">
            <span>
              <code className="font-mono">{scheduleLabel}</code>
            </span>
            {nextRun && <span>next: {formatWhen(nextRun)}</span>}
            <span>{hasScript ? 'custom script' : 'built-in logic'}</span>
          </div>
        </div>
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
          onClick={() => setEditing(true)}
          className="rounded-lg p-1.5 text-black/40 hover:bg-black/5"
          title="Edit"
        >
          <Pencil size={14} />
        </button>
      </div>
      {runResult && (
        <div className={`mt-2 rounded-lg border p-2 text-sm ${badge(runResult.verdict)}`}>
          <span className="font-semibold">{runResult.verdict}</span> — {runResult.summary}
        </div>
      )}
    </div>
  )
}

// ---- Built-in edit form ----
//
// The built-in checks are now operator-editable shell scripts stored in the
// ``autonomous.health_check`` / ``autonomous.maintenance`` config blocks.
// PUT /config is a deep merge-patch and reloads the running supervisor, so an
// edit here takes effect immediately. When a script is present it is the source
// of truth and runs in place of the built-in Python logic; an empty script
// falls back to the built-in behavior.
//
// Exit-code convention (Nagios-style): 0 = ok, 1 = warning, 2 = critical.

const HEALTH_SCRIPT_DEFAULT = `#!/bin/bash
# Health check — exit 0 = ok, 1 = warning, 2 = critical.
# stdout is recorded as the check summary and detail.

# Disk usage on /
disk=$(df -P / | awk 'NR==2 {gsub("%","",$5); print $5}')
if [ "$disk" -ge 95 ]; then echo "disk critical: \${disk}% used"; exit 2; fi
if [ "$disk" -ge 85 ]; then echo "disk warning: \${disk}% used"; exit 1; fi

# Memory
mem=$(free | awk '/Mem:/ {printf "%.0f", $3/$2*100}')
if [ "$mem" -ge 95 ]; then echo "memory critical: \${mem}% used"; exit 2; fi

# Load average vs core count
cores=$(nproc)
load=$(awk '{print $1}' /proc/loadavg)
if awk -v l="$load" -v c="$cores" 'BEGIN{exit !(l > c*2)}'; then
  echo "load critical: \${load} on \${cores} cores"; exit 2
fi

echo "ok: disk \${disk}%, mem \${mem}%, load \${load}"
exit 0`

const MAINT_SCRIPT_DEFAULT = `#!/bin/bash
# Maintenance — exit 0 = ok, 1 = warning, 2 = critical.
# This script mutates the host; it is skipped while report_only is on.

sudo -n apt-get update || echo "apt-get update failed"
sudo -n journalctl --vacuum-size=200M || echo "journal vacuum failed"
find /tmp -type f -mtime +7 -delete 2>/dev/null || true

echo "maintenance complete"
exit 0`

function BuiltInEditForm({
  section,
  name,
  cfg,
  onClose,
}: {
  section: 'health_check' | 'maintenance'
  name: string
  cfg: HealthCheckCfg | MaintenanceCfg | undefined
  onClose: () => void
}) {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const isHealth = section === 'health_check'
  const [enabled, setEnabled] = useState<boolean>(cfg?.enabled ?? false)
  const [schedule, setSchedule] = useState<string>(
    isHealth
      ? String((cfg as HealthCheckCfg | undefined)?.interval_minutes ?? 10)
      : (cfg as MaintenanceCfg | undefined)?.schedule ?? '0 3 * * *',
  )
  const [script, setScript] = useState<string>(
    (cfg?.script ?? '').trim()
      ? (cfg?.script ?? '')
      : isHealth
        ? HEALTH_SCRIPT_DEFAULT
        : MAINT_SCRIPT_DEFAULT,
  )

  const saveM = useMutation({
    mutationFn: (patch: Record<string, unknown>) =>
      api.put<{ config: AppConfig }>('/config', { autonomous: { [section]: patch } }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['config'] })
      queryClient.invalidateQueries({ queryKey: ['autonomous-status'] })
      onClose()
    },
    onError: (e) => setError((e as Error)?.message ?? 'Could not save.'),
  })

  const submit = () => {
    setError(null)
    if (!script.trim()) {
      setError('A script is required.')
      return
    }
    if (isHealth) {
      const mins = Number(schedule)
      if (!Number.isFinite(mins) || mins <= 0) {
        setError('Interval must be greater than 0 minutes.')
        return
      }
      saveM.mutate({ enabled, interval_minutes: mins, script })
    } else {
      if (!schedule.trim()) {
        setError('A cron schedule is required.')
        return
      }
      saveM.mutate({ enabled, schedule: schedule.trim(), script })
    }
  }

  return (
    <div className="rounded-lg border border-brand/30 bg-white p-4">
      <div className="mb-3 flex items-center justify-between">
        <div className="text-sm font-semibold text-black/70">Edit {name}</div>
        <button onClick={onClose} className="rounded p-1 text-black/40 hover:bg-black/5">
          <X size={15} />
        </button>
      </div>
      <div className="space-y-3">
        <label className="flex items-center gap-1.5 text-xs text-black/60">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
          Enabled
        </label>
        <Field label={isHealth ? 'Interval (minutes)' : 'Cron schedule (UTC)'}>
          <input
            value={schedule}
            onChange={(e) => setSchedule(e.target.value)}
            placeholder={isHealth ? '10' : '0 3 * * *'}
            className="w-full rounded-md border border-black/15 bg-white px-2.5 py-1.5 font-mono text-sm outline-none focus:border-brand"
          />
        </Field>
        <Field label="Script">
          <CodeEditor
            value={script}
            onChange={setScript}
            rows={14}
            placeholder="#!/bin/bash"
          />
        </Field>
        {error && (
          <div className="rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-700">
            {error}
          </div>
        )}
        <div className="flex items-center gap-2">
          <button
            onClick={submit}
            disabled={saveM.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          >
            {saveM.isPending ? <Loader2 size={13} className="animate-spin" /> : null}
            Save changes
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

// ---- User task helpers ----

function toggleTask(queryClient: ReturnType<typeof useQueryClient>, key: string, enabled: boolean) {
  api.post<{ task: Task }>(`/autonomous/tasks/${key}/${enabled ? 'enable' : 'disable'}`).then(() =>
    queryClient.invalidateQueries({ queryKey: ['tasks'] }),
  )
}

function deleteTask(queryClient: ReturnType<typeof useQueryClient>, key: string) {
  api.delete<{ deleted: string }>(`/autonomous/tasks/${key}`).then(() =>
    queryClient.invalidateQueries({ queryKey: ['tasks'] }),
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
            {task.type === 'script' && (
              <span className="rounded-full border border-purple-200 bg-purple-100 px-2 py-0.5 text-[10px] font-semibold text-purple-700">
                script
              </span>
            )}
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
  const [type, setType] = useState<TaskType>(initial?.type ?? 'prompt')
  const [prompt, setPrompt] = useState(initial?.prompt ?? '')
  const [script, setScript] = useState(initial?.script ?? '')
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
    if (type === 'script' && !script.trim()) {
      setError('A shell script is required for a script task.')
      return
    }
    if (type === 'prompt' && !prompt.trim()) {
      setError('A prompt is required for a prompt task.')
      return
    }
    const payload = {
      name,
      type,
      prompt: type === 'prompt' ? prompt : '',
      script: type === 'script' ? script : null,
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
        <Field label="Task type">
          <div className="flex gap-4">
            <label className="flex items-center gap-1.5 text-xs text-black/60">
              <input
                type="radio"
                name="task-type"
                checked={type === 'prompt'}
                onChange={() => setType('prompt')}
              />
              Prompt / playbook
            </label>
            <label className="flex items-center gap-1.5 text-xs text-black/60">
              <input
                type="radio"
                name="task-type"
                checked={type === 'script'}
                onChange={() => setType('script')}
              />
              Shell script
            </label>
          </div>
        </Field>
        {type === 'prompt' ? (
          <Field label="Prompt / playbook">
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              rows={4}
              placeholder="What should the agent do on this schedule?"
              className="w-full rounded-md border border-black/15 bg-white px-2.5 py-1.5 text-sm outline-none focus:border-brand"
            />
          </Field>
        ) : (
          <Field label="Shell script">
            <CodeEditor
              value={script}
              onChange={setScript}
              rows={12}
              placeholder={'#!/bin/bash\n# Runs directly in the workspace. Exit 0 = ok, non-zero = critical.'}
            />
          </Field>
        )}
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
