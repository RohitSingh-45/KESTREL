import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '@/components/layout/AppShell'
import { useAuth } from '@/auth/AuthContext'
import { LoadingBlock } from '@/components/ui/misc'
import { LoginPage } from '@/pages/LoginPage'
import { CasesPage } from '@/pages/CasesPage'
import { CaseDetailPage } from '@/pages/CaseDetailPage'
import { NewTracePage } from '@/pages/NewTracePage'
import { TraceDetailPage } from '@/pages/TraceDetailPage'
import { GraphPage } from '@/pages/GraphPage'
import { WalletLookupPage } from '@/pages/WalletLookupPage'
import { WalletProfilePage } from '@/pages/WalletProfilePage'
import { AlertsPage } from '@/pages/AlertsPage'
import { VaspDirectoryPage } from '@/pages/VaspDirectoryPage'
import { ReportsPage } from '@/pages/ReportsPage'
import { AnalyticsPage } from '@/pages/AnalyticsPage'
import { AdminPage } from '@/pages/AdminPage'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <LoadingBlock label="Restoring session" />
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

export function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        element={
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/cases" replace />} />
        <Route path="/cases" element={<CasesPage />} />
        <Route path="/cases/:caseId" element={<CaseDetailPage />} />
        <Route path="/trace" element={<NewTracePage />} />
        <Route path="/trace/:jobId" element={<TraceDetailPage />} />
        <Route path="/graph" element={<GraphPage />} />
        <Route path="/graph/:jobId" element={<GraphPage />} />
        <Route path="/wallets" element={<WalletLookupPage />} />
        <Route path="/wallets/:chain/:address" element={<WalletProfilePage />} />
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/vasps" element={<VaspDirectoryPage />} />
        <Route path="/reports" element={<ReportsPage />} />
        <Route path="/analytics" element={<AnalyticsPage />} />
        <Route path="/admin" element={<AdminPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/cases" replace />} />
    </Routes>
  )
}
