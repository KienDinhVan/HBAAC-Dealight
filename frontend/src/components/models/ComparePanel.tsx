import type { ModelCompare } from '@/lib/api'

const METRICS: { key: string; label: string }[] = [
  { key: 'lightgbm_wape', label: 'WAPE' },
  { key: 'lightgbm_mae', label: 'MAE' },
  { key: 'lightgbm_rmse', label: 'RMSE' },
  { key: 'lightgbm_smape', label: 'sMAPE' },
]

export function ComparePanel({ compare }: { compare: ModelCompare }) {
  const { candidate, production } = compare
  return (
    <div className="overflow-x-auto rounded-lg border border-zinc-800">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b border-zinc-800 text-left text-xs uppercase text-zinc-500">
            <th className="px-3 py-2">Metric</th>
            <th className="px-3 py-2">Production {production ? `v${production.version}` : '—'}</th>
            <th className="px-3 py-2">Candidate v{candidate.version}</th>
            <th className="px-3 py-2">Δ</th>
          </tr>
        </thead>
        <tbody>
          {METRICS.map(({ key, label }) => {
            const prod = production?.metrics[key]
            const cand = candidate.metrics[key]
            const delta = prod !== undefined && cand !== undefined ? cand - prod : null
            const better = delta !== null && delta < 0
            return (
              <tr key={key} className="border-b border-zinc-800/60 last:border-0">
                <td className="px-3 py-2 font-medium text-zinc-300">{label}</td>
                <td className="px-3 py-2 text-zinc-400">{prod?.toFixed(4) ?? '—'}</td>
                <td className="px-3 py-2 text-zinc-100">{cand?.toFixed(4) ?? '—'}</td>
                <td
                  className={`px-3 py-2 font-medium ${
                    delta === null ? 'text-zinc-600' : better ? 'text-emerald-400' : 'text-red-400'
                  }`}
                >
                  {delta === null ? '—' : `${delta > 0 ? '+' : ''}${delta.toFixed(4)}`}
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
