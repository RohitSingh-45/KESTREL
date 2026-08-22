import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api, apiUrl, downloadAuthenticated } from '@/api/client'
import type {
  Alert,
  AlertCounts,
  AnalyticsOverview,
  AttributionResult,
  AuditEntry,
  Bucket,
  BulkIntakeResult,
  Case,
  CaseSummary,
  Chain,
  DataMode,
  Page,
  PerformanceStats,
  Report,
  ReportKind,
  ResolveResponse,
  SystemStatus,
  TraceAccepted,
  TraceGraph,
  TraceJob,
  TypologyStats,
  Vasp,
  VaspAnalytics,
  WalletProfile,
} from '@/types'

export const queryKeys = {
  cases: (filters?: unknown) => ['cases', filters] as const,
  case: (id: string) => ['case', id] as const,
  job: (id: string) => ['job', id] as const,
  jobs: (caseId?: string) => ['jobs', caseId ?? 'all'] as const,
  graph: (id: string) => ['graph', id] as const,
  caseGraph: (id: string) => ['case-graph', id] as const,
  wallet: (chain: string, address: string) => ['wallet', chain, address] as const,
  attribution: (chain: string, address: string) => ['attribution', chain, address] as const,
  alerts: (filters?: unknown) => ['alerts', filters] as const,
  alertCounts: ['alerts', 'counts'] as const,
  vasps: (search?: string) => ['vasps', search] as const,
  reports: (caseId?: string) => ['reports', caseId ?? 'all'] as const,
  analytics: (kind: string) => ['analytics', kind] as const,
  audit: (filters?: unknown) => ['audit', filters] as const,
  system: ['system', 'status'] as const,
}

export function useCases(filters: Record<string, string | number | undefined> = {}) {
  return useQuery({
    queryKey: queryKeys.cases(filters),
    queryFn: () => api.get<Page<CaseSummary>>('/cases', filters),
  })
}

export function useCase(id: string | undefined) {
  return useQuery({
    queryKey: queryKeys.case(id ?? ''),
    queryFn: () => api.get<Case>(`/cases/${id}`),
    enabled: Boolean(id),
  })
}

export function useCreateCase() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: unknown) => api.post<Case>('/cases', payload),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['cases'] }),
  })
}

export function useUpdateCase() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string } & Record<string, unknown>) =>
      api.patch<Case>(`/cases/${id}`, body),
    onSuccess: (_data, vars) => {
      void qc.invalidateQueries({ queryKey: queryKeys.case(vars.id) })
      void qc.invalidateQueries({ queryKey: ['cases'] })
    },
  })
}

export function useImportNcrpCsv() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (file: File) => {
      const form = new FormData()
      form.append('file', file)
      return api.post<BulkIntakeResult>('/cases/import', form)
    },
    onSuccess: () => qc.invalidateQueries({ queryKey: ['cases'] }),
  })
}

export function useStartTrace() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (payload: {
      address: string
      chain?: Chain
      case_id?: string
      max_hops?: number
      max_fanout?: number
      min_taint?: number
    }) => api.post<TraceAccepted>('/trace', payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['jobs'] })
      void qc.invalidateQueries({ queryKey: ['cases'] })
    },
  })
}

export function useTraceJob(id: string | undefined, poll = false) {
  return useQuery({
    queryKey: queryKeys.job(id ?? ''),
    queryFn: () => api.get<TraceJob>(`/trace/${id}`),
    enabled: Boolean(id),
    refetchInterval: (query) => {
      if (!poll) return false
      const status = query.state.data?.status
      return status === 'queued' || status === 'running' ? 1500 : false
    },
  })
}

export function useTraceJobs(caseId?: string) {
  return useQuery({
    queryKey: queryKeys.jobs(caseId),
    queryFn: () => api.get<Page<TraceJob>>('/trace', { case_id: caseId, limit: 50 }),
  })
}

export function useTraceGraph(jobId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.graph(jobId ?? ''),
    queryFn: () => api.get<TraceGraph>(`/trace/${jobId}/graph`),
    enabled: Boolean(jobId),
  })
}

export function useCaseGraph(caseId: string | undefined) {
  return useQuery({
    queryKey: queryKeys.caseGraph(caseId ?? ''),
    queryFn: () => api.get<TraceGraph>(`/cases/${caseId}/graph`),
    enabled: Boolean(caseId),
  })
}

export function useResolveAddress(address: string, probe = false) {
  return useQuery({
    queryKey: ['resolve', address, probe],
    queryFn: () => api.get<ResolveResponse>('/attribution/resolve', { address, probe }),
    enabled: address.length >= 8,
    staleTime: 30_000,
  })
}

export function useWallet(chain: string | undefined, address: string | undefined) {
  return useQuery({
    queryKey: queryKeys.wallet(chain ?? '', address ?? ''),
    queryFn: () => api.get<WalletProfile>(`/wallets/${chain}/${address}`),
    enabled: Boolean(chain && address),
  })
}

export function useAttribution(chain: string | undefined, address: string | undefined) {
  return useQuery({
    queryKey: queryKeys.attribution(chain ?? '', address ?? ''),
    queryFn: () => api.get<AttributionResult>(`/attribution/${chain}/${address}`),
    enabled: Boolean(chain && address),
  })
}

export function useAlerts(filters: Record<string, string | number | boolean | undefined> = {}) {
  return useQuery({
    queryKey: queryKeys.alerts(filters),
    queryFn: () => api.get<Page<Alert>>('/alerts', filters),
    refetchInterval: 20000,
  })
}

export function useAlertCount() {
  return useQuery({
    queryKey: queryKeys.alertCounts,
    queryFn: async () => {
      const counts = await api.get<AlertCounts>('/alerts/counts')
      return counts.new
    },
    refetchInterval: 30000,
  })
}

export function useAlertCounts() {
  return useQuery({
    queryKey: [...queryKeys.alertCounts, 'full'],
    queryFn: () => api.get<AlertCounts>('/alerts/counts'),
    refetchInterval: 30000,
  })
}

export function useUpdateAlert() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({ id, status, note }: { id: string; status: string; note?: string }) =>
      api.patch<Alert>(`/alerts/${id}`, { status, note }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['alerts'] })
    },
  })
}

export function useVasps(search?: string) {
  return useQuery({
    queryKey: queryKeys.vasps(search),
    queryFn: () => api.get<Page<Vasp>>('/vasps', { q: search, limit: 200 }),
  })
}

export function useReports(caseId?: string) {
  return useQuery({
    queryKey: queryKeys.reports(caseId),
    queryFn: () => api.get<Page<Report>>('/reports', { case_id: caseId, limit: 100 }),
  })
}

export function useGenerateReport() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: ({
      caseId,
      ...body
    }: {
      caseId: string
      kind: ReportKind
      job_id?: string
      sections?: string[]
    }) => api.post<Report>(`/reports/${caseId}/generate`, body),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['reports'] }),
  })
}

export function useDownloadReport() {
  return useMutation({
    mutationFn: ({ id, filename }: { id: string; filename: string }) =>
      downloadAuthenticated(`/reports/${id}/download`, filename),
  })
}

export function useAnalyticsOverview() {
  return useQuery({
    queryKey: queryKeys.analytics('overview'),
    queryFn: () => api.get<AnalyticsOverview>('/analytics/overview'),
  })
}

export function useTypologyStats() {
  return useQuery({
    queryKey: queryKeys.analytics('typologies'),
    queryFn: () => api.get<TypologyStats>('/analytics/typologies'),
  })
}

export function useVaspInflows() {
  return useQuery({
    queryKey: queryKeys.analytics('vasps'),
    queryFn: () => api.get<VaspAnalytics>('/analytics/vasps'),
  })
}

export function useChainStats() {
  return useQuery({
    queryKey: queryKeys.analytics('chains'),
    queryFn: () => api.get<Bucket[]>('/analytics/chains'),
  })
}

export function usePerformanceStats() {
  return useQuery({
    queryKey: queryKeys.analytics('performance'),
    queryFn: () => api.get<PerformanceStats>('/analytics/performance'),
  })
}

export function useSystemStatus() {
  return useQuery({
    queryKey: queryKeys.system,
    queryFn: () => api.get<SystemStatus>('/integrations/system-status'),
  })
}

export function useSetDataMode() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: (data_mode: DataMode) =>
      api.patch<SystemStatus>('/integrations/settings', { data_mode }),
    onSuccess: () => qc.invalidateQueries({ queryKey: queryKeys.system }),
  })
}

export function useAuditLog(filters: Record<string, string | number | undefined> = {}) {
  return useQuery({
    queryKey: queryKeys.audit(filters),
    queryFn: () => api.get<Page<AuditEntry>>('/integrations/audit', filters),
  })
}

export function useMockNcrpPush() {
  const qc = useQueryClient()
  return useMutation({
    mutationFn: () => api.post<{ created: boolean; case_id: string; case_number: string }>('/integrations/ncrp/mock-push', {}),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['cases'] }),
  })
}

export function traceStreamUrl(jobId: string, from = 0): string {
  return apiUrl(`/trace/${jobId}/stream`, { from })
}
