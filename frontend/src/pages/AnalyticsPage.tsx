import type { ReactNode } from 'react'
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
  usePerformanceStats,
  useTypologyStats,
  useVaspInflows,
} from '@/api/hooks'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { LoadingBlock, StatTile } from '@/components/ui/misc'
import { formatInr, formatPercent, formatUsd } from '@/lib/format'

const COLORS = ['#38bdf8', '#22c55e', '#a855f7', '#f97316', '#eab308', '#ef4444', '#64748b']

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
  const { data: overview, isLoading } = useAnalyticsOverview()
  const { data: typologies } = useTypologyStats()
  const { data: vasps } = useVaspInflows()
  const { data: chains } = useChainStats()
  const { data: perf } = usePerformanceStats()

  if (isLoading || !overview) return <LoadingBlock label="Loading analytics" />

  return (
    <>
      <PageHeader
        title="Analytics"
        description="Caseload, receiving VASPs, typology mix and time-to-attribution."
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
      </PageBody>
    </>
  )
}
