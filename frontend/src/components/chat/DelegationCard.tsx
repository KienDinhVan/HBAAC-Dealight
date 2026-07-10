import { useState } from 'react'
import { ChevronDown, ChevronRight, Bot } from 'lucide-react'
import type { AgentBlock } from '@/types/events'
import { ToolCallItem } from './ToolCallItem'
import { MarkdownContent } from './MarkdownContent'
import { VegaChart } from '@/components/chart/VegaChart'
import { getAgentConfig } from '@/lib/agent-config'
import { cn } from '@/lib/utils'

interface DelegationCardProps {
  block: AgentBlock
}

export function DelegationCard({ block }: DelegationCardProps) {
  const [expanded, setExpanded] = useState(true)
  const cfg = getAgentConfig(block.agent)
  const hasBody = block.tools.length > 0 || block.content.length > 0 || block.charts.length > 0

  return (
    <div className={cn('rounded-xl border my-1.5', cfg.borderClass, cfg.bgClass)}>
      {/* ── Header ── */}
      <button
        onClick={() => setExpanded(e => !e)}
        className="w-full flex items-center gap-2 px-3 py-2 hover:bg-white/5 transition-colors"
      >
        {/* Pulsing status dot */}
        <span
          className={cn(
            'h-1.5 w-1.5 rounded-full flex-shrink-0',
            cfg.dotClass,
            !block.isDone && 'animate-pulse',
          )}
        />
        <Bot className={cn('h-3.5 w-3.5 flex-shrink-0', cfg.colorClass)} />
        <span className={cn('text-xs font-semibold', cfg.colorClass)}>{cfg.displayName}</span>
        <span className="text-xs text-zinc-500 ml-0.5">
          {block.isDone ? '— done' : '— thinking…'}
        </span>

        {hasBody && (
          <span className="ml-auto text-zinc-600">
            {expanded
              ? <ChevronDown className="h-3.5 w-3.5" />
              : <ChevronRight className="h-3.5 w-3.5" />}
          </span>
        )}
      </button>

      {/* ── Body ── */}
      {expanded && hasBody && (
        <div className="px-3 pb-3 space-y-2">
          {/* Tool calls */}
          {block.tools.length > 0 && (
            <div className="bg-black/20 rounded-lg p-1">
              {block.tools.map((tool, i) => (
                <ToolCallItem key={`${tool.name}-${i}`} tool={tool} />
              ))}
            </div>
          )}

          {/* Agent's streamed response text */}
          {block.content && (
            <MarkdownContent
              content={block.content}
              streaming={!block.isDone}
              className="text-zinc-300 px-1"
            />
          )}

          {/* Charts produced by the sub-agent */}
          {block.charts.map((spec, i) => (
            <VegaChart key={i} spec={spec} />
          ))}
        </div>
      )}
    </div>
  )
}
