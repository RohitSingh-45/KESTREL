import { Link } from 'react-router-dom'
import { Badge, RiskBadge } from '@/components/ui/badge'
import { CopyButton } from '@/components/ui/misc'
import { formatPercent, formatUsd } from '@/lib/format'
import type { RiskLevel, TraceJob } from '@/types'

const RISK_DOT: Record<string, string> = {
  low: '🟢',
  medium: '🟡',
  high: '🟠',
  critical: '🔴',
}

// A blockchain address never proves who holds it - "HIGH RISK" earns the
// siren, everything below it gets a calmer icon so the one status that
// actually demands action stands out rather than every level looking alarming.
const STATUS_LABEL: Record<RiskLevel, string> = {
  low: 'LOW RISK',
  medium: 'MEDIUM RISK',
  high: 'HIGH RISK 🚨',
  critical: 'CRITICAL RISK 🚨',
}

export function InvestigationResultCard({ job }: { job: TraceJob }) {
  const finding = job.summary?.attribution
  const risk = job.summary?.risk
  const counts = job.summary?.counts
  const stats = job.summary?.stats

  const transactions = Number(stats?.transfers_examined ?? counts?.edges ?? 0)
  const traced = finding?.value_usd ?? 0
  const hops = finding?.hop ?? 0
  const likelyExchange = finding?.found ? finding.entity_name ?? 'an unidentified service' : null

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
          <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">Status</p>
          <p className="mt-1 text-2xl font-semibold">
            {risk?.level ? STATUS_LABEL[risk.level] : '--'}
          </p>
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
          <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">
            {likelyExchange ? 'Likely exchange' : 'Endpoint'}
          </p>
          {likelyExchange ? (
            <>
              <p className="mt-1 truncate text-lg font-semibold" title={likelyExchange}>
                {likelyExchange}
              </p>
              <p className="text-xs text-[--color-ink-faint]">
                A wallet address does not prove identity — this is our best read, not a
                certainty.
              </p>
              <Badge variant="success" className="mt-1">
                Custodial exchange identified
              </Badge>
            </>
          ) : (
            <p className="mt-1 text-lg font-semibold text-[--color-ink-muted]">Not yet identified</p>
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
      {risk?.reasons?.length ? (
        <div className="border-t border-[--color-line] px-5 py-3">
          <p className="mb-1.5 text-[11px] uppercase tracking-wide text-[--color-ink-faint]">
            Why this risk score
          </p>
          <ul className="space-y-0.5 text-xs text-[--color-ink-muted]">
            {risk.reasons.map((reason, i) => (
              <li key={i}>• {reason}</li>
            ))}
          </ul>
        </div>
      ) : null}
      {finding?.statement && (
        <div className="border-t border-[--color-line] px-5 py-3 text-sm leading-relaxed text-[--color-ink-muted]">
          {finding.statement}
        </div>
      )}
      {(finding?.address || job.case_id) && (
        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[--color-line] px-5 py-3">
          {finding?.address && (
            <p className="mono text-xs text-[--color-ink-muted]">
              {finding.address} <CopyButton value={finding.address} />
            </p>
          )}
          {job.case_id && (
            <Link className="text-xs text-sky-300 hover:underline" to={`/cases/${job.case_id}`}>
              Open parent case
            </Link>
          )}
        </div>
      )}
    </div>
  )
}
