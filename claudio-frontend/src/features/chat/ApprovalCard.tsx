import { ShieldAlert, Check, X } from 'lucide-react'
import type { Item } from './chatState'

type ApprovalItem = Extract<Item, { kind: 'approval' }>

export default function ApprovalCard({
  item,
  onApprove,
  onDeny,
  busy,
}: {
  item: ApprovalItem
  onApprove: (id: string) => void
  onDeny: (id: string) => void
  busy: boolean
}) {
  const resolved = item.resolved

  return (
    <div
      className={`my-2 rounded-xl border p-3 ${
        resolved
          ? 'border-black/10 bg-black/[0.02]'
          : 'border-amber-300 bg-amber-50'
      }`}
    >
      <div className="flex items-center gap-2 text-sm font-semibold text-amber-800">
        <ShieldAlert size={16} />
        Approval required
      </div>
      <div className="mt-1 text-xs text-black/70">
        Tool <span className="font-mono font-semibold">{item.toolName}</span>
        {item.reason ? ` — ${item.reason}` : ''}
      </div>
      <pre className="scroll-thin mt-2 max-h-40 overflow-auto rounded bg-white p-2 font-mono text-[11px] whitespace-pre-wrap break-all border border-black/10">
        {pretty(item.args)}
      </pre>
      {resolved ? (
        <div className="mt-2 text-xs font-medium text-black/50">
          {resolved === 'approved' ? 'Approved' : 'Denied'}
        </div>
      ) : (
        <div className="mt-3 flex gap-2">
          <button
            disabled={busy}
            onClick={() => onApprove(item.approvalId)}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-green-600 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            <Check size={16} /> Approve
          </button>
          <button
            disabled={busy}
            onClick={() => onDeny(item.approvalId)}
            className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-red-500 px-3 py-2 text-sm font-semibold text-white disabled:opacity-50"
          >
            <X size={16} /> Deny
          </button>
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
