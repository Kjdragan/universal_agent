'use client'

import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { SimulationEvent } from '@/lib/agent-flow/agent-types'
import type { ConnectionStatus } from '@/lib/agent-flow/bridge-types'
import { createGatewayAgentFlowAdapter } from '@/lib/agent-flow/gateway-adapter'
import { buildReplayTimeline } from '@/lib/agent-flow/replay-timeline'
import {
  archiveStatusFromSignals,
  deriveDurationMs,
  isMeaningfulEventType,
  laneHintFromMetadata,
  mergeArchiveEvents,
  normalizeEventTimeline,
  resolveSpotlightSelection,
} from '@/lib/agent-flow/spotlight-session-utils'
import { ensureAgentFlowSpotlightSync, useAgentFlowSpotlightStore } from '@/lib/agent-flow/spotlight-store'
import type {
  SpotlightMode,
  SpotlightPlaybackMode,
  SpotlightSelectionSource,
  SpotlightSessionArchive,
} from '@/lib/agent-flow/spotlight-types'
import { fetchSessionDirectory, type SessionDirectoryItem } from '@/lib/sessionDirectory'

interface BridgeHookResult {
  connectionStatus: ConnectionStatus
  pendingEvents: readonly SimulationEvent[]
  consumeEvents: () => void
  useMockData: boolean
  archivesBySessionId: Record<string, SpotlightSessionArchive>
  recentSessionIds: string[]
  greatestHitSessionIds: string[]
  mode: SpotlightMode
  selectedSessionId: string | null
  selectedArchive: SpotlightSessionArchive | null
  selectedIsLive: boolean
  selectedPlaybackEvents: SimulationEvent[]
  selectedPlaybackMode: SpotlightPlaybackMode
  selectionSource: SpotlightSelectionSource
  currentReplayGeneration: number
  currentReplayLoopIndex: number
  currentSpotlightTitle: string
  currentSpotlightIsLive: boolean
  setMode: (mode: SpotlightMode) => void
  selectSession: (sessionId: string | null, source?: SpotlightSelectionSource) => void
  clearManualSelection: () => void
  advanceGreatestHitsLoop: () => void
  restartReplay: () => void
}

type DirectoryHint = {
  title: string
  statusHint: string
  laneHint: SpotlightSessionArchive['laneHint']
}

function parseTimestampSeconds(value: string | number | undefined | null): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value > 10_000_000_000 ? value / 1000 : value
  if (typeof value !== 'string' || !value.trim()) return null
  const parsed = Date.parse(value)
  if (!Number.isFinite(parsed)) return null
  return parsed / 1000
}

function sessionLabelFromDirectory(item: SessionDirectoryItem): string {
  const description = String(item.description || '').trim()
  if (description) return description

  const workspace = String(item.workspace_dir || '').trim()
  if (workspace) {
    const parts = workspace.replace(/\\/g, '/').split('/').filter(Boolean)
    const tail = parts[parts.length - 1]
    if (tail) return tail
  }

  return `Session ${String(item.session_id || '').slice(0, 6)}`
}

function sessionStatusFromDirectory(item: SessionDirectoryItem): SpotlightSessionArchive['status'] {
  const raw = String(item.run_status || item.status || '').trim().toLowerCase()
  if (
    raw === 'complete'
    || raw === 'completed'
    || raw === 'failed'
    || raw === 'cancelled'
    || raw === 'archived'
  ) {
    return 'completed'
  }
  return 'active'
}

function createArchiveFromDirectory(item: SessionDirectoryItem): SpotlightSessionArchive {
  return {
    sessionId: String(item.session_id || '').trim(),
    title: sessionLabelFromDirectory(item),
    status: sessionStatusFromDirectory(item),
    laneHint: laneHintFromMetadata(String(item.session_id || ''), {
      source: item.source,
      channel: item.channel,
      triggerSource: item.trigger_source,
    }),
    firstMeaningfulEventAt: parseTimestampSeconds(item.last_activity) ?? null,
    lastMeaningfulEventAt: parseTimestampSeconds(item.last_activity) ?? null,
    completedAt: sessionStatusFromDirectory(item) === 'completed' ? (parseTimestampSeconds(item.last_activity) ?? null) : null,
    durationMs: 0,
    normalizedEvents: [],
    meaningfulEventCount: 0,
  }
}

function mergeArchiveUpdate(args: {
  existing: SpotlightSessionArchive | undefined
  sessionId: string
  title: string
  laneHint: SpotlightSessionArchive['laneHint']
  status: SpotlightSessionArchive['status']
  events: SimulationEvent[]
}): SpotlightSessionArchive {
  const existing = args.existing
  const firstMeaningfulEventAt = existing?.firstMeaningfulEventAt ?? null
  const lastMeaningfulEventAt = existing?.lastMeaningfulEventAt ?? null
  let nextFirstMeaningfulEventAt = firstMeaningfulEventAt
  let nextLastMeaningfulEventAt = lastMeaningfulEventAt
  let nextMeaningfulCount = existing?.meaningfulEventCount ?? 0

  for (const event of args.events) {
    if (!isMeaningfulEventType(event.type)) continue
    nextMeaningfulCount += 1
    const eventTime = event.time
    nextFirstMeaningfulEventAt = nextFirstMeaningfulEventAt == null ? eventTime : Math.min(nextFirstMeaningfulEventAt, eventTime)
    nextLastMeaningfulEventAt = nextLastMeaningfulEventAt == null ? eventTime : Math.max(nextLastMeaningfulEventAt, eventTime)
  }

  const mergedEvents = normalizeEventTimeline(mergeArchiveEvents(existing?.normalizedEvents || [], args.events))
  const completedAt = args.status === 'completed'
    ? (nextLastMeaningfulEventAt ?? existing?.completedAt ?? (args.events[args.events.length - 1]?.time ?? null))
    : null

  return {
    sessionId: args.sessionId,
    title: args.title || existing?.title || `Session ${args.sessionId.slice(0, 6)}`,
    status: args.status,
    laneHint: args.laneHint || existing?.laneHint || 'system',
    firstMeaningfulEventAt: nextFirstMeaningfulEventAt,
    lastMeaningfulEventAt: nextLastMeaningfulEventAt,
    completedAt,
    durationMs: deriveDurationMs(nextFirstMeaningfulEventAt, nextLastMeaningfulEventAt),
    normalizedEvents: mergedEvents,
    meaningfulEventCount: nextMeaningfulCount,
  }
}

export function useUABridge(): BridgeHookResult {
  ensureAgentFlowSpotlightSync()

  const connectionStatus = useAgentFlowSpotlightStore((state) => state.connectionStatus)
  const archivesBySessionId = useAgentFlowSpotlightStore((state) => state.archivesBySessionId)
  const recentSessionIds = useAgentFlowSpotlightStore((state) => state.recentSessionIds)
  const greatestHitSessionIds = useAgentFlowSpotlightStore((state) => state.greatestHitSessionIds)
  const mode = useAgentFlowSpotlightStore((state) => state.mode)
  const selectedSessionId = useAgentFlowSpotlightStore((state) => state.selectedSessionId)
  const selectionSource = useAgentFlowSpotlightStore((state) => state.selectionSource)
  const currentReplayGeneration = useAgentFlowSpotlightStore((state) => state.currentReplayGeneration)
  const currentReplayLoopIndex = useAgentFlowSpotlightStore((state) => state.currentReplayLoopIndex)
  const currentSpotlightTitle = useAgentFlowSpotlightStore((state) => state.currentSpotlightTitle)
  const currentSpotlightIsLive = useAgentFlowSpotlightStore((state) => state.currentSpotlightIsLive)
  const setMode = useAgentFlowSpotlightStore((state) => state.setMode)
  const selectSession = useAgentFlowSpotlightStore((state) => state.selectSession)
  const clearManualSelection = useAgentFlowSpotlightStore((state) => state.clearManualSelection)

  const pendingEventsRef = useRef<SimulationEvent[]>([])
  const [, setEventVersion] = useState(0)
  const adapterRef = useRef(createGatewayAgentFlowAdapter())
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)
  const directoryHintsRef = useRef<Map<string, DirectoryHint>>(new Map())
  const allowReconnectRef = useRef(true)

  const selectedArchive = selectedSessionId ? (archivesBySessionId[selectedSessionId] || null) : null
  const selectedIsLive = selectedArchive?.status === 'active'
  const selectedPlaybackMode: SpotlightPlaybackMode = selectedIsLive ? 'live' : 'replay'
  const selectedPlaybackEvents = useMemo(() => {
    if (!selectedArchive) return []
    if (selectedArchive.status === 'active') return selectedArchive.normalizedEvents
    return buildReplayTimeline(selectedArchive.normalizedEvents)
  }, [selectedArchive])

  useEffect(() => {
    pendingEventsRef.current.length = 0
    setEventVersion((version) => version + 1)
  }, [selectedSessionId, selectedPlaybackMode, currentReplayGeneration])

  useEffect(() => {
    useAgentFlowSpotlightStore.getState().setSpotlightMeta(selectedArchive?.title || '', selectedIsLive)
  }, [selectedArchive, selectedIsLive])

  useEffect(() => {
    const next = resolveSpotlightSelection({
      mode,
      selectionSource,
      selectedSessionId,
      recentSessionIds,
      greatestHitSessionIds,
      archivesBySessionId,
    })

    const state = useAgentFlowSpotlightStore.getState()
    if (state.mode !== next.mode) state.setMode(next.mode)
    if (state.selectionSource !== next.selectionSource || state.selectedSessionId !== next.selectedSessionId) {
      state.selectSession(next.selectedSessionId, next.selectionSource)
    }
    if (state.currentReplayLoopIndex !== next.replayLoopIndex) {
      state.setReplayLoopIndex(next.replayLoopIndex)
    }
  }, [
    archivesBySessionId,
    greatestHitSessionIds,
    mode,
    recentSessionIds,
    selectedSessionId,
    selectionSource,
  ])

  useEffect(() => {
    let cancelled = false

    async function seedSessions(): Promise<void> {
      try {
        const rows = await fetchSessionDirectory(50)
        if (cancelled) return
        const archives = { ...useAgentFlowSpotlightStore.getState().archivesBySessionId }
        for (const row of rows) {
          const sessionId = String(row.session_id || '').trim()
          if (!sessionId || sessionId === 'global_agent_flow') continue
          directoryHintsRef.current.set(sessionId, {
            title: sessionLabelFromDirectory(row),
            statusHint: String(row.run_status || row.status || '').trim(),
            laneHint: laneHintFromMetadata(sessionId, {
              source: row.source,
              channel: row.channel,
              triggerSource: row.trigger_source,
            }),
          })
          archives[sessionId] = {
            ...(archives[sessionId] || createArchiveFromDirectory(row)),
            title: sessionLabelFromDirectory(row),
            status: sessionStatusFromDirectory(row),
            laneHint: laneHintFromMetadata(sessionId, {
              source: row.source,
              channel: row.channel,
              triggerSource: row.trigger_source,
            }),
          }
        }
        useAgentFlowSpotlightStore.getState().hydrateArchives(archives)
      } catch {
        // Best-effort discovery only.
      }
    }

    void seedSessions()
    return () => {
      cancelled = true
    }
  }, [])

  const consumeEvents = useCallback(() => {
    pendingEventsRef.current.length = 0
  }, [])

  const connect = useCallback(function connectImpl() {
    if (!allowReconnectRef.current) return
    if (
      wsRef.current
      && (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING)
    ) {
      return
    }

    try {
      const isSecure = window.location.protocol === 'https:'
      const wsProtocol = isSecure ? 'wss:' : 'ws:'
      let wsHost = window.location.host
      if (wsHost.startsWith('localhost:') || wsHost.startsWith('127.0.0.1:')) {
        // In local development, Next.js Turbopack rewrites do not support WebSocket proxying properly
        // Therefore connect directly to the API server hosting the WS endpoint
        wsHost = 'localhost:8001'
      }
      const wsUrl = `${wsProtocol}//${wsHost}/ws/agent?session_id=global_agent_flow`
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = () => {
        if (!allowReconnectRef.current) return
        useAgentFlowSpotlightStore.getState().setConnectionStatus('watching')
        if (reconnectTimeoutRef.current) {
          clearTimeout(reconnectTimeoutRef.current)
          reconnectTimeoutRef.current = null
        }
      }

      ws.onmessage = (message) => {
        try {
          const parsed = JSON.parse(message.data)
          if (parsed?.type === 'connected') {
            useAgentFlowSpotlightStore.getState().setConnectionStatus('watching')
            return
          }

          const result = adapterRef.current.ingest(parsed)
          const { sessionId, session, events } = result
          if (!sessionId || !session) return

          const hint = directoryHintsRef.current.get(sessionId)
          const sessionStatus = archiveStatusFromSignals({
            currentStatus: useAgentFlowSpotlightStore.getState().archivesBySessionId[sessionId]?.status ?? null,
            sessionStatusHint: hint?.statusHint || session.status,
            rawEventType: String(parsed?.type || ''),
          })

          const existingArchive = useAgentFlowSpotlightStore.getState().archivesBySessionId[sessionId]
          const previousEventCount = existingArchive?.normalizedEvents.length ?? 0
          const archive = mergeArchiveUpdate({
            existing: existingArchive,
            sessionId,
            title: hint?.title || session.label,
            laneHint: hint?.laneHint || laneHintFromMetadata(sessionId),
            status: sessionStatus,
            events,
          })

          useAgentFlowSpotlightStore.getState().upsertArchive(archive)

          if (
            events.length > 0
            && useAgentFlowSpotlightStore.getState().selectedSessionId === sessionId
            && archive.status === 'active'
          ) {
            pendingEventsRef.current.push(...archive.normalizedEvents.slice(previousEventCount))
            setEventVersion((version) => version + 1)
          }
        } catch (error) {
          console.error('[UA Bridge] Invalid message received', message.data, error)
        }
      }

      ws.onclose = () => {
        useAgentFlowSpotlightStore.getState().setConnectionStatus('disconnected')
        wsRef.current = null
        if (!allowReconnectRef.current) return
        reconnectTimeoutRef.current = setTimeout(connectImpl, 3000)
      }

      ws.onerror = (error) => {
        console.error('[UA Bridge] WebSocket error', error)
      }
    } catch (error) {
      console.error('[UA Bridge] Exception during connect', error)
      useAgentFlowSpotlightStore.getState().setConnectionStatus('disconnected')
    }
  }, [])

  useEffect(() => {
    allowReconnectRef.current = true
    connect()
    return () => {
      allowReconnectRef.current = false
      if (wsRef.current) {
        wsRef.current.close()
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }
    }
  }, [connect])

  const advanceGreatestHitsLoop = useCallback(() => {
    const state = useAgentFlowSpotlightStore.getState()
    if (state.greatestHitSessionIds.length === 0) return
    const nextIndex = (state.currentReplayLoopIndex + 1) % state.greatestHitSessionIds.length
    state.setReplayLoopIndex(nextIndex)
    state.selectSession(state.greatestHitSessionIds[nextIndex], 'auto')
    state.bumpReplayGeneration()
  }, [])

  const restartReplay = useCallback(() => {
    useAgentFlowSpotlightStore.getState().bumpReplayGeneration()
  }, [])

  return {
    connectionStatus,
    pendingEvents: pendingEventsRef.current,
    consumeEvents,
    useMockData: false,
    archivesBySessionId,
    recentSessionIds,
    greatestHitSessionIds,
    mode,
    selectedSessionId,
    selectedArchive,
    selectedIsLive,
    selectedPlaybackEvents,
    selectedPlaybackMode,
    selectionSource,
    currentReplayGeneration,
    currentReplayLoopIndex,
    currentSpotlightTitle,
    currentSpotlightIsLive,
    setMode,
    selectSession,
    clearManualSelection,
    advanceGreatestHitsLoop,
    restartReplay,
  }
}
