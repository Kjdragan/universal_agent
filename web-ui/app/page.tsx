"use client";

/**
 * Universal Agent - Main Dashboard
 *
 * AGI-era Neural Command Center with:
 * - Real-time chat interface with streaming
 * - Terminal-style monitoring view
 * - Work product visualization
 * - Session management
 */

import React, { useEffect, useState } from "react";
import { useAgentStore } from "@/lib/store";
import { getWebSocket } from "@/lib/websocket";
import { processWebSocketEvent } from "@/lib/store";
import { ConnectionStatus, WebSocketEvent } from "@/types/agent";
import { formatDuration, formatFileSize } from "@/lib/utils";
import { ApprovalModal, useApprovalModal } from "@/components/approvals/ApprovalModal";

// Icons (using emoji for now - replace with lucide-react in production)
const ICONS = {
  terminal: "⌨️",
  chat: "💬",
  folder: "📁",
  file: "📄",
  settings: "⚙️",
  activity: "⚡",
  disconnect: "🔌",
  connect: "🔗",
  clear: "🗑️",
  send: "➤",
  refresh: "🔄",
};

// =============================================================================
// Components
// =============================================================================

function ConnectionIndicator() {
  const status = useAgentStore((s) => s.connectionStatus);

  const statusConfig = {
    disconnected: { color: "bg-red-500", label: "Disconnected", pulse: false },
    connecting: { color: "bg-yellow-500", label: "Connecting...", pulse: true },
    connected: { color: "bg-primary", label: "Connected", pulse: false },
    processing: { color: "bg-primary", label: "Processing...", pulse: true },
    error: { color: "bg-red-600", label: "Error", pulse: false },
  };

  const config = statusConfig[status];

  return (
    <div className="flex items-center gap-2 text-sm">
      <div
        className={`w-2 h-2 rounded-full ${config.color} ${
          config.pulse ? "animate-pulse-glow" : ""
        }`}
      />
      <span className="text-muted-foreground">{config.label}</span>
    </div>
  );
}

function MetricsPanel() {
  const tokenUsage = useAgentStore((s) => s.tokenUsage);
  const toolCallCount = useAgentStore((s) => s.toolCallCount);
  const startTime = useAgentStore((s) => s.startTime);
  const iterationCount = useAgentStore((s) => s.iterationCount);

  const duration = startTime ? (Date.now() - startTime) / 1000 : 0;

  return (
    <div className="glass rounded-lg p-3 space-y-2">
      <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        Metrics
      </h3>
      <div className="grid grid-cols-2 gap-2 text-sm">
        <div>
          <span className="text-muted-foreground">Tokens:</span>{" "}
          <span className="font-mono">{tokenUsage.total.toLocaleString()}</span>
        </div>
        <div>
          <span className="text-muted-foreground">Tools:</span>{" "}
          <span className="font-mono">{toolCallCount}</span>
        </div>
        <div>
          <span className="text-muted-foreground">Duration:</span>{" "}
          <span className="font-mono">{formatDuration(duration)}</span>
        </div>
        <div>
          <span className="text-muted-foreground">Iterations:</span>{" "}
          <span className="font-mono">{iterationCount}</span>
        </div>
      </div>
    </div>
  );
}

function ToolCallCard({ toolCall }: { toolCall: any }) {
  const [expanded, setExpanded] = useState(false);

  const statusColors = {
    pending: "text-yellow-500",
    running: "text-primary",
    complete: "text-green-500",
    error: "text-red-500",
  };

  return (
    <div className="glass rounded-lg p-3 mb-2">
      <div
        className="flex items-center justify-between cursor-pointer"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm text-primary">{ICONS.terminal}</span>
          <span className="font-mono text-sm">{toolCall.name}</span>
          <span
            className={`text-xs ${
              statusColors[toolCall.status as keyof typeof statusColors]
            }`}
          >
            {toolCall.status}
          </span>
        </div>
        <span className="text-xs text-muted-foreground">
          {toolCall.time_offset.toFixed(2)}s
        </span>
      </div>
      {expanded && (
        <div className="mt-2 pl-6 text-xs">
          <pre className="bg-black/30 rounded p-2 overflow-x-auto">
            {JSON.stringify(toolCall.input, null, 2)}
          </pre>
          {toolCall.result && (
            <div className="mt-2">
              <div className="text-muted-foreground mb-1">Result preview:</div>
              <div className="bg-black/30 rounded p-2 max-h-20 overflow-y-auto">
                {toolCall.result.content_preview}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function TerminalLog() {
  const toolCalls = useAgentStore((s) => s.toolCalls);

  return (
    <div className="h-full overflow-y-auto scrollbar-thin p-2 space-y-1">
      {toolCalls.length === 0 ? (
        <div className="text-center text-muted-foreground py-8">
          {ICONS.terminal} Terminal ready
        </div>
      ) : (
        toolCalls.map((tc) => <ToolCallCard key={tc.id} toolCall={tc} />)
      )}
    </div>
  );
}

function ChatMessage({ message }: { message: any }) {
  const isUser = message.role === "user";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-4`}>
      <div
        className={`max-w-[80%] rounded-lg p-3 ${
          isUser
            ? "bg-primary/20 border border-primary/30"
            : "bg-card/50 border border-border/50"
        }`}
      >
        <div className="text-xs text-muted-foreground mb-1">
          {isUser ? "You" : "Agent"} • {new Date(message.timestamp).toLocaleTimeString()}
        </div>
        <div className="whitespace-pre-wrap">{message.content}</div>
      </div>
    </div>
  );
}

function ChatInterface() {
  const messages = useAgentStore((s) => s.messages);
  const currentStreamingMessage = useAgentStore((s) => s.currentStreamingMessage);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const connectionStatus = useAgentStore((s) => s.connectionStatus);
  const ws = getWebSocket();

  const handleSend = async () => {
    if (!input.trim() || isSending) return;

    setIsSending(true);
    const query = input;
    setInput("");

    // Add user message to store
    useAgentStore.getState().addMessage({
      role: "user",
      content: query,
      is_complete: true,
    });

    try {
      ws.sendQuery(query);
    } catch (error) {
      console.error("Failed to send query:", error);
      useAgentStore.getState().setLastError("Failed to send query. Check connection.");
    } finally {
      setIsSending(false);
    }
  };

  const messagesEndRef = React.useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, currentStreamingMessage]);

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto scrollbar-thin p-4">
        {messages.length === 0 && !currentStreamingMessage ? (
          <div className="text-center text-muted-foreground py-12">
            <div className="text-4xl mb-4">{ICONS.chat}</div>
            <div className="text-lg font-semibold mb-2">Universal Agent</div>
            <div className="text-sm">Enter a query to begin</div>
          </div>
        ) : (
          <>
            {messages.map((msg) => (
              <ChatMessage key={msg.id} message={msg} />
            ))}
            {currentStreamingMessage && (
              <div className="flex justify-start mb-4">
                <div className="max-w-[80%] rounded-lg p-3 bg-card/50 border border-border/50">
                  <div className="whitespace-pre-wrap">{currentStreamingMessage}</div>
                  <span className="inline-block w-2 h-4 bg-primary animate-pulse ml-1" />
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* Input */}
      <div className="p-4 border-t border-border/50">
        <div className="flex gap-2">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyPress={(e) => e.key === "Enter" && handleSend()}
            placeholder="Enter your query..."
            disabled={connectionStatus !== "connected" || isSending}
            className="flex-1 bg-background/50 border border-border rounded-lg px-4 py-2 focus:outline-none focus:border-primary disabled:opacity-50"
          />
          <button
            onClick={handleSend}
            disabled={connectionStatus !== "connected" || isSending || !input.trim()}
            className="bg-primary hover:bg-primary/90 disabled:bg-primary/30 text-primary-foreground px-4 py-2 rounded-lg transition-colors"
          >
            {ICONS.send}
          </button>
        </div>
      </div>
    </div>
  );
}

function ActivityFeed() {
  const toolCalls = useAgentStore((s) => s.toolCalls);
  const workProducts = useAgentStore((s) => s.workProducts);

  const activities = [
    ...toolCalls.map((tc) => ({
      type: "tool",
      name: tc.name,
      status: tc.status,
      time: tc.time_offset,
    })),
    ...workProducts.map((wp) => ({
      type: "product",
      name: wp.filename,
      time: wp.timestamp,
    })),
  ].sort((a, b) => a.time - b.time);

  return (
    <div className="space-y-2">
      <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
        {ICONS.activity} Activity
      </h3>
      <div className="space-y-1 max-h-48 overflow-y-auto scrollbar-thin">
        {activities.length === 0 ? (
          <div className="text-xs text-muted-foreground text-center py-4">
            No activity yet
          </div>
        ) : (
          activities.map((activity, i) => (
            <div key={i} className="text-xs flex items-center gap-2">
              <span>{activity.type === "tool" ? ICONS.terminal : ICONS.file}</span>
              <span className="flex-1 truncate">{activity.name}</span>
            </div>
          ))
        )}
      </div>
    </div>
  );
}

function WorkProductViewer() {
  const workProducts = useAgentStore((s) => s.workProducts);
  const [selectedProduct, setSelectedProduct] = useState<any>(null);

  return (
    <div className="h-full flex flex-col">
      <div className="p-3 border-b border-border/50">
        <h3 className="text-sm font-semibold">Work Products</h3>
      </div>
      {workProducts.length === 0 ? (
        <div className="flex-1 flex items-center justify-center text-muted-foreground">
          <div className="text-center">
            <div className="text-2xl mb-2">{ICONS.file}</div>
            <div className="text-sm">No work products yet</div>
          </div>
        </div>
      ) : (
        <div className="flex-1 flex overflow-hidden">
          {/* List */}
          <div className="w-48 border-r border-border/50 overflow-y-auto scrollbar-thin">
            {workProducts.map((wp) => (
              <button
                key={wp.id}
                onClick={() => setSelectedProduct(wp)}
                className={`w-full text-left px-3 py-2 text-sm hover:bg-accent/50 transition-colors ${
                  selectedProduct?.id === wp.id ? "bg-accent" : ""
                }`}
              >
                <div className="truncate">{wp.filename}</div>
              </button>
            ))}
          </div>
          {/* Preview */}
          <div className="flex-1 overflow-hidden">
            {selectedProduct ? (
              <iframe
                srcDoc={selectedProduct.content}
                className="w-full h-full border-0"
                title={selectedProduct.filename}
              />
            ) : (
              <div className="h-full flex items-center justify-center text-muted-foreground text-sm">
                Select a work product to view
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// =============================================================================
// Main App Component
// =============================================================================

export default function HomePage() {
  const connectionStatus = useAgentStore((s) => s.connectionStatus);
  const ws = getWebSocket();

  // Approval modal hook
  const { pendingApproval, handleApprove, handleReject } = useApprovalModal();

  useEffect(() => {
    // Connect to WebSocket on mount
    ws.connect();

    // Set up event listeners
    const unsubscribes: (() => void)[] = [];

    // Listen for all events
    Object.values(useAgentStore.getState().messages).forEach((message) => {
      // This will be handled by the event callback
    });

    const unsubscribeStatus = ws.onStatus((status) => {
      useAgentStore.getState().setConnectionStatus(status);
    });

    const unsubscribeError = ws.onError((error) => {
      useAgentStore.getState().setLastError(error.message);
    });

    // Set up individual event listeners
    const eventTypes: WebSocketEvent["type"][] = [
      "connected",
      "text",
      "tool_call",
      "tool_result",
      "thinking",
      "status",
      "work_product",
      "query_complete",
      "error",
      "iteration_end",
    ];

    eventTypes.forEach((eventType) => {
      const unsubscribe = ws.on(eventType, (event) => {
        processWebSocketEvent(event);
      });
      unsubscribes.push(unsubscribe);
    });

    unsubscribes.push(unsubscribeStatus, unsubscribeError);

    // Cleanup on unmount
    return () => {
      unsubscribes.forEach((unsub) => unsub());
    };
  }, []);

  return (
    <div className="h-screen flex flex-col bg-background text-foreground">
      {/* Header */}
      <header className="h-14 border-b border-border/50 glass-strong flex items-center justify-between px-4">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-bold gradient-text">Universal Agent</h1>
          <span className="text-xs text-muted-foreground px-2 py-1 rounded bg-muted">
            v2.0
          </span>
        </div>
        <div className="flex items-center gap-4">
          <ConnectionIndicator />
        </div>
      </header>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden">
        {/* Left Sidebar - Sessions */}
        <aside className="w-56 border-r border-border/50 p-3 overflow-y-auto scrollbar-thin">
          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider mb-3">
            {ICONS.folder} Sessions
          </h2>
          <div className="space-y-1">
            <div className="text-xs text-muted-foreground">
              {useAgentStore.getState().currentSession?.session_id || "No active session"}
            </div>
          </div>
        </aside>

        {/* Center - Chat Interface */}
        <main className="flex-1 border-r border-border/50">
          <ChatInterface />
        </main>

        {/* Right Sidebar - Monitoring & Workspace */}
        <aside className="w-80 flex flex-col">
          {/* Metrics */}
          <div className="p-3 border-b border-border/50">
            <MetricsPanel />
          </div>

          {/* Activity Feed */}
          <div className="p-3 border-b border-border/50">
            <ActivityFeed />
          </div>

          {/* Terminal Log */}
          <div className="flex-1 border-b border-border/50 overflow-hidden">
            <div className="h-full flex flex-col">
              <div className="p-2 border-b border-border/50">
                <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider">
                  {ICONS.terminal} Terminal
                </h3>
              </div>
              <TerminalLog />
            </div>
          </div>

          {/* Work Products */}
          <div className="h-64">
            <WorkProductViewer />
          </div>
        </aside>
      </div>

      {/* Approval Modal */}
      <ApprovalModal
        request={pendingApproval}
        onApprove={handleApprove}
        onReject={handleReject}
      />
    </div>
  );
}
