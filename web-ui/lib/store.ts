/**
 * Zustand store for Universal Agent state management.
 *
 * Manages sessions, messages, tool calls, work products, and UI state.
 */

import { create } from "zustand";
import {
  Session,
  Message,
  ToolCall,
  WorkProduct,
  ConnectionStatus,
  ViewMode,
  SessionInfo,
  WebSocketEvent,
} from "@/types/agent";
import { generateId } from "./utils";

// =============================================================================
// Agent Store State
// =============================================================================

interface AgentStore {
  // Connection
  connectionStatus: ConnectionStatus;
  setConnectionStatus: (status: ConnectionStatus) => void;

  // Session
  currentSession: SessionInfo | null;
  sessions: Session[];
  setCurrentSession: (session: SessionInfo | null) => void;
  setSessions: (sessions: Session[]) => void;
  sessionAttachMode: "default" | "tail";
  setSessionAttachMode: (mode: "default" | "tail") => void;

  // Messages
  messages: Message[];
  addMessage: (message: Omit<Message, "id" | "timestamp">) => void;
  updateMessage: (id: string, updates: Partial<Message>) => void;
  clearMessages: () => void;

  // Current streaming message
  currentStreamingMessage: string;
  currentStreamType: "text" | "thinking" | null;
  currentAuthor?: string;
  currentOffset?: number;
  appendToStream: (text: string, author?: string, offset?: number) => void;
  finishStream: () => void;

  // Tool calls
  toolCalls: ToolCall[];
  addToolCall: (toolCall: ToolCall) => void;
  updateToolCall: (id: string, updates: Partial<ToolCall>) => void;
  clearToolCalls: () => void;

  // Work products
  workProducts: WorkProduct[];
  addWorkProduct: (product: WorkProduct) => void;
  clearWorkProducts: () => void;

  // Thinking
  currentThinking: string;
  setCurrentThinking: (thinking: string) => void;

  // Token usage
  tokenUsage: {
    input: number;
    output: number;
    total: number;
  };
  updateTokenUsage: (usage: { input: number; output: number; total: number }) => void;

  // UI State
  viewMode: ViewMode;
  setViewMode: (mode: Partial<ViewMode>) => void;

  // Metrics
  startTime: number | null;
  toolCallCount: number;
  iterationCount: number;

  setStartTime: (time: number | null) => void;
  incrementToolCalls: () => void;
  incrementIterations: () => void;

  // Error
  lastError: string | null;
  setLastError: (error: string | null) => void;

  // UI Viewing State
  viewingFile: { name: string; path: string; content?: string; type: string } | null;
  setViewingFile: (file: { name: string; path: string; content?: string; type: string } | null) => void;



  // Logs (real-time tool output)
  logs: Array<{
    id: string;
    message: string;
    level: string;
    prefix: string;
    timestamp: number;
    event_kind?: string;
    metadata?: Record<string, unknown>;
  }>;
  addLog: (log: {
    message: string;
    level: string;
    prefix: string;
    event_kind?: string;
    metadata?: Record<string, unknown>;
  }) => void;
  clearLogs: () => void;

  // System events + presence
  systemEvents: Array<{
    id: string;
    event_type: string;
    payload: Record<string, unknown>;
    created_at?: string;
    session_id?: string;
    timestamp: number;
  }>;
  addSystemEvent: (event: {
    event_type: string;
    payload: Record<string, unknown>;
    created_at?: string;
    session_id?: string;
  }) => void;
  clearSystemEvents: () => void;
  systemPresence: Array<{
    node_id: string;
    status: string;
    reason?: string | null;
    metadata?: Record<string, unknown>;
    updated_at?: string;
    timestamp: number;
  }>;
  setSystemPresence: (presence: {
    node_id: string;
    status: string;
    reason?: string | null;
    metadata?: Record<string, unknown>;
    updated_at?: string;
  }) => void;
  clearSystemPresence: () => void;

  // Reset
  reset: () => void;
}

type VpMissionEventPayload = {
  event_type?: string;
  mission_id?: string;
  vp_id?: string;
  mission_status?: string;
  result_ref?: string;
  objective?: string;
  event_payload?: Record<string, unknown>;
};

function asObjectRecord(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : {};
}

function asTrimmedText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function asTrimmedTextArray(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return value
    .map((item) => asTrimmedText(item))
    .filter(Boolean);
}

function workspacePathFromResultRef(resultRef: string): string {
  if (!resultRef.startsWith("workspace://")) return "";
  return resultRef.replace("workspace://", "").trim();
}

function classifyArtifactPaths(paths: string[]): { primary: string[]; supporting: string[] } {
  const unique = Array.from(new Set(paths.filter(Boolean)));
  const supportingPatterns = [
    /\/run\.log$/i,
    /\/trace\.json$/i,
    /\/trace_catalog\.(md|json)$/i,
    /\/transcript\.md$/i,
    /\/capabilities\.md$/i,
    /\/work_products\/logfire-eval\//i,
    /\/mission_receipt\.json$/i,
    /\/sync_ready\.json$/i,
  ];
  const supporting: string[] = [];
  const primary: string[] = [];
  for (const path of unique) {
    if (supportingPatterns.some((pattern) => pattern.test(path))) {
      supporting.push(path);
    } else {
      primary.push(path);
    }
  }
  return { primary, supporting };
}

const BLOB_FIELD_KEYS = new Set(["content", "image_base64", "base64_data"]);

function looksBase64Blob(value: string): boolean {
  const normalized = String(value || "").replace(/\s+/g, "");
  return normalized.length > 512 && /^[A-Za-z0-9+/=_-]+$/.test(normalized);
}

function redactBlobField(key: string, value: string): string {
  const chars = String(value || "").length;
  return `[redacted ${key}: ${chars} chars]`;
}

function sanitizePreviewString(value: string): string {
  const text = String(value || "");
  const trimmed = text.trim();
  if (!trimmed) return text;

  if (trimmed.startsWith("{") || trimmed.startsWith("[")) {
    try {
      return JSON.stringify(sanitizeToolPayload(JSON.parse(trimmed)), null, 2);
    } catch {
      // Fall through to blob detection below.
    }
  }

  if (looksBase64Blob(trimmed)) {
    return redactBlobField("content_preview", trimmed);
  }
  return text;
}

export function sanitizeToolPayload(payload: unknown): unknown {
  if (Array.isArray(payload)) {
    return payload.map((item) => sanitizeToolPayload(item));
  }

  if (!payload || typeof payload !== "object") {
    if (typeof payload === "string" && looksBase64Blob(payload)) {
      return redactBlobField("content", payload);
    }
    return payload;
  }

  const row = payload as Record<string, unknown>;
  const next: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(row)) {
    if (typeof value === "string") {
      if (BLOB_FIELD_KEYS.has(key) && value) {
        next[key] = redactBlobField(key, value);
        continue;
      }
      if (key === "content_preview") {
        next[key] = sanitizePreviewString(value);
        continue;
      }
    }
    next[key] = sanitizeToolPayload(value);
  }
  return next;
}

function vpMissionEventLevel(eventType: string): "INFO" | "WARN" | "ERROR" {
  if (eventType.endsWith(".failed")) return "ERROR";
  if (eventType.endsWith(".cancelled") || eventType.endsWith(".cancel_requested")) return "WARN";
  return "INFO";
}

function vpMissionEventActivityLog(
  eventPayload: VpMissionEventPayload,
): {
  message: string;
  level: string;
  prefix: string;
  event_kind?: string;
  metadata?: Record<string, unknown>;
} | null {
  const eventType = asTrimmedText(eventPayload.event_type);
  if (!eventType) return null;

  const missionId = asTrimmedText(eventPayload.mission_id);
  const vpId = asTrimmedText(eventPayload.vp_id);
  const missionStatus = asTrimmedText(eventPayload.mission_status);
  const resultRef = asTrimmedText(eventPayload.result_ref);
  const objective = asTrimmedText(eventPayload.objective);
  const nestedPayload = asObjectRecord(eventPayload.event_payload);
  const artifactRelpath = asTrimmedText(nestedPayload.artifact_relpath);
  const receiptRelpath = asTrimmedText(nestedPayload.mission_receipt_relpath);
  const syncMarkerRelpath = asTrimmedText(nestedPayload.sync_ready_marker_relpath);
  const artifactRelpaths = asTrimmedTextArray(nestedPayload.artifact_relpaths);
  const resultPath = workspacePathFromResultRef(resultRef);
  const artifactPath =
    resultPath && artifactRelpath
      ? `${resultPath.replace(/\/+$/, "")}/${artifactRelpath.replace(/^\/+/, "")}`
      : "";
  const artifactPaths = resultPath
    ? Array.from(
      new Set(
        artifactRelpaths.map((relpath) => `${resultPath.replace(/\/+$/, "")}/${relpath.replace(/^\/+/, "")}`),
      ),
    )
    : [];
  const receiptPath =
    resultPath && receiptRelpath
      ? `${resultPath.replace(/\/+$/, "")}/${receiptRelpath.replace(/^\/+/, "")}`
      : "";
  const syncMarkerPath =
    resultPath && syncMarkerRelpath
      ? `${resultPath.replace(/\/+$/, "")}/${syncMarkerRelpath.replace(/^\/+/, "")}`
      : "";

  const messageParts = [
    `VP lifecycle event ${eventType}`,
    missionId ? `mission=${missionId}` : "",
    vpId ? `vp=${vpId}` : "",
    missionStatus ? `status=${missionStatus}` : "",
    resultRef ? `result_ref=${resultRef}` : "",
    resultPath ? `result_path=${resultPath}` : "",
    artifactPath ? `artifact_path=${artifactPath}` : "",
    artifactPaths.length ? `artifact_paths=${artifactPaths.join(",")}` : "",
    receiptPath ? `mission_receipt_path=${receiptPath}` : "",
    syncMarkerPath ? `sync_ready_marker_path=${syncMarkerPath}` : "",
    objective ? `objective=${objective}` : "",
  ].filter(Boolean);

  return {
    message: messageParts.join(" | "),
    level: vpMissionEventLevel(eventType),
    prefix: "VP",
    event_kind: "vp_mission_event",
    metadata: {
      event_type: eventType,
      mission_id: missionId || undefined,
      vp_id: vpId || undefined,
      mission_status: missionStatus || undefined,
      result_ref: resultRef || undefined,
      result_path: resultPath || undefined,
      artifact_path: artifactPath || undefined,
      artifact_paths: artifactPaths.length ? artifactPaths : undefined,
      mission_receipt_path: receiptPath || undefined,
      sync_ready_marker_path: syncMarkerPath || undefined,
      payload: nestedPayload,
    },
  };
}

function isTerminalVpMissionEvent(eventType: string): boolean {
  const normalized = String(eventType || "").trim().toLowerCase();
  return (
    normalized === "vp.mission.completed"
    || normalized === "vp.mission.failed"
    || normalized === "vp.mission.cancelled"
  );
}

function vpMissionTerminalChatNotice(eventPayload: VpMissionEventPayload): string | null {
  const eventType = asTrimmedText(eventPayload.event_type);
  if (!isTerminalVpMissionEvent(eventType)) return null;

  const missionId = asTrimmedText(eventPayload.mission_id) || "unknown_mission";
  const status = eventType.replace(/^vp\.mission\./, "").toUpperCase();
  const resultRef = asTrimmedText(eventPayload.result_ref);
  const resultPath = workspacePathFromResultRef(resultRef);
  const objective = asTrimmedText(eventPayload.objective);
  const nestedPayload = asObjectRecord(eventPayload.event_payload);
  const artifactRelpath = asTrimmedText(nestedPayload.artifact_relpath);
  const artifactRelpaths = asTrimmedTextArray(nestedPayload.artifact_relpaths);
  const receiptRelpath = asTrimmedText(nestedPayload.mission_receipt_relpath);
  const syncMarkerRelpath = asTrimmedText(nestedPayload.sync_ready_marker_relpath);
  const outcomeMessage =
    asTrimmedText(nestedPayload.message) || asTrimmedText(nestedPayload.final_text);
  const artifactPath =
    resultPath && artifactRelpath
      ? `${resultPath.replace(/\/+$/, "")}/${artifactRelpath.replace(/^\/+/, "")}`
      : "";
  const artifactPaths = resultPath
    ? Array.from(
      new Set(
        artifactRelpaths.map((relpath) => `${resultPath.replace(/\/+$/, "")}/${relpath.replace(/^\/+/, "")}`),
      ),
    )
    : [];
  const listedArtifactPaths = artifactPath
    ? artifactPaths.filter((candidate) => candidate !== artifactPath)
    : artifactPaths;
  const allArtifactPaths = artifactPath ? [artifactPath, ...listedArtifactPaths] : listedArtifactPaths;
  const { primary: primaryArtifactPaths, supporting: supportingArtifactPaths } =
    classifyArtifactPaths(allArtifactPaths);
  const receiptPath =
    resultPath && receiptRelpath
      ? `${resultPath.replace(/\/+$/, "")}/${receiptRelpath.replace(/^\/+/, "")}`
      : "";
  const syncMarkerPath =
    resultPath && syncMarkerRelpath
      ? `${resultPath.replace(/\/+$/, "")}/${syncMarkerRelpath.replace(/^\/+/, "")}`
      : "";

  const lines = [
    `VP task update (${status}): ${missionId}`,
    objective ? `Objective: ${objective}` : "",
    outcomeMessage ? `VP summary: ${outcomeMessage}` : "",
    resultRef ? `Result: ${resultRef}` : "",
    resultPath ? `Result workspace: ${resultPath}` : "",
    ...primaryArtifactPaths.map((path) => `Primary work product: ${path}`),
    ...supportingArtifactPaths.map((path) => `Supporting artifact: ${path}`),
    receiptPath ? `Receipt: ${receiptPath}` : "",
    syncMarkerPath ? `Sync Marker: ${syncMarkerPath}` : "",
  ].filter(Boolean);
  return lines.length > 0 ? lines.join("\n") : null;
}

// =============================================================================
// Create Store
// =============================================================================

export const useAgentStore = create<AgentStore>((set, get) => ({
  // Connection
  connectionStatus: "disconnected",
  setConnectionStatus: (status) => set({ connectionStatus: status }),

  // Session
  currentSession: null,
  sessions: [],
  setCurrentSession: (session) => set({ currentSession: session }),
  setSessions: (sessions) => set({ sessions }),
  sessionAttachMode: "default",
  setSessionAttachMode: (mode) => set({ sessionAttachMode: mode }),

  // Messages
  messages: [],
  addMessage: (message) => set((state) => ({
    messages: [
      ...state.messages,
      {
        ...message,
        id: generateId(),
        timestamp: Date.now(),
        time_offset: message.time_offset ?? 0,
      },
    ],
  })),
  updateMessage: (id, updates) => set((state) => ({
    messages: state.messages.map((m) => (m.id === id ? { ...m, ...updates } : m)),
  })),
  clearMessages: () => set({ messages: [] }),

  // Current streaming message
  currentStreamingMessage: "",
  currentStreamType: null,
  currentAuthor: undefined,
  currentOffset: undefined,
  appendToStream: (text, author, offset) => set((state) => {
    const incomingAuthor = author || "Simone";
    const currentAuthor = state.currentAuthor || incomingAuthor;

    // ── Type transition: if we were accumulating thinking, finalize it first ──
    if (state.currentStreamType === "thinking" && state.currentThinking.trim()) {
      const thinkingMessage: Message = {
        id: generateId(),
        role: "assistant",
        content: "",
        timestamp: Date.now(),
        time_offset: state.currentOffset ?? 0,
        is_complete: true,
        author: currentAuthor,
        messageType: "thought",
        thinking: state.currentThinking,
      };
      return {
        messages: [...state.messages, thinkingMessage],
        currentThinking: "",
        currentStreamingMessage: text,
        currentStreamType: "text",
        currentAuthor: incomingAuthor,
        currentOffset: offset !== undefined ? offset : undefined,
      };
    }

    // ── Author change: finalize old text stream ──
    if (state.currentStreamingMessage.trim() && incomingAuthor !== currentAuthor) {
      const finishedMessage: Message = {
        id: generateId(),
        role: "assistant",
        content: state.currentStreamingMessage,
        timestamp: Date.now(),
        time_offset: state.currentOffset ?? 0,
        is_complete: true,
        author: currentAuthor,
      };
      return {
        messages: [...state.messages, finishedMessage],
        currentStreamingMessage: text,
        currentStreamType: "text",
        currentAuthor: incomingAuthor,
        currentOffset: offset !== undefined ? offset : undefined,
        currentThinking: "",
      };
    }

    // ── Same author, same type (text) — just append ──
    return {
      currentStreamingMessage: state.currentStreamingMessage + text,
      currentStreamType: "text",
      currentAuthor: incomingAuthor,
      currentOffset: offset !== undefined ? offset : state.currentOffset,
    };
  }),
  finishStream: () => set((state) => {
    const messagesToAdd: Message[] = [];

    // Finalize any pending thinking
    if (state.currentThinking.trim()) {
      messagesToAdd.push({
        id: generateId(),
        role: "assistant",
        content: "",
        timestamp: Date.now(),
        time_offset: state.currentOffset ?? 0,
        is_complete: true,
        author: state.currentAuthor,
        messageType: "thought",
        thinking: state.currentThinking,
      });
    }

    // Finalize any pending text stream
    if (state.currentStreamingMessage.trim()) {
      messagesToAdd.push({
        id: generateId(),
        role: "assistant",
        content: state.currentStreamingMessage,
        timestamp: Date.now(),
        time_offset: state.currentOffset ?? 0,
        is_complete: true,
        author: state.currentAuthor,
      });
    }

    return {
      messages: messagesToAdd.length > 0
        ? [...state.messages, ...messagesToAdd]
        : state.messages,
      currentStreamingMessage: "",
      currentStreamType: null,
      currentAuthor: undefined,
      currentThinking: "",
    };
  }),

  // Tool calls
  toolCalls: [],
  addToolCall: (toolCall) => set((state) => {
    // Tool call IDs are expected to be unique (tool_use_id / call_...).
    // In practice we can receive duplicates (reconnects, retries, WS replay).
    // Deduplicate by id to avoid React "duplicate key" errors and state bloat.
    const id = toolCall.id || generateId();
    const existingIdx = state.toolCalls.findIndex((tc) => tc.id === id);
    if (existingIdx >= 0) {
      const next = [...state.toolCalls];
      next[existingIdx] = {
        ...next[existingIdx],
        ...toolCall,
        id,
        input: sanitizeToolPayload(toolCall.input) as Record<string, unknown>,
        // Preserve the original "first seen" timestamp unless absent.
        timestamp: next[existingIdx].timestamp ?? Date.now(),
      };
      return { toolCalls: next };
    }
    return {
      toolCalls: [
        ...state.toolCalls,
        {
          ...toolCall,
          id,
          input: sanitizeToolPayload(toolCall.input) as Record<string, unknown>,
          status: "pending",
          timestamp: Date.now(),
        },
      ],
    };
  }),
  updateToolCall: (id, updates) => set((state) => ({
    toolCalls: state.toolCalls.map((tc) =>
      tc.id === id
        ? {
            ...tc,
            ...updates,
            input: updates.input ? sanitizeToolPayload(updates.input) as Record<string, unknown> : tc.input,
            result: updates.result
              ? {
                  ...updates.result,
                  content_preview: sanitizePreviewString(String(updates.result.content_preview ?? "")),
                }
              : tc.result,
          }
        : tc
    ),
  })),
  clearToolCalls: () => set({ toolCalls: [] }),

  // Work products
  workProducts: [],
  addWorkProduct: (product) => set((state) => ({
    workProducts: [...state.workProducts, product],
  })),
  clearWorkProducts: () => set({ workProducts: [] }),

  // Thinking — type-transition aware
  currentThinking: "",
  setCurrentThinking: (thinking) => set((state) => {
    // ── Type transition: if we were accumulating text, finalize it first ──
    if (state.currentStreamType === "text" && state.currentStreamingMessage.trim()) {
      const textMessage: Message = {
        id: generateId(),
        role: "assistant",
        content: state.currentStreamingMessage,
        timestamp: Date.now(),
        time_offset: state.currentOffset ?? 0,
        is_complete: true,
        author: state.currentAuthor,
      };
      return {
        messages: [...state.messages, textMessage],
        currentStreamingMessage: "",
        currentStreamType: "thinking",
        currentThinking: thinking,
      };
    }

    // ── Same type (thinking) — append with newline separator ──
    if (state.currentStreamType === "thinking" && state.currentThinking) {
      return {
        currentThinking: state.currentThinking + "\n\n" + thinking,
        currentStreamType: "thinking",
      };
    }

    // ── Starting fresh thinking ──
    return {
      currentThinking: thinking,
      currentStreamType: "thinking",
    };
  }),

  // Token usage
  tokenUsage: { input: 0, output: 0, total: 0 },
  updateTokenUsage: (usage) => set({ tokenUsage: usage }),

  // UI State
  viewMode: {
    main: "split",
    showWorkProducts: true,
    showActivity: true,
  },
  setViewMode: (mode) => set((state) => ({
    viewMode: { ...state.viewMode, ...mode },
  })),

  // Metrics
  startTime: null,
  toolCallCount: 0,
  iterationCount: 0,

  setStartTime: (time) => set({ startTime: time }),
  incrementToolCalls: () => set((state) => ({
    toolCallCount: state.toolCallCount + 1,
  })),
  incrementIterations: () => set((state) => ({
    iterationCount: state.iterationCount + 1,
  })),

  // Error
  lastError: null,
  setLastError: (error) => set({ lastError: error }),

  // UI Viewing State
  viewingFile: null,
  setViewingFile: (file) => set({ viewingFile: file }),



  // Logs
  logs: [],
  addLog: (log) => set((state) => ({
    logs: [
      ...state.logs,
      {
        ...log,
        id: generateId(),
        timestamp: Date.now(),
      },
    ].slice(-500), // Keep last 500 logs
  })),
  clearLogs: () => set({ logs: [] }),

  // System events + presence
  systemEvents: [],
  addSystemEvent: (event) => set((state) => ({
    systemEvents: [
      ...state.systemEvents,
      {
        id: generateId(),
        event_type: event.event_type,
        payload: event.payload,
        created_at: event.created_at,
        session_id: event.session_id,
        timestamp: Date.now(),
      },
    ].slice(-200),
  })),
  clearSystemEvents: () => set({ systemEvents: [] }),
  systemPresence: [],
  setSystemPresence: (presence) => set((state) => {
    const existing = state.systemPresence.filter((node) => node.node_id !== presence.node_id);
    return {
      systemPresence: [
        ...existing,
        {
          node_id: presence.node_id,
          status: presence.status,
          reason: presence.reason,
          metadata: presence.metadata,
          updated_at: presence.updated_at,
          timestamp: Date.now(),
        },
      ],
    };
  }),
  clearSystemPresence: () => set({ systemPresence: [] }),

  // Reset
  reset: () => set({
    messages: [],
    currentStreamingMessage: "",
    toolCalls: [],
    workProducts: [],
    logs: [],
    systemEvents: [],
    systemPresence: [],
    currentThinking: "",
    tokenUsage: { input: 0, output: 0, total: 0 },
    startTime: null,
    toolCallCount: 0,
    iterationCount: 0,
    lastError: null,
    viewingFile: null,
    sessionAttachMode: "default",
  }),
}));

// =============================================================================
// Helper Functions for WebSocket Integration
// =============================================================================

const eventDeduplicationCache = new Map<string, number>();

function getEventDedupeKey(event: WebSocketEvent): string | null {
  const data = event.data as Record<string, unknown>;
  switch (event.type) {
    case "text":
      return `text_${data.time_offset}_${data.text}`;
    case "thinking":
      return `thinking_${data.thinking}`;
    case "tool_call":
      return `tool_call_${data.id}`;
    case "tool_result":
      return `tool_result_${data.tool_use_id}`;
    default:
      return null;
  }
}

/**
 * Process a WebSocket event and update the store accordingly.
 */
export function processWebSocketEvent(event: WebSocketEvent): void {
  const store = useAgentStore.getState();

  const dedupeKey = getEventDedupeKey(event);
  if (dedupeKey) {
    const now = Date.now();
    const lastSeen = eventDeduplicationCache.get(dedupeKey);
    if (lastSeen && now - lastSeen < 500) {
      return; // Drop duplicate event
    }
    eventDeduplicationCache.set(dedupeKey, now);

    // Prevent memory leak from unbounded cache growth
    if (eventDeduplicationCache.size > 2000) {
      eventDeduplicationCache.clear();
      eventDeduplicationCache.set(dedupeKey, now);
    }
  }

  switch (event.type) {
    case "connected": {
      const data = event.data as Record<string, unknown>;
      store.setConnectionStatus("connected");
      const rawPayload = ((data.session as Record<string, unknown> | undefined) ?? data) as Record<string, unknown>;
      const existing = store.currentSession;

      // Guard: If the store already holds a VP observer/mission session, do NOT
      // let the gateway's own active session overwrite it. The WS connection is
      // only used for transport in VP observer mode — the session identity must
      // remain the VP mission ID so isVpObserverSession stays true and hydrated
      // data is preserved.  Without this guard, the gateway's "connected" event
      // replaces the VP session_id causing a "flash then reset" where VP data
      // briefly appears then gets wiped by a re-render in non-observer mode.
      const existingId = String(existing?.session_id || "").trim();
      const isExistingVpObserver =
        /^vp[_-]/i.test(existingId) || /^vp-mission-/i.test(existingId) || /^m-/i.test(existingId);
      const gatewaySessionId = String(rawPayload?.session_id || "").trim();
      if (isExistingVpObserver && gatewaySessionId && gatewaySessionId !== existingId) {
        // Gateway returned a different session — keep the VP observer session intact.
        break;
      }

      const sessionId = String(rawPayload?.session_id || existing?.session_id || "").trim();
      if (sessionId) {
        const normalized: SessionInfo = {
          session_id: sessionId,
          workspace: String(rawPayload?.workspace || rawPayload?.workspace_dir || existing?.workspace || "").trim(),
          user_id: String(rawPayload?.user_id || existing?.user_id || "user_ui").trim(),
          session_url: (rawPayload?.session_url as string | undefined) ?? existing?.session_url,
          logfire_enabled:
            typeof rawPayload?.logfire_enabled === "boolean"
              ? Boolean(rawPayload.logfire_enabled)
              : Boolean(existing?.logfire_enabled),
          run_id: String(rawPayload?.run_id || existing?.run_id || "").trim() || null,
          is_live_session:
            typeof rawPayload?.is_live_session === "boolean"
              ? Boolean(rawPayload.is_live_session)
              : existing?.is_live_session ?? true,
          run_status: existing?.run_status ?? null,
          run_kind: existing?.run_kind ?? null,
          trigger_source: existing?.trigger_source ?? null,
          attempt_count: existing?.attempt_count ?? null,
        };
        store.setCurrentSession(normalized);
      }
      break;
    }

    // Handle token usage from any event that has it
    default: {
      const data = event.data as Record<string, unknown>;
      if (data && data.token_usage) {
        store.updateTokenUsage(data.token_usage as { input: number; output: number; total: number });
      }
      break;
    }
  }

  // Process specific event types
  switch (event.type) {


    case "text": {
      const data = event.data as Record<string, unknown>;
      const offset = (data.time_offset as number) ?? (event.time_offset as number);
      store.appendToStream((data.text as string) ?? "", (data.author as string), offset);
      break;
    }

    case "tool_call": {
      // Tool calls go to the Activity Log only — do NOT finishStream() here.
      // Fragmenting the chat stream on every tool call was the primary cause of
      // the "10+ chat bubbles" problem. The stream finalizes on author change
      // or query_complete instead.
      const data = event.data as Record<string, unknown>;
      store.addToolCall({
        id: (data.id as string) ?? generateId(),
        name: (data.name as string) ?? "",
        input: (data.input as Record<string, unknown>) ?? {},
        time_offset: (data.time_offset as number) ?? 0,
        status: "running",
      });
      store.incrementToolCalls();
      break;
    }

    case "tool_result": {
      const data = event.data as Record<string, unknown>;
      store.updateToolCall((data.tool_use_id as string) ?? "", {
        result: {
          tool_use_id: (data.tool_use_id as string) ?? "",
          is_error: (data.is_error as boolean) ?? false,
          content_preview: (data.content_preview as string) ?? "",
          content_size: (data.content_size as number) ?? 0,
        },
        status: (data.is_error as boolean) ? "error" : "complete",
      });
      break;
    }

    case "thinking": {
      const data = event.data as Record<string, unknown>;
      store.setCurrentThinking((data.thinking as string) ?? "");
      break;
    }

    case "status": {
      const data = event.data as Record<string, unknown>;
      const source = String(data.source ?? "").trim().toLowerCase();
      const isHeartbeatBackground = source === "heartbeat";
      const eventKind = String(data.event_kind ?? "").trim().toLowerCase();
      if (data.status === "processing" && !isHeartbeatBackground) {
        store.setConnectionStatus("processing");
      }
      if (data.is_log) {
        store.addLog({
          message: (data.status as string) ?? "",
          level: (data.level as string) ?? "INFO",
          prefix: (data.prefix as string) ?? "",
          event_kind: eventKind || undefined,
          metadata: {
            source: source || undefined,
            compact_boundary:
              eventKind === "sdk_compact_boundary"
                ? (data.compact_boundary as Record<string, unknown> | undefined)
                : undefined,
          },
        });
      }
      if (eventKind === "sdk_compact_boundary") {
        const payload = (data.compact_boundary as Record<string, unknown>) ?? {};
        const reason = String(payload.subtype ?? payload.reason ?? "auto_compaction");
        const before = Number(payload.tokens_before ?? NaN);
        const after = Number(payload.tokens_after ?? NaN);
        const hasTokenDelta = Number.isFinite(before) && Number.isFinite(after);
        const content = hasTokenDelta
          ? `Context compacted by Claude SDK (${reason}). Tokens ${Math.trunc(before)} -> ${Math.trunc(after)}.`
          : `Context compacted by Claude SDK (${reason}).`;
        store.addMessage({
          role: "system",
          content,
          time_offset: ((data.time_offset as number) ?? (event.time_offset as number) ?? 0),
          is_complete: true,
          author: "System",
        });
      }
      if (data.token_usage) {
        store.updateTokenUsage(data.token_usage as { input: number; output: number; total: number });
      }
      break;
    }

    case "cancelled":
      store.finishStream();
      store.setConnectionStatus("connected");
      break;

    case "iteration_end": {
      const data = event.data as Record<string, unknown>;
      store.incrementIterations();
      if (data.token_usage) {
        store.updateTokenUsage(data.token_usage as { input: number; output: number; total: number });
      }
      // Route to activity log for visibility
      const toolCalls = (data.tool_calls as number) ?? 0;
      const duration = (data.duration_seconds as number) ?? 0;
      const status = (data.status as string) ?? "complete";
      store.addLog({
        message: `Iteration ${store.iterationCount} ${status} — ${toolCalls} tool call${toolCalls !== 1 ? "s" : ""}, ${duration.toFixed(1)}s`,
        level: "INFO",
        prefix: "Iteration",
      });
      break;
    }

    case "work_product": {
      const data = event.data as Record<string, unknown>;
      store.addWorkProduct({
        id: generateId(),
        content_type: (data.content_type as string) ?? "",
        content: (data.content as string) ?? "",
        filename: (data.filename as string) ?? "",
        path: (data.path as string) ?? "",
        timestamp: Date.now(),
      });
      break;
    }

    case "query_complete":
      store.finishStream();
      store.setConnectionStatus("connected");
      break;

    case "error": {
      const data = event.data as Record<string, unknown>;
      store.setLastError((data.message as string) ?? "Unknown error");
      break;
    }

    case "system_event": {
      const data = event.data as Record<string, unknown>;
      const currentSession = store.currentSession?.session_id;
      const eventType = (data.type as string) ?? "system_event";
      const payload = asObjectRecord(data.payload ?? data);
      store.addSystemEvent({
        event_type: eventType,
        payload,
        created_at: (data.created_at as string) ?? undefined,
        session_id: (data.session_id as string) ?? currentSession,
      });
      if (eventType === "vp_mission_event") {
        const lifecycleLog = vpMissionEventActivityLog(payload as VpMissionEventPayload);
        if (lifecycleLog) {
          store.addLog(lifecycleLog);
        }
        const terminalNotice = vpMissionTerminalChatNotice(payload as VpMissionEventPayload);
        if (terminalNotice) {
          store.addMessage({
            role: "assistant",
            content: terminalNotice,
            time_offset: ((data.time_offset as number) ?? (event.time_offset as number) ?? 0),
            is_complete: true,
            author: "Simone",
          });
        }
      }
      break;
    }

    case "system_presence": {
      const data = event.data as Record<string, unknown>;
      const nodeId = (data.node_id as string) ?? "gateway";
      store.setSystemPresence({
        node_id: nodeId,
        status: (data.status as string) ?? "online",
        reason: (data.reason as string) ?? undefined,
        metadata: (data.metadata as Record<string, unknown>) ?? {},
        updated_at: (data.updated_at as string) ?? undefined,
      });
      break;
    }
  }
}
