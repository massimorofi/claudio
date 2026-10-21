import { ListChecks } from 'lucide-react'
import type { PlanItem } from './chatState'

interface PlanPanelProps {
  items: PlanItem[] | null
  streaming: boolean
}

const GLYPH: Record<PlanItem['status'], string> = {
  pending: '☐',
  in_progress: '◐',
  done: '☑',
}

/**
 * Bottom sidebar panel showing the agent's live todo list.
 *
 * The content is `state.plan`, written by the `todo_write` tool during a run and
 * restored from the session row's stored `todos` when a session is loaded, so
 * the panel survives a reload and shows the same list the agent is working from.
 */
export default function PlanPanel({ items, streaming }: PlanPanelProps) {
  const list = items ?? []
  const done = list.filter((i) => i.status === 'done').length

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-2 border-b border-black/10 px-3 py-2">
        <ListChecks size={14} className="text-black/50" />
        <span className="text-xs font-semibold uppercase tracking-wide text-black/50">
          Plan
        </span>
        {streaming && (
          <span className="inline-block h-1.5 w-1.5 rounded-full bg-green-500 motion-safe:animate-pulse" />
        )}
        {list.length > 0 && (
          <span className="ml-auto text-xs tabular-nums text-black/40">
            {done}/{list.length}
          </span>
        )}
      </div>

      <div className="scroll-thin min-h-0 flex-1 overflow-y-auto px-2 py-2">
        {list.length === 0 ? (
          <div className="px-1 py-2 text-xs leading-relaxed text-black/35">
            No plan yet. The agent posts one when it starts a multi-step task.
          </div>
        ) : (
          <ul className="space-y-1">
            {list.map((item, i) => (
              <li
                key={i}
                className={`flex items-start gap-2 px-1 py-0.5 text-sm ${
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
    </div>
  )
}
