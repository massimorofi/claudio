import { Routes, Route, NavLink, Navigate } from 'react-router-dom'
import { MessageSquare, Settings, Activity } from 'lucide-react'
import ChatView from './features/chat/ChatView'
import ConfigView from './features/config/ConfigView'
import MonitoringView from './features/monitoring/MonitoringView'

const navItems = [
  { to: '/chat', label: 'Chat', icon: MessageSquare },
  { to: '/config', label: 'Config', icon: Settings },
  { to: '/monitor', label: 'Monitor', icon: Activity },
]

export default function App() {
  return (
    <div className="flex h-full flex-col md:flex-row">
      {/* Desktop sidebar */}
      <aside className="hidden md:flex md:w-56 md:flex-col border-r border-black/10 bg-white">
        <div className="px-5 py-5">
          <div className="text-lg font-semibold tracking-tight text-brand">Claudio</div>
          <div className="text-xs text-black/50 mt-0.5">agentic assistant</div>
        </div>
        <nav className="flex flex-col gap-1 px-3">
          {navItems.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              className={({ isActive }) =>
                `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors ${
                  isActive ? 'bg-brand text-white' : 'text-black/70 hover:bg-black/5'
                }`
              }
            >
              <item.icon size={18} />
              {item.label}
            </NavLink>
          ))}
        </nav>
      </aside>

      {/* Main content */}
      <main className="flex-1 min-h-0 overflow-hidden">
        <Routes>
          <Route path="/chat" element={<ChatView />} />
          <Route path="/config" element={<ConfigView />} />
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
