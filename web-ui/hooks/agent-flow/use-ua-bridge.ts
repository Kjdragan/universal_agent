'use client'

import { useCallback, useEffect, useRef, useState } from 'react'
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

export function useUABridge(): BridgeHookResult {
  const selectedSessionIdRef = useRef<string | null>(null)
  const [connectionStatus, setConnectionStatus] = useState<ConnectionStatus>('disconnected')
  const [sessionsWithActivity] = useState<Set<string>>(new Set())
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null)
  
  // A ref queue for events pushed by websocket
  const eventQueueRef = useRef<SimulationEvent[]>([])
  const [pendingEvents, setPendingEvents] = useState<SimulationEvent[]>([])

  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<NodeJS.Timeout | null>(null)

  const consumeEvents = useCallback(() => {
    // Note: The UI layer currently manages event consumption by pulling from pendingEvents.
    if (pendingEvents.length > 0) {
      setPendingEvents([])
    }
  }, [pendingEvents])

  const connect = useCallback(() => {
    if (wsRef.current && (wsRef.current.readyState === WebSocket.OPEN || wsRef.current.readyState === WebSocket.CONNECTING)) {
      return
    }

    try {
      const isSecure = window.location.protocol === 'https:';
      const wsProtocol = isSecure ? 'wss:' : 'ws:';
      // In standalone dashboard, this targets the nextjs rewrites /api/v1/agent/stream
      // which forwards to gateway or local server.
      const wsUrl = `${wsProtocol}//${window.location.host}/api/dashboard/gateway/api/v1/agent/stream`
      
      const ws = new WebSocket(wsUrl)
      wsRef.current = ws

      ws.onopen = () => {
        console.log('[UA Bridge] WebSocket Connected')
        setConnectionStatus('connected')
        if (reconnectTimeoutRef.current) {
          clearTimeout(reconnectTimeoutRef.current)
          reconnectTimeoutRef.current = null
        }
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data)
          
          if (data.type === 'connected') {
            const sid = data.data?.session?.session_id
            if (sid) {
              setSessions([{
                id: sid,
                label: `Session ${sid.slice(0,6)}`,
                status: 'active',
                startTime: Date.now(),
                lastActivityTime: Date.now()
              }])
              
              if (!selectedSessionIdRef.current) {
                selectedSessionIdRef.current = sid
                setSelectedSessionId(sid)
              }
            }
          }
          
          // Map backend event formats into SimulationEvent formats
          const mappedEvent = mapBackendEventToSimulation(data)
          if (mappedEvent) {
             eventQueueRef.current.push(mappedEvent)
             // Force react flush queue
             setPendingEvents([...eventQueueRef.current])
          }
          
        } catch (e) {
          console.error('[UA Bridge] Invalid message received', event.data, e)
        }
      }

      ws.onclose = () => {
        console.log('[UA Bridge] WebSocket Disconnected')
        setConnectionStatus('disconnected')
        wsRef.current = null
        // Reconnect with backoff
        reconnectTimeoutRef.current = setTimeout(connect, 3000)
      }

      ws.onerror = (err) => {
        console.error('[UA Bridge] WebSocket Error:', err)
      }
    } catch (err) {
      console.error('[UA Bridge] Exception during connect:', err)
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
      // Stubbed: Dashboard visualization doesn't have local VSCode to open files into
  }, [])
  const selectSession = useCallback((sessionId: string | null) => {
      selectedSessionIdRef.current = sessionId
      setSelectedSessionId(sessionId)
  }, [])
  const flushSessionEvents = useCallback((_sessionId: string, _fromIndex?: number) => {}, [])
  const getSessionEventCount = useCallback((_sessionId: string) => 0, [])
  const removeSession = useCallback((_sessionId: string) => {}, [])

  return {
    isVSCode: false,
    connectionStatus,
    pendingEvents,
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

// Very basic mapping from the universal_agent event stream -> the Holographic Agent visualizer SimulationEvents
function mapBackendEventToSimulation(backendData: any): SimulationEvent | null {
   if (!backendData || !backendData.type) return null;
   
   const tType = backendData.type;
   const payload = backendData.data || {};
   const time = Date.now();
   const sessionId = payload.session_id;

   if (tType === 'iteration_start' || tType === 'system_event') {
       return { time, type: 'agent_spawn', payload: { ...payload, agentName: payload.agent_id || 'UA Agent', content: typeof payload.text === 'string' ? payload.text : JSON.stringify(payload) }, sessionId }
   }
   
   if (tType === 'plan_update' || tType === 'status') {
       return { time, type: 'message', payload: { content: typeof payload.text === 'string' ? payload.text : JSON.stringify(payload), role: 'assistant', agentId: payload.agent_id || 'main' }, sessionId }
   }
   
   // Agent finished
   if (tType === 'iteration_end' || tType === 'query_complete') {
       return { time, type: 'agent_complete', payload: { agentId: payload.agent_id || 'main' }, sessionId }
   }

   // Optional, fallback map
   return null;
}
