import type { SSEEvent } from '@/types/events'
import { clearToken, getToken, setToken } from '@/lib/auth'

export async function apiFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const token = getToken()
  const headers = new Headers(init?.headers)
  if (token) headers.set('Authorization', `Bearer ${token}`)
  const res = await fetch(input, { ...init, headers })
  if (res.status === 401) {
    clearToken()
    window.dispatchEvent(new Event('dealight:logout'))
  }
  return res
}

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

export interface AuthUser {
  username: string
  role: 'dev' | 'manager'
}

export async function login(username: string, password: string): Promise<AuthUser> {
  const res = await fetch('/api/api/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
  if (!res.ok) {
    throw new Error(res.status === 401 ? 'Invalid username or password' : `HTTP ${res.status}`)
  }
  const body = await res.json()
  setToken(body.access_token)
  return { username: body.username, role: body.role }
}

export function fetchMe(): Promise<AuthUser> {
  return getJson('/api/api/v1/auth/me')
}

// ---------------------------------------------------------------------------
// Chat (SSE)
// ---------------------------------------------------------------------------

export async function* streamChat(
  message: string,
  signal?: AbortSignal,
): AsyncGenerator<SSEEvent> {
  const response = await apiFetch('/api/chat/stream', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ message }),
    signal,
  })

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${response.statusText}`)
  }

  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffer = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        const trimmed = line.trim()
        if (!trimmed.startsWith('data: ')) continue
        const data = trimmed.slice(6).trim()
        if (data === '[DONE]') return
        try {
          yield JSON.parse(data) as SSEEvent
        } catch {
          // skip malformed frames
        }
      }
    }
  } finally {
    reader.releaseLock()
  }
}

export async function submitApproval(
  approvalId: string,
  approved: boolean,
  comment?: string,
): Promise<void> {
  await apiFetch(`/api/chat/approval/${approvalId}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ approved, comment: comment ?? '' }),
  })
}

// ---------------------------------------------------------------------------
// Predict
// ---------------------------------------------------------------------------

export interface PredictPoint {
  item_code: string
  target_date: string
  horizon: number
  predicted_quantity: number
}

export interface PredictJobResponse {
  job_id: string
  mode: 'inline' | 'async'
  status: string
  rows?: number
  items?: PredictPoint[]
  chart_spec?: Record<string, unknown>
  dag_run_id?: string
  detail?: string
}

export async function uploadPredictCsv(file: File): Promise<PredictJobResponse> {
  const fd = new FormData()
  fd.append('file', file)
  const res = await apiFetch('/api/predict/csv', { method: 'POST', body: fd })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function fetchPredictJob(jobId: string): Promise<PredictJobResponse> {
  const res = await apiFetch(`/api/predict/jobs/${jobId}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

// ---------------------------------------------------------------------------
// Dataset registry
// ---------------------------------------------------------------------------

export type DatasetStage = 'ingest' | 'features' | 'train' | 'forecast' | 'monitor'

export interface DatasetDag {
  stage: DatasetStage
  dag_id: string
  schedule: string | null
}

export interface DatasetConfig {
  name: string
  source_type: 'file' | 'database' | 'api'
  source_format: string | null
  schedule: string
  training_schedule: string
  validation_days: number
  model_name: string
  table_name: string
  mapping: {
    entity_id: string
    ds: string
    quantity: string
    attrs: string[]
  }
  dags: DatasetDag[]
}

// The web proxy removes its first /api prefix before forwarding to FastAPI.
const DATASET_API = '/api/api/v1'

export function fetchDatasets(): Promise<DatasetConfig[]> {
  return getJson(`${DATASET_API}/datasets`)
}

export async function fetchDatasetSummary(
  dataset: string,
  targetDate: string,
): Promise<ForecastSummary | null> {
  const res = await apiFetch(
    `${DATASET_API}/${encodeURIComponent(dataset)}/forecast/summary?target_date=${encodeURIComponent(targetDate)}`,
  )
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

// ---------------------------------------------------------------------------
// Dashboard
// ---------------------------------------------------------------------------

export interface ForecastRun {
  run_id: string
  forecast_date: string
  model_name: string
  model_version: string
  status: string
  row_count: number | null
  started_at: string | null
  finished_at: string | null
  error_message: string | null
}

export interface TopSku {
  item_code: string
  target_date: string
  horizon: number
  predicted_quantity: number
}

export interface ForecastSummary {
  forecast_date: string
  target_date: string
  model_name: string
  model_version: string
  sku_count: number
  total_predicted_quantity: number
  avg_predicted_quantity: number
  max_predicted_quantity: number
}

export async function fetchLatestRun(): Promise<ForecastRun | null> {
  const res = await apiFetch('/api/forecast-runs/latest')
  if (res.status === 404) return null
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function fetchTopSkus(
  targetDate: string,
  limit = 20,
): Promise<{ items: TopSku[]; target_date: string; model_name: string }> {
  const res = await apiFetch(`/api/forecast/top-skus?target_date=${targetDate}&limit=${limit}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function fetchSummary(targetDate: string): Promise<ForecastSummary> {
  const res = await apiFetch(`/api/forecast/summary?target_date=${targetDate}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

// ---------------------------------------------------------------------------
// Drift
// ---------------------------------------------------------------------------

export interface DriftReportListItem {
  report_id: string
  run_id: string
  generated_at: string
  status: string
  drift_detected: boolean
  forecast_row_count: number
  missing_sku_count: number
  negative_prediction_count: number
  alerts: string[]
}

export interface MonitoringReport {
  report_id: string
  run_id: string
  generated_at: string
  status: string
  drift_detected: boolean
  forecast_row_count: number
  sku_count: number
  horizon_count: number
  missing_sku_count: number
  negative_prediction_count: number
  prediction_min: number
  prediction_mean: number
  prediction_max: number
  zero_ratio: number
  drift_metrics: {
    data?: { psi?: Record<string, number>; method?: string; threshold?: number; drift_detected?: boolean; drifted_columns?: string[] }
    prediction?: { psi?: Record<string, number>; drift_detected?: boolean; drifted_columns?: string[] }
  }
  alerts: string[]
}

export async function fetchMonitoringLatest(): Promise<MonitoringReport> {
  const res = await apiFetch('/api/monitoring/latest')
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export async function listDriftReports(limit = 20): Promise<{ items: DriftReportListItem[] }> {
  const res = await apiFetch(`/api/drift/reports?limit=${limit}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

export function driftHtmlUrl(reportId: string, type: 'data' | 'prediction' = 'data'): string {
  return `/api/drift/reports/${encodeURIComponent(reportId)}/html?type=${type}`
}

// ---------------------------------------------------------------------------
// Retrain
// ---------------------------------------------------------------------------

export interface RetrainTriggerResponse {
  dag_id: string
  dag_run_id: string
  state: string | null
  note: string | null
}

export interface RetrainRunStatus extends RetrainTriggerResponse {
  execution_date: string | null
  start_date: string | null
  end_date: string | null
}

export async function triggerRetrain(reason: string, featureVersion?: string): Promise<RetrainTriggerResponse> {
  const res = await apiFetch('/api/retrain/trigger', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ reason, feature_version: featureVersion ?? null }),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function fetchRetrainRun(dagRunId: string): Promise<RetrainRunStatus> {
  const res = await apiFetch(`/api/retrain/runs/${encodeURIComponent(dagRunId)}`)
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  return res.json()
}

// ---------------------------------------------------------------------------
// Ingest / DE pipeline
// ---------------------------------------------------------------------------

export interface IngestUploadResult {
  batch_id: string
  source_uri: string
  dag_id: string
  dag_run_id: string
  state: string | null
}

export interface IngestTaskState {
  task_id: string
  state: string | null
  start_date: string | null
  end_date: string | null
}

export interface IngestRunTasks {
  dag_id: string
  dag_run_id: string
  state: string | null
  tasks: IngestTaskState[]
}

export interface IngestBatchItem {
  batch_id: string
  created_at: string | null
  rows_in: number
  rows_passed: number
  rows_rejected: number
  reject_ratio: number
}

export interface IngestDqDetail {
  batch_id: string
  summary: {
    rows_in?: number
    rows_passed?: number
    rows_rejected?: number
    reject_ratio?: number
    reject_reasons?: Record<string, number>
  }
  quarantine_preview: Record<string, string>[]
  preview_truncated: boolean
}

export interface OfflineBatchStat {
  batch_id: string
  row_count: number
  min_date: string
  max_date: string
  loaded_at: string
}

export interface OfflineStats {
  as_of: string | null
  total_rows: number
  batches: OfflineBatchStat[]
}

export interface OnlineStoreItem {
  item_code: string
  found: boolean
  record: Record<string, string> | null
}

async function getJson<T>(url: string): Promise<T> {
  const res = await apiFetch(url)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json()
}

export async function uploadIngestCsv(file: File): Promise<IngestUploadResult> {
  const fd = new FormData()
  fd.append('file', file)
  const res = await apiFetch('/api/ingest/upload', { method: 'POST', body: fd })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(text || `HTTP ${res.status}`)
  }
  return res.json()
}

export function fetchIngestRunTasks(dagRunId: string): Promise<IngestRunTasks> {
  return getJson(`/api/ingest/runs/${encodeURIComponent(dagRunId)}/tasks`)
}

export function listIngestBatches(limit = 20): Promise<{ items: IngestBatchItem[] }> {
  return getJson(`/api/ingest/batches?limit=${limit}`)
}

export function fetchBatchDq(batchId: string): Promise<IngestDqDetail> {
  return getJson(`/api/ingest/batches/${encodeURIComponent(batchId)}/dq`)
}

export function fetchOfflineStats(asOf?: string): Promise<OfflineStats> {
  const suffix = asOf ? `?as_of=${encodeURIComponent(asOf)}` : ''
  return getJson(`/api/ingest/offline-store/stats${suffix}`)
}

export function fetchOnlineItem(itemCode: string): Promise<OnlineStoreItem> {
  return getJson(`/api/ingest/online-store/${encodeURIComponent(itemCode)}`)
}

// ---------------------------------------------------------------------------
// Models & promotion workflow
// ---------------------------------------------------------------------------

export interface ModelVersionItem {
  version: string
  run_id: string | null
  created_at: number
  aliases: string[]
  metrics: Record<string, number>
}

export interface ModelVersions {
  dataset: string
  model_name: string
  versions: ModelVersionItem[]
}

export interface ModelCompare {
  dataset: string
  model_name: string
  candidate: ModelVersionItem
  production: ModelVersionItem | null
}

export interface PromotionRequest {
  id: number
  dataset: string
  model_name: string
  candidate_version: string
  current_prod_version: string | null
  requested_by: string
  request_note: string | null
  status: 'pending' | 'approved' | 'rejected'
  reviewed_by: string | null
  review_comment: string | null
  created_at: string
  reviewed_at: string | null
}

export function fetchModelVersions(dataset: string): Promise<ModelVersions> {
  return getJson(`${DATASET_API}/models/${encodeURIComponent(dataset)}/versions`)
}

export async function fetchModelCompare(
  dataset: string,
  candidate?: string,
): Promise<ModelCompare | null> {
  const suffix = candidate ? `?candidate=${encodeURIComponent(candidate)}` : ''
  const res = await apiFetch(
    `${DATASET_API}/models/${encodeURIComponent(dataset)}/compare${suffix}`,
  )
  if (res.status === 404) return null
  if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`)
  return res.json()
}

export async function createPromotionRequest(
  dataset: string,
  candidateVersion: string,
  note?: string,
): Promise<PromotionRequest> {
  const res = await apiFetch(
    `${DATASET_API}/models/${encodeURIComponent(dataset)}/promotion-requests`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ candidate_version: candidateVersion, note: note ?? null }),
    },
  )
  if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`)
  return res.json()
}

export function listPromotionRequests(status?: string): Promise<{ items: PromotionRequest[] }> {
  const suffix = status ? `?status=${encodeURIComponent(status)}` : ''
  return getJson(`${DATASET_API}/promotion-requests${suffix}`)
}

export async function reviewPromotionRequest(
  id: number,
  action: 'approve' | 'reject',
  comment?: string,
): Promise<PromotionRequest> {
  const res = await apiFetch(`${DATASET_API}/promotion-requests/${id}/${action}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ comment: comment ?? null }),
  })
  if (!res.ok) throw new Error((await res.text()) || `HTTP ${res.status}`)
  return res.json()
}
