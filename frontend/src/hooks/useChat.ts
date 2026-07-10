import { useState, useCallback, useRef } from 'react'
import { streamChat } from '@/lib/api'
import type {
  ChatMessage,
  AssistantChatMessage,
  AgentBlock,
} from '@/types/events'

/** Find last index satisfying predicate (Array.prototype.findLastIndex polyfill) */
function findLastIdx<T>(arr: T[], pred: (item: T) => boolean): number {
  for (let i = arr.length - 1; i >= 0; i--) {
    if (pred(arr[i])) return i
  }
  return -1
}

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isLoading, setIsLoading] = useState(false)
  const abortRef = useRef<AbortController | null>(null)

  /** Immutably update the AssistantChatMessage with the given id. */
  const patchAssistant = useCallback(
    (id: string, fn: (m: AssistantChatMessage) => AssistantChatMessage) => {
      setMessages(prev =>
        prev.map(m =>
          m.id === id && m.role === 'assistant' ? fn(m as AssistantChatMessage) : m,
        ),
      )
    },
    [],
  )

  const sendMessage = useCallback(
    async (content: string) => {
      if (isLoading) return

      // Cancel any in-flight request
      abortRef.current?.abort()
      const controller = new AbortController()
      abortRef.current = controller

      const userMsg: ChatMessage = {
        id: crypto.randomUUID(),
        role: 'user',
        content,
      }

      const assistantId = crypto.randomUUID()
      const assistantMsg: AssistantChatMessage = {
        id: assistantId,
        role: 'assistant',
        agentBlocks: [],
        isDone: false,
      }

      setMessages(prev => [...prev, userMsg, assistantMsg])
      setIsLoading(true)

      try {
        for await (const event of streamChat(content, controller.signal)) {
          patchAssistant(assistantId, msg => {
            // shallow-copy blocks array so React sees the change
            const blocks: AgentBlock[] = msg.agentBlocks.map(b => ({ ...b }))

            switch (event.type) {
              case 'agent_start': {
                blocks.push({
                  agent: event.agent,
                  tools: [],
                  content: '',
                  isDone: false,
                  charts: [],
                  isDelegate: true,
                })
                break
              }

              case 'agent_done': {
                const i = findLastIdx(blocks, b => b.agent === event.agent && b.isDelegate)
                if (i >= 0) blocks[i] = { ...blocks[i], isDone: true }
                break
              }

              case 'tool_start': {
                const i = findLastIdx(blocks, b => b.agent === event.agent)
                if (i >= 0) {
                  // If this tool came through the approval flow it already has an
                  // entry in 'pending_approval' state — transition it to 'running'
                  // instead of adding a duplicate row.
                  const pendingIdx = findLastIdx(
                    blocks[i].tools,
                    t => t.name === event.name && t.status === 'pending_approval',
                  )
                  if (pendingIdx >= 0) {
                    const updatedTools = [...blocks[i].tools]
                    updatedTools[pendingIdx] = { ...updatedTools[pendingIdx], status: 'running' as const }
                    blocks[i] = { ...blocks[i], tools: updatedTools }
                  } else {
                    blocks[i] = {
                      ...blocks[i],
                      tools: [...blocks[i].tools, { name: event.name, input: event.input, status: 'running' }],
                    }
                  }
                }
                break
              }

              case 'tool_done': {
                const i = findLastIdx(blocks, b => b.agent === event.agent)
                if (i >= 0) {
                  blocks[i] = {
                    ...blocks[i],
                    tools: blocks[i].tools.map(t =>
                      t.name === event.name && t.status === 'running'
                        ? { ...t, status: 'done' as const }
                        : t,
                    ),
                  }
                }
                break
              }

              case 'tool_approval_request': {
                const i = findLastIdx(blocks, b => b.agent === event.agent)
                if (i >= 0) {
                  blocks[i] = {
                    ...blocks[i],
                    tools: [
                      ...blocks[i].tools,
                      {
                        name: event.name,
                        input: event.input,
                        status: 'pending_approval' as const,
                        approval_id: event.approval_id,
                      },
                    ],
                  }
                }
                break
              }

              case 'tool_denied': {
                const i = findLastIdx(blocks, b => b.agent === event.agent)
                if (i >= 0) {
                  blocks[i] = {
                    ...blocks[i],
                    tools: blocks[i].tools.map(t =>
                      t.approval_id === event.approval_id
                        ? { ...t, status: 'denied' as const }
                        : t,
                    ),
                  }
                }
                break
              }

              case 'delta': {
                // Find an active (not yet done) block for this agent
                let i = findLastIdx(blocks, b => b.agent === event.agent && !b.isDone)
                if (i === -1) {
                  // Auto-create a synthesis block (e.g. TeamLeadAgent's final answer)
                  blocks.push({
                    agent: event.agent,
                    tools: [],
                    content: '',
                    isDone: false,
                    charts: [],
                    isDelegate: false,
                  })
                  i = blocks.length - 1
                }
                blocks[i] = { ...blocks[i], content: blocks[i].content + event.content }
                break
              }

              case 'chart': {
                // Only add to the active (not-yet-done) block for this agent
                const i = findLastIdx(blocks, b => b.agent === event.agent && !b.isDone)
                if (i >= 0) {
                  blocks[i] = {
                    ...blocks[i],
                    charts: [...blocks[i].charts, event.spec],
                  }
                }
                break
              }

              case 'done': {
                return { ...msg, agentBlocks: blocks, isDone: true }
              }
            }

            return { ...msg, agentBlocks: blocks }
          })
        }
      } catch (err) {
        if ((err as Error).name === 'AbortError') return
        console.error('Stream error:', err)
        patchAssistant(assistantId, msg => ({
          ...msg,
          agentBlocks: [
            ...msg.agentBlocks,
            {
              agent: 'error',
              tools: [],
              charts: [],
              content: 'An error occurred while contacting the agent. Please try again.',
              isDone: true,
              isDelegate: false,
            },
          ],
          isDone: true,
        }))
      } finally {
        setIsLoading(false)
        patchAssistant(assistantId, msg => ({ ...msg, isDone: true }))
      }
    },
    [isLoading, patchAssistant],
  )

  const stopStream = useCallback(() => {
    abortRef.current?.abort()
    setIsLoading(false)
  }, [])

  const clearMessages = useCallback(() => {
    abortRef.current?.abort()
    setMessages([])
    setIsLoading(false)
  }, [])

  return { messages, isLoading, sendMessage, stopStream, clearMessages }
}
