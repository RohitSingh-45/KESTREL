import type { CasePriority, CaseStatus, Chain, EntityType, FraudCategory, RiskLevel } from '@/types'
import type { VariantProps } from 'class-variance-authority'
import { badgeVariants } from '@/components/ui/badge'

type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>['variant']>

export const CHAIN_LABEL: Record<Chain, string> = {
  bitcoin: 'Bitcoin',
  ethereum: 'Ethereum',
  bsc: 'BNB Smart Chain',
  polygon: 'Polygon',
  arbitrum: 'Arbitrum',
  base: 'Base',
  tron: 'TRON',
}

export function riskVariant(level: string | null | undefined): BadgeVariant {
  if (level === 'critical' || level === 'high' || level === 'medium' || level === 'low') return level
  return 'default'
}

export function statusVariant(status: CaseStatus): BadgeVariant {
  if (status === 'frozen' || status === 'attributed') return 'success'
  if (status === 'tracing' || status === 'request_sent') return 'brand'
  if (status === 'closed') return 'default'
  return 'medium'
}

export function priorityVariant(priority: CasePriority): BadgeVariant {
  if (priority === 'urgent') return 'critical'
  if (priority === 'high') return 'high'
  if (priority === 'low') return 'low'
  return 'default'
}

export function entityColor(type: string): string {
  if (type.includes('cex') || type.includes('vasp') || type.includes('p2p')) return '#22c55e'
  if (type === 'mixer' || type === 'sanctioned' || type === 'scam' || type === 'ransomware') return '#ef4444'
  if (type === 'bridge') return '#a855f7'
  if (type === 'victim') return '#60a5fa'
  return '#64748b'
}

export function categoryLabel(value: FraudCategory | string): string {
  return value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function entityLabel(value: EntityType | string): string {
  return value.replace(/_/g, ' ')
}

export const RISK_LEVELS: RiskLevel[] = ['critical', 'high', 'medium', 'low']
