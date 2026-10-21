import { useState } from 'react'
import type { ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { CircleCheck, CircleX, PauseCircle, RotateCcw, Copy, Check } from 'lucide-react'

interface CompletionStatusProps {
  status: 'completed' | 'failed' | 'interrupted'
  filesWritten: string[]
  commandsRun: number
  durationSeconds: number
  inputTokens?: number
  outputTokens?: number
  replyText?: string
  error?: string
  onRetry?: () => void
}

const MAX_FILES_SHOWN = 3

/**
 * Collapse repeated writes down to each distinct file and how often it was
 * written, keeping first-seen order. The agent commonly re-writes one file
 * across many turns; listing it five times reads as noise.
 */
function tallyFiles(files: string[]): { name: string; count: number }[] {
  const order: string[] = []
  const counts = new Map<string, number>()
  for (const f of files) {
    if (!counts.has(f)) {
      order.push(f)
      counts.set(f, 0)
    }
    counts.set(f, (counts.get(f) ?? 0) + 1)
  }
  return order.map((name) => ({ name, count: counts.get(name) ?? 1 }))
}

/**
 * Always-visible completion bookend (Phase 5): states the outcome of every turn
 * — done / failed / interrupted — and what was produced, even when the assistant
 * emitted no prose. Status is carried by icon + word, never color alone.
 */
export default function CompletionStatus({
  status,
  filesWritten,
  commandsRun,
  durationSeconds,
  inputTokens = 0,
  outputTokens = 0,
  replyText,
  error,
  onRetry,
}: CompletionStatusProps) {
  const tallied = tallyFiles(filesWritten)
  const shown = tallied.slice(0, MAX_FILES_SHOWN)
  const extra = tallied.length - shown.length

  // Built as real JSX. Interpolating React elements into a template literal
  // stringifies them, which is how "wrote [object Object]" used to render.
  const wroteNode: ReactNode = (
    <>
      wrote{' '}
      {shown.map((f, i) => (
        <span key={`${f.name}-${i}`}>
          {i > 0 ? ', ' : ''}
          <Link
            to="/workspace"
            className="font-mono underline decoration-black/20 hover:decoration-brand"
          >
            {f.name}
            {f.count > 1 ? ` ×${f.count}` : ''}
          </Link>
        </span>
      ))}
      {extra > 0 ? ` + ${extra} more` : ''}
    </>
  )

  const parts: ReactNode[] = []
  if (tallied.length > 0) parts.push(wroteNode)
  if (commandsRun > 0) {
    parts.push(`${commandsRun} command${commandsRun === 1 ? '' : 's'} run`)
  }
  const producedNode: ReactNode =
    parts.length > 0 ? (
      parts.map((p, i) => (
        <span key={i}>
          {i > 0 ? ' · ' : ''}
          {p}
        </span>
      ))
    ) : (
      'no output'
    )

  const duration = `${durationSeconds.toFixed(1)}s`
  // Throughput: generated (output) tokens/sec and prompt (input) tokens/sec over
  // the turn's wall-clock. Guard the divide so a 0s turn reads 0, not Infinity.
  const genRate = durationSeconds > 0 ? outputTokens / durationSeconds : 0
  const promptRate = durationSeconds > 0 ? inputTokens / durationSeconds : 0

  if (status === 'completed') {
    return (
      <div className="my-2 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-green-200 bg-green-50 px-3 py-1.5 text-xs text-green-800">
        <CircleCheck size={14} className="shrink-0 text-green-600" />
        <span className="font-semibold">Completed</span>
        <span className="text-black/50">
          · {producedNode} · {duration}
        </span>
        <span className="text-black/50">
          · {genRate.toFixed(1)} tok/s generated · {promptRate.toFixed(1)} tok/s prompt
        </span>
        {replyText && replyText.trim() ? <CopyButton text={replyText} /> : null}
      </div>
    )
  }

  if (status === 'failed') {
    return (
      <div className="my-2 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-xs text-red-800">
        <CircleX size={14} className="shrink-0 text-red-500" />
        <span className="font-semibold">Failed</span>
        {error && <span className="truncate text-black/60">· {error}</span>}
        <span className="text-black/50">· {duration}</span>
        {replyText && replyText.trim() ? <CopyButton text={replyText} /> : null}
        {onRetry && (
          <button
            onClick={onRetry}
            className="ml-auto flex shrink-0 items-center gap-1 rounded-md bg-red-600 px-2.5 py-1 font-semibold text-white hover:opacity-90"
          >
            <RotateCcw size={12} /> Retry
          </button>
        )}
      </div>
    )
  }

  // interrupted
  return (
    <div className="my-2 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-800">
      <PauseCircle size={14} className="shrink-0 text-amber-600" />
      <span className="font-semibold">Interrupted</span>
      <span className="text-black/50">· {duration}</span>
      {replyText && replyText.trim() ? <CopyButton text={replyText} /> : null}
      {onRetry && (
        <button
          onClick={onRetry}
          className="ml-auto flex shrink-0 items-center gap-1 rounded-md bg-amber-600 px-2.5 py-1 font-semibold text-white hover:opacity-90"
        >
          <RotateCcw size={12} /> Retry
        </button>
      )}
    </div>
  )
}

/**
 * Copies the assistant's reply (raw markdown) to the clipboard. Uses the async
 * Clipboard API where available and falls back to a hidden textarea +
 * execCommand for non-secure contexts, then shows a brief "Copied" confirmation.
 */
function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)

  const doCopy = async () => {
    let ok = false
    try {
      if (navigator.clipboard && window.isSecureContext) {
        await navigator.clipboard.writeText(text)
        ok = true
      } else {
        const ta = document.createElement('textarea')
        ta.value = text
        ta.style.position = 'fixed'
        ta.style.opacity = '0'
        document.body.appendChild(ta)
        ta.focus()
        ta.select()
        ok = document.execCommand('copy')
        document.body.removeChild(ta)
      }
    } catch {
      ok = false
    }
    if (ok) {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1800)
    }
  }

  return (
    <button
      onClick={doCopy}
      title="Copy reply as Markdown"
      className={`ml-auto flex shrink-0 items-center gap-1 rounded-md border px-2 py-1 font-medium transition-colors ${
        copied
          ? 'border-green-300 bg-green-100 text-green-700'
          : 'border-black/15 bg-white text-black/60 hover:bg-black/5'
      }`}
    >
      {copied ? <Check size={13} /> : <Copy size={13} />}
      {copied ? 'Copied' : 'Copy'}
    </button>
  )
}
