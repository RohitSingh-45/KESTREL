const API_BASE = import.meta.env.VITE_API_BASE ?? '/api/v1'
const TOKEN_KEY = 'tracex.token'

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code: string = 'error',
    readonly details?: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  set: (token: string) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => localStorage.removeItem(TOKEN_KEY),
}

type RequestOptions = Omit<RequestInit, 'body'> & {
  body?: unknown
  query?: Record<string, string | number | boolean | undefined | null>
}

function buildUrl(path: string, query?: RequestOptions['query']): string {
  const url = `${API_BASE}${path}`
  if (!query) return url
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(query)) {
    if (value !== undefined && value !== null && value !== '') params.set(key, String(value))
  }
  const qs = params.toString()
  return qs ? `${url}?${qs}` : url
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, query, headers, ...rest } = options
  const token = tokenStore.get()
  const isFormData = body instanceof FormData

  const response = await fetch(buildUrl(path, query), {
    ...rest,
    headers: {
      ...(isFormData ? {} : { 'Content-Type': 'application/json' }),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
      ...headers,
    },
    body: isFormData ? body : body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (response.status === 401) {
    tokenStore.clear()
    // Let the auth layer react rather than hard-redirecting mid-render.
    window.dispatchEvent(new CustomEvent('tracex:unauthorized'))
    throw new ApiError('Session expired, please sign in again', 401, 'unauthorized')
  }

  if (!response.ok) {
    let message = `Request failed (${response.status})`
    let code = 'error'
    let details: unknown
    try {
      const payload = await response.json()
      message = payload?.error?.message ?? message
      code = payload?.error?.code ?? code
      details = payload?.error?.details
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(message, response.status, code, details)
  }

  if (response.status === 204) return undefined as T
  const contentType = response.headers.get('content-type') ?? ''
  if (!contentType.includes('application/json')) return (await response.blob()) as T
  return (await response.json()) as T
}

export const api = {
  get: <T>(path: string, query?: RequestOptions['query']) => apiFetch<T>(path, { method: 'GET', query }),
  post: <T>(path: string, body?: unknown, query?: RequestOptions['query']) =>
    apiFetch<T>(path, { method: 'POST', body, query }),
  patch: <T>(path: string, body?: unknown) => apiFetch<T>(path, { method: 'PATCH', body }),
  delete: <T>(path: string) => apiFetch<T>(path, { method: 'DELETE' }),
}

/** URL for endpoints consumed outside fetch (SSE, file downloads). */
export function apiUrl(path: string, query?: Record<string, string | number | undefined>): string {
  return buildUrl(path, query)
}

export async function downloadAuthenticated(path: string, filename?: string): Promise<void> {
  const token = tokenStore.get()
  const response = await fetch(buildUrl(path), {
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (!response.ok) throw new ApiError('Download failed', response.status)
  const blob = await response.blob()
  const url = URL.createObjectURL(blob)
  const link = document.createElement('a')
  link.href = url
  link.download = filename ?? 'download'
  document.body.appendChild(link)
  link.click()
  link.remove()
  URL.revokeObjectURL(url)
}

export { API_BASE }
