import { Bar, BarChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { GraphEdge } from '@/types'
import { formatDateTime, formatUsd, shortAddress } from '@/lib/format'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table'

export function SankeyLike({ edges }: { edges: GraphEdge[] }) {
  const data = edges
    .filter((e) => e.is_on_primary_path)
    .sort((a, b) => a.hop - b.hop)
    .map((edge) => ({
      name: `${shortAddress(edge.source, 4, 4)} → ${shortAddress(edge.target, 4, 4)}`,
      usd: edge.amount_usd,
      hop: edge.hop,
    }))
  if (!data.length) {
    return <p className="py-8 text-center text-xs text-[--color-ink-muted]">No primary path to chart.</p>
  }
  return (
    <div className="h-72">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} layout="vertical" margin={{ left: 16, right: 16 }}>
          <XAxis type="number" hide />
          <YAxis type="category" dataKey="name" width={160} tick={{ fill: '#93a3bd', fontSize: 10 }} />
          <Tooltip
            formatter={(value: number) => formatUsd(value)}
            contentStyle={{ background: '#131c2e', border: '1px solid #22304a' }}
          />
          <Bar dataKey="usd" fill="#38bdf8" radius={4} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

export function TimelineView({ edges }: { edges: GraphEdge[] }) {
  const rows = [...edges].sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime())
  return (
    <Table>
      <THead>
        <TR>
          <TH>When (UTC)</TH>
          <TH>Hop</TH>
          <TH>From</TH>
          <TH>To</TH>
          <TH>Asset</TH>
          <TH>USD</TH>
          <TH>Tx</TH>
        </TR>
      </THead>
      <TBody>
        {rows.map((edge) => (
          <TR key={edge.id} className={edge.is_on_primary_path ? 'bg-sky-500/5' : undefined}>
            <TD className="whitespace-nowrap text-xs">{formatDateTime(edge.ts)}</TD>
            <TD>{edge.hop}</TD>
            <TD className="mono text-xs">{shortAddress(edge.source)}</TD>
            <TD className="mono text-xs">{shortAddress(edge.target)}</TD>
            <TD>
              {edge.amount} {edge.asset}
            </TD>
            <TD>{formatUsd(edge.amount_usd)}</TD>
            <TD className="mono text-xs">{shortAddress(edge.tx_hash, 8, 6)}</TD>
          </TR>
        ))}
      </TBody>
    </Table>
  )
}
