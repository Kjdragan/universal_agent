import { renderHook, act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useUABridge } from './use-ua-bridge'

const fetchSessionDirectoryMock = vi.fn()

vi.mock('@/lib/sessionDirectory', () => ({
  fetchSessionDirectory: (...args: unknown[]) => fetchSessionDirectoryMock(...args),
}))

type MockSocket = {
  url: string
  readyState: number
  onopen?: () => void
  onmessage?: (event: MessageEvent) => void
  onclose?: () => void
  onerror?: (event: Event) => void
  close: ReturnType<typeof vi.fn>
}

describe('useUABridge', () => {
  let sockets: MockSocket[] = []

  beforeEach(() => {
    sockets = []
    fetchSessionDirectoryMock.mockReset()

    class MockWebSocket {
      url: string
      readyState: number
      onopen?: () => void
      onmessage?: (event: MessageEvent) => void
      onclose?: () => void
      onerror?: (event: Event) => void
      close = vi.fn()

      static CONNECTING = 0
      static OPEN = 1

      constructor(url: string) {
        this.url = url
        this.readyState = MockWebSocket.CONNECTING
        sockets.push(this as unknown as MockSocket)
      }
    }

    global.WebSocket = MockWebSocket as unknown as typeof WebSocket

    Object.defineProperty(window, 'location', {
      value: { protocol: 'http:', host: 'localhost:3000' },
      writable: true,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('seeds real sessions and ignores global_agent_flow as a selectable session', async () => {
    fetchSessionDirectoryMock.mockResolvedValue([
      {
        session_id: 'session_seeded',
        status: 'running',
        workspace_dir: '/tmp/session_seeded',
        last_activity: '2026-04-04T12:00:00Z',
      },
    ])

    const { result } = renderHook(() => useUABridge())

    await waitFor(() => {
      expect(result.current.selectedSessionId).toBe('session_seeded')
    })

    expect(sockets).toHaveLength(1)
    expect(sockets[0].url).toContain('/ws/agent?session_id=global_agent_flow')
    expect(result.current.sessions.map((session) => session.id)).toEqual(['session_seeded'])

    act(() => {
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: 'connected',
          data: {
            session: {
              session_id: 'global_agent_flow',
            },
          },
        }),
      } as MessageEvent)
    })

    expect(result.current.sessions.map((session) => session.id)).toEqual(['session_seeded'])
  })

  it('buffers per-session events, tracks background activity, and replays on session switch', async () => {
    fetchSessionDirectoryMock.mockResolvedValue([
      {
        session_id: 'session_a',
        status: 'running',
        workspace_dir: '/tmp/session_a',
        last_activity: '2026-04-04T12:00:00Z',
      },
      {
        session_id: 'session_b',
        status: 'running',
        workspace_dir: '/tmp/session_b',
        last_activity: '2026-04-04T11:59:00Z',
      },
    ])

    const { result } = renderHook(() => useUABridge())

    await waitFor(() => {
      expect(result.current.selectedSessionId).toBe('session_a')
    })

    act(() => {
      result.current.flushSessionEvents('session_a')
    })

    act(() => {
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: 'text',
          data: {
            session_id: 'session_b',
            author: 'assistant',
            text: 'Background cron update',
          },
        }),
      } as MessageEvent)
    })

    expect(result.current.getSessionEventCount('session_b')).toBe(2)
    expect(result.current.sessionsWithActivity.has('session_b')).toBe(true)
    expect(result.current.pendingEvents).toHaveLength(0)

    act(() => {
      result.current.selectSession('session_b')
      result.current.flushSessionEvents('session_b')
    })

    expect(result.current.selectedSessionId).toBe('session_b')
    expect(result.current.sessionsWithActivity.has('session_b')).toBe(false)
    expect(result.current.pendingEvents).toHaveLength(2)
    expect(result.current.pendingEvents.map((event) => event.type)).toEqual(['agent_spawn', 'message'])

    act(() => {
      result.current.consumeEvents()
    })

    expect(result.current.pendingEvents).toHaveLength(0)
  })
})
