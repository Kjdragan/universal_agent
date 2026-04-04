'use client'

import { memo } from 'react'
import { Z } from '@/lib/agent-flow/agent-types'
import { COLORS } from '@/lib/agent-flow/colors'
import type { SpotlightMode, SpotlightSessionArchive } from '@/lib/agent-flow/spotlight-types'

interface SpotlightBarProps {
  mode: SpotlightMode
  recentArchives: SpotlightSessionArchive[]
  greatestHitArchives: SpotlightSessionArchive[]
  selectedSessionId: string | null
  currentTitle: string
  isLive: boolean
  onModeChange: (mode: SpotlightMode) => void
  onSelectRecent: (sessionId: string) => void
  onSelectGreatestHit: (sessionId: string) => void
}

function ModeButton({
  active,
  label,
  onClick,
}: {
  active: boolean
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-full px-3 py-1 text-[10px] font-semibold uppercase tracking-[0.16em] transition-colors"
      style={{
        background: active ? COLORS.tabSelectedBg : COLORS.tabInactiveBg,
        border: `1px solid ${active ? COLORS.tabSelectedBorder : COLORS.tabInactiveBorder}`,
        color: active ? COLORS.holoBright : COLORS.textMuted,
      }}
    >
      {label}
    </button>
  )
}

function ArchivePill({
  archive,
  selected,
  onClick,
}: {
  archive: SpotlightSessionArchive
  selected: boolean
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-full px-2.5 py-1 text-[10px] font-mono transition-colors"
      style={{
        background: selected ? COLORS.tabSelectedBg : COLORS.tabInactiveBg,
        border: `1px solid ${selected ? COLORS.tabSelectedBorder : COLORS.tabInactiveBorder}`,
        color: selected ? COLORS.holoBright : COLORS.textMuted,
      }}
    >
      {archive.title}
    </button>
  )
}

export const SpotlightBar = memo(function SpotlightBar({
  mode,
  recentArchives,
  greatestHitArchives,
  selectedSessionId,
  currentTitle,
  isLive,
  onModeChange,
  onSelectRecent,
  onSelectGreatestHit,
}: SpotlightBarProps) {
  return (
    <div
      className="absolute top-3 left-3 right-3 flex items-start justify-between gap-6 font-mono"
      style={{ zIndex: Z.info }}
    >
      <div className="min-w-0 flex-1 space-y-3">
        <div className="flex items-center gap-2">
          <ModeButton active={mode === 'recent'} label="Live / Recent" onClick={() => onModeChange('recent')} />
          <ModeButton active={mode === 'greatest_hits'} label="Greatest Hits" onClick={() => onModeChange('greatest_hits')} />
          <span
            className="rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.16em]"
            style={{
              background: isLive ? 'rgba(102, 255, 170, 0.16)' : 'rgba(255, 187, 68, 0.14)',
              border: `1px solid ${isLive ? 'rgba(102, 255, 170, 0.35)' : 'rgba(255, 187, 68, 0.28)'}`,
              color: isLive ? COLORS.complete : COLORS.tool,
            }}
          >
            {isLive ? 'LIVE' : 'REPLAY'}
          </span>
          <div className="truncate text-[11px]" style={{ color: COLORS.holoBright }}>
            {currentTitle || 'No session selected'}
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-center gap-2 overflow-x-auto scrollbar-hide">
            <span className="text-[10px] uppercase tracking-[0.18em]" style={{ color: COLORS.textMuted }}>
              Recent
            </span>
            {recentArchives.length === 0 ? (
              <span className="text-[10px]" style={{ color: COLORS.textMuted }}>No recent runs</span>
            ) : (
              recentArchives.map((archive) => (
                <ArchivePill
                  key={archive.sessionId}
                  archive={archive}
                  selected={selectedSessionId === archive.sessionId}
                  onClick={() => onSelectRecent(archive.sessionId)}
                />
              ))
            )}
          </div>

          <div className="flex items-center gap-2 overflow-x-auto scrollbar-hide">
            <span className="text-[10px] uppercase tracking-[0.18em]" style={{ color: COLORS.textMuted }}>
              Greatest
            </span>
            {greatestHitArchives.length === 0 ? (
              <span className="text-[10px]" style={{ color: COLORS.textMuted }}>No completed highlights</span>
            ) : (
              greatestHitArchives.map((archive) => (
                <ArchivePill
                  key={archive.sessionId}
                  archive={archive}
                  selected={selectedSessionId === archive.sessionId}
                  onClick={() => onSelectGreatestHit(archive.sessionId)}
                />
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  )
})
