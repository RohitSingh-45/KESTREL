import * as React from 'react'
import { Navigate, useNavigate } from 'react-router-dom'
import {
  Building2,
  FileText,
  Landmark,
  Moon,
  Radar,
  ShieldCheck,
  Sun,
  UserPlus,
} from 'lucide-react'
import { useAuth } from '@/auth/AuthContext'
import { useTheme } from '@/theme/ThemeContext'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent } from '@/components/ui/dialog'
import { Field, Input } from '@/components/ui/input'
import { ErrorBlock, LoadingBlock } from '@/components/ui/misc'

const HIGHLIGHTS = [
  { icon: Radar, text: 'Forward-traces funds across Bitcoin, EVM chains and TRON' },
  { icon: Building2, text: 'Attributes the nearest receiving exchange in four tiers' },
  { icon: ShieldCheck, text: 'Scores laundering risk against 10 known typologies' },
  { icon: FileText, text: 'Emits a hashed, court-ready freeze request in minutes' },
]

function TraceGraphic() {
  const nodes = [
    { x: 20, y: 70 },
    { x: 90, y: 30 },
    { x: 90, y: 110 },
    { x: 165, y: 55 },
    { x: 165, y: 130 },
    { x: 245, y: 90 },
  ]
  const edges: [number, number][] = [
    [0, 1],
    [0, 2],
    [1, 3],
    [2, 3],
    [2, 4],
    [3, 5],
    [4, 5],
  ]
  return (
    <svg viewBox="0 0 270 160" className="h-auto w-full max-w-sm">
      {edges.map(([a, b], i) => (
        <line
          key={i}
          x1={nodes[a].x}
          y1={nodes[a].y}
          x2={nodes[b].x}
          y2={nodes[b].y}
          stroke="currentColor"
          strokeOpacity={0.25}
          strokeWidth={1.5}
          strokeDasharray="4 4"
        />
      ))}
      {nodes.map((n, i) => (
        <circle
          key={i}
          cx={n.x}
          cy={n.y}
          r={i === 5 ? 7 : 5}
          fill={i === 5 ? 'var(--color-accent-gold)' : 'currentColor'}
          className={i === 0 ? 'animate-pulse-ring' : undefined}
          style={{ animationDelay: `${i * 0.2}s` }}
        />
      ))}
    </svg>
  )
}

export function LoginPage() {
  const { user, loading, login } = useAuth()
  const { theme, toggleTheme } = useTheme()
  const navigate = useNavigate()
  const [email, setEmail] = React.useState('admin@tracex.gov.in')
  const [password, setPassword] = React.useState('ChangeMe#123')
  const [error, setError] = React.useState<unknown>(null)
  const [submitting, setSubmitting] = React.useState(false)
  const [signUpOpen, setSignUpOpen] = React.useState(false)

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
    <div className="grid-bg flex h-full flex-col">
      <div className="flex items-center justify-between border-b border-amber-500/20 bg-amber-500/[0.06] px-4 py-1.5 text-[10px] font-medium uppercase tracking-[0.2em] text-amber-300/80">
        <span className="flex-1 text-center">
          Restricted &middot; For Official Use Only &middot; All access is logged
        </span>
        <button
          type="button"
          onClick={toggleTheme}
          aria-label="Toggle theme"
          className="ml-2 rounded p-1 text-[--color-ink-faint] hover:text-[--color-ink]"
        >
          {theme === 'dark' ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
        </button>
      </div>
      <div className="flex flex-1 items-stretch justify-center overflow-y-auto">
        <div className="hidden w-[46%] max-w-md flex-col justify-between overflow-hidden border-r border-[--color-line] bg-gradient-to-br from-blue-950 via-[--color-surface-1] to-[--color-surface-0] p-10 md:flex">
          <div className="flex items-center gap-2.5">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-blue-500/15 ring-1 ring-blue-500/30">
              <Landmark className="h-4.5 w-4.5 text-blue-400" />
            </div>
            <div className="leading-tight">
              <p className="text-sm font-semibold tracking-tight">TraceX</p>
              <p className="text-[10px] uppercase tracking-wider text-[--color-ink-faint]">
                I4C &middot; Ministry of Home Affairs
              </p>
            </div>
          </div>

          <div className="animate-slide-up space-y-6">
            <div className="text-blue-400/70">
              <TraceGraphic />
            </div>
            <div>
              <h2 className="text-2xl font-semibold leading-tight tracking-tight text-[--color-ink]">
                Follow the money.
                <br />
                Freeze it in time.
              </h2>
              <p className="mt-2 max-w-xs text-sm text-[--color-ink-muted]">
                A victim-reported wallet, traced to a named exchange, with a
                risk score and a legal request ready to send &mdash; before the
                trail goes cold.
              </p>
            </div>
            <ul className="space-y-3">
              {HIGHLIGHTS.map(({ icon: Icon, text }) => (
                <li key={text} className="flex items-start gap-2.5 text-xs text-[--color-ink-muted]">
                  <Icon className="mt-0.5 h-3.5 w-3.5 shrink-0 text-blue-400" />
                  <span>{text}</span>
                </li>
              ))}
            </ul>
          </div>

          <p className="text-[10px] text-[--color-ink-faint]">
            Prototype build &middot; hybrid live / synthetic data mode
          </p>
        </div>

        <div className="grid-bg flex flex-1 items-center justify-center px-4">
          <div className="w-full max-w-sm">
            <div className="mb-6 flex flex-col items-center gap-2 text-center md:hidden">
              <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-blue-500/15 ring-1 ring-blue-500/30">
                <Landmark className="h-5 w-5 text-blue-400" />
              </div>
              <h1 className="text-xl font-semibold tracking-tight">TraceX</h1>
              <p className="text-xs text-[--color-ink-muted]">
                Real-Time Crypto Fraud Attribution System
              </p>
            </div>

            <div className="mb-5 hidden text-center md:block">
              <h1 className="text-lg font-semibold tracking-tight">Sign in to your desk</h1>
              <p className="mt-1 text-xs text-[--color-ink-muted]">
                Enter your official credentials to continue
              </p>
            </div>

            <form onSubmit={onSubmit} className="panel animate-slide-up space-y-4 p-5 shadow-lg shadow-black/10">
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

            <button
              type="button"
              onClick={() => setSignUpOpen(true)}
              className="mt-4 flex w-full items-center justify-center gap-1.5 text-xs text-[--color-ink-muted] hover:text-[--color-ink]"
            >
              <UserPlus className="h-3.5 w-3.5" />
              New officer? Get access
            </button>
          </div>
        </div>
      </div>
      <SignUpDialog open={signUpOpen} onOpenChange={setSignUpOpen} />
    </div>
  )
}

function SignUpDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (v: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        title="Getting access to TraceX"
        description="Accounts on a case-management system like this are provisioned by your department, not self-registered — so victim and case data is only ever visible to vetted officers."
      >
        <div className="space-y-3 text-sm text-[--color-ink-muted]">
          <p>To get an account:</p>
          <ol className="list-decimal space-y-1.5 pl-4">
            <li>Ask your unit&rsquo;s TraceX administrator to add you from the Admin panel.</li>
            <li>They&rsquo;ll create your login with your official email, role and badge number.</li>
            <li>You&rsquo;ll receive a temporary password to sign in with above, and can change it after
              your first login.</li>
          </ol>
          <p className="flex items-center gap-1.5 pt-1 text-[11px] text-[--color-ink-faint]">
            <ShieldCheck className="h-3.5 w-3.5 shrink-0" />
            This keeps every account tied to a vetted, named officer &mdash; no open sign-up.
          </p>
        </div>
      </DialogContent>
    </Dialog>
  )
}
