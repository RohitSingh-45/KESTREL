import type { ReactNode } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { Radar } from 'lucide-react'
import { useCase, useGenerateReport, useTraceJobs, useUpdateCase } from '@/api/hooks'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { NativeSelect } from '@/components/ui/input'
import { CopyButton, EmptyState, ErrorBlock, LoadingBlock, Mono, StatTile } from '@/components/ui/misc'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table'
import { formatInr, formatDateTime, shortAddress, timeAgo, titleCase } from '@/lib/format'
import { categoryLabel, CHAIN_LABEL, priorityVariant, riskVariant, statusVariant } from '@/lib/labels'
import type { CaseStatus, ReportKind } from '@/types'

export function CaseDetailPage() {
  const { caseId } = useParams()
  const navigate = useNavigate()
  const { data: caseRow, isLoading, error } = useCase(caseId)
  const { data: jobs } = useTraceJobs(caseId)
  const update = useUpdateCase()
  const generate = useGenerateReport()

  if (isLoading) return <LoadingBlock label="Loading case" />
  if (error) return <ErrorBlock error={error} className="m-6" />
  if (!caseRow) return <EmptyState title="Case not found" />

  return (
    <>
      <PageHeader
        title={caseRow.case_number}
        description={caseRow.title}
        actions={
          <div className="flex items-center gap-2">
            <NativeSelect
              value={caseRow.status}
              onChange={(e) => update.mutate({ id: caseRow.id, status: e.target.value })}
              className="w-40"
            >
              {['open', 'tracing', 'attributed', 'request_sent', 'frozen', 'closed'].map((s) => (
                <option key={s} value={s}>
                  {titleCase(s)}
                </option>
              ))}
            </NativeSelect>
            <Button
              variant="outline"
              onClick={() =>
                generate.mutate({ caseId: caseRow.id, kind: 'investigation_packet' as ReportKind })
              }
              loading={generate.isPending}
            >
              Generate packet
            </Button>
          </div>
        }
      />
      <PageBody className="space-y-5">
        <div className="grid gap-3 md:grid-cols-4">
          <StatTile label="Loss" value={formatInr(caseRow.loss_amount_inr)} />
          <StatTile
            label="Status"
            value={<Badge variant={statusVariant(caseRow.status as CaseStatus)}>{titleCase(caseRow.status)}</Badge>}
          />
          <StatTile
            label="Priority"
            value={<Badge variant={priorityVariant(caseRow.priority)}>{titleCase(caseRow.priority)}</Badge>}
          />
          <StatTile label="Open alerts" value={caseRow.open_alert_count} sub={`${caseRow.alert_count} total`} />
        </div>

        <div className="grid gap-4 lg:grid-cols-3">
          <Card className="lg:col-span-2">
            <CardHeader>
              <CardTitle>Complaint</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-2 text-sm md:grid-cols-2">
              <Field label="Category" value={categoryLabel(caseRow.fraud_category)} />
              <Field label="NCRP ack" value={caseRow.ncrp_ack_no ?? '—'} />
              <Field label="Victim" value={caseRow.victim_name ?? '—'} />
              <Field label="Contact" value={caseRow.victim_contact ?? '—'} />
              <Field label="Police station" value={caseRow.police_station ?? '—'} />
              <Field label="State" value={caseRow.state ?? '—'} />
              <Field label="Incident" value={formatDateTime(caseRow.incident_date)} />
              <Field label="Intake" value={caseRow.intake_source} />
              {caseRow.description && (
                <p className="col-span-full text-sm text-[--color-ink-muted]">{caseRow.description}</p>
              )}
            </CardContent>
          </Card>
          <Card>
            <CardHeader>
              <CardTitle>Assignment</CardTitle>
            </CardHeader>
            <CardContent className="space-y-2 text-sm">
              <Field label="Officer" value={caseRow.assigned_to_name ?? 'Unassigned'} />
              <Field label="Opened" value={timeAgo(caseRow.created_at)} />
              <Field label="Updated" value={timeAgo(caseRow.updated_at)} />
            </CardContent>
          </Card>
        </div>

        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <CardTitle>Reported addresses</CardTitle>
          </CardHeader>
          <CardContent>
            {!caseRow.addresses.length && <EmptyState title="No addresses on this case" />}
            {caseRow.addresses.length > 0 && (
              <Table>
                <THead>
                  <TR>
                    <TH>Chain</TH>
                    <TH>Address</TH>
                    <TH>Last trace</TH>
                    <TH>VASP</TH>
                    <TH>Risk</TH>
                    <TH />
                  </TR>
                </THead>
                <TBody>
                  {caseRow.addresses.map((row) => (
                    <TR key={row.id}>
                      <TD>{CHAIN_LABEL[row.chain]}</TD>
                      <TD>
                        <Mono className="inline-flex items-center gap-1">
                          {shortAddress(row.address)}
                          <CopyButton value={row.address} />
                        </Mono>
                      </TD>
                      <TD>
                        {row.last_job_id ? (
                          <Link className="text-blue-300 hover:underline" to={`/trace/${row.last_job_id}`}>
                            {row.last_job_status}
                          </Link>
                        ) : (
                          '—'
                        )}
                      </TD>
                      <TD>{row.attributed_vasp ?? '—'}</TD>
                      <TD>
                        {row.risk_level ? (
                          <Badge variant={riskVariant(row.risk_level)}>{row.risk_level}</Badge>
                        ) : (
                          '—'
                        )}
                      </TD>
                      <TD>
                        <Button
                          size="sm"
                          onClick={() =>
                            navigate(`/trace?address=${encodeURIComponent(row.address)}&chain=${row.chain}&case=${caseRow.id}`)
                          }
                        >
                          <Radar className="h-3.5 w-3.5" />
                          Trace
                        </Button>
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Traces</CardTitle>
          </CardHeader>
          <CardContent>
            {!jobs?.items.length && <EmptyState title="No traces yet" />}
            {!!jobs?.items.length && (
              <Table>
                <THead>
                  <TR>
                    <TH>Seed</TH>
                    <TH>Status</TH>
                    <TH>Finding</TH>
                    <TH>Duration</TH>
                  </TR>
                </THead>
                <TBody>
                  {jobs.items.map((job) => (
                    <TR key={job.id} clickable onClick={() => navigate(`/trace/${job.id}`)}>
                      <TD className="mono text-xs">{shortAddress(job.seed_address)}</TD>
                      <TD>{job.status}</TD>
                      <TD className="max-w-md truncate text-xs">
                        {job.summary?.attribution?.statement ?? '—'}
                      </TD>
                      <TD>{job.duration_ms ? `${job.duration_ms} ms` : '—'}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </PageBody>
    </>
  )
}

function Field({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <p className="text-[11px] uppercase tracking-wide text-[--color-ink-faint]">{label}</p>
      <p className="mt-0.5">{value}</p>
    </div>
  )
}
