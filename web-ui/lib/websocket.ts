/**
 * WebSocket manager for Universal Agent.
 *
 * Handles WebSocket connection, event streaming, and reconnection logic.
 */

import {
  WebSocketEvent,
  EventType,
  SessionInfo,
  ToolCall,
  WorkProduct,
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

  constructor(url: string = "ws://localhost:8001/ws/agent") {
    this.url = url;
  }

  // ==========================================================================
  // Connection Management
  // ==========================================================================

  connect(): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      console.log("WebSocket already connected");
      return;
    }

    this.isManualClose = false;
    this.updateStatus("connecting");

    try {
      this.ws = new WebSocket(this.url);

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
        console.error("WebSocket error:", event);
        this.updateStatus("error");
        this.notifyError(new Error("WebSocket connection error"));
      };

      this.ws.onclose = () => {
        console.log("WebSocket closed");
        this.stopPing();
        this.updateStatus("disconnected");

        if (!this.isManualClose && this.reconnectAttempts < this.maxReconnectAttempts) {
          this.scheduleReconnect();
        }
      };
    } catch (error) {
      console.error("Failed to create WebSocket:", error);
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
      } else if (event.type === "query_complete") {
        this.updateStatus("connected");
      } else if (event.type === "status") {
        const statusData = event.data as Record<string, unknown>;
        if (statusData.status === "processing") {
          this.updateStatus("processing");
        }
      }
    } catch (error) {
      console.error("Failed to parse WebSocket message:", error);
    }
  }

  sendQuery(text: string): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
      throw new Error("WebSocket is not connected");
    }

    const event: WebSocketEvent = {
      type: "query",
      data: { text },
      timestamp: Date.now(),
    };

    this.ws.send(JSON.stringify(event));
    this.updateStatus("processing");
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
