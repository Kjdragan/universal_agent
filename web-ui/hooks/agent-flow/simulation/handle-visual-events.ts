import type {
  ArtifactVisual,
  ErrorRecoveryVisual,
  PhaseTransition,
  TextBurst,
  TextBurstKind,
  PhaseTransitionKind,
} from '@/lib/agent-flow/agent-types'
import type { MutableEventState } from './process-event'
import { asString } from './types'

const MAX_TEXT_BURSTS = 24
const MAX_PHASE_TRANSITIONS = 36
const MAX_ARTIFACT_VISUALS = 18
const MAX_ERROR_RECOVERY_VISUALS = 24

function capTail<T>(items: T[], max: number): T[] {
  return items.length > max ? items.slice(items.length - max) : items
}

function agentOffset(agentName: string, currentTime: number, index: number): { x: number; y: number } {
  const seed = Array.from(agentName).reduce((acc, ch) => acc + ch.charCodeAt(0), 0)
  const direction = ((seed + index) % 2) === 0 ? 1 : -1
  return {
    x: direction * (180 + (index % 3) * 42),
    y: -120 + ((Math.round(currentTime * 10) + index) % 4) * 92,
  }
}

export function handleTextBurst(
  payload: Record<string, unknown>,
  currentTime: number,
  state: MutableEventState,
): void {
  const agentId = asString(payload.agent)
  const content = asString(payload.content)
  if (!agentId || !content) return
  const agent = state.agents.get(agentId)
  if (!agent) return

  const offset = agentOffset(agentId, currentTime, state.textBursts.length)
  const burst: TextBurst = {
    id: asString(payload.id) || `text-burst-${agentId}-${currentTime}-${state.textBursts.length}`,
    agentId,
    kind: (asString(payload.kind) || 'assistant') as TextBurstKind,
    title: asString(payload.title) || 'TEXT',
    content,
    summary: asString(payload.summary) || content.slice(0, 220),
    x: agent.x + offset.x,
    y: agent.y + offset.y,
    timestamp: currentTime,
  }

  state.textBursts = capTail([...state.textBursts, burst], MAX_TEXT_BURSTS)
}

export function handlePhaseTransition(
  payload: Record<string, unknown>,
  currentTime: number,
  state: MutableEventState,
): void {
  const agentId = asString(payload.agent)
  const agent = state.agents.get(agentId)
  if (!agent) return
  const transition: PhaseTransition = {
    id: asString(payload.id) || `phase-${agentId}-${currentTime}-${state.phaseTransitions.length}`,
    agentId,
    phase: (asString(payload.phase) || 'thinking') as PhaseTransitionKind,
    label: asString(payload.label) || 'Phase',
    timestamp: currentTime,
  }
  state.phaseTransitions = capTail([...state.phaseTransitions, transition], MAX_PHASE_TRANSITIONS)
}

export function handleArtifactEmitted(
  payload: Record<string, unknown>,
  currentTime: number,
  state: MutableEventState,
): void {
  const agentId = asString(payload.agent)
  const agent = state.agents.get(agentId)
  if (!agent) return
  const offset = agentOffset(agentId, currentTime, state.artifactVisuals.length)
  const artifact: ArtifactVisual = {
    id: asString(payload.id) || `artifact-${agentId}-${currentTime}-${state.artifactVisuals.length}`,
    agentId,
    title: asString(payload.title) || 'Artifact',
    content: asString(payload.content) || asString(payload.summary) || 'Output produced',
    x: agent.x + offset.x * 0.85,
    y: agent.y + offset.y + 92,
    timestamp: currentTime,
  }
  state.artifactVisuals = capTail([...state.artifactVisuals, artifact], MAX_ARTIFACT_VISUALS)
}

export function handleErrorRecovery(
  payload: Record<string, unknown>,
  currentTime: number,
  state: MutableEventState,
): void {
  const agentId = asString(payload.agent)
  const agent = state.agents.get(agentId)
  if (!agent) return
  const visual: ErrorRecoveryVisual = {
    id: asString(payload.id) || `recovery-${agentId}-${currentTime}-${state.errorRecoveryVisuals.length}`,
    agentId,
    stage: asString(payload.stage) === 'recovery' ? 'recovery' : 'error',
    label: asString(payload.label) || (asString(payload.stage) === 'recovery' ? 'Recovered' : 'Error'),
    timestamp: currentTime,
  }
  state.errorRecoveryVisuals = capTail([...state.errorRecoveryVisuals, visual], MAX_ERROR_RECOVERY_VISUALS)
}
