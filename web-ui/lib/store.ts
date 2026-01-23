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
  appendToStream: (text: string) => void;
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
      },
    ],
  })),
  updateMessage: (id, updates) => set((state) => ({
    messages: state.messages.map((m) => (m.id === id ? { ...m, ...updates } : m)),
  })),
  clearMessages: () => set({ messages: [] }),

  // Current streaming message
  currentStreamingMessage: "",
  appendToStream: (text) => set((state) => ({
    currentStreamingMessage: state.currentStreamingMessage + text,
  })),
  finishStream: () => set((state) => {
    // Add the completed message to the messages list
    if (state.currentStreamingMessage.trim()) {
      const newMessage: Message = {
        id: generateId(),
        role: "assistant",
        content: state.currentStreamingMessage,
        timestamp: Date.now(),
        is_complete: true,
      };
      return {
        messages: [...state.messages, newMessage],
        currentStreamingMessage: "",
      };
    }
    return { currentStreamingMessage: "" };
  }),

  // Tool calls
  toolCalls: [],
  addToolCall: (toolCall) => set((state) => ({
    toolCalls: [...state.toolCalls, { ...toolCall, status: "pending" }],
  })),
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

  // Reset
  reset: () => set({
    messages: [],
    currentStreamingMessage: "",
    toolCalls: [],
    workProducts: [],
    logs: [],
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
      store.appendToStream((data.text as string) ?? "");
      break;
    }

    case "tool_call": {
      const data = event.data as Record<string, unknown>;
      store.addToolCall({
        id: (data.id as string) ?? "",
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
  }
}
