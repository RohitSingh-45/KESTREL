import * as React from 'react'
import { useNavigate } from 'react-router-dom'
import {
  AlertTriangle,
  ArrowLeftRight,
  Building2,
  Repeat,
  ShieldAlert,
  Shuffle,
  TrendingUp,
  type LucideIcon,
} from 'lucide-react'
import { useAlertCounts, useAlerts, useUpdateAlert } from '@/api/hooks'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { NativeSelect } from '@/components/ui/input'
import { EmptyState, ErrorBlock, LoadingBlock, StatTile } from '@/components/ui/misc'
import { formatDuration, shortAddress, timeAgo, titleCase } from '@/lib/format'
import type { Alert, AlertType, RiskLevel } from '@/types'

// Plain-language framing for each risk level, so a first-time viewer knows
// what to do without decoding a severity word first.
const SEVERITY_SECTION: Record<
  RiskLevel,
  { heading: string; instruction: string; accent: string; ring: string }
> = {
  critical: {
    heading: 'Act now',
    instruction: 'Sanctions or mixer exposure — escalate today.',
    accent: 'border-red-500/60 bg-red-500/10 text-red-300',
    ring: 'border-l-red-500',
  },
  high: {
    heading: 'Act today',
    instruction: 'A named exchange is holding the funds — request it before the trail goes cold.',
    accent: 'border-orange-500/50 bg-orange-500/10 text-orange-300',
    ring: 'border-l-orange-500',
  },
  medium: {
    heading: 'This week',
    instruction: 'Worth following up, not urgent.',
    accent: 'border-yellow-500/50 bg-yellow-500/10 text-yellow-300',
    ring: 'border-l-yellow-500',
  },
  low: {
    heading: 'When you get to it',
    instruction: 'Recorded for the case file.',
    accent: 'border-emerald-500/50 bg-emerald-500/10 text-emerald-300',
    ring: 'border-l-emerald-500',
  },
}

const SEVERITY_ORDER: RiskLevel[] = ['critical', 'high', 'medium', 'low']

const TYPE_ICON: Record<AlertType, LucideIcon> = {
  sanctions_hit: ShieldAlert,
  vasp_identified: Building2,
  mixer_detected: Shuffle,
  bridge_detected: ArrowLeftRight,
  high_value_flow: TrendingUp,
  rapid_movement: Repeat,
  trace_failed: AlertTriangle,
}

// A short, plain sentence for the card's main line - the technical message
// (addresses, compliance emails) moves to a collapsed "Details" section
// instead of competing with it for attention.
export function plainSummary(alert: Alert): string {
  switch (alert.alert_type) {
    case 'sanctions_hit':
      return 'Funds passed through a sanctioned address.'
    case 'vasp_identified':
      return alert.vasp_name
        ? `Money traced to a ${alert.vasp_name} account.`
        : 'Money traced to a named exchange.'
    case 'mixer_detected':
      return 'Funds were run through a mixing service to break the trail.'
    case 'bridge_detected':
      return 'Funds crossed to another blockchain — the trail continues there.'
    case 'high_value_flow':
      return 'An unusually large amount moved in one transfer.'
    case 'rapid_movement':
      return 'Funds were forwarded within minutes — looks automated, not manual.'
    case 'trace_failed':
      return 'The trace could not finish — data was unavailable.'
    default:
      return alert.title
  }
}

export function slaPill(seconds: number | null, overdue: boolean): { text: string; tone: 'critical' | 'default' } {
  if (seconds == null) return { text: 'No deadline', tone: 'default' }
  if (overdue) return { text: `Overdue by ${formatDuration(Math.abs(seconds))}`, tone: 'critical' }
  return { text: `${formatDuration(seconds)} left to respond`, tone: 'default' }
}

function AlertCard({
  alert,
  onNavigate,
  onUpdate,
}: {
  alert: Alert
  onNavigate: (caseId: string) => void
  onUpdate: (id: string, status: string) => void
}) {
  const section = SEVERITY_SECTION[alert.severity]
  const Icon = TYPE_ICON[alert.alert_type] ?? AlertTriangle
  const sla = slaPill(alert.sla_seconds_remaining, alert.is_overdue)

  return (
    <div className={`panel flex flex-col gap-3 border-l-4 p-4 md:flex-row md:items-start ${section.ring}`}>
      <div className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-lg ${section.accent}`}>
        <Icon className="h-5 w-5" />
      </div>
      <div className="flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-semibold">{alert.title}</p>
          <Badge variant={sla.tone}>{sla.text}</Badge>
        </div>
        <p className="text-sm text-[--color-ink-muted]">{plainSummary(alert)}</p>
        {alert.recommended_action && (
          <p className="text-xs text-blue-200/80">→ {alert.recommended_action}</p>
        )}
        <details className="group">
          <summary className="cursor-pointer text-[11px] text-[--color-ink-faint] hover:text-[--color-ink-muted]">
            Details
          </summary>
          <div className="mt-1.5 space-y-1 text-xs text-[--color-ink-faint]">
            <p>{alert.message}</p>
            <p>
              {alert.case_number ?? 'No case'} · {alert.vasp_name ?? alert.vasp_slug ?? '—'} ·{' '}
              {alert.address ? shortAddress(alert.address) : ''} · {timeAgo(alert.created_at)}
            </p>
          </div>
        </details>
      </div>
      <div className="flex shrink-0 flex-wrap gap-2">
        {alert.case_id && (
          <Button size="sm" variant="ghost" onClick={() => onNavigate(alert.case_id!)}>
            Open case
          </Button>
        )}
        {alert.status === 'new' && (
          <Button size="sm" variant="secondary" onClick={() => onUpdate(alert.id, 'acknowledged')}>
            Ack
          </Button>
        )}
        {alert.status !== 'actioned' && alert.status !== 'dismissed' && (
          <Button size="sm" onClick={() => onUpdate(alert.id, 'actioned')}>
            Action
          </Button>
        )}
        {alert.status !== 'dismissed' && (
          <Button size="sm" variant="ghost" onClick={() => onUpdate(alert.id, 'dismissed')}>
            Dismiss
          </Button>
        )}
      </div>
    </div>
  )
}

export function AlertsPage() {
  const navigate = useNavigate()
  const [status, setStatus] = React.useState('')
  const { data, isLoading, error } = useAlerts({
    status: status || undefined,
    open_only: status ? undefined : true,
    limit: 100,
  })
  const { data: counts } = useAlertCounts()
  const update = useUpdateAlert()

  const bySeverity = React.useMemo(() => {
    const groups: Record<RiskLevel, Alert[]> = { critical: [], high: [], medium: [], low: [] }
    for (const alert of data?.items ?? []) groups[alert.severity]?.push(alert)
    return groups
  }, [data])

  return (
    <>
      <PageHeader
        title="Alerts"
        description="What needs attention, worst first. Click Details on any card for the full technical picture."
      />
      <PageBody className="space-y-5">
        {counts && (
          <div className="grid gap-3 md:grid-cols-4">
            <StatTile label="New" value={counts.new} accent="text-blue-300" />
            <StatTile label="Overdue" value={counts.overdue} accent="text-red-400" />
            <StatTile label="Critical" value={counts.critical} accent="text-red-400" />
            <StatTile label="Actioned" value={counts.actioned} />
          </div>
        )}
        <NativeSelect value={status} onChange={(e) => setStatus(e.target.value)} className="w-48">
          <option value="">Open queue</option>
          {['new', 'acknowledged', 'actioned', 'dismissed'].map((s) => (
            <option key={s} value={s}>
              {titleCase(s)}
            </option>
          ))}
        </NativeSelect>
        {isLoading && <LoadingBlock label="Loading alerts" />}
        {error && <ErrorBlock error={error} />}
        {data && data.items.length === 0 && <EmptyState title="Inbox is clear" />}

        {SEVERITY_ORDER.map((level) => {
          const items = bySeverity[level]
          if (!items.length) return null
          const section = SEVERITY_SECTION[level]
          return (
            <div key={level} className="space-y-2.5">
              <div className="flex items-baseline gap-2">
                <h2 className="text-sm font-semibold">{section.heading}</h2>
                <Badge variant={level}>{items.length}</Badge>
                <p className="text-xs text-[--color-ink-faint]">{section.instruction}</p>
              </div>
              <div className="space-y-2">
                {items.map((alert) => (
                  <AlertCard
                    key={alert.id}
                    alert={alert}
                    onNavigate={(caseId) => navigate(`/cases/${caseId}`)}
                    onUpdate={(id, s) => update.mutate({ id, status: s })}
                  />
                ))}
              </div>
            </div>
          )
        })}
      </PageBody>
    </>
  )
}
