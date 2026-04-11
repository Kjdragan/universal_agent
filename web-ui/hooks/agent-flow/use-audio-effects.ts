import { useRef, useState, useEffect, useCallback } from 'react'
import { SOUND_PREF_KEY } from '@/lib/agent-flow/canvas-constants'
import { AudioEngine } from '@/lib/agent-flow/audio-engine'
import type { Agent, ToolCallNode } from '@/lib/agent-flow/agent-types'
import { detectStateChanges } from '@/components/agent-flow/canvas/detect-state-changes'

export function useAudioEffects(
  agents: Map<string, Agent>,
  toolCalls: Map<string, ToolCallNode>,
  isReviewing: boolean,
) {
  const audioRef = useRef<AudioEngine | null>(null)
  const [isMuted, setIsMuted] = useState(() => {
    try {
      return localStorage.getItem(SOUND_PREF_KEY) !== 'on'
    } catch {
      return true
    }
  })
  const initialMutedRef = useRef(isMuted)
  const seekingRef = useRef(false)
  const prevToolStatesRef = useRef<Map<string, string>>(new Map())
  const prevAgentStatesRef = useRef<Map<string, string>>(new Map())

  // Audio engine lifecycle + restore persisted mute preference
  useEffect(() => {
    const engine = new AudioEngine()
    engine.setMuted(initialMutedRef.current)
    audioRef.current = engine
    return () => { audioRef.current?.dispose(); audioRef.current = null }
  }, [])

  // Detect tool/agent state transitions and play sounds (live mode only)
  useEffect(() => {
    if (seekingRef.current || !audioRef.current || isReviewing) return
    const audio = audioRef.current

    const { transitions, newAgentStates, newToolStates } = detectStateChanges(
      agents, toolCalls,
      prevAgentStatesRef.current, prevToolStatesRef.current,
    )
    prevAgentStatesRef.current = newAgentStates
    prevToolStatesRef.current = newToolStates

    for (const t of transitions) {
      switch (t.kind) {
        case 'agent_spawn':   audio.playAgentSpawn(); break
        case 'agent_complete': audio.playAgentComplete(); break
        case 'tool_start':    audio.playToolStart(); break
        case 'tool_complete': audio.playToolEnd(); break
        case 'tool_error':    audio.playError(); break
      }
    }
  }, [agents, toolCalls, isReviewing])

  const handleToggleMute = useCallback(() => {
    if (audioRef.current) {
      const nowMuted = audioRef.current.toggleMute()
      setIsMuted(nowMuted)
      try { localStorage.setItem(SOUND_PREF_KEY, nowMuted ? 'off' : 'on') } catch { /* ignore */ }
    }
  }, [])

  return { isMuted, seekingRef, handleToggleMute }
}
