import { useState } from 'react'
import { Routes, Route, NavLink, Navigate } from 'react-router-dom'
import {
  MessageSquare,
  Settings,
  Activity,
  FolderOpen,
  PanelLeftClose,
  PanelLeftOpen,
} from 'lucide-react'
import ChatView from './features/chat/ChatView'
import ConfigView from './features/config/ConfigView'
import MonitoringView from './features/monitoring/MonitoringView'
import WorkspaceView from './features/workspace/WorkspaceView'

const navItems = [
  { to: '/chat', label: 'Chat', icon: MessageSquare },
  { to: '/config', label: 'Config', icon: Settings },
  { to: '/workspace', label: 'Workspace', icon: FolderOpen },
  { to: '/monitor', label: 'Monitor', icon: Activity },
]

const COLLAPSE_KEY = 'claudio.sidebar.collapsed'

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === '1'
  } catch {
    return false
  }
}

export default function App() {
  const [collapsed, setCollapsed] = useState<boolean>(readCollapsed)

  const toggle = () =>
    setCollapsed((c) => {
      const next = !c
      try {
        localStorage.setItem(COLLAPSE_KEY, next ? '1' : '0')
      } catch {
        /* storage may be unavailable; collapse still works for the session */
      }
      return next
    })

  return (
    <div className="flex h-full flex-col md:flex-row">
      {/* Desktop sidebar */}
      <aside
        className={`hidden md:flex md:flex-col border-r border-black/10 bg-white transition-[width] duration-200 ${
          collapsed ? 'md:w-16' : 'md:w-56'
        }`}
      >
        <div
          className={`flex items-center py-5 ${collapsed ? 'justify-center px-2' : 'justify-between px-5'}`}
        >
          {collapsed ? (
            <div className="text-lg font-semibold tracking-tight text-brand">C</div>
          ) : (
            <>
              <div>
                <div className="text-lg font-semibold tracking-tight text-brand">Claudio</div>
                <div className="text-xs text-black/50 mt-0.5">agentic assistant</div>
              </div>
            </>
          )}
          <button
            onClick={toggle}
            title={collapsed ? 'Expand menu' : 'Collapse menu'}
            className="rounded-lg p-1.5 text-black/40 hover:bg-black/5 hover:text-black/70"
          >
            {collapsed ? <PanelLeftOpen size={18} /> : <PanelLeftClose size={18} />}
          </button>
        </div>
        <nav className="flex flex-col gap-1 px-3">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              title={item.label}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg py-2 text-sm font-medium transition-colors ${
                  collapsed ? 'justify-center px-2' : 'px-3'
                } ${isActive ? 'bg-brand text-white' : 'text-black/70 hover:bg-black/5'}`
              }
            >
              <item.icon size={18} />
              {!collapsed && <span>{item.label}</span>}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Main content */}
      <main className="flex-1 min-h-0 overflow-hidden">
        <Routes>
          <Route path="/chat" element={<ChatView />} />
          <Route path="/config" element={<ConfigView />} />
          <Route path="/workspace" element={<WorkspaceView />} />
          <Route path="/monitor" element={<MonitoringView />} />
          <Route path="*" element={<Navigate to="/chat" replace />} />
        </Routes>
      </main>

      {/* Mobile bottom tab bar */}
      <nav className="md:hidden flex border-t border-black/10 bg-white safe-area-bottom">
        {navItems.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            className={({ isActive }) =>
              `flex flex-1 flex-col items-center gap-1 py-2 text-[11px] font-medium transition-colors ${
                isActive ? 'text-brand' : 'text-black/50'
              }`
            }
          >
            <item.icon size={20} />
            {item.label}
          </NavLink>
        ))}
      </nav>
    </div>
  )
}
