import { useEffect, useMemo, useState } from 'react'
import {
  RefreshCcw,
  AlertTriangle,
  Activity,
  TrendingUp,
  Boxes,
  CalendarDays,
  ShieldAlert,
  ShieldCheck,
  PackageX,
  Layers,
  Brain,
} from 'lucide-react'
import { Button } from '@/components/ui/button'
import { VegaChart } from '@/components/chart/VegaChart'
import {
  fetchLatestRun,
  fetchSummary,
  fetchTopSkus,
  fetchMonitoringLatest,
  type ForecastRun,
  type ForecastSummary,
  type TopSku,
  type MonitoringReport,
} from '@/lib/api'

const TREND_DAYS = 14

function nextDay(iso: string, offset = 1): string {
  const d = new Date(iso)
  d.setUTCDate(d.getUTCDate() + offset)
  return d.toISOString().slice(0, 10)
}

interface TrendPoint {
  date: string
  total: number
  avg: number
}

export default function DashboardPage() {
  const [run, setRun] = useState<ForecastRun | null>(null)
  const [summary, setSummary] = useState<ForecastSummary | null>(null)
  const [topSkus, setTopSkus] = useState<TopSku[]>([])
  const [trend, setTrend] = useState<TrendPoint[]>([])
  const [monitoring, setMonitoring] = useState<MonitoringReport | null>(null)
  const [targetDate, setTargetDate] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)

  async function load() {
    setLoading(true)
    setError(null)
    try {
      const latest = await fetchLatestRun()
      if (!latest) {
        setRun(null)
        setSummary(null)
        setTopSkus([])
        setTrend([])
        setMonitoring(null)
        return
      }
      setRun(latest)
      const td = targetDate || nextDay(latest.forecast_date)
      setTargetDate(td)

      const [s, top, mon] = await Promise.all([
        fetchSummary(td),
        fetchTopSkus(td, 20),
        fetchMonitoringLatest().catch(() => null),
      ])
      setSummary(s)
      setTopSkus(top.items)
      setMonitoring(mon)

      const dates = Array.from({ length: TREND_DAYS }, (_, i) => nextDay(td, i))
      const summaries = await Promise.all(
        dates.map(async (d) => {
          try {
            const r = await fetchSummary(d)
            return { date: d, total: r.total_predicted_quantity, avg: r.avg_predicted_quantity }
          } catch {
            return null
          }
        })
      )
      setTrend(summaries.filter((p): p is TrendPoint => p !== null))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const topSkuChart = useMemo(() => {
    if (!topSkus.length) return null
    return {
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      description: `Top SKUs predicted for ${targetDate}`,
      mark: { type: 'bar', color: '#34d399' },
      encoding: {
        y: { field: 'item_code', type: 'nominal', sort: '-x', title: 'SKU' },
        x: { field: 'predicted_quantity', type: 'quantitative', title: 'Predicted qty' },
        tooltip: [
          { field: 'item_code', type: 'nominal' },
          { field: 'predicted_quantity', type: 'quantitative', format: '.2f' },
        ],
      },
      data: { values: topSkus },
      height: { step: 16 },
      width: 'container' as const,
    }
  }, [topSkus, targetDate])

  const trendChart = useMemo(() => {
    if (!trend.length) return null
    return {
      $schema: 'https://vega.github.io/schema/vega-lite/v5.json',
      description: `Daily aggregate forecast — next ${TREND_DAYS} days`,
      mark: { type: 'area', color: '#22d3ee', opacity: 0.45, line: { color: '#22d3ee' } },
      encoding: {
        x: { field: 'date', type: 'temporal', title: 'Forecast date' },
        y: { field: 'total', type: 'quantitative', title: 'Total predicted qty' },
        tooltip: [
          { field: 'date', type: 'temporal' },
          { field: 'total', type: 'quantitative', format: '.1f' },
          { field: 'avg', type: 'quantitative', format: '.3f', title: 'Avg / SKU' },
        ],
      },
      data: { values: trend },
      height: 200,
      width: 'container' as const,
    }
  }, [trend])

  const psiBars = useMemo(() => {
    const psi = monitoring?.drift_metrics?.data?.psi
    if (!psi) return []
    const threshold = monitoring?.drift_metrics?.data?.threshold ?? 0.2
    return Object.entries(psi)
      .sort(([, a], [, b]) => b - a)
      .map(([feature, value]) => ({ feature, value, isDrift: value >= threshold, threshold }))
  }, [monitoring])

  const driftedColumns = monitoring?.drift_metrics?.data?.drifted_columns ?? []
  const driftDetected = monitoring?.drift_detected ?? false
  const overstockAlerts = topSkus.slice(0, 5)
  const zeroForecastShare = monitoring?.zero_ratio ?? null

  return (
    <div className="flex h-full flex-col overflow-y-auto bg-zinc-950 p-6">
      <div className="mb-5 flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-zinc-100">
            HBAAC-Dealight Forecast Dashboard
          </h1>
          <p className="mt-0.5 text-xs text-zinc-500">
            Sales · Inventory · Forecast · Alerts — auto-refresh on demand
          </p>
        </div>
        <div className="flex items-center gap-2">
          <label className="text-xs text-zinc-400">Target date</label>
          <input
            type="date"
            value={targetDate}
            onChange={(e) => setTargetDate(e.target.value)}
            className="rounded-md border border-zinc-700 bg-zinc-900 px-2 py-1 text-sm text-zinc-200 focus:border-emerald-500 focus:outline-none"
          />
          <Button onClick={load} disabled={loading} size="sm">
            <RefreshCcw className={`mr-2 h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            Reload
          </Button>
        </div>
      </div>

      {error && (
        <div className="mb-4 flex items-center gap-2 rounded-xl border border-red-700/40 bg-red-950/40 px-3 py-2 text-sm text-red-300">
          <AlertTriangle className="h-4 w-4" />
          <span>{error}</span>
        </div>
      )}

      {!error && !loading && !run && (
        <div className="mb-4 flex items-center gap-2 rounded-md border border-amber-700/40 bg-amber-950/30 px-3 py-2 text-sm text-amber-200">
          <AlertTriangle className="h-4 w-4 shrink-0" />
          <span>No successful forecast yet. Run training and batch forecast to populate the dashboard.</span>
        </div>
      )}

      <div className="mb-5 grid grid-cols-2 gap-3 md:grid-cols-5">
        <KpiCard
          icon={<Brain className="h-4 w-4 text-emerald-400" />}
          label="Model"
          value={run ? `${run.model_name}` : '—'}
          sub={run ? `v ${run.model_version}` : ''}
          tone="emerald"
        />
        <KpiCard
          icon={<CalendarDays className="h-4 w-4 text-sky-400" />}
          label="Latest run"
          value={run ? run.forecast_date : '—'}
          sub={run ? run.status : ''}
          tone="sky"
        />
        <KpiCard
          icon={<TrendingUp className="h-4 w-4 text-cyan-400" />}
          label="Total predicted"
          value={summary ? summary.total_predicted_quantity.toFixed(1) : '—'}
          sub={summary ? `${targetDate} — ${summary.sku_count.toLocaleString()} SKU` : ''}
          tone="cyan"
        />
        <KpiCard
          icon={
            driftDetected ? (
              <ShieldAlert className="h-4 w-4 text-red-400" />
            ) : (
              <ShieldCheck className="h-4 w-4 text-emerald-400" />
            )
          }
          label="Drift status"
          value={driftDetected ? 'Drift detected' : 'Stable'}
          sub={
            driftedColumns.length
              ? `${driftedColumns.length} feature(s)`
              : monitoring
                ? 'PSI within threshold'
                : ''
          }
          tone={driftDetected ? 'red' : 'emerald'}
        />
        <KpiCard
          icon={<Boxes className="h-4 w-4 text-amber-400" />}
          label="Inactive SKUs"
          value={
            zeroForecastShare !== null ? `${(zeroForecastShare * 100).toFixed(1)}%` : '—'
          }
          sub={monitoring ? 'zero-forecast share' : ''}
          tone="amber"
        />
      </div>

      <div className="mb-5 grid grid-cols-1 gap-4 lg:grid-cols-2">
        <Panel
          title={`Daily forecast trend — next ${TREND_DAYS} days`}
          icon={<Activity className="h-4 w-4 text-cyan-400" />}
        >
          {trendChart ? <VegaChart spec={trendChart} /> : <SkeletonBox h={200} />}
        </Panel>

        <Panel
          title={`Top 20 SKUs by predicted demand @ ${targetDate || '—'}`}
          icon={<Layers className="h-4 w-4 text-emerald-400" />}
        >
          {topSkuChart ? <VegaChart spec={topSkuChart} /> : <SkeletonBox h={320} />}
        </Panel>
      </div>

      <div className="mb-5 grid grid-cols-1 gap-4 lg:grid-cols-3">
        <Panel
          title="Drift PSI by feature"
          icon={<ShieldAlert className="h-4 w-4 text-red-400" />}
          className="lg:col-span-2"
        >
          {psiBars.length ? (
            <div className="space-y-1.5">
              {psiBars.map((b) => (
                <PsiBar key={b.feature} {...b} />
              ))}
              <div className="mt-2 text-[10px] text-zinc-500">
                PSI ≥ {psiBars[0]?.threshold} indicates significant distribution shift.
              </div>
            </div>
          ) : (
            <div className="text-xs text-zinc-500">No PSI data available yet.</div>
          )}
        </Panel>

        <Panel
          title="System alerts"
          icon={
            driftDetected ? (
              <ShieldAlert className="h-4 w-4 text-red-400" />
            ) : (
              <ShieldCheck className="h-4 w-4 text-emerald-400" />
            )
          }
        >
          <AlertsFeed monitoring={monitoring} />
        </Panel>
      </div>

      <Panel
        title="Inventory prep watchlist — top demand for next planning cycle"
        icon={<PackageX className="h-4 w-4 text-amber-400" />}
      >
        {overstockAlerts.length ? (
          <div className="overflow-hidden rounded-lg border border-zinc-800">
            <table className="w-full text-xs">
              <thead className="bg-zinc-900/60 text-left text-zinc-400">
                <tr>
                  <th className="px-3 py-2">SKU</th>
                  <th className="px-3 py-2">Target date</th>
                  <th className="px-3 py-2">Horizon</th>
                  <th className="px-3 py-2 text-right">Predicted qty</th>
                  <th className="px-3 py-2">Recommendation</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-zinc-800">
                {overstockAlerts.map((s) => (
                  <tr key={s.item_code} className="hover:bg-zinc-900/40">
                    <td className="px-3 py-2 font-mono text-zinc-200">{s.item_code}</td>
                    <td className="px-3 py-2 text-zinc-400">{s.target_date}</td>
                    <td className="px-3 py-2 text-zinc-400">D+{s.horizon}</td>
                    <td className="px-3 py-2 text-right text-emerald-300">
                      {s.predicted_quantity.toFixed(2)}
                    </td>
                    <td className="px-3 py-2 text-zinc-300">
                      {s.predicted_quantity > 20
                        ? 'Prepare stock — high demand'
                        : s.predicted_quantity > 5
                          ? 'Confirm inventory'
                          : 'Watch'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <SkeletonBox h={120} />
        )}
        <div className="mt-2 text-[10px] text-zinc-500">
          For stockout-risk detection on historically-active SKUs, ask the AI Chat:
          <span className="ml-1 text-emerald-400">
            "Which SKUs are at risk of stockout in the next 28 days?"
          </span>
        </div>
      </Panel>
    </div>
  )
}

interface KpiCardProps {
  icon: React.ReactNode
  label: string
  value: string
  sub?: string
  tone: 'emerald' | 'sky' | 'cyan' | 'amber' | 'red'
}

const TONE_CLASS: Record<KpiCardProps['tone'], string> = {
  emerald: 'border-emerald-700/40 bg-emerald-950/20',
  sky: 'border-sky-700/40 bg-sky-950/20',
  cyan: 'border-cyan-700/40 bg-cyan-950/20',
  amber: 'border-amber-700/40 bg-amber-950/20',
  red: 'border-red-700/40 bg-red-950/30',
}

function KpiCard({ icon, label, value, sub, tone }: KpiCardProps) {
  return (
    <div className={`rounded-xl border p-3 ${TONE_CLASS[tone]}`}>
      <div className="mb-1 flex items-center gap-1.5 text-[11px] uppercase tracking-wider text-zinc-400">
        {icon}
        {label}
      </div>
      <div className="truncate text-base font-semibold text-zinc-100" title={value}>
        {value}
      </div>
      {sub && <div className="mt-0.5 truncate text-[11px] text-zinc-500">{sub}</div>}
    </div>
  )
}

function Panel({
  title,
  icon,
  children,
  className,
}: {
  title: string
  icon?: React.ReactNode
  children: React.ReactNode
  className?: string
}) {
  return (
    <div className={`rounded-xl border border-zinc-800 bg-zinc-900/40 p-4 ${className ?? ''}`}>
      <div className="mb-3 flex items-center gap-2 text-xs font-medium text-zinc-300">
        {icon}
        {title}
      </div>
      {children}
    </div>
  )
}

function PsiBar({
  feature,
  value,
  threshold,
  isDrift,
}: {
  feature: string
  value: number
  threshold: number
  isDrift: boolean
}) {
  const scale = Math.max(threshold * 3, value)
  const widthPct = Math.min(100, (value / scale) * 100)
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-32 truncate font-mono text-zinc-400" title={feature}>
        {feature}
      </span>
      <div className="relative h-3 flex-1 overflow-hidden rounded bg-zinc-800">
        <div
          className={`h-full ${isDrift ? 'bg-red-500/70' : 'bg-emerald-500/60'}`}
          style={{ width: `${widthPct}%` }}
        />
        <div
          className="absolute top-0 h-full w-px bg-amber-400/80"
          style={{ left: `${Math.min(100, (threshold / scale) * 100)}%` }}
          title={`threshold ${threshold}`}
        />
      </div>
      <span
        className={`w-14 text-right font-mono tabular-nums ${isDrift ? 'text-red-300' : 'text-emerald-300'}`}
      >
        {value.toFixed(3)}
      </span>
    </div>
  )
}

function AlertsFeed({ monitoring }: { monitoring: MonitoringReport | null }) {
  if (!monitoring) return <SkeletonBox h={120} />
  const items: { tone: 'red' | 'amber' | 'emerald'; text: string }[] = []

  if (monitoring.drift_detected) {
    const cols = monitoring.drift_metrics?.data?.drifted_columns ?? []
    items.push({
      tone: 'red',
      text: cols.length
        ? `Data drift on ${cols.length} feature(s): ${cols.join(', ')}`
        : 'Data or prediction drift detected',
    })
  }
  if (monitoring.missing_sku_count > 0) {
    items.push({ tone: 'amber', text: `${monitoring.missing_sku_count} SKU(s) missing in forecast` })
  }
  if (monitoring.negative_prediction_count > 0) {
    items.push({
      tone: 'amber',
      text: `${monitoring.negative_prediction_count} negative prediction(s) — investigate`,
    })
  }
  if (monitoring.zero_ratio >= 0.95) {
    items.push({
      tone: 'amber',
      text: `${(monitoring.zero_ratio * 100).toFixed(1)}% zero-forecast share — model conservative on inactive SKUs`,
    })
  }
  monitoring.alerts.forEach((a) => {
    if (!items.find((i) => i.text.includes(a))) items.push({ tone: 'red', text: a })
  })
  if (items.length === 0) {
    items.push({ tone: 'emerald', text: 'All systems nominal — no active alerts.' })
  }

  const toneClass = {
    red: 'border-red-700/40 bg-red-950/30 text-red-200',
    amber: 'border-amber-700/40 bg-amber-950/30 text-amber-200',
    emerald: 'border-emerald-700/40 bg-emerald-950/30 text-emerald-200',
  }

  return (
    <ul className="space-y-1.5">
      {items.map((it, i) => (
        <li
          key={i}
          className={`flex items-start gap-2 rounded-md border px-2 py-1.5 text-xs ${toneClass[it.tone]}`}
        >
          {it.tone === 'emerald' ? (
            <ShieldCheck className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
          ) : (
            <AlertTriangle className="mt-0.5 h-3.5 w-3.5 flex-shrink-0" />
          )}
          <span>{it.text}</span>
        </li>
      ))}
    </ul>
  )
}

function SkeletonBox({ h }: { h: number }) {
  return (
    <div
      className="animate-pulse rounded-md bg-zinc-800/60"
      style={{ height: `${h}px` }}
    />
  )
}
