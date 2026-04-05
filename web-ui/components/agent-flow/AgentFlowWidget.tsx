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

import { useState, useCallback, useMemo, useEffect, useRef } from "react"
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
import { SpotlightBar } from "./spotlight-bar"
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
    loadEventPlayback,
    hydrateToLatest,
  } = useAgentSimulation({
    useMockData: bridge.useMockData,
    externalEvents: bridge.pendingEvents,
    onExternalEventsConsumed: bridge.consumeEvents,
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

  const previousSelectionRef = useRef<{ sessionId: string | null; mode: typeof bridge.selectedPlaybackMode }>({
    sessionId: null,
    mode: 'live',
  })
  const replayTransitionTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const replayLoopGuardRef = useRef<string | null>(null)

  useEffect(() => {
    if (replayTransitionTimeoutRef.current) {
      clearTimeout(replayTransitionTimeoutRef.current)
      replayTransitionTimeoutRef.current = null
    }

    if (!bridge.selectedArchive) {
      restart()
      previousSelectionRef.current = { sessionId: null, mode: bridge.selectedPlaybackMode }
      return
    }

    const previous = previousSelectionRef.current
    const sameSession = previous.sessionId === bridge.selectedArchive.sessionId
    const switchedFromLiveToReplay = sameSession && previous.mode === 'live' && bridge.selectedPlaybackMode === 'replay'
    const changedSession = previous.sessionId !== bridge.selectedArchive.sessionId
    const changedMode = previous.mode !== bridge.selectedPlaybackMode

    if (bridge.selectedPlaybackMode === 'live') {
      if (changedSession || changedMode) {
        hydrateToLatest(bridge.selectedArchive.normalizedEvents)
        replayLoopGuardRef.current = null
      }
      previousSelectionRef.current = { sessionId: bridge.selectedArchive.sessionId, mode: bridge.selectedPlaybackMode }
      return
    }

    if (switchedFromLiveToReplay) {
      replayTransitionTimeoutRef.current = setTimeout(() => {
        loadEventPlayback(bridge.selectedPlaybackEvents, {
          currentTime: 0,
          eventIndex: 0,
          maxTimeReached: bridge.selectedPlaybackEvents[bridge.selectedPlaybackEvents.length - 1]?.time ?? 0,
          isPlaying: true,
        })
        previousSelectionRef.current = { sessionId: bridge.selectedArchive?.sessionId ?? null, mode: 'replay' }
        replayLoopGuardRef.current = null
      }, 2000)
      return
    }

    if (changedSession || changedMode || bridge.currentReplayGeneration > 0) {
      loadEventPlayback(bridge.selectedPlaybackEvents, {
        currentTime: 0,
        eventIndex: 0,
        maxTimeReached: bridge.selectedPlaybackEvents[bridge.selectedPlaybackEvents.length - 1]?.time ?? 0,
        isPlaying: true,
      })
    }
    previousSelectionRef.current = { sessionId: bridge.selectedArchive.sessionId, mode: bridge.selectedPlaybackMode }
    replayLoopGuardRef.current = null
  }, [
    bridge.currentReplayGeneration,
    bridge.selectedArchive,
    bridge.selectedPlaybackEvents,
    bridge.selectedPlaybackMode,
    hydrateToLatest,
    loadEventPlayback,
    restart,
  ])

  useEffect(() => () => {
    if (replayTransitionTimeoutRef.current) clearTimeout(replayTransitionTimeoutRef.current)
  }, [])

  const timelineEvents = useMemo((): TimelineEvent[] => {
    const events: TimelineEvent[] = []
    for (const [agentId, msgs] of conversations) {
      for (let i = 0; i < msgs.length; i += 1) {
        const msg = msgs[i]
        events.push({
          id: `${agentId}:${msg.timestamp}:${msg.type}:${i}`,
          type: msg.type === 'tool_call' ? 'tool_call' : msg.type === 'tool_result' ? 'tool_result' : 'message',
          label: msg.content.slice(0, 20),
          timestamp: msg.timestamp,
          nodeId: agentId,
        })
      }
    }
    events.sort((a, b) => a.timestamp - b.timestamp)
    return events
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
  }), [handlePlayPause, selection, setSpeed, handleToggleMute, toggleExclusivePanel])

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

  const openFile = useCallback((_filePath: string, _line?: number) => {
    // No-op in UA context (no VS Code editor to open files in)
  }, [])

  const isEmpty = !bridge.selectedArchive && bridge.useMockData === false
  const recentArchives = useMemo(
    () => bridge.recentSessionIds.map((id) => bridge.archivesBySessionId[id]).filter(Boolean),
    [bridge.archivesBySessionId, bridge.recentSessionIds],
  )
  const greatestHitArchives = useMemo(
    () => bridge.greatestHitSessionIds.map((id) => bridge.archivesBySessionId[id]).filter(Boolean),
    [bridge.archivesBySessionId, bridge.greatestHitSessionIds],
  )
  const totalDuration = bridge.useMockData
    ? (isReviewing ? Math.max(maxTimeReached, currentTime) : MOCK_DURATION)
    : Math.max(maxTimeReached, currentTime)

  useEffect(() => {
    if (bridge.selectedPlaybackMode !== 'replay' || !bridge.selectedArchive) return
    if (maxTimeReached <= 0) return

    const replayKey = `${bridge.mode}:${bridge.selectedArchive.sessionId}:${bridge.currentReplayGeneration}`
    if (currentTime < maxTimeReached + 2) return
    if (replayLoopGuardRef.current === replayKey) return
    replayLoopGuardRef.current = replayKey

    if (bridge.mode === 'greatest_hits' && bridge.selectionSource === 'auto' && bridge.greatestHitSessionIds.length > 1) {
      bridge.advanceGreatestHitsLoop()
      return
    }

    bridge.restartReplay()
  }, [
    bridge,
    bridge.advanceGreatestHitsLoop,
    bridge.currentReplayGeneration,
    bridge.greatestHitSessionIds.length,
    bridge.mode,
    bridge.restartReplay,
    bridge.selectedArchive,
    bridge.selectedPlaybackMode,
    bridge.selectionSource,
    currentTime,
    maxTimeReached,
  ])

  const spotlightBadge = (
    <span
      className="rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.16em]"
      style={{
        background: bridge.currentSpotlightIsLive ? 'rgba(102, 255, 170, 0.16)' : 'rgba(255, 187, 68, 0.14)',
        border: `1px solid ${bridge.currentSpotlightIsLive ? 'rgba(102, 255, 170, 0.35)' : 'rgba(255, 187, 68, 0.28)'}`,
        color: bridge.currentSpotlightIsLive ? COLORS.complete : COLORS.tool,
      }}
    >
      {bridge.currentSpotlightIsLive ? 'LIVE' : 'REPLAY'}
    </span>
  )

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
        <div className="absolute top-3 left-3 right-3 z-10 pointer-events-none flex items-center justify-between gap-3">
          <div className="min-w-0 truncate text-[10px] font-mono" style={{ color: COLORS.holoBright }}>
            {bridge.currentSpotlightTitle || 'Agent Flow'}
          </div>
          {spotlightBadge}
        </div>
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
            {bridge.currentSpotlightTitle || 'No spotlight'}
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
          <div className="absolute top-3 left-3 right-3 z-10 pointer-events-none flex items-center justify-between gap-3">
            <div className="min-w-0 truncate text-[10px] font-mono" style={{ color: COLORS.holoBright }}>
              {bridge.currentSpotlightTitle || 'Agent Flow'}
            </div>
            {spotlightBadge}
          </div>
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
            totalDuration={totalDuration}
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
          totalDuration={totalDuration}
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

        <SpotlightBar
          mode={bridge.mode}
          recentArchives={recentArchives}
          greatestHitArchives={greatestHitArchives}
          selectedSessionId={bridge.selectedSessionId}
          currentTitle={bridge.currentSpotlightTitle}
          isLive={bridge.currentSpotlightIsLive}
          onModeChange={bridge.setMode}
          onSelectRecent={(sessionId) => {
            bridge.setMode('recent')
            bridge.selectSession(sessionId, 'manual')
          }}
          onSelectGreatestHit={(sessionId) => {
            bridge.setMode('greatest_hits')
            bridge.selectSession(sessionId, 'manual')
          }}
        />

        <div
          className="absolute top-[108px] right-3 flex items-center gap-4 rounded px-3 py-1.5 font-mono text-[10px]"
          style={{
            zIndex: 11,
            background: COLORS.holoBg03,
            border: `1px solid ${COLORS.holoBorder08}`,
            color: COLORS.textMuted,
          }}
        >
          <span>{bridge.connectionStatus === 'watching' ? 'LIVE' : 'OFFLINE'}</span>
          <span>{agents.size} agents</span>
          <span>{Math.round(totalTokens / 1000)}k tokens</span>
          <button type="button" onClick={() => toggleExclusivePanel('files')} style={{ color: showFileAttention ? COLORS.holoBright : COLORS.textMuted }}>Files</button>
          <button type="button" onClick={() => toggleExclusivePanel('transcript')} style={{ color: showTranscript ? COLORS.holoBright : COLORS.textMuted }}>Chat</button>
          <button type="button" onClick={() => toggleExclusivePanel('cost')} style={{ color: showCostOverlay ? COLORS.complete : COLORS.textMuted }}>$Cost</button>
          <button type="button" onClick={() => setShowTimeline(prev => !prev)} style={{ color: showTimeline ? COLORS.holoBright : COLORS.textMuted }}>Timeline</button>
          <button type="button" onClick={handleToggleMute} style={{ color: isMuted ? COLORS.textMuted : COLORS.holoBright }}>
            {isMuted ? 'Mute' : 'Sound'}
          </button>
        </div>
      </div>
    </OpenFileProvider>
  )
}
