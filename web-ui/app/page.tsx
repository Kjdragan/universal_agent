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
  close: "✕",
  download: "⬇️",
};


// =============================================================================
// Components
// =============================================================================

function FileViewer() {
  const viewingFile = useAgentStore((s) => s.viewingFile);
  const setViewingFile = useAgentStore((s) => s.setViewingFile);

  if (!viewingFile) return null;

  const isHtml = viewingFile.name.endsWith(".html");
  const isPdf = viewingFile.name.endsWith(".pdf");
  const isImage = viewingFile.name.match(/\.(png|jpg|jpeg|gif|webp)$/i);

  // For PDF/HTML, we use the server's get_file endpoint.
  // Endpoint: /api/files/{session_id}/{file_path}
  const currentSession = useAgentStore.getState().currentSession;
  const fileUrl = `${API_BASE}/api/files/${currentSession?.session_id}/${viewingFile.path}`;

  return (
    <div className="flex flex-col h-full bg-background animate-in fade-in zoom-in-95 duration-200">
      <div className="h-10 border-b border-border/50 flex items-center justify-between px-4 bg-secondary/10">
        <div className="flex items-center gap-2">
          <span className="text-lg">{ICONS.file}</span>
          <span className="font-semibold text-sm">{viewingFile.name}</span>
          <span className="text-xs text-muted-foreground ml-2 opacity-50">{viewingFile.path}</span>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={() => window.open(fileUrl, '_blank')}
            className="p-1 hover:bg-black/10 rounded text-muted-foreground hover:text-foreground transition-colors"
            title="Download/Open External"
          >
            {ICONS.download}
          </button>
          <button
            onClick={() => setViewingFile(null)}
            className="p-1 hover:bg-red-500/10 rounded text-muted-foreground hover:text-red-500 transition-colors"
            title="Close Preview"
          >
            {ICONS.close}
          </button>
        </div>
      </div>
      <div className="flex-1 overflow-hidden relative bg-white/5">
        {isHtml || isPdf || isImage ? (
          <iframe
            src={fileUrl}
            className="w-full h-full border-0 block"
            title={viewingFile.name}
          />
        ) : (
          <div className="h-full overflow-auto p-4 scrollbar-thin">
            <pre className="text-xs font-mono whitespace-pre-wrap text-muted-foreground">
              {viewingFile.content || "Loading or Binary Content..."}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}

function TaskPanel() {
  const toolCalls = useAgentStore((s) => s.toolCalls);
  const [isCollapsed, setIsCollapsed] = useState(false);

  // Filter for 'Task' tool calls
  const tasks = toolCalls.filter(tc => tc.name === "Task" || tc.name === "task").reverse();

  return (
    <div className={`flex flex-col border-t border-border/50 bg-background/50 transition-all duration-300 ${isCollapsed ? 'h-10 shrink-0 overflow-hidden' : 'flex-1 min-h-0'}`}>
      <div
        className="p-3 bg-secondary/10 border-b border-border/50 cursor-pointer hover:bg-secondary/20 flex items-center justify-between"
        onClick={() => setIsCollapsed(!isCollapsed)}
      >
        <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-2">
          {ICONS.activity} Tasks
          {tasks.length > 0 && !isCollapsed && (
            <span className="bg-primary/20 text-primary px-1.5 py-0.5 rounded-full text-[10px]">{tasks.length}</span>
          )}
        </h2>
        <span className={`text-[10px] text-muted-foreground transition-transform duration-200 ${isCollapsed ? 'rotate-180' : ''}`}>
          ▼
        </span>
      </div>
      {!isCollapsed && (
        <div className="flex-1 overflow-y-auto scrollbar-thin p-2 space-y-2">
          {tasks.length === 0 ? (
            <div className="text-xs text-muted-foreground text-center py-8 italic opacity-50">
              No active tasks
            </div>
          ) : (
            tasks.map((task) => {
              const input = task.input as any;
              const subagent = input.subagent_type || "unknown";
              const description = input.description || "No description";
              const statusConfig = {
                pending: { color: "bg-yellow-500/10 text-yellow-500 border-yellow-500/20", icon: "⏳" },
                running: { color: "bg-blue-500/10 text-blue-500 border-blue-500/20", icon: "🔄" },
                complete: { color: "bg-green-500/10 text-green-500 border-green-500/20", icon: "✅" },
                error: { color: "bg-red-500/10 text-red-500 border-red-500/20", icon: "❌" },
              };
              const config = statusConfig[task.status as keyof typeof statusConfig] || statusConfig.running;

              return (
                <div key={task.id} className={`rounded-md border p-2 text-xs ${config.color}`}>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="font-semibold capitalize flex items-center gap-1.5">
                      {config.icon} {subagent.replace("-", " ")}
                    </span>
                  </div>
                  <div className="line-clamp-3 opacity-80 leading-relaxed font-light">{description}</div>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

const API_BASE = "http://localhost:8001";

function FileExplorer() {
  const currentSession = useAgentStore((s) => s.currentSession);
  const setViewingFile = useAgentStore((s) => s.setViewingFile);
  const [path, setPath] = useState("");
  const [files, setFiles] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(false);

  useEffect(() => {
    if (!currentSession?.session_id) return;

    setLoading(true);
    fetch(`${API_BASE}/api/files?session_id=${currentSession.session_id}&path=${encodeURIComponent(path)}`)
      .then(res => res.json())
      .then(data => {
        setFiles(data.files || []);
      })
      .catch(err => console.error("Failed to fetch files:", err))
      .finally(() => setLoading(false));
  }, [currentSession?.session_id, path]);

  const handleNavigate = (itemName: string, isDir: boolean) => {
    if (!isDir) {
      // Open file preview
      const fullPath = path ? `${path}/${itemName}` : itemName;
      setViewingFile({ name: itemName, path: fullPath, type: 'file' });
      return;
    }
    setPath(prev => prev ? `${prev}/${itemName}` : itemName);
  };

  const handleUp = () => {
    if (!path) return;
    const parts = path.split("/");
    parts.pop();
    setPath(parts.join("/"));
  };

  return (
    <div className={`flex flex-col border-b border-border/50 transition-all duration-300 ${isCollapsed ? 'h-10 shrink-0 overflow-hidden' : 'flex-1 min-h-0'}`}>
      <div
        className="p-3 border-b border-border/50 bg-secondary/10 flex items-center justify-between cursor-pointer hover:bg-secondary/20"
        onClick={(e) => {
          // Prevent collapse when clicking the 'Up' button
          if ((e.target as HTMLElement).tagName === 'BUTTON') return;
          setIsCollapsed(!isCollapsed);
        }}
      >
        <div className="flex items-center gap-2 overflow-hidden">
          <span className={`text-[10px] text-muted-foreground transition-transform duration-200 ${isCollapsed ? '-rotate-90' : ''}`}>▼</span>
          <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider truncate" title={currentSession?.session_id}>
            {ICONS.folder} {path ? `.../${path.split("/").pop()}` : "Files"}
          </h2>
        </div>
        {!isCollapsed && path && (
          <button onClick={handleUp} className="text-xs hover:bg-black/20 p-1 rounded" title="Go Up">
            ⬆️
          </button>
        )}
      </div>
      {!isCollapsed && (
        <div className="flex-1 overflow-y-auto scrollbar-thin p-1">
          {!currentSession ? (
            <div className="text-xs text-muted-foreground text-center py-4">No active session</div>
          ) : loading ? (
            <div className="text-xs text-muted-foreground text-center py-4">Loading...</div>
          ) : files.length === 0 ? (
            <div className="text-xs text-muted-foreground text-center py-4">Empty directory</div>
          ) : (
            <div className="space-y-0.5">
              {files.map((file, i) => (
                <div
                  key={i}
                  className={`text-xs px-2 py-1.5 rounded flex items-center gap-2 cursor-pointer transition-colors ${file.is_dir ? "hover:bg-primary/10 text-primary" : "hover:bg-accent text-foreground/80"
                    }`}
                  onClick={() => handleNavigate(file.name, file.is_dir)}
                >
                  <span>{file.is_dir ? ICONS.folder : ICONS.file}</span>
                  <span className="truncate flex-1">{file.name}</span>
                  {file.size && <span className="text-[9px] opacity-50">{formatFileSize(file.size)}</span>}
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

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
        className={`w-2 h-2 rounded-full ${config.color} ${config.pulse ? "animate-pulse-glow" : ""
          }`}
      />
      <span className="text-muted-foreground">{config.label}</span>
    </div>
  );
}

function HeaderMetrics() {
  const tokenUsage = useAgentStore((s) => s.tokenUsage);
  const toolCallCount = useAgentStore((s) => s.toolCallCount);
  const startTime = useAgentStore((s) => s.startTime);
  const iterationCount = useAgentStore((s) => s.iterationCount);

  // Use 0 if no start time, preventing hydration mismatch of "NaN" or large negative numbers
  // Calculate only on client in useEffect, or just render "0s" if null.
  const [duration, setDuration] = useState(0);

  useEffect(() => {
    if (!startTime) {
      setDuration(0);
      return;
    }
    const interval = setInterval(() => {
      setDuration((Date.now() - startTime) / 1000);
    }, 1000); // Update every second
    return () => clearInterval(interval);
  }, [startTime]);

  return (
    <div className="hidden md:flex items-center gap-3 mr-6 bg-secondary/20 px-3 py-1.5 rounded-md border border-border/50">
      <div className="flex items-center gap-2 text-[10px] tracking-wide">
        <span className="text-muted-foreground font-semibold">TOKENS</span>
        <span className="font-mono">{tokenUsage.total.toLocaleString()}</span>
      </div>
      <div className="w-px h-3 bg-border/50" />
      <div className="flex items-center gap-2 text-[10px] tracking-wide">
        <span className="text-muted-foreground font-semibold">TOOLS</span>
        <span className="font-mono">{toolCallCount}</span>
      </div>
      <div className="w-px h-3 bg-border/50" />
      <div className="flex items-center gap-2 text-[10px] tracking-wide">
        <span className="text-muted-foreground font-semibold">TIME</span>
        <span className="font-mono">{formatDuration(duration)}</span>
      </div>
      <div className="w-px h-3 bg-border/50" />
      <div className="flex items-center gap-2 text-[10px] tracking-wide">
        <span className="text-muted-foreground font-semibold">ITERS</span>
        <span className="font-mono">{iterationCount}</span>
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
            className={`text-xs ${statusColors[toolCall.status as keyof typeof statusColors]
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
  const [formattedTime, setFormattedTime] = useState("");

  // Format time only on client to avoid hydration mismatch
  useEffect(() => {
    setFormattedTime(new Date(message.timestamp).toLocaleTimeString());
  }, [message.timestamp]);

  // Split content by newlines that look like separate thoughts or sections
  const contentSegments = message.content.split(/\n\n+/).filter(Boolean);

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"} mb-6 group`}>
      <div
        className={`max-w-[85%] rounded-xl p-4 shadow-sm ${isUser
          ? "bg-primary/10 border border-primary/20 text-foreground"
          : "bg-card border border-border/50 shadow-lg"
          }`}
      >
        <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-2 flex items-center gap-2 font-medium">
          <span className={`${isUser ? "text-primary" : "text-blue-400"}`}>
            {isUser ? "You" : "Primary Agent"}
          </span>
          <span className="opacity-30">•</span>
          <span className="opacity-50 font-mono">{formattedTime}</span>
        </div>

        <div className="space-y-4 text-sm leading-relaxed">
          {contentSegments.map((segment: string, i: number) => (
            <div key={i}>
              <div className="whitespace-pre-wrap">{segment}</div>
              {i < contentSegments.length - 1 && (
                <div className="w-8 h-px bg-border/50 my-3 ml-0.5" />
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}

function ChatInterface() {
  const messages = useAgentStore((s) => s.messages);
  const currentStreamingMessage = useAgentStore((s) => s.currentStreamingMessage);
  const setStartTime = useAgentStore((s) => s.setStartTime);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const connectionStatus = useAgentStore((s) => s.connectionStatus);
  const ws = getWebSocket();

  const handleSend = async () => {
    if (!input.trim() || isSending) return;

    setIsSending(true);
    const query = input;
    setInput("");

    // Set Start Time if not already set (new run)
    if (!useAgentStore.getState().startTime) {
      setStartTime(Date.now());
    }

    // Add user message to store
    useAgentStore.getState().addMessage({
      role: "user",
      content: query,
      is_complete: true,
    });

    try {
      await ws.sendQuery(query);
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

function ActivityItem({ activity }: { activity: any }) {
  const [expanded, setExpanded] = useState(false);

  return (
    <div>
      <div
        className="text-xs flex items-center gap-2 cursor-pointer hover:bg-white/5 p-1 rounded"
        onClick={() => setExpanded(!expanded)}
      >
        <span>{activity.type === "tool" ? ICONS.terminal : ICONS.file}</span>
        <span className="flex-1 truncate font-mono opacity-80">{activity.name}</span>
        <span className="text-[10px] text-muted-foreground">
          {expanded ? "▼" : "▶"}
        </span>
      </div>

      {expanded && activity.item && (
        <div className="pl-6 mt-1 text-[10px] space-y-1 overflow-hidden">
          {activity.type === "tool" && (
            <>
              <div className="text-muted-foreground uppercase tracking-wider text-[9px]">Input</div>
              <pre className="bg-black/30 p-1.5 rounded text-muted-foreground overflow-x-auto">
                {JSON.stringify(activity.item.input, null, 2)}
              </pre>
              {activity.item.result && (
                <>
                  <div className="text-muted-foreground uppercase tracking-wider text-[9px] mt-1">Output</div>
                  <div className="bg-black/30 p-1.5 rounded text-muted-foreground max-h-32 overflow-y-auto whitespace-pre-wrap">
                    {activity.item.result.content_preview || "No output"}
                  </div>
                </>
              )}
            </>
          )}
          {activity.type === "product" && (
            <div className="bg-black/30 p-1.5 rounded text-muted-foreground">
              {activity.item.path}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ActivityFeed() {
  const toolCalls = useAgentStore((s) => s.toolCalls);
  const workProducts = useAgentStore((s) => s.workProducts);
  const [isCollapsed, setIsCollapsed] = useState(false);

  const activities = [
    ...toolCalls.map((tc) => ({
      type: "tool",
      name: tc.name,
      status: tc.status,
      time: tc.time_offset,
      item: tc
    })),
    ...workProducts.map((wp) => ({
      type: "product",
      name: wp.filename,
      time: wp.timestamp,
      item: wp
    })),
  ].sort((a, b) => a.time - b.time);

  return (
    <div className={`flex flex-col transition-all duration-300 ${isCollapsed ? 'h-10 shrink-0 overflow-hidden' : 'flex-1 min-h-0'}`}>
      <div
        className="p-3 border-b border-border/50 flex items-center justify-between cursor-pointer hover:bg-secondary/10"
        onClick={() => setIsCollapsed(!isCollapsed)}
      >
        <h3 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-2">
          {ICONS.activity} Activity
        </h3>
        <span className={`text-[10px] text-muted-foreground transition-transform duration-200 ${isCollapsed ? 'rotate-180' : ''}`}>
          ▼
        </span>
      </div>
      {!isCollapsed && (
        <div className="flex-1 overflow-y-auto scrollbar-thin p-3 space-y-1">
          {activities.length === 0 ? (
            <div className="text-xs text-muted-foreground text-center py-4">
              No activity yet
            </div>
          ) : (
            activities.map((activity, i) => (
              <ActivityItem key={i} activity={activity} />
            ))
          )}
        </div>
      )}
    </div>
  );
}

function WorkProductViewer() {
  const workProducts = useAgentStore((s) => s.workProducts);
  const currentSession = useAgentStore((s) => s.currentSession);
  const setViewingFile = useAgentStore((s) => s.setViewingFile);
  const [keyFiles, setKeyFiles] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(false);

  // Fetch key files (transcript, runs, report) from actual FS
  useEffect(() => {
    if (!currentSession?.session_id) return;

    const fetchKeyFiles = async () => {
      try {
        const res = await fetch(`${API_BASE}/api/files?session_id=${currentSession.session_id}&path=.`);
        const data = await res.json();
        const files = data.files || [];

        // Filter for interesting files
        const interesting = files.filter((f: any) =>
          f.name === 'run.log' ||
          f.name === 'transcript.md' ||
          f.name.startsWith('report') ||
          f.name.endsWith('.pdf')
        );
        setKeyFiles(interesting);
      } catch (e) {
        console.error("Failed to fetch key files", e);
      }
    };

    // Refresh periodically or just once? Once + on workProducts update might be good.
    // For now just once + manual dependency on workProducts length?
    fetchKeyFiles();
  }, [currentSession?.session_id, workProducts.length]);

  return (
    <div className={`flex flex-col ${isCollapsed ? '' : 'h-64'} transition-all duration-300`}>
      <div
        className="p-3 border-b border-border/50 flex items-center justify-between cursor-pointer hover:bg-secondary/10"
        onClick={() => setIsCollapsed(!isCollapsed)}
      >
        <h3 className="text-sm font-semibold flex items-center gap-2">
          {ICONS.file} Key Work Products
          <span className="text-xs text-muted-foreground font-normal">({keyFiles.length})</span>
        </h3>
        <span className={`text-[10px] text-muted-foreground transition-transform duration-200 ${isCollapsed ? 'rotate-180' : ''}`}>
          ▼
        </span>
      </div>

      {!isCollapsed && (
        <div className="flex-1 overflow-y-auto scrollbar-thin bg-secondary/5 p-2 space-y-1">
          {keyFiles.length === 0 ? (
            <div className="text-xs text-muted-foreground text-center py-4">No key files found</div>
          ) : (
            keyFiles.map((file, i) => (
              <button
                key={i}
                onClick={() => setViewingFile({ name: file.name, path: file.name, type: 'file' })}
                className="w-full text-left px-3 py-2 text-xs rounded hover:bg-black/20 transition-colors flex items-center gap-2"
              >
                <span className="text-lg">{file.name.endsWith('pdf') ? '📕' : file.name.endsWith('html') ? '🌐' : '📄'}</span>
                <div className="flex-1 min-w-0">
                  <div className="truncate font-medium">{file.name}</div>
                  <div className="text-[9px] text-muted-foreground opacity-70">{formatFileSize(file.size)}</div>
                </div>
              </button>
            ))
          )}
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
  const viewingFile = useAgentStore((s) => s.viewingFile); // Sub to viewing state
  const ws = getWebSocket();

  // Layout State
  const [leftWidth, setLeftWidth] = useState(260);
  const [rightWidth, setRightWidth] = useState(400); // Wider default as requested

  // Resizing Logic
  const startResizing = (direction: 'left' | 'right') => (mouseDownEvent: React.MouseEvent) => {
    mouseDownEvent.preventDefault();
    const startX = mouseDownEvent.clientX;
    const startWidth = direction === 'left' ? leftWidth : rightWidth;

    const onMouseMove = (mouseMoveEvent: MouseEvent) => {
      const delta = mouseMoveEvent.clientX - startX;
      const newWidth = direction === 'left'
        ? Math.max(200, Math.min(600, startWidth + delta))
        : Math.max(300, Math.min(800, startWidth - delta));

      if (direction === 'left') setLeftWidth(newWidth);
      else setRightWidth(newWidth);
    };

    const onMouseUp = () => {
      document.body.style.cursor = 'default';
      document.removeEventListener("mousemove", onMouseMove);
      document.removeEventListener("mouseup", onMouseUp);
    };

    document.body.style.cursor = 'col-resize';
    document.addEventListener("mousemove", onMouseMove);
    document.addEventListener("mouseup", onMouseUp);
  };

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
      <header className="h-14 border-b border-border/50 glass-strong flex items-center justify-between px-4 shrink-0 z-10 relative">
        <div className="flex items-center gap-3">
          <h1 className="text-lg font-bold gradient-text">Universal Agent</h1>
          <span className="text-xs text-muted-foreground px-2 py-1 rounded bg-muted">
            v2.1
          </span>
        </div>
        <div className="flex items-center gap-4">
          <HeaderMetrics />
          <ConnectionIndicator />
        </div>
      </header>

      {/* Main Content */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Left Sidebar - Sessions & Tasks */}
        <aside
          className="shrink-0 border-r border-border/50 flex flex-col overflow-hidden bg-background/30 backdrop-blur-sm relative"
          style={{ width: leftWidth }}
        >
          <FileExplorer />
          <TaskPanel />
          {/* Resizer */}
          <div
            className="absolute top-0 right-0 w-1 h-full cursor-col-resize hover:bg-primary/50 transition-colors z-20"
            onMouseDown={startResizing('left')}
          />
        </aside>

        {/* Center - Chat Interface OR File Viewer */}
        <main className="flex-1 border-r border-border/50 min-w-0 bg-background/50">
          {viewingFile ? (
            <FileViewer />
          ) : (
            <ChatInterface />
          )}
        </main>

        {/* Right Sidebar - Monitoring & Workspace */}
        <aside
          className="shrink-0 flex flex-col h-full overflow-hidden relative bg-background/30 backdrop-blur-sm"
          style={{ width: rightWidth }}
        >
          {/* Resizer */}
          <div
            className="absolute top-0 left-0 w-1 h-full cursor-col-resize hover:bg-primary/50 transition-colors z-20"
            onMouseDown={startResizing('right')}
          />


          {/* Activity Feed */}
          <div className="flex-1 min-h-0 border-b border-border/50 flex flex-col overflow-hidden">
            {/* Using flex-1 on container and flex-col ensures child overflow-y-auto works */}
            <ActivityFeed />
          </div>

          {/* Work Products */}
          <div className="shrink-0 border-t border-border/50">
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
