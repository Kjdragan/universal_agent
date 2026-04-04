'use client'

import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import type { ConnectionStatus } from './bridge-types'
import type {
  SpotlightMode,
  SpotlightSelectionSource,
  SpotlightSessionArchive,
} from './spotlight-types'
import {
  deriveGreatestHitSessionIds,
  deriveRecentSessionIds,
} from './spotlight-session-utils'

const STORAGE_KEY = 'ua.agent-flow-spotlight.v1'
const CHANNEL_NAME = 'ua.agent-flow-spotlight'

type ControlSnapshot = {
  mode: SpotlightMode
  selectedSessionId: string | null
  selectionSource: SpotlightSelectionSource
  currentReplayLoopIndex: number
  currentReplayGeneration: number
}

interface SpotlightStoreState extends ControlSnapshot {
  archivesBySessionId: Record<string, SpotlightSessionArchive>
  recentSessionIds: string[]
  greatestHitSessionIds: string[]
  currentSpotlightTitle: string
  currentSpotlightIsLive: boolean
  connectionStatus: ConnectionStatus
  upsertArchive: (archive: SpotlightSessionArchive) => void
  hydrateArchives: (archives: Record<string, SpotlightSessionArchive>) => void
  setMode: (mode: SpotlightMode) => void
  selectSession: (sessionId: string | null, source?: SpotlightSelectionSource) => void
  clearManualSelection: () => void
  setReplayLoopIndex: (index: number) => void
  bumpReplayGeneration: () => void
  setSpotlightMeta: (title: string, isLive: boolean) => void
  setConnectionStatus: (status: ConnectionStatus) => void
  applyRemoteControlSnapshot: (snapshot: ControlSnapshot) => void
}

function recomputeLists(archivesBySessionId: Record<string, SpotlightSessionArchive>) {
  return {
    recentSessionIds: deriveRecentSessionIds(archivesBySessionId),
    greatestHitSessionIds: deriveGreatestHitSessionIds(archivesBySessionId),
  }
}

export const useAgentFlowSpotlightStore = create<SpotlightStoreState>()(
  persist(
    (set) => ({
      mode: 'recent',
      selectedSessionId: null,
      selectionSource: 'auto',
      archivesBySessionId: {},
      recentSessionIds: [],
      greatestHitSessionIds: [],
      currentReplayLoopIndex: 0,
      currentReplayGeneration: 0,
      currentSpotlightTitle: '',
      currentSpotlightIsLive: false,
      connectionStatus: 'disconnected',
      upsertArchive: (archive) => set((state) => {
        const archivesBySessionId = {
          ...state.archivesBySessionId,
          [archive.sessionId]: archive,
        }
        return {
          archivesBySessionId,
          ...recomputeLists(archivesBySessionId),
        }
      }),
      hydrateArchives: (archivesBySessionId) => set(() => ({
        archivesBySessionId,
        ...recomputeLists(archivesBySessionId),
      })),
      setMode: (mode) => set(() => ({ mode })),
      selectSession: (sessionId, source = 'manual') => set((state) => {
        if (state.selectedSessionId === sessionId && state.selectionSource === source) {
          return state
        }
        return {
          selectedSessionId: sessionId,
          selectionSource: source,
          currentReplayLoopIndex: Math.max(0, state.greatestHitSessionIds.indexOf(sessionId || '')),
        }
      }),
      clearManualSelection: () => set(() => ({
        selectionSource: 'auto',
      })),
      setReplayLoopIndex: (index) => set(() => ({
        currentReplayLoopIndex: Math.max(0, index),
      })),
      bumpReplayGeneration: () => set((state) => ({
        currentReplayGeneration: state.currentReplayGeneration + 1,
      })),
      setSpotlightMeta: (title, isLive) => set(() => ({
        currentSpotlightTitle: title,
        currentSpotlightIsLive: isLive,
      })),
      setConnectionStatus: (status) => set(() => ({
        connectionStatus: status,
      })),
      applyRemoteControlSnapshot: (snapshot) => set(() => snapshot),
    }),
    {
      name: STORAGE_KEY,
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        mode: state.mode,
        selectedSessionId: state.selectedSessionId,
        selectionSource: state.selectionSource,
        archivesBySessionId: state.archivesBySessionId,
      }),
      onRehydrateStorage: () => (state) => {
        if (!state) return
        const lists = recomputeLists(state.archivesBySessionId)
        state.recentSessionIds = lists.recentSessionIds
        state.greatestHitSessionIds = lists.greatestHitSessionIds
      },
    },
  ),
)

let syncInitialized = false

export function ensureAgentFlowSpotlightSync(): void {
  if (syncInitialized || typeof window === 'undefined') return
  syncInitialized = true

  const channel = typeof BroadcastChannel !== 'undefined'
    ? new BroadcastChannel(CHANNEL_NAME)
    : null

  let lastSerialized = ''

  useAgentFlowSpotlightStore.subscribe((state) => {
    if (!channel) return
    const snapshot: ControlSnapshot = {
      mode: state.mode,
      selectedSessionId: state.selectedSessionId,
      selectionSource: state.selectionSource,
      currentReplayLoopIndex: state.currentReplayLoopIndex,
      currentReplayGeneration: state.currentReplayGeneration,
    }
    const serialized = JSON.stringify(snapshot)
    if (serialized === lastSerialized) return
    lastSerialized = serialized
    channel.postMessage(snapshot)
  })

  channel?.addEventListener('message', (event) => {
    const data = event.data as ControlSnapshot | undefined
    if (!data) return
    const serialized = JSON.stringify(data)
    if (serialized === lastSerialized) return
    lastSerialized = serialized
    useAgentFlowSpotlightStore.getState().applyRemoteControlSnapshot(data)
  })
}
