import * as React from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useGenerateReport, useTraceGraph, useTraceJob, traceStreamUrl } from '@/api/hooks'
import { FundFlowGraph } from '@/components/graph/FundFlowGraph'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { ErrorBlock, LoadingBlock, StatTile } from '@/components/ui/misc'
import { InvestigationResultCard } from '@/components/trace/InvestigationResultCard'
import { NodeDetailPanel } from '@/components/trace/NodeDetailPanel'
import { StageChecklist } from '@/components/trace/StageChecklist'
import { TraceTimelinePanel } from '@/components/trace/TraceTimelinePanel'
import { formatPercent, formatUsd, shortAddress, titleCase } from '@/lib/format'
import { CHAIN_LABEL } from '@/lib/labels'
import type { GraphNode, TraceEvent } from '@/types'

export function TraceDetailPage() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const { data: job, isLoading, error } = useTraceJob(jobId, true)
  // While `job` hasn't loaded yet, treat it as still running rather than
  // not-running - otherwise the graph query fires immediately on first
  // mount, before the trace has even started server-side, and caches an
  // empty result that never gets refreshed once the trace actually finishes.
  const running = !job || job.status === 'queued' || job.status === 'running'
  const { data: graph } = useTraceGraph(running ? undefined : jobId)
  const generate = useGenerateReport()
  const [live, setLive] = React.useState<TraceEvent | null>(null)
  const [selectedNode, setSelectedNode] = React.useState<GraphNode | null>(null)

  React.useEffect(() => {
    if (!jobId || !running) return
    const source = new EventSource(traceStreamUrl(jobId))
    const onEvent = (event: MessageEvent) => {
      try {
        setLive(JSON.parse(event.data) as TraceEvent)
      } catch {
        /* ignore */
      }
    }
    source.addEventListener('progress', onEvent)
    source.addEventListener('analysing', onEvent)
    source.addEventListener('completed', onEvent)
    source.addEventListener('failed', onEvent)
    source.onmessage = onEvent
    return () => source.close()
  }, [jobId, running])

  if (isLoading) return <LoadingBlock label="Loading trace" />
  if (error) return <ErrorBlock error={error} className="m-6" />
  if (!job) return null

  const finding = job.summary?.attribution
  const progress = (live ?? job.progress) as Record<string, unknown>
  // The terminal "completed" SSE event carries the full summary, not the
  // hop/addresses_examined counters hop-level events do, so once the job is
  // done those come from the final stats instead of the last progress tick.
  const stats = job.summary?.stats
  const examined = Number(
    (job.status === 'completed' ? stats?.addresses_examined : undefined) ??
      progress.addresses_examined ??
      0,
  )
  const hop = Number(
    (job.status === 'completed' ? stats?.hops_completed : undefined) ?? progress.hop ?? 0,
  )

  return (
    <>
      <PageHeader
        title="Live trace"
        description={`${CHAIN_LABEL[job.chain]} · ${shortAddress(job.seed_address)}`}
        actions={
          <div className="flex gap-2">
            {graph && (
              <Button variant="outline" onClick={() => navigate(`/graph/${job.id}`)}>
                Open graph
              </Button>
            )}
            {job.case_id && finding?.found && (
              <Button
                onClick={() =>
                  generate.mutate({ caseId: job.case_id!, kind: 'bnss_94_request', job_id: job.id })
                }
                loading={generate.isPending}
              >
                Generate freeze request
              </Button>
            )}
          </div>
        }
      />
      <PageBody className="space-y-5">
        <div className="grid gap-3 md:grid-cols-5">
          <StatTile label="Status" value={titleCase(job.status)} accent={job.status === 'failed' ? 'text-red-400' : undefined} />
          <StatTile label="Hop" value={`${hop}`} />
          <StatTile label="Addresses" value={examined} />
          <StatTile label="Data" value={job.data_source} />
          <StatTile label="Duration" value={job.duration_ms ? `${job.duration_ms} ms` : '—'} />
        </div>

        {running && (
          <Card>
            <CardHeader>
              <CardTitle>Analysing</CardTitle>
            </CardHeader>
            <CardContent>
              <StageChecklist event={live} />
            </CardContent>
          </Card>
        )}

        {job.error && <ErrorBlock error={job.error} />}

        {job.status === 'completed' && <InvestigationResultCard job={job} />}

        {!!job.summary?.recommendations?.length && (
          <Card>
            <CardHeader>
              <CardTitle>Recommended actions</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {job.summary.recommendations.map((rec, index) => (
                <div key={index} className="rounded-lg border border-[--color-line] px-3 py-2">
                  <p className="text-sm font-medium">{rec.action}</p>
                  <p className="text-xs text-[--color-ink-muted]">{rec.rationale}</p>
                </div>
              ))}
            </CardContent>
          </Card>
        )}

        {graph && (
          <Card>
            <CardHeader>
              <CardTitle>Fund flow</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              <p className="text-xs text-[--color-ink-muted]">Click a wallet to see every transaction it was part of.</p>
              <FundFlowGraph
                nodes={graph.nodes}
                edges={graph.edges}
                height={420}
                onSelect={setSelectedNode}
              />
            </CardContent>
          </Card>
        )}

        {job.status === 'completed' && <TraceTimelinePanel jobId={job.id} />}

        {!!job.summary?.cross_chain_links?.length && (
          <Card>
            <CardHeader>
              <CardTitle>Probable bridge links</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-xs text-[--color-ink-muted]">
              <p>Heuristic inferences — not proven on-chain links. Confidence is shown per hop.</p>
              {job.summary.cross_chain_links.map((link, index) => (
                <p key={index}>
                  {link.bridge_name}: {link.src_chain} → {link.dst_chain} · {formatUsd(link.value_usd)} ·{' '}
                  {formatPercent(link.confidence)}
                </p>
              ))}
            </CardContent>
          </Card>
        )}
      </PageBody>
      <NodeDetailPanel
        jobId={job.id}
        address={selectedNode?.address ?? null}
        onClose={() => setSelectedNode(null)}
      />
    </>
  )
}
