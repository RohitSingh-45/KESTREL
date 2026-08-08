import type { AgentSummary, DashboardState } from './types'

declare global {
  interface Window {
    __KESTREL_API_BASE__?: string
  }
}

/**
 * API base resolution:
 * 1) window.__KESTREL_API_BASE__ (runtime inject)
 * 2) VITE_API_BASE_URL (build-time, for split Vercel→API deploys)
 * 3) "" same-origin (FastAPI serves UI, or Vite dev/preview proxy)
 */
function resolveApiBase(): string {
  if (typeof window !== 'undefined' && window.__KESTREL_API_BASE__) {
    return String(window.__KESTREL_API_BASE__).trim().replace(/\/$/, '')
  }
  return String(import.meta.env.VITE_API_BASE_URL || '')
    .trim()
    .replace(/\/$/, '')
}

const API_BASE = resolveApiBase()

function apiUrl(path: string): string {
  const normalized = path.startsWith('/') ? path : `/${path}`
  return `${API_BASE}${normalized}`
}

function describeHttpError(status: number, url: string, body: string): string {
  if (status === 502 || status === 503 || status === 504) {
    return (
      `Backend unavailable (${status}) at ${url}. ` +
      `Start the API (uvicorn on port 8000) or set VITE_API_BASE_URL to your deployed backend.`
    )
  }
  if (body.trim().startsWith('<')) {
    return (
      `Request failed (${status}) at ${url}. ` +
      `Received HTML instead of JSON — the frontend is probably not reaching the FastAPI backend.`
    )
  }
  return body || `Request failed (${status}) at ${url}`
}

async function getJson<T>(path: string): Promise<T> {
  const url = apiUrl(path)
  let res: Response
  try {
    res = await fetch(url)
  } catch {
    throw new Error(
      `Network error contacting ${url}. Is the Kestrel API running?`,
    )
  }
  if (!res.ok) {
    const text = await res.text()
    throw new Error(describeHttpError(res.status, url, text))
  }
  try {
    return (await res.json()) as T
  } catch {
    throw new Error(
      `Invalid JSON from ${url}. Frontend is not talking to the FastAPI backend.`,
    )
  }
}

export type HealthInfo = {
  status: string
  service?: string
  db?: string
  scheduler?: string
  llm?: string
}

export async function fetchHealth(): Promise<HealthInfo> {
  return getJson<HealthInfo>('/health')
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
  const url = apiUrl('/api/agent/init')
  let res: Response
  try {
    res = await fetch(url, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        persona: { name: 'Kestrel', domain: 'AI Security' },
      }),
    })
  } catch {
    throw new Error(`Network error contacting ${url}. Is the Kestrel API running?`)
  }
  if (!res.ok) {
    const text = await res.text()
    throw new Error(describeHttpError(res.status, url, text))
  }
  const data = (await res.json()) as { agentId: string }
  if (!data.agentId) {
    throw new Error('Init succeeded but no agentId was returned')
  }
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

export { API_BASE, apiUrl }
