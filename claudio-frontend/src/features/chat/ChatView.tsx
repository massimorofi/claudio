import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import {
  ChevronDown,
  ChevronRight,
  Loader2,
  Paperclip,
  PanelLeftClose,
  PanelLeftOpen,
  Plus,
  Send,
  Trash2,
  X,
} from 'lucide-react'
import { api, uploadFile } from '../../api/client'
import { saveActiveSession, getActiveSession } from '../../api/storage'
import type { FileUploadResponse, SessionSummary } from '../../api/types'
import { useChat } from './useChat'
import { groupIntoTurns } from './workflow'
import WorkflowPanel from './WorkflowPanel'
import PlanPanel from './PlanPanel'
import ContextGauge from './ContextGauge'

// localStorage key holding the sidebar split ratio (plan panel height, in %).
const PLAN_SPLIT_KEY = 'claudio.planSplitPct'
const SPLIT_MIN_PCT = 20
const SPLIT_MAX_PCT = 80
const SPLIT_DEFAULT_PCT = 45
// localStorage key remembering whether the chat sidebar is collapsed.
const CHAT_SIDEBAR_COLLAPSED_KEY = 'claudio.chatSidebar.collapsed'
import CompletionStatus from './CompletionStatus'
import ApprovalCard from './ApprovalCard'

export default function ChatView() {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [approvalMode, setApprovalMode] = useState('on_request')
  // Whether the user has explicitly chosen an approval mode in this session.
  // Until they do, the select mirrors the agent's configured approval_mode so
  // the chat never silently disagrees with the backend's own setting.
  const approvalTouchedRef = useRef(false)
  const [input, setInput] = useState('')
  const [showSessions, setShowSessions] = useState(false)
  const [restoring, setRestoring] = useState(true)
  const [toast, setToast] = useState<string | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()
  const restoredRef = useRef(false)
  // Files uploaded for the next message. Each entry is the upload response; the
  // workspace-relative `path` is what the chat request references.
  const [attached, setAttached] = useState<FileUploadResponse[]>([])
  const [uploading, setUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  // Whether the transcript should keep following new streamed content. It is
  // turned off as soon as the user scrolls up, so streaming never yanks the
  // view away from what they are reading.
  const stickRef = useRef(true)
  const lastScrollTop = useRef(0)
  const [showJump, setShowJump] = useState(false)
  // Vertical split of the sidebar: sessions on top, plan below. The divider is
  // draggable and the chosen ratio is remembered across reloads. Stored as the
  // plan panel's share of the split, in percent.
  const [planPct, setPlanPct] = useState<number>(() => {
    try {
      const saved = Number(localStorage.getItem(PLAN_SPLIT_KEY))
      if (Number.isFinite(saved) && saved >= SPLIT_MIN_PCT && saved <= SPLIT_MAX_PCT) {
        return saved
      }
    } catch {
      /* storage unavailable (private mode); fall through to the default */
    }
    return SPLIT_DEFAULT_PCT
  })
  const splitRef = useRef<HTMLDivElement>(null)
  const draggingRef = useRef(false)
  // Whether the chat sidebar is collapsed to a thin strip, mirroring the app's
  // main navigation sidebar. The choice is remembered across reloads.
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem(CHAT_SIDEBAR_COLLAPSED_KEY) === '1'
    } catch {
      return false
    }
  })

  const toggleCollapsed = () =>
    setCollapsed((c) => {
      const next = !c
      try {
        localStorage.setItem(CHAT_SIDEBAR_COLLAPSED_KEY, next ? '1' : '0')
      } catch {
        /* storage may be unavailable; collapse still works for the session */
      }
      return next
    })

  useEffect(() => {
    try {
      localStorage.setItem(PLAN_SPLIT_KEY, String(planPct))
    } catch {
      /* non-fatal: the split just won't be remembered */
    }
  }, [planPct])

  const onSplitDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault()
    draggingRef.current = true
    e.currentTarget.setPointerCapture(e.pointerId)
    // Stop the drag from selecting transcript text while it is in flight.
    document.body.style.userSelect = 'none'
  }

  const onSplitMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!draggingRef.current) return
    const box = splitRef.current?.getBoundingClientRect()
    if (!box || box.height === 0) return
    // The plan panel sits at the bottom, so its share grows as the pointer moves up.
    const pct = ((box.bottom - e.clientY) / box.height) * 100
    setPlanPct(Math.min(SPLIT_MAX_PCT, Math.max(SPLIT_MIN_PCT, pct)))
  }

  const onSplitUp = (e: React.PointerEvent<HTMLDivElement>) => {
    draggingRef.current = false
    document.body.style.userSelect = ''
    try {
      e.currentTarget.releasePointerCapture(e.pointerId)
    } catch {
      /* capture may already be gone; nothing to release */
    }
  }

  const {
    state,
    send,
    stop,
    approve,
    deny,
    clear,
    loadSession,
    resumeActiveRun,
    compactSession,
  } = useChat(sessionId, approvalMode, (id) => {
    setSessionId(id)
    saveActiveSession(id)
    queryClient.invalidateQueries({ queryKey: ['sessions'] })
  })

  const turns = useMemo(() => groupIntoTurns(state.items), [state.items])

  const showToast = (msg: string) => {
    setToast(msg)
    window.setTimeout(() => setToast(null), 4000)
  }

  const handleCompact = async () => {
    const prev = state.context?.used_tokens ?? 0
    const res = await compactSession()
    if (res.ok && res.requested) {
      // A run is active: the request is queued and the running loop will
      // compact at the next turn boundary. The real token delta arrives with
      // the run_compacted event, which fires its own toast.
      showToast('Compaction scheduled — applies at the next step')
    } else if (res.ok) {
      const now = res.used_tokens ?? 0
      showToast(`Compacted ${prev.toLocaleString()} → ${now.toLocaleString()} tokens`)
    } else {
      showToast(res.error ?? 'Compaction failed')
    }
  }

  // Fire a one-shot toast each time a mid-run compaction completes.
  useEffect(() => {
    if (state.compactedCount > 0) showToast('Context compacted')
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state.compactedCount])

  const handleRetry = () => {
    if (state.streaming) return
    // Prefer the in-session last message; fall back to the last user item in the
    // transcript so Retry still works after a page reload (where lastUserText
    // is not carried over).
    let text = state.lastUserText
    if (!text) {
      for (let i = state.items.length - 1; i >= 0; i--) {
        const it = state.items[i]
        if (it.kind === 'user') {
          text = it.text
          break
        }
      }
    }
    if (!text) return
    stickRef.current = true
    setShowJump(false)
    send(text)
  }

  // On first mount, reopen the last active session and reattach to any run that
  // is still in flight (e.g. started before a page reload).
  useEffect(() => {
    if (restoredRef.current) return
    restoredRef.current = true
    const saved = getActiveSession()
    if (!saved) {
      setRestoring(false)
      return
    }
    ;(async () => {
      try {
        await loadSession(saved)
        setSessionId(saved)
        await resumeActiveRun(saved)
      } catch {
        /* session may have been deleted; start fresh */
        saveActiveSession(null)
      } finally {
        setRestoring(false)
      }
    })()
  }, [loadSession, resumeActiveRun])

  const sessionsQ = useQuery({
    queryKey: ['sessions'],
    queryFn: () => api.get<{ sessions: SessionSummary[] }>('/sessions'),
  })

  // Mirror the agent's configured approval mode into the chat's selector on
  // mount, so "off" in the Agent config actually shows as "off" here instead of
  // the chat defaulting to on_request and overriding it.
  useEffect(() => {
    if (approvalTouchedRef.current) return
    api
      .get<{ config?: { agent?: { approval_mode?: string } } }>('/config')
      .then((res) => {
        const mode = res?.config?.agent?.approval_mode
        if (mode && !approvalTouchedRef.current) setApprovalMode(mode)
      })
      .catch(() => undefined)
  }, [])

  const isAtBottom = () => {
    const el = scrollRef.current
    if (!el) return true
    return el.scrollHeight - el.scrollTop - el.clientHeight <= 80
  }

  const handleScroll = () => {
    const el = scrollRef.current
    if (!el) return
    const cur = el.scrollTop
    const atBottom = el.scrollHeight - cur - el.clientHeight <= 80
    if (cur < lastScrollTop.current - 1) {
      // Any upward scroll means the user wants to read above — stop following.
      stickRef.current = false
    } else if (atBottom) {
      stickRef.current = true
    }
    lastScrollTop.current = cur
    setShowJump(!atBottom)
  }

  const scrollToBottom = () => {
    const el = scrollRef.current
    if (el) el.scrollTop = el.scrollHeight
    stickRef.current = true
    setShowJump(false)
  }

  // Follow new streamed content only while the user is pinned to the bottom.
  useEffect(() => {
    if (stickRef.current) {
      const el = scrollRef.current
      if (el) el.scrollTop = el.scrollHeight
    }
  }, [state.items])

  // An approval needs the user's attention: bring it into view regardless of
  // where they had scrolled, and re-pin so the follow-the-stream resumes.
  useEffect(() => {
    if (state.pendingApproval) {
      const el = scrollRef.current
      if (el) el.scrollTop = el.scrollHeight
      stickRef.current = true
      setShowJump(false)
    }
  }, [state.pendingApproval])

  const onSend = () => {
    const text = input.trim()
    if (!text || state.streaming) return
    const paths = attached.map((f) => f.path)
    setInput('')
    setAttached([])
    // A new message should always bring the view back to the bottom.
    stickRef.current = true
    setShowJump(false)
    send(text, paths)
  }

  const openFilePicker = () => {
    if (state.streaming || uploading) return
    fileInputRef.current?.click()
  }

  const onFilesSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? [])
    // Reset immediately so selecting the same file again still fires change.
    if (fileInputRef.current) fileInputRef.current.value = ''
    if (!files.length) return
    setUploading(true)
    let failed = 0
    for (const file of files) {
      try {
        const res = await uploadFile<FileUploadResponse>('/files/upload', file)
        setAttached((prev) => [...prev, res])
      } catch (err) {
        failed += 1
        const msg = err instanceof Error ? err.message : 'upload failed'
        showToast(`Could not attach ${file.name}: ${msg}`)
      }
    }
    setUploading(false)
    if (failed === 0 && files.length > 0) {
      showToast(`${files.length} file(s) attached`)
    }
  }

  const removeAttachment = (path: string) => {
    setAttached((prev) => prev.filter((f) => f.path !== path))
  }

  const onKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      onSend()
    }
  }

  const newChat = () => {
    clear()
    setSessionId(null)
    saveActiveSession(null)
    stickRef.current = true
    setShowJump(false)
  }

  const openSession = async (id: string) => {
    setShowSessions(false)
    await loadSession(id)
    setSessionId(id)
    saveActiveSession(id)
    await resumeActiveRun(id)
    stickRef.current = true
    setShowJump(false)
  }

  const deleteSession = async (id: string) => {
    await api.delete(`/sessions/${id}`)
    if (id === sessionId) newChat()
    queryClient.invalidateQueries({ queryKey: ['sessions'] })
  }

  return (
    <div className="flex h-full">
      {/* Sidebar (desktop): sessions on top, current plan below. Collapsible
          like the app's main navigation sidebar; the choice is remembered. */}
      <div
        className={`hidden lg:flex flex-col border-r border-black/10 bg-white transition-[width] duration-200 ${
          collapsed ? 'w-14' : 'w-72'
        }`}
      >
        {collapsed ? (
          <div className="flex flex-col items-center gap-2 p-2">
            <button
              onClick={toggleCollapsed}
              title="Expand sidebar"
              className="rounded-lg p-2 text-black/40 hover:bg-black/5 hover:text-black/70"
            >
              <PanelLeftOpen size={18} />
            </button>
            <button
              onClick={newChat}
              title="New chat"
              className="rounded-lg p-2 text-black/50 hover:bg-black/5 hover:text-black/70"
            >
              <Plus size={18} />
            </button>
          </div>
        ) : (
          <>
            <div className="flex items-center gap-2 p-3">
              <button
                onClick={newChat}
                className="flex flex-1 items-center justify-center gap-2 rounded-lg bg-brand px-3 py-2 text-sm font-semibold text-white"
              >
                <Plus size={16} /> New chat
              </button>
              <button
                onClick={toggleCollapsed}
                title="Collapse sidebar"
                className="rounded-lg p-1.5 text-black/40 hover:bg-black/5 hover:text-black/70"
              >
                <PanelLeftClose size={18} />
              </button>
            </div>

            <div ref={splitRef} className="flex min-h-0 flex-1 flex-col">
              {/* Top panel: session list */}
              <div
                className="scroll-thin min-h-0 overflow-y-auto px-2 pb-3 space-y-1"
                style={{ flexGrow: 100 - planPct }}
              >
                {(sessionsQ.data?.sessions ?? []).map((s) => (
                  <div
                    key={s.id}
                    className={`group flex items-center gap-2 rounded-lg px-3 py-2 text-sm cursor-pointer ${
                      s.id === sessionId ? 'bg-brand/10 text-brand' : 'hover:bg-black/5'
                    }`}
                    onClick={() => openSession(s.id)}
                  >
                    <span className="flex-1 truncate">{s.title || 'Untitled'}</span>
                    <button
                      onClick={(e) => {
                        e.stopPropagation()
                        deleteSession(s.id)
                      }}
                      className="opacity-0 group-hover:opacity-100 text-black/30 hover:text-red-500"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                ))}
              </div>

              {/* Draggable divider */}
              <div
                onPointerDown={onSplitDown}
                onPointerMove={onSplitMove}
                onPointerUp={onSplitUp}
                onPointerCancel={onSplitUp}
                className="relative h-2 shrink-0 cursor-row-resize bg-black/[0.03] hover:bg-brand/20"
                title="Drag to resize"
              >
                <div className="absolute left-1/2 top-1/2 h-0.5 w-10 -translate-x-1/2 -translate-y-1/2 rounded-full bg-black/15" />
              </div>

              {/* Bottom panel: current plan */}
              <div
                className="min-h-0 bg-black/[0.015]"
                style={{ flexGrow: planPct }}
              >
                <PlanPanel items={state.plan} streaming={state.streaming} />
              </div>
            </div>
          </>
        )}
      </div>

      {/* Chat column */}
      <div className="flex flex-1 flex-col min-w-0">
        {/* Header */}
        <div className="flex items-center gap-2 border-b border-black/10 bg-white px-4 py-2.5">
          <div className="lg:hidden">
            <button
              onClick={() => setShowSessions((v) => !v)}
              className="flex items-center gap-1 rounded-lg px-2 py-1 text-sm text-black/60 hover:bg-black/5"
            >
              {sessionId ? 'Session' : 'New chat'}
              {showSessions ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            </button>
          </div>
          <div className="hidden lg:block text-sm font-medium text-black/70">
            {sessionId ? 'Active session' : 'New conversation'}
          </div>
          <div className="ml-auto flex items-center gap-2">
            <ContextGauge
              usage={state.context}
              disabled={state.streaming}
              onCompact={handleCompact}
            />
            <label className="text-xs text-black/50">Approval</label>
            <select
              value={approvalMode}
              onChange={(e) => {
                const mode = e.target.value
                approvalTouchedRef.current = true
                setApprovalMode(mode)
                // Switching to "off" while a run is paused on an approval:
                // clear the pending approval so the run proceeds, honoring the
                // user's intent that nothing should be gated.
                if (mode === 'off' && state.pendingApproval) {
                  approve(state.pendingApproval.approval_id)
                }
              }}
              className="rounded-md border border-black/15 bg-white px-2 py-1 text-xs"
            >
              <option value="on_request">On request</option>
              <option value="always">Always ask</option>
              <option value="off">Off</option>
            </select>
            <button
              onClick={newChat}
              className="rounded-lg p-1.5 text-black/50 hover:bg-black/5 lg:hidden"
              title="New chat"
            >
              <Plus size={18} />
            </button>
          </div>
        </div>

        {/* Mobile sessions + plan dropdown */}
        {showSessions && (
          <div className="lg:hidden border-b border-black/10 bg-white">
            <div className="scroll-thin max-h-56 overflow-y-auto p-2 space-y-1">
              {(sessionsQ.data?.sessions ?? []).map((s) => (
                <div
                  key={s.id}
                  className={`flex items-center gap-2 rounded-lg px-3 py-2 text-sm cursor-pointer ${
                    s.id === sessionId ? 'bg-brand/10 text-brand' : 'hover:bg-black/5'
                  }`}
                  onClick={() => openSession(s.id)}
                >
                  <span className="flex-1 truncate">{s.title || 'Untitled'}</span>
                  <button
                    onClick={(e) => {
                      e.stopPropagation()
                      deleteSession(s.id)
                    }}
                    className="text-black/30 hover:text-red-500"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
            {state.plan && state.plan.length > 0 && (
              <div className="h-56 border-t border-black/10 bg-black/[0.015]">
                <PlanPanel items={state.plan} streaming={state.streaming} />
              </div>
            )}
          </div>
        )}

        {/* Transcript */}
        <div className="relative flex-1 min-h-0">
          {state.reconnecting && (
            <div className="absolute inset-x-0 top-0 z-20 flex items-center justify-center gap-2 bg-amber-100/95 px-4 py-1.5 text-xs font-medium text-amber-800 shadow-sm">
              <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-amber-500" />
              Reconnecting… the run is still going in the background
            </div>
          )}
          <div
            ref={scrollRef}
            onScroll={handleScroll}
            className="scroll-thin h-full overflow-y-auto px-4 py-4"
          >
          {state.items.length === 0 && (
            <div className="flex h-full items-center justify-center text-sm text-black/40">
              {restoring ? 'Restoring session…' : 'Ask Claudio anything.'}
            </div>
          )}
          {turns.map((turn, ti) => {
            return (
              <div key={ti}>
                {turn.user && (
                  <div className="mb-3 flex justify-end">
                    <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-brand px-3.5 py-2 text-sm text-white whitespace-pre-wrap">
                      {turn.user.text}
                    </div>
                  </div>
                )}
                {turn.flow.map((item) => {
                  if (item.kind === 'assistant') {
                    return (
                      <div key={item.id} className="mb-3 flex justify-start">
                        <div className="md-body max-w-[90%] rounded-2xl rounded-bl-sm bg-white border border-black/10 px-3.5 py-2 text-sm">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{item.text}</ReactMarkdown>
                        </div>
                      </div>
                    )
                  }
                  if (item.kind === 'reasoning') {
                    return <ReasoningBlock key={item.id} text={item.text} />
                  }
                  return null
                })}
                {turn.steps.length > 0 && (
                  <WorkflowPanel
                    steps={turn.steps}
                    streaming={state.streaming}
                    onApprove={approve}
                    onDeny={deny}
                  />
                )}
                {turn.turnStatus && (
                  <CompletionStatus
                    status={turn.turnStatus.status}
                    filesWritten={turn.turnStatus.filesWritten}
                    commandsRun={turn.turnStatus.commandsRun}
                    durationSeconds={turn.turnStatus.durationSeconds}
                    inputTokens={turn.turnStatus.inputTokens}
                    outputTokens={turn.turnStatus.outputTokens}
                    replyText={turn.flow
                      .filter((f) => f.kind === 'assistant')
                      .map((f) => f.text)
                      .join('\n\n')}
                    error={turn.turnStatus.error}
                    onRetry={
                      turn.turnStatus.status === 'completed' ? undefined : handleRetry
                    }
                  />
                )}
              </div>
            )
          })}
          {state.pendingApproval && (
            <div className="mt-2">
              <ApprovalCard
                item={{
                  kind: 'approval',
                  id: `live-${state.pendingApproval.approval_id}`,
                  approvalId: state.pendingApproval.approval_id,
                  toolName: state.pendingApproval.tool,
                  args: state.pendingApproval.arguments,
                  reason: state.pendingApproval.reason,
                }}
                onApprove={approve}
                onDeny={deny}
                busy={false}
              />
            </div>
          )}
          {state.error && (
            <div className="my-2 rounded-lg border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              {state.error}
            </div>
          )}
          </div>
          {showJump && (
            <button
              onClick={scrollToBottom}
              className="absolute bottom-4 left-1/2 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-black/10 bg-white px-3 py-1.5 text-xs font-medium text-black/70 shadow-md hover:bg-black/5"
            >
              <ChevronDown size={14} /> Scroll to bottom
            </button>
          )}
        </div>

        {/* Composer */}
        <div className="border-t border-black/10 bg-white p-3 safe-area-bottom">
          {attached.length > 0 && (
            <div className="mb-2 flex flex-wrap gap-1.5">
              {attached.map((f) => (
                <span
                  key={f.path}
                  className="flex items-center gap-1.5 rounded-full border border-black/10 bg-black/5 px-2.5 py-1 text-xs text-black/70"
                  title={f.note || `${f.kind}, ${f.size} bytes`}
                >
                  <Paperclip size={12} />
                  <span className="max-w-[160px] truncate">{f.name}</span>
                  {!f.extracted && f.note && (
                    <span className="text-amber-600">· {f.kind}</span>
                  )}
                  <button
                    onClick={() => removeAttachment(f.path)}
                    className="text-black/30 hover:text-red-500"
                    title="Remove"
                  >
                    <X size={12} />
                  </button>
                </span>
              ))}
            </div>
          )}
          <div className="flex items-end gap-2">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              className="hidden"
              onChange={onFilesSelected}
            />
            <button
              onClick={openFilePicker}
              disabled={state.streaming || uploading}
              className="flex h-[42px] w-[42px] items-center justify-center rounded-xl border border-black/15 bg-white text-black/60 hover:bg-black/5 disabled:opacity-40"
              title="Attach files"
            >
              <Paperclip size={18} />
            </button>
            <textarea
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={onKeyDown}
              rows={1}
              placeholder="Message Claudio…"
              className="scroll-thin max-h-32 min-h-[42px] flex-1 resize-none rounded-xl border border-black/15 bg-paper px-3 py-2 text-sm outline-none focus:border-brand"
            />
            {state.streaming ? (
              <button
                onClick={stop}
                className="flex h-[42px] items-center gap-1.5 rounded-xl bg-red-500 px-4 text-sm font-semibold text-white hover:bg-red-600"
              >
                <Loader2 size={16} className="animate-spin" /> Stop
              </button>
            ) : (
              <button
                onClick={onSend}
                disabled={!input.trim()}
                className="flex h-[42px] items-center gap-1.5 rounded-xl bg-brand px-4 text-sm font-semibold text-white disabled:opacity-40"
              >
                <Send size={16} /> Send
              </button>
            )}
          </div>
        </div>
      </div>

      {toast && (
        <div className="fixed bottom-6 left-1/2 z-50 -translate-x-1/2 rounded-lg bg-black/80 px-4 py-2 text-sm text-white shadow-lg">
          {toast}
        </div>
      )}
    </div>
  )
}

function ReasoningBlock({ text }: { text: string }) {
  const [open, setOpen] = useState(false)
  return (
    <div className="my-1.5 rounded-lg border border-black/10 bg-black/[0.02] overflow-hidden">
      <button
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-xs text-black/50 hover:bg-black/[0.03]"
      >
        <span className="font-medium italic">Reasoning</span>
        <span className="ml-auto text-black/30">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </span>
      </button>
      {open && (
        <div className="scroll-thin max-h-60 overflow-auto border-t border-black/10 px-3 py-2 text-xs italic text-black/60 whitespace-pre-wrap">
          {text}
        </div>
      )}
    </div>
  )
}
