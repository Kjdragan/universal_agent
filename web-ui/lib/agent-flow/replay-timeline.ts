import type { SimulationEvent } from './agent-types'

export const REPLAY_BASE_INTERVAL_S = 0.45
export const REPLAY_MILESTONE_INTERVAL_S = 0.7
export const REPLAY_LOOP_HOLD_S = 2

const MILESTONE_TYPES = new Set<SimulationEvent['type']>([
  'agent_spawn',
  'tool_call_end',
  'subagent_dispatch',
  'subagent_return',
  'permission_requested',
  'agent_complete',
])

export function buildReplayTimeline(events: SimulationEvent[]): SimulationEvent[] {
  let cursor = 0
  return events.map((event, index) => {
    if (index === 0) {
      cursor = 0
    } else {
      const previous = events[index - 1]
      cursor += MILESTONE_TYPES.has(previous.type) ? REPLAY_MILESTONE_INTERVAL_S : REPLAY_BASE_INTERVAL_S
    }
    return {
      ...event,
      time: cursor,
    }
  })
}
