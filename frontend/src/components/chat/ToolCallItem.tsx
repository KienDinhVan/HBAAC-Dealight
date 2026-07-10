import { useState } from 'react'
import { CheckCircle2, Loader2, Wrench, ChevronDown, ChevronRight, ShieldCheck, ShieldX, Ban } from 'lucide-react'
import type { ToolCall } from '@/types/events'
import { submitApproval } from '@/lib/api'
import { cn } from '@/lib/utils'

interface ToolCallItemProps {
  tool: ToolCall
}

export function ToolCallItem({ tool }: ToolCallItemProps) {
  const [expanded, setExpanded] = useState(false)
  const [deciding, setDeciding] = useState(false)
  const [comment, setComment] = useState('')
  const done = tool.status === 'done'
  const isPending = tool.status === 'pending_approval'
  const isDenied = tool.status === 'denied'
  const hasInput = Object.keys(tool.input).length > 0

  async function handleDecision(approved: boolean) {
    if (!tool.approval_id || deciding) return
    setDeciding(true)
    await submitApproval(tool.approval_id, approved, comment || undefined)
    // Status will be updated by the incoming SSE (tool_start or tool_denied)
  }

  return (
    <div className={cn('rounded-md text-xs transition-opacity', done ? 'opacity-60' : '')}>
      {/* ── Row: icon / name / chevron / status ── */}
      <div
        className={cn(
          'flex items-center gap-2 px-2 py-1 rounded-md',
          hasInput && !isPending && 'cursor-pointer hover:bg-white/5',
        )}
        onClick={() => hasInput && !isPending && setExpanded(e => !e)}
      >
        <Wrench className="h-3 w-3 flex-shrink-0 text-zinc-500" />
        <span className="font-mono text-zinc-300 flex-1 truncate">{tool.name}</span>

        {hasInput && !isPending && (
          <span className="text-zinc-600">
            {expanded
              ? <ChevronDown className="h-3 w-3" />
              : <ChevronRight className="h-3 w-3" />}
          </span>
        )}

        {done && <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 flex-shrink-0" />}
        {tool.status === 'running' && <Loader2 className="h-3.5 w-3.5 text-blue-400 animate-spin flex-shrink-0" />}
        {isDenied && <Ban className="h-3.5 w-3.5 text-red-400 flex-shrink-0" />}
        {isPending && !deciding && (
          <span className="text-amber-400 flex-shrink-0 animate-pulse text-[10px] font-semibold uppercase tracking-wide">
            approval needed
          </span>
        )}
        {isPending && deciding && (
          <Loader2 className="h-3.5 w-3.5 text-amber-400 animate-spin flex-shrink-0" />
        )}
      </div>

      {/* ── Approval buttons (shown when awaiting decision) ── */}
      {isPending && (
        <div className="mx-2 mb-2 mt-1">
          {/* Show inputs so the user can review before deciding */}
          {hasInput && (
            <div className="mb-2 rounded bg-black/30 border border-zinc-800 overflow-hidden">
              {Object.entries(tool.input).map(([key, value]) => (
                <div key={key} className="flex gap-2 px-2 py-1 border-b border-zinc-800/60 last:border-0">
                  <span className="font-mono text-indigo-400 flex-shrink-0 w-24 truncate">{key}</span>
                  <span className="font-mono text-zinc-300 break-all whitespace-pre-wrap leading-relaxed">
                    {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
                  </span>
                </div>
              ))}
            </div>
          )}
          {/* Comment textarea */}
          <textarea
            disabled={deciding}
            placeholder="Optional comment for the agent…"
            value={comment}
            onChange={e => setComment(e.target.value)}
            rows={2}
            className={cn(
              'w-full mb-2 px-2 py-1.5 rounded-md text-xs font-mono resize-none',
              'bg-black/30 border border-zinc-700 text-zinc-300 placeholder-zinc-600',
              'focus:outline-none focus:border-zinc-500',
              'disabled:opacity-50 disabled:cursor-not-allowed',
            )}
          />
          <div className="flex gap-2">
            <button
              disabled={deciding}
              onClick={() => handleDecision(true)}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-semibold transition-colors',
                'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40',
                'hover:bg-emerald-500/30 disabled:opacity-50 disabled:cursor-not-allowed',
              )}
            >
              <ShieldCheck className="h-3.5 w-3.5" />
              Approve
            </button>
            <button
              disabled={deciding}
              onClick={() => handleDecision(false)}
              className={cn(
                'flex items-center gap-1.5 px-3 py-1 rounded-md text-xs font-semibold transition-colors',
                'bg-red-500/20 text-red-300 border border-red-500/40',
                'hover:bg-red-500/30 disabled:opacity-50 disabled:cursor-not-allowed',
              )}
            >
              <ShieldX className="h-3.5 w-3.5" />
              Deny
            </button>
          </div>
        </div>
      )}

      {/* ── Expandable: argument key-value pairs (for non-pending tools) ── */}
      {expanded && hasInput && !isPending && (
        <div className="mx-2 mb-1 rounded bg-black/30 border border-zinc-800 overflow-hidden">
          {Object.entries(tool.input).map(([key, value]) => (
            <div key={key} className="flex gap-2 px-2 py-1 border-b border-zinc-800/60 last:border-0">
              <span className="font-mono text-indigo-400 flex-shrink-0 w-24 truncate">{key}</span>
              <span className="font-mono text-zinc-300 break-all whitespace-pre-wrap leading-relaxed">
                {typeof value === 'string' ? value : JSON.stringify(value, null, 2)}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

