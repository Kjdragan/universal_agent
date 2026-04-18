'use client'

import type {
  AgentFlowVisualPreferences,
  ReplayPacingMode,
  TextDisplayMode,
  ThinkingDisplayMode,
} from '@/lib/agent-flow/visual-preferences'
import { COLORS } from '@/lib/agent-flow/colors'
import { CloseButton, SlidingPanel, stopPropagationHandlers } from './shared-ui'

interface VisualsPanelProps {
  visible: boolean
  preferences: AgentFlowVisualPreferences
  onChange: (update: Partial<AgentFlowVisualPreferences>) => void
  onReset: () => void
  onClose: () => void
}

function SegmentedButton<T extends string>({
  value,
  active,
  onClick,
}: {
  value: T
  active: boolean
  onClick: (value: T) => void
}) {
  return (
    <button
      type="button"
      onClick={() => onClick(value)}
      className="px-2 py-1 text-[10px] font-mono capitalize transition-colors"
      style={{
        borderRadius: 6,
        background: active ? COLORS.toggleActive : 'transparent',
        border: `1px solid ${active ? COLORS.holoBorder12 : COLORS.holoBorder06}`,
        color: active ? COLORS.holoBright : COLORS.textMuted,
      }}
    >
      {value.replace('_', ' ')}
    </button>
  )
}

function ControlLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-[9px] font-mono uppercase tracking-[0.16em]" style={{ color: COLORS.panelLabelDim }}>
      {children}
    </div>
  )
}

export function VisualsPanel({
  visible,
  preferences,
  onChange,
  onReset,
  onClose,
}: VisualsPanelProps) {
  return (
    <SlidingPanel
      visible={visible}
      position={{ top: 150, right: 12 }}
      zIndex={62}
      width={300}
      {...stopPropagationHandlers}
    >
      <div
        className="flex flex-col gap-3 p-3"
        style={{
          borderRadius: 8,
          background: COLORS.panelBg,
          border: `1px solid ${COLORS.holoBorder12}`,
          backdropFilter: 'blur(22px)',
        }}
      >
        <div className="flex items-center justify-between">
          <div className="text-[11px] font-mono font-semibold uppercase tracking-[0.18em]" style={{ color: COLORS.holoBright }}>
            Visuals
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={onReset}
              className="text-[9px] font-mono"
              style={{ color: COLORS.textMuted }}
            >
              Reset
            </button>
            <CloseButton onClick={onClose} />
          </div>
        </div>

        <div className="space-y-1">
          <ControlLabel>Text mode</ControlLabel>
          <div className="flex gap-1">
            {(['compact', 'hybrid', 'inline'] as TextDisplayMode[]).map((mode) => (
              <SegmentedButton
                key={mode}
                value={mode}
                active={preferences.textDisplayMode === mode}
                onClick={(textDisplayMode) => onChange({ textDisplayMode })}
              />
            ))}
          </div>
        </div>

        <div className="space-y-1">
          <ControlLabel>Replay pacing</ControlLabel>
          <div className="flex gap-1">
            {(['fast', 'readable', 'dramatic'] as ReplayPacingMode[]).map((mode) => (
              <SegmentedButton
                key={mode}
                value={mode}
                active={preferences.replayPacingMode === mode}
                onClick={(replayPacingMode) => onChange({ replayPacingMode })}
              />
            ))}
          </div>
        </div>

        <div className="space-y-1">
          <ControlLabel>Thinking</ControlLabel>
          <div className="flex gap-1">
            {(['ambient', 'bubbles', 'off'] as ThinkingDisplayMode[]).map((mode) => (
              <SegmentedButton
                key={mode}
                value={mode}
                active={preferences.thinkingDisplay === mode}
                onClick={(thinkingDisplay) => onChange({ thinkingDisplay })}
              />
            ))}
          </div>
        </div>

        <label className="space-y-1">
          <ControlLabel>Text scale {Math.round(preferences.textScale * 100)}%</ControlLabel>
          <input
            type="range"
            min="0.8"
            max="1.3"
            step="0.05"
            value={preferences.textScale}
            onChange={(event) => onChange({ textScale: Number(event.currentTarget.value) })}
            className="w-full"
          />
        </label>

        <label className="space-y-1">
          <ControlLabel>Readable hold {preferences.readableHoldMultiplier.toFixed(1)}x</ControlLabel>
          <input
            type="range"
            min="0.5"
            max="2"
            step="0.1"
            value={preferences.readableHoldMultiplier}
            onChange={(event) => onChange({ readableHoldMultiplier: Number(event.currentTarget.value) })}
            className="w-full"
          />
        </label>

        <label className="flex items-center justify-between gap-3 text-[10px] font-mono" style={{ color: COLORS.textPrimary }}>
          Auto-fade text
          <input
            type="checkbox"
            checked={preferences.autoFadeText}
            onChange={(event) => onChange({ autoFadeText: event.currentTarget.checked })}
          />
        </label>

        <label className="flex items-center justify-between gap-3 text-[10px] font-mono" style={{ color: COLORS.textPrimary }}>
          Pin on hover
          <input
            type="checkbox"
            checked={preferences.pinOnHover}
            onChange={(event) => onChange({ pinOnHover: event.currentTarget.checked })}
          />
        </label>
      </div>
    </SlidingPanel>
  )
}
