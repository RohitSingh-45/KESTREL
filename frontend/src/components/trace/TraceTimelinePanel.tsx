import { useTraceTimeline } from '@/api/hooks'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { CopyButton, ErrorBlock, LoadingBlock } from '@/components/ui/misc'
import { formatDuration, formatUsd, shortAddress, shortHash } from '@/lib/format'

export function TraceTimelinePanel({ jobId }: { jobId: string | undefined }) {
  const { data, isLoading, error } = useTraceTimeline(jobId)

  if (isLoading) return <LoadingBlock label="Building timeline" />
  if (error) return <ErrorBlock error={error} />
  if (!data || data.steps.length === 0) return null

  const last = data.steps[data.steps.length - 1]
  const totalElapsed = data.steps.reduce(
    (sum, step) => sum + (step.elapsed_since_previous_seconds ?? 0),
    0,
  )

  return (
    <Card>
      <CardHeader>
        <CardTitle>Timeline</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {totalElapsed > 0 && (
          <p className="rounded-lg border border-blue-500/30 bg-blue-500/10 px-3 py-2 text-sm text-blue-200">
            Funds reached {last.to_label} in {formatDuration(totalElapsed)} after the victim's first
            payment.
          </p>
        )}
        <ol className="space-y-0">
          {data.steps.map((step, index) => (
            <li key={step.tx_hash + step.step} className="relative pb-5 pl-6 last:pb-0">
              {index < data.steps.length - 1 && (
                <span className="absolute left-[7px] top-3 h-full w-px bg-[--color-line]" />
              )}
              <span className="absolute left-0 top-1 h-3.5 w-3.5 rounded-full border-2 border-blue-400 bg-[--color-surface-1]" />
              <div className="mono text-xs text-[--color-ink-faint]">
                {new Date(step.timestamp).toLocaleTimeString('en-IN', { hour12: false })}
                {step.elapsed_since_previous_human && (
                  <span className="ml-2 text-[--color-ink-faint]">
                    (+{step.elapsed_since_previous_human})
                  </span>
                )}
              </div>
              <p className="mt-0.5 text-sm font-medium">
                {step.from_label} ({shortAddress(step.from_address)}) → {step.to_label} (
                {shortAddress(step.to_address)})
              </p>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-[--color-ink-muted]">
                <span className="tabular-nums">
                  {step.amount} {step.asset} ({formatUsd(step.amount_usd)})
                </span>
                <span className="mono flex items-center gap-1">
                  {shortHash(step.tx_hash)}
                  <CopyButton value={step.tx_hash} />
                </span>
              </div>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  )
}
