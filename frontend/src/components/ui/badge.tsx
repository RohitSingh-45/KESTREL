import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

export const badgeVariants = cva(
  'inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-[11px] font-medium leading-tight whitespace-nowrap',
  {
    variants: {
      variant: {
        default: 'border-[--color-line] bg-[--color-surface-2] text-[--color-ink-muted]',
        brand: 'border-blue-500/40 bg-blue-500/10 text-blue-300',
        low: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
        medium: 'border-yellow-500/40 bg-yellow-500/10 text-yellow-300',
        high: 'border-orange-500/40 bg-orange-500/10 text-orange-300',
        critical: 'border-red-500/50 bg-red-500/15 text-red-300',
        success: 'border-emerald-500/40 bg-emerald-500/10 text-emerald-300',
        purple: 'border-violet-500/40 bg-violet-500/10 text-violet-300',
      },
    },
    defaultVariants: { variant: 'default' },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

export function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />
}

export type RiskLevel = 'low' | 'medium' | 'high' | 'critical'

export function RiskBadge({ level, score }: { level: RiskLevel; score?: number }) {
  return (
    <Badge variant={level}>
      {level.toUpperCase()}
      {score != null && <span className="mono opacity-80">{Math.round(score)}</span>}
    </Badge>
  )
}
