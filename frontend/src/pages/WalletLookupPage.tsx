import * as React from 'react'
import { useNavigate } from 'react-router-dom'
import { useResolveAddress } from '@/api/hooks'
import { PageBody, PageHeader } from '@/components/layout/AppShell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import { Field, Input, NativeSelect } from '@/components/ui/input'
import { CHAIN_LABEL } from '@/lib/labels'

export function WalletLookupPage() {
  const navigate = useNavigate()
  const [address, setAddress] = React.useState('')
  const [chain, setChain] = React.useState('')
  const { data: resolved } = useResolveAddress(address.trim())

  return (
    <>
      <PageHeader
        title="Wallet lookup"
        description="Risk, labels, typologies and counterparties for a single address."
      />
      <PageBody>
        <Card className="max-w-xl">
          <CardContent className="space-y-3 pt-5">
            <Field label="Address">
              <Input
                className="mono"
                value={address}
                onChange={(e) => setAddress(e.target.value)}
                placeholder="0x… / T… / bc1…"
              />
            </Field>
            {resolved && (
              <p className="text-xs text-[--color-ink-muted]">
                Format {resolved.format} · <Badge variant="brand">{CHAIN_LABEL[resolved.chain]}</Badge>
              </p>
            )}
            <Field label="Chain override">
              <NativeSelect value={chain} onChange={(e) => setChain(e.target.value)}>
                <option value="">Use detected chain</option>
                {Object.entries(CHAIN_LABEL).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </NativeSelect>
            </Field>
            <Button
              disabled={address.length < 8}
              onClick={() =>
                navigate(`/wallets/${chain || resolved?.chain || 'ethereum'}/${address.trim()}`)
              }
            >
              Open profile
            </Button>
          </CardContent>
        </Card>
      </PageBody>
    </>
  )
}
