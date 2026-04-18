import type { SimulationEvent } from './agent-types'
import type { ReplayPacingMode } from './visual-preferences'

export const REPLAY_BASE_INTERVAL_S = 0.45
export const REPLAY_MILESTONE_INTERVAL_S = 0.7
export const REPLAY_LOOP_HOLD_S = 2

export interface ReplayTimelineOptions {
  pacingMode?: ReplayPacingMode
  readableHoldMultiplier?: number
}

const MILESTONE_TYPES = new Set<SimulationEvent['type']>([
  'agent_spawn',
  'tool_call_end',
  'subagent_dispatch',
  'subagent_return',
  'permission_requested',
  'agent_complete',
])

function readableContentLength(event: SimulationEvent): number {
  if (event.type === 'text_burst') return String(event.payload.content || event.payload.summary || '').length
  if (event.type === 'artifact_emitted') return String(event.payload.content || event.payload.summary || event.payload.title || '').length
  if (event.type === 'error_recovery') return String(event.payload.label || '').length
  return 0
}

function replayIntervalAfter(event: SimulationEvent, options: ReplayTimelineOptions): number {
  const base = MILESTONE_TYPES.has(event.type) ? REPLAY_MILESTONE_INTERVAL_S : REPLAY_BASE_INTERVAL_S
  const pacingMode = options.pacingMode || 'fast'
  if (pacingMode === 'fast') return base

  const length = readableContentLength(event)
  if (length <= 0) return base

  const multiplier = Math.min(2, Math.max(0.5, options.readableHoldMultiplier ?? 1))
  const maxHold = pacingMode === 'dramatic' ? 4.2 : 2.6
  const minHold = pacingMode === 'dramatic' ? 0.9 : 0.45
  const estimatedHold = Math.min(maxHold, Math.max(minHold, length / (pacingMode === 'dramatic' ? 520 : 780)))
  return base + estimatedHold * multiplier
}

export function buildReplayTimeline(events: SimulationEvent[], options: ReplayTimelineOptions = {}): SimulationEvent[] {
  let cursor = 0
  return events.map((event, index) => {
    if (index === 0) {
      cursor = 0
    } else {
      const previous = events[index - 1]
      cursor += replayIntervalAfter(previous, options)
    }
    return {
      ...event,
      time: cursor,
    }
  })
}
