import * as React from 'react'
import { NavLink, Outlet, useNavigate } from 'react-router-dom'
import {
  BarChart3,
  Bell,
  Building2,
  FileText,
  FolderSearch,
  Landmark,
  LogOut,
  Moon,
  Radar,
  Settings,
  Share2,
  Sun,
  Wallet,
} from 'lucide-react'
import { useAuth } from '@/auth/AuthContext'
import { useTheme } from '@/theme/ThemeContext'
import { cn } from '@/lib/utils'
import { Badge } from '@/components/ui/badge'
import { useAlertCount } from '@/api/hooks'

// Ordered to match how an investigator actually works a day: see what needs
// attention (Alerts) and which case it belongs to (Cases), run the trace
// workflow (New Trace -> Fund Flow -> Wallet Lookup), then the reference and
// output tools that come later in a case (VASP Directory -> Reports ->
// Analytics), with Admin last since only admins see it at all.
const NAV = [
  { to: '/alerts', label: 'Alerts', icon: Bell, badge: 'alerts' },
  { to: '/cases', label: 'Cases', icon: FolderSearch },
  { to: '/trace', label: 'New Trace', icon: Radar },
  { to: '/graph', label: 'Fund Flow', icon: Share2 },
  { to: '/wallets', label: 'Wallet Lookup', icon: Wallet },
  { to: '/vasps', label: 'VASP Directory', icon: Building2 },
  { to: '/reports', label: 'Reports', icon: FileText },
  { to: '/analytics', label: 'Analytics', icon: BarChart3 },
  { to: '/admin', label: 'Admin', icon: Settings, minRole: 'admin' as const },
]

export function AppShell() {
  const { user, logout, hasRole } = useAuth()
  const navigate = useNavigate()
  const { data: openAlerts } = useAlertCount()
  const { theme, toggleTheme } = useTheme()

  const items = NAV.filter((item) => !item.minRole || hasRole(item.minRole))

  return (
    <div className="flex h-full flex-col">
      <div className="flex shrink-0 items-center justify-center gap-2 border-b border-amber-500/20 bg-amber-500/[0.06] px-4 py-1 text-[10px] font-medium uppercase tracking-[0.2em] text-amber-300/80">
        Restricted &middot; For Official Use Only &middot; Session Logged
      </div>
      <div className="flex flex-1 overflow-hidden">
      <aside className="flex w-60 shrink-0 flex-col border-r border-[--color-line] bg-[--color-surface-1]">
        <div className="flex items-center gap-2.5 border-b border-[--color-line] px-4 py-4">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-blue-500/15 ring-1 ring-blue-500/30">
            <Landmark className="h-4 w-4 text-blue-400" />
          </div>
          <div className="leading-tight">
            <p className="text-sm font-semibold tracking-tight">TraceX</p>
            <p className="text-[10px] uppercase tracking-wider text-[--color-ink-faint]">
              I4C &middot; Ministry of Home Affairs
            </p>
          </div>
        </div>

        <nav className="flex-1 space-y-0.5 overflow-y-auto p-2">
          {items.map(({ to, label, icon: Icon, badge }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  'flex items-center gap-2.5 rounded-lg px-3 py-2 text-sm transition-colors',
                  isActive
                    ? 'bg-blue-500/12 font-medium text-blue-300'
                    : 'text-[--color-ink-muted] hover:bg-[--color-surface-2] hover:text-[--color-ink]',
                )
              }
            >
              <Icon className="h-4 w-4 shrink-0" />
              <span className="flex-1">{label}</span>
              {badge === 'alerts' && openAlerts ? (
                <Badge variant="critical">{openAlerts}</Badge>
              ) : null}
            </NavLink>
          ))}
        </nav>

        <div className="border-t border-[--color-line] p-3">
          <div className="mb-2 px-1">
            <p className="truncate text-xs font-medium">{user?.full_name}</p>
            <p className="truncate text-[11px] text-[--color-ink-faint]">
              {user?.agency ?? 'Unassigned'} - {user?.role}
            </p>
            <p className="mono truncate text-[10px] text-[--color-ink-faint]">
              Badge {user?.badge_number ?? '—'}
            </p>
          </div>
          <button
            onClick={toggleTheme}
            className="mb-1 flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs text-[--color-ink-muted] transition-colors hover:bg-[--color-surface-2] hover:text-[--color-ink]"
          >
            {theme === 'dark' ? (
              <Sun className="h-3.5 w-3.5" />
            ) : (
              <Moon className="h-3.5 w-3.5" />
            )}
            {theme === 'dark' ? 'Light theme' : 'Dark theme'}
          </button>
          <button
            onClick={() => {
              logout()
              navigate('/login')
            }}
            className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-xs text-[--color-ink-muted] transition-colors hover:bg-[--color-surface-2] hover:text-red-300"
          >
            <LogOut className="h-3.5 w-3.5" />
            Sign out
          </button>
        </div>
      </aside>

      <main className="flex-1 overflow-y-auto">
        <Outlet />
      </main>
      </div>
    </div>
  )
}

export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string
  description?: string
  actions?: React.ReactNode
}) {
  return (
    <div className="sticky top-0 z-20 flex items-start justify-between gap-4 border-b border-[--color-line] bg-[--color-surface-0]/90 px-6 py-4 backdrop-blur">
      <div>
        <h1 className="text-lg font-semibold tracking-tight">{title}</h1>
        {description && <p className="mt-0.5 text-xs text-[--color-ink-muted]">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  )
}

export function PageBody({
  children,
  className,
}: {
  children: React.ReactNode
  className?: string
}) {
  return <div className={cn('px-6 py-5', className)}>{children}</div>
}
