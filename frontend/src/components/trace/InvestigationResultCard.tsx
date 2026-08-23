import { Badge, RiskBadge } from '@/components/ui/badge'
import { formatPercent, formatUsd } from '@/lib/format'
import type { TraceJob } from '@/types'

const RISK_DOT: Record<string, string> = {
  low: '🟢',
  medium: '🟡',
  high: '🟠',
  critical: '🔴',
}

export function InvestigationResultCard({ job }: { job: TraceJob }) {
  const finding = job.summary?.attribution
  const risk = job.summary?.risk
  const counts = job.summary?.counts
  const stats = job.summary?.stats

  const transactions = Number(stats?.transfers_examined ?? counts?.edges ?? 0)
  const traced = finding?.value_usd ?? 0
  const hops = finding?.hop ?? 0
  const endpoint = finding?.found ? finding.entity_name ?? 'Unidentified service' : 'Not yet identified'

  return (
    <div className="overflow-hidden rounded-xl border border-[--color-line] bg-gradient-to-br from-[--color-surface-1] to-[--color-surface-2]">
      <div className="border-b border-[--color-line] bg-[--color-surface-2] px-5 py-3">
        <p className="text-xs font-semibold uppercase tracking-wider text-[--color-ink-muted]">
          Investigation result
        </p>
      </div>
      <div className="grid gap-4 p-5 sm:grid-cols-2 lg:grid-cols-3">
        <div>
          <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">Risk score</p>
          <p className="mt-1 flex items-center gap-2 text-2xl font-semibold tabular-nums">
            {risk?.score != null ? Math.round(risk.score) : '--'}
            <span className="text-sm text-[--color-ink-faint]">/100</span>
            <span>{risk?.level ? RISK_DOT[risk.level] : ''}</span>
          </p>
          {risk?.level && <RiskBadge level={risk.level} />}
        </div>
        <div>
          <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">Transactions</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{transactions}</p>
        </div>
        <div>
          <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">Fund traced</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{formatUsd(traced)}</p>
        </div>
        <div>
          <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">Hops</p>
          <p className="mt-1 text-2xl font-semibold tabular-nums">{hops}</p>
        </div>
        <div className="sm:col-span-2 lg:col-span-1">
          <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">Endpoint</p>
          <p className="mt-1 truncate text-lg font-semibold" title={endpoint}>
            {endpoint}
          </p>
          {finding?.found && (
            <Badge variant="success" className="mt-1">
              Custodial exchange identified
            </Badge>
          )}
        </div>
        {finding?.confidence != null && (
          <div>
            <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">Confidence</p>
            <p className="mt-1 text-2xl font-semibold tabular-nums">{formatPercent(finding.confidence)}</p>
          </div>
        )}
        {finding?.elapsed_human && (
          <div>
            <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">Time to endpoint</p>
            <p className="mt-1 text-2xl font-semibold">{finding.elapsed_human}</p>
          </div>
        )}
      </div>
      {finding?.statement && (
        <div className="border-t border-[--color-line] px-5 py-3 text-sm leading-relaxed text-[--color-ink-muted]">
          {finding.statement}
        </div>
      )}
    </div>
  )
}
