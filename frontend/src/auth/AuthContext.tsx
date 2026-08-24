import * as React from 'react'
import { api, tokenStore } from '@/api/client'
import type { User } from '@/types'

interface AuthState {
  user: User | null
  loading: boolean
  login: (email: string, password: string) => Promise<void>
  logout: () => void
  hasRole: (minimum: User['role']) => boolean
}

const ROLE_RANK: Record<User['role'], number> = { investigator: 1, supervisor: 2, admin: 3 }

const AuthContext = React.createContext<AuthState | null>(null)

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = React.useState<User | null>(null)
  // No stored token means there's nothing to restore, so the initial value
  // already reflects that - no need for an effect to flip it on mount.
  const [loading, setLoading] = React.useState(() => Boolean(tokenStore.get()))

  const logout = React.useCallback(() => {
    tokenStore.clear()
    setUser(null)
  }, [])

  React.useEffect(() => {
    const onUnauthorized = () => setUser(null)
    window.addEventListener('tracex:unauthorized', onUnauthorized)
    return () => window.removeEventListener('tracex:unauthorized', onUnauthorized)
  }, [])

  React.useEffect(() => {
    if (!tokenStore.get()) return
    api
      .get<User>('/auth/me')
      .then(setUser)
      .catch(() => tokenStore.clear())
      .finally(() => setLoading(false))
  }, [])

  const login = React.useCallback(async (email: string, password: string) => {
    const result = await api.post<{ access_token: string; user: User }>('/auth/login', {
      email,
      password,
    })
    tokenStore.set(result.access_token)
    setUser(result.user)
  }, [])

  const hasRole = React.useCallback(
    (minimum: User['role']) => (user ? ROLE_RANK[user.role] >= ROLE_RANK[minimum] : false),
    [user],
  )

  const value = React.useMemo(
    () => ({ user, loading, login, logout, hasRole }),
    [user, loading, login, logout, hasRole],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}

export function useAuth(): AuthState {
  const context = React.useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used inside AuthProvider')
  return context
}
