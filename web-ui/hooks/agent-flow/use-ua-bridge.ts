'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
import type { SimulationEvent } from '@/lib/agent-flow/agent-types'
import type { SessionInfo, ConnectionStatus } from '@/lib/agent-flow/bridge-types'
import { createGatewayAgentFlowAdapter } from '@/lib/agent-flow/gateway-adapter'
import { fetchSessionDirectory, type SessionDirectoryItem } from '@/lib/sessionDirectory'

interface BridgeHookResult {
  isVSCode: boolean
  connectionStatus: ConnectionStatus
  pendingEvents: readonly SimulationEvent[]
  consumeEvents: () => void
  useMockData: boolean
  bridgeOpenFile: (filePath: string, line?: number) => void
  sessions: SessionInfo[]
  selectedSessionId: string | null
  selectSession: (sessionId: string | null) => void
  flushSessionEvents: (sessionId: string, fromIndex?: number) => void
  getSessionEventCount: (sessionId: string) => number
  selectedSessionIdRef: React.RefObject<string | null>
  sessionsWithActivity: Set<string>
  removeSession: (sessionId: string) => void
}

function parseTimestamp(value: string | number | undefined | null): number | null {
  if (typeof value === 'number' && Number.isFinite(value)) return value
  if (typeof value !== 'string' || !value.trim()) return null
  const parsed = Date.parse(value)
  return Number.isFinite(parsed) ? parsed : null
}

function sessionStatusFromDirectory(item: SessionDirectoryItem): SessionInfo['status'] {
  const raw = String(item.run_status || item.status || '').trim().toLowerCase()
  if (!raw) return 'active'
  if (
    raw === 'active'
    || raw === 'running'
    || raw === 'processing'
    || raw === 'queued'
    || raw === 'pending'
    || raw === 'in_progress'
  ) {
    return 'active'
  }
  return 'completed'
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

  return `Session ${item.session_id.slice(0, 6)}`
}

function toSessionInfo(item: SessionDirectoryItem, now: number): SessionInfo | null {
  const sessionId = String(item.session_id || '').trim()
  if (!sessionId || sessionId === 'global_agent_flow') return null
  const lastActivity =
    parseTimestamp(item.last_activity)
    ?? (typeof item.heartbeat_last === 'number' && Number.isFinite(item.heartbeat_last) ? item.heartbeat_last * 1000 : null)
    ?? now
  return {
    id: sessionId,
    label: sessionLabelFromDirectory(item),
    status: sessionStatusFromDirectory(item),
    startTime: lastActivity,
    lastActivityTime: lastActivity,
  }
}

function sortSessions(sessionList: SessionInfo[]): SessionInfo[] {
  return [...sessionList].sort((a, b) => {
    const aActive = a.status === 'active' ? 1 : 0
    const bActive = b.status === 'active' ? 1 : 0
    if (aActive !== bActive) return bActive - aActive
    return b.lastActivityTime - a.lastActivityTime
  })
}

function mergeSessionLists(current: SessionInfo[], incoming: SessionInfo): SessionInfo[] {
  const next = [...current]
  const index = next.findIndex((session) => session.id === incoming.id)
  if (index === -1) {
    next.push(incoming)
  } else {
    next[index] = {
      ...next[index],
      ...incoming,
      startTime: next[index].startTime ?? incoming.startTime,
      lastActivityTime: Math.max(next[index].lastActivityTime, incoming.lastActivityTime),
    }
  }
  return sortSessions(next)
}

export function useUABridge(): BridgeHookResult {
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('disconnected')
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null)
  const selectedSessionIdRef = useRef<string | null>(null)
  const [sessionsWithActivity, setSessionsWithActivity] = useState<Set<string>>(new Set())

  const pendingEventsRef = useRef<SimulationEvent[]>([])
  const [, setEventVersion] = useState(0)

  const sessionEventsRef = useRef<Map<string, SimulationEvent[]>>(new Map())
  const sessionSwitchPendingRef = useRef(false)
  const dismissedSessionsRef = useRef<Map<string, SessionInfo>>(new Map())
  const adapterRef = useRef(createGatewayAgentFlowAdapter())

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  const consumeEvents = useCallback(() => {
    pendingEventsRef.current.length = 0
  }, [])

  const selectSession = useCallback((sessionId: string | null) => {
    sessionSwitchPendingRef.current = true
    pendingEventsRef.current.length = 0
    selectedSessionIdRef.current = sessionId
    setSelectedSessionId(sessionId)
    if (sessionId) {
      setSessionsWithActivity((prev) => {
        if (!prev.has(sessionId)) return prev
        const next = new Set(prev)
        next.delete(sessionId)
        return next
      })
    }
  }, [])

  const flushSessionEvents = useCallback((sessionId: string, fromIndex = 0) => {
    sessionSwitchPendingRef.current = false
    const buffered = sessionEventsRef.current.get(sessionId) || []
    pendingEventsRef.current.length = 0
    pendingEventsRef.current.push(...buffered.slice(fromIndex))
    setEventVersion((version) => version + 1)
  }, [])

  const getSessionEventCount = useCallback((sessionId: string): number => {
    return sessionEventsRef.current.get(sessionId)?.length ?? 0
  }, [])

  const removeSession = useCallback((sessionId: string) => {
    setSessions((prev) => {
      const existing = prev.find((session) => session.id === sessionId)
      if (existing) dismissedSessionsRef.current.set(sessionId, existing)
      return prev.filter((session) => session.id !== sessionId)
    })
    setSessionsWithActivity((prev) => {
      if (!prev.has(sessionId)) return prev
      const next = new Set(prev)
      next.delete(sessionId)
      return next
    })
  }, [])

  useEffect(() => {
    let cancelled = false

    async function seedSessions(): Promise<void> {
      try {
        const rows = await fetchSessionDirectory(50)
        if (cancelled) return
        const now = Date.now()
        const seeded = sortSessions(
          rows
            .map((row) => toSessionInfo(row, now))
            .filter((row): row is SessionInfo => row !== null),
        )
        if (seeded.length === 0) return
        setSessions((prev) => {
          let next = prev
          for (const session of seeded) {
            next = mergeSessionLists(next, session)
          }
          return next
        })
        if (!selectedSessionIdRef.current) {
          const autoSelected = seeded[0]?.id || null
          if (autoSelected) {
            sessionSwitchPendingRef.current = true
            pendingEventsRef.current.length = 0
            selectedSessionIdRef.current = autoSelected
            setSelectedSessionId(autoSelected)
          }
        }
      } catch {
        // Best-effort discovery only.
      }
    }

    void seedSessions()
    return () => {
      cancelled = true
    }
  }, [])

  const connect = useCallback(() => {
    if (
      wsRef.current
      && (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING)
    ) {
      return
    }

    try {
      const isSecure = window.location.protocol === 'https:'
      const wsProtocol = isSecure ? 'wss:' : 'ws:'
      const wsUrl = `${wsProtocol}//${window.location.host}/ws/agent?session_id=global_agent_flow`

      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = () => {
        setConnectionStatus('watching')
        if (reconnectTimeoutRef.current) {
          clearTimeout(reconnectTimeoutRef.current)
          reconnectTimeoutRef.current = null
        }
      }

      ws.onmessage = (message) => {
        try {
          const parsed = JSON.parse(message.data)
          if (parsed?.type === 'connected') {
            setConnectionStatus('watching')
          }

          const result = adapterRef.current.ingest(parsed)
          const { sessionId, session, events } = result
          if (!sessionId || !session) return

          if (dismissedSessionsRef.current.has(sessionId)) {
            dismissedSessionsRef.current.delete(sessionId)
          }

          setSessions((prev) => mergeSessionLists(prev, session))

          if (!selectedSessionIdRef.current) {
            sessionSwitchPendingRef.current = true
            pendingEventsRef.current.length = 0
            selectedSessionIdRef.current = sessionId
            setSelectedSessionId(sessionId)
          }

          if (events.length > 0) {
            const buffered = sessionEventsRef.current.get(sessionId) || []
            buffered.push(...events)
            sessionEventsRef.current.set(sessionId, buffered)
          }

          const selected = selectedSessionIdRef.current
          if (events.length > 0 && selected && sessionId === selected && !sessionSwitchPendingRef.current) {
            pendingEventsRef.current.push(...events)
            setEventVersion((version) => version + 1)
          } else if (events.length > 0 && sessionId !== selected) {
            setSessionsWithActivity((prev) => {
              if (prev.has(sessionId)) return prev
              const next = new Set(prev)
              next.add(sessionId)
              return next
            })
          }
        } catch (error) {
          console.error('[UA Bridge] Invalid message received', message.data, error)
        }
      }

      ws.onclose = () => {
        setConnectionStatus('disconnected')
        wsRef.current = null
        reconnectTimeoutRef.current = setTimeout(connect, 3000)
      }

      ws.onerror = (error) => {
        console.error('[UA Bridge] WebSocket error', error)
      }
    } catch (error) {
      console.error('[UA Bridge] Exception during connect', error)
      setConnectionStatus('disconnected')
    }
  }, [])

  useEffect(() => {
    connect()
    return () => {
      if (wsRef.current) {
        wsRef.current.close()
      }
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }
    }
  }, [connect])

  const bridgeOpenFile = useCallback((_filePath: string, _line?: number) => {
    // Standalone dashboard cannot open local editor buffers.
  }, [])

  return {
    isVSCode: true,
    connectionStatus,
    pendingEvents: pendingEventsRef.current,
    consumeEvents,
    useMockData: false,
    bridgeOpenFile,
    sessions,
    selectedSessionId,
    selectSession,
    flushSessionEvents,
    getSessionEventCount,
    selectedSessionIdRef,
    sessionsWithActivity,
    removeSession,
  }
}
