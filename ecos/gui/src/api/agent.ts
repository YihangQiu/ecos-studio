import { API_BASE_URL } from './client'

export const AGENT_HOST = '127.0.0.1'
export const AGENT_PORT = 8766
export const AGENT_BASE_URL = `http://${AGENT_HOST}:${AGENT_PORT}`

export interface AgentChatRequest {
  message: string
  session_id: string
  workspace_id: string
  active_step: string
  mode: 'chat' | 'builder'
  scenario: 'ecos'
  api_base_url: string
  stream?: boolean
}

export interface AgentChatResponse {
  reply: string
  sources: Record<string, unknown>[]
  tools_used: string[]
  trace_id: string
  confidence?: number | null
  fallback_used: boolean
  decision: Record<string, unknown>
}

export interface AgentEvent {
  type: string
  stage?: string
  message?: string
  trace_id?: string
  [key: string]: unknown
}

export async function checkAgentHealth(timeoutMs = 1500): Promise<boolean> {
  try {
    const response = await fetch(`${AGENT_BASE_URL}/health`, {
      method: 'GET',
      signal: AbortSignal.timeout(timeoutMs),
    })
    return response.ok
  } catch {
    return false
  }
}

export async function sendAgentChat(request: Omit<AgentChatRequest, 'scenario' | 'api_base_url'>): Promise<AgentChatResponse> {
  const payload: AgentChatRequest = {
    ...request,
    scenario: 'ecos',
    api_base_url: API_BASE_URL,
    stream: request.stream ?? false,
  }
  const response = await fetch(`${AGENT_BASE_URL}/api/agent/chat`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    throw new Error(`Agent API unavailable (${response.status}). Start it with: edabot serve`)
  }
  return await response.json() as AgentChatResponse
}

export interface AgentSSEClient {
  connect: () => void
  close: () => void
  onEvent: (handler: (event: AgentEvent) => void) => void
}

export function createAgentSSEClient(sessionId: string): AgentSSEClient {
  let eventSource: EventSource | null = null
  const handlers: Array<(event: AgentEvent) => void> = []

  function emit(event: AgentEvent) {
    handlers.forEach((handler) => {
      try {
        handler(event)
      } catch (error) {
        console.error('Agent SSE handler error:', error)
      }
    })
  }

  function connect() {
    if (eventSource) {
      eventSource.close()
    }
    eventSource = new EventSource(`${AGENT_BASE_URL}/api/agent/events/${encodeURIComponent(sessionId)}`)
    for (const eventType of ['tool_call', 'tool_result', 'flow_progress', 'error', 'heartbeat', 'done', 'message']) {
      eventSource.addEventListener(eventType, (event: MessageEvent) => {
        try {
          emit(JSON.parse(event.data) as AgentEvent)
        } catch (error) {
          console.error('Failed to parse Agent SSE event:', error)
        }
      })
    }
    eventSource.onerror = () => {
      emit({ type: 'error', message: 'Agent event stream disconnected' })
    }
  }

  function close() {
    eventSource?.close()
    eventSource = null
  }

  return {
    connect,
    close,
    onEvent(handler) {
      handlers.push(handler)
    },
  }
}
