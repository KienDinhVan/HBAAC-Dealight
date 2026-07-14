import { useCallback, useEffect, useRef, useState } from 'react'
import { CheckCircle2, CircleDashed, Clock, Database, ExternalLink, Loader2, RefreshCw, Search, UploadCloud, XCircle } from 'lucide-react'
import {
  fetchBatchDq,
  fetchIngestRunTasks,
  fetchOfflineStats,
  fetchOnlineItem,
  listIngestBatches,
  uploadIngestCsv,
  type IngestBatchItem,
  type IngestDqDetail,
  type IngestRunTasks,
  type OfflineStats,
  type OnlineStoreItem,
  type DatasetConfig,
} from '@/lib/api'

const STEPS = [
  { id: 'ingest_raw', label: 'Raw' },
  { id: 'process_validate_to_staging', label: 'Staging + DQ' },
  { id: 'build_curated', label: 'Curated' },
  { id: 'load_offline_store', label: 'BigQuery' },
  { id: 'sync_online_store', label: 'Redis' },
]

const card = 'rounded-md border border-zinc-800 bg-zinc-900/60 p-4'
const heading = 'mb-3 flex items-center gap-2 text-sm font-semibold text-zinc-200'

function StepIcon({ state }: { state: string | null | undefined }) {
  if (state === 'success') return <CheckCircle2 className="h-5 w-5 text-emerald-400" />
  if (state === 'running' || state === 'queued' || state === 'up_for_retry')
    return <Loader2 className="h-5 w-5 animate-spin text-amber-400" />
  if (state === 'failed' || state === 'upstream_failed')
    return <XCircle className="h-5 w-5 text-red-400" />
  return <CircleDashed className="h-5 w-5 text-zinc-600" />
}

export default function PipelinePage({ dataset }: { dataset: DatasetConfig | null }) {
  const [uploading, setUploading] = useState(false)
  const [uploadError, setUploadError] = useState<string | null>(null)
  const [activeBatch, setActiveBatch] = useState<string | null>(null)
  const [run, setRun] = useState<IngestRunTasks | null>(null)
  const [batches, setBatches] = useState<IngestBatchItem[]>([])
  const [batchesError, setBatchesError] = useState<string | null>(null)
  const [dq, setDq] = useState<IngestDqDetail | null>(null)
  const [stats, setStats] = useState<OfflineStats | null>(null)
  const [statsAsOf, setStatsAsOf] = useState<OfflineStats | null>(null)
  const [statsError, setStatsError] = useState<string | null>(null)
  const [asOf, setAsOf] = useState('')
  const [itemCode, setItemCode] = useState('')
  const [onlineItem, setOnlineItem] = useState<OnlineStoreItem | null>(null)
  const [onlineError, setOnlineError] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const pollRef = useRef<number | null>(null)

  const refreshBatches = useCallback(() => {
    listIngestBatches()
      .then((r) => {
        setBatches(r.items)
        setBatchesError(null)
      })
      .catch((e: Error) => setBatchesError(e.message))
  }, [])

  const refreshStats = useCallback(() => {
    fetchOfflineStats()
      .then((s) => {
        setStats(s)
        setStatsError(null)
      })
      .catch((e: Error) => setStatsError(e.message))
  }, [])

  useEffect(() => {
    if (dataset?.name === 'hbaac_sku') {
      refreshBatches()
      refreshStats()
    }
  }, [dataset?.name, refreshBatches, refreshStats])

  // Poll DAG task states every 3s while a run is in flight.
  const startPolling = useCallback(
    (dagRunId: string) => {
      if (pollRef.current) window.clearInterval(pollRef.current)
      const tick = async () => {
        try {
          const r = await fetchIngestRunTasks(dagRunId)
          setRun(r)
          if (r.state === 'success' || r.state === 'failed') {
            if (pollRef.current) window.clearInterval(pollRef.current)
            pollRef.current = null
            refreshBatches()
            refreshStats()
          }
        } catch {
          // keep polling; transient errors are fine mid-run
        }
      }
      void tick()
      pollRef.current = window.setInterval(tick, 3000)
    },
    [refreshBatches, refreshStats],
  )

  useEffect(
    () => () => {
      if (pollRef.current) window.clearInterval(pollRef.current)
    },
    [],
  )

  const onUpload = async (file: File) => {
    setUploading(true)
    setUploadError(null)
    setRun(null)
    try {
      const result = await uploadIngestCsv(file)
      setActiveBatch(result.batch_id)
      startPolling(result.dag_run_id)
    } catch (e) {
      setUploadError((e as Error).message)
    } finally {
      setUploading(false)
    }
  }

  const showDq = (batchId: string) => {
    setDq(null)
    fetchBatchDq(batchId)
      .then(setDq)
      .catch((e: Error) => setBatchesError(e.message))
  }

  const compareAsOf = () => {
    if (!asOf) {
      setStatsAsOf(null)
      return
    }
    fetchOfflineStats(new Date(asOf).toISOString())
      .then(setStatsAsOf)
      .catch((e: Error) => setStatsError(e.message))
  }

  const lookupItem = () => {
    if (!itemCode) return
    setOnlineItem(null)
    setOnlineError(null)
    fetchOnlineItem(itemCode.trim())
      .then(setOnlineItem)
      .catch((e: Error) => setOnlineError(e.message))
  }

  const taskState = (taskId: string) =>
    run?.tasks.find((t) => t.task_id === taskId)?.state

  return (
    <div className="h-full overflow-y-auto p-4 sm:p-6">
      <div className="mx-auto flex max-w-5xl flex-col gap-4">
        <div className="flex flex-wrap items-end justify-between gap-2">
          <div>
            <h1 className="text-lg font-semibold text-zinc-100">Data Pipeline</h1>
            <p className="mt-0.5 font-mono text-xs text-emerald-300">
              {dataset?.name ?? 'Loading registry'}
            </p>
          </div>
          {dataset && (
            <div className="text-right text-[11px] text-zinc-500">
              <div>{dataset.source_type} · {dataset.table_name}</div>
              <div>ingest {dataset.schedule}</div>
            </div>
          )}
        </div>

        {dataset && <DatasetDagOverview dataset={dataset} />}

        {dataset?.name === 'hbaac_sku' ? <>

        {/* Upload + DAG progress */}
        <section className={card}>
          <div className={heading}>
            <UploadCloud className="h-4 w-4 text-emerald-400" /> Upload CSV → GCS → Airflow
          </div>
          <div className="flex items-center gap-3">
            <input
              ref={fileInput}
              type="file"
              accept=".csv"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0]
                if (f) void onUpload(f)
                e.target.value = ''
              }}
            />
            <button
              onClick={() => fileInput.current?.click()}
              disabled={uploading}
              className="rounded-xl border border-emerald-500/40 bg-emerald-600/20 px-4 py-2 text-sm font-medium text-emerald-200 hover:bg-emerald-600/30 disabled:opacity-50"
            >
              {uploading ? 'Đang upload…' : 'Chọn file CSV'}
            </button>
            {activeBatch && (
              <span className="text-xs text-zinc-400">
                batch <code className="text-emerald-300">{activeBatch.slice(0, 12)}…</code>
                {run?.state && <> · DAG: <b className="text-zinc-200">{run.state}</b></>}
              </span>
            )}
          </div>
          {uploadError && <p className="mt-2 text-xs text-red-400">{uploadError}</p>}

          <div className="mt-4 overflow-x-auto">
            <div className="flex min-w-[620px] items-center">
              {STEPS.map((s, i) => (
                <div key={s.id} className="flex flex-1 items-center">
                  <div className="flex flex-col items-center gap-1">
                    <StepIcon state={taskState(s.id)} />
                    <span className="text-[11px] text-zinc-400">{s.label}</span>
                  </div>
                  {i < STEPS.length - 1 && (
                    <div
                      className={`mx-2 h-px flex-1 ${taskState(s.id) === 'success' ? 'bg-emerald-500/60' : 'bg-zinc-700'}`}
                    />
                  )}
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Batches + DQ */}
        <section className={card}>
          <div className={heading}>
            <Database className="h-4 w-4 text-emerald-400" /> Batches & Data Quality
            <button onClick={refreshBatches} className="ml-auto text-zinc-400 hover:text-zinc-200">
              <RefreshCw className="h-4 w-4" />
            </button>
          </div>
          {batchesError && <p className="mb-2 text-xs text-red-400">{batchesError}</p>}
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <table className="w-full text-left text-xs">
              <thead className="text-zinc-500">
                <tr>
                  <th className="py-1">Batch</th>
                  <th>Rows in</th>
                  <th>Passed</th>
                  <th>Rejected</th>
                  <th>Ratio</th>
                </tr>
              </thead>
              <tbody>
                {batches.map((b) => (
                  <tr
                    key={b.batch_id}
                    onClick={() => showDq(b.batch_id)}
                    className="cursor-pointer border-t border-zinc-800 text-zinc-300 hover:bg-zinc-800/50"
                  >
                    <td className="py-1.5 font-mono text-emerald-300">{b.batch_id.slice(0, 10)}…</td>
                    <td>{b.rows_in.toLocaleString()}</td>
                    <td>{b.rows_passed.toLocaleString()}</td>
                    <td>{b.rows_rejected.toLocaleString()}</td>
                    <td>{(b.reject_ratio * 100).toFixed(2)}%</td>
                  </tr>
                ))}
                {batches.length === 0 && (
                  <tr>
                    <td colSpan={5} className="py-3 text-zinc-500">
                      Chưa có batch nào — upload một file CSV để bắt đầu.
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
            <div className="rounded-xl border border-zinc-800 bg-zinc-950/60 p-3 text-xs">
              {!dq && <p className="text-zinc-500">Click một batch để xem chi tiết DQ + quarantine.</p>}
              {dq && (
                <>
                  <p className="mb-1 font-mono text-emerald-300">{dq.batch_id}</p>
                  <p className="text-zinc-300">
                    Lý do reject:{' '}
                    {Object.entries(dq.summary.reject_reasons ?? {}).map(([k, v]) => (
                      <span key={k} className="mr-2 rounded bg-red-500/10 px-1.5 py-0.5 text-red-300">
                        {k}: {v.toLocaleString()}
                      </span>
                    ))}
                    {Object.keys(dq.summary.reject_reasons ?? {}).length === 0 && '—'}
                  </p>
                  {dq.quarantine_preview.length > 0 && (
                    <div className="mt-2 max-h-48 overflow-auto">
                      <table className="w-full text-[11px]">
                        <thead className="text-zinc-500">
                          <tr>
                            {Object.keys(dq.quarantine_preview[0]).map((c) => (
                              <th key={c} className="pr-2 text-left">{c}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody className="text-zinc-400">
                          {dq.quarantine_preview.map((row, i) => (
                            <tr key={i} className="border-t border-zinc-800/60">
                              {Object.values(row).map((v, j) => (
                                <td key={j} className="pr-2">{v}</td>
                              ))}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      {dq.preview_truncated && (
                        <p className="mt-1 text-zinc-500">…và nhiều dòng khác trong quarantine/.</p>
                      )}
                    </div>
                  )}
                </>
              )}
            </div>
          </div>
        </section>

        {/* Offline store + time travel, Online store */}
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <section className={card}>
            <div className={heading}>
              <Clock className="h-4 w-4 text-emerald-400" /> BigQuery (Iceberg) + Time travel
            </div>
            {statsError && <p className="mb-2 text-xs text-red-400">{statsError}</p>}
            <p className="text-xs text-zinc-400">
              Hiện tại: <b className="text-zinc-100">{stats?.total_rows.toLocaleString() ?? '…'}</b> dòng ·{' '}
              {stats?.batches.length ?? 0} batch
            </p>
            <div className="mt-2 flex items-center gap-2">
              <input
                type="datetime-local"
                value={asOf}
                onChange={(e) => setAsOf(e.target.value)}
                className="rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-200"
              />
              <button
                onClick={compareAsOf}
                className="rounded-lg border border-zinc-700 px-3 py-1 text-xs text-zinc-200 hover:bg-zinc-800"
              >
                Xem tại thời điểm
              </button>
            </div>
            {statsAsOf && (
              <p className="mt-2 text-xs text-amber-300">
                Tại {asOf}: <b>{statsAsOf.total_rows.toLocaleString()}</b> dòng ·{' '}
                {statsAsOf.batches.length} batch{' '}
                {stats && statsAsOf.total_rows !== stats.total_rows && (
                  <span className="text-zinc-400">
                    (chênh {Math.abs(stats.total_rows - statsAsOf.total_rows).toLocaleString()} dòng so với hiện tại)
                  </span>
                )}
              </p>
            )}
            <div className="mt-2 max-h-36 overflow-auto text-[11px] text-zinc-400">
              {stats?.batches.map((b) => (
                <div key={b.batch_id} className="flex justify-between border-t border-zinc-800/60 py-1">
                  <span className="font-mono">{b.batch_id.slice(0, 10)}…</span>
                  <span>{b.row_count.toLocaleString()} dòng</span>
                  <span>
                    {b.min_date} → {b.max_date}
                  </span>
                </div>
              ))}
            </div>
          </section>

          <section className={card}>
            <div className={heading}>
              <Search className="h-4 w-4 text-emerald-400" /> Redis Online Store
            </div>
            <div className="flex items-center gap-2">
              <input
                value={itemCode}
                onChange={(e) => setItemCode(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && lookupItem()}
                placeholder="Nhập item_code, vd SKU-00002"
                className="flex-1 rounded-lg border border-zinc-700 bg-zinc-950 px-2 py-1 text-xs text-zinc-200"
              />
              <button
                onClick={lookupItem}
                className="rounded-lg border border-zinc-700 px-3 py-1 text-xs text-zinc-200 hover:bg-zinc-800"
              >
                Tra cứu
              </button>
            </div>
            {onlineError && <p className="mt-2 text-xs text-red-400">{onlineError}</p>}
            {onlineItem && !onlineItem.found && (
              <p className="mt-2 text-xs text-zinc-400">Không có SKU này trong online store.</p>
            )}
            {onlineItem?.record && (
              <dl className="mt-3 grid grid-cols-2 gap-y-1 text-xs">
                {Object.entries(onlineItem.record).map(([k, v]) => (
                  <div key={k} className="contents">
                    <dt className="text-zinc-500">{k}</dt>
                    <dd className="font-mono text-zinc-200">{v}</dd>
                  </div>
                ))}
              </dl>
            )}
          </section>
        </div>
        </> : dataset ? <DatasetOperations dataset={dataset} /> : null}
      </div>
    </div>
  )
}

function airflowDagUrl(dagId: string): string {
  const { protocol, hostname } = window.location
  const host = hostname.startsWith('web.')
    ? hostname.replace(/^web\./, 'airflow.')
    : `${hostname}:8080`
  return `${protocol}//${host}/dags/${encodeURIComponent(dagId)}/grid`
}

function DatasetDagOverview({ dataset }: { dataset: DatasetConfig }) {
  return (
    <section className={card}>
      <div className={heading}>
        <Database className="h-4 w-4 text-sky-400" /> Factory DAGs
        <span className="ml-auto rounded border border-emerald-700/50 bg-emerald-950/40 px-1.5 py-0.5 text-[10px] font-medium text-emerald-300">
          Registered
        </span>
      </div>
      <div className="overflow-x-auto">
        <div className="grid min-w-[720px] grid-cols-5 border-y border-zinc-800">
          {dataset.dags.map((dag, index) => (
            <div key={dag.dag_id} className={`min-w-0 px-3 py-3 ${index ? 'border-l border-zinc-800' : ''}`}>
              <div className="mb-2 flex items-center justify-between">
                <span className="text-[10px] font-medium uppercase text-zinc-500">{dag.stage}</span>
                <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
              </div>
              <div className="truncate font-mono text-xs text-zinc-200" title={dag.dag_id}>
                {dag.dag_id}
              </div>
              <div className="mt-1 text-[10px] text-zinc-500">{dag.schedule ?? 'manual'}</div>
              <a
                href={airflowDagUrl(dag.dag_id)}
                target="_blank"
                rel="noreferrer"
                title={`Open ${dag.dag_id} in Airflow`}
                className="mt-2 inline-flex h-6 w-6 items-center justify-center rounded text-zinc-500 hover:bg-zinc-800 hover:text-zinc-200"
              >
                <ExternalLink className="h-3.5 w-3.5" />
              </a>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

function DatasetOperations({ dataset }: { dataset: DatasetConfig }) {
  return (
    <section className={card}>
      <div className={heading}>
        <CheckCircle2 className="h-4 w-4 text-emerald-400" /> Runtime resources
      </div>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-4 text-xs sm:grid-cols-2 lg:grid-cols-4">
        <ResourceField label="Source adapter" value={dataset.source_type} />
        <ResourceField label="Canonical table" value={dataset.table_name} />
        <ResourceField label="Registered model" value={dataset.model_name} />
        <ResourceField label="Validation window" value={`${dataset.validation_days} days`} />
        <ResourceField label="Entity column" value={dataset.mapping.entity_id} />
        <ResourceField label="Date column" value={dataset.mapping.ds} />
        <ResourceField label="Quantity column" value={dataset.mapping.quantity} />
        <ResourceField label="Attributes" value={dataset.mapping.attrs.join(', ') || 'None'} />
      </dl>
    </section>
  )
}

function ResourceField({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="mb-1 text-[10px] font-medium uppercase text-zinc-500">{label}</dt>
      <dd className="break-all font-mono text-zinc-200">{value}</dd>
    </div>
  )
}
