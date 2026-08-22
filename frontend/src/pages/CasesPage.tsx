import * as React from 'react'
import { useNavigate } from 'react-router-dom'
import { Plus, Upload } from 'lucide-react'
import { useCases, useCreateCase, useImportNcrpCsv } from '@/api/hooks'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Field, Input, NativeSelect, Textarea } from '@/components/ui/input'
import { EmptyState, ErrorBlock, LoadingBlock } from '@/components/ui/misc'
import { Table, TBody, TD, TH, THead, TR } from '@/components/ui/table'
import { formatInr, timeAgo, titleCase } from '@/lib/format'
import { categoryLabel, priorityVariant, riskVariant, statusVariant } from '@/lib/labels'
import type { CasePriority, FraudCategory } from '@/types'

const CATEGORIES: FraudCategory[] = [
  'investment_scam',
  'task_based_fraud',
  'sextortion',
  'ransomware',
  'phishing',
  'darknet',
  'organised_cyber_crime',
  'other',
]

export function CasesPage() {
  const navigate = useNavigate()
  const [q, setQ] = React.useState('')
  const [status, setStatus] = React.useState('')
  const [open, setOpen] = React.useState(false)
  const { data, isLoading, error } = useCases({ q, status: status || undefined, limit: 50 })

  return (
    <>
      <PageHeader
        title="Cases"
        description="Complaints under investigation. Open one to trace, attribute and serve a request."
        actions={
          <>
            <Button variant="outline" onClick={() => setOpen(true)}>
              <Plus className="h-4 w-4" />
              New case
            </Button>
          </>
        }
      />
      <PageBody className="space-y-4">
        <div className="flex flex-wrap gap-2">
          <Input
            placeholder="Search case number, title or address"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="max-w-sm"
          />
          <NativeSelect value={status} onChange={(e) => setStatus(e.target.value)} className="w-44">
            <option value="">All statuses</option>
            {['open', 'tracing', 'attributed', 'request_sent', 'frozen', 'closed'].map((s) => (
              <option key={s} value={s}>
                {titleCase(s)}
              </option>
            ))}
          </NativeSelect>
        </div>

        {isLoading && <LoadingBlock label="Loading cases" />}
        {error && <ErrorBlock error={error} />}
        {data && data.items.length === 0 && (
          <EmptyState title="No cases yet" description="Create a case or import an NCRP CSV." />
        )}
        {data && data.items.length > 0 && (
          <div className="panel overflow-hidden">
            <Table>
              <THead>
                <TR>
                  <TH>Case</TH>
                  <TH>Category</TH>
                  <TH>Status</TH>
                  <TH>Priority</TH>
                  <TH>Loss</TH>
                  <TH>VASP</TH>
                  <TH>Risk</TH>
                  <TH>Opened</TH>
                </TR>
              </THead>
              <TBody>
                {data.items.map((row) => (
                  <TR key={row.id} clickable onClick={() => navigate(`/cases/${row.id}`)}>
                    <TD>
                      <p className="font-medium">{row.case_number}</p>
                      <p className="max-w-xs truncate text-xs text-[--color-ink-muted]">{row.title}</p>
                    </TD>
                    <TD className="text-xs">{categoryLabel(row.fraud_category)}</TD>
                    <TD>
                      <Badge variant={statusVariant(row.status)}>{titleCase(row.status)}</Badge>
                    </TD>
                    <TD>
                      <Badge variant={priorityVariant(row.priority)}>{titleCase(row.priority)}</Badge>
                    </TD>
                    <TD>{formatInr(row.loss_amount_inr)}</TD>
                    <TD>{row.top_vasp ?? '—'}</TD>
                    <TD>
                      {row.highest_risk ? (
                        <Badge variant={riskVariant(row.highest_risk)}>{row.highest_risk}</Badge>
                      ) : (
                        '—'
                      )}
                    </TD>
                    <TD className="text-xs text-[--color-ink-muted]">{timeAgo(row.created_at)}</TD>
                  </TR>
                ))}
              </TBody>
            </Table>
          </div>
        )}
      </PageBody>
      <NewCaseDialog open={open} onOpenChange={setOpen} />
    </>
  )
}

function NewCaseDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (v: boolean) => void }) {
  const navigate = useNavigate()
  const create = useCreateCase()
  const importer = useImportNcrpCsv()
  const [title, setTitle] = React.useState('')
  const [addresses, setAddresses] = React.useState('')
  const [category, setCategory] = React.useState<FraudCategory>('investment_scam')
  const [priority, setPriority] = React.useState<CasePriority>('normal')
  const [loss, setLoss] = React.useState('')
  const [victim, setVictim] = React.useState('')
  const [state, setState] = React.useState('')
  const [description, setDescription] = React.useState('')

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    const created = await create.mutateAsync({
      title,
      fraud_category: category,
      priority,
      loss_amount_inr: loss ? Number(loss) : null,
      victim_name: victim || null,
      state: state || null,
      description: description || null,
      addresses: addresses
        .split(/[\s,;]+/)
        .map((a) => a.trim())
        .filter(Boolean)
        .map((address) => ({ address })),
    })
    onOpenChange(false)
    navigate(`/cases/${created.id}`)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Open a case" description="Paste one or more wallets. Chain is detected from the address format.">
        <form onSubmit={onSubmit} className="space-y-3">
          <Field label="Title">
            <Input value={title} onChange={(e) => setTitle(e.target.value)} required />
          </Field>
          <Field label="Reported wallets" hint="One per line, or comma-separated. Chain is auto-detected.">
            <Textarea
              value={addresses}
              onChange={(e) => setAddresses(e.target.value)}
              placeholder="TGMkKG6YRdJ6Cgnt8czbtrMXAQWQcV7Whq"
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Category">
              <NativeSelect value={category} onChange={(e) => setCategory(e.target.value as FraudCategory)}>
                {CATEGORIES.map((c) => (
                  <option key={c} value={c}>
                    {categoryLabel(c)}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Priority">
              <NativeSelect value={priority} onChange={(e) => setPriority(e.target.value as CasePriority)}>
                {['low', 'normal', 'high', 'urgent'].map((p) => (
                  <option key={p} value={p}>
                    {titleCase(p)}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Field label="Loss (INR)">
              <Input type="number" min={0} value={loss} onChange={(e) => setLoss(e.target.value)} />
            </Field>
            <Field label="Victim name">
              <Input value={victim} onChange={(e) => setVictim(e.target.value)} />
            </Field>
          </div>
          <Field label="State / UT">
            <Input value={state} onChange={(e) => setState(e.target.value)} />
          </Field>
          <Field label="Narrative">
            <Textarea value={description} onChange={(e) => setDescription(e.target.value)} />
          </Field>
          {(create.error || importer.error) && <ErrorBlock error={create.error ?? importer.error} />}
          <div className="flex items-center justify-between gap-2 pt-1">
            <label className="inline-flex cursor-pointer items-center gap-2 text-xs text-[--color-ink-muted]">
              <Upload className="h-3.5 w-3.5" />
              Import NCRP CSV
              <input
                type="file"
                accept=".csv,text/csv"
                className="hidden"
                onChange={async (e) => {
                  const file = e.target.files?.[0]
                  if (!file) return
                  await importer.mutateAsync(file)
                  onOpenChange(false)
                }}
              />
            </label>
            <Button type="submit" loading={create.isPending}>
              Create case
            </Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  )
}
