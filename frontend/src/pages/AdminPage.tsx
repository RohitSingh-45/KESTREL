import * as React from 'react'
import {
  KeyRound,
  LogIn,
  Megaphone,
  Search,
  Shuffle,
  Sliders,
  Tags,
  Users,
  type LucideIcon,
} from 'lucide-react'
import { useAuditLog, useMockNcrpPush, useSetDataMode, useSystemStatus } from '@/api/hooks'
import { useAuth } from '@/auth/AuthContext'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Badge, type BadgeProps } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input, NativeSelect } from '@/components/ui/input'
import { EmptyState, ErrorBlock, LoadingBlock, StatTile } from '@/components/ui/misc'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table'
import { formatDateTime, timeAgo } from '@/lib/format'
import type { AuditEntry, DataMode } from '@/types'

// Every action string the backend actually writes, mapped to a plain
// sentence and a category so the log reads as a story instead of dotted
// identifiers. If a new action shows up unmapped, it still renders (falls
// back to the raw string) rather than disappearing.
type Category = 'auth' | 'case' | 'trace' | 'alert' | 'report' | 'integration' | 'admin'

const CATEGORY: Record<Category, { label: string; variant: BadgeProps['variant']; icon: LucideIcon }> = {
  auth: { label: 'Auth', variant: 'brand', icon: LogIn },
  case: { label: 'Case', variant: 'success', icon: Users },
  trace: { label: 'Trace', variant: 'purple', icon: Search },
  alert: { label: 'Alert', variant: 'high', icon: Megaphone },
  report: { label: 'Report', variant: 'medium', icon: Tags },
  integration: { label: 'Integration', variant: 'default', icon: Shuffle },
  admin: { label: 'Admin', variant: 'critical', icon: Sliders },
}

const ACTION_LABEL: Record<string, { text: string; category: Category }> = {
  'auth.login': { text: 'Signed in', category: 'auth' },
  'case.create': { text: 'Created a case', category: 'case' },
  'case.read': { text: 'Opened a case', category: 'case' },
  'case.update': { text: 'Updated a case', category: 'case' },
  'case.address.add': { text: 'Added an address to a case', category: 'case' },
  'case.import': { text: 'Imported cases from a CSV', category: 'case' },
  'trace.start': { text: 'Started a trace', category: 'trace' },
  'attribution.lookup': { text: 'Looked up an address', category: 'trace' },
  'wallet.read': { text: 'Viewed a wallet profile', category: 'trace' },
  'alert.update': { text: 'Updated an alert', category: 'alert' },
  'report.generate': { text: 'Generated a report', category: 'report' },
  'report.download': { text: 'Downloaded a report', category: 'report' },
  'integration.ncrp.webhook': { text: 'Received an NCRP webhook complaint', category: 'integration' },
  'integration.ncrp.mock_push': { text: 'Pushed a mock NCRP complaint', category: 'integration' },
  'settings.data_mode': { text: 'Changed the data mode', category: 'admin' },
  'user.create': { text: 'Created a user', category: 'admin' },
}

function describeAction(action: string): { text: string; category: Category } {
  return ACTION_LABEL[action] ?? { text: action, category: 'admin' }
}

function AuditRow({ row }: { row: AuditEntry }) {
  const { text, category } = describeAction(row.action)
  const meta = CATEGORY[category]
  const Icon = meta.icon
  const hasDetails = row.details && Object.keys(row.details).length > 0

  return (
    <div className="flex flex-col gap-1.5 border-b border-[--color-line] py-3 last:border-0 md:flex-row md:items-start md:gap-4">
      <div className="flex items-center gap-2 md:w-40 md:shrink-0">
        <Icon className="h-3.5 w-3.5 text-[--color-ink-faint]" />
        <Badge variant={meta.variant}>{meta.label}</Badge>
      </div>
      <div className="flex-1 space-y-0.5">
        <p className="text-sm">
          {text}
          {row.resource_id && (
            <span className="text-[--color-ink-faint]"> · {row.resource_type} {row.resource_id.slice(0, 8)}</span>
          )}
        </p>
        <p className="text-xs text-[--color-ink-faint]">
          {row.actor_email ?? 'system'} ({row.actor_role ?? '—'}) · <span title={formatDateTime(row.ts)}>{timeAgo(row.ts)}</span>
          {row.ip_address && <> · {row.ip_address}</>}
        </p>
        {hasDetails && (
          <details>
            <summary className="cursor-pointer text-[11px] text-[--color-ink-faint] hover:text-[--color-ink-muted]">
              Raw detail
            </summary>
            <pre className="mono mt-1 max-w-full overflow-x-auto rounded-md bg-[--color-surface-2] p-2 text-[11px] text-[--color-ink-muted]">
              {JSON.stringify(row.details, null, 2)}
            </pre>
          </details>
        )}
      </div>
    </div>
  )
}

export function AdminPage() {
  const { hasRole } = useAuth()
  const { data: status, isLoading, error } = useSystemStatus()
  const [search, setSearch] = React.useState('')
  const [category, setCategory] = React.useState<Category | ''>('')
  const [visible, setVisible] = React.useState(40)
  const { data: audit } = useAuditLog({
    q: search || undefined,
    limit: visible,
  })
  const setMode = useSetDataMode()
  const mockPush = useMockNcrpPush()

  if (!hasRole('admin')) {
    return <EmptyState title="Admin only" description="This panel is restricted to administrators." />
  }
  if (isLoading || !status) return <LoadingBlock label="Loading system status" />
  if (error) return <ErrorBlock error={error} className="m-6" />

  const configuredProviders = status.providers.filter((p) => p.configured).length
  const usingRealCache = status.cache_backend.toLowerCase() !== 'memory'
  const rows = audit?.items.filter((row) =>
    category ? describeAction(row.action).category === category : true,
  )

  return (
    <>
      <PageHeader
        title="Admin"
        description="One picture of how the system is currently running, and everything that has happened on it."
      />
      <PageBody className="space-y-5">
        <div className="grid gap-3 md:grid-cols-4">
          <StatTile
            label="Data mode"
            value={status.data_mode}
            sub={
              status.data_mode === 'synthetic'
                ? 'Offline demo data only'
                : status.data_mode === 'live'
                  ? 'Explorer APIs only, no fallback'
                  : 'Live lookups, falls back to seeded data'
            }
          />
          <StatTile
            label="Cache"
            value={status.cache_backend}
            sub={usingRealCache ? 'Persists across restarts' : 'In-memory — resets on restart'}
            accent={usingRealCache ? undefined : 'text-yellow-400'}
          />
          <StatTile
            label="Providers"
            value={`${configuredProviders}/${status.providers.length}`}
            sub="Live explorer keys configured"
            accent={configuredProviders < status.providers.length ? 'text-yellow-400' : undefined}
          />
          <StatTile label="Jobs" value={status.job_backend} sub={`${status.synthetic_scenarios} demo scenarios seeded`} />
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

        <div className="grid gap-4 lg:grid-cols-2">
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
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Audit log</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              <div className="relative w-56">
                <KeyRound className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-[--color-ink-faint]" />
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Search actor or address"
                  className="pl-8"
                />
              </div>
              <NativeSelect
                value={category}
                onChange={(e) => setCategory(e.target.value as Category | '')}
                className="w-40"
              >
                <option value="">Every category</option>
                {(Object.keys(CATEGORY) as Category[]).map((key) => (
                  <option key={key} value={key}>
                    {CATEGORY[key].label}
                  </option>
                ))}
              </NativeSelect>
            </div>

            {!rows?.length && <p className="py-6 text-center text-sm text-[--color-ink-faint]">No matching activity.</p>}
            <div>{rows?.map((row) => <AuditRow key={row.id} row={row} />)}</div>

            {audit && audit.items.length >= visible && (
              <div className="pt-1 text-center">
                <Button variant="ghost" size="sm" onClick={() => setVisible((v) => v + 40)}>
                  Load more
                </Button>
              </div>
            )}
          </CardContent>
        </Card>
      </PageBody>
    </>
  )
}
