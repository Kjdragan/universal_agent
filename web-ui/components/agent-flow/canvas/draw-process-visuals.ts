import type {
  Agent,
  ArtifactVisual,
  ErrorRecoveryVisual,
  PhaseTransition,
  TextBurst,
  TextBurstKind,
  PhaseTransitionKind,
} from '@/lib/agent-flow/agent-types'
import type { AgentFlowVisualPreferences } from '@/lib/agent-flow/visual-preferences'
import { COLORS } from '@/lib/agent-flow/colors'
import { alphaHex } from '@/lib/agent-flow/utils'
import { measureTextCached } from './render-cache'
import { drawHexagon, truncateText } from './draw-misc'

type Rect = { x: number; y: number; w: number; h: number }

const TEXT_BURST_STYLE = {
  compact: { w: 280, maxLines: 7, capacity: 700, font: 10, lineH: 14, hold: 8 },
  hybrid: { w: 390, maxLines: 11, capacity: 1500, font: 11, lineH: 15, hold: 12 },
  inline: { w: 520, maxLines: 16, capacity: 2600, font: 12, lineH: 17, hold: 16 },
} as const

const PHASE_COLORS: Record<PhaseTransitionKind, string> = {
  start: COLORS.holoBase,
  input: COLORS.message,
  thinking: COLORS.thinking,
  tools: COLORS.tool,
  delegation: COLORS.dispatch,
  synthesis: COLORS.return,
  completion: COLORS.complete,
}

const TEXT_KIND_COLORS: Record<TextBurstKind, string> = {
  user: COLORS.roleUserText,
  assistant: COLORS.roleAssistantText,
  tool: COLORS.tool,
  artifact: COLORS.complete,
}

function colorAlpha(color: string, alpha: number): string {
  if (color.startsWith('#')) return color + alphaHex(alpha)
  return color
}

function wrapLines(ctx: CanvasRenderingContext2D, text: string, maxW: number): string[] {
  const lines: string[] = []
  for (const para of text.replace(/\s+$/g, '').split('\n')) {
    const words = para.trim().split(/\s+/).filter(Boolean)
    if (words.length === 0) {
      lines.push('')
      continue
    }
    let line = ''
    for (const word of words) {
      const test = line ? `${line} ${word}` : word
      if (line && measureTextCached(ctx, test) > maxW) {
        lines.push(line)
        line = word
      } else {
        line = test
      }
      while (measureTextCached(ctx, line) > maxW && line.length > 1) {
        let cut = line.length - 1
        while (cut > 1 && measureTextCached(ctx, `${line.slice(0, cut)}-`) > maxW) cut--
        lines.push(`${line.slice(0, cut)}-`)
        line = line.slice(cut)
      }
    }
    if (line) lines.push(line)
  }
  return lines.length ? lines : ['']
}

export function textBurstRect(burst: TextBurst, preferences: AgentFlowVisualPreferences): Rect {
  const style = TEXT_BURST_STYLE[preferences.textDisplayMode]
  const scale = preferences.textScale
  const w = style.w * scale
  const h = (44 + style.maxLines * style.lineH + 18) * scale
  return {
    x: burst.x - w / 2,
    y: burst.y - h / 2,
    w,
    h,
  }
}

export function findTextBurstAt(
  x: number,
  y: number,
  textBursts: TextBurst[],
  preferences: AgentFlowVisualPreferences,
): string | null {
  for (let i = textBursts.length - 1; i >= 0; i--) {
    const burst = textBursts[i]
    const rect = textBurstRect(burst, preferences)
    if (x >= rect.x && x <= rect.x + rect.w && y >= rect.y && y <= rect.y + rect.h) return burst.id
  }
  return null
}

function visualAlpha(age: number, hold: number, preferences: AgentFlowVisualPreferences, pinned: boolean): number {
  const fadeIn = 0.35
  const fadeOut = 2.2
  if (age < fadeIn) return age / fadeIn
  if (!preferences.autoFadeText || pinned) return 1
  if (age <= hold) return 1
  return Math.max(0, 1 - (age - hold) / fadeOut)
}

export function drawTextBursts(
  ctx: CanvasRenderingContext2D,
  textBursts: TextBurst[],
  preferences: AgentFlowVisualPreferences,
  time: number,
  hoveredTextBurstId: string | null,
) {
  for (const burst of textBursts) {
    const style = TEXT_BURST_STYLE[preferences.textDisplayMode]
    const age = time - burst.timestamp
    const pinned = preferences.pinOnHover && hoveredTextBurstId === burst.id
    const contentLength = burst.content.length
    const hold = (style.hold + Math.min(5, contentLength / 450)) * preferences.readableHoldMultiplier
    const alpha = visualAlpha(age, hold, preferences, pinned)
    if (alpha <= 0.02) continue

    const rect = textBurstRect(burst, preferences)
    const color = TEXT_KIND_COLORS[burst.kind] || COLORS.holoBase
    const scale = preferences.textScale
    const pad = 12 * scale
    const font = style.font * scale
    const lineH = style.lineH * scale
    const headerH = 30 * scale
    const source = preferences.textDisplayMode === 'compact'
      ? burst.summary || burst.content
      : burst.content
    const sliced = source.slice(0, style.capacity)
    const continued = burst.content.length > sliced.length

    ctx.save()
    ctx.globalAlpha = alpha
    ctx.shadowColor = color
    ctx.shadowBlur = pinned ? 18 : 8
    ctx.beginPath()
    ctx.roundRect(rect.x, rect.y, rect.w, rect.h, 8)
    ctx.fillStyle = preferences.textDisplayMode === 'inline' ? 'rgba(8, 12, 24, 0.84)' : 'rgba(8, 12, 24, 0.74)'
    ctx.fill()
    ctx.shadowBlur = 0
    ctx.strokeStyle = colorAlpha(color, pinned ? 0.7 : 0.38)
    ctx.lineWidth = pinned ? 1.4 : 0.8
    ctx.stroke()

    ctx.fillStyle = colorAlpha(color, 0.2)
    ctx.fillRect(rect.x, rect.y, 4 * scale, rect.h)

    ctx.font = `bold ${9 * scale}px monospace`
    ctx.fillStyle = color
    ctx.textBaseline = 'top'
    ctx.textAlign = 'left'
    ctx.fillText(truncateText(ctx, burst.title, rect.w - pad * 2), rect.x + pad, rect.y + 8 * scale)

    ctx.font = `${font}px monospace`
    ctx.fillStyle = COLORS.textPrimary
    const lines = wrapLines(ctx, sliced, rect.w - pad * 2).slice(0, style.maxLines)
    for (let i = 0; i < lines.length; i++) {
      ctx.fillText(lines[i], rect.x + pad, rect.y + headerH + i * lineH)
    }
    if (continued || lines.length >= style.maxLines) {
      ctx.font = `${9 * scale}px monospace`
      ctx.fillStyle = colorAlpha(color, 0.9)
      ctx.fillText('continued in transcript', rect.x + pad, rect.y + rect.h - 16 * scale)
    }
    ctx.restore()
  }
}

export function drawPhaseTransitions(
  ctx: CanvasRenderingContext2D,
  transitions: PhaseTransition[],
  agents: Map<string, Agent>,
  time: number,
) {
  for (const transition of transitions) {
    const agent = agents.get(transition.agentId)
    if (!agent) continue
    const age = time - transition.timestamp
    if (age < 0 || age > 6) continue
    const progress = Math.min(1, age / 3.2)
    const alpha = Math.max(0, 1 - progress)
    const color = PHASE_COLORS[transition.phase] || COLORS.holoBase
    const radius = 42 + progress * 130
    ctx.save()
    ctx.globalAlpha = alpha
    ctx.strokeStyle = color
    ctx.lineWidth = Math.max(0.6, 2.5 * (1 - progress))
    drawHexagon(ctx, agent.x, agent.y, radius)
    ctx.stroke()
    ctx.font = '9px monospace'
    ctx.fillStyle = colorAlpha(color, 0.85)
    ctx.textAlign = 'center'
    ctx.fillText(transition.label.toUpperCase(), agent.x, agent.y - radius - 8)
    ctx.restore()
  }
}

export function drawArtifactVisuals(
  ctx: CanvasRenderingContext2D,
  artifacts: ArtifactVisual[],
  agents: Map<string, Agent>,
  time: number,
) {
  for (const artifact of artifacts) {
    const agent = agents.get(artifact.agentId)
    if (!agent) continue
    const age = time - artifact.timestamp
    if (age < 0 || age > 38) continue
    const alpha = age < 0.4 ? age / 0.4 : age > 28 ? Math.max(0, 1 - (age - 28) / 5) : 1
    if (alpha <= 0.02) continue
    const w = 180
    const h = 72
    const x = artifact.x - w / 2
    const y = artifact.y - h / 2
    ctx.save()
    ctx.globalAlpha = alpha
    ctx.strokeStyle = colorAlpha(COLORS.complete, 0.5)
    ctx.fillStyle = colorAlpha(COLORS.complete, 0.08)
    ctx.beginPath()
    ctx.roundRect(x, y, w, h, 8)
    ctx.fill()
    ctx.stroke()
    ctx.beginPath()
    ctx.moveTo(x + 16, y + 12)
    ctx.lineTo(x + 46, y + 12)
    ctx.lineTo(x + 56, y + 24)
    ctx.lineTo(x + 56, y + 50)
    ctx.lineTo(x + 16, y + 50)
    ctx.closePath()
    ctx.strokeStyle = COLORS.complete
    ctx.stroke()
    ctx.font = 'bold 9px monospace'
    ctx.fillStyle = COLORS.complete
    ctx.fillText(truncateText(ctx, artifact.title, 105), x + 66, y + 13)
    ctx.font = '8px monospace'
    ctx.fillStyle = COLORS.textMuted
    ctx.fillText(truncateText(ctx, artifact.content, 102), x + 66, y + 31)
    ctx.strokeStyle = colorAlpha(COLORS.complete, 0.2)
    ctx.beginPath()
    ctx.moveTo(agent.x, agent.y)
    ctx.lineTo(artifact.x, artifact.y)
    ctx.stroke()
    ctx.restore()
  }
}

export function drawErrorRecoveryVisuals(
  ctx: CanvasRenderingContext2D,
  visuals: ErrorRecoveryVisual[],
  agents: Map<string, Agent>,
  time: number,
) {
  for (const visual of visuals) {
    const agent = agents.get(visual.agentId)
    if (!agent) continue
    const age = time - visual.timestamp
    if (age < 0 || age > 10) continue
    const progress = Math.min(1, age / 2.8)
    const color = visual.stage === 'recovery' ? COLORS.complete : COLORS.error
    const alpha = Math.max(0, 1 - progress)
    ctx.save()
    ctx.globalAlpha = alpha
    ctx.strokeStyle = color
    ctx.lineWidth = visual.stage === 'recovery' ? 1.6 : 2
    if (visual.stage === 'error') {
      for (let i = 0; i < 6; i++) {
        const a = (i / 6) * Math.PI * 2 + progress
        ctx.beginPath()
        ctx.moveTo(agent.x + Math.cos(a) * 30, agent.y + Math.sin(a) * 30)
        ctx.lineTo(agent.x + Math.cos(a) * (80 + progress * 30), agent.y + Math.sin(a) * (80 + progress * 30))
        ctx.stroke()
      }
    } else {
      ctx.beginPath()
      ctx.arc(agent.x, agent.y, 38 + progress * 96, 0, Math.PI * 2)
      ctx.stroke()
    }
    ctx.font = '9px monospace'
    ctx.fillStyle = colorAlpha(color, 0.9)
    ctx.textAlign = 'center'
    ctx.fillText(visual.label.toUpperCase(), agent.x, agent.y + 74 + progress * 28)
    ctx.restore()
  }
}
