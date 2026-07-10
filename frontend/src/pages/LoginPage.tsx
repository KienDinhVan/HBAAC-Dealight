import { TrendingUp, Zap, ArrowRight } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface LoginPageProps {
  onLogin: () => void
}

const FEATURES = [
  'Sales performance & revenue analysis',
  'Demand forecasting with LightGBM AI model',
  'Stockout & overstock risk alerts',
  'Multi-agent AI collaboration',
]

export default function LoginPage({ onLogin }: LoginPageProps) {
  return (
    <div className="min-h-screen bg-zinc-950 flex items-center justify-center relative overflow-hidden">
      {/* Subtle dot-grid background */}
      <div
        className="absolute inset-0 opacity-[0.025]"
        style={{
          backgroundImage:
            'radial-gradient(circle, #fff 1px, transparent 1px)',
          backgroundSize: '28px 28px',
        }}
      />

      {/* Emerald radial glow */}
      <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
        <div className="h-[700px] w-[700px] rounded-full bg-emerald-700/15 blur-[140px]" />
      </div>

      {/* Card */}
      <div className="relative z-10 w-full max-w-sm mx-4">
        <div className="bg-zinc-900/80 backdrop-blur border border-zinc-800 rounded-2xl p-8 shadow-2xl shadow-black/60">
          {/* Logo */}
          <div className="flex justify-center mb-6">
            <div className="h-16 w-16 rounded-2xl bg-emerald-600/20 border border-emerald-500/40 flex items-center justify-center shadow-lg shadow-emerald-900/30">
              <TrendingUp className="h-8 w-8 text-emerald-400" />
            </div>
          </div>

          {/* Title */}
          <div className="text-center mb-8">
            <h1 className="text-2xl font-bold text-zinc-50 tracking-tight">Dealight Analytics</h1>
            <p className="text-zinc-400 text-sm mt-1.5 leading-relaxed">
              AI-powered retail sales & forecasting assistant
            </p>
          </div>

          {/* Feature list */}
          <ul className="space-y-2.5 mb-8">
            {FEATURES.map(f => (
              <li key={f} className="flex items-center gap-2.5 text-sm text-zinc-400">
                <Zap className="h-3.5 w-3.5 text-emerald-400 flex-shrink-0" />
                {f}
              </li>
            ))}
          </ul>

          {/* CTA */}
          <Button onClick={onLogin} className="w-full bg-emerald-600 hover:bg-emerald-500 text-white" size="lg">
            Enter Workspace
            <ArrowRight className="h-4 w-4" />
          </Button>

          <p className="text-center text-xs text-zinc-600 mt-4">
            Powered by DuckDB · LightGBM · LLM
          </p>
        </div>
      </div>
    </div>
  )
}
