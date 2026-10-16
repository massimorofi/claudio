import { useState } from 'react'
import { ChevronDown, ChevronRight } from 'lucide-react'
import type { PlanItem } from './chatState'

interface PlanTrackerProps {
  items: PlanItem[]
  streaming: boolean
}

const GLYPH: Record<PlanItem['status'], string> = {
  pending: '☐',
  in_progress: '◐',
  done: '☑',
}

/**
 * Pinned checklist of the agent's live todo list (Phase 2). Renders nothing when
 * the agent never called ``todo_write``.
 */
export default function PlanTracker({ items, streaming }: PlanTrackerProps) {
  const [open, setOpen] = useState(true)
  if (!items || items.length === 0) return null

  const done = items.filter((i) => i.status === 'done').length

  return (
    <div className="my-1.5 rounded-lg border border-black/10 bg-white overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-black/60 hover:bg-black/[0.03]"
      >
        <span className="font-medium">📋 Plan · {done}/{items.length} done</span>
        {streaming && (
          <span className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-green-500 motion-safe:animate-pulse" />
        )}
        <span className="ml-auto text-black/30">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>
      {open && (
        <ul className="border-t border-black/10 px-3 py-2 space-y-1">
          {items.map((item, i) => (
            <li
              key={i}
              className={`flex items-start gap-2 text-sm ${
                item.status === 'done' ? 'text-black/40 line-through' : 'text-black/75'
              }`}
            >
              <span
                className={`mt-0.5 shrink-0 ${
                  item.status === 'done'
                    ? 'text-green-600'
                    : item.status === 'in_progress'
                      ? 'text-brand'
                      : 'text-black/30'
                }`}
              >
                {GLYPH[item.status]}
              </span>
              <span>{item.content}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
