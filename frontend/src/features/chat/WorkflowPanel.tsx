import { useState } from 'react'
import {
  ChevronDown,
  ChevronRight,
  CircleCheck,
  CircleX,
  Loader2,
  ShieldAlert,
} from 'lucide-react'
import type { StepItem, ToolItem, ApprovalItem } from './workflow'
import { toolDetail } from './workflow'
import ApprovalCard from './ApprovalCard'

interface WorkflowPanelProps {
  steps: StepItem[]
  streaming: boolean
  onApprove: (id: string) => void
  onDeny: (id: string) => void
}

/**
 * Consolidated per-turn tool-call trace: one collapsible numbered panel instead
 * of a scattered card per call (strixper parity). Approvals render inline as
 * steps so the numbered trace stays contiguous.
 */
export default function WorkflowPanel({
  steps,
  streaming,
  onApprove,
  onDeny,
}: WorkflowPanelProps) {
  const [open, setOpen] = useState(false)
  if (!steps || steps.length === 0) return null

  return (
    <div className="my-1.5 rounded-lg border border-black/10 bg-black/[0.02] overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-black/60 hover:bg-black/[0.03]"
      >
        <span className="font-medium">🔧 Workflow ({steps.length} step{steps.length === 1 ? '' : 's'})</span>
        <span className="ml-auto text-black/30">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>
      {open && (
        <div className="border-t border-black/10 px-3 py-2">
          <ol className="space-y-1">
            {steps.map((step, i) => (
              <StepRow
                key={step.id}
                index={i + 1}
                step={step}
                onApprove={onApprove}
                onDeny={onDeny}
              />
            ))}
          </ol>
          {streaming && (
            <div className="mt-1.5 text-xs italic text-black/40">Working…</div>
          )}
        </div>
      )}
    </div>
  )
}

function StepRow({
  index,
  step,
  onApprove,
  onDeny,
}: {
  index: number
  step: StepItem
  onApprove: (id: string) => void
  onDeny: (id: string) => void
}) {
  const [expanded, setExpanded] = useState(false)

  if (step.kind === 'approval') {
    return <ApprovalStep index={index} step={step} onApprove={onApprove} onDeny={onDeny} />
  }

  const tool = step as ToolItem
  const statusIcon = tool.pending ? (
    <Loader2 size={13} className="animate-spin text-brand" />
  ) : tool.ok ? (
    <CircleCheck size={13} className="text-green-600" />
  ) : (
    <CircleX size={13} className="text-red-500" />
  )
  const detail = toolDetail(tool.args)

  return (
    <li className="text-xs">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-black/[0.03]"
      >
        <span className="w-5 shrink-0 text-black/40 tabular-nums">{index}.</span>
        {statusIcon}
        <span className="font-mono text-brand">{tool.name}</span>
        {detail && <span className="truncate text-black/40">({detail})</span>}
        <span className="ml-auto shrink-0 text-black/25">
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
      </button>
      {expanded && (
        <div className="mt-1 space-y-2 rounded bg-black/[0.03] px-2 py-2">
          <div>
            <div className="text-black/40 mb-0.5">Arguments</div>
            <pre className="scroll-thin overflow-x-auto rounded bg-black/[0.04] p-2 font-mono text-[11px] whitespace-pre-wrap break-all">
              {pretty(tool.args)}
            </pre>
          </div>
          {tool.output !== undefined && (
            <div>
              <div className="text-black/40 mb-0.5">Result</div>
              <pre
                className={`scroll-thin overflow-x-auto rounded p-2 font-mono text-[11px] whitespace-pre-wrap break-all ${
                  tool.ok ? 'bg-black/[0.04]' : 'bg-red-50 text-red-700'
                }`}
              >
                {pretty(tool.output)}
              </pre>
            </div>
          )}
        </div>
      )}
    </li>
  )
}

function ApprovalStep({
  index,
  step,
  onApprove,
  onDeny,
}: {
  index: number
  step: ApprovalItem
  onApprove: (id: string) => void
  onDeny: (id: string) => void
}) {
  const [expanded, setExpanded] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const handle = (fn: (id: string) => void) => (id: string) => {
    if (submitting) return
    setSubmitting(true)
    fn(id)
  }
  const statusIcon = step.resolved ? (
    step.resolved === 'approved' ? (
      <CircleCheck size={13} className="text-green-600" />
    ) : (
      <CircleX size={13} className="text-red-500" />
    )
  ) : (
    <ShieldAlert size={13} className="text-amber-500" />
  )

  return (
    <li className="text-xs">
      <button
        onClick={() => setExpanded((e) => !e)}
        className="flex w-full items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-black/[0.03]"
      >
        <span className="w-5 shrink-0 text-black/40 tabular-nums">{index}.</span>
        {statusIcon}
        <span className="font-mono text-brand">{step.toolName}</span>
        <span className="text-black/40">
          {step.resolved === 'approved'
            ? '(approved)'
            : step.resolved === 'denied'
              ? '(denied)'
              : '(awaiting approval)'}
        </span>
        <span className="ml-auto shrink-0 text-black/25">
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
      </button>
      {expanded && (
        <div className="mt-1">
          <ApprovalCard
            item={step}
            onApprove={handle(onApprove)}
            onDeny={handle(onDeny)}
            busy={submitting}
          />
        </div>
      )}
    </li>
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
