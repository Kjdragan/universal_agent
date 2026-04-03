'use client'

/**
 * Mock Bridge — replaces the VS Code bridge for standalone use in UA dashboard.
 *
 * In Phase 1, this simply returns useMockData=true so the visualizer
 * plays the built-in demo scenario. In Phase 2, this will connect to
 * the UA gateway WebSocket to receive real agent events.
 */

import { useCallback, useRef, useState } from 'react'
import type { SimulationEvent } from '@/lib/agent-flow/agent-types'
import type { SessionInfo, ConnectionStatus } from '@/lib/agent-flow/bridge-types'

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

/**
 * Provides the same interface as useVSCodeBridge but always returns
 * mock-data mode. The visualizer will play its built-in demo scenario.
 */
export function useVSCodeBridge(): BridgeHookResult {
  const selectedSessionIdRef = useRef<string | null>(null)
  const [sessionsWithActivity] = useState<Set<string>>(new Set())

  const consumeEvents = useCallback(() => {}, [])
  const bridgeOpenFile = useCallback((_filePath: string, _line?: number) => {}, [])
  const selectSession = useCallback((_sessionId: string | null) => {}, [])
  const flushSessionEvents = useCallback((_sessionId: string, _fromIndex?: number) => {}, [])
  const getSessionEventCount = useCallback((_sessionId: string) => 0, [])
  const removeSession = useCallback((_sessionId: string) => {}, [])

  return {
    isVSCode: false,
    connectionStatus: 'disconnected',
    pendingEvents: [],
    consumeEvents,
    useMockData: true,
    bridgeOpenFile,
    sessions: [],
    selectedSessionId: null,
    selectSession,
    flushSessionEvents,
    getSessionEventCount,
    selectedSessionIdRef,
    sessionsWithActivity,
    removeSession,
  }
}
