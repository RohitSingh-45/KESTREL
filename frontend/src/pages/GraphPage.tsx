import * as React from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { useCaseGraph, useTraceGraph, useTraceJobs } from '@/api/hooks'
import { SankeyLike, TimelineView } from '@/components/graph/AlternateViews'
import { FundFlowGraph } from '@/components/graph/FundFlowGraph'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Badge, RiskBadge } from '@/components/ui/badge'
import { NativeSelect } from '@/components/ui/input'
import { SidePanel } from '@/components/ui/dialog'
import { EmptyState, LoadingBlock, Mono } from '@/components/ui/misc'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { formatPercent, formatUsd, shortAddress, titleCase } from '@/lib/format'
import { entityLabel } from '@/lib/labels'
import type { GraphNode } from '@/types'

export function GraphPage() {
  const { jobId } = useParams()
  const navigate = useNavigate()
  const { data: jobs } = useTraceJobs()
  const selected = jobId ?? jobs?.items.find((j) => j.status === 'completed')?.id
  const { data: graph, isLoading } = useTraceGraph(selected)
  const [node, setNode] = React.useState<GraphNode | null>(null)

  return (
    <>
      <PageHeader
        title="Fund flow"
        description="Primary path is highlighted. Click a node for the detail drawer."
        actions={
          <NativeSelect
            value={selected ?? ''}
            onChange={(e) => navigate(e.target.value ? `/graph/${e.target.value}` : '/graph')}
            className="w-72"
          >
            <option value="">Select a completed trace</option>
            {jobs?.items
              .filter((j) => j.status === 'completed')
              .map((job) => (
                <option key={job.id} value={job.id}>
                  {job.chain} · {shortAddress(job.seed_address)}
                </option>
              ))}
          </NativeSelect>
        }
      />
      <PageBody>
        {!selected && <EmptyState title="Pick a completed trace to visualise" />}
        {isLoading && selected && <LoadingBlock label="Loading graph" />}
        {graph && (
          <Tabs defaultValue="graph">
            <TabsList>
              <TabsTrigger value="graph">Graph</TabsTrigger>
              <TabsTrigger value="sankey">Value path</TabsTrigger>
              <TabsTrigger value="timeline">Timeline</TabsTrigger>
            </TabsList>
            <TabsContent value="graph">
              <FundFlowGraph nodes={graph.nodes} edges={graph.edges} onSelect={setNode} />
            </TabsContent>
            <TabsContent value="sankey">
              <SankeyLike edges={graph.edges} />
            </TabsContent>
            <TabsContent value="timeline">
              <TimelineView edges={graph.edges} />
            </TabsContent>
          </Tabs>
        )}
      </PageBody>
      <SidePanel
        open={Boolean(node)}
        onOpenChange={(open) => !open && setNode(null)}
        title={node?.entity_name || shortAddress(node?.address ?? '')}
        description={node?.address}
      >
        {node && (
          <div className="space-y-3 text-sm">
            <div className="flex flex-wrap gap-2">
              <Badge>{entityLabel(node.entity_type)}</Badge>
              <RiskBadge level={node.risk_level as 'low' | 'medium' | 'high' | 'critical'} score={node.risk_score} />
              {node.is_seed && <Badge variant="brand">Seed</Badge>}
              {node.is_terminal && <Badge variant="success">Terminal</Badge>}
              {node.is_sanctioned && <Badge variant="critical">Sanctioned</Badge>}
            </div>
            <p>Hop {node.hop} · taint {formatPercent(node.taint)}</p>
            <p>In {formatUsd(node.value_in_usd)} · Out {formatUsd(node.value_out_usd)}</p>
            {node.terminal_explanation && (
              <p className="text-xs text-[--color-ink-muted]">{node.terminal_explanation}</p>
            )}
            {node.attribution_method && (
              <p className="text-xs">
                {titleCase(node.attribution_method)} · {formatPercent(node.attribution_confidence)}
              </p>
            )}
            <button
              className="text-xs text-sky-300 hover:underline"
              onClick={() => navigate(`/wallets/${node.chain}/${node.address}`)}
            >
              Open wallet profile
            </button>
            <Mono className="block break-all text-[11px]">{node.address}</Mono>
          </div>
        )}
      </SidePanel>
    </>
  )
}

export function CaseGraphPage({ caseId }: { caseId: string }) {
  const { data } = useCaseGraph(caseId)
  if (!data) return null
  return <FundFlowGraph nodes={data.nodes} edges={data.edges} height={360} />
}
