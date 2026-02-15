/**
 * WebSocket manager for Universal Agent.
 *
 * Handles WebSocket connection, event streaming, and reconnection logic.
 */

import {
  WebSocketEvent,
  EventType,
  ConnectionStatus,
} from "@/types/agent";
import { generateId } from "./utils";

// =============================================================================
// Event Callback Types
// =============================================================================

type EventCallback = (event: WebSocketEvent) => void;
type StatusCallback = (status: ConnectionStatus) => void;
type ErrorCallback = (error: Error) => void;

// =============================================================================
// WebSocket Manager
// =============================================================================

export class AgentWebSocket {
  private ws: WebSocket | null = null;
  private url: string;
  private reconnectAttempts = 0;
  private maxReconnectAttempts = 5;
  private reconnectDelay = 1000;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private isManualClose = false;

  // Event handlers
  private eventCallbacks: Map<EventType, Set<EventCallback>> = new Map();
  private statusCallbacks: Set<StatusCallback> = new Set();
  private errorCallbacks: Set<ErrorCallback> = new Set();

  // State
  private currentStatus: ConnectionStatus = "disconnected";
  private sessionIdKey = "universal_agent_session_id"; // Key for localStorage

  private getSessionStorage(): Storage | null {
    if (typeof window === "undefined") return null;
    try {
      return window.sessionStorage;
    } catch {
      return null;
    }
  }

  private getLocalStorage(): Storage | null {
    if (typeof window === "undefined") return null;
    try {
      return window.localStorage;
    } catch {
      return null;
    }
  }

  private extractSessionId(data: unknown): string | null {
    if (!data || typeof data !== "object") return null;
    const payload = data as Record<string, unknown>;
    const nested = payload.session;
    if (nested && typeof nested === "object") {
      const nestedId = (nested as Record<string, unknown>).session_id;
      if (typeof nestedId === "string" && nestedId.trim()) {
        return nestedId;
      }
    }
    const topLevelId = payload.session_id;
    if (typeof topLevelId === "string" && topLevelId.trim()) {
      return topLevelId;
    }
    return null;
  }

  private isBackgroundStatus(statusData: Record<string, unknown>): boolean {
    const source = String(statusData.source ?? "").trim().toLowerCase();
    return source === "heartbeat";
  }

  constructor(url?: string) {
    if (url) {
      this.url = url;
      return;
    }

    const envUrl = process.env.NEXT_PUBLIC_WS_URL;
    if (envUrl) {
      this.url = envUrl;
      return;
    }

    if (typeof window !== "undefined") {
      // Use relative protocol (ws for http, wss for https)
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      const hostname = window.location.hostname;
      const port = window.location.port === "3000" ? "8001" : window.location.port;
      const host = port ? `${hostname}:${port}` : hostname;
      this.url = `${protocol}//${host}/ws/agent`;
      return;
    }

    this.url = "ws://localhost:8001/ws/agent";
  }

  // ==========================================================================
  // Connection Management
  // ==========================================================================

  private getStoredSessionId(): string | null {
    const sessionStorage = this.getSessionStorage();
    const localStorage = this.getLocalStorage();

    const tabScoped = sessionStorage?.getItem(this.sessionIdKey);
    if (tabScoped) return tabScoped;

    // Migrate once from legacy global key to tab-scoped storage.
    const legacyGlobal = localStorage?.getItem(this.sessionIdKey) ?? null;
    if (legacyGlobal && sessionStorage) {
      sessionStorage.setItem(this.sessionIdKey, legacyGlobal);
      localStorage?.removeItem(this.sessionIdKey);
      return legacyGlobal;
    }
    return legacyGlobal;
  }

  private setStoredSessionId(sessionId: string | null): void {
    const sessionStorage = this.getSessionStorage();
    const localStorage = this.getLocalStorage();
    if (!sessionId) {
      sessionStorage?.removeItem(this.sessionIdKey);
      localStorage?.removeItem(this.sessionIdKey);
      return;
    }
    sessionStorage?.setItem(this.sessionIdKey, sessionId);
    // Clear legacy global pointer to avoid cross-tab session collisions.
    localStorage?.removeItem(this.sessionIdKey);
  }

  connect(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      console.log("WebSocket already connected");
      return;
    }

    this.isManualClose = false;
    this.updateStatus("connecting");

    try {
      let wsUrl = this.url;
      // Append session_id if available to resume session
      const storedSessionId = this.getStoredSessionId();
      if (storedSessionId) {
        const separator = wsUrl.includes("?") ? "&" : "?";
        wsUrl += `${separator}session_id=${encodeURIComponent(storedSessionId)}`;
        console.log(`Resuming session: ${storedSessionId}`);
      }

      this.ws = new WebSocket(wsUrl);

      this.ws.onopen = () => {
        console.log("WebSocket connected");
        this.reconnectAttempts = 0;
        this.updateStatus("connected");
        this.startPing();
      };

      this.ws.onmessage = (event) => {
        this.handleMessage(event.data);
      };

      this.ws.onerror = (event) => {
        // In Next.js dev mode, `console.error` triggers the full-screen error overlay.
        // A transient WS failure should not block typing in the chat UI.
        console.warn("[AgentWebSocket] WebSocket error:", { url: wsUrl, event });
        this.updateStatus("disconnected");
        this.notifyError(
          new Error(
            `WebSocket connection error (${wsUrl}). If you are using an SSH tunnel to access the Web UI on localhost:3000, also forward port 8001.`
          )
        );
      };

      this.ws.onclose = (event) => {
        console.log("[AgentWebSocket] WebSocket closed", {
          code: event.code,
          reason: event.reason,
          wasClean: event.wasClean,
        });
        this.stopPing();
        this.updateStatus("disconnected");

        if (!this.isManualClose && this.reconnectAttempts < this.maxReconnectAttempts) {
          this.scheduleReconnect();
        }
      };
    } catch (error) {
      // Avoid Next.js dev overlay for environment/connection issues.
      console.warn("[AgentWebSocket] Failed to create WebSocket:", error);
      this.updateStatus("error");
      this.notifyError(error as Error);
    }
  }

  disconnect(): void {
    this.isManualClose = true;
    this.stopPing();

    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }

    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
  }

  private scheduleReconnect(): void {
    this.reconnectAttempts++;
    const delay = this.reconnectDelay * Math.pow(1.5, this.reconnectAttempts - 1);

    console.log(`Scheduling reconnect attempt ${this.reconnectAttempts}/${this.maxReconnectAttempts} in ${delay}ms`);

    this.reconnectTimer = setTimeout(() => {
      console.log(`Reconnecting... (attempt ${this.reconnectAttempts})`);
      this.connect();
    }, delay);
  }

  // ==========================================================================
  // Message Handling
  // ==========================================================================

  private handleMessage(data: string): void {
    try {
      const event: WebSocketEvent = JSON.parse(data);
      this.emit(event.type, event);

      // Handle connection-specific events
      if (event.type === "connected") {
        console.log("Connection confirmed:", event.data);
        // Save session_id for resumption
        const sessionId = this.extractSessionId(event.data);
        if (sessionId) this.setStoredSessionId(sessionId);
      } else if (event.type === "query_complete" || event.type === "cancelled") {
        this.updateStatus("connected");
      } else if (event.type === "status") {
        const statusData = event.data as Record<string, unknown>;
        if (statusData.status === "processing" && !this.isBackgroundStatus(statusData)) {
          this.updateStatus("processing");
        }
      }
    } catch (error) {
      // Do not crash the UI for a single bad event.
      console.warn("[AgentWebSocket] Failed to parse WebSocket message:", error);
    }
  }

  async sendQuery(text: string): Promise<void> {
    console.log(`[AgentWebSocket] sending query: "${text.substring(0, 50)}..."`);

    // Auto-connect if needed
    if (!this.ws || this.ws.readyState === WebSocket.CLOSED || this.ws.readyState === WebSocket.CLOSING) {
      console.log("[AgentWebSocket] WebSocket disconnected, attempting to reconnect...");
      this.connect();
    }

    // Wait for connection if connecting
    if (this.ws && this.ws.readyState === WebSocket.CONNECTING) {
      console.log("[AgentWebSocket] WebSocket connecting, waiting...");
      try {
        await this.waitForConnection();
      } catch (e) {
        throw new Error("Connection timeout awaiting socket open");
      }
    }

    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      const state = this.ws ? this.ws.readyState : "null";
      console.warn(`[AgentWebSocket] Cannot send, socket state: ${state}`);
      throw new Error(
        `WebSocket is not connected (State: ${state}). If you are accessing the UI via an SSH tunnel on localhost:3000, also forward port 8001 (API).`
      );
    }

    const event: WebSocketEvent = {
      type: "query",
      data: { text, client_turn_id: generateId() },
      timestamp: Date.now(),
    };

    console.log("[AgentWebSocket] sending payload over wire");
    this.ws.send(JSON.stringify(event));
    this.updateStatus("processing");
  }

  private waitForConnection(timeout = 5000): Promise<void> {
    return new Promise((resolve, reject) => {
      const start = Date.now();
      const interval = setInterval(() => {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
          clearInterval(interval);
          resolve();
        } else if (Date.now() - start > timeout) {
          clearInterval(interval);
          reject(new Error("Timeout"));
        }
      }, 100);
    });
  }

  sendApproval(approval: { phase_id: string; approved: boolean; followup_input?: string }): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error("WebSocket is not connected");
    }

    const event: WebSocketEvent = {
      type: "approval",
      data: approval,
      timestamp: Date.now(),
    };

    this.ws.send(JSON.stringify(event));
  }

  sendInputResponse(input_id: string, response: string): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error("WebSocket is not connected");
    }

    const event: WebSocketEvent = {
      type: "input_response",
      data: { input_id, response },
      timestamp: Date.now(),
    };

    this.ws.send(JSON.stringify(event));
  }

  sendPing(): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      return;
    }

    const event: WebSocketEvent = {
      type: "ping",
      data: {},
      timestamp: Date.now(),
    };

    this.ws.send(JSON.stringify(event));
  }

  sendCancel(reason?: string): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      console.warn("[AgentWebSocket] Cannot send cancel, socket not open");
      return;
    }

    const event: WebSocketEvent = {
      type: "cancel",
      data: { reason: reason || "User requested stop" },
      timestamp: Date.now(),
    };

    console.log("[AgentWebSocket] Sending cancel request");
    this.ws.send(JSON.stringify(event));
  }

  attachToSession(sessionId: string): void {
    const normalized = (sessionId || "").trim();
    if (!normalized) return;

    const current = this.getStoredSessionId();
    this.setStoredSessionId(normalized);

    const shouldReconnect =
      current !== normalized
      || !this.ws
      || this.ws.readyState !== WebSocket.OPEN;

    if (!shouldReconnect) return;

    this.disconnect();
    this.connect();
  }

  startNewSession(): void {
    this.setStoredSessionId(null);
    this.disconnect();
    this.connect();
  }

  // ==========================================================================
  // Event Management
  // ==========================================================================

  on(eventType: EventType, callback: EventCallback): () => void {
    if (!this.eventCallbacks.has(eventType)) {
      this.eventCallbacks.set(eventType, new Set());
    }
    this.eventCallbacks.get(eventType)!.add(callback);

    // Return unsubscribe function
    return () => {
      const callbacks = this.eventCallbacks.get(eventType);
      if (callbacks) {
        callbacks.delete(callback);
      }
    };
  }

  onStatus(callback: StatusCallback): () => void {
    this.statusCallbacks.add(callback);
    callback(this.currentStatus);

    return () => {
      this.statusCallbacks.delete(callback);
    };
  }

  onError(callback: ErrorCallback): () => void {
    this.errorCallbacks.add(callback);

    return () => {
      this.errorCallbacks.delete(callback);
    };
  }

  private emit(eventType: EventType, event: WebSocketEvent): void {
    const callbacks = this.eventCallbacks.get(eventType);
    if (callbacks) {
      callbacks.forEach((callback) => {
        try {
          callback(event);
        } catch (error) {
          console.error(`Error in ${eventType} callback:`, error);
        }
      });
    }
  }

  private updateStatus(status: ConnectionStatus): void {
    this.currentStatus = status;
    this.statusCallbacks.forEach((callback) => {
      try {
        callback(status);
      } catch (error) {
        console.error("Error in status callback:", error);
      }
    });
  }

  private notifyError(error: Error): void {
    this.errorCallbacks.forEach((callback) => {
      try {
        callback(error);
      } catch (e) {
        console.error("Error in error callback:", e);
      }
    });
  }

  // ==========================================================================
  // Ping/Pong for Connection Health
  // ==========================================================================

  private startPing(): void {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      this.sendPing();
    }, 30000); // Ping every 30 seconds
  }

  private stopPing(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  // ==========================================================================
  // Getters
  // ==========================================================================

  get status(): ConnectionStatus {
    return this.currentStatus;
  }

  get isConnected(): boolean {
    return this.currentStatus === "connected" || this.currentStatus === "processing";
  }
}

// =============================================================================
// Global WebSocket Instance
// =============================================================================

let globalWebSocket: AgentWebSocket | null = null;

export function getWebSocket(): AgentWebSocket {
  if (!globalWebSocket) {
    globalWebSocket = new AgentWebSocket();
  }
  return globalWebSocket;
}

export function resetWebSocket(): void {
  if (globalWebSocket) {
    globalWebSocket.disconnect();
    globalWebSocket = null;
  }
}
