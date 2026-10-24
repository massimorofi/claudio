import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Shield,
  ShieldAlert,
  ShieldCheck,
  RefreshCw,
  Stethoscope,
  Loader2,
  Check,
  CircleDot,
  Wrench,
  X,
  AlertTriangle,
  Terminal,
  KeyRound,
} from 'lucide-react'
import { api } from '../../api/client'
import type {
  AutonomousStatus,
  HealthCheckRecord,
  HealthCheckItem,
  HealthVerdict,
  Issue,
  NotifyTestResult,
  FixSuggestion,
  AcceptFixResult,
  FixRisk,
} from '../../api/types'
import TasksPanel from './TasksPanel'
import AuditPanel from './AuditPanel'
import DaemonConfigPanel from './DaemonConfigPanel'

type Tab = 'dashboard' | 'issues' | 'tasks' | 'audit' | 'daemon'

const TAB_LABELS: Record<Tab, string> = {
  dashboard: 'Dashboard',
  issues: 'Issues',
  tasks: 'Tasks',
  audit: 'Audit',
  daemon: 'Daemon',
}

export default function AutonomousView() {
  const [tab, setTab] = useState<Tab>('tasks')

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-black/10 bg-white px-4 py-3">
        <div className="flex gap-1 overflow-x-auto">
          {(['dashboard', 'issues', 'tasks', 'audit', 'daemon'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium ${
                tab === t ? 'bg-brand text-white' : 'text-black/60 hover:bg-black/5'
              }`}
            >
              {TAB_LABELS[t]}
            </button>
          ))}
        </div>
      </div>
      <div className="scroll-thin flex-1 overflow-y-auto p-4">
        {tab === 'dashboard' && <Dashboard />}
        {tab === 'issues' && <Issues />}
        {tab === 'tasks' && <TasksPanel />}
        {tab === 'audit' && <AuditPanel />}
        {tab === 'daemon' && <DaemonConfigPanel />}
      </div>
    </div>
  )
}

// ---- verdict styling ---------------------------------------------------

const VERDICT_STYLE: Record<
  HealthVerdict,
  { text: string; badge: string; label: string }
> = {
  ok: {
    text: 'text-green-600',
    badge: 'bg-green-100 text-green-700 border-green-200',
    label: 'OK',
  },
  warning: {
    text: 'text-amber-600',
    badge: 'bg-amber-100 text-amber-700 border-amber-200',
    label: 'WARNING',
  },
  critical: {
    text: 'text-red-600',
    badge: 'bg-red-100 text-red-700 border-red-200',
    label: 'CRITICAL',
  },
}

const ITEM_DOT: Record<HealthCheckItem['status'], string> = {
  ok: 'bg-green-500',
  warning: 'bg-amber-500',
  critical: 'bg-red-500',
  skipped: 'bg-black/20',
}

function verdictStyle(v: string) {
  return VERDICT_STYLE[(v as HealthVerdict) in VERDICT_STYLE ? (v as HealthVerdict) : 'warning']
}

// ---- Dashboard -------------------------------------------------------

function Dashboard() {
  const queryClient = useQueryClient()
  const statusQ = useQuery({
    queryKey: ['autonomous-status'],
    queryFn: () => api.get<AutonomousStatus>('/autonomous/status'),
    refetchInterval: 10000,
  })
  const historyQ = useQuery({
    queryKey: ['autonomous-history'],
    queryFn: () => api.get<{ checks: HealthCheckRecord[] }>('/autonomous/health/history?limit=20'),
    refetchInterval: 10000,
  })

  // Run a fresh diagnostic on demand. This is the "did my fix actually work?"
  // action: it re-runs every check now and records a brand-new result, rather
  // than just re-reading the last stored one.
  const runQ = useMutation({
    mutationFn: () =>
      api.post<{
        id: number
        verdict: HealthVerdict
        summary: string
        duration_ms: number
        checks: HealthCheckItem[]
        new_issue: number | null
      }>('/autonomous/health/run'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['autonomous-status'] })
      queryClient.invalidateQueries({ queryKey: ['autonomous-history'] })
      queryClient.invalidateQueries({ queryKey: ['autonomous-issues'] })
    },
  })

  const status = statusQ.data
  const last = status?.last_check ?? null
  const vs = verdictStyle(last?.verdict ?? 'ok')
  const ran = runQ.data

  return (
    <div className="max-w-3xl space-y-4">
      <div className="flex items-center gap-2">
        {last?.verdict === 'ok' ? (
          <ShieldCheck size={18} className={vs.text} />
        ) : (
          <ShieldAlert size={18} className={vs.text} />
        )}
        <span className={`text-sm font-semibold ${vs.text}`}>
          {last ? vs.label : 'No checks yet'}
        </span>
        {last && (
          <span className="text-xs text-black/40">· last run {formatWhen(last.created_at)}</span>
        )}
        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => runQ.mutate()}
            disabled={runQ.isPending}
            className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
            title="Run all health checks now"
          >
            {runQ.isPending ? (
              <Loader2 size={14} className="animate-spin" />
            ) : (
              <Stethoscope size={14} />
            )}
            {runQ.isPending ? 'Running…' : 'Run check now'}
          </button>
          <button
            onClick={() => {
              statusQ.refetch()
              historyQ.refetch()
            }}
            className="rounded-lg p-1.5 text-black/40 hover:bg-black/5"
            title="Refresh view"
          >
            <RefreshCw size={15} />
          </button>
        </div>
      </div>

      {runQ.isError && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          Could not run the check: {(runQ.error as Error)?.message ?? 'unknown error'}
        </div>
      )}
      {ran && !runQ.isPending && (
        <div
          className={`rounded-lg border p-3 text-sm ${verdictStyle(ran.verdict).badge}`}
        >
          <span className="font-semibold">{verdictStyle(ran.verdict).label}</span>
          {' — '}
          {ran.summary}
          {ran.new_issue != null && (
            <span className="ml-1 text-xs opacity-80">
              (new issue #{ran.new_issue} raised)
            </span>
          )}
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat
          label="Daemon"
          value={status?.enabled ? 'Enabled' : 'Disabled'}
          tone={status?.enabled ? 'good' : 'muted'}
        />
        <Stat
          label="Mode"
          value={status?.report_only ? 'Report only' : 'Action'}
          tone={status?.report_only ? 'muted' : 'warn'}
        />
        <Stat
          label="Open issues"
          value={status?.open_issues_count ?? 0}
          tone={(status?.open_issues_count ?? 0) > 0 ? 'bad' : 'good'}
        />
        <Stat
          label="Interval"
          value={status ? `${status.interval_minutes} min` : '—'}
        />
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Stat
          label="Notifications"
          value={
            status && status.notification_enabled
              ? status.notification_channels.join(', ') || 'none configured'
              : 'Off'
          }
          tone={status?.notification_enabled ? 'good' : 'muted'}
        />
        <Stat
          label="Scheduler"
          value={status?.scheduler_running ? 'Running' : 'Stopped'}
          tone={status?.scheduler_running ? 'good' : 'muted'}
        />
      </div>

      {last && (
        <div className="rounded-lg border border-black/10 bg-white p-3">
          <div className="mb-2 flex items-center justify-between">
            <div className="text-xs font-semibold text-black/60">Latest check</div>
            <div className="text-xs text-black/40">{formatWhen(last.created_at)}</div>
          </div>
          <div className="mb-2 text-sm text-black/70">{last.summary}</div>
          <div className="space-y-1">
            {(last.details ?? []).map((c) => (
              <div key={c.name} className="flex items-center gap-2 text-sm">
                <span
                  className={`inline-block h-2 w-2 rounded-full ${ITEM_DOT[c.status]}`}
                />
                <span className="capitalize">{c.name}</span>
                <span className="text-xs text-black/40">— {c.message}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="rounded-lg border border-black/10 bg-white p-3">
        <div className="mb-2 text-xs font-semibold text-black/60">Recent checks</div>
        <RecentChecks checks={historyQ.data?.checks ?? []} />
      </div>
    </div>
  )
}

function RecentChecks({ checks }: { checks: HealthCheckRecord[] }) {
  if (checks.length === 0) {
    return <div className="text-sm text-black/40">No checks recorded yet.</div>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-left text-xs">
        <thead className="border-b border-black/10 text-black/50">
          <tr>
            <th className="px-2 py-2">When</th>
            <th className="px-2 py-2">Verdict</th>
            <th className="px-2 py-2">Summary</th>
            <th className="px-2 py-2">Source</th>
          </tr>
        </thead>
        <tbody>
          {checks.map((c) => {
            const vs = verdictStyle(c.verdict)
            return (
              <tr key={c.id} className="border-b border-black/5">
                <td className="whitespace-nowrap px-2 py-2">{formatWhen(c.created_at)}</td>
                <td className="px-2 py-2">
                  <span
                    className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${vs.badge}`}
                  >
                    {vs.label}
                  </span>
                </td>
                <td className="px-2 py-2 text-black/70">{c.summary}</td>
                <td className="px-2 py-2 text-black/50">{c.source}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}

// ---- Issues ----------------------------------------------------------

const RISK_STYLE: Record<FixRisk, { badge: string; label: string }> = {
  low: { badge: 'bg-green-100 text-green-700 border-green-200', label: 'low risk' },
  medium: { badge: 'bg-amber-100 text-amber-700 border-amber-200', label: 'medium risk' },
  high: { badge: 'bg-red-100 text-red-700 border-red-200', label: 'high risk' },
}

function Issues() {
  const [filter, setFilter] = useState<'open' | 'acknowledged' | 'resolved' | ''>('open')
  const queryClient = useQueryClient()

  const issuesQ = useQuery({
    queryKey: ['autonomous-issues', filter],
    queryFn: () =>
      api.get<{ issues: Issue[] }>(
        `/autonomous/issues${filter ? `?status=${filter}` : ''}`,
      ),
    refetchInterval: 10000,
  })

  // Re-run the diagnostics from here too, so after fixing something you can
  // immediately see whether the problem is actually gone.
  const runQ = useMutation({
    mutationFn: () =>
      api.post<{
        id: number
        verdict: HealthVerdict
        summary: string
        duration_ms: number
        checks: HealthCheckItem[]
        new_issue: number | null
      }>('/autonomous/health/run'),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['autonomous-issues'] })
      queryClient.invalidateQueries({ queryKey: ['autonomous-status'] })
      queryClient.invalidateQueries({ queryKey: ['autonomous-history'] })
    },
  })

  const issues = issuesQ.data?.issues ?? []

  return (
    <div className="max-w-3xl space-y-3">
      <div className="flex items-center gap-2">
        <select
          value={filter}
          onChange={(e) => setFilter(e.target.value as typeof filter)}
          className="rounded-md border border-black/15 bg-white px-2 py-1 text-xs"
        >
          <option value="open">Open</option>
          <option value="acknowledged">Acknowledged</option>
          <option value="resolved">Resolved</option>
          <option value="">All</option>
        </select>
        <button
          onClick={() => issuesQ.refetch()}
          className="rounded-lg p-1.5 text-black/40 hover:bg-black/5"
          title="Refresh"
        >
          <RefreshCw size={15} />
        </button>
        <button
          onClick={() => runQ.mutate()}
          disabled={runQ.isPending}
          className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
          title="Run all health checks now"
        >
          {runQ.isPending ? (
            <Loader2 size={13} className="animate-spin" />
          ) : (
            <Stethoscope size={13} />
          )}
          {runQ.isPending ? 'Running…' : 'Run check now'}
        </button>
        <span className="ml-auto text-xs text-black/40">
          {issues.length} issue{issues.length === 1 ? '' : 's'}
        </span>
      </div>

      {runQ.isError && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          Could not run the check: {(runQ.error as Error)?.message ?? 'unknown error'}
        </div>
      )}
      {runQ.data && !runQ.isPending && (
        <div
          className={`rounded-lg border p-3 text-sm ${verdictStyle(runQ.data.verdict).badge}`}
        >
          <span className="font-semibold">{verdictStyle(runQ.data.verdict).label}</span>
          {' — '}
          {runQ.data.summary}
          {runQ.data.new_issue != null && (
            <span className="ml-1 text-xs opacity-80">
              (new issue #{runQ.data.new_issue} raised — the problem is still present)
            </span>
          )}
        </div>
      )}

      {issues.length === 0 ? (
        <div className="rounded-lg border border-black/10 bg-white p-6 text-center text-sm text-black/40">
          No issues here.
        </div>
      ) : (
        <div className="space-y-2">
          {issues.map((issue) => (
            <IssueCard key={issue.id} issue={issue} />
          ))}
        </div>
      )}
    </div>
  )
}

function IssueCard({ issue }: { issue: Issue }) {
  const queryClient = useQueryClient()
  const [proposal, setProposal] = useState<FixSuggestion | null>(null)
  const [acceptResult, setAcceptResult] = useState<AcceptFixResult | null>(null)
  const [password, setPassword] = useState('')
  const vs = verdictStyle(issue.severity)
  const isResolved = issue.status === 'resolved'
  // The backend only feeds the password to commands that start with sudo, so we
  // only ask for it in that case.
  const needsSudo = /^\s*sudo(\s|$)/.test(proposal?.command ?? '')

  const proposeM = useMutation({
    mutationFn: () =>
      api.post<{ suggestion: FixSuggestion }>(
        `/autonomous/issues/${issue.id}/propose-fix`,
      ),
    onSuccess: (data) => {
      setPassword('')
      setProposal(data.suggestion)
    },
  })

  const acceptM = useMutation({
    mutationFn: (pwd: string) =>
      api.post<AcceptFixResult>(`/autonomous/suggestions/${proposal!.id}/accept`, {
        password: pwd,
      }),
    onSuccess: (data) => {
      setAcceptResult(data)
      setPassword('')
      queryClient.invalidateQueries({ queryKey: ['autonomous-issues'] })
      queryClient.invalidateQueries({ queryKey: ['autonomous-status'] })
      queryClient.invalidateQueries({ queryKey: ['autonomous-history'] })
      // If this fix didn't clear the issue and the agent proposed a different
      // one, present it as the next fix to approve. If it gave up, drop the
      // proposal so the operator isn't offered the same failed command again.
      setProposal(data.issue_resolved ? null : data.next_suggestion)
    },
  })

  const dismissM = useMutation({
    mutationFn: () =>
      api.post<{ suggestion: FixSuggestion }>(
        `/autonomous/suggestions/${proposal!.id}/dismiss`,
      ),
    onSuccess: () => {
      setProposal(null)
      setAcceptResult(null)
      queryClient.invalidateQueries({ queryKey: ['autonomous-issues'] })
    },
  })

  const busy = proposeM.isPending || acceptM.isPending || dismissM.isPending

  return (
    <div className="rounded-lg border border-black/10 bg-white p-3">
      <div className="flex items-start gap-2">
        <span
          className={`mt-1 inline-block h-2 w-2 shrink-0 rounded-full ${
            issue.severity === 'critical' ? 'bg-red-500' : 'bg-amber-500'
          }`}
        />
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="text-sm font-semibold text-black/80">{issue.title}</span>
            <span
              className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${vs.badge}`}
            >
              {vs.label}
            </span>
            <span className="ml-auto text-xs text-black/40">{formatWhen(issue.created_at)}</span>
          </div>
          {issue.detail && (
            <div className="mt-1 whitespace-pre-wrap text-xs text-black/60">{issue.detail}</div>
          )}
          {isResolved && issue.resolution && (
            <div className="mt-1 text-xs text-green-700">Resolved: {issue.resolution}</div>
          )}

          {/* ---- Fix flow: propose → review the exact command → accept/dismiss ---- */}
          {!isResolved && !proposal && (
            <div className="mt-2">
              <button
                onClick={() => proposeM.mutate()}
                disabled={busy}
                className="inline-flex items-center gap-1.5 rounded-lg bg-brand px-3 py-1.5 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
              >
                {proposeM.isPending ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Wrench size={13} />
                )}
                {proposeM.isPending ? 'Thinking…' : 'Propose a fix'}
              </button>
              {proposeM.isError && (
                <div className="mt-2 rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-700">
                  {(proposeM.error as Error)?.message ?? 'Could not generate a fix.'}
                </div>
              )}
            </div>
          )}

          {proposal && (
            <div className="mt-2 rounded-lg border border-black/10 bg-black/[0.02] p-3">
              <div className="mb-1 flex items-center gap-2">
                <span className="text-xs font-semibold text-black/60">
                  Proposed command
                </span>
                <span
                  className={`rounded-full border px-2 py-0.5 text-[10px] font-semibold ${
                    RISK_STYLE[proposal.risk]?.badge ?? RISK_STYLE.medium.badge
                  }`}
                >
                  {RISK_STYLE[proposal.risk]?.label ?? proposal.risk}
                </span>
                <span className="text-[10px] text-black/40">
                  {proposal.reversible ? 'reversible' : 'may be irreversible'}
                </span>
              </div>
              <div className="flex items-start gap-2 rounded-md border border-black/10 bg-[#0d1117] p-2">
                <Terminal size={13} className="mt-0.5 shrink-0 text-[#8b949e]" />
                <code className="whitespace-pre-wrap break-all font-mono text-[12px] text-[#e6edf3]">
                  {proposal.command}
                </code>
              </div>
              {proposal.explanation && (
                <p className="mt-2 text-xs text-black/60">{proposal.explanation}</p>
              )}
              {needsSudo && (
                <div className="mt-3">
                  <label className="mb-1 flex items-center gap-1.5 text-xs font-medium text-black/60">
                    <KeyRound size={12} /> sudo password
                  </label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="off"
                    spellCheck={false}
                    placeholder="your login password — used once, never stored"
                    className="w-full rounded-md border border-black/15 bg-white px-2.5 py-1.5 text-sm outline-none focus:border-brand"
                  />
                  <p className="mt-1 text-[10px] text-black/40">
                    Fed to <code className="font-mono">sudo -S</code> via stdin. Not
                    written to the audit log or any file.
                  </p>
                </div>
              )}
              <div className="mt-3 flex items-center gap-2">
                <button
                  onClick={() => acceptM.mutate(password)}
                  disabled={busy || (needsSudo && password.length === 0)}
                  className="inline-flex items-center gap-1.5 rounded-lg bg-green-600 px-3 py-1.5 text-xs font-medium text-white hover:bg-green-700 disabled:opacity-50"
                >
                  {acceptM.isPending ? (
                    <Loader2 size={13} className="animate-spin" />
                  ) : (
                    <Check size={13} />
                  )}
                  {acceptM.isPending ? 'Running…' : 'Accept & run this command'}
                </button>
                <button
                  onClick={() => dismissM.mutate()}
                  disabled={busy}
                  className="inline-flex items-center gap-1 rounded-lg border border-black/15 bg-white px-2.5 py-1.5 text-xs font-medium text-black/60 hover:bg-black/5 disabled:opacity-50"
                >
                  <X size={13} /> Dismiss
                </button>
              </div>
              {acceptM.isError && (
                <div className="mt-2 rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-700">
                  {(acceptM.error as Error)?.message ?? 'Could not run the command.'}
                </div>
              )}
            </div>
          )}

          {acceptResult && (
            <div className="mt-2 space-y-2">
              <div
                className={`rounded-lg border p-2 text-xs ${
                  !acceptResult.executed.ok
                    ? 'border-red-200 bg-red-50 text-red-700'
                    : acceptResult.issue_resolved
                      ? 'border-green-200 bg-green-50 text-green-700'
                      : 'border-amber-200 bg-amber-50 text-amber-700'
                }`}
              >
                <span className="font-semibold">
                  {!acceptResult.executed.ok
                    ? `Command failed${
                        acceptResult.executed.exit_code != null
                          ? ` (exit ${acceptResult.executed.exit_code})`
                          : ''
                      }`
                    : acceptResult.issue_resolved
                      ? 'Command succeeded — issue resolved'
                      : 'Command ran (exit 0) but the issue is still present'}
                </span>
                {!acceptResult.executed.ok && acceptResult.executed.stderr && (
                  <div className="mt-1 whitespace-pre-wrap break-all font-mono text-[11px]">
                    {acceptResult.executed.stderr}
                  </div>
                )}
              </div>
              <div
                className={`rounded-lg border p-2 text-sm ${verdictStyle(
                  acceptResult.recheck.verdict,
                ).badge}`}
              >
                <span className="font-semibold">
                  {verdictStyle(acceptResult.recheck.verdict).label}
                </span>
                {' — '}
                {acceptResult.recheck.summary}
              </div>
              {acceptResult.issue_resolved ? (
                <div className="flex items-center gap-1.5 text-xs text-green-700">
                  <ShieldCheck size={13} /> Issue resolved — the check now passes.
                </div>
              ) : acceptResult.next_suggestion ? (
                <div className="flex items-center gap-1.5 text-xs text-amber-700">
                  <AlertTriangle size={13} />
                  Still not clear — the agent is working on a different fix.
                </div>
              ) : (
                <div className="flex items-center gap-1.5 text-xs text-red-700">
                  <AlertTriangle size={13} />
                  We've worked through every fix and the issue persists — this needs
                  manual attention.
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ---- shared ----------------------------------------------------------

function Stat({
  label,
  value,
  tone = 'default',
}: {
  label: string
  value: string | number
  tone?: 'default' | 'good' | 'warn' | 'bad' | 'muted'
}) {
  const toneClass =
    tone === 'good'
      ? 'text-green-600'
      : tone === 'warn'
        ? 'text-amber-600'
        : tone === 'bad'
          ? 'text-red-600'
          : tone === 'muted'
            ? 'text-black/50'
            : 'text-black/80'
  return (
    <div className="rounded-lg border border-black/10 bg-white p-3">
      <div className="text-xs text-black/50">{label}</div>
      <div className={`mt-0.5 text-lg font-semibold ${toneClass}`}>{value}</div>
    </div>
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
