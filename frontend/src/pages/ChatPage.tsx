import { useEffect, useRef } from 'react'
import { TrendingUp, LogOut, Trash2, Sparkles } from 'lucide-react'
import { useChat } from '@/hooks/useChat'
import { ChatInput } from '@/components/chat/ChatInput'
import { AssistantBubble } from '@/components/chat/AssistantBubble'
import { UserBubble } from '@/components/chat/UserBubble'
import { Button } from '@/components/ui/button'
import type { AssistantChatMessage, UserChatMessage } from '@/types/events'

interface ChatPageProps {
  onLogout: () => void
}

const SUGGESTED = [
  'Which SKUs generated the most profit in the last 3 months?',
  'Show me a chart of daily revenue trend for 2025.',
  'Which SKUs are at risk of stockout in the next 28 days?',
  'What are the top 10 SKUs by forecasted demand for the next month?',
  'Which products have the highest return rates?',
  'Show overstock risk SKUs that need procurement attention.',
]

export default function ChatPage({ onLogout }: ChatPageProps) {
  const { messages, isLoading, sendMessage, stopStream, clearMessages } = useChat()
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  return (
    <div className="flex flex-col h-screen bg-zinc-950">
      {/* ── Top bar ── */}
      <header className="flex-shrink-0 border-b border-zinc-800 bg-zinc-950/90 backdrop-blur-sm z-10">
        <div className="max-w-4xl mx-auto flex items-center justify-between px-4 h-14">
          {/* Brand */}
          <div className="flex items-center gap-2.5">
            <div className="h-7 w-7 rounded-lg bg-emerald-600/20 border border-emerald-500/40 flex items-center justify-center">
              <TrendingUp className="h-4 w-4 text-emerald-400" />
            </div>
            <span className="font-semibold text-zinc-100 text-sm">Dealight Analytics</span>
            <span className="text-[10px] text-zinc-500 bg-zinc-800 px-2 py-0.5 rounded-full border border-zinc-700">
              AI Agent
            </span>
          </div>

          {/* Actions */}
          <div className="flex items-center gap-1">
            {messages.length > 0 && (
              <Button
                variant="ghost"
                size="icon"
                onClick={clearMessages}
                title="Clear conversation"
                className="h-8 w-8"
              >
                <Trash2 className="h-4 w-4" />
              </Button>
            )}
            <Button
              variant="ghost"
              size="icon"
              onClick={onLogout}
              title="Sign out"
              className="h-8 w-8"
            >
              <LogOut className="h-4 w-4" />
            </Button>
          </div>
        </div>
      </header>

      {/* ── Messages ── */}
      <main className="flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          /* Empty / welcome state */
          <div className="h-full flex flex-col items-center justify-center px-4 pb-16">
            <div className="h-14 w-14 rounded-2xl bg-emerald-600/20 border border-emerald-500/40 flex items-center justify-center mb-5">
              <Sparkles className="h-7 w-7 text-emerald-400" />
            </div>
            <h2 className="text-xl font-semibold text-zinc-100 mb-1.5">Dealight Analytics</h2>
            <p className="text-zinc-500 text-sm mb-8 text-center max-w-xs leading-relaxed">
              Ask about sales performance, top products, demand forecasts, or inventory risk — in plain language.
            </p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 w-full max-w-2xl">
              {SUGGESTED.map(prompt => (
                <button
                  key={prompt}
                  onClick={() => sendMessage(prompt)}
                  className="text-left text-sm text-zinc-300 bg-zinc-900 hover:bg-zinc-800 border border-zinc-800 hover:border-zinc-700 rounded-xl px-4 py-3 transition-colors leading-snug"
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        ) : (
          /* Conversation */
          <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
            {messages.map(message =>
              message.role === 'user' ? (
                <UserBubble key={message.id} message={message as UserChatMessage} />
              ) : (
                <AssistantBubble key={message.id} message={message as AssistantChatMessage} />
              ),
            )}
            <div ref={bottomRef} />
          </div>
        )}
      </main>

      {/* ── Input ── */}
      <ChatInput onSend={sendMessage} onStop={stopStream} isLoading={isLoading} />
    </div>
  )
}
