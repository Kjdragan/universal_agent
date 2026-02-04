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

  // Messages
  messages: Message[];
  addMessage: (message: Omit<Message, "id" | "timestamp">) => void;
  updateMessage: (id: string, updates: Partial<Message>) => void;
  clearMessages: () => void;

  // Current streaming message
  currentStreamingMessage: string;
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
  logs: Array<{ id: string; message: string; level: string; prefix: string; timestamp: number }>;
  addLog: (log: { message: string; level: string; prefix: string }) => void;
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
  currentAuthor: undefined,
  currentOffset: undefined,
  appendToStream: (text, author, offset) => set((state) => ({
    currentStreamingMessage: state.currentStreamingMessage + text,
    currentAuthor: author || state.currentAuthor,
    currentOffset: offset !== undefined ? offset : state.currentOffset,
  })),
  finishStream: () => set((state) => {
    // Add the completed message to the messages list
    if (state.currentStreamingMessage.trim()) {
      const newMessage: Message = {
        id: generateId(),
        role: "assistant",
        content: state.currentStreamingMessage,
        timestamp: Date.now(),
        time_offset: state.currentOffset ?? 0,
        is_complete: true,
        author: state.currentAuthor,
        thinking: state.currentThinking,
      };
      return {
        messages: [...state.messages, newMessage],
        currentStreamingMessage: "",
        currentAuthor: undefined,
        currentThinking: "",
      };
    }
    return { currentStreamingMessage: "", currentAuthor: undefined, currentThinking: "" };
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
        // Preserve the original "first seen" timestamp unless absent.
        timestamp: next[existingIdx].timestamp ?? Date.now(),
      };
      return { toolCalls: next };
    }
    return {
      toolCalls: [...state.toolCalls, { ...toolCall, id, status: "pending", timestamp: Date.now() }],
    };
  }),
  updateToolCall: (id, updates) => set((state) => ({
    toolCalls: state.toolCalls.map((tc) =>
      tc.id === id ? { ...tc, ...updates } : tc
    ),
  })),
  clearToolCalls: () => set({ toolCalls: [] }),

  // Work products
  workProducts: [],
  addWorkProduct: (product) => set((state) => ({
    workProducts: [...state.workProducts, product],
  })),
  clearWorkProducts: () => set({ workProducts: [] }),

  // Thinking
  currentThinking: "",
  setCurrentThinking: (thinking) => set({ currentThinking: thinking }),

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
  }),
}));

// =============================================================================
// Helper Functions for WebSocket Integration
// =============================================================================

/**
 * Process a WebSocket event and update the store accordingly.
 */
export function processWebSocketEvent(event: WebSocketEvent): void {
  const store = useAgentStore.getState();

  switch (event.type) {
    case "connected": {
      const data = event.data as Record<string, unknown>;
      store.setConnectionStatus("connected");
      store.setCurrentSession(data.session as SessionInfo);
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
      const store = useAgentStore.getState(); // Re-fetch logic if needed, but 'store' is already in scope
      store.finishStream(); // <--- BREAK THE WALL OF TEXT

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
      if (data.status === "processing") {
        store.setConnectionStatus("processing");
      }
      if (data.is_log) {
        store.addLog({
          message: (data.status as string) ?? "",
          level: (data.level as string) ?? "INFO",
          prefix: (data.prefix as string) ?? "",
        });
      }
      if (data.token_usage) {
        store.updateTokenUsage(data.token_usage as { input: number; output: number; total: number });
      }
      break;
    }

    case "iteration_end": {
      const data = event.data as Record<string, unknown>;
      store.incrementIterations();
      if (data.token_usage) {
        store.updateTokenUsage(data.token_usage as { input: number; output: number; total: number });
      }
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
      store.addSystemEvent({
        event_type: (data.type as string) ?? "system_event",
        payload: (data.payload as Record<string, unknown>) ?? data,
        created_at: (data.created_at as string) ?? undefined,
        session_id: (data.session_id as string) ?? currentSession,
      });
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
