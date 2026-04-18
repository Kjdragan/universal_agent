import { describe, expect, it } from 'vitest'
import type { SimulationEvent } from './agent-types'
import { buildReplayTimeline, REPLAY_BASE_INTERVAL_S, REPLAY_MILESTONE_INTERVAL_S } from './replay-timeline'

describe('buildReplayTimeline', () => {
  it('preserves event order and rewrites times to a synthetic cadence', () => {
    const source: SimulationEvent[] = [
      { time: 0, type: 'agent_spawn', payload: { name: 'orchestrator' }, sessionId: 's' },
      { time: 30, type: 'message', payload: { agent: 'orchestrator', content: 'hello' }, sessionId: 's' },
      { time: 300, type: 'tool_call_end', payload: { agent: 'orchestrator', tool: 'Read' }, sessionId: 's' },
      { time: 900, type: 'agent_complete', payload: { name: 'orchestrator' }, sessionId: 's' },
    ]

    const replay = buildReplayTimeline(source)

    expect(replay.map((event) => event.type)).toEqual(source.map((event) => event.type))
    expect(replay[0]?.time).toBe(0)
    expect(replay[1]?.time).toBe(REPLAY_MILESTONE_INTERVAL_S)
    expect(replay[2]?.time).toBe(REPLAY_MILESTONE_INTERVAL_S + REPLAY_BASE_INTERVAL_S)
    expect(replay[3]?.time).toBe(REPLAY_MILESTONE_INTERVAL_S + REPLAY_BASE_INTERVAL_S + REPLAY_MILESTONE_INTERVAL_S)
  })

  it('adds capped readable holds for text-heavy replay modes', () => {
    const source: SimulationEvent[] = [
      { time: 0, type: 'agent_spawn', payload: { name: 'orchestrator' }, sessionId: 's' },
      { time: 1, type: 'text_burst', payload: { content: 'Readable text. '.repeat(120) }, sessionId: 's' },
      { time: 2, type: 'agent_complete', payload: { name: 'orchestrator' }, sessionId: 's' },
    ]

    const fast = buildReplayTimeline(source, { pacingMode: 'fast' })
    const readable = buildReplayTimeline(source, { pacingMode: 'readable', readableHoldMultiplier: 1 })

    expect(readable.map((event) => event.type)).toEqual(source.map((event) => event.type))
    expect(readable[2].time).toBeGreaterThan(fast[2].time)
  })
})
