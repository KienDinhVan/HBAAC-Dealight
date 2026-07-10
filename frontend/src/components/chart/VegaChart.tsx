import { useEffect, useRef, useCallback, useState, useMemo } from 'react'
import type { Result } from 'vega-embed'
import { GripHorizontal, AlertCircle, Loader2 } from 'lucide-react'

interface VegaChartProps {
  spec: Record<string, unknown>
}

const MIN_HEIGHT = 200
const DEFAULT_HEIGHT = 420

export function VegaChart({ spec }: VegaChartProps) {
  const containerRef = useRef<HTMLDivElement>(null)
  const wrapperRef = useRef<HTMLDivElement>(null)
  const viewRef = useRef<Result | null>(null)
  const heightRef = useRef(DEFAULT_HEIGHT)
  const [status, setStatus] = useState<'loading' | 'ready' | 'error'>('loading')
  const [errorMsg, setErrorMsg] = useState<string>('')

  // Fix 1: Stable dependency — only re-embed when spec *content* changes,
  // not on every re-render that produces a new object reference.
  const specKey = useMemo(() => JSON.stringify(spec), [spec])

  useEffect(() => {
    if (!containerRef.current) return
    setStatus('loading')
    setErrorMsg('')

    // Parse back from the stable key so we always use a consistent snapshot
    const parsedSpec = JSON.parse(specKey) as Record<string, unknown>

    const embedSpec = {
      ...parsedSpec,
      width: 'container' as const,
      // Don't override height — let Vega use the agent spec's height.
      // Overriding with a fixed number sets only the *plot area*; the full
      // SVG (plot + axis labels + titles) is larger and overflows, causing
      // the top to be clipped by overflow-hidden.
      autosize: { type: 'fit-x', contains: 'padding' } as const,
      background: 'transparent',
      config: {
        ...(typeof parsedSpec.config === 'object' && parsedSpec.config !== null
          ? parsedSpec.config
          : {}),
        axis: {
          labelColor: '#a1a1aa',
          titleColor: '#a1a1aa',
          gridColor: '#3f3f46',
          domainColor: '#52525b',
          // Keeps the rotated axis title from overlapping tick labels
          titlePadding: 10,
          labelLimit: 120,
        },
        legend: { labelColor: '#a1a1aa', titleColor: '#a1a1aa' },
        title: { color: '#f4f4f5' },
        view: { stroke: 'transparent' },
      },
    }

    let cancelled = false

    import('vega-embed')
      .then(({ default: vegaEmbed }) => {
        if (cancelled || !containerRef.current) return
        viewRef.current?.finalize()
        vegaEmbed(containerRef.current, embedSpec as never, {
          actions: false,
          renderer: 'svg',
          theme: 'dark',
        })
          .then(result => {
            if (cancelled) {
              result.finalize()
            } else {
              viewRef.current = result
              setStatus('ready')

              // Fix 2: Correct width if the container was zero-width on initial paint
              const w = containerRef.current?.clientWidth ?? 0
              if (w > 0) result.view.width(w).run()
            }
          })
          .catch((err: unknown) => {
            if (!cancelled) {
              setStatus('error')
              setErrorMsg(
                err instanceof Error ? err.message : 'Invalid chart specification',
              )
            }
          })
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setStatus('error')
          setErrorMsg(
            err instanceof Error ? err.message : 'Failed to load chart library',
          )
        }
      })

    return () => {
      cancelled = true
      viewRef.current?.finalize()
      viewRef.current = null
    }
  }, [specKey]) // ← stable string, not the object reference

  // Fix 2 (continued): ResizeObserver keeps chart width correct whenever the
  // container resizes (e.g. panel opens, sidebar collapses, initial zero-width).
  useEffect(() => {
    const el = containerRef.current
    if (!el) return

    const observer = new ResizeObserver(entries => {
      for (const entry of entries) {
        const w = entry.contentRect.width
        if (w > 0 && viewRef.current?.view) {
          viewRef.current.view.width(w).run()
        }
      }
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])

  // Drag-to-resize handle (unchanged logic, just wired up to new refs)
  const onMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    const startY = e.clientY
    const startH = heightRef.current

    const onMove = (ev: MouseEvent) => {
      const newH = Math.max(MIN_HEIGHT, startH + ev.clientY - startY)
      heightRef.current = newH
      if (wrapperRef.current) {
        wrapperRef.current.style.height = `${newH}px`
      }
      // Width is handled by the ResizeObserver; we no longer control
      // height directly since autosize:fit-x lets Vega own that.
      if (viewRef.current?.view) {
        viewRef.current.view
          .width(containerRef.current?.clientWidth ?? 600)
          .run()
      }
    }

    const onUp = () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }

    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
  }, [])

  return (
    <div
      ref={wrapperRef}
      className="mt-3 rounded-xl border border-zinc-700/40 bg-zinc-950 flex flex-col"
      style={{ height: DEFAULT_HEIGHT }}
    >
      {/* Loading / error states with visual feedback */}
      <div className="flex-1 min-h-0 relative overflow-hidden">
        {/* Chart node — kept in normal flow (h-full w-full) so clientWidth resolves
            correctly when Vega initialises with width: 'container'. */}
        <div
          ref={containerRef}
          className="h-full w-full px-3 pt-3 overflow-hidden"
          style={{ visibility: status === 'error' ? 'hidden' : 'visible' }}
        />

        {/* Loading spinner — absolute overlay, pointer-events-none so it doesn't
            block the chart if Vega renders faster than the state update. */}
        {status === 'loading' && (
          <div className="absolute inset-0 flex items-center justify-center pointer-events-none">
            <Loader2 className="h-5 w-5 text-zinc-500 animate-spin" />
          </div>
        )}

        {/* Error state */}
        {status === 'error' && (
          <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-4 text-center">
            <AlertCircle className="h-5 w-5 text-red-400" />
            <p className="text-xs text-zinc-400">Failed to render chart</p>
            {errorMsg && (
              <p className="text-xs text-zinc-600 font-mono break-all">{errorMsg}</p>
            )}
          </div>
        )}
      </div>

      {/* Drag handle */}
      <div
        onMouseDown={onMouseDown}
        className="flex-shrink-0 flex items-center justify-center h-5 cursor-ns-resize
                   border-t border-zinc-700/40 hover:bg-zinc-800/60 transition-colors group"
        title="Drag to resize"
      >
        <GripHorizontal className="h-3 w-3 text-zinc-600 group-hover:text-zinc-400 transition-colors" />
      </div>
    </div>
  )
}
