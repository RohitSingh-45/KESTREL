import * as React from 'react'
import { useNavigate } from 'react-router-dom'
import { useAlertCounts, useAlerts, useUpdateAlert } from '@/api/hooks'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Badge, RiskBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { NativeSelect } from '@/components/ui/input'
import { EmptyState, ErrorBlock, LoadingBlock, StatTile } from '@/components/ui/misc'
import { formatDuration, shortAddress, timeAgo, titleCase } from '@/lib/format'

function slaLabel(seconds: number | null, overdue: boolean): string {
  if (seconds == null) return 'No SLA'
  if (overdue) return `Overdue ${formatDuration(Math.abs(seconds))}`
  return formatDuration(seconds)
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

  return (
    <>
      <PageHeader
        title="Alerts"
        description="Auto-generated from traces. Worst first, with an SLA countdown per item."
      />
      <PageBody className="space-y-4">
        {counts && (
          <div className="grid gap-3 md:grid-cols-4">
            <StatTile label="New" value={counts.new} accent="text-sky-300" />
            <StatTile label="Overdue" value={counts.overdue} accent="text-red-400" />
            <StatTile label="Critical" value={counts.critical} />
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
        <div className="space-y-2">
          {data?.items.map((alert) => (
            <div key={alert.id} className="panel flex flex-col gap-3 p-4 md:flex-row md:items-start">
              <div className="flex-1 space-y-1">
                <div className="flex flex-wrap items-center gap-2">
                  <RiskBadge level={alert.severity} />
                  <Badge>{titleCase(alert.alert_type)}</Badge>
                  <Badge variant={alert.is_overdue ? 'critical' : 'default'}>
                    {slaLabel(alert.sla_seconds_remaining, alert.is_overdue)}
                  </Badge>
                </div>
                <p className="text-sm font-medium">{alert.title}</p>
                <p className="text-xs text-[--color-ink-muted]">{alert.message}</p>
                <p className="text-[11px] text-[--color-ink-faint]">
                  {alert.case_number ?? 'No case'} · {alert.vasp_name ?? alert.vasp_slug ?? '—'} ·{' '}
                  {alert.address ? shortAddress(alert.address) : ''} · {timeAgo(alert.created_at)}
                </p>
                {alert.recommended_action && (
                  <p className="text-xs text-sky-200/80">{alert.recommended_action}</p>
                )}
              </div>
              <div className="flex shrink-0 flex-wrap gap-2">
                {alert.case_id && (
                  <Button size="sm" variant="ghost" onClick={() => navigate(`/cases/${alert.case_id}`)}>
                    Case
                  </Button>
                )}
                {alert.status === 'new' && (
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => update.mutate({ id: alert.id, status: 'acknowledged' })}
                  >
                    Ack
                  </Button>
                )}
                {alert.status !== 'actioned' && alert.status !== 'dismissed' && (
                  <Button size="sm" onClick={() => update.mutate({ id: alert.id, status: 'actioned' })}>
                    Action
                  </Button>
                )}
                {alert.status !== 'dismissed' && (
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => update.mutate({ id: alert.id, status: 'dismissed' })}
                  >
                    Dismiss
                  </Button>
                )}
              </div>
            </div>
          ))}
        </div>
      </PageBody>
    </>
  )
}
