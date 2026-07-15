import { useCallback, useEffect, useState } from 'react'
import { Check, ChevronDown, ChevronRight, X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { ComparePanel } from '@/components/models/ComparePanel'
import {
  fetchModelCompare,
  listPromotionRequests,
  reviewPromotionRequest,
  type ModelCompare,
  type PromotionRequest,
} from '@/lib/api'

export default function ApprovalsPage({ onDecided }: { onDecided: () => void }) {
  const [pending, setPending] = useState<PromotionRequest[]>([])
  const [history, setHistory] = useState<PromotionRequest[]>([])
  const [openId, setOpenId] = useState<number | null>(null)
  const [compare, setCompare] = useState<ModelCompare | null>(null)
  const [comment, setComment] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const reload = useCallback(() => {
    listPromotionRequests()
      .then(({ items }) => {
        setPending(items.filter((r) => r.status === 'pending'))
        setHistory(items.filter((r) => r.status !== 'pending'))
      })
      .catch((e: Error) => setError(e.message))
  }, [])

  useEffect(reload, [reload])

  const toggle = (r: PromotionRequest) => {
    if (openId === r.id) {
      setOpenId(null)
      return
    }
    setOpenId(r.id)
    setCompare(null)
    fetchModelCompare(r.dataset, r.candidate_version)
      .then(setCompare)
      .catch(() => setCompare(null))
  }

  const decide = async (id: number, action: 'approve' | 'reject') => {
    setBusy(true)
    setError(null)
    try {
      await reviewPromotionRequest(id, action, comment || undefined)
      setComment('')
      setOpenId(null)
      reload()
      onDecided()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Review failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="h-full overflow-y-auto p-4 md:p-6">
      <div className="mx-auto max-w-4xl space-y-6">
        <div>
          <h1 className="text-lg font-semibold text-zinc-100">Approvals</h1>
          <p className="text-sm text-zinc-500">Model promotion requests awaiting review</p>
        </div>

        {error && (
          <div className="rounded-md border border-red-800 bg-red-950/40 p-3 text-sm text-red-300">
            {error}
          </div>
        )}

        <section className="space-y-2">
          {pending.map((r) => (
            <div key={r.id} className="rounded-lg border border-zinc-800 bg-zinc-900/50">
              <button
                onClick={() => toggle(r)}
                className="flex w-full items-center justify-between px-3 py-2.5 text-left text-sm"
              >
                <div className="flex flex-col">
                  <span className="text-zinc-100">
                    {r.dataset}: v{r.candidate_version}
                    {r.current_prod_version
                      ? ` → replaces v${r.current_prod_version}`
                      : ' → first production'}
                  </span>
                  <span className="text-xs text-zinc-500">
                    by {r.requested_by} · {new Date(r.created_at).toLocaleString()}
                    {r.request_note ? ` · "${r.request_note}"` : ''}
                  </span>
                </div>
                {openId === r.id ? (
                  <ChevronDown className="h-4 w-4 text-zinc-500" />
                ) : (
                  <ChevronRight className="h-4 w-4 text-zinc-500" />
                )}
              </button>
              {openId === r.id && (
                <div className="space-y-3 border-t border-zinc-800 p-3">
                  {compare ? (
                    <ComparePanel compare={compare} />
                  ) : (
                    <p className="text-sm text-zinc-500">Loading live comparison…</p>
                  )}
                  <div className="flex items-center gap-2">
                    <input
                      value={comment}
                      onChange={(e) => setComment(e.target.value)}
                      placeholder="Comment (optional)"
                      className="h-9 flex-1 rounded-md border border-zinc-700 bg-zinc-900 px-3 text-sm text-zinc-100 outline-none focus:border-emerald-500"
                    />
                    <Button
                      onClick={() => decide(r.id, 'approve')}
                      disabled={busy}
                      className="bg-emerald-600 hover:bg-emerald-500 text-white"
                    >
                      <Check className="h-4 w-4" /> Approve
                    </Button>
                    <Button
                      onClick={() => decide(r.id, 'reject')}
                      disabled={busy}
                      className="bg-red-700 hover:bg-red-600 text-white"
                    >
                      <X className="h-4 w-4" /> Reject
                    </Button>
                  </div>
                </div>
              )}
            </div>
          ))}
          {!pending.length && <p className="text-sm text-zinc-500">No pending requests.</p>}
        </section>

        <section className="space-y-2">
          <h2 className="text-sm font-medium text-zinc-300">History</h2>
          {history.map((r) => (
            <div
              key={r.id}
              className="flex items-center justify-between rounded-lg border border-zinc-800 bg-zinc-900/30 px-3 py-2 text-sm"
            >
              <span className="text-zinc-300">
                {r.dataset}: v{r.candidate_version} · by {r.requested_by}
              </span>
              <span className="text-xs text-zinc-500">
                {r.status} by {r.reviewed_by}
                {r.review_comment ? ` — "${r.review_comment}"` : ''}
              </span>
            </div>
          ))}
          {!history.length && <p className="text-sm text-zinc-500">Nothing reviewed yet.</p>}
        </section>
      </div>
    </div>
  )
}
