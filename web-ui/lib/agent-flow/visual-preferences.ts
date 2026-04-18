'use client'

import { useCallback, useState } from 'react'

export type TextDisplayMode = 'compact' | 'hybrid' | 'inline'
export type ReplayPacingMode = 'fast' | 'readable' | 'dramatic'
export type ThinkingDisplayMode = 'ambient' | 'bubbles' | 'off'

export interface AgentFlowVisualPreferences {
  textDisplayMode: TextDisplayMode
  replayPacingMode: ReplayPacingMode
  textScale: number
  readableHoldMultiplier: number
  thinkingDisplay: ThinkingDisplayMode
  autoFadeText: boolean
  pinOnHover: boolean
}

export const VISUAL_PREFS_STORAGE_KEY = 'ua.agent-flow-visuals.v1'

export const DEFAULT_VISUAL_PREFERENCES: AgentFlowVisualPreferences = {
  textDisplayMode: 'hybrid',
  replayPacingMode: 'readable',
  textScale: 1,
  readableHoldMultiplier: 1,
  thinkingDisplay: 'ambient',
  autoFadeText: true,
  pinOnHover: true,
}

function normalizeChoice<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  return typeof value === 'string' && allowed.includes(value as T) ? value as T : fallback
}

function normalizeNumber(value: unknown, fallback: number, min: number, max: number): number {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return fallback
  return Math.min(max, Math.max(min, parsed))
}

export function normalizeVisualPreferences(value: unknown): AgentFlowVisualPreferences {
  const row = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  return {
    textDisplayMode: normalizeChoice(row.textDisplayMode, ['compact', 'hybrid', 'inline'] as const, DEFAULT_VISUAL_PREFERENCES.textDisplayMode),
    replayPacingMode: normalizeChoice(row.replayPacingMode, ['fast', 'readable', 'dramatic'] as const, DEFAULT_VISUAL_PREFERENCES.replayPacingMode),
    textScale: normalizeNumber(row.textScale, DEFAULT_VISUAL_PREFERENCES.textScale, 0.8, 1.3),
    readableHoldMultiplier: normalizeNumber(row.readableHoldMultiplier, DEFAULT_VISUAL_PREFERENCES.readableHoldMultiplier, 0.5, 2),
    thinkingDisplay: normalizeChoice(row.thinkingDisplay, ['ambient', 'bubbles', 'off'] as const, DEFAULT_VISUAL_PREFERENCES.thinkingDisplay),
    autoFadeText: typeof row.autoFadeText === 'boolean' ? row.autoFadeText : DEFAULT_VISUAL_PREFERENCES.autoFadeText,
    pinOnHover: typeof row.pinOnHover === 'boolean' ? row.pinOnHover : DEFAULT_VISUAL_PREFERENCES.pinOnHover,
  }
}

export function loadVisualPreferences(): AgentFlowVisualPreferences {
  if (typeof window === 'undefined') return DEFAULT_VISUAL_PREFERENCES
  try {
    const raw = window.localStorage.getItem(VISUAL_PREFS_STORAGE_KEY)
    if (!raw) return DEFAULT_VISUAL_PREFERENCES
    return normalizeVisualPreferences(JSON.parse(raw))
  } catch {
    window.localStorage.removeItem(VISUAL_PREFS_STORAGE_KEY)
    return DEFAULT_VISUAL_PREFERENCES
  }
}

export function saveVisualPreferences(preferences: AgentFlowVisualPreferences): void {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(VISUAL_PREFS_STORAGE_KEY, JSON.stringify(normalizeVisualPreferences(preferences)))
}

export function useAgentFlowVisualPreferences() {
  const [preferences, setPreferencesState] = useState<AgentFlowVisualPreferences>(loadVisualPreferences)

  const setPreferences = useCallback((update: Partial<AgentFlowVisualPreferences>) => {
    setPreferencesState((current) => {
      const next = normalizeVisualPreferences({ ...current, ...update })
      saveVisualPreferences(next)
      return next
    })
  }, [])

  const resetPreferences = useCallback(() => {
    saveVisualPreferences(DEFAULT_VISUAL_PREFERENCES)
    setPreferencesState(DEFAULT_VISUAL_PREFERENCES)
  }, [])

  return { preferences, setPreferences, resetPreferences }
}
