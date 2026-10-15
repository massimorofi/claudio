import { useEffect, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Save, RotateCcw, Play, AlertTriangle, History } from 'lucide-react'
import { api } from '../../api/client'
import type { AppConfig, ConfigHistoryEntry, LlmTestResult } from '../../api/types'

type SetField = <K extends keyof AppConfig>(k: K, v: AppConfig[K]) => void
type TestMut = {
  mutate: (llm: AppConfig['llm']) => void
  isPending: boolean
  data?: LlmTestResult
}

export default function ConfigView() {
  const [tab, setTab] = useState<'llm' | 'agent' | 'mcp' | 'danger'>('llm')
  const [draft, setDraft] = useState<AppConfig | null>(null)
  const [restartNeeded, setRestartNeeded] = useState(false)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const queryClient = useQueryClient()

  const configQ = useQuery({
    queryKey: ['config'],
    queryFn: () => api.get<{ config: AppConfig; generation: number }>('/config'),
  })

  useEffect(() => {
    if (configQ.data && !draft) setDraft(configQ.data.config)
  }, [configQ.data, draft])

  const saveMut = useMutation({
    mutationFn: (cfg: AppConfig) =>
      api.put<{ restart_required: string[]; changed_keys: string[] }>('/config', cfg),
    onSuccess: (res) => {
      const needs = (res.restart_required ?? []).length > 0
      setRestartNeeded(needs)
      setMessage({
        kind: 'ok',
        text: needs
          ? `Saved. Restart required for: ${(res.restart_required ?? []).join(', ')}`
          : 'Saved.',
      })
      queryClient.invalidateQueries({ queryKey: ['config'] })
    },
    onError: (e: Error) => setMessage({ kind: 'err', text: e.message }),
  })

  const testMut = useMutation({
    mutationFn: (llm: AppConfig['llm']) =>
      api.post<LlmTestResult>('/config/llm/test', { llm }),
    onError: (e: Error) => setMessage({ kind: 'err', text: e.message }),
  })

  const historyQ = useQuery({
    queryKey: ['config-history'],
    queryFn: () => api.get<{ history: ConfigHistoryEntry[] }>('/config/history'),
  })

  const restoreMut = useMutation({
    mutationFn: (id: number) =>
      api.post<{ restart_required: string[] }>(`/config/restore`, { id }),
    onSuccess: (res) => {
      setDraft(null)
      setRestartNeeded((res.restart_required ?? []).length > 0)
      queryClient.invalidateQueries({ queryKey: ['config'] })
      setMessage({ kind: 'ok', text: 'Config restored.' })
    },
    onError: (e: Error) => setMessage({ kind: 'err', text: e.message }),
  })

  const restartMut = useMutation({
    mutationFn: () => api.post('/agent/restart'),
    onSuccess: () => {
      setRestartNeeded(false)
      setMessage({ kind: 'ok', text: 'Agent restarted.' })
      queryClient.invalidateQueries()
    },
    onError: (e: Error) => setMessage({ kind: 'err', text: e.message }),
  })

  if (!draft) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-black/40">
        Loading configuration…
      </div>
    )
  }

  const set = <K extends keyof AppConfig>(key: K, value: AppConfig[K]) =>
    setDraft((d) => (d ? { ...d, [key]: value } : d))

  return (
    <div className="flex h-full flex-col">
      <div className="border-b border-black/10 bg-white px-4 py-3">
        <div className="flex gap-1 overflow-x-auto">
          {(['llm', 'agent', 'mcp', 'danger'] as const).map((t) => (
            <button
              key={t}
              onClick={() => setTab(t)}
              className={`rounded-lg px-3 py-1.5 text-sm font-medium capitalize ${
                tab === t ? 'bg-brand text-white' : 'text-black/60 hover:bg-black/5'
              }`}
            >
              {t === 'mcp' ? 'MCP Servers' : t}
            </button>
          ))}
        </div>
      </div>

      {message && (
        <div
          className={`px-4 py-2 text-sm ${
            message.kind === 'ok' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
          }`}
        >
          {message.text}
        </div>
      )}
      {restartNeeded && (
        <div className="flex items-center gap-3 bg-amber-50 px-4 py-2 text-sm text-amber-800">
          <AlertTriangle size={16} />
          Changes require an agent restart.
          <button
            onClick={() => restartMut.mutate()}
            className="ml-auto rounded-lg bg-amber-600 px-3 py-1 text-xs font-semibold text-white"
          >
            Restart now
          </button>
        </div>
      )}

      <div className="scroll-thin flex-1 overflow-y-auto p-4">
        {tab === 'llm' && (
          <LlmSection draft={draft} setDraft={set} testMut={testMut} />
        )}
        {tab === 'agent' && <AgentSection draft={draft} setDraft={set} />}
        {tab === 'mcp' && <McpSection draft={draft} setDraft={set} />}
        {tab === 'danger' && <DangerSection />}
      </div>

      {tab !== 'danger' && (
        <div className="border-t border-black/10 bg-white p-3">
          <button
            onClick={() => saveMut.mutate(draft)}
            disabled={saveMut.isPending}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white disabled:opacity-50 md:w-auto"
          >
            <Save size={16} /> Save configuration
          </button>
        </div>
      )}
    </div>
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
    <label className="block mb-3">
      <span className="mb-1 block text-xs font-medium text-black/60">{label}</span>
      {children}
      {hint && <span className="mt-0.5 block text-[11px] text-black/40">{hint}</span>}
    </label>
  )
}

const inputCls =
  'w-full rounded-lg border border-black/15 bg-white px-3 py-2 text-sm outline-none focus:border-brand'

function LlmSection({
  draft,
  setDraft,
  testMut,
}: {
  draft: AppConfig
  setDraft: SetField
  testMut: TestMut
}) {
  const llm = draft.llm
  const upd = (patch: Partial<AppConfig['llm']>) => setDraft('llm', { ...llm, ...patch })

  return (
    <div className="max-w-xl">
      <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
        <Field label="Model">
          <input
            className={inputCls}
            value={llm.model}
            onChange={(e) => upd({ model: e.target.value })}
          />
        </Field>
        <Field label="Base URL">
          <input
            className={inputCls}
            value={llm.base_url}
            onChange={(e) => upd({ base_url: e.target.value })}
          />
        </Field>
        <Field label="API key" hint="Stored locally; never returned by the API.">
          <input
            className={inputCls}
            type="password"
            value={llm.api_key}
            onChange={(e) => upd({ api_key: e.target.value })}
          />
        </Field>
        <Field label="Fallback model">
          <input
            className={inputCls}
            value={llm.fallback_model ?? ''}
            onChange={(e) => upd({ fallback_model: e.target.value || null })}
          />
        </Field>
        <Field label="Temperature">
          <input
            className={inputCls}
            type="number"
            step="0.1"
            value={llm.temperature}
            onChange={(e) => upd({ temperature: Number(e.target.value) })}
          />
        </Field>
        <Field label="Max tokens">
          <input
            className={inputCls}
            type="number"
            value={llm.max_tokens}
            onChange={(e) => upd({ max_tokens: Number(e.target.value) })}
          />
        </Field>
        <Field label="Timeout (s)">
          <input
            className={inputCls}
            type="number"
            value={llm.timeout_seconds}
            onChange={(e) => upd({ timeout_seconds: Number(e.target.value) })}
          />
        </Field>
        <Field label="Retry attempts">
          <input
            className={inputCls}
            type="number"
            value={llm.retry_attempts}
            onChange={(e) => upd({ retry_attempts: Number(e.target.value) })}
          />
        </Field>
        <Field label="Price input / Mtok (USD)">
          <input
            className={inputCls}
            type="number"
            step="0.01"
            value={llm.price_input_per_mtok}
            onChange={(e) => upd({ price_input_per_mtok: Number(e.target.value) })}
          />
        </Field>
        <Field label="Price output / Mtok (USD)">
          <input
            className={inputCls}
            type="number"
            step="0.01"
            value={llm.price_output_per_mtok}
            onChange={(e) => upd({ price_output_per_mtok: Number(e.target.value) })}
          />
        </Field>
      </div>

      <button
        onClick={() => testMut.mutate(llm)}
        disabled={testMut.isPending}
        className="mt-2 flex items-center gap-2 rounded-lg border border-brand px-4 py-2 text-sm font-semibold text-brand disabled:opacity-50"
      >
        <Play size={16} /> Test connection
      </button>
      {testMut.data && (
        <div
          className={`mt-2 text-sm ${
            (testMut.data as LlmTestResult).ok ? 'text-green-700' : 'text-red-600'
          }`}
        >
          {(testMut.data as LlmTestResult).ok
            ? `OK — ${(testMut.data as LlmTestResult).latency_ms} ms`
            : `Failed: ${(testMut.data as LlmTestResult).error}`}
        </div>
      )}
    </div>
  )
}

function AgentSection({
  draft,
  setDraft,
}: {
  draft: AppConfig
  setDraft: SetField
}) {
  const agent = draft.agent
  const upd = (patch: Partial<AppConfig['agent']>) => setDraft('agent', { ...agent, ...patch })
  const allTools = [
    'read_file',
    'write_file',
    'edit_file',
    'list_dir',
    'run_command',
    'web_fetch',
    'web_search',
    'todo_write',
    'rewind_files',
    'spawn_subagent',
  ]

  const toggleTool = (name: string) => {
    const enabled = agent.tools.enabled.includes(name)
      ? agent.tools.enabled.filter((t) => t !== name)
      : [...agent.tools.enabled, name]
    upd({ tools: { ...agent.tools, enabled } })
  }

  return (
    <div className="max-w-xl">
      <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
        <Field label="Agent name">
          <input
            className={inputCls}
            value={agent.name}
            onChange={(e) => upd({ name: e.target.value })}
          />
        </Field>
        <Field label="Approval mode">
          <select
            className={inputCls}
            value={agent.approval_mode}
            onChange={(e) =>
              upd({ approval_mode: e.target.value as AppConfig['agent']['approval_mode'] })
            }
          >
            <option value="on_request">On request</option>
            <option value="always">Always ask</option>
            <option value="off">Off</option>
          </select>
        </Field>
        <Field label="Max turns">
          <input
            className={inputCls}
            type="number"
            value={agent.max_turns}
            onChange={(e) => upd({ max_turns: Number(e.target.value) })}
          />
        </Field>
        <Field label="Max budget (USD)">
          <input
            className={inputCls}
            type="number"
            step="0.5"
            value={agent.max_budget_usd}
            onChange={(e) => upd({ max_budget_usd: Number(e.target.value) })}
          />
        </Field>
        <Field label="Context window">
          <input
            className={inputCls}
            type="number"
            value={agent.context_window}
            onChange={(e) => upd({ context_window: Number(e.target.value) })}
          />
        </Field>
        <Field label="Compaction reserve">
          <input
            className={inputCls}
            type="number"
            value={agent.compaction_reserve}
            onChange={(e) => upd({ compaction_reserve: Number(e.target.value) })}
          />
        </Field>
        <Field label="Max output chars">
          <input
            className={inputCls}
            type="number"
            value={agent.max_output_chars}
            onChange={(e) => upd({ max_output_chars: Number(e.target.value) })}
          />
        </Field>
        <Field label="Workspace dir">
          <input
            className={inputCls}
            value={agent.workspace_dir}
            onChange={(e) => upd({ workspace_dir: e.target.value })}
          />
        </Field>
      </div>

      <div className="mt-2">
        <div className="mb-1 text-xs font-medium text-black/60">Enabled tools</div>
        <div className="grid grid-cols-2 gap-1 sm:grid-cols-3">
          {allTools.map((t) => (
            <label key={t} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={agent.tools.enabled.includes(t)}
                onChange={() => toggleTool(t)}
              />
              <span className="font-mono text-xs">{t}</span>
            </label>
          ))}
        </div>
      </div>
    </div>
  )
}

function McpSection({
  draft,
  setDraft,
}: {
  draft: AppConfig
  setDraft: SetField
}) {
  const servers = draft.mcp.servers
  const upd = (list: AppConfig['mcp']['servers']) => setDraft('mcp', { servers: list })

  const add = () =>
    upd([
      ...servers,
      { id: `server-${servers.length + 1}`, transport: 'stdio', command: '', enabled: false },
    ])

  const patch = (i: number, p: Partial<AppConfig['mcp']['servers'][number]>) =>
    upd(servers.map((s, idx) => (idx === i ? { ...s, ...p } : s)))

  const remove = (i: number) => upd(servers.filter((_, idx) => idx !== i))

  return (
    <div className="max-w-2xl">
      {servers.length === 0 && (
        <div className="mb-3 text-sm text-black/40">No MCP servers configured.</div>
      )}
      {servers.map((s, i) => (
        <div key={i} className="mb-3 rounded-lg border border-black/10 p-3">
          <div className="grid grid-cols-1 gap-x-4 sm:grid-cols-2">
            <Field label="ID">
              <input className={inputCls} value={s.id} onChange={(e) => patch(i, { id: e.target.value })} />
            </Field>
            <Field label="Transport">
              <select
                className={inputCls}
                value={s.transport}
                onChange={(e) =>
                  patch(i, { transport: e.target.value as AppConfig['mcp']['servers'][number]['transport'] })
                }
              >
                <option value="stdio">stdio</option>
                <option value="sse">sse</option>
                <option value="streamable_http">streamable_http</option>
              </select>
            </Field>
            {s.transport === 'stdio' ? (
              <Field label="Command">
                <input
                  className={inputCls}
                  value={s.command ?? ''}
                  onChange={(e) => patch(i, { command: e.target.value })}
                />
              </Field>
            ) : (
              <Field label="URL">
                <input
                  className={inputCls}
                  value={s.url ?? ''}
                  onChange={(e) => patch(i, { url: e.target.value })}
                />
              </Field>
            )}
            <label className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={s.enabled}
                onChange={(e) => patch(i, { enabled: e.target.checked })}
              />
              Enabled
            </label>
          </div>
          <button
            onClick={() => remove(i)}
            className="mt-2 text-xs text-red-500 hover:underline"
          >
            Remove
          </button>
        </div>
      ))}
      <button
        onClick={add}
        className="rounded-lg border border-brand px-4 py-2 text-sm font-semibold text-brand"
      >
        Add MCP server
      </button>
    </div>
  )
}

function DangerSection() {
  const [confirm, setConfirm] = useState(false)
  const clearMut = useMutation<{ ok: boolean }, Error>({
    mutationFn: () => api.post<{ ok: boolean }>('/sessions/clear'),
    onSuccess: () => {
      setConfirm(false)
    },
  })
  return (
    <div className="max-w-xl">
      <div className="rounded-lg border border-red-200 bg-red-50 p-4">
        <div className="flex items-center gap-2 text-sm font-semibold text-red-700">
          <AlertTriangle size={16} /> Danger zone
        </div>
        <p className="mt-1 text-sm text-red-600">
          This permanently deletes all chat sessions and their history.
        </p>
        {confirm ? (
          <div className="mt-3 flex gap-2">
            <button
              onClick={() => clearMut.mutate()}
              className="rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white"
            >
              Yes, delete everything
            </button>
            <button
              onClick={() => setConfirm(false)}
              className="rounded-lg border border-black/15 px-4 py-2 text-sm"
            >
              Cancel
            </button>
          </div>
        ) : (
          <button
            onClick={() => setConfirm(true)}
            className="mt-3 rounded-lg border border-red-300 px-4 py-2 text-sm font-semibold text-red-600"
          >
            Clear all sessions
          </button>
        )}
        {clearMut.data && (
          <div className="mt-2 text-xs text-green-700">All sessions cleared.</div>
        )}
      </div>
    </div>
  )
}
