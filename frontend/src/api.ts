import type { AgentSummary, DashboardState } from './types'

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `Request failed (${res.status})`)
  }
  return res.json() as Promise<T>
}

export async function fetchAgents(): Promise<AgentSummary[]> {
  const data = await getJson<{ agents: AgentSummary[] }>('/api/dashboard/agents')
  return data.agents
}

export async function fetchState(agentId: string): Promise<DashboardState> {
  const data = await getJson<{ state: DashboardState }>(
    `/api/dashboard/state?agentId=${encodeURIComponent(agentId)}`,
  )
  return data.state
}

export async function initAgent(): Promise<string> {
  const res = await fetch('/api/agent/init', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      persona: { name: 'Kestrel', domain: 'AI Security' },
    }),
  })
  if (!res.ok) throw new Error('Failed to initialize agent')
  const data = (await res.json()) as { agentId: string }
  return data.agentId
}

export function relativeTime(iso?: string | null): string {
  if (!iso) return '—'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return '—'
  const delta = Math.max(0, Date.now() - then)
  const mins = Math.floor(delta / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 48) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}
