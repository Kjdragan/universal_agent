import { describe, expect, it } from 'vitest'
import { createGatewayAgentFlowAdapter } from './gateway-adapter'

describe('createGatewayAgentFlowAdapter', () => {
  it('maps core runtime events into simulation events', () => {
    let now = 1000
    const adapter = createGatewayAgentFlowAdapter(() => now++)

    const status = adapter.ingest({
      type: 'status',
      data: {
        session_id: 'session_alpha',
        status: 'processing',
        query: 'Trace the active flow',
      },
    })

    expect(status.sessionId).toBe('session_alpha')
    expect(status.session?.label).toBe('Trace the active flow')
    expect(status.events).toHaveLength(1)
    expect(status.events[0]).toMatchObject({
      type: 'agent_spawn',
      sessionId: 'session_alpha',
      payload: {
        name: 'orchestrator',
        isMain: true,
      },
    })

    const text = adapter.ingest({
      type: 'text',
      data: {
        session_id: 'session_alpha',
        author: 'user',
        text: 'Show me the live events.',
      },
    })

    expect(text.events).toEqual([
      expect.objectContaining({
        type: 'message',
        payload: expect.objectContaining({
          agent: 'orchestrator',
          role: 'user',
          content: 'Show me the live events.',
        }),
      }),
    ])

    const toolCall = adapter.ingest({
      type: 'tool_call',
      data: {
        session_id: 'session_alpha',
        id: 'tool-1',
        name: 'Read',
        input: {
          file_path: '/tmp/active_flow.md',
        },
      },
    })

    expect(toolCall.events).toEqual([
      expect.objectContaining({
        type: 'tool_call_start',
        payload: expect.objectContaining({
          agent: 'orchestrator',
          tool: 'Read',
          args: '/tmp/active_flow.md',
        }),
      }),
    ])

    const toolResult = adapter.ingest({
      type: 'tool_result',
      data: {
        session_id: 'session_alpha',
        tool_use_id: 'tool-1',
        content_preview: 'Loaded 42 lines',
      },
    })

    expect(toolResult.events).toEqual([
      expect.objectContaining({
        type: 'tool_call_end',
        payload: expect.objectContaining({
          agent: 'orchestrator',
          tool: 'Read',
          result: 'Loaded 42 lines',
          isError: false,
        }),
      }),
    ])

    const authRequired = adapter.ingest({
      type: 'auth_required',
      data: {
        session_id: 'session_alpha',
        auth_link: 'https://example.com/auth',
      },
    })

    expect(authRequired.events).toEqual([
      expect.objectContaining({
        type: 'permission_requested',
        payload: expect.objectContaining({
          agent: 'orchestrator',
          message: 'https://example.com/auth',
        }),
      }),
    ])

    const completion = adapter.ingest({
      type: 'iteration_end',
      data: {
        session_id: 'session_alpha',
      },
    })

    expect(completion.events).toEqual([
      expect.objectContaining({
        type: 'agent_complete',
        payload: { name: 'orchestrator' },
      }),
    ])
  })

  it('preserves top-level session ids for global-flow system events', () => {
    let now = 2000
    const adapter = createGatewayAgentFlowAdapter(() => now++)

    const heartbeat = adapter.ingest({
      type: 'system_event',
      session_id: 'cron_heartbeat_session',
      data: {
        type: 'heartbeat_summary',
        payload: {
          summary: 'Heartbeat completed successfully',
        },
      },
    })

    expect(heartbeat.sessionId).toBe('cron_heartbeat_session')
    expect(heartbeat.events.map((event) => event.type)).toEqual([
      'agent_spawn',
      'subagent_dispatch',
      'agent_spawn',
      'message',
      'subagent_return',
      'agent_complete',
    ])
    expect(heartbeat.events[3]).toMatchObject({
      payload: expect.objectContaining({
        agent: 'heartbeat-service',
        content: 'Heartbeat completed successfully',
      }),
    })
  })
})
