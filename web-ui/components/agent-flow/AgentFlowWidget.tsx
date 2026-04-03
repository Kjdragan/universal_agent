"use client"

/**
 * AgentFlowWidget — Reusable, multi-mode agent activity visualizer.
 *
 * Modes:
 *   "full"    — Dedicated tab, full viewport, all panels visible
 *   "compact" — Panel-sized view (e.g., chat page sidebar), simplified controls
 *   "mini"    — Small card widget (e.g., dashboard overview), auto-fit, click-to-expand
 *
 * This is the main entry point. Import it anywhere:
 *   import { AgentFlowWidget } from "@/components/agent-flow/AgentFlowWidget"
 *   <AgentFlowWidget mode="full" />
 */

import { useState, useCallback, useMemo, useEffect, useLayoutEffect, useRef } from "react"
import { useRouter } from "next/navigation"
import { useAgentSimulation } from "@/hooks/agent-flow/use-agent-simulation"
import { useUABridge } from "@/hooks/agent-flow/use-ua-bridge"
import { useSelectionState } from "@/hooks/agent-flow/use-selection-state"
import { useKeyboardShortcuts } from "@/hooks/agent-flow/use-keyboard-shortcuts"
import { AgentCanvas } from "./canvas"
import { ControlBar } from "./control-bar"
import { AgentDetailCard } from "./agent-detail-card"
import { GlassContextMenu } from "./glass-context-menu"
import { ToolDetailPopup } from "./tool-detail-popup"
import { DiscoveryDetailPopup } from "./discovery-detail-popup"
import { FileAttentionPanel } from "./file-attention-panel"
import { TimelinePanel } from "./timeline-panel"
import { AgentChatPanel } from "./chat-panel"
import { SessionTranscriptPanel } from "./session-transcript-panel"
import { OpenFileProvider } from "./tool-content-renderer"
import { stopPropagationHandlers } from "./shared-ui"
import { TimelineEvent, TIMING } from "@/lib/agent-flow/agent-types"
import { COLORS } from "@/lib/agent-flow/colors"
import { MOCK_DURATION } from "@/lib/agent-flow/mock-scenario"
import { MessageFeedPanel } from "./message-feed-panel"
import { TopBar } from "./top-bar"
import { useAudioEffects } from "@/hooks/agent-flow/use-audio-effects"

export type AgentFlowMode = "full" | "compact" | "mini"

interface AgentFlowWidgetProps {
  mode?: AgentFlowMode
  /** Optional CSS class for the container */
  className?: string
  /** Callback when mini widget is clicked (default: navigate to full tab) */
  onExpand?: () => void
}

export function AgentFlowWidget({
  mode = "full",
  className = "",
  onExpand,
}: AgentFlowWidgetProps) {
  const router = useRouter()
  const bridge = useUABridge()

  const {
    frameRef,
    agents,
    toolCalls,
    particles,
    edges,
    discoveries,
    fileAttention,
    timelineEntries,
    currentTime,
    isPlaying,
    speed,
    maxTimeReached,
    conversations,
    play,
    pause,
    restart,
    setSpeed,
    seekToTime,
    updateAgentPosition,
    saveSnapshot,
    restoreSnapshot,
  } = useAgentSimulation({
    useMockData: bridge.useMockData,
    externalEvents: bridge.pendingEvents,
    onExternalEventsConsumed: bridge.consumeEvents,
    sessionFilter: bridge.selectedSessionId,
    sessionFilterRef: bridge.selectedSessionIdRef,
  })

  const selection = useSelectionState({ agents, toolCalls, discoveries })

  const [showStats, setShowStats] = useState(false)
  const [showHexGrid, setShowHexGrid] = useState(true)
  const [showCostOverlay, setShowCostOverlay] = useState(false)
  const [showTimeline, setShowTimeline] = useState(false)
  const [showFileAttention, setShowFileAttention] = useState(false)
  const [showTranscript, setShowTranscript] = useState(false)

  const toggleExclusivePanel = useCallback((panel: 'files' | 'transcript' | 'cost') => {
    setShowFileAttention(prev => panel === 'files' ? !prev : false)
    setShowTranscript(prev => panel === 'transcript' ? !prev : false)
    setShowCostOverlay(prev => panel === 'cost' ? !prev : false)
  }, [])

  const [zoomToFitTrigger, setZoomToFitTrigger] = useState(0)
  const [isReviewing, setIsReviewing] = useState(false)
  const { isMuted, seekingRef, handleToggleMute } = useAudioEffects(agents, toolCalls, isReviewing)

  // Auto-play on mount
  useEffect(() => {
    const timer = setTimeout(() => play(), TIMING.autoPlayDelayMs)
    return () => clearTimeout(timer)
  }, [play])

  // Per-session state cache
  const sessionCacheRef = useRef<Map<string, { snapshot: ReturnType<typeof saveSnapshot>; eventCount: number }>>(new Map())
  const prevSelectedRef = useRef<string | null>(null)
  useLayoutEffect(() => {
    if (bridge.selectedSessionId && bridge.selectedSessionId !== prevSelectedRef.current) {
      if (prevSelectedRef.current !== null) {
        sessionCacheRef.current.set(prevSelectedRef.current, {
          snapshot: saveSnapshot(),
          eventCount: bridge.getSessionEventCount(prevSelectedRef.current),
        })
      }
      const cached = sessionCacheRef.current.get(bridge.selectedSessionId)
      if (cached) {
        restoreSnapshot(cached.snapshot)
        bridge.flushSessionEvents(bridge.selectedSessionId, cached.eventCount)
      } else {
        restart()
        bridge.flushSessionEvents(bridge.selectedSessionId)
      }
      prevSelectedRef.current = bridge.selectedSessionId
    }
  }, [bridge.selectedSessionId, restart, bridge.flushSessionEvents, saveSnapshot, restoreSnapshot, bridge.getSessionEventCount])

  // Timeline events
  const timelineCacheRef = useRef<{
    counts: Map<string, number>
    events: TimelineEvent[]
    idCounter: number
  }>({ counts: new Map(), events: [], idCounter: 0 })

  const timelineEvents = useMemo((): TimelineEvent[] => {
    const cache = timelineCacheRef.current
    let appended = false
    for (const [agentId, msgs] of conversations) {
      const prevLen = cache.counts.get(agentId) ?? 0
      if (msgs.length > prevLen) {
        for (let i = prevLen; i < msgs.length; i++) {
          const msg = msgs[i]
          cache.events.push({
            id: `event-${cache.idCounter++}`,
            type: msg.type === 'tool_call' ? 'tool_call' : msg.type === 'tool_result' ? 'tool_result' : 'message',
            label: msg.content.slice(0, 20),
            timestamp: msg.timestamp,
            nodeId: agentId,
          })
        }
        cache.counts.set(agentId, msgs.length)
        appended = true
      }
    }
    if (appended) cache.events.sort((a, b) => a.timestamp - b.timestamp)
    return cache.events
  }, [conversations])

  // Play/pause
  const handlePlayPause = useCallback(() => {
    if (isPlaying) {
      pause()
      setIsReviewing(true)
    } else {
      play()
    }
  }, [isPlaying, play, pause])

  const handleEnterReview = useCallback(() => {
    pause()
    setIsReviewing(true)
  }, [pause])

  const resumeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const handleResumeLive = useCallback(() => {
    setIsReviewing(false)
    seekToTime(maxTimeReached)
    setZoomToFitTrigger(n => n + 1)
    if (resumeTimerRef.current) clearTimeout(resumeTimerRef.current)
    resumeTimerRef.current = setTimeout(() => { resumeTimerRef.current = null; play() }, TIMING.resumeLiveDelayMs)
  }, [seekToTime, maxTimeReached, play])
  useEffect(() => () => { if (resumeTimerRef.current) clearTimeout(resumeTimerRef.current) }, [])

  const handleRestart = useCallback(() => {
    setIsReviewing(false)
    restart(true)
  }, [restart])

  // Keyboard shortcuts — only in full and compact modes
  const keyboardActions = useMemo(() => ({
    togglePlayPause: handlePlayPause,
    toggleFilePanel: () => toggleExclusivePanel('files'),
    toggleTranscript: () => toggleExclusivePanel('transcript'),
    toggleTimeline: () => { setShowTimeline(prev => !prev) },
    toggleHexGrid: () => { setShowHexGrid(prev => !prev) },
    toggleStats: () => { setShowStats(prev => !prev) },
    toggleCostOverlay: () => toggleExclusivePanel('cost'),
    zoomToFit: () => { setZoomToFitTrigger(n => n + 1) },
    clearSelection: () => { selection.clearAllSelections() },
    deselectAgent: () => { selection.clearAgent() },
    closeTranscript: () => { setShowTranscript(false) },
    toggleMute: handleToggleMute,
    setSpeed,
    selectedAgentId: selection.selectedAgentId,
  }), [handlePlayPause, selection.clearAllSelections, selection.clearAgent, selection.selectedAgentId, setSpeed, handleToggleMute, toggleExclusivePanel])

  useKeyboardShortcuts(mode !== "mini" ? keyboardActions : null)

  const totalTokens = useMemo(() => {
    let sum = 0
    for (const a of agents.values()) sum += a.tokensUsed
    return sum
  }, [agents])

  const selectedAgent = selection.selectedAgentId ? agents.get(selection.selectedAgentId) : null
  const selectedConversation = selection.selectedAgentId ? (conversations.get(selection.selectedAgentId) || []) : []

  const sessionConversation = useMemo(() => {
    if (!showTranscript) return []
    const all = Array.from(conversations.values()).flat()
    return all.sort((a, b) => a.timestamp - b.timestamp)
  }, [conversations, showTranscript])

  // Context menu items
  const contextMenuItems = selection.contextMenu ? (
    selection.contextMenu.agentId ? [
      { label: '📊  Toggle Stats', onClick: () => setShowStats(prev => !prev) },
    ] : [
      { label: '🔍  Zoom to Fit', onClick: () => setZoomToFitTrigger(n => n + 1) },
      { label: '📊  Toggle Stats', onClick: () => setShowStats(prev => !prev) },
      { label: '⬡  Toggle Grid', onClick: () => setShowHexGrid(prev => !prev) },
      { label: '', onClick: () => {}, separator: true },
      { label: '⟲  Restart', onClick: restart },
    ]
  ) : []

  const handleCloseSession = useCallback((id: string) => {
    bridge.removeSession(id)
    sessionCacheRef.current.delete(id)
    if (bridge.selectedSessionId === id) {
      const remaining = bridge.sessions.filter(s => s.id !== id)
      if (remaining.length > 0) {
        bridge.selectSession(remaining[remaining.length - 1].id)
      }
    }
  }, [bridge])

  const openFile = useCallback((_filePath: string, _line?: number) => {
    // No-op in UA context (no VS Code editor to open files in)
  }, [])

  const isEmpty = agents.size === 0 && bridge.useMockData === false

  /* ─── Mini mode: compact card with click-to-expand ─── */
  if (mode === "mini") {
    return (
      <div
        className={`relative overflow-hidden rounded-xl border border-white/10 cursor-pointer group ${className}`}
        style={{ background: COLORS.void, minHeight: 200 }}
        onClick={() => {
          if (onExpand) onExpand()
          else router.push("/dashboard/agent-flow")
        }}
      >
        <AgentCanvas
          simulationRef={frameRef}
          selectedAgentId={null}
          hoveredAgentId={null}
          showStats={false}
          showHexGrid={false}
          zoomToFitTrigger={zoomToFitTrigger}
          pauseAutoFit={false}
          onAgentClick={() => {}}
          onAgentHover={() => {}}
          onAgentDrag={() => {}}
          onContextMenu={() => {}}
          onToolCallClick={() => {}}
          selectedToolCallId={null}
          onDiscoveryClick={() => {}}
          selectedDiscoveryId={null}
          showCostOverlay={false}
        />
        {/* Overlay with agent count and expand hint */}
        <div className="absolute inset-0 pointer-events-none flex items-end justify-between p-3">
          <div className="flex items-center gap-2 text-xs text-cyan-400/80 font-mono">
            <span className="inline-block w-2 h-2 rounded-full bg-cyan-400 animate-pulse" />
            {agents.size} agent{agents.size !== 1 ? 's' : ''} active
          </div>
          <div className="text-[10px] text-white/30 opacity-0 group-hover:opacity-100 transition-opacity">
            Click to expand →
          </div>
        </div>
      </div>
    )
  }

  /* ─── Compact mode: panel-sized with simplified controls ─── */
  if (mode === "compact") {
    return (
      <OpenFileProvider value={null}>
        <div className={`relative overflow-hidden ${className}`} style={{ background: COLORS.void }}>
          {isEmpty && (
            <div className="absolute inset-0 flex items-center justify-center z-10 pointer-events-none">
              <div className="text-center" style={{ fontFamily: "'SF Mono', 'Fira Code', monospace" }}>
                <div className="text-sm" style={{ color: '#66ccff80' }}>WAITING FOR AGENT SESSION</div>
                <div className="mt-2 text-xs" style={{ color: '#66ccff40' }}>Start a session to see activity</div>
              </div>
            </div>
          )}

          <AgentCanvas
            simulationRef={frameRef}
            selectedAgentId={selection.selectedAgentId}
            hoveredAgentId={selection.hoveredAgentId}
            showStats={false}
            showHexGrid={showHexGrid}
            zoomToFitTrigger={zoomToFitTrigger}
            pauseAutoFit={false}
            onAgentClick={selection.handleAgentClick}
            onAgentHover={selection.setHoveredAgentId}
            onAgentDrag={updateAgentPosition}
            onContextMenu={selection.handleContextMenu}
            onToolCallClick={selection.handleToolCallClick}
            selectedToolCallId={selection.selectedToolCallId}
            onDiscoveryClick={selection.handleDiscoveryClick}
            selectedDiscoveryId={selection.selectedDiscoveryId}
            showCostOverlay={false}
          />

          {/* Agent detail card */}
          {selectedAgent && selection.selectedAgentWorldPos && (
            <div {...stopPropagationHandlers}>
              <AgentDetailCard agent={selectedAgent} onClose={selection.clearAgent} />
            </div>
          )}

          {/* Tool call detail popup */}
          {selection.selectedToolData && selection.selectedToolScreenPos && (
            <div {...stopPropagationHandlers}>
              <ToolDetailPopup
                tool={selection.selectedToolData}
                position={selection.selectedToolScreenPos}
                onClose={selection.clearTool}
              />
            </div>
          )}

          {/* Simplified control bar */}
          <ControlBar
            isPlaying={isPlaying}
            speed={speed}
            currentTime={currentTime}
            totalDuration={bridge.useMockData
              ? (isReviewing ? Math.max(maxTimeReached, currentTime) : MOCK_DURATION)
              : Math.max(maxTimeReached, currentTime)
            }
            onPlayPause={handlePlayPause}
            onRestart={handleRestart}
            onSpeedChange={setSpeed}
            onSeek={(time) => {
              seekingRef.current = true
              pause()
              seekToTime(time)
              setZoomToFitTrigger(n => n + 1)
              if (resumeTimerRef.current) clearTimeout(resumeTimerRef.current)
              resumeTimerRef.current = setTimeout(() => { resumeTimerRef.current = null; seekingRef.current = false }, TIMING.seekCompleteDelayMs)
            }}
            timelineEvents={timelineEvents}
            isReviewing={isReviewing}
            eventCount={timelineEvents.length}
            onEnterReview={handleEnterReview}
            onResumeLive={handleResumeLive}
          />

          {/* Context menu */}
          {selection.contextMenu && (
            <GlassContextMenu
              position={selection.contextMenu}
              items={contextMenuItems}
              onClose={() => selection.setContextMenu(null)}
            />
          )}
        </div>
      </OpenFileProvider>
    )
  }

  /* ─── Full mode: dedicated tab with all panels ─── */
  return (
    <OpenFileProvider value={null}>
      <div className={`h-full w-full relative overflow-hidden ${className}`} style={{ background: COLORS.void }}>
        {isEmpty && (
          <div className="absolute inset-0 flex items-center justify-center z-10 pointer-events-none">
            <div className="text-center" style={{ fontFamily: "'SF Mono', 'Fira Code', monospace" }}>
              <div className="text-sm" style={{ color: '#66ccff80' }}>WAITING FOR AGENT SESSION</div>
              <div className="mt-2 text-xs" style={{ color: '#66ccff40' }}>Start a session to see activity</div>
            </div>
          </div>
        )}

        {/* Canvas fills everything */}
        <AgentCanvas
          simulationRef={frameRef}
          selectedAgentId={selection.selectedAgentId}
          hoveredAgentId={selection.hoveredAgentId}
          showStats={showStats}
          showHexGrid={showHexGrid}
          zoomToFitTrigger={zoomToFitTrigger}
          pauseAutoFit={selection.contextMenu !== null}
          onAgentClick={selection.handleAgentClick}
          onAgentHover={selection.setHoveredAgentId}
          onAgentDrag={updateAgentPosition}
          onContextMenu={selection.handleContextMenu}
          onToolCallClick={selection.handleToolCallClick}
          selectedToolCallId={selection.selectedToolCallId}
          onDiscoveryClick={selection.handleDiscoveryClick}
          selectedDiscoveryId={selection.selectedDiscoveryId}
          showCostOverlay={showCostOverlay}
        />

        {/* Message feed panel (top-left) */}
        <MessageFeedPanel
          conversations={conversations}
          agents={agents}
          onAgentClick={selection.handleAgentClick}
          selectedAgentId={selection.selectedAgentId}
        />

        {/* Agent detail card */}
        {selectedAgent && selection.selectedAgentWorldPos && (
          <div {...stopPropagationHandlers}>
            <AgentDetailCard agent={selectedAgent} onClose={selection.clearAgent} />
          </div>
        )}

        {/* Tool call detail popup */}
        {selection.selectedToolData && selection.selectedToolScreenPos && (
          <div {...stopPropagationHandlers}>
            <ToolDetailPopup
              tool={selection.selectedToolData}
              position={selection.selectedToolScreenPos}
              onClose={selection.clearTool}
            />
          </div>
        )}

        {/* Discovery detail popup */}
        {selection.selectedDiscoveryData && selection.selectedDiscoveryScreenPos && (
          <div {...stopPropagationHandlers}>
            <DiscoveryDetailPopup
              discovery={selection.selectedDiscoveryData}
              position={selection.selectedDiscoveryScreenPos}
              onClose={selection.clearDiscovery}
            />
          </div>
        )}

        {/* Chat panel (bottom-right, shown when agent selected) */}
        <AgentChatPanel
          visible={!!selectedAgent}
          agentName={selectedAgent?.name ?? ''}
          agentState={selectedAgent?.state ?? 'idle'}
          conversation={selectedConversation}
          onClose={selection.clearAgent}
        />

        {/* Context menu */}
        {selection.contextMenu && (
          <GlassContextMenu
            position={selection.contextMenu}
            items={contextMenuItems}
            onClose={() => selection.setContextMenu(null)}
          />
        )}

        {/* Floating control strip */}
        <ControlBar
          isPlaying={isPlaying}
          speed={speed}
          currentTime={currentTime}
          totalDuration={bridge.useMockData
            ? (isReviewing ? Math.max(maxTimeReached, currentTime) : MOCK_DURATION)
            : Math.max(maxTimeReached, currentTime)
          }
          onPlayPause={handlePlayPause}
          onRestart={handleRestart}
          onSpeedChange={setSpeed}
          onSeek={(time) => {
            seekingRef.current = true
            pause()
            seekToTime(time)
            setZoomToFitTrigger(n => n + 1)
            if (resumeTimerRef.current) clearTimeout(resumeTimerRef.current)
            resumeTimerRef.current = setTimeout(() => { resumeTimerRef.current = null; seekingRef.current = false }, TIMING.seekCompleteDelayMs)
          }}
          timelineEvents={timelineEvents}
          isReviewing={isReviewing}
          eventCount={timelineEvents.length}
          onEnterReview={handleEnterReview}
          onResumeLive={handleResumeLive}
        />

        {/* File attention panel */}
        <FileAttentionPanel
          visible={showFileAttention}
          fileAttention={fileAttention}
          onClose={() => setShowFileAttention(false)}
        />

        {/* Session transcript panel */}
        <SessionTranscriptPanel
          visible={showTranscript}
          conversation={sessionConversation}
          onClose={() => setShowTranscript(false)}
        />

        {/* Timeline panel */}
        <TimelinePanel
          visible={showTimeline}
          timelineEntries={timelineEntries}
          currentTime={currentTime}
          onClose={() => setShowTimeline(false)}
        />

        {/* Top bar: session tabs + info/controls */}
        <TopBar
          sessions={bridge.sessions}
          selectedSessionId={bridge.selectedSessionId}
          sessionsWithActivity={bridge.sessionsWithActivity}
          onSelectSession={bridge.selectSession}
          onCloseSession={handleCloseSession}
          isVSCode={false}
          connectionStatus={bridge.connectionStatus}
          agentCount={agents.size}
          totalTokens={totalTokens}
          showFileAttention={showFileAttention}
          showTranscript={showTranscript}
          showCostOverlay={showCostOverlay}
          showTimeline={showTimeline}
          isMuted={isMuted}
          onTogglePanel={toggleExclusivePanel}
          onToggleTimeline={() => setShowTimeline(prev => !prev)}
          onToggleMute={handleToggleMute}
        />
      </div>
    </OpenFileProvider>
  )
}
