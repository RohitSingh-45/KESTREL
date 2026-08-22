import * as React from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { useGenerateReport, useTraceGraph, useTraceJob, traceStreamUrl } from '@/api/hooks'
import { FundFlowGraph } from '@/components/graph/FundFlowGraph'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Badge, RiskBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { CopyButton, ErrorBlock, LoadingBlock, ProgressBar, StatTile } from '@/components/ui/misc'
import { formatPercent, formatUsd, shortAddress, titleCase } from '@/lib/format'
import { CHAIN_LABEL } from '@/lib/labels'
import type { TraceEvent } from '@/types'

export function TraceDetailPage() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const { data: job, isLoading, error } = useTraceJob(jobId, true)
  const running = job?.status === 'queued' || job?.status === 'running'
  const { data: graph } = useTraceGraph(running ? undefined : jobId)
  const generate = useGenerateReport()
  const [live, setLive] = React.useState<TraceEvent | null>(null)

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
  const risk = job.summary?.risk
  const progress = (live ?? job.progress) as Record<string, unknown>
  const examined = Number(progress.addresses_examined ?? 0)
  const hop = Number(progress.hop ?? 0)

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
            <CardContent className="pt-5">
              <p className="mb-2 text-xs text-[--color-ink-muted]">
                {(progress.event as string) ?? 'queued'} · hop {hop} · {examined} addresses examined
              </p>
              <ProgressBar value={Math.min(95, hop * 18 + examined / 4)} />
            </CardContent>
          </Card>
        )}

        {job.error && <ErrorBlock error={job.error} />}

        {finding && (
          <Card>
            <CardHeader>
              <CardTitle>Attribution</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <p className="text-sm leading-relaxed">{finding.statement}</p>
              <div className="flex flex-wrap gap-2">
                {finding.entity_name && <Badge variant="success">{finding.entity_name}</Badge>}
                {finding.method && <Badge>{titleCase(finding.method)}</Badge>}
                {finding.confidence != null && (
                  <Badge variant="brand">{formatPercent(finding.confidence)} confidence</Badge>
                )}
                {risk?.level && <RiskBadge level={risk.level} score={risk.score} />}
              </div>
              {finding.address && (
                <p className="mono text-xs text-[--color-ink-muted]">
                  {finding.address} <CopyButton value={finding.address} />
                </p>
              )}
              {job.case_id && (
                <Link className="text-xs text-sky-300 hover:underline" to={`/cases/${job.case_id}`}>
                  Open parent case
                </Link>
              )}
            </CardContent>
          </Card>
        )}

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
            <CardContent>
              <FundFlowGraph nodes={graph.nodes} edges={graph.edges} height={420} />
            </CardContent>
          </Card>
        )}

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
    </>
  )
}
