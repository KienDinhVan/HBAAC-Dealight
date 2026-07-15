import { useCallback, useEffect, useState } from 'react'
import { GitCompareArrows, Rocket } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ComparePanel } from '@/components/models/ComparePanel'
import {
  createPromotionRequest,
  fetchModelCompare,
  fetchModelVersions,
  listPromotionRequests,
  type ModelCompare,
  type ModelVersions,
  type PromotionRequest,
} from '@/lib/api'

const STATUS_STYLE: Record<PromotionRequest['status'], string> = {
  pending: 'bg-amber-500/15 text-amber-300 border-amber-500/30',
  approved: 'bg-emerald-500/15 text-emerald-300 border-emerald-500/30',
  rejected: 'bg-red-500/15 text-red-300 border-red-500/30',
}

export default function ModelsPage({ dataset }: { dataset: string }) {
  const [versions, setVersions] = useState<ModelVersions | null>(null)
  const [compare, setCompare] = useState<ModelCompare | null>(null)
  const [requests, setRequests] = useState<PromotionRequest[]>([])
  const [note, setNote] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const reload = useCallback(() => {
    setError(null)
    fetchModelVersions(dataset).then(setVersions).catch((e: Error) => setError(e.message))
    fetchModelCompare(dataset).then(setCompare).catch(() => setCompare(null))
    listPromotionRequests()
      .then(({ items }) => setRequests(items.filter((r) => r.dataset === dataset)))
      .catch(() => setRequests([]))
  }, [dataset])

  useEffect(reload, [reload])

  const requestPromote = async () => {
    if (!compare) return
    setBusy(true)
    setError(null)
    try {
      await createPromotionRequest(dataset, compare.candidate.version, note || undefined)
      setNote('')
      reload()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request failed')
    } finally {
      setBusy(false)
    }
  }

  const candidateIsProd = compare?.production?.version === compare?.candidate.version

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6">
      <div className="mx-auto max-w-4xl space-y-6">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">Models</h1>
          <p className="text-sm text-zinc-500">
            {versions?.model_name ?? `${dataset}-forecaster`} — registry versions & promotion
          </p>
        </div>

        {error && (
          <div className="rounded-md border border-red-800 bg-red-950/40 p-3 text-sm text-red-300">
            {error}
          </div>
        )}

        <section className="space-y-2">
          <h2 className="flex items-center gap-2 text-sm font-medium text-zinc-300">
            <GitCompareArrows className="h-4 w-4 text-emerald-400" /> Staging vs Production
          </h2>
          {compare ? (
            <>
              <ComparePanel compare={compare} />
              <div className="flex items-center gap-2">
                <input
                  value={note}
                  onChange={(e) => setNote(e.target.value)}
                  placeholder="Note for the reviewer (optional)"
                  className="h-9 flex-1 rounded-md border border-zinc-700 bg-zinc-900 px-3 text-sm text-zinc-100 outline-none focus:border-emerald-500"
                />
                <Button
                  onClick={requestPromote}
                  disabled={busy || candidateIsProd}
                  className="bg-emerald-600 hover:bg-emerald-500 text-white"
                >
                  <Rocket className="h-4 w-4" /> Request promote
                </Button>
              </div>
            </>
          ) : (
            <p className="text-sm text-zinc-500">
              No staging candidate yet — trigger a retrain from the Drift page first.
            </p>
          )}
        </section>

        <section className="space-y-2">
          <h2 className="text-sm font-medium text-zinc-300">Versions</h2>
          <div className="overflow-x-auto rounded-lg border border-zinc-800">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-zinc-800 text-left text-xs uppercase text-zinc-500">
                  <th className="px-3 py-2">Version</th>
                  <th className="px-3 py-2">Alias</th>
                  <th className="px-3 py-2">WAPE</th>
                  <th className="px-3 py-2">MAE</th>
                  <th className="px-3 py-2">Created</th>
                </tr>
              </thead>
              <tbody>
                {(versions?.versions ?? []).map((v) => (
                  <tr key={v.version} className="border-b border-zinc-800/60 last:border-0">
                    <td className="px-3 py-2 font-medium text-zinc-100">v{v.version}</td>
                    <td className="px-3 py-2">
                      {v.aliases.map((a) => (
                        <span
                          key={a}
                          className={`mr-1 rounded border px-1.5 py-0.5 text-[10px] uppercase ${
                            a === 'production'
                              ? 'border-emerald-500/30 bg-emerald-500/15 text-emerald-300'
                              : 'border-amber-500/30 bg-amber-500/15 text-amber-300'
                          }`}
                        >
                          {a}
                        </span>
                      ))}
                    </td>
                    <td className="px-3 py-2 text-zinc-300">
                      {v.metrics.lightgbm_wape?.toFixed(4) ?? '—'}
                    </td>
                    <td className="px-3 py-2 text-zinc-300">
                      {v.metrics.lightgbm_mae?.toFixed(4) ?? '—'}
                    </td>
                    <td className="px-3 py-2 text-zinc-500">
                      {new Date(v.created_at).toLocaleString()}
                    </td>
                  </tr>
                ))}
                {!versions?.versions.length && (
                  <tr>
                    <td colSpan={5} className="px-3 py-4 text-center text-zinc-500">
                      No registered versions
                    </td>
                  </tr>
                )}
              </tbody>
            </table>
          </div>
        </section>

        <section className="space-y-2">
          <h2 className="text-sm font-medium text-zinc-300">Promotion requests</h2>
          <div className="space-y-2">
            {requests.map((r) => (
              <div
                key={r.id}
                className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900/50 px-3 py-2 text-sm"
              >
                <div className="flex flex-col">
                  <span className="text-zinc-200">
                    v{r.candidate_version}
                    {r.current_prod_version ? ` (replacing v${r.current_prod_version})` : ''}
                  </span>
                  <span className="text-xs text-zinc-500">
                    by {r.requested_by} · {new Date(r.created_at).toLocaleString()}
                    {r.review_comment ? ` · "${r.review_comment}"` : ''}
                  </span>
                </div>
                <span className={`rounded border px-2 py-0.5 text-[10px] uppercase ${STATUS_STYLE[r.status]}`}>
                  {r.status}
                </span>
              </div>
            ))}
            {!requests.length && <p className="text-sm text-zinc-500">No requests yet.</p>}
          </div>
        </section>
      </div>
    </div>
  )
}
