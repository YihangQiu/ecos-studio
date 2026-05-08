import { afterEach, describe, expect, it, vi } from 'vitest'
import { sendAgentChat } from './agent'

describe('Agent API client', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('sends ECOS workspace context and backend API base URL', async () => {
    const fetchMock = vi.spyOn(globalThis, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ reply: 'ok', sources: [], tools_used: [], trace_id: 't', fallback_used: false, decision: {} }),
    } as Response)

    await sendAgentChat({
      message: 'status',
      session_id: 'session-1',
      workspace_id: '/tmp/ws',
      active_step: 'place',
      mode: 'chat',
    })

    const [, init] = fetchMock.mock.calls[0]
    const body = JSON.parse(String(init?.body))
    expect(body).toMatchObject({
      message: 'status',
      session_id: 'session-1',
      workspace_id: '/tmp/ws',
      active_step: 'place',
      mode: 'chat',
      scenario: 'ecos',
    })
    expect(body.api_base_url).toMatch(/^http:\/\/127\.0\.0\.1:/)
  })
})
