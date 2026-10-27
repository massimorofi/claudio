import { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Save, Wifi, WifiOff, Loader2, AlertTriangle } from 'lucide-react'
import { getBaseUrl, setBaseUrl, normalizeBase } from '../../api/connection'
import { getToken, setToken } from '../../api/auth'

const inputCls =
  'w-full rounded-lg border border-black/15 bg-white px-3 py-2 text-sm outline-none focus:border-brand'

interface TestResult {
  ok: boolean
  text: string
}

/**
 * Frontend connection settings: the agent backend address and the bearer token
 * this browser sends with every request.
 *
 * These live in localStorage, not in the backend config file, because they are
 * what the browser needs *before* it can talk to the backend at all. The section
 * is deliberately independent of the backend config query so it stays reachable
 * (and fixable) even when the backend is down or the token is wrong.
 */
export default function ConnectionSection() {
  const [baseUrl, setBaseUrlDraft] = useState(getBaseUrl())
  const [token, setTokenDraft] = useState(getToken())
  const [saved, setSaved] = useState(false)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<TestResult | null>(null)
  const queryClient = useQueryClient()

  const save = () => {
    setBaseUrl(baseUrl)
    setToken(token.trim())
    setSaved(true)
    // Every cached query was fetched against the old address/token; drop them so
    // the next render refetches through the new connection.
    queryClient.invalidateQueries()
  }

  // Test with the *typed* values, not the saved ones, so the user can verify a
  // new address before committing to it.
  const test = async () => {
    setTesting(true)
    setResult(null)
    const base = normalizeBase(baseUrl)
    const t = token.trim()
    try {
      const health = await fetch(`${base}/healthz`, { signal: AbortSignal.timeout(5000) })
      if (!health.ok) throw new Error(`liveness check failed: HTTP ${health.status}`)

      const ver = await fetch(`${base}/api/v1/version`, {
        headers: { Authorization: `Bearer ${t}` },
        signal: AbortSignal.timeout(5000),
      })
      if (!ver.ok) {
        const body = await ver.text().catch(() => '')
        throw new Error(
          `authenticated request failed: HTTP ${ver.status}${body ? ` — ${body.slice(0, 160)}` : ''}`,
        )
      }
      const data = (await ver.json()) as { backend?: string }
      setResult({ ok: true, text: `Connected — backend ${data.backend ?? 'unknown version'}` })
    } catch (e) {
      setResult({ ok: false, text: (e as Error).message || 'connection failed' })
    } finally {
      setTesting(false)
    }
  }

  return (
    <div className="max-w-xl">
      <p className="mb-3 text-sm text-black/50">
        Where this browser reaches the Claudio agent, and the token it authenticates
        with. Leave the address empty to use this site&apos;s own origin (the nginx
        proxy in the docker setup); set a full URL such as{' '}
        <code className="rounded bg-black/5 px-1">http://192.168.1.50:8100</code> to
        call the agent directly.
      </p>

      <label className="block mb-3">
        <span className="mb-1 block text-xs font-medium text-black/60">
          Agent backend address
        </span>
        <input
          className={inputCls}
          placeholder="(same origin)"
          value={baseUrl}
          onChange={(e) => {
            setBaseUrlDraft(e.target.value)
            setSaved(false)
          }}
        />
        <span className="mt-0.5 block text-[11px] text-black/40">
          Applied to every API call on save. Empty = same origin.
        </span>
      </label>

      <label className="block mb-3">
        <span className="mb-1 block text-xs font-medium text-black/60">Auth token</span>
        <input
          className={inputCls}
          type="password"
          placeholder="dev-token-change-me"
          value={token}
          onChange={(e) => {
            setTokenDraft(e.target.value)
            setSaved(false)
          }}
        />
        <span className="mt-0.5 block text-[11px] text-black/40">
          Must match the backend&apos;s server.auth_token.
        </span>
      </label>

      <div className="mt-2 flex flex-wrap items-center gap-2">
        <button
          onClick={save}
          className="flex items-center gap-2 rounded-lg bg-brand px-4 py-2 text-sm font-semibold text-white"
        >
          <Save size={16} /> Save connection
        </button>
        <button
          onClick={test}
          disabled={testing}
          className="flex items-center gap-2 rounded-lg border border-brand px-4 py-2 text-sm font-semibold text-brand disabled:opacity-50"
        >
          {testing ? <Loader2 size={16} className="animate-spin" /> : <Wifi size={16} />}
          Test connection
        </button>
        {saved && !result && (
          <span className="text-sm text-green-700">Saved.</span>
        )}
      </div>

      {result && (
        <div
          className={`mt-3 flex items-start gap-2 rounded-lg px-3 py-2 text-sm ${
            result.ok ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
          }`}
        >
          {result.ok ? (
            <Wifi size={16} className="mt-0.5 shrink-0" />
          ) : (
            <AlertTriangle size={16} className="mt-0.5 shrink-0" />
          )}
          <span>{result.text}</span>
        </div>
      )}
    </div>
  )
}
