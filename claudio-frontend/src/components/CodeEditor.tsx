import { useEffect, useRef } from 'react'

/**
 * A lightweight, dependency-free code editor with bash syntax highlighting.
 *
 * The technique: a transparent-text <textarea> sits exactly on top of a
 * highlighted <pre>. The textarea captures input and the caret; the <pre>
 * renders the colored tokens underneath. Both share identical font metrics,
 * padding and wrapping so the glyphs line up perfectly. The <pre> is scrolled
 * in lockstep with the textarea.
 *
 * This avoids pulling in a heavyweight editor (CodeMirror/Monaco) for what is
 * a small, focused editing surface.
 */

// GitHub-dark-ish palette, matching the existing dark script textarea in the app.
const COLORS = {
  plain: '#e6edf3',
  comment: '#8b949e',
  string: '#a5d6ff',
  variable: '#ffa657',
  keyword: '#ff7b72',
  number: '#79c0ff',
  operator: '#ff7b72',
  command: '#d2a8ff',
}

const KEYWORDS = new Set([
  'if', 'then', 'else', 'elif', 'fi', 'for', 'while', 'until', 'do', 'done',
  'case', 'esac', 'function', 'in', 'select', 'time', 'return', 'break',
  'continue', 'export', 'local', 'readonly', 'declare', 'unset', 'shift',
  'trap', 'exit', 'set', 'unset', 'source', 'true', 'false',
])

// Order matters: comments and strings are matched first so their contents are
// consumed as a single token and never re-highlighted as code.
const TOKEN_RE = new RegExp(
  [
    '(#[^\\n]*)', // comment
    '("(?:[^"\\\\\\n]|\\\\.)*"?)', // double-quoted string (tolerate unterminated)
    "('(?:[^'\\\\\\n]|\\\\.)*'?)", // single-quoted string
    '(\\$\\{[^}]*\\}|\\$[A-Za-z_][A-Za-z0-9_]*|\\$[0-9]+|\\$[@*#?!-])', // variable
    '([0-9]+(?:\\.[0-9]+)?)', // number
    '(&&|\\|\\||[|;<>=]+)', // operators
    '([A-Za-z_][A-Za-z0-9_.-]*)', // bare word (keyword / command)
  ].join('|'),
  'g',
)

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

/** Tokenize a line of bash into HTML with colored spans. */
function highlightLine(line: string, atLineStart: boolean): string {
  let out = ''
  let lastIndex = 0
  // Reset the regex stateful lastIndex for each line.
  TOKEN_RE.lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = TOKEN_RE.exec(line)) !== null) {
    // Plain text before this token.
    if (match.index > lastIndex) {
      out += escapeHtml(line.slice(lastIndex, match.index))
    }
    const [full, comment, dq, sq, variable, num, operator, word] = match
    let color = COLORS.plain
    if (comment !== undefined) {
      color = COLORS.comment
    } else if (dq !== undefined || sq !== undefined) {
      color = COLORS.string
    } else if (variable !== undefined) {
      color = COLORS.variable
    } else if (num !== undefined) {
      color = COLORS.number
    } else if (operator !== undefined) {
      color = COLORS.operator
    } else if (word !== undefined) {
      if (KEYWORDS.has(word)) {
        color = COLORS.keyword
      } else if (atLineStart) {
        // A bare word at the start of a command position is a command name.
        color = COLORS.command
      } else {
        color = COLORS.plain
      }
    }
    out += `<span style="color:${color}">${escapeHtml(full)}</span>`
    lastIndex = match.index + full.length
  }
  if (lastIndex < line.length) {
    out += escapeHtml(line.slice(lastIndex))
  }
  return out
}

/**
 * Highlight a whole script. We track whether we're at a "command start" position
 * (beginning of line, or right after a command separator) so the first bare word
 * of each command is colored as a command.
 */
function highlightScript(code: string): string {
  const lines = code.split('\n')
  const rendered: string[] = []
  for (const line of lines) {
    // A line whose first non-space token follows a separator is a command start.
    const trimmed = line.replace(/^[ \t]*/, '')
    const atStart = trimmed.length > 0
    rendered.push(highlightLine(line, atStart))
  }
  return rendered.join('\n')
}

export interface CodeEditorProps {
  value: string
  onChange: (value: string) => void
  rows?: number
  placeholder?: string
  readOnly?: boolean
}

export default function CodeEditor({
  value,
  onChange,
  rows = 12,
  placeholder,
  readOnly = false,
}: CodeEditorProps) {
  const taRef = useRef<HTMLTextAreaElement>(null)
  const preRef = useRef<HTMLPreElement>(null)

  // Keep the highlighted layer scrolled with the textarea.
  const syncScroll = () => {
    if (taRef.current && preRef.current) {
      preRef.current.scrollTop = taRef.current.scrollTop
      preRef.current.scrollLeft = taRef.current.scrollLeft
    }
  }

  useEffect(() => {
    syncScroll()
  }, [value])

  // Shared metrics so the overlay aligns with the textarea exactly.
  const sharedStyle: React.CSSProperties = {
    fontFamily:
      'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, "Liberation Mono", monospace',
    fontSize: '13px',
    lineHeight: '1.5',
    tabSize: 4,
    padding: '10px 12px',
    whiteSpace: 'pre-wrap',
    wordBreak: 'break-word',
    overflowWrap: 'break-word',
    margin: 0,
    border: 'none',
  }

  return (
    <div
      className="relative w-full overflow-hidden rounded-md border border-black/15 bg-[#0d1117] focus-within:border-brand"
      style={{ minHeight: `${rows * 1.5 * 13 + 20}px` }}
    >
      {/* Highlighted layer (behind). aria-hidden: it's a visual mirror. */}
      <pre
        ref={preRef}
        aria-hidden="true"
        className="pointer-events-none absolute inset-0 overflow-auto"
        style={{
          ...sharedStyle,
          color: COLORS.plain,
        }}
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: highlightScript(value) + '\n' }}
      />
      {/* Input layer (front): transparent text, visible caret. */}
      <textarea
        ref={taRef}
        value={value}
        readOnly={readOnly}
        onChange={(e) => onChange(e.target.value)}
        onScroll={syncScroll}
        onKeyDown={(e) => {
          // Insert a literal tab instead of moving focus.
          if (e.key === 'Tab') {
            e.preventDefault()
            const ta = e.currentTarget
            const start = ta.selectionStart
            const end = ta.selectionEnd
            const next = value.slice(0, start) + '  ' + value.slice(end)
            onChange(next)
            requestAnimationFrame(() => {
              ta.selectionStart = ta.selectionEnd = start + 2
            })
          }
        }}
        placeholder={placeholder}
        spellCheck={false}
        className="absolute inset-0 h-full w-full resize-y bg-transparent text-transparent caret-white outline-none"
        style={{
          ...sharedStyle,
          WebkitTextFillColor: 'transparent',
        }}
      />
    </div>
  )
}
