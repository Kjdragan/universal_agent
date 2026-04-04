import { describe, expect, it } from 'vitest'
import type { SpotlightSessionArchive } from './spotlight-types'
import {
  deriveGreatestHitSessionIds,
  deriveRecentSessionIds,
  resolveSpotlightSelection,
} from './spotlight-session-utils'

function archive(overrides: Partial<SpotlightSessionArchive>): SpotlightSessionArchive {
  return {
    sessionId: 'session',
    title: 'Session',
    status: 'active',
    laneHint: 'chat',
    firstMeaningfulEventAt: 1,
    lastMeaningfulEventAt: 2,
    completedAt: null,
    durationMs: 1000,
    normalizedEvents: [],
    meaningfulEventCount: 5,
    ...overrides,
  }
}

describe('spotlight session utils', () => {
  it('sorts recent sessions with active sessions first and newest first', () => {
    const archives = {
      a: archive({ sessionId: 'a', status: 'completed', lastMeaningfulEventAt: 10 }),
      b: archive({ sessionId: 'b', status: 'active', lastMeaningfulEventAt: 8 }),
      c: archive({ sessionId: 'c', status: 'active', lastMeaningfulEventAt: 12 }),
    }

    expect(deriveRecentSessionIds(archives)).toEqual(['c', 'b', 'a'])
  })

  it('builds greatest hits from longest completed runs only', () => {
    const archives = {
      short: archive({ sessionId: 'short', status: 'completed', durationMs: 1_000, completedAt: 100, meaningfulEventCount: 5 }),
      long: archive({ sessionId: 'long', status: 'completed', durationMs: 9_000, completedAt: 50, meaningfulEventCount: 6 }),
      ignored: archive({ sessionId: 'ignored', status: 'completed', durationMs: 99_000, completedAt: 200, meaningfulEventCount: 3 }),
    }

    expect(deriveGreatestHitSessionIds(archives)).toEqual(['long', 'short'])
  })

  it('keeps manual selection when it remains valid and otherwise auto-selects the newest recent session', () => {
    const archives = {
      a: archive({ sessionId: 'a', status: 'completed', lastMeaningfulEventAt: 10 }),
      b: archive({ sessionId: 'b', status: 'active', lastMeaningfulEventAt: 20 }),
    }

    expect(resolveSpotlightSelection({
      mode: 'recent',
      selectionSource: 'manual',
      selectedSessionId: 'a',
      recentSessionIds: ['b', 'a'],
      greatestHitSessionIds: ['a'],
      archivesBySessionId: archives,
    })).toMatchObject({
      mode: 'recent',
      selectionSource: 'manual',
      selectedSessionId: 'a',
    })

    expect(resolveSpotlightSelection({
      mode: 'recent',
      selectionSource: 'auto',
      selectedSessionId: null,
      recentSessionIds: ['b', 'a'],
      greatestHitSessionIds: ['a'],
      archivesBySessionId: archives,
    })).toMatchObject({
      mode: 'recent',
      selectionSource: 'auto',
      selectedSessionId: 'b',
    })
  })
})
