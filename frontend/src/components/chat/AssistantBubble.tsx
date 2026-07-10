import { Bot } from 'lucide-react'
import type { AssistantChatMessage } from '@/types/events'
import { DelegationCard } from './DelegationCard'
import { MarkdownContent } from './MarkdownContent'
import { VegaChart } from '@/components/chart/VegaChart'
import { cn } from '@/lib/utils'

interface AssistantBubbleProps {
  message: AssistantChatMessage
}

export function AssistantBubble({ message }: AssistantBubbleProps) {
  const delegateBlocks = message.agentBlocks.filter(b => b.isDelegate)
  const synthesisBlocks = message.agentBlocks.filter(b => !b.isDelegate)
  const isWaiting = message.agentBlocks.length === 0 && !message.isDone

  return (
    <div className="flex gap-3">
      {/* Avatar */}
      <div className="flex-shrink-0 mt-0.5">
        <div className="h-8 w-8 rounded-full bg-emerald-600/20 border border-emerald-500/40 flex items-center justify-center">
          <Bot className="h-4 w-4 text-emerald-400" />
        </div>
      </div>

      {/* Content */}
      <div className="flex-1 min-w-0 pt-1">
        <div className="text-xs text-zinc-500 font-medium mb-2">Dealight Analytics</div>

        {/* Waiting dots */}
        {isWaiting && (
          <div className="flex gap-1 py-1">
            <span className="h-1.5 w-1.5 rounded-full bg-zinc-500 animate-bounce [animation-delay:0ms]" />
            <span className="h-1.5 w-1.5 rounded-full bg-zinc-500 animate-bounce [animation-delay:150ms]" />
            <span className="h-1.5 w-1.5 rounded-full bg-zinc-500 animate-bounce [animation-delay:300ms]" />
          </div>
        )}

        {/* Sub-agent delegation cards (with charts inside) */}
        {delegateBlocks.length > 0 && (
          <div className="mb-3 space-y-1">
            {delegateBlocks.map((block, i) => (
              <DelegationCard key={`${block.agent}-${i}`} block={block} />
            ))}
          </div>
        )}

        {/* TeamLead synthesis text + any charts it produced directly */}
        {synthesisBlocks.map((block, i) => (
          <div key={`synthesis-${i}`} className={cn(i > 0 && 'mt-3')}>
            <MarkdownContent
              content={block.content}
              streaming={!block.isDone && !message.isDone}
              className="text-zinc-100"
            />
            {block.charts.map((spec, ci) => (
              <VegaChart key={ci} spec={spec} />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
