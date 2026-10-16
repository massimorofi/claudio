import { Link } from 'react-router-dom'
import { CircleCheck, CircleX, PauseCircle, RotateCcw } from 'lucide-react'

interface CompletionStatusProps {
  status: 'completed' | 'failed' | 'interrupted'
  filesWritten: string[]
  commandsRun: number
  durationSeconds: number
  error?: string
  onRetry?: () => void
}

const MAX_FILES_SHOWN = 3

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
  error,
  onRetry,
}: CompletionStatusProps) {
  const shown = filesWritten.slice(0, MAX_FILES_SHOWN)
  const extra = filesWritten.length - shown.length

  const produced: string[] = []
  if (filesWritten.length > 0) {
    produced.push(
      `wrote ${shown.map((f) => (
        <Link key={f} to="/workspace" className="font-mono underline decoration-black/20 hover:decoration-brand">
          {f}
        </Link>
      )).join(', ')}${extra > 0 ? ` + ${extra} more` : ''}`,
    )
  }
  if (commandsRun > 0) produced.push(`${commandsRun} command${commandsRun === 1 ? '' : 's'} run`)
  const producedText = produced.length > 0 ? produced.join(' · ') : 'no output'

  const duration = `${durationSeconds.toFixed(1)}s`

  if (status === 'completed') {
    return (
      <div className="my-2 flex items-center gap-2 rounded-lg border border-green-200 bg-green-50 px-3 py-1.5 text-xs text-green-800">
        <CircleCheck size={14} className="shrink-0 text-green-600" />
        <span className="font-semibold">Completed</span>
        <span className="text-black/50">· {producedText} · {duration}</span>
      </div>
    )
  }

  if (status === 'failed') {
    return (
      <div className="my-2 flex items-center gap-2 rounded-lg border border-red-200 bg-red-50 px-3 py-1.5 text-xs text-red-800">
        <CircleX size={14} className="shrink-0 text-red-500" />
        <span className="font-semibold">Failed</span>
        {error && <span className="truncate text-black/60">· {error}</span>}
        <span className="text-black/50">· {duration}</span>
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
    <div className="my-2 flex items-center gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-1.5 text-xs text-amber-800">
      <PauseCircle size={14} className="shrink-0 text-amber-600" />
      <span className="font-semibold">Interrupted</span>
      <span className="text-black/50">· {duration}</span>
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
