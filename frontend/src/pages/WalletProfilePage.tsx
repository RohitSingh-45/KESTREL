import { useNavigate, useParams } from 'react-router-dom'
import { useAttribution, useWallet } from '@/api/hooks'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Badge, RiskBadge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { CopyButton, EmptyState, ErrorBlock, LoadingBlock, Mono, StatTile } from '@/components/ui/misc'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table'
import { formatAmount, formatDateTime, formatPercent, formatUsd, shortAddress, titleCase } from '@/lib/format'
import { CHAIN_LABEL, entityLabel } from '@/lib/labels'

export function WalletProfilePage() {
  const { chain, address } = useParams()
  const navigate = useNavigate()
  const { data, isLoading, error } = useWallet(chain, address)
  const { data: attribution } = useAttribution(chain, address)

  if (isLoading) return <LoadingBlock label="Profiling wallet" />
  if (error) return <ErrorBlock error={error} className="m-6" />
  if (!data) return <EmptyState title="Wallet not found" />

  return (
    <>
      <PageHeader
        title={data.entity_name || shortAddress(data.address)}
        description={`${CHAIN_LABEL[data.chain]} · ${data.data_source}`}
        actions={
          <Button onClick={() => navigate(`/trace?address=${data.address}&chain=${data.chain}`)}>
            Trace from here
          </Button>
        }
      />
      <PageBody className="space-y-5">
        <div className="flex flex-wrap items-center gap-2">
          <Mono className="text-xs">{data.address}</Mono>
          <CopyButton value={data.address} />
          <Badge>{entityLabel(data.entity_type)}</Badge>
          <RiskBadge level={data.risk_level} score={data.risk_score} />
          {data.is_sanctioned && <Badge variant="critical">Sanctioned</Badge>}
        </div>

        <div className="grid gap-3 md:grid-cols-4">
          <StatTile label="Received" value={formatUsd(data.total_received_usd)} />
          <StatTile label="Sent" value={formatUsd(data.total_sent_usd)} />
          <StatTile label="Tx count" value={data.tx_count} sub={`${data.in_degree} in / ${data.out_degree} out`} />
          <StatTile
            label="Balance"
            value={data.balance_native ? `${data.balance_native} ${data.native_asset}` : '—'}
          />
        </div>

        {!!data.risk_reasons.length && (
          <Card>
            <CardHeader>
              <CardTitle>Why this risk score</CardTitle>
            </CardHeader>
            <CardContent className="space-y-1 text-sm text-[--color-ink-muted]">
              {data.risk_reasons.map((reason) => (
                <p key={reason}>• {reason}</p>
              ))}
            </CardContent>
          </Card>
        )}

        {attribution && (
          <Card>
            <CardHeader>
              <CardTitle>Attribution candidates</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {attribution.candidates.map((c, i) => (
                <div key={i} className="flex items-center justify-between rounded-lg border border-[--color-line] px-3 py-2">
                  <div>
                    <p className="text-sm font-medium">{c.entity_name}</p>
                    <p className="text-xs text-[--color-ink-muted]">
                      {titleCase(c.method)} · {entityLabel(c.entity_type)}
                    </p>
                  </div>
                  <Badge variant="brand">{formatPercent(c.confidence)}</Badge>
                </div>
              ))}
              {!attribution.candidates.length && <p className="text-xs text-[--color-ink-muted]">No labelled candidate.</p>}
            </CardContent>
          </Card>
        )}

        {!!data.labels.length && (
          <div className="flex flex-wrap gap-2">
            {data.labels.map((label) => (
              <Badge key={`${label.source}-${label.label}`} variant={label.is_sanctioned ? 'critical' : 'default'}>
                {label.label} · {label.source}
              </Badge>
            ))}
          </div>
        )}

        {!!data.typologies.length && (
          <div className="flex flex-wrap gap-2">
            {data.typologies.map((t) => (
              <Badge key={t.typology} variant="purple">
                {t.label} {formatPercent(t.confidence)}
              </Badge>
            ))}
          </div>
        )}

        <Card>
          <CardHeader>
            <CardTitle>Counterparties</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <THead>
                <TR>
                  <TH>Address</TH>
                  <TH>Dir</TH>
                  <TH>Count</TH>
                  <TH>USD</TH>
                </TR>
              </THead>
              <TBody>
                {data.counterparties.map((c) => (
                  <TR
                    key={`${c.address}-${c.direction}`}
                    clickable
                    onClick={() => navigate(`/wallets/${c.chain}/${c.address}`)}
                  >
                    <TD className="mono text-xs">{shortAddress(c.address)}</TD>
                    <TD>{c.direction}</TD>
                    <TD>{c.transfer_count}</TD>
                    <TD>{formatUsd(c.total_usd)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Recent transfers</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <THead>
                <TR>
                  <TH>When</TH>
                  <TH>Dir</TH>
                  <TH>Counterparty</TH>
                  <TH>Amount</TH>
                  <TH>Tx</TH>
                </TR>
              </THead>
              <TBody>
                {data.recent_transfers.map((t) => (
                  <TR key={`${t.tx_hash}-${t.counterparty}`}>
                    <TD className="text-xs">{formatDateTime(t.ts)}</TD>
                    <TD>{t.direction}</TD>
                    <TD className="mono text-xs">{shortAddress(t.counterparty)}</TD>
                    <TD>{formatAmount(t.amount, t.asset)}</TD>
                    <TD className="mono text-xs">{shortAddress(t.tx_hash, 8, 6)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>
      </PageBody>
    </>
  )
}
