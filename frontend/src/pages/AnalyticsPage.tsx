import * as React from 'react'
import type { ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import {
  useAnalyticsOverview,
  useChainStats,
  useNetworkGraph,
  usePerformanceStats,
  useTypologyStats,
  useVaspInflows,
} from '@/api/hooks'
import { FundFlowGraph } from '@/components/graph/FundFlowGraph'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { NativeSelect } from '@/components/ui/input'
import { EmptyState, LoadingBlock, StatTile } from '@/components/ui/misc'
import { formatInr, formatPercent, formatUsd, shortAddress } from '@/lib/format'

const COLORS = ['#38bdf8', '#22c55e', '#a855f7', '#f97316', '#eab308', '#ef4444', '#64748b']
const DAY_OPTIONS = [
  { value: '', label: 'All time' },
  { value: '7', label: 'Last 7 days' },
  { value: '30', label: 'Last 30 days' },
  { value: '90', label: 'Last 90 days' },
]

function ChartCard({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
      </CardHeader>
      <CardContent className="h-64">{children}</CardContent>
    </Card>
  )
}

export function AnalyticsPage() {
  const navigate = useNavigate()
  const [days, setDays] = React.useState<string>('')
  const daysNum = days ? Number(days) : undefined

  const { data: overview, isLoading } = useAnalyticsOverview(daysNum)
  const { data: typologies } = useTypologyStats()
  const { data: vasps } = useVaspInflows()
  const { data: chains } = useChainStats()
  const { data: perf } = usePerformanceStats()
  const { data: network, isLoading: networkLoading } = useNetworkGraph({ days: daysNum })

  if (isLoading || !overview) return <LoadingBlock label="Loading analytics" />

  const crossCaseCount = Number(
    (network?.summary as Record<string, unknown> | null | undefined)?.cross_case_addresses ?? 0,
  )

  return (
    <>
      <PageHeader
        title="Analytics"
        description="Caseload, receiving VASPs, typology mix, time-to-attribution and the full network of everything traced so far."
        actions={
          <NativeSelect
            value={days}
            onChange={(e) => setDays(e.target.value)}
            className="w-40"
          >
            {DAY_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </NativeSelect>
        }
      />
      <PageBody className="space-y-5">
        <div className="grid gap-3 md:grid-cols-4">
          <StatTile label="Cases" value={overview.cases_total} sub={`${overview.cases_open} open`} />
          <StatTile
            label="Attribution rate"
            value={formatPercent(overview.attribution_rate)}
            sub={`${overview.vasps_identified} VASP hits`}
          />
          <StatTile label="Traced value" value={formatUsd(overview.traced_value_usd)} />
          <StatTile label="Reported loss" value={formatInr(overview.total_loss_inr)} />
        </div>
        <div className="grid gap-3 md:grid-cols-4">
          <StatTile label="Median trace" value={`${Math.round(overview.median_trace_ms)} ms`} />
          <StatTile label="Median hops to VASP" value={overview.median_hops_to_vasp.toFixed(1)} />
          <StatTile label="Open alerts" value={overview.alerts_open} sub={`${overview.alerts_overdue} overdue`} />
          <StatTile
            label="vs manual baseline"
            value={perf ? `${perf.manual_baseline_hours}h` : '72h'}
            sub="Typical investigator turnaround"
          />
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <ChartCard title="Cases by status">
            <ResponsiveContainer>
              <BarChart data={overview.cases_by_status}>
                <CartesianGrid stroke="#22304a" vertical={false} />
                <XAxis dataKey="label" tick={{ fill: '#93a3bd', fontSize: 11 }} />
                <YAxis tick={{ fill: '#93a3bd', fontSize: 11 }} />
                <Tooltip contentStyle={{ background: '#131c2e', border: '1px solid #22304a' }} />
                <Bar dataKey="count" fill="#38bdf8" radius={4} />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
          <ChartCard title="Fraud categories">
            <ResponsiveContainer>
              <PieChart>
                <Pie data={overview.cases_by_category} dataKey="count" nameKey="label" innerRadius={48} outerRadius={80}>
                  {overview.cases_by_category.map((_, i) => (
                    <Cell key={i} fill={COLORS[i % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip contentStyle={{ background: '#131c2e', border: '1px solid #22304a' }} />
              </PieChart>
            </ResponsiveContainer>
          </ChartCard>
          <ChartCard title="Typologies">
            <ResponsiveContainer>
              <BarChart data={typologies?.distribution ?? []}>
                <CartesianGrid stroke="#22304a" vertical={false} />
                <XAxis dataKey="label" tick={{ fill: '#93a3bd', fontSize: 10 }} interval={0} angle={-20} height={60} />
                <YAxis tick={{ fill: '#93a3bd', fontSize: 11 }} />
                <Tooltip contentStyle={{ background: '#131c2e', border: '1px solid #22304a' }} />
                <Bar dataKey="count" fill="#a855f7" radius={4} />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
          <ChartCard title="Top receiving VASPs">
            <ResponsiveContainer>
              <BarChart data={vasps?.top_receiving ?? []} layout="vertical">
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="label" width={110} tick={{ fill: '#93a3bd', fontSize: 11 }} />
                <Tooltip contentStyle={{ background: '#131c2e', border: '1px solid #22304a' }} />
                <Bar dataKey="value_usd" fill="#22c55e" radius={4} />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
          <ChartCard title="Chains">
            <ResponsiveContainer>
              <BarChart data={chains ?? []}>
                <CartesianGrid stroke="#22304a" vertical={false} />
                <XAxis dataKey="label" tick={{ fill: '#93a3bd', fontSize: 11 }} />
                <YAxis tick={{ fill: '#93a3bd', fontSize: 11 }} />
                <Tooltip contentStyle={{ background: '#131c2e', border: '1px solid #22304a' }} />
                <Bar dataKey="count" fill="#f97316" radius={4} />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
          <ChartCard title="Attribution method">
            <ResponsiveContainer>
              <BarChart data={perf?.by_method ?? []}>
                <CartesianGrid stroke="#22304a" vertical={false} />
                <XAxis dataKey="label" tick={{ fill: '#93a3bd', fontSize: 10 }} />
                <YAxis tick={{ fill: '#93a3bd', fontSize: 11 }} />
                <Tooltip contentStyle={{ background: '#131c2e', border: '1px solid #22304a' }} />
                <Bar dataKey="count" fill="#eab308" radius={4} />
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>

        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Recent VASP attributions</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {!overview.recent_attributions?.length && (
                <p className="text-xs text-[--color-ink-faint]">No attributed traces yet.</p>
              )}
              {overview.recent_attributions?.map((row) => (
                <button
                  key={row.key}
                  onClick={() => row.extra?.case_id && navigate(`/cases/${row.extra.case_id}`)}
                  className="flex w-full items-center justify-between gap-3 rounded-lg border border-[--color-line] px-3 py-2 text-left text-xs transition-colors hover:border-blue-500/40 hover:bg-[--color-surface-2]"
                >
                  <span className="flex items-center gap-2">
                    <Badge variant="success">{row.label}</Badge>
                    <span className="text-[--color-ink-faint]">{String(row.extra?.chain ?? '')}</span>
                    <span className="mono text-[--color-ink-faint]">
                      {shortAddress(String(row.extra?.address ?? ''))}
                    </span>
                  </span>
                  <span className="flex items-center gap-3 tabular-nums text-[--color-ink-muted]">
                    <span>hop {row.count}</span>
                    <span>{formatUsd(row.value_usd)}</span>
                    <span>{formatPercent(Number(row.extra?.confidence ?? 0))}</span>
                  </span>
                </button>
              ))}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>VASP jurisdiction mix</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid grid-cols-2 gap-3">
                <StatTile label="Indian entity" value={formatPercent((vasps?.indian_share_pct ?? 0) / 100, 0)} />
                <StatTile
                  label="FIU-IND registered"
                  value={formatPercent((vasps?.fiu_registered_share_pct ?? 0) / 100, 0)}
                />
              </div>
              <div className="space-y-1.5">
                {(vasps?.by_jurisdiction ?? []).slice(0, 6).map((row) => (
                  <div key={row.key} className="flex items-center justify-between text-xs">
                    <span className="text-[--color-ink-muted]">{row.label}</span>
                    <span className="tabular-nums text-[--color-ink-faint]">{row.count}</span>
                  </div>
                ))}
              </div>
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Full network graph</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <p className="text-xs text-[--color-ink-muted]">
              Every completed trace merged onto one canvas, capped to the highest-taint
              addresses. An address reached by more than one case is a lead worth checking
              on its own — it may mean two victims paid into the same laundering
              infrastructure. Click a wallet to open its full profile.
            </p>
            {crossCaseCount > 0 && (
              <p className="rounded-lg border border-orange-500/30 bg-orange-500/10 px-3 py-2 text-sm text-orange-200">
                {crossCaseCount} address{crossCaseCount === 1 ? '' : 'es'} in this network were
                reached by more than one case — possible shared fraud infrastructure.
              </p>
            )}
            {networkLoading && <LoadingBlock label="Building network graph" />}
            {!networkLoading && !network?.nodes.length && (
              <EmptyState
                title="No completed traces yet"
                description="Run a trace to start building the network view."
              />
            )}
            {!!network?.nodes.length && (
              <FundFlowGraph
                nodes={network.nodes}
                edges={network.edges}
                height={560}
                onSelect={(node) => node && navigate(`/wallets/${node.chain}/${node.address}`)}
              />
            )}
          </CardContent>
        </Card>
      </PageBody>
    </>
  )
}
