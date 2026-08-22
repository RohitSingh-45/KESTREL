import * as React from 'react'
import { useVasps } from '@/api/hooks'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { EmptyState, LoadingBlock } from '@/components/ui/misc'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table'

export function VaspDirectoryPage() {
  const [q, setQ] = React.useState('')
  const { data, isLoading } = useVasps(q || undefined)

  return (
    <>
      <PageHeader
        title="VASP directory"
        description="Who to serve the request on — compliance contact, FIU-IND status and SLA."
      />
      <PageBody className="space-y-4">
        <Input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="Search name, slug or jurisdiction"
          className="max-w-sm"
        />
        {isLoading && <LoadingBlock label="Loading directory" />}
        {!data?.items.length && !isLoading && <EmptyState title="No VASPs match" />}
        {!!data?.items.length && (
          <div className="panel overflow-hidden">
            <Table>
              <THead>
                <TR>
                  <TH>Entity</TH>
                  <TH>Jurisdiction</TH>
                  <TH>FIU-IND</TH>
                  <TH>Compliance</TH>
                  <TH>SLA</TH>
                  <TH>Freeze</TH>
                  <TH>Hits</TH>
                </TR>
              </THead>
              <TBody>
                {data.items.map((vasp) => (
                  <TR key={vasp.slug}>
                    <TD>
                      <p className="font-medium">{vasp.name}</p>
                      <p className="text-xs text-[--color-ink-muted]">{vasp.legal_name}</p>
                    </TD>
                    <TD>
                      {vasp.jurisdiction}
                      {vasp.is_indian_entity && (
                        <Badge variant="brand" className="ml-2">
                          IN
                        </Badge>
                      )}
                    </TD>
                    <TD>
                      {vasp.fiu_ind_registered ? (
                        <Badge variant="success">{vasp.fiu_ind_reg_no ?? 'Registered'}</Badge>
                      ) : (
                        <Badge>No</Badge>
                      )}
                    </TD>
                    <TD className="text-xs">
                      <p>{vasp.compliance_email ?? '—'}</p>
                      <p className="text-[--color-ink-faint]">{vasp.grievance_officer}</p>
                    </TD>
                    <TD>{vasp.response_sla_hours}h</TD>
                    <TD>{vasp.accepts_emergency_freeze ? 'Yes' : '—'}</TD>
                    <TD>{vasp.attributed_addresses ?? 0}</TD>
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
