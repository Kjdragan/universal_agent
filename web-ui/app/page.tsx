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
import { InputModal, useInputModal } from "@/components/inputs/InputModal";
import { CombinedActivityLog } from "@/components/CombinedActivityLog";
import { OpsProvider, SessionsSection, CalendarSection, SkillsSection, ChannelsSection, ApprovalsSection, SystemEventsSection, OpsConfigSection, SessionContinuityWidget, HeartbeatWidget } from "@/components/OpsDropdowns";
// UI Primitives
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { LinkifiedText, PathLink, linkify } from "@/components/LinkifiedText";

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

  const isHtml = viewingFile?.name.endsWith(".html") ?? false;
  const isPdf = viewingFile?.name.endsWith(".pdf") ?? false;
  const isImage = viewingFile?.name.match(/\.(png|jpg|jpeg|gif|webp)$/i) ?? false;

  const encodePath = (p: string) => p.split("/").map(encodeURIComponent).join("/");

  // For PDF/HTML, we use the server's get_file endpoint.
  // Endpoint: /api/files/{session_id}/{file_path}
  const currentSession = useAgentStore.getState().currentSession;
  const fileUrl = viewingFile
    ? (viewingFile.type === "artifact"
      ? `${API_BASE}/api/artifacts/files/${encodePath(viewingFile.path)}`
      : (currentSession?.session_id
        ? `${API_BASE}/api/files/${currentSession.session_id}/${encodePath(viewingFile.path)}`
        : ""))
    : "";

  // Fetch content if missing and not an iframe type
  useEffect(() => {
    if (!viewingFile || !fileUrl || isHtml || isPdf || isImage || viewingFile.content) return;

    // Important: if the user closes the viewer while a fetch is in-flight, the old
    // promise can resolve and re-open the file (because it captured a stale viewingFile).
    // Use an AbortController + "is still current" check to prevent that.
    const controller = new AbortController();

    fetch(fileUrl, { signal: controller.signal })
      .then(res => res.text())
      .then(text => {
        // Auto-format if JSON or similar
        if (viewingFile.name.endsWith(".json")) {
          try {
            const obj = JSON.parse(text);
            text = JSON.stringify(obj, null, 2);
          } catch (e) {
            // Keep original text on parse error
          }
        }

        const current = useAgentStore.getState().viewingFile;
        if (!current || current.path !== viewingFile.path) return;
        useAgentStore.getState().setViewingFile({ ...viewingFile, content: text });
      })
      .catch(err => {
        // Abort is expected on close/unmount; ignore it.
        if (err?.name === "AbortError") return;
        console.error("Failed to fetch file content:", err);
      });

    return () => controller.abort();
  }, [viewingFile, fileUrl, isHtml, isPdf, isImage]);

  if (!viewingFile) return null;

  return (
    <div className="flex flex-col h-full bg-slate-950 animate-in fade-in zoom-in-95 duration-200">
      <div className="h-10 border-b border-slate-800 flex items-center justify-between px-4 bg-slate-900/60">
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
      <div className="flex-1 overflow-hidden relative bg-white">
        {isHtml || isPdf || isImage ? (
          <iframe
            src={fileUrl}
            className="w-full h-full border-0 block"
            title={viewingFile.name}
          />
        ) : (
          <div className="h-full overflow-auto p-4 scrollbar-thin bg-background text-foreground">
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
    <div className={`flex flex-col border-t border-slate-800 bg-slate-900/20 transition-all duration-300 ${isCollapsed ? 'h-10 shrink-0 overflow-hidden' : 'flex-1 min-h-0'}`}>
      <div
        className="p-3 bg-slate-900/40 border-b border-slate-800 cursor-pointer hover:bg-slate-800/60 flex items-center justify-between"
        onClick={() => setIsCollapsed(!isCollapsed)}
      >
        <h2 className="text-[10px] font-bold text-slate-400/80 uppercase tracking-widest flex items-center gap-2">
          <span className="text-cyan-500/60">{ICONS.activity}</span>
          Tasks
          {tasks.length > 0 && !isCollapsed && (
            <span className="bg-primary/20 text-primary px-1.5 py-0.5 rounded text-[9px] font-mono">{tasks.length}</span>
          )}
        </h2>
        <span className={`text-[9px] text-primary/60 transition-transform duration-200 ${isCollapsed ? 'rotate-180' : ''}`}>
          ▼
        </span>
      </div>
      {!isCollapsed && (
        <div className="flex-1 overflow-y-auto scrollbar-thin p-2 space-y-2">
          {tasks.length === 0 ? (
            <div className="text-xs text-muted-foreground/50 text-center py-8 font-mono">No active tasks</div>
          ) : (
            tasks.map((task) => {
              const input = task.input as any;
              const subagent = input.subagent_type || "unknown";
              const description = input.description || "No description";
              const statusConfig = {
                pending: { color: "bg-amber-500/10 text-amber-400 border-amber-500/30", icon: "⏳", label: "PENDING" },
                running: { color: "bg-cyan-500/10 text-cyan-400 border-cyan-500/30", icon: "🔄", label: "RUNNING" },
                complete: { color: "bg-emerald-500/10 text-emerald-400 border-emerald-500/30", icon: "✅", label: "COMPLETE" },
                error: { color: "bg-red-500/10 text-red-400 border-red-500/30", icon: "❌", label: "ERROR" },
              };
              const config = statusConfig[task.status as keyof typeof statusConfig] || statusConfig.running;

              return (
                <div key={task.id} className={`rounded border p-2.5 text-xs ${config.color} bg-card/20`}>
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="font-mono text-[9px] uppercase tracking-wider flex items-center gap-1.5">
                      {config.icon} {subagent.replace("-", " ")}
                    </span>
                    <span className="text-[8px] uppercase tracking-wider opacity-70">{config.label}</span>
                  </div>
                  <div className="line-clamp-3 opacity-70 leading-relaxed font-light">{description}</div>
                </div>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

const API_BASE = "";

function FileExplorer() {
  const currentSession = useAgentStore((s) => s.currentSession);
  const setViewingFile = useAgentStore((s) => s.setViewingFile);
  const [mode, setMode] = useState<"session" | "artifacts">("session");
  const [path, setPath] = useState("");
  const [files, setFiles] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(false);

  useEffect(() => {
    const sessionId = currentSession?.session_id;
    if (mode === "session" && !sessionId) return;

    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    const url = mode === "artifacts"
      ? `${API_BASE}/api/artifacts?path=${encodeURIComponent(path)}`
      : `${API_BASE}/api/files?session_id=${sessionId}&path=${encodeURIComponent(path)}`;
    fetch(url)
      .then(res => res.json())
      .then(data => {
        const sortedFiles = (data.files || []).sort((a: any, b: any) => {
          if (a.is_dir === b.is_dir) {
            return a.name.localeCompare(b.name);
          }
          return a.is_dir ? -1 : 1;
        });
        setFiles(sortedFiles);
      })
      .catch(err => console.error("Failed to fetch files:", err))
      .finally(() => setLoading(false));
  }, [currentSession?.session_id, path, mode]);

  const handleNavigate = (itemName: string, isDir: boolean) => {
    if (!isDir) {
      // Open file preview
      const fullPath = path ? `${path}/${itemName}` : itemName;
      setViewingFile({ name: itemName, path: fullPath, type: mode === "artifacts" ? "artifact" : "file" });
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
    <div className={`flex flex-col border-b border-slate-800 transition-all duration-300 ${isCollapsed ? 'h-10 shrink-0 overflow-hidden' : 'flex-1 min-h-0'}`}>
      <div
        className="p-3 border-b border-slate-800 bg-slate-900/40 flex items-center justify-between cursor-pointer hover:bg-slate-800/60"
        onClick={(e) => {
          // Prevent collapse when clicking the 'Up' button
          if ((e.target as HTMLElement).tagName === 'BUTTON') return;
          setIsCollapsed(!isCollapsed);
        }}
      >
        <div className="flex items-center gap-2 overflow-hidden">
          <span className={`text-[9px] text-cyan-500/60 transition-transform duration-200 ${isCollapsed ? '-rotate-90' : ''}`}>▼</span>
          <h2 className="text-[10px] font-bold text-slate-400/80 uppercase tracking-widest truncate" title={currentSession?.session_id}>
            <span className="text-cyan-500/60 mr-1">{ICONS.folder}</span>
            {mode === "artifacts" ? (path ? `Artifacts/.../${path.split("/").pop()}` : "Artifacts") : (path ? `.../${path.split("/").pop()}` : "Files")}
          </h2>
        </div>
        {!isCollapsed && (
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => { setMode("session"); setPath(""); }}
              className={`text-[9px] px-2 py-1 rounded border font-medium transition-all ${mode === "session" ? "bg-primary/20 text-primary border-primary/40" : "bg-card/40 text-muted-foreground/70 border-border/40 hover:bg-card/60"}`}
              title="Browse session files"
            >
              SESSION
            </button>
            <button
              type="button"
              onClick={() => { setMode("artifacts"); setPath(""); }}
              className={`text-[9px] px-2 py-1 rounded border font-medium transition-all ${mode === "artifacts" ? "bg-primary/20 text-primary border-primary/40" : "bg-card/40 text-muted-foreground/70 border-border/40 hover:bg-card/60"}`}
              title="Browse persistent artifacts"
            >
              ARTIFACTS
            </button>
            {path && (
              <button onClick={handleUp} className="text-xs hover:bg-primary/10 p-1 rounded text-primary" title="Go Up">
                ⬆️
              </button>
            )}
          </div>
        )}
      </div>
      {!isCollapsed && (
        <div className="flex-1 overflow-y-auto scrollbar-thin p-1">
          {mode === "session" && !currentSession ? (
            <div className="text-xs text-muted-foreground/60 text-center py-4 font-mono">NO ACTIVE SESSION</div>
          ) : loading ? (
            <div className="text-xs text-muted-foreground/60 text-center py-4 font-mono">LOADING...</div>
          ) : files.length === 0 ? (
            <div className="text-xs text-muted-foreground/60 text-center py-4 font-mono">EMPTY DIRECTORY</div>
          ) : (
            <div className="space-y-0.5">
              {files.map((file, i) => (
                <div
                  key={i}
                  className={`text-xs px-2 py-1.5 rounded flex items-center gap-2 cursor-pointer transition-all ${file.is_dir ? "hover:bg-cyan-500/10 text-cyan-400/80" : "hover:bg-slate-800/60 text-slate-300/70"
                    }`}
                  onClick={() => handleNavigate(file.name, file.is_dir)}
                >
                  <span className="opacity-60">{file.is_dir ? ICONS.folder : ICONS.file}</span>
                  <span className="truncate flex-1 font-mono">{file.name}</span>
                  {file.size && <span className="text-[9px] opacity-40 font-mono">{formatFileSize(file.size)}</span>}
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
    disconnected: {
      color: "bg-red-500",
      label: "OFFLINE",
      pulse: false,
      textColor: "text-red-400",
      glow: "shadow-red-500/50"
    },
    connecting: {
      color: "bg-yellow-500",
      label: "CONNECTING",
      pulse: true,
      textColor: "text-yellow-400",
      glow: "shadow-yellow-500/50"
    },
    connected: {
      color: "bg-primary",
      label: "ONLINE",
      pulse: true,
      textColor: "text-status-connected",
      glow: "shadow-primary/50"
    },
    processing: {
      color: "bg-primary",
      label: "PROCESSING",
      pulse: true,
      textColor: "text-status-processing",
      glow: "shadow-primary/50"
    },
    error: {
      color: "bg-red-600",
      label: "ERROR",
      pulse: true,
      textColor: "text-status-error",
      glow: "shadow-red-500/50"
    },
  };

  const config = statusConfig[status];

  return (
    <div className="flex items-center gap-3">
      <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-slate-900/40 border border-slate-800">
        <div
          className={`w-2 h-2 rounded-full ${config.color} ${config.pulse ? "status-pulse" : ""
            }`}
        />
        <span className={`text-[10px] font-bold uppercase tracking-widest ${config.textColor}`}>
          {config.label}
        </span>
      </div>
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
    if (!startTime) return;
    const interval = setInterval(() => {
      setDuration((Date.now() - startTime) / 1000);
    }, 1000); // Update every second
    return () => clearInterval(interval);
  }, [startTime]);

  const currentSession = useAgentStore((s) => s.currentSession);
  const sessionId = currentSession?.workspace ? currentSession.workspace.split('/').pop() : 'NO SESSION';

  return (
    <div className="hidden md:flex items-center gap-6 mr-6 px-5 py-2.5 rounded-lg bg-slate-900/40 border border-slate-800 tactical-panel min-w-fit">
      <div className="flex items-center gap-2 text-[0.7rem] tracking-wider">
        <span className="font-mono text-primary whitespace-nowrap" title={sessionId}>{sessionId}</span>
      </div>
      <div className="w-px h-4 bg-border/40" />
      <div className="flex items-center gap-2 text-xs tracking-wider">
        <span className="text-muted-foreground/70 font-semibold text-[0.7rem]">TOKENS</span>
        <span className="font-mono text-[0.7rem]">{tokenUsage.total.toLocaleString()}</span>
      </div>
      <div className="w-px h-4 bg-border/40" />
      <div className="flex items-center gap-2 text-xs tracking-wider">
        <span className="text-muted-foreground/70 font-semibold text-[0.7rem]">TOOLS</span>
        <span className="font-mono text-[0.7rem]">{toolCallCount}</span>
      </div>
      <div className="w-px h-4 bg-border/40" />
      <div className="flex items-center gap-2 text-xs tracking-wider">
        <span className="text-muted-foreground/70 font-semibold text-[0.7rem]">TIME</span>
        <span className="font-mono text-[0.7rem]">{formatDuration(startTime ? duration : 0)}</span>
      </div>
      <div className="w-px h-4 bg-border/40" />
      <div className="flex items-center gap-2 text-xs tracking-wider">
        <span className="text-muted-foreground/70 font-semibold text-[0.7rem]">ITERS</span>
        <span className="font-mono text-[0.7rem]">{iterationCount}</span>
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
                {typeof toolCall.result.content_preview === "string" ? (
                  <div className="whitespace-pre-wrap font-mono">
                    <LinkifiedText text={toolCall.result.content_preview} />
                  </div>
                ) : (
                  toolCall.result.content_preview
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// --- Markdown Helper ---
const { isLikelyUrl, isLikelyPath, normalizeUrl } = linkify;

const markdownComponents: any = {
  // Override paragraph to linkify file paths in text
  p: ({ children }: any) => {
    return (
      <div className="whitespace-pre-wrap mb-2 last:mb-0">
        {React.Children.map(children, (child, index) => {
          if (typeof child === "string") {
            return <LinkifiedText key={index} text={child} />;
          }
          return <React.Fragment key={index}>{child}</React.Fragment>;
        })}
      </div>
    );
  },
  // Ensure links open in new tab
  a: ({ href, children }: any) => (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-cyan-400 hover:underline font-medium"
      onClick={(e) => e.stopPropagation()}
    >
      {children}
    </a>
  ),
  // Style lists
  ul: ({ children }: any) => <ul className="list-disc pl-4 mb-2">{children}</ul>,
  ol: ({ children }: any) => <ol className="list-decimal pl-4 mb-2">{children}</ol>,
  li: ({ children }: any) => (
    <li className="mb-0.5">
      {React.Children.map(children, (child, index) => {
        if (typeof child === "string") {
          return <LinkifiedText key={index} text={child} />;
        }
        return <React.Fragment key={index}>{child}</React.Fragment>;
      })}
    </li>
  ),
  // Style headers
  h1: ({ children }: any) => <h1 className="text-xl font-bold mb-2 mt-4">{children}</h1>,
  h2: ({ children }: any) => <h2 className="text-lg font-bold mb-2 mt-3">{children}</h2>,
  h3: ({ children }: any) => <h3 className="text-md font-bold mb-1 mt-2">{children}</h3>,
  // Style code
  code: ({ className, children, ...props }: any) => {
    const match = /language-(\w+)/.exec(className || "");
    const isInline = !match && !String(children).includes("\n");
    const rawCode = String(children).trim();
    if (isInline && rawCode) {
      if (isLikelyUrl(rawCode)) {
        return (
          <a
            href={normalizeUrl(rawCode)}
            target="_blank"
            rel="noopener noreferrer"
            className="text-cyan-400 hover:underline font-medium break-all"
            onClick={(e) => e.stopPropagation()}
          >
            {rawCode}
          </a>
        );
      }
      if (isLikelyPath(rawCode)) {
        return <PathLink path={rawCode} />;
      }
    }
    return (
      <code
        className={`${isInline ? "bg-black/20 text-cyan-400 px-1 rounded font-mono text-xs" : "block bg-black/30 p-2 rounded font-mono text-xs overflow-x-auto"}`}
        {...props}
      >
        {isInline ? children : <LinkifiedText text={String(children)} />}
      </code>
    );
  },
  pre: ({ children }: any) => <pre className="my-2">{children}</pre>,
};

const ThinkingBubble = ({ content }: { content: string }) => {
  const [isExpanded, setIsExpanded] = useState(true);

  if (!content) return null;

  return (
    <div className="flex justify-start mb-4 group ml-10 max-w-[85%]">
      <div className="bg-cyan-500/5 border border-cyan-500/20 rounded-xl p-0 overflow-hidden w-full transition-colors hover:bg-cyan-500/10">
        <div
          className="flex items-center gap-2 cursor-pointer bg-cyan-500/10 px-3 py-2 text-cyan-400 hover:text-cyan-300 transition-colors"
          onClick={() => setIsExpanded(!isExpanded)}
        >
          <span className="text-sm">🧠</span>
          <span className="uppercase tracking-wider font-bold text-[10px]">Thinking Process</span>
          <span className="ml-auto text-[10px] opacity-60 hover:opacity-100">{isExpanded ? "Collapse" : "Expand"}</span>
        </div>
        {isExpanded && (
          <div className="p-3 bg-cyan-500/5">
            <div className="whitespace-pre-wrap text-cyan-200/70 font-mono text-xs leading-relaxed border-l-2 border-cyan-500/30 pl-3">
              {content}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// =============================================================================
// Agent Style Utility — shared between ChatMessage and streaming view
// =============================================================================
function getAgentStyle(author: string) {
  const a = author.toLowerCase();
  if (a.includes("research")) {
    return { icon: "🔍", labelColor: "text-purple-400", iconBg: "bg-purple-500/10", iconBorder: "border-purple-500/20", borderAccent: "border-l-purple-500/40" };
  }
  if (a.includes("report") || a.includes("writer")) {
    return { icon: "📝", labelColor: "text-orange-400", iconBg: "bg-orange-500/10", iconBorder: "border-orange-500/20", borderAccent: "border-l-orange-500/40" };
  }
  if (a.includes("plan") || a.includes("orchestra")) {
    return { icon: "🗺️", labelColor: "text-cyan-400", iconBg: "bg-cyan-500/10", iconBorder: "border-cyan-500/20", borderAccent: "border-l-cyan-500/40" };
  }
  if (a.includes("verify") || a.includes("test")) {
    return { icon: "✅", labelColor: "text-green-400", iconBg: "bg-green-500/10", iconBorder: "border-green-500/20", borderAccent: "border-l-green-500/40" };
  }
  if (a.includes("image") || a.includes("video")) {
    return { icon: "🎨", labelColor: "text-pink-400", iconBg: "bg-pink-500/10", iconBorder: "border-pink-500/20", borderAccent: "border-l-pink-500/40" };
  }
  if (a.includes("subagent")) {
    return { icon: "⚙️", labelColor: "text-emerald-400", iconBg: "bg-emerald-500/10", iconBorder: "border-emerald-500/20", borderAccent: "border-l-emerald-500/40" };
  }
  // Primary Agent (default)
  return { icon: "🤖", labelColor: "text-blue-400", iconBg: "bg-blue-500/10", iconBorder: "border-blue-500/20", borderAccent: "border-l-blue-500/40" };
}

function ChatMessage({ message }: { message: any }) {
  const isUser = message.role === "user";
  const formattedDelta = React.useMemo(() => {
    const delta = message.time_offset;
    if (delta !== undefined) {
      return delta > 0 ? `+${delta.toFixed(1)}s` : `0s`;
    }
    return new Date(message.timestamp).toLocaleTimeString();
  }, [message.time_offset, message.timestamp]);

  if (isUser) {
    return (
      <div className="flex justify-end mb-6">
        <div className="flex flex-col items-end max-w-[85%]">
          <div className="flex items-center gap-2 mb-1 w-full justify-between text-[10px] text-muted-foreground uppercase tracking-wider">
            <span className="opacity-50">{formattedDelta}</span>
            <div className="flex items-center gap-1">
              <span>You</span>
              <span>{ICONS.chat}</span>
            </div>
          </div>
          <div className="bg-cyan-500/10 border border-cyan-500/20 text-slate-100 rounded-xl p-4 shadow-sm text-sm">
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={markdownComponents}
              className="prose prose-sm dark:prose-invert max-w-none"
            >
              {message.content}
            </ReactMarkdown>
          </div>
        </div>
      </div>
    );
  }

  // Assistant: single consolidated bubble per message
  const author = message.author || "Primary Agent";
  const style = getAgentStyle(author);

  return (
    <div className="flex flex-col gap-2 mb-6">
      {/* Thinking block (collapsible, attached to this message) */}
      {message.thinking && <ThinkingBubble content={message.thinking} />}

      <div className="flex justify-start group">
        <div className="flex gap-3 max-w-[90%]">
          <div className={`shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-lg border ${style.iconBg} ${style.iconBorder}`}>
            {style.icon}
          </div>
          <div className="flex flex-col flex-1">
            <div className="flex items-center justify-between mb-1">
              <div className={`text-[10px] uppercase tracking-wider font-medium ${style.labelColor}`}>
                {author}
              </div>
              <div className="text-[9px] text-muted-foreground opacity-50 ml-4">
                {formattedDelta}
              </div>
            </div>
            <div className={`bg-slate-900/80 border border-slate-800 border-l-2 ${style.borderAccent} shadow-md rounded-xl p-4 text-sm leading-relaxed`}>
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={markdownComponents}
                className="prose prose-sm dark:prose-invert max-w-none"
              >
                {message.content}
              </ReactMarkdown>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function ChatInterface() {
  const messages = useAgentStore((s) => s.messages);
  const toolCalls = useAgentStore((s) => s.toolCalls);
  const logs = useAgentStore((s) => s.logs);
  const currentSession = useAgentStore((s) => s.currentSession);
  const sessionAttachMode = useAgentStore((s) => s.sessionAttachMode);
  const setSessionAttachMode = useAgentStore((s) => s.setSessionAttachMode);
  const currentStreamingMessage = useAgentStore((s) => s.currentStreamingMessage);
  const currentThinking = useAgentStore((s) => s.currentThinking);
  const currentAuthor = useAgentStore((s) => s.currentAuthor);
  const setStartTime = useAgentStore((s) => s.setStartTime);
  const [input, setInput] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [pendingQuery, setPendingQuery] = useState<string | null>(null);
  const [chatRole, setChatRole] = useState<"writer" | "viewer">("writer");
  const connectionStatus = useAgentStore((s) => s.connectionStatus);
  const ws = getWebSocket();

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const role = (params.get("role") || "").trim().toLowerCase();
    setChatRole(role === "viewer" ? "viewer" : "writer");
    // Pre-fill input from ?message= query param (used by dashboard Quick Command)
    const prefill = (params.get("message") || "").trim();
    if (prefill) {
      setInput(prefill);
      // Clean up the URL so refreshing doesn't re-fill
      const url = new URL(window.location.href);
      url.searchParams.delete("message");
      window.history.replaceState({}, "", url.toString());
    }
  }, []);

  const handleSend = async (textOverride?: string) => {
    if (chatRole === "viewer") return;
    const query = textOverride ?? input;
    if (!query.trim() || (isSending && !textOverride)) return;

    setIsSending(true);
    if (!textOverride) setInput("");

    // Set Start Time if not already set (new run)
    if (!useAgentStore.getState().startTime) {
      setStartTime(Date.now());
    }

    // Add user message to store
    useAgentStore.getState().addMessage({
      role: "user",
      content: query,
      time_offset: 0,
      is_complete: true,
    });

    try {
      await ws.sendQuery(query);
    } catch (error) {
      // Avoid triggering Next.js dev overlay for transient connectivity issues.
      console.warn("Failed to send query:", error);
      useAgentStore.getState().setLastError("Failed to send query. Check connection.");
    } finally {

      setIsSending(false);
    }
  };

  // Handle pending query after cancellation
  useEffect(() => {
    if (connectionStatus === "connected" && pendingQuery) {
      const query = pendingQuery;
      setPendingQuery(null);
      handleSend(query);
    }
  }, [connectionStatus, pendingQuery]);

  const messagesEndRef = React.useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, currentStreamingMessage]);

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto scrollbar-thin p-4">
        {chatRole === "viewer" && (
          <div className="mb-3 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[10px] uppercase tracking-wider text-amber-300">
            Viewer mode: read-only attachment. Open as writer to send messages.
          </div>
        )}
        {sessionAttachMode === "tail" && currentSession?.session_id && (
          <div className="mb-3 flex items-center justify-between rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-[10px] uppercase tracking-wider text-emerald-300">
            <span>Attached Session (Tail): <span className="font-mono">{currentSession.session_id}</span></span>
            <button
              type="button"
              onClick={() => setSessionAttachMode("default")}
              className="rounded border border-emerald-500/40 px-2 py-0.5 text-[9px] text-emerald-200 hover:bg-emerald-500/20"
            >
              Hide Tag
            </button>
          </div>
        )}
        {messages.length === 0 && !currentStreamingMessage ? (
          <div className="h-full flex items-center justify-center">
            <div className="text-center max-w-md">
              <div className="relative inline-block mb-6">
                <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-primary/20 to-primary/5 border border-primary/30 flex items-center justify-center mx-auto">
                  <span className="text-4xl">{ICONS.chat}</span>
                </div>
                <div className="absolute inset-0 rounded-2xl animate-ping opacity-5 bg-primary" />
              </div>
              <div className="font-display text-lg font-bold text-foreground mb-2 tracking-wide">
                UNIVERSAL AGENT
              </div>
              <div className="text-sm text-muted-foreground/70 font-mono">
                Initialize your neural query sequence
              </div>
              <div className="mt-6 flex items-center justify-center gap-2 text-[9px] text-muted-foreground/50 uppercase tracking-widest">
                <span>Ready to process</span>
                <div className="w-1.5 h-1.5 rounded-full bg-primary/60 animate-pulse" />
              </div>
            </div>
          </div>
        ) : (
          <>
            {messages.map((msg) => (
              <ChatMessage key={msg.id} message={msg} />
            ))}
            {/* Streaming Thinking Context */}
            {currentThinking && <ThinkingBubble content={currentThinking} />}

            {currentStreamingMessage && (() => {
              const author = currentAuthor || "Primary Agent";
              const style = getAgentStyle(author);
              // Re-trigger the scanline animation whenever streaming text grows.
              // This is intentionally keyed on length to avoid adding any per-character latency.
              const scanlineKey = `${author}:${currentStreamingMessage.length}`;
              return (
                <div className="flex justify-start mb-4">
                  <div className="flex gap-3 max-w-[90%]">
                    <div className={`shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-lg border ${style.iconBg} ${style.iconBorder}`}>
                      {style.icon}
                    </div>
                    <div className="flex flex-col flex-1">
                      <div className={`text-[10px] uppercase tracking-wider font-medium mb-1 ${style.labelColor}`}>
                        {author}
                      </div>
                      <div className={`relative overflow-hidden bg-card border border-border/50 border-l-2 ${style.borderAccent} shadow-md rounded-xl p-4 text-sm leading-relaxed`}>
                        <span key={scanlineKey} className="ua-scanline-pulse" aria-hidden="true" />
                        <div className="whitespace-pre-wrap">
                          {currentStreamingMessage}
                          <span className="ua-stream-cursor" aria-hidden="true" />
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })()}
            {connectionStatus === "processing" && !currentStreamingMessage && (
              <div className="flex justify-start mb-4">
                <div className="flex items-center gap-3 text-slate-200 bg-slate-900/60 border border-slate-800 px-4 py-2.5 rounded-lg text-xs processing-bar">
                  {(() => {
                    // Check for running tools
                    const runningTool = toolCalls.find((tc: any) => tc.status === 'running');
                    if (runningTool) {
                      return (
                        <>
                          <span className="text-base animate-pulse">⚙️</span>
                          <span className="uppercase tracking-wider">Executing: <span className="font-mono text-status-processing">{runningTool.name}</span></span>
                        </>
                      );
                    }

                    // Check for recent active logs (last 5 seconds)
                    // This captures "Step 1/4" or "Local Toolkit" updates that aren't formal tool calls
                    const now = Date.now();
                    const recentLog = logs
                      .slice()
                      .reverse()
                      .find((l: any) =>
                        (now - l.timestamp < 5000) &&
                        (l.message.includes("Step") || l.prefix.includes("Toolkit") || l.message.includes("Generating"))
                      );

                    if (recentLog) {
                      return (
                        <>
                          <span className="text-base animate-pulse">⚡</span>
                          <span className="truncate max-w-[500px] uppercase tracking-wider">{recentLog.prefix}: {recentLog.message}</span>
                        </>
                      );
                    }

                    // Default fallback
                    return (
                      <>
                        <span className="text-base animate-pulse">🧠</span>
                        <span className="uppercase tracking-wider">Processing neural sequence...</span>
                      </>
                    );
                  })()}
                </div>
              </div>
            )}
            <div ref={messagesEndRef} />
          </>
        )}
      </div>

      {/* Input - Floating Bar Style */}
      <div className="p-4 bg-slate-900/60 border border-slate-800 backdrop-blur-md mb-20 md:mb-10 ml-4 md:ml-64 mr-4 md:mr-6 rounded-2xl shadow-xl transition-all duration-300">
        <div className="flex gap-3">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                if (chatRole === "viewer") return;
                if (connectionStatus === "processing" && input.trim()) {
                  // Stop & Send
                  setPendingQuery(input);
                  setInput("");
                  ws.sendCancel(`Interrupted by user query: ${input}`);
                } else {
                  // Allow sending even if currently disconnected; `sendQuery` will auto-connect.
                  handleSend();
                }
              }
            }}
            placeholder={
              chatRole === "viewer"
                ? "Viewer mode: input disabled"
                : connectionStatus === "processing"
                  ? "Type to redirect (Enter to stop & send)..."
                  : "Enter your neural query..."
            }
            disabled={chatRole === "viewer"}
            className="flex-1 bg-slate-950/50 border border-slate-800 rounded-lg px-4 py-3 text-sm focus:outline-none focus:border-cyan-500/50 focus:shadow-glow-sm disabled:opacity-50 transition-all font-mono"
          />
          {connectionStatus === "processing" && chatRole !== "viewer" ? (
            <button
              onClick={() => ws.sendCancel()}
              className="bg-red-500/20 hover:bg-red-500/30 border border-red-500/40 text-red-400 px-4 py-2 rounded-lg transition-all flex items-center gap-2 text-xs font-bold uppercase tracking-wider"
              title="Stop the current agent run"
            >
              ⏹ Abort
            </button>
          ) : (
            <button
              onClick={() => handleSend()}
              disabled={chatRole === "viewer" || isSending || !input.trim()}
              className="bg-cyan-600 hover:bg-cyan-500 disabled:bg-cyan-600/20 disabled:text-cyan-400/40 text-white px-5 py-2 rounded-lg transition-all btn-primary text-xs font-bold uppercase tracking-wider flex items-center gap-2"
            >
              <span>Send</span>
              <span>{ICONS.send}</span>
            </button>
          )}
        </div>
      </div>
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
        // Fetch valid files from ROOT
        const resRoot = await fetch(`${API_BASE}/api/files?session_id=${currentSession.session_id}&path=.`);
        const dataRoot = await resRoot.json();
        const filesRoot = dataRoot.files || [];

        // Fetch valid files from WORK_PRODUCTS
        const resWp = await fetch(`${API_BASE}/api/files?session_id=${currentSession.session_id}&path=work_products`);
        const dataWp = await resWp.json();
        const filesWp = dataWp.files || [];

        // Merge and filter
        // 1. Root files: Filtered (logs, transcript, reports)
        const relevantRootFiles = filesRoot.filter((f: any) =>
          !f.is_dir && (
            f.name.endsWith('.log') ||
            f.name === 'transcript.md' ||
            f.name.startsWith('report') ||
            f.name.endsWith('.pdf') ||
            f.name.endsWith('.html') ||
            f.name.endsWith('.md')
          )
        );

        // 2. Work Products: Include ALL files from work_products directory
        // We assume anything the agent put there is important
        const relevantWorkProducts = filesWp
          .filter((f: any) => !f.is_dir)
          .map((f: any) => ({ ...f, source: 'work_product' }));

        const interesting = [...relevantRootFiles, ...relevantWorkProducts];

        // Remove duplicates if any
        const unique = Array.from(new Map(interesting.map((item: any) => [item.path, item])).values());

        setKeyFiles(unique);
      } catch (err) {
        console.error("Failed to fetch key files:", err);
      }
    };

    fetchKeyFiles(); // Initial fetch
    const interval = setInterval(fetchKeyFiles, 10000); // Poll every 10 seconds

    return () => clearInterval(interval);
  }, [currentSession?.session_id, workProducts.length]);

  return (
    <div className={`flex flex-col transition-all duration-300 ${isCollapsed ? 'h-10 shrink-0 overflow-hidden' : 'flex-1 min-h-0'}`}>
      <div
        className="p-3 border-b border-slate-800 flex items-center justify-between cursor-pointer hover:bg-slate-800/40"
        onClick={() => setIsCollapsed(!isCollapsed)}
      >
        <h3 className="text-[10px] font-bold text-slate-400/80 uppercase tracking-widest flex items-center gap-2">
          <span className="text-cyan-500/60">{ICONS.file}</span>
          Work Products
          <span className="text-[9px] text-muted-foreground/60 font-normal font-mono">({keyFiles.length})</span>
        </h3>
        <span className={`text-[9px] text-primary/60 transition-transform duration-200 ${isCollapsed ? 'rotate-180' : ''}`}>
          ▼
        </span>
      </div>

      {!isCollapsed && (
        <div className="flex-1 overflow-y-auto scrollbar-thin bg-card/10 p-2 space-y-1">
          {keyFiles.length === 0 ? (
            <div className="text-xs text-muted-foreground/50 text-center py-4 font-mono">No key files found</div>
          ) : (
            keyFiles.map((file, i) => (
              <button
                key={i}
                onClick={() => setViewingFile({ name: file.name, path: file.path, type: 'file' })}
                className="w-full text-left px-3 py-2 text-xs rounded hover:bg-slate-800/40 transition-all flex items-center gap-2 border border-transparent hover:border-slate-800/40 text-slate-300"
              >
                <span className="text-base opacity-60">{file.name.endsWith('pdf') ? '📕' : file.name.endsWith('html') ? '🌐' : '📄'}</span>
                <div className="flex-1 min-w-0">
                  <div className="truncate font-medium font-mono">{file.name}</div>
                  <div className="text-[9px] text-muted-foreground/50 font-mono">{formatFileSize(file.size)}</div>
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

import Image from "next/image";

// ... (previous imports)

// =============================================================================
// Main App Component
// =============================================================================

type DashboardAuthSession = {
  authenticated: boolean;
  auth_required: boolean;
  owner_id: string;
  expires_at?: number | null;
};

export default function HomePage() {
  const connectionStatus = useAgentStore((s) => s.connectionStatus);
  const viewingFile = useAgentStore((s) => s.viewingFile); // Sub to viewing state
  const ws = getWebSocket();
  const [authSession, setAuthSession] = useState<DashboardAuthSession | null>(null);
  const [loadingAuth, setLoadingAuth] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);

  // Layout State
  // We now have: [Chat (flex)] - [Activity (px)] - [Files (px)]
  // We track widths for the two right-side panels.
  const [activityWidth, setActivityWidth] = useState(560);
  const [filesWidth, setFilesWidth] = useState(320);


  const [activityCollapsed, setActivityCollapsed] = useState(false);

  // Resizing Logic
  // Both resizers are on the LEFT edge of their respective panels, dragging expanding to the LEFT (increasing width).
  const startResizing = (panel: 'activity' | 'files') => (mouseDownEvent: React.MouseEvent) => {
    mouseDownEvent.preventDefault();
    const startX = mouseDownEvent.clientX;
    const startWidth = panel === 'activity' ? activityWidth : filesWidth;

    const onMouseMove = (mouseMoveEvent: MouseEvent) => {
      // Dragging LEFT (negative delta) should INCREASE width.
      // delta = current - start. If current < start (moved left), delta is negative.
      // newWidth = startWidth - delta.
      const delta = mouseMoveEvent.clientX - startX;
      const newWidth = Math.max(200, Math.min(800, startWidth - delta));

      if (panel === 'activity') {
        setActivityWidth(newWidth);
      } else {
        setFilesWidth(newWidth);
      }
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

  // Responsive State
  const [activeMobileTab, setActiveMobileTab] = useState<'chat' | 'activity' | 'files' | 'dashboard'>('chat');
  const [showTabletFiles, setShowTabletFiles] = useState(false);
  const [isMobile, setIsMobile] = useState(false);
  const [isDesktop, setIsDesktop] = useState(true);

  // Track screen size to conditionally apply inline styles (avoiding hydration mismatch)
  useEffect(() => {
    const handleResize = () => {
      const w = window.innerWidth;
      setIsMobile(w < 768);
      setIsDesktop(w >= 1280);
    };

    // Initial check
    handleResize();

    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // Approval modal hook
  const { pendingApproval, handleApprove, handleReject } = useApprovalModal();
  const { pendingInput, handleSubmit: handleInputSubmit, handleCancel: handleInputCancel } = useInputModal();

  useEffect(() => {
    let cancelled = false;
    fetch("/api/dashboard/auth/session", { cache: "no-store" })
      .then(async (response) => {
        const payload = (await response.json()) as DashboardAuthSession;
        if (cancelled) return;
        if (!response.ok && response.status !== 401) {
          throw new Error(`Auth check failed (${response.status})`);
        }
        setAuthSession(payload);
      })
      .catch((error) => {
        if (cancelled) return;
        setAuthError((error as Error).message || "Auth check failed");
        setAuthSession({
          authenticated: false,
          auth_required: true,
          owner_id: "owner_primary",
        });
      })
      .finally(() => {
        if (!cancelled) setLoadingAuth(false);
      });

    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (loadingAuth) return;
    if (authSession?.auth_required && !authSession.authenticated) {
      ws.disconnect();
      return;
    }

    // Connect to WebSocket on mount. If a session is explicitly requested in URL,
    // attach to that session in this tab before opening the socket.
    const params = typeof window !== "undefined" ? new URLSearchParams(window.location.search) : null;
    const requestedSessionId = (params?.get("session_id") || "").trim();
    const requestedAttach = (params?.get("attach") || "").trim().toLowerCase();

    if (requestedSessionId) {
      const store = useAgentStore.getState();
      store.setSessionAttachMode(requestedAttach === "tail" ? "tail" : "default");
      ws.attachToSession(requestedSessionId);
    } else {
      ws.connect();
    }

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
      "cancelled",
      "error",
      "iteration_end",
      "system_event",
      "system_presence",
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
  }, [ws, loadingAuth, authSession?.auth_required, authSession?.authenticated]);

  if (loadingAuth) {
    return (
      <div className="flex h-screen items-center justify-center bg-gradient-to-br from-slate-950 via-zinc-950 to-slate-900 text-slate-200">
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 px-6 py-5 text-sm text-slate-300">
          Verifying access...
        </div>
      </div>
    );
  }

  if (authSession?.auth_required && !authSession.authenticated) {
    return (
      <div className="flex h-screen items-center justify-center bg-gradient-to-br from-slate-950 via-zinc-950 to-slate-900 text-slate-100 p-4">
        <div className="w-full max-w-md rounded-xl border border-slate-800 bg-slate-900/80 p-5">
          <h1 className="text-lg font-semibold">Dashboard Access Required</h1>
          <p className="mt-1 text-sm text-slate-400">Sign in from the dashboard shell to use chat and session controls.</p>
          {authError && (
            <div className="mt-3 rounded-md border border-rose-800/70 bg-rose-900/20 px-3 py-2 text-xs text-rose-200">
              {authError}
            </div>
          )}
          <div className="mt-4 flex items-center gap-2">
            <a
              href="/dashboard"
              className="rounded-md border border-cyan-700 bg-cyan-600/20 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-600/30"
            >
              Go to Login
            </a>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-sm text-slate-200 hover:bg-slate-800/70"
            >
              Retry
            </button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <OpsProvider>
      <div className="h-screen flex flex-col bg-gradient-to-br from-slate-950 via-zinc-950 to-slate-900 text-slate-100 relative z-10">
        {/* Header */}
        <header className="h-14 border-b border-slate-800/80 bg-slate-900/80 backdrop-blur-md flex items-center px-4 shrink-0 z-20 relative gap-4">

          {/* Left: Logo & Brand */}
          <div className="flex items-center gap-4 shrink-0 h-full">
            {/* Logo Image */}
            <div className="relative h-full w-32 md:w-48 py-2">
              <Image
                src="/simon_logo_v2.png"
                alt="Simon"
                fill
                className="object-contain object-left"
                priority
              />
            </div>
          </div>

          {/* Center: Ops dropdown buttons - Hidden on Mobile */}
          <div className="hidden md:flex items-center gap-2 shrink-0">
            {([
              { key: "sessions", label: "Sessions", icon: "📋", content: <SessionsSection />, width: "w-[800px]" },
              { key: "calendar", label: "Calendar", icon: "🗓️", content: <CalendarSection />, width: "w-[1100px]" },
              { key: "skills", label: "Skills", icon: "🧩", content: <SkillsSection />, width: "w-[800px]" },
              { key: "channels", label: "Channels", icon: "📡", content: <ChannelsSection />, width: "w-[600px]" },
              { key: "approvals", label: "Approvals", icon: "✅", content: <ApprovalsSection />, width: "w-[600px]" },
              { key: "events", label: "Events", icon: "⚡", content: <SystemEventsSection />, width: "w-[800px]" },
              { key: "config", label: "Config", icon: "⚙️", content: <OpsConfigSection />, width: "w-[800px]" },
              { key: "continuity", label: "Continuity", icon: "📈", content: <SessionContinuityWidget />, width: "w-[520px]" },
            ] as const).map((item) => (
              <Popover key={item.key}>
                <PopoverTrigger asChild>
                  <button
                    type="button"
                    className="flex items-center gap-1.5 px-2 py-1.5 rounded-lg border text-[15px] uppercase tracking-widest font-semibold transition border-border/50 bg-card/40 text-muted-foreground hover:border-primary/40 hover:bg-card/60 data-[state=open]:border-primary/50 data-[state=open]:bg-primary/10 data-[state=open]:text-primary"
                  >
                    <span className="text-xs">{item.icon}</span>
                    <span>{item.label}</span>
                    <span className="text-[8px] transition-transform group-data-[state=open]:rotate-180">▾</span>
                  </button>
                </PopoverTrigger>
                <PopoverContent
                  className={`p-0 overflow-hidden bg-background/95 backdrop-blur-xl border-border/60 shadow-2xl ${item.width}`}
                  align="start"
                  collisionPadding={10}
                >
                  <div className="max-h-[85vh] overflow-y-auto scrollbar-thin">
                    {item.content}
                  </div>
                </PopoverContent>
              </Popover>
            ))}
          </div>

          {/* Right: Metrics, Status */}
          <div className="ml-auto flex items-center gap-2 md:gap-4">
            <a
              href="/files/"
              target="_blank"
              rel="noopener noreferrer"
              className="hidden md:block rounded-lg border border-border/50 bg-card/40 px-3 py-2 text-[15px] uppercase tracking-widest text-muted-foreground hover:border-primary/40 hover:text-primary"
            >
              File Browser
            </a>
            {/* Mobile/Tablet Menu Button could go here */}

            <HeaderMetrics />
            <ConnectionIndicator />
          </div>
        </header>

        {/* Main Content Area */}
        {/* Responsive Layout:
            - Mobile (<768px): Vertical Stack via activeTab 
            - Tablet (768px-1280px): Flex Row (Chat | Activity). Files hidden/toggleable.
            - Desktop (>1280px): Flex Row (Chat | Activity | Files).
        */}
        <div className="flex-1 flex flex-col md:flex-row overflow-hidden relative pb-14 md:pb-0">

          {/* PANEL 1: CHAT / VIEWER */}
          {/* Visible if: Desktop/Tablet OR (Mobile AND tab=='chat') */}
          <main
            className={`
              flex-1 min-w-0 bg-background/30 flex relative flex-col border-r border-border/40
              ${activeMobileTab === 'chat' ? 'flex' : 'hidden md:flex'}
            `}
          >
            {viewingFile ? (
              <div className="flex-1 flex overflow-hidden">
                <div className="flex-1 min-w-0 bg-background/30 flex relative">
                  <FileViewer />
                </div>
              </div>
            ) : (
              <ChatInterface />
            )}
          </main>

          {/* PANEL 2: ACTIVITY LOG */}
          {/* Visible if: Desktop OR Tablet OR (Mobile AND tab=='activity') */}
          <div
            className={`
              border-r border-slate-800 bg-slate-900/20 relative transition-all duration-300 flex-col
              ${activeMobileTab === 'activity' ? 'flex w-full' : 'hidden md:flex'}
              ${activityCollapsed ? 'w-10 shrink-0' : ''}
            `}
            style={
              // On Desktop/Tablet: Use dynamic width. 
              // On Mobile: width is auto/full (handled by flex class above).
              // We only apply inline width style for md+ (which we track via !isMobile to match server assumption of desktop)
              // Note: We use !isMobile here which is updated after mount. On server it's false (isMobile=false),
              // so it renders the width style. Logic:
              // Server: isMobile=false -> renders width style.
              // Client Mount: isMobile=false -> renders width style.
              // Client Effect: Detects isMobile=true -> removes width style.
              !isMobile
                ? (activityCollapsed ? { width: 40, minHeight: '100%' } : { width: activityWidth })
                : {}
            }
          >
            {/* Desktop/Tablet Resizer */}
            <div className="hidden md:block">
              {!activityCollapsed && (
                <div
                  className="absolute top-0 left-0 w-1 h-full cursor-col-resize hover:bg-cyan-500/40 transition-colors z-20"
                  onMouseDown={startResizing('activity')}
                />
              )}
            </div>

            {activityCollapsed ? (
              <button
                type="button"
                onClick={() => setActivityCollapsed(false)}
                className="h-full w-10 flex items-center justify-center hover:bg-card/30 transition-colors border-l border-slate-700/50 bg-slate-900/40"
                title="Expand Activity Log"
              >
                <span className="text-primary/60 text-xs [writing-mode:vertical-lr] rotate-180 tracking-widest uppercase font-bold whitespace-nowrap">{ICONS.activity} Activity ▶</span>
              </button>
            ) : (
              <div className="h-full flex flex-col overflow-hidden">
                <CombinedActivityLog onCollapse={() => setActivityCollapsed(true)} />
              </div>
            )}
          </div>

          {/* PANEL 3: FILES & TASKS */}
          {/* Visible if: Desktop OR (Mobile AND tab=='files') OR Tablet Overlay */}
          <aside
            className={`
              shrink-0 flex-col overflow-hidden bg-slate-900/30 backdrop-blur-sm relative
              ${activeMobileTab === 'files' ? 'flex w-full' : 'hidden xl:flex'}
            `}
            style={
              isDesktop
                ? { width: filesWidth }
                : {}
            }
          >
            {/* Desktop Resizer */}
            <div className="hidden xl:block">
              <div
                className="absolute top-0 left-0 w-1 h-full cursor-col-resize hover:bg-cyan-500/40 transition-colors z-20"
                onMouseDown={startResizing('files')}
              />
            </div>

            <div className="flex-1 flex flex-col min-h-0 pl-1">
              <FileExplorer />
              <div className="border-t border-border/40 pt-2 flex-1 flex flex-col min-h-0">
                <WorkProductViewer />
              </div>
              <HeartbeatWidget />
              <div className="border-t border-border/40 pt-2 h-1/4 flex flex-col min-h-0">
                <TaskPanel />
              </div>
            </div>
          </aside>

          {/* Tablet "Files" Overlay Button */}
          {/* Only shown on Tablet (md) but not Desktop (xl), if files hidden */}
          <div className="hidden md:flex xl:hidden absolute right-0 top-1/2 -translate-y-1/2 z-30">
            <button
              onClick={() => setShowTabletFiles(!showTabletFiles)}
              className="bg-slate-800/80 border border-slate-700 text-slate-300 p-2 rounded-l-lg shadow-xl hover:bg-slate-700"
              title="Toggle Files"
            >
              {ICONS.folder}
            </button>
          </div>

          {/* Tablet Files Overlay Drawer */}
          {showTabletFiles && (
            <div className="absolute right-0 top-0 h-full w-[320px] bg-slate-950/95 border-l border-slate-700 z-40 flex flex-col shadow-2xl animate-in slide-in-from-right-10 duration-200">
              <div className="p-2 border-b border-slate-800 flex justify-between items-center bg-slate-900/50">
                <span className="text-xs font-bold uppercase tracking-wider pl-2">Files & Tasks</span>
                <button onClick={() => setShowTabletFiles(false)} className="p-1 hover:text-white text-slate-400">✕</button>
              </div>
              <div className="flex-1 flex flex-col min-h-0 overflow-hidden">
                <FileExplorer />
                <div className="border-t border-border/40 pt-2 flex-1 flex flex-col min-h-0">
                  <WorkProductViewer />
                </div>
                <HeartbeatWidget />
                <div className="border-t border-border/40 pt-2 h-1/4 flex flex-col min-h-0">
                  <TaskPanel />
                </div>
              </div>
            </div>
          )}

        </div>

        {/* MOBILE DASHBOARD MENU (Visible only on Mobile AND tab=='dashboard') */}
        {/* Note: Desktop header items are hidden on mobile. We expose them here. */}
        <div
          className={`
               flex-1 flex-col overflow-y-auto bg-slate-950/95 p-4 space-y-4 pb-20
               ${activeMobileTab === 'dashboard' ? 'flex' : 'hidden'}
               md:hidden
             `}
        >
          <h2 className="text-sm font-bold uppercase tracking-widest text-muted-foreground border-b border-border/40 pb-2 mb-2">Dashboard Menu</h2>

          {/* Render the sections that are usually in the Header Popovers */}
          {[
            { key: "sessions", label: "Sessions", icon: "📋", content: <SessionsSection /> },
            { key: "calendar", label: "Calendar", icon: "🗓️", content: <CalendarSection /> },
            { key: "skills", label: "Skills", icon: "🧩", content: <SkillsSection /> },
            { key: "channels", label: "Channels", icon: "📡", content: <ChannelsSection /> },
            { key: "approvals", label: "Approvals", icon: "✅", content: <ApprovalsSection /> },
            { key: "events", label: "Events", icon: "⚡", content: <SystemEventsSection /> },
            { key: "config", label: "Config", icon: "⚙️", content: <OpsConfigSection /> },
            { key: "continuity", label: "Continuity", icon: "📈", content: <SessionContinuityWidget /> },
          ].map((item) => (
            <div key={item.key} className="bg-card/20 rounded-lg border border-border/40 overflow-hidden">
              <div className="p-3 bg-card/40 font-bold uppercase tracking-wider text-xs flex items-center gap-2">
                <span>{item.icon}</span> {item.label}
              </div>
              <div className="p-2">
                {/* Most sections are designed for popovers/dropdowns. 
                       They might be wide. We let them flow naturally or scroll horizontally if needed. */}
                <div className="overflow-x-auto">
                  {item.content}
                </div>
              </div>
            </div>
          ))}

          <a
            href="/dashboard"
            className="block rounded-lg border border-border/50 bg-card/40 px-4 py-3 text-center text-sm uppercase tracking-widest text-muted-foreground hover:border-primary/40 hover:text-primary mt-4"
          >
            Go to Dashboard Shell
          </a>
        </div>

      </div>

      {/* Mobile Bottom Tab Bar */}
      <div className="md:hidden fixed bottom-0 left-0 right-0 h-16 bg-slate-950/95 border-t border-slate-800 backdrop-blur-lg flex items-center justify-around z-50 safe-area-bottom pb-env">
        <button
          onClick={() => setActiveMobileTab('chat')}
          className={`flex flex-col items-center gap-1 p-2 w-full ${activeMobileTab === 'chat' ? 'text-cyan-400' : 'text-slate-500'}`}
        >
          <span className="text-xl">{ICONS.chat}</span>
          <span className="text-[9px] uppercase tracking-widest font-bold">Chat</span>
        </button>
        <button
          onClick={() => setActiveMobileTab('activity')}
          className={`flex flex-col items-center gap-1 p-2 w-full ${activeMobileTab === 'activity' ? 'text-amber-400' : 'text-slate-500'}`}
        >
          <span className="text-xl">{ICONS.activity}</span>
          <span className="text-[9px] uppercase tracking-widest font-bold">Activity</span>
        </button>
        <button
          onClick={() => setActiveMobileTab('files')}
          className={`flex flex-col items-center gap-1 p-2 w-full ${activeMobileTab === 'files' ? 'text-purple-400' : 'text-slate-500'}`}
        >
          <span className="text-xl">{ICONS.folder}</span>
          <span className="text-[9px] uppercase tracking-widest font-bold">Files</span>
        </button>
        <button
          onClick={() => setActiveMobileTab('dashboard')}
          className={`flex flex-col items-center gap-1 p-2 w-full ${activeMobileTab === 'dashboard' ? 'text-emerald-400' : 'text-slate-500'}`}
        >
          <span className="text-xl">☰</span>
          <span className="text-[9px] uppercase tracking-widest font-bold">Menu</span>
        </button>
      </div>

      {/* Approval Modal */}
      <ApprovalModal
        request={pendingApproval}
        onApprove={handleApprove}
        onReject={handleReject}
      />
      <InputModal
        request={pendingInput}
        onSubmit={handleInputSubmit}
        onCancel={handleInputCancel}
      />
    </div >
    </OpsProvider >
  );
}
