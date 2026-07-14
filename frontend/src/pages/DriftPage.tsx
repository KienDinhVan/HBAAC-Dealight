import { useEffect, useState } from 'react'
import { AlertTriangle, ShieldAlert, RefreshCcw, PlayCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import {
  listDriftReports,
  driftHtmlUrl,
  triggerRetrain,
  fetchRetrainRun,
  type DriftReportListItem,
  type RetrainTriggerResponse,
} from '@/lib/api'

export default function DriftPage() {
  const [reports, setReports] = useState<DriftReportListItem[]>([])
  const [selected, setSelected] = useState<DriftReportListItem | null>(null)
  const [type, setType] = useState<'data' | 'prediction'>('data')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const [retrain, setRetrain] = useState<RetrainTriggerResponse | null>(null)
  const [retrainState, setRetrainState] = useState<string | null>(null)
  const [reason, setReason] = useState('Drift detected — manual retrain from workspace')

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const res = await listDriftReports(20)
      setReports(res.items)
      if (!selected && res.items.length) setSelected(res.items[0])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  async function handleRetrain() {
    setError(null)
    try {
      const r = await triggerRetrain(reason)
      setRetrain(r)
      setRetrainState(r.state ?? 'queued')
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    if (!retrain) return
    const id = window.setInterval(async () => {
      try {
        const r = await fetchRetrainRun(retrain.dag_run_id)
        setRetrainState(r.state)
        if (r.state && ['success', 'failed'].includes(r.state)) window.clearInterval(id)
      } catch {
        // ignore
      }
    }, 5000)
    return () => window.clearInterval(id)
  }, [retrain])

  return (
    <div className="flex h-full">
      <div className="w-80 flex-shrink-0 border-r border-zinc-800 bg-zinc-950/80">
        <div className="flex h-14 items-center justify-between border-b border-zinc-800 px-4">
          <span className="text-sm font-semibold">Drift reports</span>
          <Button variant="ghost" size="icon" onClick={load}>
            <RefreshCcw className="h-4 w-4" />
          </Button>
        </div>
        <div className="overflow-y-auto p-2" style={{ height: 'calc(100% - 3.5rem)' }}>
          {loading && <div className="px-2 text-xs text-zinc-500">Loading…</div>}
          {reports.map((r) => (
            <button
              key={r.report_id}
              onClick={() => setSelected(r)}
              className={`mb-1 w-full rounded-lg p-2 text-left text-xs transition-colors ${
                selected?.report_id === r.report_id
                  ? 'bg-emerald-600/15 border border-emerald-500/30'
                  : 'hover:bg-zinc-900'
              }`}
            >
              <div className="flex items-center justify-between">
                <span className="truncate font-mono text-zinc-200">{r.report_id}</span>
                {r.drift_detected && <ShieldAlert className="ml-2 h-3.5 w-3.5 text-amber-400" />}
              </div>
              <div className="mt-1 text-[10px] text-zinc-500">{new Date(r.generated_at).toLocaleString()}</div>
              {r.alerts.length > 0 && (
                <div className="mt-1 truncate text-[10px] text-amber-300">{r.alerts.join('; ')}</div>
              )}
            </button>
          ))}
          {!loading && !reports.length && <div className="px-2 text-xs text-zinc-500">No reports yet.</div>}
        </div>
      </div>

      <div className="flex flex-1 flex-col overflow-hidden">
        <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-3">
          <div className="flex items-center gap-3">
            {selected && (
              <>
                <span className="font-mono text-sm text-zinc-200">{selected.report_id}</span>
                <span
                  className={`rounded-full px-2 py-0.5 text-[10px] ${
                    selected.drift_detected
                      ? 'bg-amber-900/30 text-amber-300'
                      : 'bg-emerald-900/30 text-emerald-300'
                  }`}
                >
                  {selected.drift_detected ? 'drift' : 'stable'}
                </span>
                <span className="text-xs text-zinc-500">{selected.status}</span>
              </>
            )}
          </div>
          <div className="flex items-center gap-2">
            <select
              value={type}
              onChange={(e) => setType(e.target.value as 'data' | 'prediction')}
              className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-xs text-zinc-200"
            >
              <option value="data">Data drift</option>
              <option value="prediction">Prediction drift</option>
            </select>
          </div>
        </div>

        {error && (
          <div className="mx-4 mt-3 flex items-center gap-2 rounded-xl border border-red-700/40 bg-red-950/40 px-3 py-2 text-sm text-red-300">
            <AlertTriangle className="h-4 w-4" />
            <span>{error}</span>
          </div>
        )}

        <div className="m-4 rounded-xl border border-zinc-800 bg-zinc-900/50 p-3 text-xs">
          <div className="mb-2 flex items-center gap-2 text-zinc-300">
            <PlayCircle className="h-4 w-4 text-emerald-400" />
            <span className="font-medium">Trigger retrain via Airflow (train_hbaac_sku)</span>
          </div>
          <div className="flex items-center gap-2">
            <input
              type="text"
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              className="flex-1 rounded-md border border-zinc-700 bg-zinc-950 px-2 py-1 text-zinc-200"
            />
            <Button onClick={handleRetrain}>Trigger retrain</Button>
          </div>
          {retrain && (
            <div className="mt-2 text-[11px] text-zinc-400">
              dag_run_id <span className="font-mono text-zinc-200">{retrain.dag_run_id}</span> · state{' '}
              <span className="text-emerald-300">{retrainState ?? '—'}</span>
            </div>
          )}
        </div>

        <div className="flex-1 px-4 pb-4">
          {selected ? (
            <iframe
              key={`${selected.report_id}-${type}`}
              title="Evidently drift report"
              src={driftHtmlUrl(selected.report_id, type)}
              sandbox="allow-same-origin allow-scripts"
              className="h-full w-full rounded-xl border border-zinc-800 bg-white"
            />
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-zinc-500">
              Select a report from the left.
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
