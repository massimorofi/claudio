import type { Item } from './chatState'

export type ToolItem = Extract<Item, { kind: 'tool' }>
export type ApprovalItem = Extract<Item, { kind: 'approval' }>
export type TurnStatusItem = Extract<Item, { kind: 'turn_status' }>

/**
 * One-line summary of a tool call's arguments: the first three ``k=v`` pairs.
 * Mirrors the strixper ``_tool_detail``. Non-JSON or empty args yield ''.
 */
export function toolDetail(args: string): string {
  let parsed: unknown
  try {
    parsed = args ? JSON.parse(args) : {}
  } catch {
    return ''
  }
  if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) {
    const entries = Object.entries(parsed as Record<string, unknown>).slice(0, 3)
    return entries.map(([k, v]) => `${k}=${formatValue(v)}`).join(', ')
  }
  return ''
}

function formatValue(v: unknown): string {
  if (v === null || v === undefined) return String(v)
  if (typeof v === 'string') return truncate(v, 60)
  if (typeof v === 'number' || typeof v === 'boolean') return String(v)
  try {
    return truncate(JSON.stringify(v), 60)
  } catch {
    return String(v)
  }
}

function truncate(s: string, max: number): string {
  if (s.length <= max) return s
  return s.slice(0, max - 1) + '…'
}

/** A step in the workflow trace: a tool call or an approval, in original order. */
export type StepItem = ToolItem | ApprovalItem

/**
 * A turn: everything after a ``user`` item up to (not including) the next one.
 * ``flow`` keeps reasoning/assistant in order; ``steps`` holds the tool and
 * approval items interleaved in original order (the numbered trace);
 * ``turnStatus`` is the completion bookend if present.
 */
export interface Turn {
  user: Extract<Item, { kind: 'user' }> | null
  flow: Array<Extract<Item, { kind: 'reasoning' } | { kind: 'assistant' }>>
  steps: StepItem[]
  turnStatus: TurnStatusItem | null
}

export function groupIntoTurns(items: Item[]): Turn[] {
  const turns: Turn[] = []
  let current: Turn | null = null
  for (const it of items) {
    if (it.kind === 'user') {
      current = { user: it, flow: [], steps: [], turnStatus: null }
      turns.push(current)
      continue
    }
    if (!current) {
      current = { user: null, flow: [], steps: [], turnStatus: null }
      turns.push(current)
    }
    if (it.kind === 'tool' || it.kind === 'approval') current.steps.push(it)
    else if (it.kind === 'turn_status') current.turnStatus = it
    else current.flow.push(it)
  }
  return turns
}
