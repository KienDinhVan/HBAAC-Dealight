import { BarChart3, FileUp, Sparkles, ShieldAlert, TrendingUp, Workflow } from 'lucide-react'

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
}

export function Sidebar({ current, onChange }: Props) {
  return (
    <aside className="flex w-60 flex-shrink-0 flex-col border-r border-zinc-800 bg-zinc-950/95 backdrop-blur">
      <div className="flex h-14 items-center gap-2.5 border-b border-zinc-800 px-4">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg border border-emerald-500/40 bg-emerald-600/20">
          <TrendingUp className="h-4 w-4 text-emerald-400" />
        </div>
        <div className="flex flex-col leading-tight">
          <span className="text-sm font-semibold text-zinc-100">HBAAC Workspace</span>
          <span className="text-[10px] text-zinc-500">Dealight Analytics</span>
        </div>
      </div>

      <nav className="flex flex-1 flex-col gap-1 p-2">
        {ITEMS.map(({ id, label, Icon, hint }) => {
          const active = id === current
          return (
            <button
              key={id}
              onClick={() => onChange(id)}
              className={`group flex items-start gap-3 rounded-xl px-3 py-2.5 text-left transition-colors ${
                active
                  ? 'bg-emerald-600/15 text-emerald-200 border border-emerald-500/30'
                  : 'text-zinc-300 hover:bg-zinc-900/80'
              }`}
            >
              <Icon
                className={`h-4 w-4 mt-0.5 flex-shrink-0 ${active ? 'text-emerald-300' : 'text-zinc-500 group-hover:text-zinc-300'}`}
              />
              <div className="flex flex-col leading-tight">
                <span className="text-sm font-medium">{label}</span>
                <span className="text-[11px] text-zinc-500">{hint}</span>
              </div>
            </button>
          )
        })}
      </nav>

      <div className="border-t border-zinc-800 p-3 text-[10px] text-zinc-500">
        Backend: <span className="text-zinc-300">FastAPI</span> · Agent:{' '}
        <span className="text-zinc-300">OpenRouter</span>
      </div>
    </aside>
  )
}
