'use client'

import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import type { PersistStorage, StorageValue } from 'zustand/middleware'
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

type PersistedSpotlightState = ControlSnapshot

const PERSIST_VERSION = 2

function normalizePersistedSnapshot(value: unknown): PersistedSpotlightState {
  const row = value && typeof value === 'object' ? (value as Record<string, unknown>) : {}
  const mode = row.mode === 'greatest_hits' ? 'greatest_hits' : 'recent'
  const selectedSessionId = typeof row.selectedSessionId === 'string' && row.selectedSessionId.trim()
    ? row.selectedSessionId.trim()
    : null
  const selectionSource = row.selectionSource === 'manual' ? 'manual' : 'auto'
  const currentReplayLoopIndex = Number.isFinite(Number(row.currentReplayLoopIndex))
    ? Math.max(0, Number(row.currentReplayLoopIndex))
    : 0
  const currentReplayGeneration = Number.isFinite(Number(row.currentReplayGeneration))
    ? Math.max(0, Number(row.currentReplayGeneration))
    : 0

  return {
    mode,
    selectedSessionId,
    selectionSource,
    currentReplayLoopIndex,
    currentReplayGeneration,
  }
}

const spotlightStorage: PersistStorage<PersistedSpotlightState> = {
  getItem: (name) => {
    try {
      const raw = localStorage.getItem(name)
      if (!raw) return null
      const parsed = JSON.parse(raw) as StorageValue<Partial<PersistedSpotlightState> & { archivesBySessionId?: unknown }>
      return {
        state: normalizePersistedSnapshot(parsed?.state),
        version: typeof parsed?.version === 'number' ? parsed.version : 0,
      }
    } catch {
      localStorage.removeItem(name)
      return null
    }
  },
  setItem: (name, value) => {
    localStorage.setItem(name, JSON.stringify({
      ...value,
      state: normalizePersistedSnapshot(value.state),
    }))
  },
  removeItem: (name) => {
    localStorage.removeItem(name)
  },
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
      version: PERSIST_VERSION,
      storage: spotlightStorage,
      partialize: (state) => ({
        mode: state.mode,
        selectedSessionId: state.selectedSessionId,
        selectionSource: state.selectionSource,
        currentReplayLoopIndex: state.currentReplayLoopIndex,
        currentReplayGeneration: state.currentReplayGeneration,
      }),
      migrate: (persistedState) => {
        const row = persistedState && typeof persistedState === 'object'
          ? (persistedState as Record<string, unknown>)
          : {}
        return normalizePersistedSnapshot(row)
      },
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
