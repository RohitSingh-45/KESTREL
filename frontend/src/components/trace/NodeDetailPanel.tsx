import { ArrowDownLeft, ArrowUpRight } from 'lucide-react'
import { useTraceNodeDetail } from '@/api/hooks'
import { RiskBadge } from '@/components/ui/badge'
import { CopyButton, ErrorBlock, LoadingBlock, Separator } from '@/components/ui/misc'
import { SidePanel } from '@/components/ui/dialog'
import { formatDateTime, formatUsd, shortAddress, shortHash } from '@/lib/format'
import { entityLabel } from '@/lib/labels'
import type { GraphEdge } from '@/types'

function TransferRow({ edge, direction }: { edge: GraphEdge; direction: 'in' | 'out' }) {
  const counterparty = direction === 'in' ? edge.source : edge.target
  return (
    <div className="rounded-lg border border-[--color-line] px-3 py-2 text-xs">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-1.5 font-medium">
          {direction === 'in' ? (
            <ArrowDownLeft className="h-3.5 w-3.5 text-emerald-400" />
          ) : (
            <ArrowUpRight className="h-3.5 w-3.5 text-orange-400" />
          )}
          {direction === 'in' ? 'From' : 'To'} {shortAddress(counterparty)}
        </span>
        <span className="tabular-nums text-[--color-ink-muted]">{formatUsd(edge.amount_usd)}</span>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[--color-ink-faint]">
        <span>{edge.amount} {edge.asset}</span>
        <span>{formatDateTime(edge.ts)}</span>
        <span className="mono flex items-center gap-1">
          {shortHash(edge.tx_hash)}
          <CopyButton value={edge.tx_hash} />
        </span>
      </div>
    </div>
  )
}

export function NodeDetailPanel({
  jobId,
  address,
  onClose,
}: {
  jobId: string | undefined
  address: string | null
  onClose: () => void
}) {
  const { data, isLoading, error } = useTraceNodeDetail(jobId, address ?? undefined)

  return (
    <SidePanel
      open={Boolean(address)}
      onOpenChange={(open) => !open && onClose()}
      title={address ? shortAddress(address, 10, 8) : 'Wallet detail'}
      description={address ?? undefined}
    >
      {isLoading && <LoadingBlock label="Loading wallet detail" />}
      {error && <ErrorBlock error={error} />}
      {data && (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3">
            <div>
              <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">Entity</p>
              <p className="mt-0.5 text-sm font-medium">
                {data.node.entity_name ?? entityLabel(data.node.entity_type)}
              </p>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">Risk</p>
              <RiskBadge
                level={(data.node.risk_level as 'low' | 'medium' | 'high' | 'critical') ?? 'low'}
                score={data.node.risk_score}
              />
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">Received</p>
              <p className="mt-0.5 text-sm font-medium tabular-nums">{formatUsd(data.node.value_in_usd)}</p>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">Sent onward</p>
              <p className="mt-0.5 text-sm font-medium tabular-nums">{formatUsd(data.node.value_out_usd)}</p>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">First seen</p>
              <p className="mt-0.5 text-sm">{formatDateTime(data.node.first_seen)}</p>
            </div>
            <div>
              <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">Hop from victim</p>
              <p className="mt-0.5 text-sm tabular-nums">{data.node.hop}</p>
            </div>
          </div>

          {data.node.terminal_explanation && (
            <p className="rounded-lg border border-[--color-line] bg-[--color-surface-2] px-3 py-2 text-xs text-[--color-ink-muted]">
              {data.node.terminal_explanation}
            </p>
          )}

          <Separator />

          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[--color-ink-faint]">
              Incoming transactions ({data.inbound.length})
            </p>
            <div className="space-y-1.5">
              {data.inbound.length === 0 && (
                <p className="text-xs text-[--color-ink-faint]">None observed by this trace.</p>
              )}
              {data.inbound.map((edge) => (
                <TransferRow key={edge.id} edge={edge} direction="in" />
              ))}
            </div>
          </div>

          <div>
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[--color-ink-faint]">
              Outgoing transactions ({data.outbound.length}) — next wallet(s)
            </p>
            <div className="space-y-1.5">
              {data.outbound.length === 0 && (
                <p className="text-xs text-[--color-ink-faint]">
                  Funds have not moved on from here, or this trace stopped before reaching further hops.
                </p>
              )}
              {data.outbound.map((edge) => (
                <TransferRow key={edge.id} edge={edge} direction="out" />
              ))}
            </div>
          </div>
        </div>
      )}
    </SidePanel>
  )
}
