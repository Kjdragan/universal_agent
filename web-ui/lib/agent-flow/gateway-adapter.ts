import type { SimulationEvent } from './agent-types'
import type { SessionInfo } from './bridge-types'

const ORCHESTRATOR_AGENT = 'orchestrator'
const HEARTBEAT_AGENT = 'heartbeat-service'
const TEXT_BURST_MESSAGE_THRESHOLD = 220
const TEXT_BURST_TOOL_THRESHOLD = 260

type RawEvent = {
  type?: unknown
  data?: unknown
  session_id?: unknown
  timestamp?: unknown
}

type SessionState = {
  sessionId: string
  orchestratorSpawned: boolean
  sessionInfo?: SessionInfo
  toolCallsById: Map<string, { toolName: string; args: string; agent: string }>
  vpAgentsSeen: Set<string>
  hasUnresolvedError: boolean
}

export interface GatewayAdapterResult {
  sessionId: string | null
  session: SessionInfo | null
  events: SimulationEvent[]
}

export interface GatewayAgentFlowAdapter {
  ingest: (event: unknown) => GatewayAdapterResult
}

function asRecord(value: unknown): Record<string, unknown> | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null
  return value as Record<string, unknown>
}

function asString(value: unknown): string {
  return typeof value === 'string' ? value : ''
}

function stringifyUnknown(value: unknown): string {
  if (typeof value === 'string') return value
  if (value == null) return ''
  try {
    return JSON.stringify(value)
  } catch {
    return String(value)
  }
}

function truncate(text: string, max = 240): string {
  const normalized = text.trim()
  if (normalized.length <= max) return normalized
  return `${normalized.slice(0, Math.max(0, max - 3)).trimEnd()}...`
}

function sessionLabel(sessionId: string, hint?: string): string {
  const trimmed = (hint || '').trim()
  if (trimmed) return truncate(trimmed, 56)
  return `Session ${sessionId.slice(0, 6)}`
}

function sessionStatusFromHint(statusHint: string): SessionInfo['status'] {
  const status = statusHint.trim().toLowerCase()
  if (!status) return 'active'
  if (
    status === 'complete'
    || status === 'completed'
    || status === 'archived'
    || status === 'archived_incomplete'
    || status === 'cancelled'
    || status === 'failed'
  ) {
    return 'completed'
  }
  return 'active'
}

function summarizeToolArgs(input: Record<string, unknown>): string {
  const direct =
    asString(input.file_path)
    || asString(input.path)
    || asString(input.command)
    || asString(input.query)
    || asString(input.url)
    || asString(input.pattern)
    || asString(input.description)
  if (direct) return truncate(direct, 120)

  const pairs = Object.entries(input)
    .filter(([, value]) => value != null && value !== '')
    .slice(0, 3)
    .map(([key, value]) => `${key}=${truncate(stringifyUnknown(value), 48)}`)
  return truncate(pairs.join(', '), 120)
}

function summarizeToolResult(payload: Record<string, unknown>): string {
  const preview =
    asString(payload.content_preview)
    || asString(payload.result)
    || asString(payload.message)
  if (preview) return truncate(preview, 180)

  const raw = payload.content_raw
  if (typeof raw === 'string') return truncate(raw, 180)
  if (raw != null) return truncate(stringifyUnknown(raw), 180)
  return 'Done'
}

function roleFromAuthor(author: string): 'user' | 'assistant' {
  return author.trim().toLowerCase() === 'user' ? 'user' : 'assistant'
}

function systemEventSummary(eventType: string, payload: Record<string, unknown>): string {
  const summary =
    asString(payload.summary)
    || asString(payload.message)
    || asString(payload.text)
    || asString(payload.reason)
    || asString(payload.title)
    || asString(payload.objective)
  if (summary) return truncate(summary, 180)
  return truncate(`${eventType}: ${stringifyUnknown(payload)}`, 180)
}

function makeSimulationEvent(
  now: number,
  type: SimulationEvent['type'],
  payload: Record<string, unknown>,
  sessionId: string,
): SimulationEvent {
  return { time: now, type, payload, sessionId }
}

function visualId(sessionId: string, type: string, now: number): string {
  return `${sessionId}:${type}:${Math.round(now * 1000)}`
}

function pushPhaseEvent(
  events: SimulationEvent[],
  now: number,
  sessionId: string,
  phase: string,
  label: string,
  agent = ORCHESTRATOR_AGENT,
): void {
  events.push(makeSimulationEvent(now, 'phase_transition', {
    id: visualId(sessionId, `phase:${phase}`, now),
    agent,
    phase,
    label,
  }, sessionId))
}

function pushTextBurstEvent(args: {
  events: SimulationEvent[]
  now: number
  sessionId: string
  agent: string
  kind: string
  title: string
  content: string
  summary?: string
}): void {
  if (!args.content.trim()) return
  args.events.push(makeSimulationEvent(args.now, 'text_burst', {
    id: visualId(args.sessionId, `text:${args.kind}`, args.now),
    agent: args.agent,
    kind: args.kind,
    title: args.title,
    content: args.content,
    summary: args.summary || truncate(args.content, 260),
  }, args.sessionId))
}

function pushArtifactEvent(args: {
  events: SimulationEvent[]
  now: number
  sessionId: string
  agent: string
  title: string
  content: string
}): void {
  args.events.push(makeSimulationEvent(args.now, 'artifact_emitted', {
    id: visualId(args.sessionId, 'artifact', args.now),
    agent: args.agent,
    title: args.title,
    content: args.content || args.title,
  }, args.sessionId))
}

function pushErrorRecoveryEvent(args: {
  events: SimulationEvent[]
  now: number
  sessionId: string
  agent: string
  stage: 'error' | 'recovery'
  label: string
}): void {
  args.events.push(makeSimulationEvent(args.now, 'error_recovery', {
    id: visualId(args.sessionId, `error:${args.stage}`, args.now),
    agent: args.agent,
    stage: args.stage,
    label: args.label,
  }, args.sessionId))
}

function recoverAfterSuccessfulActivity(
  state: SessionState,
  events: SimulationEvent[],
  now: number,
  sessionId: string,
  agent = ORCHESTRATOR_AGENT,
): void {
  if (!state.hasUnresolvedError) return
  state.hasUnresolvedError = false
  pushErrorRecoveryEvent({
    events,
    now,
    sessionId,
    agent,
    stage: 'recovery',
    label: 'Recovered and continued',
  })
}

function sessionIdFromEvent(eventType: string, payload: Record<string, unknown>, raw: RawEvent): string {
  const nestedSession =
    asString(payload.session_id)
    || asString((asRecord(payload.session)?.session_id))
    || asString((asRecord(payload.payload)?.session_id))
    || asString(raw.session_id)
  if (nestedSession && nestedSession !== 'global_agent_flow') return nestedSession

  if (eventType === 'connected') {
    const connected = asString((asRecord(payload.session)?.session_id))
    if (connected && connected !== 'global_agent_flow') return connected
  }

  return ''
}

function buildSessionInfo(
  sessionId: string,
  previous: SessionInfo | undefined,
  options: {
    labelHint?: string
    statusHint?: string
    now: number
  },
): SessionInfo {
  return {
    id: sessionId,
    label: sessionLabel(sessionId, options.labelHint || previous?.label),
    status: sessionStatusFromHint(options.statusHint || previous?.status || 'active'),
    startTime: previous?.startTime ?? options.now,
    lastActivityTime: options.now,
  }
}

function ensureOrchestrator(
  state: SessionState,
  events: SimulationEvent[],
  now: number,
  sessionId: string,
  taskHint?: string,
): void {
  if (state.orchestratorSpawned) return
  events.push(
    makeSimulationEvent(now, 'agent_spawn', {
      name: ORCHESTRATOR_AGENT,
      isMain: true,
      task: taskHint || state.sessionInfo?.label || `Session ${sessionId.slice(0, 6)}`,
    }, sessionId),
  )
  state.orchestratorSpawned = true
}

function heartbeatLifecycleEvents(
  state: SessionState,
  sessionId: string,
  now: number,
  text: string,
): SimulationEvent[] {
  ensureOrchestrator(state, [], now, sessionId)
  return [
    makeSimulationEvent(now, 'subagent_dispatch', {
      parent: ORCHESTRATOR_AGENT,
      child: HEARTBEAT_AGENT,
      task: 'Heartbeat check',
    }, sessionId),
    makeSimulationEvent(now, 'agent_spawn', {
      name: HEARTBEAT_AGENT,
      parent: ORCHESTRATOR_AGENT,
      task: 'Heartbeat check',
    }, sessionId),
    makeSimulationEvent(now, 'message', {
      agent: HEARTBEAT_AGENT,
      role: 'assistant',
      content: text,
    }, sessionId),
    makeSimulationEvent(now, 'subagent_return', {
      parent: ORCHESTRATOR_AGENT,
      child: HEARTBEAT_AGENT,
      summary: truncate(text, 72),
    }, sessionId),
    makeSimulationEvent(now, 'agent_complete', {
      name: HEARTBEAT_AGENT,
    }, sessionId),
  ]
}

function vpLifecycleEvents(
  state: SessionState,
  sessionId: string,
  now: number,
  eventType: string,
  payload: Record<string, unknown>,
): SimulationEvent[] {
  const vpId = asString(payload.vp_id) || 'worker'
  const missionId = asString(payload.mission_id)
  const child = `vp-${vpId}`
  const objective = asString(payload.objective) || 'Delegated mission'
  const summary = systemEventSummary(eventType, payload)
  const events: SimulationEvent[] = []

  if (!state.vpAgentsSeen.has(child)) {
    state.vpAgentsSeen.add(child)
    events.push(
      makeSimulationEvent(now, 'subagent_dispatch', {
        parent: ORCHESTRATOR_AGENT,
        child,
        task: truncate(objective || missionId || 'VP mission', 72),
      }, sessionId),
    )
    events.push(
      makeSimulationEvent(now, 'agent_spawn', {
        name: child,
        parent: ORCHESTRATOR_AGENT,
        task: truncate(objective || missionId || 'VP mission', 72),
      }, sessionId),
    )
  } else if (eventType.endsWith('.started') || eventType.endsWith('.claimed') || eventType.endsWith('.dispatched')) {
    events.push(
      makeSimulationEvent(now, 'agent_spawn', {
        name: child,
        parent: ORCHESTRATOR_AGENT,
        task: truncate(objective || missionId || 'VP mission', 72),
      }, sessionId),
    )
  }

  events.push(
    makeSimulationEvent(now, 'message', {
      agent: child,
      role: 'assistant',
      content: summary,
    }, sessionId),
  )

  if (
    eventType.endsWith('.completed')
    || eventType.endsWith('.failed')
    || eventType.endsWith('.cancelled')
  ) {
    events.push(
      makeSimulationEvent(now, 'subagent_return', {
        parent: ORCHESTRATOR_AGENT,
        child,
        summary: truncate(summary, 72),
      }, sessionId),
    )
    events.push(
      makeSimulationEvent(now, 'agent_complete', {
        name: child,
      }, sessionId),
    )
  }

  return events
}

export function createGatewayAgentFlowAdapter(nowProvider: () => number = () => Date.now() / 1000): GatewayAgentFlowAdapter {
  const sessionState = new Map<string, SessionState>()

  function getSessionState(sessionId: string): SessionState {
    const existing = sessionState.get(sessionId)
    if (existing) return existing
      const created: SessionState = {
        sessionId,
        orchestratorSpawned: false,
        toolCallsById: new Map(),
        vpAgentsSeen: new Set(),
        hasUnresolvedError: false,
      }
    sessionState.set(sessionId, created)
    return created
  }

  return {
    ingest(event: unknown): GatewayAdapterResult {
      const raw = (asRecord(event) || {}) as RawEvent
      const eventType = asString(raw.type)
      const payload = asRecord(raw.data) || {}
      const sessionId = sessionIdFromEvent(eventType, payload, raw)
      const now = nowProvider()

      if (!eventType || !sessionId) {
        return { sessionId: null, session: null, events: [] }
      }

      const state = getSessionState(sessionId)
      let session = buildSessionInfo(sessionId, state.sessionInfo, { now })
      const events: SimulationEvent[] = []

      switch (eventType) {
        case 'connected':
          session = buildSessionInfo(sessionId, state.sessionInfo, {
            now,
            labelHint: session.label,
          })
          break

          case 'session_info': {
          const sessionPayload = asRecord(payload.session) || payload
          session = buildSessionInfo(sessionId, state.sessionInfo, {
            now,
            labelHint: asString(sessionPayload.workspace) || asString(sessionPayload.workspace_dir),
            })
            ensureOrchestrator(state, events, now, sessionId, session.label)
            pushPhaseEvent(events, now, sessionId, 'start', 'Session attached')
            break
          }

        case 'status': {
          const status = asString(payload.status)
          const query = asString(payload.query)
          const source = asString(payload.source)
          const labelHint =
            query
            || (source ? `${source}: ${sessionId.slice(0, 6)}` : '')
            || state.sessionInfo?.label
          session = buildSessionInfo(sessionId, state.sessionInfo, {
            now,
            labelHint,
            statusHint: status,
          })

            if (status === 'processing') {
              ensureOrchestrator(state, events, now, sessionId, labelHint)
              pushPhaseEvent(events, now, sessionId, query ? 'input' : 'thinking', query ? 'Input received' : 'Processing')
            } else if (status && status !== 'engine_complete' && status !== 'tools_complete') {
              ensureOrchestrator(state, events, now, sessionId, labelHint)
            const content =
              payload.goal_satisfaction != null
                ? `${status}: ${truncate(stringifyUnknown(payload.goal_satisfaction), 160)}`
                : status.replaceAll('_', ' ')
            events.push(
              makeSimulationEvent(now, 'message', {
                agent: ORCHESTRATOR_AGENT,
                role: 'assistant',
                content,
                }, sessionId),
              )
              pushPhaseEvent(events, now, sessionId, status.includes('tool') ? 'tools' : 'thinking', status.replaceAll('_', ' '))
            }
            break
          }

        case 'text': {
          ensureOrchestrator(state, events, now, sessionId, state.sessionInfo?.label)
          const text = asString(payload.text)
            if (text) {
              events.push(
              makeSimulationEvent(now, 'message', {
                agent: ORCHESTRATOR_AGENT,
                role: roleFromAuthor(asString(payload.author)),
                content: text,
                }, sessionId),
              )
              const role = roleFromAuthor(asString(payload.author))
              if (text.length >= TEXT_BURST_MESSAGE_THRESHOLD) {
                pushTextBurstEvent({
                  events,
                  now,
                  sessionId,
                  agent: ORCHESTRATOR_AGENT,
                  kind: role,
                  title: role === 'user' ? 'USER INPUT' : 'CLAUDE OUTPUT',
                  content: text,
                })
              }
              if (role === 'user') {
                pushPhaseEvent(events, now, sessionId, 'input', 'User input')
              } else {
                recoverAfterSuccessfulActivity(state, events, now, sessionId)
              }
            }
            break
          }

        case 'thinking': {
          ensureOrchestrator(state, events, now, sessionId, state.sessionInfo?.label)
          const text = asString(payload.thinking)
          if (text) {
            events.push(
              makeSimulationEvent(now, 'message', {
                agent: ORCHESTRATOR_AGENT,
                role: 'thinking',
                content: text,
              }, sessionId),
            )
          }
          break
        }

        case 'tool_call': {
          ensureOrchestrator(state, events, now, sessionId, state.sessionInfo?.label)
          const toolName = asString(payload.name) || 'Tool'
          const toolId = asString(payload.id)
          const inputData = asRecord(payload.input) || {}
          const args = summarizeToolArgs(inputData)
          if (toolId) {
            state.toolCallsById.set(toolId, {
              toolName,
              args,
              agent: ORCHESTRATOR_AGENT,
            })
          }
            events.push(
              makeSimulationEvent(now, 'tool_call_start', {
              agent: ORCHESTRATOR_AGENT,
              tool: toolName,
              args,
              inputData,
              }, sessionId),
            )
            pushPhaseEvent(events, now, sessionId, 'tools', toolName)
            break
          }

          case 'tool_result': {
            ensureOrchestrator(state, events, now, sessionId, state.sessionInfo?.label)
            const toolId = asString(payload.tool_use_id) || asString(payload.tool_call_id) || asString(payload.id)
            const call = toolId ? state.toolCallsById.get(toolId) : undefined
            if (toolId) state.toolCallsById.delete(toolId)
            const result = summarizeToolResult(payload)
            const isError = Boolean(payload.is_error)
            events.push(
              makeSimulationEvent(now, 'tool_call_end', {
                agent: call?.agent || ORCHESTRATOR_AGENT,
                tool: call?.toolName || asString(payload.name) || 'Tool',
                result,
                isError,
                errorMessage: isError ? result : undefined,
              }, sessionId),
            )
            if (isError) {
              state.hasUnresolvedError = true
              pushErrorRecoveryEvent({
                events,
                now,
                sessionId,
                agent: call?.agent || ORCHESTRATOR_AGENT,
                stage: 'error',
                label: `${call?.toolName || asString(payload.name) || 'Tool'} failed`,
              })
              pushTextBurstEvent({
                events,
                now,
                sessionId,
                agent: call?.agent || ORCHESTRATOR_AGENT,
                kind: 'tool',
                title: 'TOOL ERROR',
                content: result,
              })
            } else {
              recoverAfterSuccessfulActivity(state, events, now, sessionId, call?.agent || ORCHESTRATOR_AGENT)
              if (result.length >= TEXT_BURST_TOOL_THRESHOLD) {
                pushTextBurstEvent({
                  events,
                  now,
                  sessionId,
                  agent: call?.agent || ORCHESTRATOR_AGENT,
                  kind: 'tool',
                  title: `${call?.toolName || asString(payload.name) || 'Tool'} RESULT`,
                  content: result,
                })
              }
              if ((call?.toolName || asString(payload.name)).toLowerCase().match(/write|artifact|publish|report/)) {
                pushArtifactEvent({
                  events,
                  now,
                  sessionId,
                  agent: call?.agent || ORCHESTRATOR_AGENT,
                  title: call?.toolName || asString(payload.name) || 'Artifact',
                  content: result,
                })
              }
            }
            break
          }

        case 'auth_required': {
          ensureOrchestrator(state, events, now, sessionId, state.sessionInfo?.label)
            events.push(
              makeSimulationEvent(now, 'permission_requested', {
              agent: ORCHESTRATOR_AGENT,
              message: asString(payload.auth_link) || 'Permission required',
              title: 'Permission required',
              }, sessionId),
            )
            pushPhaseEvent(events, now, sessionId, 'thinking', 'Permission required')
            break
          }

        case 'iteration_end': {
          ensureOrchestrator(state, events, now, sessionId, state.sessionInfo?.label)
            events.push(
              makeSimulationEvent(now, 'agent_complete', {
                name: ORCHESTRATOR_AGENT,
              }, sessionId),
            )
            pushPhaseEvent(events, now, sessionId, 'completion', 'Complete')
            break
          }

          case 'query_complete': {
            ensureOrchestrator(state, events, now, sessionId, state.sessionInfo?.label)
            if (payload.completed !== false) {
            events.push(
              makeSimulationEvent(now, 'agent_complete', {
                name: ORCHESTRATOR_AGENT,
                }, sessionId),
              )
              pushPhaseEvent(events, now, sessionId, 'completion', 'Complete')
            }
            break
          }

        case 'system_event': {
          ensureOrchestrator(state, events, now, sessionId, state.sessionInfo?.label)
          const systemType = asString(payload.type)
          const systemPayload = asRecord(payload.payload) || payload
          if (systemType === 'heartbeat_summary' || systemType === 'heartbeat_indicator') {
            events.push(...heartbeatLifecycleEvents(
              state,
              sessionId,
              now,
              systemEventSummary(systemType, systemPayload),
            ))
            } else if (systemType === 'vp_mission_event') {
              pushPhaseEvent(events, now, sessionId, 'delegation', 'VP mission')
              events.push(...vpLifecycleEvents(
              state,
              sessionId,
              now,
              asString(systemPayload.event_type) || systemType,
              systemPayload,
            ))
            } else {
              if (systemType.toLowerCase().includes('artifact') || systemType.toLowerCase().includes('work_product')) {
                pushArtifactEvent({
                  events,
                  now,
                  sessionId,
                  agent: ORCHESTRATOR_AGENT,
                  title: systemType || 'Artifact',
                  content: systemEventSummary(systemType || 'system_event', systemPayload),
                })
              }
              events.push(
              makeSimulationEvent(now, 'message', {
                agent: ORCHESTRATOR_AGENT,
                role: 'assistant',
                content: systemEventSummary(systemType || 'system_event', systemPayload),
              }, sessionId),
            )
            }
            break
          }

          case 'work_product': {
            ensureOrchestrator(state, events, now, sessionId, state.sessionInfo?.label)
            const title = asString(payload.title) || asString(payload.name) || 'Work product'
            const content = systemEventSummary(eventType, payload)
            pushArtifactEvent({
              events,
              now,
              sessionId,
              agent: ORCHESTRATOR_AGENT,
              title,
              content,
            })
            pushTextBurstEvent({
              events,
              now,
              sessionId,
              agent: ORCHESTRATOR_AGENT,
              kind: 'artifact',
              title: title.toUpperCase(),
              content,
            })
            break
          }

        default:
          break
      }

      state.sessionInfo = session
      return {
        sessionId,
        session,
        events,
      }
    },
  }
}
