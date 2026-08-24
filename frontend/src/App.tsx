import { lazy, Suspense } from 'react'
import { Navigate, Route, Routes } from 'react-router-dom'
import { AppShell } from '@/components/layout/AppShell'
import { useAuth } from '@/auth/AuthContext'
import { LoadingBlock } from '@/components/ui/misc'
import { LoginPage } from '@/pages/LoginPage'

// Route-level code splitting: cytoscape (graph pages) and recharts
// (analytics) are each a meaningful chunk of the bundle on their own, and
// most sessions never touch every page in one visit - loading them on
// navigation instead of on first paint keeps the initial bundle to what a
// login actually needs.
const CasesPage = lazy(() => import('@/pages/CasesPage').then((m) => ({ default: m.CasesPage })))
const CaseDetailPage = lazy(() =>
  import('@/pages/CaseDetailPage').then((m) => ({ default: m.CaseDetailPage })),
)
const NewTracePage = lazy(() =>
  import('@/pages/NewTracePage').then((m) => ({ default: m.NewTracePage })),
)
const TraceDetailPage = lazy(() =>
  import('@/pages/TraceDetailPage').then((m) => ({ default: m.TraceDetailPage })),
)
const GraphPage = lazy(() => import('@/pages/GraphPage').then((m) => ({ default: m.GraphPage })))
const WalletLookupPage = lazy(() =>
  import('@/pages/WalletLookupPage').then((m) => ({ default: m.WalletLookupPage })),
)
const WalletProfilePage = lazy(() =>
  import('@/pages/WalletProfilePage').then((m) => ({ default: m.WalletProfilePage })),
)
const AlertsPage = lazy(() => import('@/pages/AlertsPage').then((m) => ({ default: m.AlertsPage })))
const VaspDirectoryPage = lazy(() =>
  import('@/pages/VaspDirectoryPage').then((m) => ({ default: m.VaspDirectoryPage })),
)
const ReportsPage = lazy(() =>
  import('@/pages/ReportsPage').then((m) => ({ default: m.ReportsPage })),
)
const AnalyticsPage = lazy(() =>
  import('@/pages/AnalyticsPage').then((m) => ({ default: m.AnalyticsPage })),
)
const AdminPage = lazy(() => import('@/pages/AdminPage').then((m) => ({ default: m.AdminPage })))

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user, loading } = useAuth()
  if (loading) return <LoadingBlock label="Restoring session" />
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

export function App() {
  return (
    <Suspense fallback={<LoadingBlock label="Loading" />}>
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
    </Suspense>
  )
}
