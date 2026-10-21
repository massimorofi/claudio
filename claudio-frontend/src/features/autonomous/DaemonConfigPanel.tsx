import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Save, Play, Loader2, Send } from 'lucide-react'
import { api } from '../../api/client'
import type {
  AppConfig,
  AutonomousCfg,
  TaskReport,
  NotifyTestResult,
} from '../../api/types'

const inputCls =
  'w-full rounded-lg border border-black/15 bg-white px-3 py-2 text-sm outline-none focus:border-brand'

function emptyAutonomous(): AutonomousCfg {
  return {
    enabled: false,
    report_only: true,
    health_check: {
      enabled: true,
      interval_minutes: 10,
      disk_warn_percent: 85,
      disk_crit_percent: 95,
      mem_crit_percent: 95,
      swap_warn_percent: 80,
      load_warn_factor: 1,
      load_crit_factor: 2,
      backup_path: '',
      backup_max_age_hours: 26,
      script: '',
    },
    maintenance: {
      enabled: false,
      schedule: '0 3 * * *',
      vacuum_size: '200M',
      temp_dirs: ['/tmp', '/var/tmp'],
      audit_retention_days: 90,
      apt_update: true,
      vacuum_logs: true,
      temp_cleanup: true,
      autoremove: true,
      full_upgrade: false,
      docker_prune: false,
      script: '',
    },
    recovery: {
      enabled: false,
      expected_services: [],
      restart_allowlist: [],
    },
    notification: {
      enabled: false,
      email: {
        enabled: false,
        smtp_host: '',
        smtp_port: 587,
        username: '',
        password: '',
        from_addr: '',
        to_addr: '',
        use_tls: true,
      },
      webhook: { enabled: false, url: '' },
    },
    command_allowlist: null,
  }
}

export default function DaemonConfigPanel() {
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState<AutonomousCfg | null>(null)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)

  const configQ = useQuery({
    queryKey: ['config'],
    queryFn: () => api.get<{ config: AppConfig; generation: number }>('/config'),
  })

  useEffect(() => {
    if (configQ.data && !draft) {
      setDraft(configQ.data.config.autonomous ?? emptyAutonomous())
    }
  }, [configQ.data, draft])

  const saveM = useMutation({
    mutationFn: (autonomous: AutonomousCfg) =>
      api.put<{ restart_required: string[] }>('/config', { autonomous }),
    onSuccess: () => {
      setMessage({ kind: 'ok', text: 'Daemon settings saved.' })
      queryClient.invalidateQueries({ queryKey: ['config'] })
      queryClient.invalidateQueries({ queryKey: ['autonomous-status'] })
    },
    onError: (e: Error) => setMessage({ kind: 'err', text: e.message }),
  })

  if (!draft) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-black/40">
        Loading daemon settings…
      </div>
    )
  }

  const set = (patch: Partial<AutonomousCfg>) =>
    setDraft((d) => (d ? { ...d, ...patch } : d))
  const setMaint = (patch: Partial<AutonomousCfg['maintenance']>) =>
    setDraft((d) => (d ? { ...d, maintenance: { ...d.maintenance, ...patch } } : d))
  const setRec = (patch: Partial<AutonomousCfg['recovery']>) =>
    setDraft((d) => (d ? { ...d, recovery: { ...d.recovery, ...patch } } : d))
  const setNotif = (patch: Partial<AutonomousCfg['notification']>) =>
    setDraft((d) => (d ? { ...d, notification: { ...d.notification, ...patch } } : d))
  const setEmail = (patch: Partial<AutonomousCfg['notification']['email']>) =>
    setDraft((d) =>
      d
        ? {
            ...d,
            notification: { ...d.notification, email: { ...d.notification.email, ...patch } },
          }
        : d,
    )

  return (
    <div className="max-w-2xl space-y-5">
      {message && (
        <div
          className={`rounded-lg px-3 py-2 text-sm ${
            message.kind === 'ok' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
          }`}
        >
          {message.text}
        </div>
      )}

      {/* ---- Master switches ---- */}
      <section className="rounded-lg border border-black/10 bg-white p-4">
        <div className="mb-3 text-sm font-semibold text-black/70">Autonomy</div>
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.enabled}
              onChange={(e) => set({ enabled: e.target.checked })}
            />
            <span className="font-medium">Daemon enabled</span>
            <span className="text-xs text-black/40">
              master switch — when off, no scheduled work runs
            </span>
          </label>
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.report_only}
              onChange={(e) => set({ report_only: e.target.checked })}
            />
            <span className="font-medium">Report only</span>
            <span className="text-xs text-black/40">
              when on, the daemon never runs mutating commands
            </span>
          </label>
        </div>
      </section>

      {/* ---- Built-in run-now cards ---- */}
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <RunNowCard
          title="Maintenance"
          subtitle="Safe cleanup pass"
          endpoint="/autonomous/maintenance/run"
        />
        <RunNowCard
          title="Recovery"
          subtitle="Boot disaster recovery"
          endpoint="/autonomous/recovery/run"
        />
      </section>

      {/* ---- Maintenance config ---- */}
      <section className="rounded-lg border border-black/10 bg-white p-4">
        <div className="mb-3 text-sm font-semibold text-black/70">Maintenance</div>
        <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
          <Field label="Enabled">
            <input
              type="checkbox"
              checked={draft.maintenance.enabled}
              onChange={(e) => setMaint({ enabled: e.target.checked })}
            />
          </Field>
          <Field label="Schedule (cron, UTC)" hint="e.g. 0 3 * * * = daily at 03:00">
            <input
              className={`${inputCls} font-mono`}
              value={draft.maintenance.schedule}
              onChange={(e) => setMaint({ schedule: e.target.value })}
            />
          </Field>
          <Field label="Vacuum size" hint="journalctl --vacuum-size value, e.g. 200M">
            <input
              className={inputCls}
              value={draft.maintenance.vacuum_size}
              onChange={(e) => setMaint({ vacuum_size: e.target.value })}
            />
          </Field>
          <Field label="Audit retention (days)">
            <input
              className={inputCls}
              type="number"
              value={draft.maintenance.audit_retention_days}
              onChange={(e) =>
                setMaint({ audit_retention_days: Number(e.target.value) || 90 })
              }
            />
          </Field>
          <div className="sm:col-span-2">
            <Field
              label="Temp dirs (comma-separated)"
              hint="Directories cleaned by the temp-cleanup script"
            >
              <input
                className={inputCls}
                value={draft.maintenance.temp_dirs.join(', ')}
                onChange={(e) =>
                  setMaint({
                    temp_dirs: splitList(e.target.value),
                  })
                }
              />
            </Field>
          </div>
        </div>

        <div className="mt-3">
          <div className="mb-1 text-xs font-medium text-black/60">Actions</div>
          <div className="grid grid-cols-1 gap-1 sm:grid-cols-2">
            <Toggle
              label="apt-get update"
              checked={draft.maintenance.apt_update}
              onChange={(v) => setMaint({ apt_update: v })}
            />
            <Toggle
              label="Vacuum logs"
              checked={draft.maintenance.vacuum_logs}
              onChange={(v) => setMaint({ vacuum_logs: v })}
            />
            <Toggle
              label="Temp cleanup"
              checked={draft.maintenance.temp_cleanup}
              onChange={(v) => setMaint({ temp_cleanup: v })}
            />
            <Toggle
              label="autoremove"
              hint="irreversible → suggestion"
              checked={draft.maintenance.autoremove}
              onChange={(v) => setMaint({ autoremove: v })}
            />
            <Toggle
              label="Full upgrade"
              hint="irreversible → suggestion"
              checked={draft.maintenance.full_upgrade}
              onChange={(v) => setMaint({ full_upgrade: v })}
            />
            <Toggle
              label="Docker prune"
              hint="irreversible → suggestion"
              checked={draft.maintenance.docker_prune}
              onChange={(v) => setMaint({ docker_prune: v })}
            />
          </div>
        </div>
      </section>

      {/* ---- Recovery config ---- */}
      <section className="rounded-lg border border-black/10 bg-white p-4">
        <div className="mb-3 text-sm font-semibold text-black/70">Disaster recovery</div>
        <div className="space-y-3">
          <label className="flex items-center gap-2 text-sm">
            <input
              type="checkbox"
              checked={draft.recovery.enabled}
              onChange={(e) => setRec({ enabled: e.target.checked })}
            />
            <span>Enabled</span>
          </label>
          <Field
            label="Expected services (comma-separated)"
            hint="Services that must be running after boot"
          >
            <input
              className={inputCls}
              value={draft.recovery.expected_services.join(', ')}
              onChange={(e) => setRec({ expected_services: splitList(e.target.value) })}
            />
          </Field>
          <Field
            label="Restart allowlist (comma-separated)"
            hint="Units the recovery handler may restart"
          >
            <input
              className={inputCls}
              value={draft.recovery.restart_allowlist.join(', ')}
              onChange={(e) => setRec({ restart_allowlist: splitList(e.target.value) })}
            />
          </Field>
        </div>
      </section>

      {/* ---- Notification config ---- */}
      <section className="rounded-lg border border-black/10 bg-white p-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="text-sm font-semibold text-black/70">Notifications</div>
          <NotifyTestButton />
        </div>
        <label className="mb-3 flex items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={draft.notification.enabled}
            onChange={(e) => setNotif({ enabled: e.target.checked })}
          />
          <span>Notifications enabled</span>
        </label>

        <div className="rounded-lg border border-black/10 p-3">
          <label className="mb-2 flex items-center gap-2 text-sm font-medium text-black/70">
            <input
              type="checkbox"
              checked={draft.notification.email.enabled}
              onChange={(e) => setEmail({ enabled: e.target.checked })}
            />
            Email
          </label>
          <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
            <Field label="SMTP host">
              <input
                className={inputCls}
                value={draft.notification.email.smtp_host}
                onChange={(e) => setEmail({ smtp_host: e.target.value })}
              />
            </Field>
            <Field label="SMTP port">
              <input
                className={inputCls}
                type="number"
                value={draft.notification.email.smtp_port}
                onChange={(e) => setEmail({ smtp_port: Number(e.target.value) || 587 })}
              />
            </Field>
            <Field label="Username">
              <input
                className={inputCls}
                value={draft.notification.email.username}
                onChange={(e) => setEmail({ username: e.target.value })}
              />
            </Field>
            <Field label="Password" hint="Stored locally; never returned by the API.">
              <input
                className={inputCls}
                type="password"
                autoComplete="off"
                value={draft.notification.email.password}
                onChange={(e) => setEmail({ password: e.target.value })}
              />
            </Field>
            <Field label="From address">
              <input
                className={inputCls}
                value={draft.notification.email.from_addr}
                onChange={(e) => setEmail({ from_addr: e.target.value })}
              />
            </Field>
            <Field label="To address">
              <input
                className={inputCls}
                value={draft.notification.email.to_addr}
                onChange={(e) => setEmail({ to_addr: e.target.value })}
              />
            </Field>
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={draft.notification.email.use_tls}
                onChange={(e) => setEmail({ use_tls: e.target.checked })}
              />
              Use TLS
            </label>
          </div>
        </div>

        <div className="mt-3 rounded-lg border border-black/10 p-3">
          <label className="mb-2 flex items-center gap-2 text-sm font-medium text-black/70">
            <input
              type="checkbox"
              checked={draft.notification.webhook.enabled}
              onChange={(e) =>
                setNotif({ webhook: { ...draft.notification.webhook, enabled: e.target.checked } })
              }
            />
            Webhook
          </label>
          <Field label="Webhook URL">
            <input
              className={inputCls}
              value={draft.notification.webhook.url}
              onChange={(e) =>
                setNotif({ webhook: { ...draft.notification.webhook, url: e.target.value } })
              }
            />
          </Field>
        </div>
      </section>

      {/* ---- Command allowlist (read-only) ---- */}
      <section className="rounded-lg border border-black/10 bg-white p-4">
        <div className="mb-2 text-sm font-semibold text-black/70">
          Command allowlist
          <span className="ml-2 text-xs font-normal text-black/40">(read-only)</span>
        </div>
        {draft.command_allowlist && draft.command_allowlist.length > 0 ? (
          <ul className="space-y-1">
            {draft.command_allowlist.map((cmd, i) => (
              <li key={i}>
                <code className="break-all rounded bg-black/5 px-2 py-1 font-mono text-[12px] text-black/70">
                  {cmd}
                </code>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-black/50">
            No custom allowlist set — the built-in defaults are in effect. Set
            <code className="mx-1 rounded bg-black/5 px-1 font-mono text-[12px]">
              autonomous.command_allowlist
            </code>
            in the config file to override.
          </p>
        )}
      </section>

      <div className="pb-2">
        <button
          onClick={() => saveM.mutate(draft)}
          disabled={saveM.isPending}
          className="inline-flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white hover:opacity-90 disabled:opacity-50"
        >
          {saveM.isPending ? (
            <Loader2 size={16} className="animate-spin" />
          ) : (
            <Save size={16} />
          )}
          Save daemon settings
        </button>
      </div>
    </div>
  )
}

function RunNowCard({
  title,
  subtitle,
  endpoint,
}: {
  title: string
  subtitle: string
  endpoint: string
}) {
  const [report, setReport] = useState<TaskReport | null>(null)
  const runM = useMutation({
    mutationFn: () => api.post<{ report: TaskReport }>(endpoint),
    onSuccess: (data) => setReport(data.report),
  })
  return (
    <div className="rounded-lg border border-black/10 bg-white p-3">
      <div className="flex items-center justify-between">
        <div>
          <div className="text-sm font-semibold text-black/80">{title}</div>
          <div className="text-xs text-black/40">{subtitle}</div>
        </div>
        <button
          onClick={() => runM.mutate()}
          disabled={runM.isPending}
          className="inline-flex items-center gap-1 rounded-lg bg-brand px-2.5 py-1 text-xs font-medium text-white hover:opacity-90 disabled:opacity-50"
        >
          {runM.isPending ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Play size={12} />
          )}
          Run now
        </button>
      </div>
      {report && (
        <div
          className={`mt-2 rounded-lg border p-2 text-xs ${
            report.verdict === 'ok'
              ? 'border-green-200 bg-green-50 text-green-700'
              : report.verdict === 'critical'
                ? 'border-red-200 bg-red-50 text-red-700'
                : 'border-amber-200 bg-amber-50 text-amber-700'
          }`}
        >
          <span className="font-semibold">{report.verdict}</span> — {report.summary}
        </div>
      )}
      {runM.isError && (
        <div className="mt-2 rounded-lg border border-red-200 bg-red-50 p-2 text-xs text-red-700">
          {(runM.error as Error)?.message ?? 'Run failed.'}
        </div>
      )}
    </div>
  )
}

function NotifyTestButton() {
  const [result, setResult] = useState<NotifyTestResult | null>(null)
  const testM = useMutation({
    mutationFn: () => api.post<NotifyTestResult>('/autonomous/notify/test'),
    onSuccess: (data) => setResult(data),
    onError: (e: Error) =>
      setResult({
        enabled: false,
        channels: {},
        ok: false,
        message: e.message,
      }),
  })
  return (
    <div className="flex items-center gap-2">
      {result && (
        <span
          className={`text-xs ${result.ok ? 'text-green-700' : 'text-red-600'}`}
        >
          {result.ok ? 'Test sent' : `Failed: ${result.message}`}
        </span>
      )}
      <button
        onClick={() => testM.mutate()}
        disabled={testM.isPending}
        className="inline-flex items-center gap-1 rounded-lg border border-brand px-2.5 py-1 text-xs font-medium text-brand hover:bg-brand/5 disabled:opacity-50"
      >
        {testM.isPending ? (
          <Loader2 size={12} className="animate-spin" />
        ) : (
          <Send size={12} />
        )}
        Test
      </button>
    </div>
  )
}

function Toggle({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string
  hint?: string
  checked: boolean
  onChange: (v: boolean) => void
}) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>{label}</span>
      {hint && <span className="text-[11px] text-black/40">— {hint}</span>}
    </label>
  )
}

function Field({
  label,
  children,
  hint,
}: {
  label: string
  children: React.ReactNode
  hint?: string
}) {
  return (
    <div className="mb-3">
      <span className="mb-1 block text-xs font-medium text-black/60">{label}</span>
      {children}
      {hint && <span className="mt-0.5 block text-[11px] text-black/40">{hint}</span>}
    </div>
  )
}

function splitList(value: string): string[] {
  return value
    .split(',')
    .map((s) => s.trim())
    .filter((s) => s.length > 0)
}
