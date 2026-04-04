import { renderHook, act, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useUABridge } from './use-ua-bridge'
import { useAgentFlowSpotlightStore } from '@/lib/agent-flow/spotlight-store'

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
    localStorage.clear()
    useAgentFlowSpotlightStore.setState({
      mode: 'recent',
      selectedSessionId: null,
      selectionSource: 'auto',
      archivesBySessionId: {},
      recentSessionIds: [],
      greatestHitSessionIds: [],
      currentReplayLoopIndex: 0,
      currentReplayGeneration: 0,
      currentSpotlightTitle: '',
      currentSpotlightIsLive: false,
      connectionStatus: 'disconnected',
    })

    class MockBroadcastChannel {
      constructor(_name: string) {}
      addEventListener() {}
      postMessage() {}
      close() {}
    }

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

    global.BroadcastChannel = MockBroadcastChannel as unknown as typeof BroadcastChannel
    global.WebSocket = MockWebSocket as unknown as typeof WebSocket

    Object.defineProperty(window, 'location', {
      value: { protocol: 'http:', host: 'localhost:3000' },
      writable: true,
    })
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('auto-selects the newest active recent session and builds replay playlists', async () => {
    fetchSessionDirectoryMock.mockResolvedValue([
      {
        session_id: 'session_a',
        status: 'running',
        source: 'chat',
        description: 'Chat session',
        workspace_dir: '/tmp/session_a',
        last_activity: '2026-04-04T12:00:00Z',
      },
      {
        session_id: 'session_b',
        status: 'running',
        source: 'chat',
        description: 'Second chat session',
        workspace_dir: '/tmp/session_b',
        last_activity: '2026-04-04T12:01:00Z',
      },
    ])

    const { result } = renderHook(() => useUABridge())

    act(() => {
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: 'text',
          data: {
            session_id: 'session_a',
            author: 'assistant',
            text: 'alpha',
          },
        }),
      } as MessageEvent)
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: 'text',
          data: {
            session_id: 'session_b',
            author: 'assistant',
            text: 'beta',
          },
        }),
      } as MessageEvent)
    })

    await waitFor(() => {
      expect(result.current.selectedSessionId).toBe('session_b')
    })

    expect(result.current.recentSessionIds).toEqual(['session_b', 'session_a'])
    expect(result.current.selectedPlaybackMode).toBe('live')
  })

  it('keeps manual selection pinned and switches completed sessions into replay mode', async () => {
    fetchSessionDirectoryMock.mockResolvedValue([
      {
        session_id: 'session_alpha',
        status: 'running',
        source: 'chat',
        description: 'Alpha session',
        workspace_dir: '/tmp/session_alpha',
        last_activity: '2026-04-04T12:00:00Z',
      },
      {
        session_id: 'session_beta',
        status: 'running',
        source: 'chat',
        description: 'Beta session',
        workspace_dir: '/tmp/session_beta',
        last_activity: '2026-04-04T12:01:00Z',
      },
    ])

    const { result } = renderHook(() => useUABridge())

    act(() => {
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: 'status',
          data: {
            session_id: 'session_alpha',
            status: 'processing',
            query: 'Alpha query',
          },
        }),
      } as MessageEvent)
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: 'text',
          data: {
            session_id: 'session_alpha',
            author: 'assistant',
            text: 'Alpha text',
          },
        }),
      } as MessageEvent)
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: 'status',
          data: {
            session_id: 'session_beta',
            status: 'processing',
            query: 'Beta query',
          },
        }),
      } as MessageEvent)
    })

    act(() => {
      result.current.selectSession('session_alpha', 'manual')
    })

    act(() => {
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: 'text',
          data: {
            session_id: 'session_beta',
            author: 'assistant',
            text: 'Beta keeps running',
          },
        }),
      } as MessageEvent)
    })

    expect(result.current.selectedSessionId).toBe('session_alpha')

    act(() => {
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: 'tool_call',
          data: {
            session_id: 'session_alpha',
            id: 'tool-1',
            name: 'Read',
            input: { file_path: '/tmp/a.md' },
          },
        }),
      } as MessageEvent)
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: 'tool_result',
          data: {
            session_id: 'session_alpha',
            tool_use_id: 'tool-1',
            content_preview: 'done',
          },
        }),
      } as MessageEvent)
      sockets[0].onmessage?.({
        data: JSON.stringify({
          type: 'iteration_end',
          data: {
            session_id: 'session_alpha',
          },
        }),
      } as MessageEvent)
    })

    await waitFor(() => {
      expect(result.current.selectedPlaybackMode).toBe('replay')
      expect(result.current.greatestHitSessionIds).toEqual(['session_alpha'])
    })

    expect(result.current.selectedPlaybackEvents.length).toBeGreaterThanOrEqual(4)
  })
})
