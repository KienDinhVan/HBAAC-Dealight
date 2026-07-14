import { BarChart3, Database, FileUp, Sparkles, ShieldAlert, TrendingUp, Workflow } from 'lucide-react'
import type { DatasetConfig } from '@/lib/api'

export type WorkspacePage = 'dashboard' | 'predict' | 'pipeline' | 'drift' | 'chat'

interface NavItem {
  id: WorkspacePage
  label: string
  Icon: typeof BarChart3
  hint: string
}

const ITEMS: NavItem[] = [
  { id: 'dashboard', label: 'Dashboard', Icon: BarChart3, hint: 'Latest forecast run + summaries' },
  { id: 'predict', label: 'Predict CSV', Icon: FileUp, hint: 'Import CSV → predictions + chart' },
  { id: 'pipeline', label: 'Data Pipeline', Icon: Workflow, hint: 'Upload → GCS → BigQuery → Redis' },
  { id: 'drift', label: 'Drift & Retrain', Icon: ShieldAlert, hint: 'Drift reports + retrain DAG' },
  { id: 'chat', label: 'Agent Chat', Icon: Sparkles, hint: 'Multi-agent ReAct workspace' },
]

interface Props {
  current: WorkspacePage
  onChange: (p: WorkspacePage) => void
  datasets: DatasetConfig[]
  selectedDataset: string
  onDatasetChange: (dataset: string) => void
  datasetError: string | null
}

export function Sidebar({
  current,
  onChange,
  datasets,
  selectedDataset,
  onDatasetChange,
  datasetError,
}: Props) {
  return (
    <aside className="flex w-full flex-shrink-0 flex-col border-b border-zinc-800 bg-zinc-950/95 md:h-full md:w-60 md:border-b-0 md:border-r">
      <div className="flex h-12 items-center gap-2.5 border-b border-zinc-800 px-3 md:h-14 md:px-4">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-emerald-500/40 bg-emerald-600/20">
          <TrendingUp className="h-4 w-4 text-emerald-400" />
        </div>
        <div className="flex flex-col leading-tight">
          <span className="text-sm font-semibold text-zinc-100">Dealight Platform</span>
          <span className="text-[10px] text-zinc-500">Multi-dataset forecasting</span>
        </div>
      </div>

      <div className="border-b border-zinc-800 px-3 py-2.5">
        <label className="mb-1 flex items-center justify-between text-[10px] font-medium uppercase text-zinc-500">
          <span className="flex items-center gap-1.5">
            <Database className="h-3 w-3" /> Dataset
          </span>
          <span>{datasets.length} registered</span>
        </label>
        <select
          value={selectedDataset}
          onChange={(event) => onDatasetChange(event.target.value)}
          disabled={!datasets.length}
          title={datasetError ?? 'Active dataset'}
          className={`h-8 w-full rounded-md border bg-zinc-900 px-2 text-xs font-medium outline-none ${
            datasetError
              ? 'border-red-700 text-red-300'
              : 'border-zinc-700 text-zinc-200 focus:border-emerald-500'
          }`}
        >
          {!datasets.length && <option value={selectedDataset}>Loading registry...</option>}
          {datasets.map((dataset) => (
            <option key={dataset.name} value={dataset.name}>
              {dataset.name}
            </option>
          ))}
        </select>
      </div>

      <nav className="flex flex-row gap-1 overflow-x-auto p-2 md:flex-1 md:flex-col md:overflow-visible">
        {ITEMS.map(({ id, label, Icon, hint }) => {
          const active = id === current
          return (
            <button
              key={id}
              onClick={() => onChange(id)}
              className={`group flex min-w-fit items-start gap-2 rounded-md border px-2.5 py-2 text-left transition-colors md:w-full md:gap-3 md:px-3 md:py-2.5 ${
                active
                  ? 'border-emerald-500/30 bg-emerald-600/15 text-emerald-200'
                  : 'border-transparent text-zinc-300 hover:bg-zinc-900/80'
              }`}
            >
              <Icon
                className={`h-4 w-4 mt-0.5 flex-shrink-0 ${active ? 'text-emerald-300' : 'text-zinc-500 group-hover:text-zinc-300'}`}
              />
              <div className="flex flex-col leading-tight">
                <span className="text-sm font-medium">{label}</span>
                <span className="hidden text-[11px] text-zinc-500 md:block">{hint}</span>
              </div>
            </button>
          )
        })}
      </nav>

      <div className="hidden border-t border-zinc-800 p-3 text-[10px] text-zinc-500 md:block">
        Backend: <span className="text-zinc-300">FastAPI</span> · Agent:{' '}
        <span className="text-zinc-300">OpenRouter</span>
      </div>
    </aside>
  )
}
