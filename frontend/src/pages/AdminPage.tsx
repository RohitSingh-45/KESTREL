import { useAuditLog, useMockNcrpPush, useSetDataMode, useSystemStatus } from '@/api/hooks'
import { useAuth } from '@/auth/AuthContext'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { NativeSelect } from '@/components/ui/input'
import { EmptyState, ErrorBlock, LoadingBlock, StatTile } from '@/components/ui/misc'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table'
import { formatDateTime } from '@/lib/format'
import type { DataMode } from '@/types'

export function AdminPage() {
  const { hasRole } = useAuth()
  const { data: status, isLoading, error } = useSystemStatus()
  const { data: audit } = useAuditLog({ limit: 40 })
  const setMode = useSetDataMode()
  const mockPush = useMockNcrpPush()

  if (!hasRole('admin')) {
    return <EmptyState title="Admin only" description="This panel is restricted to administrators." />
  }
  if (isLoading || !status) return <LoadingBlock label="Loading system status" />
  if (error) return <ErrorBlock error={error} className="m-6" />

  return (
    <>
      <PageHeader
        title="Admin"
        description="Data mode, provider keys, label inventory and the audit trail."
      />
      <PageBody className="space-y-5">
        <div className="grid gap-3 md:grid-cols-4">
          <StatTile label="Data mode" value={status.data_mode} sub={`env ${status.configured_data_mode}`} />
          <StatTile label="Cache" value={status.cache_backend} />
          <StatTile label="Jobs" value={status.job_backend} />
          <StatTile label="Scenarios" value={status.synthetic_scenarios} />
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Runtime data mode</CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap items-center gap-3">
            <NativeSelect
              value={status.data_mode}
              onChange={(e) => setMode.mutate(e.target.value as DataMode)}
              className="w-48"
            >
              <option value="hybrid">hybrid — live then synthetic fallback</option>
              <option value="synthetic">synthetic — seeded dataset only</option>
              <option value="live">live — explorer APIs only</option>
            </NativeSelect>
            <Button variant="outline" onClick={() => mockPush.mutate()} loading={mockPush.isPending}>
              Push mock NCRP complaint
            </Button>
            {mockPush.data && (
              <span className="text-xs text-emerald-300">Created {mockPush.data.case_number}</span>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Providers</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <THead>
                <TR>
                  <TH>Provider</TH>
                  <TH>Chains</TH>
                  <TH>Key</TH>
                  <TH>Rate</TH>
                  <TH>Detail</TH>
                </TR>
              </THead>
              <TBody>
                {status.providers.map((p) => (
                  <TR key={p.name}>
                    <TD>{p.name}</TD>
                    <TD className="text-xs">{p.chains.join(', ')}</TD>
                    <TD>
                      <Badge variant={p.configured ? 'success' : 'medium'}>
                        {p.configured ? 'configured' : 'missing'}
                      </Badge>
                    </TD>
                    <TD>{p.rate_limit_per_sec}/s</TD>
                    <TD className="text-xs text-[--color-ink-muted]">{p.detail}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Label sources</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <THead>
                <TR>
                  <TH>Source</TH>
                  <TH>Labels</TH>
                  <TH>Sanctioned</TH>
                </TR>
              </THead>
              <TBody>
                {status.label_sources.map((s) => (
                  <TR key={s.source}>
                    <TD>{s.source}</TD>
                    <TD>{s.labels}</TD>
                    <TD>{s.sanctioned}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Audit log</CardTitle>
          </CardHeader>
          <CardContent>
            <Table>
              <THead>
                <TR>
                  <TH>When</TH>
                  <TH>Actor</TH>
                  <TH>Action</TH>
                  <TH>Resource</TH>
                </TR>
              </THead>
              <TBody>
                {audit?.items.map((row) => (
                  <TR key={row.id}>
                    <TD className="whitespace-nowrap text-xs">{formatDateTime(row.ts)}</TD>
                    <TD className="text-xs">
                      {row.actor_email}
                      <span className="text-[--color-ink-faint]"> · {row.actor_role}</span>
                    </TD>
                    <TD className="text-xs">{row.action}</TD>
                    <TD className="text-xs">
                      {row.resource_type} {row.resource_id ?? ''}
                    </TD>
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
