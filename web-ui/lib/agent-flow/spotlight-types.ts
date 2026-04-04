import type { SimulationEvent } from './agent-types'

export type SpotlightMode = 'recent' | 'greatest_hits'
export type SpotlightSelectionSource = 'auto' | 'manual'
export type SpotlightLaneHint = 'chat' | 'email' | 'heartbeat' | 'system'
export type SpotlightArchiveStatus = 'active' | 'completed'
export type SpotlightPlaybackMode = 'live' | 'replay'

export interface SpotlightSessionArchive {
  sessionId: string
  title: string
  status: SpotlightArchiveStatus
  laneHint: SpotlightLaneHint
  firstMeaningfulEventAt: number | null
  lastMeaningfulEventAt: number | null
  completedAt: number | null
  durationMs: number
  normalizedEvents: SimulationEvent[]
  meaningfulEventCount: number
}
