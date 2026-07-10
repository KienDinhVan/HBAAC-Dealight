import { User } from 'lucide-react'
import type { UserChatMessage } from '@/types/events'

interface UserBubbleProps {
  message: UserChatMessage
}

export function UserBubble({ message }: UserBubbleProps) {
  return (
    <div className="flex gap-3 flex-row-reverse">
      {/* Avatar */}
      <div className="flex-shrink-0 mt-0.5">
        <div className="h-8 w-8 rounded-full bg-zinc-800 border border-zinc-700 flex items-center justify-center">
          <User className="h-4 w-4 text-zinc-400" />
        </div>
      </div>

      {/* Bubble */}
      <div className="max-w-[75%] bg-zinc-800 border border-zinc-700 rounded-2xl rounded-tr-sm px-4 py-2.5">
        <p className="text-sm text-zinc-100 leading-relaxed">{message.content}</p>
      </div>
    </div>
  )
}
