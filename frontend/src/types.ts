export type Chain = 'bitcoin' | 'ethereum' | 'bsc' | 'polygon' | 'arbitrum' | 'base' | 'tron'
export type RiskLevel = 'low' | 'medium' | 'high' | 'critical'
export type DataSource = 'live' | 'synthetic' | 'cache' | 'mixed'
export type DataMode = 'live' | 'synthetic' | 'hybrid'
export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
export type CaseStatus = 'open' | 'tracing' | 'attributed' | 'request_sent' | 'frozen' | 'closed'
export type CasePriority = 'low' | 'normal' | 'high' | 'urgent'
export type FraudCategory =
  | 'investment_scam'
  | 'task_based_fraud'
  | 'sextortion'
  | 'ransomware'
  | 'phishing'
  | 'darknet'
  | 'organised_cyber_crime'
  | 'other'
export type EntityType =
  | 'unknown'
  | 'cex_hot_wallet'
  | 'cex_deposit'
  | 'cex_cold_wallet'
  | 'vasp_other'
  | 'p2p_merchant'
  | 'mixer'
  | 'bridge'
  | 'defi_contract'
  | 'token_contract'
  | 'gambling'
  | 'darknet'
  | 'ransomware'
  | 'scam'
  | 'sanctioned'
  | 'personal_wallet'
  | 'victim'
export type AttributionMethod =
  | 'direct_label'
  | 'sanctions_list'
  | 'deposit_consolidation'
  | 'common_input_cluster'
  | 'ml_classifier'
  | 'contract_registry'
export type AlertType =
  | 'vasp_identified'
  | 'sanctions_hit'
  | 'mixer_detected'
  | 'bridge_detected'
  | 'high_value_flow'
  | 'rapid_movement'
  | 'trace_failed'
export type AlertStatus = 'new' | 'acknowledged' | 'actioned' | 'dismissed'
export type ReportKind = 'investigation_packet' | 'bnss_94_request' | 'sahyog_package'

export interface User {
  id: string
  email: string
  full_name: string
  role: 'investigator' | 'supervisor' | 'admin'
  agency: string | null
  badge_number: string | null
  is_active: boolean
  last_login_at: string | null
}

export interface Page<T> {
  items: T[]
  total: number
  limit: number
  offset: number
}

export interface CaseAddress {
  id: string
  chain: Chain
  address: string
  role: string
  amount_sent_inr: number | null
  note: string | null
  last_job_id: string | null
  last_job_status: string | null
  attributed_vasp: string | null
  risk_level: string | null
}

export interface Case {
  id: string
  case_number: string
  ncrp_ack_no: string | null
  title: string
  description: string | null
  fraud_category: FraudCategory
  status: CaseStatus
  priority: CasePriority
  victim_name: string | null
  victim_contact: string | null
  loss_amount_inr: number | null
  incident_date: string | null
  reported_date: string | null
  police_station: string | null
  state: string | null
  intake_source: string
  assigned_to_name: string | null
  created_at: string
  updated_at: string
  addresses: CaseAddress[]
  address_count: number
  alert_count: number
  open_alert_count: number
}

export interface CaseSummary {
  id: string
  case_number: string
  title: string
  fraud_category: FraudCategory
  status: CaseStatus
  priority: CasePriority
  loss_amount_inr: number | null
  state: string | null
  created_at: string
  address_count: number
  top_vasp: string | null
  highest_risk: string | null
}

export interface BulkIntakeResult {
  filename: string
  total_rows: number
  accepted: number
  rejected: number
  cases_created: number
  addresses_added: number
  case_ids: string[]
  rows: { row: number; ok: boolean; address?: string; error?: string }[]
  dry_run: boolean
}

export interface TraceAccepted {
  job_id: string
  status: JobStatus
  chain: Chain
  address: string
  stream_url: string
  backend: string
  chain_detected: boolean
  chain_activity: Record<string, number>
}

export interface AttributionFinding {
  found: boolean
  statement?: string
  address?: string
  chain?: Chain
  hop?: number
  taint?: number
  taint_pct?: number
  value_usd?: number
  entity_type?: EntityType
  entity_name?: string
  vasp_slug?: string
  confidence?: number
  method?: AttributionMethod
  terminal_reason?: string
  elapsed_seconds?: number
  elapsed_human?: string
}

export interface TraceJob {
  id: string
  case_id: string | null
  chain: Chain
  seed_address: string
  status: JobStatus
  params: Record<string, unknown>
  progress: Record<string, unknown>
  summary: {
    attribution?: AttributionFinding
    endpoints?: Record<string, unknown>[]
    vasps?: Record<string, unknown>[]
    counts?: Record<string, number>
    taint?: Record<string, number>
    risk?: {
      score?: number
      level?: RiskLevel
      reasons?: string[]
      recommended_action?: string
      sanctions_override?: boolean
    }
    typologies?: { typology?: string; label?: string; confidence?: number }[]
    cross_chain_links?: CrossChainLink[]
    recommendations?: { priority?: number; action?: string; rationale?: string; target?: string }[]
    stats?: Record<string, number>
    alerts_raised?: number
  } | null
  data_source: DataSource
  error: string | null
  created_at: string | null
  started_at: string | null
  finished_at: string | null
  duration_ms: number | null
}

export interface CrossChainLink {
  bridge_name: string
  src_chain: Chain
  src_address: string
  src_tx_hash: string
  src_ts: string
  dst_chain: Chain
  dst_address: string | null
  dst_tx_hash: string | null
  dst_ts: string | null
  value_usd: number
  time_delta_seconds: number | null
  value_delta_pct: number | null
  confidence: number
  is_inferred: boolean
}

export interface GraphNode {
  id: string
  address: string
  chain: Chain
  hop: number
  taint: number
  entity_type: string
  entity_name: string | null
  vasp_slug: string | null
  attribution_method: string | null
  attribution_confidence: number
  risk_score: number
  risk_level: string
  typologies: string[]
  value_in_usd: number
  value_out_usd: number
  is_seed: boolean
  is_terminal: boolean
  is_sanctioned: boolean
  terminal_reason: string | null
  terminal_explanation: string | null
  notes: string[]
  first_seen: string | null
}

export interface GraphEdge {
  id: string
  source: string
  target: string
  chain: Chain
  tx_hash: string
  ts: string
  block_number: number
  asset: string
  amount: string
  amount_usd: number
  hop: number
  taint_share: number
  is_on_primary_path: boolean
  edge_kind: string
}

export interface TraceGraph {
  job_id: string
  chain: Chain
  seed_address: string
  status: JobStatus
  nodes: GraphNode[]
  edges: GraphEdge[]
  summary: TraceJob['summary']
  data_source: DataSource
}

export interface AttributionCandidate {
  entity_name: string
  entity_type: EntityType
  vasp_slug: string | null
  method: AttributionMethod
  confidence: number
  is_sanctioned: boolean
  description: string
  evidence: Record<string, unknown>
}

export interface Vasp {
  id?: string
  slug: string
  name: string
  legal_name: string | null
  jurisdiction: string
  country_code: string | null
  is_indian_entity: boolean
  fiu_ind_registered: boolean
  fiu_ind_reg_no: string | null
  compliance_email: string | null
  grievance_officer: string | null
  law_enforcement_portal: string | null
  response_sla_hours: number
  accepts_emergency_freeze: boolean
  notes: string | null
  aliases?: string[]
  attributed_addresses?: number
  cases_involved?: number
  open_alerts?: number
}

export interface AttributionResult {
  chain: Chain
  address: string
  entity_name: string | null
  entity_type: EntityType
  vasp_slug: string | null
  method: AttributionMethod | null
  confidence: number
  is_vasp: boolean
  is_sanctioned: boolean
  candidates: AttributionCandidate[]
  vasp: Vasp | null
  data_source: DataSource
  notes: string[]
  cached: boolean
}

export interface ResolveResponse {
  address: string
  chain: Chain
  ambiguous: boolean
  candidates: Chain[]
  format: string
  activity: Record<string, number>
}

export interface WalletLabel {
  label: string
  source: string
  category: string | null
  entity_name: string | null
  confidence: number
  is_sanctioned: boolean
}

export interface WalletTypology {
  typology: string
  label: string
  confidence: number
  description?: string | null
}

export interface Counterparty {
  address: string
  chain: Chain
  direction: string
  transfer_count: number
  total_usd: number
  first_seen: string | null
  last_seen: string | null
  entity_type: EntityType
  entity_name: string | null
}

export interface TransferRow {
  tx_hash: string
  chain: Chain
  ts: string
  block_number: number
  direction: string
  counterparty: string
  asset: string
  amount: string
  amount_usd: number | null
  is_token: boolean
}

export interface WalletProfile {
  chain: Chain
  address: string
  entity_type: EntityType
  entity_name: string | null
  is_contract: boolean
  is_sanctioned: boolean
  risk_score: number
  risk_level: RiskLevel
  risk_reasons: string[]
  balance_native: string | null
  native_asset: string
  tx_count: number
  in_degree: number
  out_degree: number
  total_received_usd: number
  total_sent_usd: number
  first_seen: string | null
  last_seen: string | null
  labels: WalletLabel[]
  typologies: WalletTypology[]
  attribution: AttributionCandidate | null
  counterparties: Counterparty[]
  recent_transfers: TransferRow[]
  seen_in_jobs: string[]
  data_source: DataSource
  profiled_at: string | null
}

export interface Alert {
  id: string
  case_id: string | null
  case_number: string | null
  job_id: string | null
  alert_type: AlertType
  severity: RiskLevel
  status: AlertStatus
  title: string
  message: string
  chain: Chain | null
  address: string | null
  vasp_slug: string | null
  vasp_name: string | null
  recommended_action: string | null
  sla_due_at: string | null
  sla_seconds_remaining: number | null
  is_overdue: boolean
  acknowledged_at: string | null
  acknowledged_by_name: string | null
  created_at: string
}

export interface AlertCounts {
  total: number
  new: number
  acknowledged: number
  actioned: number
  dismissed: number
  overdue: number
  critical: number
  by_type: Record<string, number>
}

export interface Report {
  id: string
  case_id: string
  job_id: string | null
  kind: ReportKind
  filename: string
  content_type: string
  sha256: string
  size_bytes: number
  generated_by_name: string | null
  sections: string[]
  custody_log: { action: string; actor?: string; at: string; detail?: string }[]
  created_at: string
  download_url: string | null
}

export interface Bucket {
  key: string
  label: string
  count: number
  value_usd: number
  extra: Record<string, string | number | null>
}

export interface AnalyticsOverview {
  cases_total: number
  cases_open: number
  cases_attributed: number
  cases_frozen: number
  addresses_reported: number
  traces_run: number
  traces_succeeded: number
  vasps_identified: number
  alerts_open: number
  alerts_overdue: number
  total_loss_inr: number
  traced_value_usd: number
  attribution_rate: number
  median_trace_ms: number
  median_hops_to_vasp: number
  sanctioned_exposure_count: number
  cases_by_status: Bucket[]
  cases_by_category: Bucket[]
  recent_attributions: Bucket[]
}

export interface TypologyStats {
  distribution: Bucket[]
  by_chain: Bucket[]
  total_hits: number
}

export interface VaspAnalytics {
  top_receiving: Bucket[]
  by_jurisdiction: Bucket[]
  indian_share_pct: number
  fiu_registered_share_pct: number
}

export interface PerformanceStats {
  traces_total: number
  success_rate: number
  median_trace_ms: number
  p95_trace_ms: number
  mean_addresses_examined: number
  mean_upstream_calls: number
  by_method: Bucket[]
  by_chain: Bucket[]
  by_data_source: Bucket[]
  manual_baseline_hours: number
  median_time_to_attribution_seconds: number
}

export interface AuditEntry {
  id: string
  ts: string
  actor_email: string | null
  actor_role: string | null
  action: string
  resource_type: string
  resource_id: string | null
  ip_address: string | null
  details: Record<string, unknown> | null
}

export interface ProviderStatus {
  name: string
  chains: Chain[]
  configured: boolean
  rate_limit_per_sec: number
  detail: string
}

export interface LabelSourceStat {
  source: string
  labels: number
  sanctioned: number
  last_updated: string | null
}

export interface SystemStatus {
  data_mode: DataMode
  configured_data_mode: DataMode
  cache_backend: string
  job_backend: string
  providers: ProviderStatus[]
  label_sources: LabelSourceStat[]
  synthetic_scenarios: number
  database: string
  indexed_addresses: number
  indexed_attributions: number
}

export interface TraceTimelineStep {
  step: number
  hop: number
  chain: Chain
  from_address: string
  from_label: string
  to_address: string
  to_label: string
  asset: string
  amount: string
  amount_usd: number
  tx_hash: string
  timestamp: string
  elapsed_since_previous_seconds: number | null
  elapsed_since_previous_human: string | null
  share_of_victim_funds_pct: number
  narrative: string
}

export interface TraceTimeline {
  job_id: string
  chain: Chain
  seed_address: string
  primary_path_only: boolean
  steps: TraceTimelineStep[]
}

export interface TraceNodeDetail {
  node: GraphNode
  inbound: GraphEdge[]
  outbound: GraphEdge[]
}

export interface TraceEvent {
  seq: number
  job_id: string
  event?: string
  hop?: number
  addresses_examined?: number
  vasp_hits?: number
  message?: string
  error?: string
  summary?: TraceJob['summary']
  [key: string]: unknown
}
