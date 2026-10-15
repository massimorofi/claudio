import { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ChevronDown, ChevronRight, Plus, Send, Square, Trash2 } from 'lucide-react'
import { api } from '../../api/client'
import { saveActiveSession, getActiveSession } from '../../api/storage'
import type { SessionSummary } from '../../api/types'
import { useChat } from './useChat'
import ToolCard from './ToolCard'
import ApprovalCard from './ApprovalCard'

export default function ChatView() {
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [approvalMode, setApprovalMode] = useState('on_request')
  const [input, setInput] = useState('')
  const [showSessions, setShowSessions] = useState(false)
  const [restoring, setRestoring] = useState(true)
  const scrollRef = useRef<HTMLDivElement>(null)
  const queryClient = useQueryClient()
  const restoredRef = useRef(false)
  // Whether the transcript should keep following new streamed content. It is
  // turned off as soon as the user scrolls up, so streaming never yanks the
  // view away from what they are reading.
  const stickRef = useRef(true)
  const lastScrollTop = useRef(0)
  const [showJump, setShowJump] = useState(false)

  const { state, send, stop, approve, deny, clear, loadSession, resumeActiveRun } = useChat(
    sessionId,
    approvalMode,
    (id) => {
      setSessionId(id)
      saveActiveSession(id)
      queryClient.invalidateQueries({ queryKey: ['sessions'] })
    },
  )

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

  const onSend = () => {
    const text = input.trim()
    if (!text || state.streaming) return
    setInput('')
    // A new message should always bring the view back to the bottom.
    stickRef.current = true
    setShowJump(false)
    send(text)
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
      {/* Sessions drawer (desktop) */}
      <div className="hidden lg:flex w-64 flex-col border-r border-black/10 bg-white">
        <div className="p-3">
          <button
            onClick={newChat}
            className="flex w-full items-center justify-center gap-2 rounded-lg bg-brand px-3 py-2 text-sm font-semibold text-white"
          >
            <Plus size={16} /> New chat
          </button>
        </div>
        <div className="scroll-thin flex-1 overflow-y-auto px-2 pb-3 space-y-1">
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
            <label className="text-xs text-black/50">Approval</label>
            <select
              value={approvalMode}
              onChange={(e) => setApprovalMode(e.target.value)}
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

        {/* Mobile sessions dropdown */}
        {showSessions && (
          <div className="lg:hidden border-b border-black/10 bg-white p-2 max-h-56 overflow-y-auto scroll-thin space-y-1">
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
        )}

        {/* Transcript */}
        <div className="relative flex-1 min-h-0">
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
          {state.items.map((item) => {
            if (item.kind === 'user') {
              return (
                <div key={item.id} className="mb-3 flex justify-end">
                  <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-brand px-3.5 py-2 text-sm text-white whitespace-pre-wrap">
                    {item.text}
                  </div>
                </div>
              )
            }
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
            if (item.kind === 'tool') {
              return <ToolCard key={item.id} item={item} />
            }
            if (item.kind === 'approval') {
              return (
                <ApprovalCard
                  key={item.id}
                  item={item}
                  onApprove={approve}
                  onDeny={deny}
                  busy={false}
                />
              )
            }
            return null
          })}
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
          <div className="flex items-end gap-2">
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
                className="flex h-[42px] items-center gap-1.5 rounded-xl bg-red-500 px-4 text-sm font-semibold text-white"
              >
                <Square size={16} /> Stop
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
