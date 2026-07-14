import { useRef, useState } from 'react'
import { FileUp, Loader2, AlertTriangle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { VegaChart } from '@/components/chart/VegaChart'
import {
  uploadPredictCsv,
  fetchPredictJob,
  type PredictJobResponse,
  type PredictPoint,
} from '@/lib/api'

export default function PredictPage() {
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<PredictJobResponse | null>(null)
  const [items, setItems] = useState<PredictPoint[]>([])
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const pollRef = useRef<number | null>(null)

  async function handleUpload() {
    if (!file) return
    setError(null)
    setLoading(true)
    setResult(null)
    setItems([])
    try {
      const res = await uploadPredictCsv(file)
      setResult(res)
      if (res.items?.length) setItems(res.items)
      if (res.mode === 'async') startPolling(res.job_id)
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  function startPolling(jobId: string) {
    if (pollRef.current) window.clearInterval(pollRef.current)
    pollRef.current = window.setInterval(async () => {
      try {
        const job = await fetchPredictJob(jobId)
        setResult((prev) => ({ ...(prev ?? job), ...job }))
        if (job.items?.length) setItems(job.items)
        if (['completed', 'failed', 'error'].includes(job.status) && pollRef.current) {
          window.clearInterval(pollRef.current)
          pollRef.current = null
        }
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e))
        if (pollRef.current) window.clearInterval(pollRef.current)
      }
    }, 4000)
  }

  return (
    <div className="flex h-full flex-col overflow-y-auto p-6">
      <div className="mb-6 max-w-3xl">
        <h1 className="text-xl font-semibold text-zinc-100">Predict from CSV</h1>
        <p className="mt-1 text-sm text-zinc-400">
          Upload a CSV with at least <code className="text-emerald-300">Date</code> and{' '}
          <code className="text-emerald-300">ItemCode</code> columns. ≤50k rows runs inline; larger files queue
          via Airflow <code className="text-zinc-300">forecast_hbaac_sku</code>.
        </p>
      </div>

      <div className="mb-6 flex max-w-3xl items-center gap-3 rounded-2xl border border-zinc-800 bg-zinc-900/60 p-4">
        <input
          type="file"
          accept=".csv"
          onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          className="flex-1 text-sm text-zinc-300 file:mr-3 file:rounded-md file:border-0 file:bg-zinc-800 file:px-3 file:py-1.5 file:text-zinc-200 file:hover:bg-zinc-700"
        />
        <Button onClick={handleUpload} disabled={!file || loading}>
          {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <FileUp className="mr-2 h-4 w-4" />}
          Predict
        </Button>
      </div>

      {error && (
        <div className="mb-4 flex max-w-3xl items-center gap-2 rounded-xl border border-red-700/40 bg-red-950/40 px-3 py-2 text-sm text-red-300">
          <AlertTriangle className="h-4 w-4" />
          <span>{error}</span>
        </div>
      )}

      {result && (
        <div className="mb-4 max-w-3xl rounded-xl border border-zinc-800 bg-zinc-900/50 p-4 text-sm">
          <div className="grid grid-cols-2 gap-y-1 sm:grid-cols-4">
            <Cell label="Job" value={result.job_id.slice(0, 12) + '…'} />
            <Cell label="Mode" value={result.mode} />
            <Cell label="Status" value={result.status} />
            <Cell label="Rows" value={result.rows?.toString() ?? '—'} />
          </div>
          {result.dag_run_id && (
            <div className="mt-2 text-xs text-zinc-400">
              Airflow run: <span className="font-mono text-zinc-200">{result.dag_run_id}</span>
            </div>
          )}
        </div>
      )}

      {result?.chart_spec && (
        <div className="mb-4 max-w-5xl rounded-xl border border-zinc-800 bg-zinc-900/40 p-3">
          <VegaChart spec={result.chart_spec} />
        </div>
      )}

      {items.length > 0 && (
        <div className="max-w-5xl overflow-x-auto rounded-xl border border-zinc-800 bg-zinc-900/40">
          <table className="min-w-full text-xs">
            <thead className="bg-zinc-900/80 text-zinc-400">
              <tr>
                <th className="px-3 py-2 text-left">SKU</th>
                <th className="px-3 py-2 text-left">Target date</th>
                <th className="px-3 py-2 text-right">Horizon</th>
                <th className="px-3 py-2 text-right">Predicted qty</th>
              </tr>
            </thead>
            <tbody>
              {items.slice(0, 200).map((p, i) => (
                <tr key={i} className="border-t border-zinc-800/60 text-zinc-200">
                  <td className="px-3 py-1.5 font-mono">{p.item_code}</td>
                  <td className="px-3 py-1.5">{p.target_date}</td>
                  <td className="px-3 py-1.5 text-right">{p.horizon}</td>
                  <td className="px-3 py-1.5 text-right">{p.predicted_quantity.toFixed(2)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {items.length > 200 && (
            <div className="border-t border-zinc-800 bg-zinc-900/60 px-3 py-2 text-[11px] text-zinc-500">
              Showing first 200 of {items.length} predictions.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function Cell({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col">
      <span className="text-[10px] uppercase tracking-wider text-zinc-500">{label}</span>
      <span className="text-zinc-100">{value}</span>
    </div>
  )
}
