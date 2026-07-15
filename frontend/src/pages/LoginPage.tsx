import { useState } from 'react'
import { TrendingUp, ArrowRight, Loader2 } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { login, type AuthUser } from '@/lib/api'

interface LoginPageProps {
  onLogin: (user: AuthUser) => void
}

export default function LoginPage({ onLogin }: LoginPageProps) {
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)

  const submit = async (e: React.FormEvent) => {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      onLogin(await login(username, password))
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed')
    } finally {
      setBusy(false)
    }
  }

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

          {/* Login form */}
          <form onSubmit={submit} className="space-y-3">
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              placeholder="Username"
              autoComplete="username"
              className="h-10 w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 text-sm text-zinc-100 outline-none focus:border-emerald-500"
            />
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="Password"
              autoComplete="current-password"
              className="h-10 w-full rounded-md border border-zinc-700 bg-zinc-900 px-3 text-sm text-zinc-100 outline-none focus:border-emerald-500"
            />
            {error && <p className="text-xs text-red-400">{error}</p>}
            <Button
              type="submit"
              disabled={busy || !username || !password}
              className="w-full bg-emerald-600 hover:bg-emerald-500 text-white"
              size="lg"
            >
              {busy ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <>
                  Sign in
                  <ArrowRight className="h-4 w-4" />
                </>
              )}
            </Button>
          </form>

          <p className="text-center text-xs text-zinc-600 mt-4">
            Powered by DuckDB · LightGBM · LLM
          </p>
        </div>
      </div>
    </div>
  )
}
