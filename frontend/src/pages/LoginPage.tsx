import * as React from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import { Activity, ShieldCheck } from 'lucide-react'
import { useAuth } from '@/auth/AuthContext'
import { Button } from '@/components/ui/button'
import { Field, Input } from '@/components/ui/input'
import { ErrorBlock, LoadingBlock } from '@/components/ui/misc'

export function LoginPage() {
  const { user, loading, login } = useAuth()
  const navigate = useNavigate()
  const [email, setEmail] = React.useState('admin@tracex.gov.in')
  const [password, setPassword] = React.useState('ChangeMe#123')
  const [error, setError] = React.useState<unknown>(null)
  const [submitting, setSubmitting] = React.useState(false)

  if (loading) return <LoadingBlock label="Restoring session" />
  if (user) return <Navigate to="/cases" replace />

  async function onSubmit(event: React.FormEvent) {
    event.preventDefault()
    setError(null)
    setSubmitting(true)
    try {
      await login(email, password)
      navigate('/cases')
    } catch (err) {
      setError(err)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="grid-bg flex h-full items-center justify-center px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex flex-col items-center gap-2 text-center">
          <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-sky-500/15 ring-1 ring-sky-500/30">
            <Activity className="h-5 w-5 text-sky-400" />
          </div>
          <h1 className="text-xl font-semibold tracking-tight">TraceX</h1>
          <p className="text-xs text-[--color-ink-muted]">
            Real-Time Crypto Fraud Attribution System
          </p>
        </div>

        <form onSubmit={onSubmit} className="panel space-y-4 p-5">
          <Field label="Official email">
            <Input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              autoComplete="username"
              required
            />
          </Field>
          <Field label="Password">
            <Input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
            />
          </Field>

          {error != null && <ErrorBlock error={error} />}

          <Button type="submit" className="w-full" loading={submitting}>
            Sign in
          </Button>

          <p className="flex items-center justify-center gap-1.5 text-[11px] text-[--color-ink-faint]">
            <ShieldCheck className="h-3.5 w-3.5" />
            All activity is recorded in the audit trail
          </p>
        </form>
      </div>
    </div>
  )
}
