import { Check, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { TraceEvent } from '@/types'

/**
 * Four hop-level SSE events (started/hop_started/hop_completed/analysing) do
 * not map 1:1 to a story a non-technical viewer can follow along with, so
 * this collapses them into the four stages an investigator actually cares
 * about, each with a running count pulled from whatever the latest event
 * happens to carry.
 */
type StageKey = 'fetch' | 'graph' | 'tracing' | 'analysing'

const STAGES: { key: StageKey; label: string }[] = [
  { key: 'fetch', label: 'Fetching transactions' },
  { key: 'graph', label: 'Building transaction graph' },
  { key: 'tracing', label: 'Tracing fund flow' },
  { key: 'analysing', label: 'Analysing behaviour' },
]

function stageStatus(
  stage: StageKey,
  event: string | undefined,
  hop: number,
): 'done' | 'active' | 'pending' {
  const order: StageKey[] = ['fetch', 'graph', 'tracing', 'analysing']
  const currentIndex =
    event === 'completed'
      ? 4
      : event === 'analysing'
        ? 3
        : event === 'hop_completed' || event === 'hop_started'
          ? hop > 0
            ? 2
            : 1
          : event === 'started'
            ? 0
            : -1
  const stageIndex = order.indexOf(stage)
  if (currentIndex > stageIndex) return 'done'
  if (currentIndex === stageIndex) return 'active'
  return 'pending'
}

function stageDetail(stage: StageKey, progress: Record<string, unknown>): string | null {
  const transfers = Number(progress.transfers_examined ?? 0)
  const addresses = Number(progress.addresses_examined ?? 0)
  const edges = Number(progress.edges ?? 0)
  const hop = Number(progress.hop ?? 0)
  const vaspHits = Number(progress.vasp_hits ?? 0)
  switch (stage) {
    case 'fetch':
      return transfers > 0 ? `${transfers} transactions found` : null
    case 'graph':
      return addresses > 0 ? `${addresses} addresses, ${edges} edges` : null
    case 'tracing':
      return hop > 0 ? `${hop} hop${hop === 1 ? '' : 's'} identified` : null
    case 'analysing':
      return vaspHits > 0 ? `${vaspHits} exchange endpoint${vaspHits === 1 ? '' : 's'} found` : null
    default:
      return null
  }
}

export function StageChecklist({ event }: { event: TraceEvent | null }) {
  const progress = (event ?? {}) as Record<string, unknown>
  const currentEvent = event?.event
  const hop = Number(progress.hop ?? 0)

  return (
    <div className="space-y-2.5">
      {STAGES.map((stage) => {
        const status = stageStatus(stage.key, currentEvent, hop)
        const detail = status !== 'pending' ? stageDetail(stage.key, progress) : null
        return (
          <div key={stage.key} className="flex items-center gap-3">
            <span
              className={cn(
                'flex h-5 w-5 shrink-0 items-center justify-center rounded-full border text-[10px]',
                status === 'done' && 'border-emerald-500/50 bg-emerald-500/15 text-emerald-300',
                status === 'active' && 'border-sky-500/50 bg-sky-500/15 text-sky-300',
                status === 'pending' && 'border-[--color-line] bg-[--color-surface-2] text-[--color-ink-faint]',
              )}
            >
              {status === 'done' && <Check className="h-3 w-3" />}
              {status === 'active' && <Loader2 className="h-3 w-3 animate-spin" />}
            </span>
            <div className="min-w-0">
              <p
                className={cn(
                  'text-sm',
                  status === 'pending' ? 'text-[--color-ink-faint]' : 'text-[--color-ink]',
                )}
              >
                {stage.label}
                {status === 'active' && <span className="text-[--color-ink-faint]">...</span>}
              </p>
              {detail && <p className="text-xs text-emerald-300/90">✓ {detail}</p>}
            </div>
          </div>
        )
      })}
    </div>
  )
}
