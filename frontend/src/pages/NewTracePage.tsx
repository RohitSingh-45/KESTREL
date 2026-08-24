import * as React from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useCases, useResolveAddress, useStartTrace } from '@/api/hooks'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Field, Input, NativeSelect } from '@/components/ui/input'
import { ErrorBlock } from '@/components/ui/misc'
import { CHAIN_LABEL } from '@/lib/labels'
import type { Chain } from '@/types'

const CHAINS: Chain[] = ['tron', 'ethereum', 'bitcoin', 'bsc', 'polygon', 'arbitrum', 'base']

export function NewTracePage() {
  const navigate = useNavigate()
  const [params] = useSearchParams()
  const [address, setAddress] = React.useState(params.get('address') ?? '')
  const [chain, setChain] = React.useState<string>(params.get('chain') ?? '')
  const [caseId, setCaseId] = React.useState(params.get('case') ?? '')
  const [maxHops, setMaxHops] = React.useState(5)
  const start = useStartTrace()
  const { data: cases } = useCases({ limit: 50 })
  const { data: resolved } = useResolveAddress(address.trim(), true)

  React.useEffect(() => {
    // Syncing UI state to a query result that arrives asynchronously - React
    // Query v5 has no onSuccess callback on useQuery, so this is the
    // supported way to auto-fill the chain once detection resolves, without
    // overriding a chain the user already picked themselves.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (resolved && !chain) setChain(resolved.chain)
  }, [resolved, chain])

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    const accepted = await start.mutateAsync({
      address: address.trim(),
      chain: (chain || undefined) as Chain | undefined,
      case_id: caseId || undefined,
      max_hops: maxHops,
    })
    navigate(`/trace/${accepted.job_id}`)
  }

  return (
    <>
      <PageHeader
        title="New trace"
        description="Forward BFS from a victim-reported address. Stops at the first high-confidence VASP deposit."
      />
      <PageBody>
        <Card className="max-w-2xl">
          <CardContent className="pt-5">
            <form onSubmit={onSubmit} className="space-y-4">
              <Field label="Wallet address" hint="TRON (T…), EVM (0x…) or Bitcoin (1/3/bc1).">
                <Input
                  value={address}
                  onChange={(e) => setAddress(e.target.value)}
                  placeholder="TGMkKG6YRdJ6Cgnt8czbtrMXAQWQcV7Whq"
                  required
                  className="mono"
                />
              </Field>
              {resolved && (
                <div className="flex flex-wrap items-center gap-2 text-xs text-[--color-ink-muted]">
                  Detected as <Badge variant="brand">{CHAIN_LABEL[resolved.chain]}</Badge>
                  {resolved.ambiguous && <span>Ambiguous EVM address — probed for activity.</span>}
                  {Object.entries(resolved.activity).map(([c, n]) => (
                    <Badge key={c}>
                      {c}: {n} txs
                    </Badge>
                  ))}
                </div>
              )}
              <div className="grid gap-3 md:grid-cols-3">
                <Field label="Chain override">
                  <NativeSelect value={chain} onChange={(e) => setChain(e.target.value)}>
                    <option value="">Auto-detect</option>
                    {CHAINS.map((c) => (
                      <option key={c} value={c}>
                        {CHAIN_LABEL[c]}
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
                <Field label="Attach to case">
                  <NativeSelect value={caseId} onChange={(e) => setCaseId(e.target.value)}>
                    <option value="">Standalone</option>
                    {cases?.items.map((row) => (
                      <option key={row.id} value={row.id}>
                        {row.case_number}
                      </option>
                    ))}
                  </NativeSelect>
                </Field>
                <Field label="Max hops">
                  <Input
                    type="number"
                    min={1}
                    max={8}
                    value={maxHops}
                    onChange={(e) => setMaxHops(Number(e.target.value))}
                  />
                </Field>
              </div>
              {start.error && <ErrorBlock error={start.error} />}
              <Button type="submit" loading={start.isPending}>
                Start trace
              </Button>
            </form>
          </CardContent>
        </Card>
      </PageBody>
    </>
  )
}
