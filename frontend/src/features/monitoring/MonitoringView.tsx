import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Activity, RefreshCw } from 'lucide-react'
import { api } from '../../api/client'
import type {
  MonitoringHealth,
  MetricsSnapshot,
  LogEntry,
  RunInfo,
} from '../../api/types'

export default function MonitoringView() {
  const [tab, setTab] = useState<'overview' | 'logs' | 'runs'>('overview')

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-black/10 bg-white px-4 py-3">
        <div className="flex gap-1 overflow-x-auto">
          {(['overview', 'logs', 'runs'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium capitalize ${
                tab === t ? 'bg-brand text-white' : 'text-black/60 hover:bg-black/5'
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      </div>
      <div className="scroll-thin flex-1 overflow-y-auto p-4">
        {tab === 'overview' && <Overview />}
        {tab === 'logs' && <Logs />}
        {tab === 'runs' && <Runs />}
      </div>
    </div>
  )
}

function Overview() {
  const healthQ = useQuery({
    queryKey: ['health'],
    queryFn: () => api.get<MonitoringHealth>('/monitoring/health'),
    refetchInterval: 5000,
  })
  const metricsQ = useQuery({
    queryKey: ['metrics'],
    queryFn: () => api.get<MetricsSnapshot>('/monitoring/metrics'),
    refetchInterval: 5000,
  })

  const overall = healthQ.data?.overall ?? 'down'
  const color =
    overall === 'ok' ? 'text-green-600' : overall === 'degraded' ? 'text-amber-600' : 'text-red-600'

  const m = metricsQ.data

  return (
    <div className="max-w-3xl space-y-4">
      <div className="flex items-center gap-2">
        <Activity size={18} className={color} />
        <span className={`text-sm font-semibold capitalize ${color}`}>{overall}</span>
        <button
          onClick={() => {
            healthQ.refetch()
            metricsQ.refetch()
          }}
          className="ml-auto rounded-lg p-1.5 text-black/40 hover:bg-black/5"
        >
          <RefreshCw size={15} />
        </button>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <Stat label="Active runs" value={m?.runs_active ?? 0} />
        <Stat label="Completed" value={m?.runs_completed ?? 0} />
        <Stat label="Failed" value={m?.runs_failed ?? 0} />
        <Stat label="Retries" value={m?.retry_count ?? 0} />
        <Stat label="Tokens in" value={fmt(m?.tokens_in ?? 0)} />
        <Stat label="Tokens out" value={fmt(m?.tokens_out ?? 0)} />
        <Stat label="Cost USD" value={(m?.cost_usd_total ?? 0).toFixed(4)} />
        <Stat label="LLM errors" value={m?.llm_errors_total ?? 0} />
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <Stat label="Latency p50" value={`${Math.round(m?.latency_p50_ms ?? 0)} ms`} />
        <Stat label="Latency p95" value={`${Math.round(m?.latency_p95_ms ?? 0)} ms`} />
      </div>

      <div className="rounded-lg border border-black/10 bg-white p-3">
        <div className="mb-2 text-xs font-semibold text-black/60">Health checks</div>
        <div className="space-y-1">
          {(healthQ.data?.checks ?? []).map((c) => (
            <div key={c.name} className="flex items-center gap-2 text-sm">
              <span
                className={`inline-block h-2 w-2 rounded-full ${
                  c.ok ? 'bg-green-500' : 'bg-red-500'
                }`}
              />
              <span className="capitalize">{c.name}</span>
              {c.detail && <span className="text-xs text-black/40">— {c.detail}</span>}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function Stat({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-lg border border-black/10 bg-white p-3">
      <div className="text-xs text-black/50">{label}</div>
      <div className="mt-0.5 text-lg font-semibold text-black/80">{value}</div>
    </div>
  )
}

function Logs() {
  const [level, setLevel] = useState('')
  const logsQ = useQuery({
    queryKey: ['logs', level],
    queryFn: () =>
      api.get<{ logs: LogEntry[] }>(
        `/monitoring/logs?limit=300${level ? `&level=${level}` : ''}`,
      ),
    refetchInterval: 5000,
  })

  // Light text on the dark terminal-style panel — black-on-dark was unreadable.
  const levelColor = (l: string) =>
    l === 'ERROR'
      ? 'text-[#f85149]'
      : l === 'WARNING'
        ? 'text-[#d29922]'
        : l === 'DEBUG'
          ? 'text-[#8b949e]'
          : 'text-[#58a6ff]'

  return (
    <div className="flex h-full flex-col">
      <div className="mb-2 flex items-center gap-2">
        <select
          value={level}
          onChange={(e) => setLevel(e.target.value)}
          className="rounded-md border border-black/15 bg-white px-2 py-1 text-xs"
        >
          <option value="">All levels</option>
          <option value="DEBUG">DEBUG</option>
          <option value="INFO">INFO</option>
          <option value="WARNING">WARNING</option>
          <option value="ERROR">ERROR</option>
        </select>
        <button
          onClick={() => logsQ.refetch()}
          className="rounded-lg p-1.5 text-black/40 hover:bg-black/5"
        >
          <RefreshCw size={15} />
        </button>
      </div>
      <div className="scroll-thin flex-1 overflow-auto rounded-lg border border-black/10 bg-[#0d1117] p-2 font-mono text-[11px]">
        {(logsQ.data?.logs ?? []).map((l, i) => (
          <div key={i} className="whitespace-pre-wrap break-all">
            <span className="text-[#8b949e]">{l.timestamp} </span>
            <span className={`${levelColor(l.level)} font-semibold`}>{l.level} </span>
            <span className="text-[#8b949e]">{l.logger}: </span>
            <span className="text-[#e6edf3]">{l.message}</span>
          </div>
        ))}
      </div>
    </div>
  )
}

function Runs() {
  const runsQ = useQuery({
    queryKey: ['runs'],
    queryFn: () => api.get<{ runs: RunInfo[] }>('/monitoring/runs'),
    refetchInterval: 5000,
  })
  return (
    <div className="overflow-x-auto rounded-lg border border-black/10 bg-white">
      <table className="w-full text-left text-xs">
        <thead className="border-b border-black/10 text-black/50">
          <tr>
            <th className="px-3 py-2">Run</th>
            <th className="px-3 py-2">State</th>
            <th className="px-3 py-2">Turns</th>
            <th className="px-3 py-2">Cost</th>
            <th className="px-3 py-2">Started</th>
          </tr>
        </thead>
        <tbody>
          {(runsQ.data?.runs ?? []).map((r) => (
            <tr key={r.id} className="border-b border-black/5">
              <td className="px-3 py-2 font-mono">{r.id.slice(0, 8)}</td>
              <td className="px-3 py-2 capitalize">{r.state}</td>
              <td className="px-3 py-2">{r.turns_used}</td>
              <td className="px-3 py-2">${(r.cost_usd ?? 0).toFixed(4)}</td>
              <td className="px-3 py-2">{r.started_at}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function fmt(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`
  return String(n)
}
