import { useState, useRef, type KeyboardEvent } from 'react'
import { ArrowUp, Loader2, StopCircle } from 'lucide-react'
import { cn } from '@/lib/utils'

interface ChatInputProps {
  onSend: (message: string) => void
  onStop: () => void
  isLoading: boolean
}

export function ChatInput({ onSend, onStop, isLoading }: ChatInputProps) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSend = () => {
    const trimmed = value.trim()
    if (!trimmed || isLoading) return
    onSend(trimmed)
    setValue('')
    // Reset textarea height
    if (textareaRef.current) textareaRef.current.style.height = 'auto'
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleInput = () => {
    const el = textareaRef.current
    if (!el) return
    el.style.height = 'auto'
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`
  }

  return (
    <div className="flex-shrink-0 border-t border-zinc-800 bg-zinc-950 px-4 py-4">
      <div className="max-w-3xl mx-auto">
        <div
          className={cn(
            'flex items-end gap-3 bg-zinc-900 border rounded-2xl px-4 py-3 transition-colors',
            isLoading ? 'border-emerald-500/40' : 'border-zinc-700 focus-within:border-zinc-600',
          )}
        >
          <textarea
            ref={textareaRef}
            value={value}
            onChange={e => setValue(e.target.value)}
            onKeyDown={handleKeyDown}
            onInput={handleInput}
            placeholder={isLoading ? 'Agent is thinking…' : 'Ask Dealight Analytics anything…'}
            disabled={isLoading}
            rows={1}
            className={cn(
              'flex-1 resize-none bg-transparent text-sm text-zinc-100 placeholder:text-zinc-500',
              'focus:outline-none leading-6 min-h-[24px] max-h-[200px]',
              'disabled:opacity-50',
            )}
          />

          {/* Send / Stop button */}
          {isLoading ? (
            <button
              onClick={onStop}
              className="flex-shrink-0 h-8 w-8 rounded-full bg-zinc-700 hover:bg-zinc-600 text-zinc-300 flex items-center justify-center transition-colors"
              title="Stop generation"
            >
              <StopCircle className="h-4 w-4" />
            </button>
          ) : (
            <button
              onClick={handleSend}
              disabled={!value.trim()}
              className={cn(
                'flex-shrink-0 h-8 w-8 rounded-full flex items-center justify-center transition-colors',
                value.trim()
                  ? 'bg-emerald-600 hover:bg-emerald-700 text-white'
                  : 'bg-zinc-800 text-zinc-600 cursor-not-allowed',
              )}
              title="Send message"
            >
              <ArrowUp className="h-4 w-4" />
            </button>
          )}
        </div>

        <p className="text-center text-xs text-zinc-700 mt-2">
          Enter to send · Shift+Enter for new line
        </p>
      </div>
    </div>
  )
}
