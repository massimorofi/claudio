import { useState } from 'react'
import { ChevronDown, ChevronRight, CircleCheck, CircleX, Loader2 } from 'lucide-react'
import type { Item } from './chatState'

type ToolItem = Extract<Item, { kind: 'tool' }>

export default function ToolCard({ item }: { item: ToolItem }) {
  const [open, setOpen] = useState(false)

  const statusIcon = item.pending ? (
    <Loader2 size={14} className="animate-spin text-brand" />
  ) : item.ok ? (
    <CircleCheck size={14} className="text-green-600" />
  ) : (
    <CircleX size={14} className="text-red-500" />
  )

  return (
    <div className="my-1.5 rounded-lg border border-black/10 bg-white overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-medium text-black/70 hover:bg-black/[0.03]"
      >
        {statusIcon}
        <span className="font-mono text-brand">{item.name}</span>
        {item.pending && <span className="text-black/40">running…</span>}
        <span className="ml-auto text-black/30">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>
      {open && (
        <div className="border-t border-black/10 px-3 py-2 space-y-2 text-xs">
          <div>
            <div className="text-black/40 mb-0.5">Arguments</div>
            <pre className="scroll-thin overflow-x-auto rounded bg-black/[0.04] p-2 font-mono text-[11px] whitespace-pre-wrap break-all">
              {pretty(item.args)}
            </pre>
          </div>
          {item.output !== undefined && (
            <div>
              <div className="text-black/40 mb-0.5">Result</div>
              <pre
                className={`scroll-thin overflow-x-auto rounded p-2 font-mono text-[11px] whitespace-pre-wrap break-all ${
                  item.ok ? 'bg-black/[0.04]' : 'bg-red-50 text-red-700'
                }`}
              >
                {pretty(item.output)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function pretty(raw: unknown): string {
  if (raw == null || raw === '') return ''
  if (typeof raw !== 'string') {
    try {
      return JSON.stringify(raw, null, 2)
    } catch {
      return String(raw)
    }
  }
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return raw
  }
}
