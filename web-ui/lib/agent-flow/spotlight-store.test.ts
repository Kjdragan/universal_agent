import { beforeEach, describe, expect, it } from 'vitest'
import { useAgentFlowSpotlightStore } from './spotlight-store'

const STORAGE_KEY = 'ua.agent-flow-spotlight.v1'

describe('spotlight store persistence', () => {
  beforeEach(() => {
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
  })

  it('persists only lightweight control state', () => {
    useAgentFlowSpotlightStore.setState({
      mode: 'greatest_hits',
      selectedSessionId: 'session_alpha',
      selectionSource: 'manual',
      currentReplayLoopIndex: 2,
      currentReplayGeneration: 3,
      archivesBySessionId: {
        session_alpha: {
          sessionId: 'session_alpha',
          title: 'Alpha',
          status: 'completed',
          laneHint: 'chat',
          firstMeaningfulEventAt: 1,
          lastMeaningfulEventAt: 2,
          completedAt: 2,
          durationMs: 1,
          normalizedEvents: [{ time: 1, type: 'message', payload: { agent: 'alpha', role: 'assistant', content: 'hello' }, sessionId: 'session_alpha' }],
          meaningfulEventCount: 1,
        },
      },
      recentSessionIds: ['session_alpha'],
      greatestHitSessionIds: ['session_alpha'],
    })

    const raw = localStorage.getItem(STORAGE_KEY)
    expect(raw).toBeTruthy()

    const parsed = JSON.parse(raw as string)
    expect(parsed.state).toEqual({
      mode: 'greatest_hits',
      selectedSessionId: 'session_alpha',
      selectionSource: 'manual',
      currentReplayLoopIndex: 2,
      currentReplayGeneration: 3,
    })
    expect(parsed.state.archivesBySessionId).toBeUndefined()
  })

  it('drops malformed persisted state without throwing', () => {
    localStorage.setItem(STORAGE_KEY, '{bad-json')

    expect(() => {
      useAgentFlowSpotlightStore.persist.rehydrate()
    }).not.toThrow()

    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })
})
