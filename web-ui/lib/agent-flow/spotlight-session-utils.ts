import type { SimulationEvent } from './agent-types'
import type {
  SpotlightArchiveStatus,
  SpotlightLaneHint,
  SpotlightSelectionSource,
  SpotlightSessionArchive,
  SpotlightMode,
} from './spotlight-types'

export const MEANINGFUL_EVENT_TYPES = new Set<SimulationEvent['type']>([
  'agent_spawn',
  'message',
  'tool_call_start',
  'tool_call_end',
  'subagent_dispatch',
  'subagent_return',
  'permission_requested',
  'agent_complete',
  'text_burst',
  'phase_transition',
  'artifact_emitted',
  'error_recovery',
])

export const GREATEST_HITS_MIN_EVENTS = 4

export function isMeaningfulEventType(type: SimulationEvent['type']): boolean {
  return MEANINGFUL_EVENT_TYPES.has(type)
}

export function laneHintFromMetadata(sessionId: string, hints?: {
  source?: string
  channel?: string
  triggerSource?: string
}): SpotlightLaneHint {
  const values = [
    hints?.source,
    hints?.channel,
    hints?.triggerSource,
    sessionId,
  ]
    .map((value) => String(value || '').trim().toLowerCase())
    .filter(Boolean)

  if (values.some((value) => value.includes('email') || value.includes('mail'))) return 'email'
  if (values.some((value) => value.includes('heartbeat'))) return 'heartbeat'
  if (values.some((value) => value === 'chat' || value.startsWith('session_'))) return 'chat'
  return 'system'
}

export function normalizeEventTimeline(events: SimulationEvent[]): SimulationEvent[] {
  if (events.length === 0) return []
  const baseTime = events[0]?.time || 0
  return events.map((event) => ({
    ...event,
    time: Math.max(0, event.time - baseTime),
  }))
}

export function deriveDurationMs(firstMeaningfulEventAt: number | null, lastMeaningfulEventAt: number | null): number {
  if (firstMeaningfulEventAt == null || lastMeaningfulEventAt == null) return 0
  return Math.max(0, Math.round((lastMeaningfulEventAt - firstMeaningfulEventAt) * 1000))
}

export function deriveRecentSessionIds(archivesBySessionId: Record<string, SpotlightSessionArchive>): string[] {
  return Object.values(archivesBySessionId)
    .filter((archive) => archive.meaningfulEventCount > 0)
    .sort((a, b) => {
      if (a.status !== b.status) return a.status === 'active' ? -1 : 1
      return (b.lastMeaningfulEventAt || 0) - (a.lastMeaningfulEventAt || 0)
    })
    .slice(0, 5)
    .map((archive) => archive.sessionId)
}

export function deriveGreatestHitSessionIds(archivesBySessionId: Record<string, SpotlightSessionArchive>): string[] {
  return Object.values(archivesBySessionId)
    .filter((archive) => archive.status === 'completed' && archive.meaningfulEventCount >= GREATEST_HITS_MIN_EVENTS)
    .sort((a, b) => {
      if (a.durationMs !== b.durationMs) return b.durationMs - a.durationMs
      return (b.completedAt || 0) - (a.completedAt || 0)
    })
    .slice(0, 5)
    .map((archive) => archive.sessionId)
}

export function resolveSpotlightSelection(args: {
  mode: SpotlightMode
  selectionSource: SpotlightSelectionSource
  selectedSessionId: string | null
  recentSessionIds: string[]
  greatestHitSessionIds: string[]
  archivesBySessionId: Record<string, SpotlightSessionArchive>
}): {
  mode: SpotlightMode
  selectionSource: SpotlightSelectionSource
  selectedSessionId: string | null
  replayLoopIndex: number
} {
  const {
    mode,
    selectionSource,
    selectedSessionId,
    recentSessionIds,
    greatestHitSessionIds,
    archivesBySessionId,
  } = args

  const currentList = mode === 'recent' ? recentSessionIds : greatestHitSessionIds
  const hasValidSelection = Boolean(
    selectedSessionId
    && currentList.includes(selectedSessionId)
    && archivesBySessionId[selectedSessionId],
  )

  if (selectionSource === 'manual' && hasValidSelection) {
    return {
      mode,
      selectionSource,
      selectedSessionId,
      replayLoopIndex: Math.max(0, greatestHitSessionIds.indexOf(selectedSessionId || '')),
    }
  }

  if (selectionSource === 'auto' && mode === 'greatest_hits' && hasValidSelection) {
    return {
      mode,
      selectionSource,
      selectedSessionId,
      replayLoopIndex: Math.max(0, greatestHitSessionIds.indexOf(selectedSessionId || '')),
    }
  }

  if (mode === 'recent') {
    if (recentSessionIds.length > 0) {
      return {
        mode,
        selectionSource: 'auto',
        selectedSessionId: recentSessionIds[0],
        replayLoopIndex: 0,
      }
    }
    if (greatestHitSessionIds.length > 0) {
      return {
        mode: 'greatest_hits',
        selectionSource: 'auto',
        selectedSessionId: greatestHitSessionIds[0],
        replayLoopIndex: 0,
      }
    }
    return {
      mode,
      selectionSource: 'auto',
      selectedSessionId: null,
      replayLoopIndex: 0,
    }
  }

  if (greatestHitSessionIds.length > 0) {
    return {
      mode,
      selectionSource: 'auto',
      selectedSessionId: greatestHitSessionIds[0],
      replayLoopIndex: 0,
    }
  }

  if (recentSessionIds.length > 0) {
    return {
      mode: 'recent',
      selectionSource: 'auto',
      selectedSessionId: recentSessionIds[0],
      replayLoopIndex: 0,
    }
  }

  return {
    mode,
    selectionSource: 'auto',
    selectedSessionId: null,
    replayLoopIndex: 0,
  }
}

export function mergeArchiveEvents(
  currentEvents: SimulationEvent[],
  nextEvents: SimulationEvent[],
): SimulationEvent[] {
  if (nextEvents.length === 0) return currentEvents
  return [...currentEvents, ...nextEvents]
}

export function archiveStatusFromSignals(args: {
  currentStatus: SpotlightArchiveStatus | null
  sessionStatusHint?: string
  rawEventType?: string
}): SpotlightArchiveStatus {
  const raw = String(args.sessionStatusHint || '').trim().toLowerCase()
  if (
    raw === 'complete'
    || raw === 'completed'
    || raw === 'failed'
    || raw === 'cancelled'
    || raw === 'archived'
    || args.rawEventType === 'iteration_end'
    || args.rawEventType === 'query_complete'
  ) {
    return 'completed'
  }
  return args.currentStatus === 'completed' ? 'completed' : 'active'
}
