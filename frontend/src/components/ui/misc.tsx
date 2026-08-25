import * as React from 'react'
import { AlertTriangle, Check, Copy, Inbox, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cn('h-4 w-4 animate-spin text-blue-400', className)} />
}

export function LoadingBlock({ label = 'Loading' }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-12 text-sm text-[--color-ink-muted]">
      <Spinner />
      {label}...
    </div>
  )
}

export function ErrorBlock({ error, className }: { error: unknown; className?: string }) {
  const message = error instanceof Error ? error.message : String(error ?? 'Unknown error')
  return (
    <div
      className={cn(
        'flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-300',
        className,
      )}
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
      <span>{message}</span>
    </div>
  )
}

export function EmptyState({
  title,
  description,
  icon: Icon = Inbox,
  action,
}: {
  title: string
  description?: string
  icon?: React.ElementType
  action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-14 text-center">
      <Icon className="h-8 w-8 text-[--color-ink-faint]" />
      <p className="text-sm font-medium text-[--color-ink]">{title}</p>
      {description && <p className="max-w-md text-xs text-[--color-ink-muted]">{description}</p>}
      {action && <div className="mt-2">{action}</div>}
    </div>
  )
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn('animate-pulse rounded-md bg-[--color-surface-3]', className)} />
}

export function Separator({ className }: { className?: string }) {
  return <div className={cn('h-px w-full bg-[--color-line]', className)} />
}

export function CopyButton({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = React.useState(false)
  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation()
        void navigator.clipboard.writeText(value)
        setCopied(true)
        setTimeout(() => setCopied(false), 1400)
      }}
      title={label ?? 'Copy'}
      className="inline-flex items-center gap-1 rounded p-1 text-[--color-ink-faint] transition-colors hover:bg-[--color-surface-3] hover:text-blue-300"
    >
      {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
    </button>
  )
}

export function Mono({ children, className }: { children: React.ReactNode; className?: string }) {
  return <span className={cn('mono text-xs', className)}>{children}</span>
}

export function StatTile({
  label,
  value,
  sub,
  accent,
}: {
  label: string
  value: React.ReactNode
  sub?: React.ReactNode
  accent?: string
}) {
  return (
    <div className="rounded-xl border border-[--color-line] bg-[--color-surface-1] px-4 py-3">
      <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">{label}</p>
      <p className={cn('mt-1 text-xl font-semibold tabular-nums', accent)}>{value}</p>
      {sub && <p className="mt-0.5 text-xs text-[--color-ink-muted]">{sub}</p>}
    </div>
  )
}

export function ProgressBar({ value, className }: { value: number; className?: string }) {
  return (
    <div className={cn('h-1.5 w-full overflow-hidden rounded-full bg-[--color-surface-3]', className)}>
      <div
        className="h-full rounded-full bg-blue-500 transition-[width] duration-300"
        style={{ width: `${Math.min(100, Math.max(0, value))}%` }}
      />
    </div>
  )
}
