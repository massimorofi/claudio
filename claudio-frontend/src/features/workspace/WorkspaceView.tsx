import { useMemo, useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  Download,
  Trash2,
  RefreshCw,
  FileText,
  Folder,
  ChevronRight,
  ChevronDown,
  AlertTriangle,
  X,
} from 'lucide-react'
import { api, downloadApi, saveBlob } from '../../api/client'
import type {
  WorkspaceTreeResponse,
  WorkspaceDeleteResponse,
  WorkspaceNode,
} from '../../api/types'

function formatBytes(n: number): string {
  if (n < 1024) return `${n} B`
  const units = ['KB', 'MB', 'GB', 'TB']
  let v = n / 1024
  let i = 0
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024
    i++
  }
  return `${v.toFixed(v >= 10 ? 0 : 1)} ${units[i]}`
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  if (isNaN(d.getTime())) return iso
  return d.toLocaleString()
}

function flattenPaths(nodes: WorkspaceNode[]): string[] {
  const out: string[] = []
  const walk = (list: WorkspaceNode[]) => {
    for (const n of list) {
      out.push(n.path)
      if (n.children && n.children.length) walk(n.children)
    }
  }
  walk(nodes)
  return out
}

interface RowProps {
  node: WorkspaceNode
  depth: number
  expanded: Set<string>
  selected: Set<string>
  onToggleExpand: (path: string) => void
  onToggleSelect: (path: string) => void
  onDownload: (node: WorkspaceNode) => void
  onDelete: (node: WorkspaceNode) => void
}

function TreeRow({
  node,
  depth,
  expanded,
  selected,
  onToggleExpand,
  onToggleSelect,
  onDownload,
  onDelete,
}: RowProps) {
  const isDir = node.type === 'dir'
  const isOpen = expanded.has(node.path)
  return (
    <>
      <div
        className="flex items-center gap-2 border-b border-black/5 py-1.5 pr-4 text-sm hover:bg-black/[0.02]"
        style={{ paddingLeft: depth * 18 + 12 }}
      >
        <input
          type="checkbox"
          checked={selected.has(node.path)}
          onChange={() => onToggleSelect(node.path)}
        />
        {isDir ? (
          <button
            onClick={() => onToggleExpand(node.path)}
            title={isOpen ? 'Collapse' : 'Expand'}
            className="flex w-5 shrink-0 items-center justify-center rounded text-black/50 hover:bg-black/5"
          >
            {isOpen ? <ChevronDown size={15} /> : <ChevronRight size={15} />}
          </button>
        ) : (
          <span className="w-5 shrink-0" />
        )}
        {isDir ? (
          <Folder size={15} className="shrink-0 text-brand" />
        ) : (
          <FileText size={15} className="shrink-0 text-black/40" />
        )}
        <span
          className="min-w-0 flex-1 truncate font-mono text-xs text-black/80"
          title={node.path}
        >
          {node.name}
        </span>
        {!isDir && (
          <span className="shrink-0 text-xs text-black/50">
            {formatBytes(node.size ?? 0)}
          </span>
        )}
        <span className="hidden shrink-0 text-xs text-black/40 sm:inline">
          {formatDate(node.modified)}
        </span>
        <button
          onClick={() => onDownload(node)}
          title={isDir ? 'Download as zip' : 'Download'}
          className="shrink-0 rounded p-1.5 text-black/50 hover:bg-black/5 hover:text-brand"
        >
          <Download size={15} />
        </button>
        <button
          onClick={() => onDelete(node)}
          title={isDir ? 'Delete folder' : 'Delete'}
          className="shrink-0 rounded p-1.5 text-black/50 hover:bg-red-50 hover:text-red-600"
        >
          <Trash2 size={15} />
        </button>
      </div>
      {isDir &&
        isOpen &&
        (node.children ?? []).map((child) => (
          <TreeRow
            key={child.path}
            node={child}
            depth={depth + 1}
            expanded={expanded}
            selected={selected}
            onToggleExpand={onToggleExpand}
            onToggleSelect={onToggleSelect}
            onDownload={onDownload}
            onDelete={onDelete}
          />
        ))}
    </>
  )
}

export default function WorkspaceView() {
  const [selected, setSelected] = useState<Set<string>>(new Set())
  const [expanded, setExpanded] = useState<Set<string>>(new Set())
  const [confirmDelete, setConfirmDelete] = useState(false)
  const [message, setMessage] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null)
  const queryClient = useQueryClient()

  const treeQ = useQuery({
    queryKey: ['workspace-tree'],
    queryFn: () => api.get<WorkspaceTreeResponse>('/workspace/tree'),
  })

  const tree = treeQ.data?.tree ?? []
  const allPaths = useMemo(() => flattenPaths(tree), [tree])

  const toggleExpand = (path: string) =>
    setExpanded((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })

  const toggleSelect = (path: string) =>
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })

  const toggleAll = () =>
    setSelected((prev) =>
      prev.size === allPaths.length ? new Set() : new Set(allPaths)
    )

  const clearSelection = () => {
    setSelected(new Set())
    setConfirmDelete(false)
  }

  const deleteMut = useMutation({
    mutationFn: (paths: string[]) =>
      api.post<WorkspaceDeleteResponse>('/workspace/files/delete', { paths }),
    onSuccess: (res) => {
      const failed = res.failed ?? []
      setMessage({
        kind: failed.length ? 'err' : 'ok',
        text: failed.length
          ? `Deleted ${res.deleted.length}, ${failed.length} failed: ${failed
              .map((f) => `${f.path} (${f.error})`)
              .join(', ')}`
          : `Deleted ${res.deleted.length} item${res.deleted.length === 1 ? '' : 's'}.`,
      })
      clearSelection()
      queryClient.invalidateQueries({ queryKey: ['workspace-tree'] })
    },
    onError: (e: Error) => setMessage({ kind: 'err', text: e.message }),
  })

  const onDownload = async (node: WorkspaceNode) => {
    try {
      if (node.type === 'dir') {
        const blob = await downloadApi.post('/workspace/files/download', {
          paths: [node.path],
        })
        saveBlob(blob, `${node.name}.zip`)
      } else {
        const blob = await downloadApi.get(
          `/workspace/files/download?path=${encodeURIComponent(node.path)}`,
        )
        saveBlob(blob, node.name)
      }
    } catch (e) {
      setMessage({ kind: 'err', text: (e as Error).message })
    }
  }

  const downloadSelected = async () => {
    if (selected.size === 0) return
    try {
      const blob = await downloadApi.post('/workspace/files/download', {
        paths: Array.from(selected),
      })
      saveBlob(blob, 'workspace-files.zip')
    } catch (e) {
      setMessage({ kind: 'err', text: (e as Error).message })
    }
  }

  const onDelete = (node: WorkspaceNode) => {
    setSelected(new Set([node.path]))
    setConfirmDelete(true)
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-3 border-b border-black/10 bg-white px-4 py-3">
        <div className="min-w-0 flex-1">
          <div className="text-sm font-semibold text-black/80">Workspace</div>
          <div
            className="truncate font-mono text-[11px] text-black/40"
            title={treeQ.data?.workspace}
          >
            {treeQ.data?.workspace ?? '—'}
          </div>
        </div>
        <button
          onClick={() => queryClient.invalidateQueries({ queryKey: ['workspace-tree'] })}
          className="flex items-center gap-1.5 rounded-lg border border-black/15 px-3 py-1.5 text-xs font-medium text-black/70 hover:bg-black/5"
        >
          <RefreshCw size={14} className={treeQ.isFetching ? 'animate-spin' : ''} />
          Refresh
        </button>
      </div>

      {message && (
        <div
          className={`px-4 py-2 text-sm ${
            message.kind === 'ok' ? 'bg-green-50 text-green-700' : 'bg-red-50 text-red-700'
          }`}
        >
          <div className="flex items-center gap-2">
            <span className="flex-1">{message.text}</span>
            <button
              onClick={() => setMessage(null)}
              className="text-black/40 hover:text-black/70"
            >
              <X size={14} />
            </button>
          </div>
        </div>
      )}

      {tree.length > 0 && (
        <div className="flex items-center gap-3 border-b border-black/10 bg-black/[0.02] px-4 py-2 text-xs text-black/60">
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={allPaths.length > 0 && selected.size === allPaths.length} onChange={toggleAll} />
            Select all
          </label>
          <span>{allPaths.length} item{allPaths.length === 1 ? '' : 's'}</span>
          {selected.size > 0 && (
            <div className="ml-auto flex items-center gap-2">
              <span className="font-medium text-black/70">{selected.size} selected</span>
              <button
                onClick={downloadSelected}
                className="flex items-center gap-1.5 rounded-lg border border-brand px-3 py-1 text-xs font-semibold text-brand hover:bg-brand/5"
              >
                <Download size={13} /> Download zip
              </button>
              <button
                onClick={() => setConfirmDelete(true)}
                className="flex items-center gap-1.5 rounded-lg border border-red-300 px-3 py-1 text-xs font-semibold text-red-600 hover:bg-red-50"
              >
                <Trash2 size={13} /> Delete
              </button>
            </div>
          )}
        </div>
      )}

      <div className="scroll-thin flex-1 overflow-y-auto">
        {treeQ.isLoading ? (
          <div className="flex h-full items-center justify-center text-sm text-black/40">
            Loading workspace…
          </div>
        ) : treeQ.isError ? (
          <div className="p-4 text-sm text-red-600">
            Could not load workspace: {String(treeQ.error)}
          </div>
        ) : tree.length === 0 ? (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-black/40">
            <Folder size={32} />
            <div className="text-sm">The workspace is empty.</div>
          </div>
        ) : (
          <div className="py-1">
            {tree.map((node) => (
              <TreeRow
                key={node.path}
                node={node}
                depth={0}
                expanded={expanded}
                selected={selected}
                onToggleExpand={toggleExpand}
                onToggleSelect={toggleSelect}
                onDownload={onDownload}
                onDelete={onDelete}
              />
            ))}
          </div>
        )}
        {treeQ.data?.truncated && (
          <div className="px-4 py-2 text-xs text-amber-600">
            Listing truncated — too many files to show all.
          </div>
        )}
      </div>

      {confirmDelete && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4">
          <div className="w-full max-w-md rounded-xl bg-white p-5 shadow-xl">
            <div className="flex items-center gap-2 text-sm font-semibold text-red-700">
              <AlertTriangle size={18} />
              Delete {selected.size} item{selected.size === 1 ? '' : 's'}?
            </div>
            <p className="mt-2 text-sm text-black/60">
              This permanently removes the selected files and folders from the
              workspace. Deleting a folder removes everything inside it. This cannot
              be undone.
            </p>
            <div className="scroll-thin mt-3 max-h-40 overflow-y-auto rounded border border-black/10 bg-black/[0.02] p-2">
              <ul className="space-y-0.5 font-mono text-[11px] text-black/60">
                {Array.from(selected).map((p) => (
                  <li key={p}>{p}</li>
                ))}
              </ul>
            </div>
            <div className="mt-4 flex gap-2">
              <button
                onClick={() => deleteMut.mutate(Array.from(selected))}
                disabled={deleteMut.isPending}
                className="flex-1 rounded-lg bg-red-600 px-4 py-2 text-sm font-semibold text-white disabled:opacity-50"
              >
                Yes, delete
              </button>
              <button
                onClick={clearSelection}
                className="flex-1 rounded-lg border border-black/15 px-4 py-2 text-sm"
              >
                Cancel
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
