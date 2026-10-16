import { useState } from 'react'
import type { ContextUsage } from '../../api/types'

interface ContextGaugeProps {
  usage: ContextUsage | null
  disabled: boolean
  onCompact: () => void
}

const SIZE = 36
const R = 14
const C = 2 * Math.PI * R

function bandColor(pct: number): string {
  if (pct < 60) return '#16a34a' // green
  if (pct <= 85) return '#d97706' // amber
  return '#dc2626' // red
}

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}

/**
 * Context usage donut (Phase 4): arc = used share of the usable window, with the
 * reserve drawn as a distinct band and a tick at the auto-compact point. Clicking
 * opens a confirm popover that triggers manual compaction.
 */
export default function ContextGauge({ usage, disabled, onCompact }: ContextGaugeProps) {
  const [open, setOpen] = useState(false)

  const pct = usage?.percent_used ?? 0
  const window = usage?.context_window ?? 1
  const usable = usage?.usable_tokens ?? 1
  const usedFrac = usage ? Math.min(1, usage.used_tokens / window) : 0
  const usableFrac = Math.min(1, usable / window)
  const color = bandColor(pct)

  const handleClick = () => {
    if (disabled) return
    setOpen((o) => !o)
  }

  const confirm = () => {
    setOpen(false)
    onCompact()
  }

  return (
    <div className="relative">
      <button
        onClick={handleClick}
        disabled={disabled}
        title={
          disabled
            ? 'Finish the current run first'
            : 'Context usage — click to compact'
        }
        className={`rounded-full p-0.5 transition-colors ${
          disabled ? 'cursor-not-allowed opacity-50' : 'hover:bg-black/5'
        }`}
        aria-label={`Context ${pct}% used`}
      >
        <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} aria-hidden="true">
          {/* Full-window track */}
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={R}
            fill="none"
            stroke="rgba(0,0,0,0.08)"
            strokeWidth="4"
          />
          {/* Reserve band: the held-back portion, drawn distinctly */}
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={R}
            fill="none"
            stroke="rgba(217,119,6,0.18)"
            strokeWidth="4"
            strokeDasharray={`${(1 - usableFrac) * C} ${C}`}
            strokeDashoffset={-usableFrac * C}
            transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
          />
          {/* Used arc */}
          <circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={R}
            fill="none"
            stroke={color}
            strokeWidth="4"
            strokeDasharray={`${usedFrac * C} ${C}`}
            strokeLinecap="round"
            transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
          />
          {/* Auto-compact threshold tick */}
          <line
            x1={SIZE / 2}
            y1={SIZE / 2 - R - 3}
            x2={SIZE / 2}
            y2={SIZE / 2 - R + 3}
            stroke="rgba(0,0,0,0.35)"
            strokeWidth="1.5"
            transform={`rotate(${usableFrac * 360} ${SIZE / 2} ${SIZE / 2})`}
          />
        </svg>
        <span
          className="pointer-events-none absolute inset-0 flex items-center justify-center text-[10px] font-semibold"
          style={{ color }}
        >
          {formatTokens(usage?.free_tokens ?? 0)}
        </span>
      </button>

      {usage?.auto_disabled && (
        <span className="absolute -top-1 -right-1 rounded bg-amber-100 px-1 text-[8px] font-semibold text-amber-700">
          auto off
        </span>
      )}

      {open && !disabled && (
        <div className="absolute right-0 top-full z-50 mt-1 w-60 rounded-lg border border-black/10 bg-white p-3 shadow-lg">
          <div className="text-xs text-black/70">
            Context <span className="font-semibold">{pct}%</span> used ·{' '}
            <span className="font-semibold">
              {formatTokens(usage?.free_tokens ?? 0)}
            </span>{' '}
            tokens free
          </div>
          <div className="mt-2 flex gap-2">
            <button
              onClick={confirm}
              className="flex-1 rounded-lg bg-brand px-3 py-1.5 text-xs font-semibold text-white hover:opacity-90"
            >
              Compact now
            </button>
            <button
              onClick={() => setOpen(false)}
              className="flex-1 rounded-lg border border-black/15 px-3 py-1.5 text-xs font-medium text-black/60 hover:bg-black/5"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
