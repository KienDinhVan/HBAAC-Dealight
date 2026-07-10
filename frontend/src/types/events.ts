// Raw SSE events emitted by the backend
export type SSEEvent =
  | { type: 'agent_start'; agent: string }
  | { type: 'agent_done'; agent: string }
  | { type: 'tool_start'; name: string; agent: string; input: Record<string, unknown> }
  | { type: 'tool_done'; name: string; agent: string }
  | { type: 'tool_approval_request'; name: string; agent: string; input: Record<string, unknown>; approval_id: string }
  | { type: 'tool_denied'; name: string; agent: string; approval_id: string }
  | { type: 'delta'; content: string; agent: string }
  | { type: 'chart'; spec: Record<string, unknown>; agent: string }
  | { type: 'done' }

// A single tool call visible in an agent block
export type ToolCall = {
  name: string
  input: Record<string, unknown>
  status: 'running' | 'done' | 'pending_approval' | 'denied'
  /** Present when status is 'pending_approval' — used to call the approval endpoint. */
  approval_id?: string
}

// One agent's activity section inside an assistant message
export type AgentBlock = {
  agent: string
  tools: ToolCall[]
  content: string     // accumulated streaming text
  isDone: boolean
  charts: Record<string, unknown>[]
  /** true  → created by agent_start (sub-agent delegation card)
   *  false → auto-created when delta arrives without agent_start (synthesis text) */
  isDelegate: boolean
}

export type UserChatMessage = {
  id: string
  role: 'user'
  content: string
}

export type AssistantChatMessage = {
  id: string
  role: 'assistant'
  agentBlocks: AgentBlock[]
  isDone: boolean
}

export type ChatMessage = UserChatMessage | AssistantChatMessage
