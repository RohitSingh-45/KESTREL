import * as React from 'react'
import { useCases, useDownloadReport, useGenerateReport, useReports } from '@/api/hooks'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { NativeSelect } from '@/components/ui/input'
import { EmptyState, ErrorBlock, LoadingBlock, Mono } from '@/components/ui/misc'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table'
import { formatDateTime, shortAddress, titleCase } from '@/lib/format'
import type { ReportKind } from '@/types'

const KINDS: ReportKind[] = ['investigation_packet', 'bnss_94_request', 'sahyog_package']

export function ReportsPage() {
  const { data: cases } = useCases({ limit: 100 })
  const [caseId, setCaseId] = React.useState('')
  const [kind, setKind] = React.useState<ReportKind>('investigation_packet')
  const { data, isLoading, error } = useReports(caseId || undefined)
  const generate = useGenerateReport()
  const download = useDownloadReport()

  return (
    <>
      <PageHeader
        title="Reports"
        description="Investigation packets, Section 94 BNSS requests and SAHYOG packages. Every file is SHA-256 hashed."
      />
      <PageBody className="space-y-4">
        <div className="flex flex-wrap items-end gap-2">
          <NativeSelect value={caseId} onChange={(e) => setCaseId(e.target.value)} className="w-72">
            <option value="">All cases</option>
            {cases?.items.map((row) => (
              <option key={row.id} value={row.id}>
                {row.case_number} — {row.title}
              </option>
            ))}
          </NativeSelect>
          <NativeSelect value={kind} onChange={(e) => setKind(e.target.value as ReportKind)} className="w-56">
            {KINDS.map((k) => (
              <option key={k} value={k}>
                {titleCase(k)}
              </option>
            ))}
          </NativeSelect>
          <Button
            disabled={!caseId}
            loading={generate.isPending}
            onClick={() => generate.mutate({ caseId, kind })}
          >
            Generate
          </Button>
        </div>
        {generate.error && <ErrorBlock error={generate.error} />}
        {isLoading && <LoadingBlock label="Loading reports" />}
        {error && <ErrorBlock error={error} />}
        {data && data.items.length === 0 && (
          <EmptyState title="No reports yet" description="Pick a case that has been traced and generate a packet." />
        )}
        {!!data?.items.length && (
          <div className="panel overflow-hidden">
            <Table>
              <THead>
                <TR>
                  <TH>Kind</TH>
                  <TH>File</TH>
                  <TH>SHA-256</TH>
                  <TH>Size</TH>
                  <TH>By</TH>
                  <TH>When</TH>
                  <TH />
                </TR>
              </THead>
              <TBody>
                {data.items.map((row) => (
                  <TR key={row.id}>
                    <TD>
                      <Badge variant="brand">{titleCase(row.kind)}</Badge>
                    </TD>
                    <TD className="text-xs">{row.filename}</TD>
                    <TD>
                      <Mono>{shortAddress(row.sha256, 10, 8)}</Mono>
                    </TD>
                    <TD>{Math.round(row.size_bytes / 1024)} KB</TD>
                    <TD className="text-xs">{row.generated_by_name}</TD>
                    <TD className="text-xs">{formatDateTime(row.created_at)}</TD>
                    <TD>
                      <Button
                        size="sm"
                        variant="outline"
                        loading={download.isPending}
                        onClick={() => download.mutate({ id: row.id, filename: row.filename })}
                      >
                        Download
                      </Button>
                    </TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>
        )}
      </PageBody>
    </>
  )
}
