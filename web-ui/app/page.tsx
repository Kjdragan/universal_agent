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
import { OpsProvider, SessionsSection, SkillsSection, ChannelsSection, ApprovalsSection, SystemEventsSection, OpsConfigSection, SessionContinuityWidget, HeartbeatWidget } from "@/components/OpsDropdowns";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

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
    <div className={`flex flex-col border-t border-border/40 bg-card/10 transition-all duration-300 ${isCollapsed ? 'h-10 shrink-0 overflow-hidden' : 'flex-1 min-h-0'}`}>
      <div
        className="p-3 bg-card/30 border-b border-border/40 cursor-pointer hover:bg-card/40 flex items-center justify-between"
        onClick={() => setIsCollapsed(!isCollapsed)}
      >
        <h2 className="text-[10px] font-bold text-muted-foreground/80 uppercase tracking-widest flex items-center gap-2">
          <span className="text-primary/60">{ICONS.activity}</span>
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
    <div className={`flex flex-col border-b border-border/40 transition-all duration-300 ${isCollapsed ? 'h-10 shrink-0 overflow-hidden' : 'flex-1 min-h-0'}`}>
      <div
        className="p-3 border-b border-border/40 bg-card/30 flex items-center justify-between cursor-pointer hover:bg-card/40"
        onClick={(e) => {
          // Prevent collapse when clicking the 'Up' button
          if ((e.target as HTMLElement).tagName === 'BUTTON') return;
          setIsCollapsed(!isCollapsed);
        }}
      >
        <div className="flex items-center gap-2 overflow-hidden">
          <span className={`text-[9px] text-primary/60 transition-transform duration-200 ${isCollapsed ? '-rotate-90' : ''}`}>▼</span>
          <h2 className="text-[10px] font-bold text-muted-foreground/80 uppercase tracking-widest truncate" title={currentSession?.session_id}>
            <span className="text-primary/60 mr-1">{ICONS.folder}</span>
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
                  className={`text-xs px-2 py-1.5 rounded flex items-center gap-2 cursor-pointer transition-all ${file.is_dir ? "hover:bg-primary/10 text-primary/80" : "hover:bg-card/60 text-foreground/70"
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
      <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-card/40 border border-border/60">
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
    <div className="hidden md:flex items-center gap-6 mr-6 px-5 py-2.5 rounded-lg bg-card/30 border border-border/40 tactical-panel">
      <div className="flex items-center gap-2 text-xs tracking-wider">
        <span className="text-muted-foreground/70 font-semibold">SESSION</span>
        <span className="font-mono text-primary truncate max-w-[120px]" title={sessionId}>{sessionId}</span>
      </div>
      <div className="w-px h-4 bg-border/40" />
      <div className="flex items-center gap-2 text-xs tracking-wider">
        <span className="text-muted-foreground/70 font-semibold">TOKENS</span>
        <span className="font-mono">{tokenUsage.total.toLocaleString()}</span>
      </div>
      <div className="w-px h-4 bg-border/40" />
      <div className="flex items-center gap-2 text-xs tracking-wider">
        <span className="text-muted-foreground/70 font-semibold">TOOLS</span>
        <span className="font-mono">{toolCallCount}</span>
      </div>
      <div className="w-px h-4 bg-border/40" />
      <div className="flex items-center gap-2 text-xs tracking-wider">
        <span className="text-muted-foreground/70 font-semibold">TIME</span>
        <span className="font-mono">{formatDuration(startTime ? duration : 0)}</span>
      </div>
      <div className="w-px h-4 bg-border/40" />
      <div className="flex items-center gap-2 text-xs tracking-wider">
        <span className="text-muted-foreground/70 font-semibold">ITERS</span>
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

// --- Markdown Helper ---
// eslint-disable-next-line no-useless-escape
const LINKIFIABLE_TOKEN_REGEX =
  /(https?:\/\/[^\s<>"'`]+|www\.[^\s<>"'`]+|(?:\/|\.\.?\/)[A-Za-z0-9._~\-\/]+|[A-Za-z]:\\[^\s<>"'`]+|[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+\.[A-Za-z0-9]+)/g;

const isLikelyUrl = (value: string) =>
  /^https?:\/\//i.test(value) || /^www\./i.test(value);

const normalizeUrl = (value: string) =>
  /^www\./i.test(value) ? `https://${value}` : value;

const isLikelyPath = (value: string) => {
  if (!value) return false;
  const looksAbsoluteUnix = value.startsWith("/");
  const looksRelative = value.startsWith("./") || value.startsWith("../");
  const looksWindows = /^[A-Za-z]:\\/.test(value);
  const looksArtifacts = value.startsWith("artifacts/");
  const looksImplicitFile = /^[A-Za-z0-9._-]+\/[A-Za-z0-9._-]+\.[A-Za-z0-9]+$/.test(value);
  return looksAbsoluteUnix || looksRelative || looksWindows || looksArtifacts || looksImplicitFile;
};

const splitTrailingPunctuation = (token: string): [string, string] => {
  const match = token.match(/^(.*?)([),.;!?]+)$/);
  if (!match) return [token, ""];
  return [match[1], match[2]];
};

const resolveArtifactRelativePath = (path: string): string | null => {
  const normalized = path.replace(/\\/g, "/");
  if (normalized.startsWith("artifacts/")) {
    return normalized.slice("artifacts/".length);
  }
  const marker = "/artifacts/";
  const idx = normalized.indexOf(marker);
  if (idx >= 0) {
    return normalized.slice(idx + marker.length);
  }
  return null;
};

const PathLink = ({ path }: { path: string }) => {
  const setViewingFile = useAgentStore((s) => s.setViewingFile);
  const currentSession = useAgentStore((s) => s.currentSession);

  return (
    <button
      type="button"
      onClick={(e) => {
        e.stopPropagation();
        const name = path.split(/[\\/]/).pop() || path;

        const artifactRelativePath = resolveArtifactRelativePath(path);
        if (artifactRelativePath) {
          setViewingFile({ name, path: artifactRelativePath, type: "artifact" });
          return;
        }

        let fullPath = path;
        // Resolve relative paths
        if (!path.startsWith("/") && !path.match(/^[a-zA-Z]:\\/)) {
          if (currentSession?.workspace) {
            // Handle ./ prefix
            const cleanPath = path.replace(/^\.\//, "");
            // Simple join (not robust path generic, but works for linux/web)
            const workspace = currentSession.workspace.endsWith("/")
              ? currentSession.workspace
              : currentSession.workspace + "/";
            fullPath = workspace + cleanPath;
          }
        }

        // Strip session workspace prefix from absolute paths to get relative
        // paths for the file API. The API expects: /api/files/{session_id}/{relative_path}
        if (currentSession?.workspace && fullPath.startsWith(currentSession.workspace)) {
          const wsPrefix = currentSession.workspace.endsWith("/")
            ? currentSession.workspace
            : currentSession.workspace + "/";
          if (fullPath.startsWith(wsPrefix)) {
            fullPath = fullPath.slice(wsPrefix.length);
          }
        }

        setViewingFile({ name, path: fullPath, type: "file" });
      }}
      className="text-primary hover:underline cursor-pointer break-all font-mono bg-primary/10 px-1 rounded mx-0.5 text-left"
      title="Open file preview"
    >
      {path}
    </button>
  );
};

const LinkifiedText = ({ text }: { text: string }) => {
  const parts = text.split(LINKIFIABLE_TOKEN_REGEX);

  return (
    <>
      {parts.map((part, index) => {
        if (index % 2 === 0) return part;
        const [token, trailing] = splitTrailingPunctuation(part);
        if (!token) return part;

        if (isLikelyUrl(token)) {
          return (
            <React.Fragment key={`${token}-${index}`}>
              <a
                href={normalizeUrl(token)}
                target="_blank"
                rel="noopener noreferrer"
                className="text-primary hover:underline font-medium break-all"
                onClick={(e) => e.stopPropagation()}
              >
                {token}
              </a>
              {trailing}
            </React.Fragment>
          );
        }

        if (isLikelyPath(token)) {
          return (
            <React.Fragment key={`${token}-${index}`}>
              <PathLink path={token} />
              {trailing}
            </React.Fragment>
          );
        }

        return part;
      })}
    </>
  );
};

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
      className="text-primary hover:underline font-medium"
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
            className="text-primary hover:underline font-medium break-all"
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
        className={`${isInline ? "bg-black/20 text-primary px-1 rounded font-mono text-xs" : "block bg-black/30 p-2 rounded font-mono text-xs overflow-x-auto"}`}
        {...props}
      >
        {children}
      </code>
    );
  },
  pre: ({ children }: any) => <pre className="my-2">{children}</pre>,
};

function ThinkingBubble({ content }: { content: string }) {
  const [isExpanded, setIsExpanded] = useState(true);

  if (!content) return null;

  return (
    <div className="flex justify-start mb-4 group ml-10 max-w-[85%]">
      <div className="bg-amber-500/5 border border-amber-500/20 rounded-xl p-0 overflow-hidden w-full transition-colors hover:bg-amber-500/10">
        <div
          className="flex items-center gap-2 cursor-pointer bg-amber-500/10 px-3 py-2 text-amber-600/80 hover:text-amber-600 transition-colors"
          onClick={() => setIsExpanded(!isExpanded)}
        >
          <span className="text-sm">🧠</span>
          <span className="uppercase tracking-wider font-bold text-[10px]">Thinking Process</span>
          <span className="ml-auto text-[10px] opacity-60 hover:opacity-100">{isExpanded ? "Collapse" : "Expand"}</span>
        </div>
        {isExpanded && (
          <div className="p-3 bg-amber-500/5">
            <div className="whitespace-pre-wrap text-amber-900/70 dark:text-amber-100/70 font-mono text-xs leading-relaxed border-l-2 border-amber-500/30 pl-3">
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
          <div className="bg-primary/10 border border-primary/20 text-foreground rounded-xl p-4 shadow-sm text-sm">
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
            <div className={`bg-card border border-border/50 border-l-2 ${style.borderAccent} shadow-md rounded-xl p-4 text-sm leading-relaxed`}>
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
  const connectionStatus = useAgentStore((s) => s.connectionStatus);
  const ws = getWebSocket();

  const handleSend = async (textOverride?: string) => {
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
      console.error("Failed to send query:", error);
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
                      <div className={`bg-card border border-border/50 border-l-2 ${style.borderAccent} shadow-md rounded-xl p-4 text-sm leading-relaxed`}>
                        <div className="whitespace-pre-wrap">
                          {currentStreamingMessage}
                          <span className="inline-block w-2 h-4 bg-primary animate-pulse ml-1" />
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              );
            })()}
            {/* Active Process Indicator */}
            {connectionStatus === "processing" && !currentStreamingMessage && (
              <div className="flex justify-start mb-4">
                <div className="flex items-center gap-3 text-foreground/80 bg-card/40 border border-border/60 px-4 py-2.5 rounded-lg text-xs processing-bar">
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
      <div className="p-4 bg-card/40 border border-border/40 backdrop-blur-md mb-10 ml-64 mr-6 rounded-2xl shadow-xl">
        <div className="flex gap-3">
          <input
            type="text"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                if (connectionStatus === "connected") {
                  handleSend();
                } else if (connectionStatus === "processing" && input.trim()) {
                  // Stop & Send
                  setPendingQuery(input);
                  setInput("");
                  ws.sendCancel(`Interrupted by user query: ${input}`);
                }
              }
            }}
            placeholder={connectionStatus === "processing" ? "Type to redirect (Enter to stop & send)..." : "Enter your neural query..."}
            disabled={connectionStatus === "disconnected" || connectionStatus === "connecting"}
            className="flex-1 bg-card/40 border border-border/60 rounded-lg px-4 py-3 text-sm focus:outline-none focus:border-primary/60 focus:shadow-glow-sm disabled:opacity-50 transition-all font-mono"
          />
          {connectionStatus === "processing" ? (
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
              disabled={connectionStatus !== "connected" || isSending || !input.trim()}
              className="bg-primary hover:bg-primary/90 disabled:bg-primary/20 disabled:text-primary/40 text-primary-foreground px-5 py-2 rounded-lg transition-all btn-primary text-xs font-bold uppercase tracking-wider flex items-center gap-2"
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
        className="p-3 border-b border-border/40 flex items-center justify-between cursor-pointer hover:bg-card/30"
        onClick={() => setIsCollapsed(!isCollapsed)}
      >
        <h3 className="text-[10px] font-bold text-muted-foreground/80 uppercase tracking-widest flex items-center gap-2">
          <span className="text-primary/60">{ICONS.file}</span>
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
                className="w-full text-left px-3 py-2 text-xs rounded hover:bg-card/40 transition-all flex items-center gap-2 border border-transparent hover:border-border/40"
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

export default function HomePage() {
  const connectionStatus = useAgentStore((s) => s.connectionStatus);
  const viewingFile = useAgentStore((s) => s.viewingFile); // Sub to viewing state
  const ws = getWebSocket();

  // Layout State
  // We now have: [Chat (flex)] - [Activity (px)] - [Files (px)]
  // We track widths for the two right-side panels.
  const [activityWidth, setActivityWidth] = useState(400);
  const [filesWidth, setFilesWidth] = useState(320);

  const [openDropdown, setOpenDropdown] = useState<string | null>(null);
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

  // Approval modal hook
  const { pendingApproval, handleApprove, handleReject } = useApprovalModal();
  const { pendingInput, handleSubmit: handleInputSubmit, handleCancel: handleInputCancel } = useInputModal();

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
  }, [ws]);

  return (
    <OpsProvider>
      <div className="h-screen flex flex-col bg-background text-foreground relative z-10">
        {/* Header */}
        <header className="h-14 border-b border-border/40 glass-strong flex items-center px-4 shrink-0 z-20 relative gap-4">

          {/* Left: Logo & Brand */}
          <div className="flex items-center gap-4 shrink-0 h-full">
            {/* Logo Image */}
            <div className="relative h-full w-48">
              <Image
                src="/simon_logo_v2.png"
                alt="Simon"
                fill
                className="object-contain"
                priority
              />
            </div>

            <div className="h-8 w-px bg-border/30" />
            <span className="text-[15px] text-muted-foreground/70 px-3 py-2 rounded bg-card/40 border border-border/30 font-mono font-semibold tracking-wide">
              v2.1
            </span>
          </div>

          {/* Center: Ops dropdown buttons - REPOSITIONED to be part of flow */}
          <div className="flex items-center gap-0.5 shrink-0">
            {([
              { key: "sessions", label: "Sessions", icon: "📋", content: <SessionsSection />, width: 1200 },
              { key: "skills", label: "Skills", icon: "🧩", content: <SkillsSection />, width: 1200 },
              { key: "channels", label: "Channels", icon: "📡", content: <ChannelsSection />, width: 800 },
              { key: "approvals", label: "Approvals", icon: "✅", content: <ApprovalsSection />, width: 800 },
              { key: "events", label: "Events", icon: "⚡", content: <SystemEventsSection />, width: 900 },
              { key: "config", label: "Config", icon: "⚙️", content: <OpsConfigSection />, width: 1200 },
            ] as const).map((item) => (
              <div key={item.key} className="relative">
                <button
                  type="button"
                  onClick={() => setOpenDropdown((prev) => prev === item.key ? null : item.key)}
                  className={`flex items-center gap-1.5 px-2 py-1.5 rounded-lg border text-[15px] uppercase tracking-widest font-semibold transition ${openDropdown === item.key
                    ? "border-primary/50 bg-primary/10 text-primary"
                    : "border-border/50 bg-card/40 text-muted-foreground hover:border-primary/40 hover:bg-card/60"
                    }`}
                >
                  <span className="text-xs">{item.icon}</span>
                  <span>{item.label}</span>
                  <span className={`text-[8px] transition-transform ${openDropdown === item.key ? "rotate-180" : ""}`}>▾</span>
                </button>
                {openDropdown === item.key && (
                  <div
                    className="absolute top-full left-1/2 -translate-x-1/2 mt-2 max-h-[85vh] overflow-hidden rounded-lg border border-border/60 bg-background/95 backdrop-blur-xl shadow-2xl z-50"
                    style={{ width: item.width }}
                  >
                    <div className="max-h-[85vh] overflow-y-auto scrollbar-thin">
                      {item.content}
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
          {/* Click-away overlay to close dropdowns */}
          {openDropdown && (
            <div className="fixed inset-0 z-10" onClick={() => setOpenDropdown(null)} />
          )}

          {/* Right: Metrics, Status */}
          <div className="ml-auto flex items-center gap-4">
            <a
              href="/dashboard"
              className="rounded-lg border border-border/50 bg-card/40 px-3 py-2 text-[15px] uppercase tracking-widest text-muted-foreground hover:border-primary/40 hover:text-primary"
            >
              Dashboard Shell
            </a>
            <HeaderMetrics />
            <ConnectionIndicator />
          </div>
        </header>

        {/* Main Content Area */}
        {/* Layout: [Chat (Left, Flex)] | [Activity (Center, Fixed)] | [Files (Right, Fixed)] */}
        <div className="flex-1 flex overflow-hidden relative">

          {viewingFile ? (
            <div className="flex-1 flex overflow-hidden">
              {/* If viewing a file, we might want to hide chat or just overlay? 
                 Current logic expects viewingFile to take over the main area.
                 Let's keep it simple: if viewingFile, it takes the 'Chat' slot.
             */}
              <div className="flex-1 min-w-0 bg-background/30 flex relative">
                <FileViewer />
              </div>
            </div>
          ) : (
            <main className="flex-1 min-w-0 bg-background/30 flex relative flex-col border-r border-border/40">
              <ChatInterface />
            </main>
          )}

          {/* Center: Activity Log */}
          {/* This panel sits between Chat (Left) and Files (Right) */}
          <div
            className={`min-h-0 border-r border-border/40 bg-card/10 relative transition-all duration-300 flex flex-col ${activityCollapsed ? 'w-10 shrink-0' : ''}`}
            style={activityCollapsed ? { width: 40 } : { width: activityWidth }}
          >
            {/* Resizer handle on the LEFT edge of this panel */}
            {!activityCollapsed && (
              <div
                className="absolute top-0 left-0 w-1 h-full cursor-col-resize hover:bg-primary/40 transition-colors z-20"
                onMouseDown={startResizing('activity')}
              />
            )}

            {activityCollapsed ? (
              <button
                type="button"
                onClick={() => setActivityCollapsed(false)}
                className="h-full w-full flex items-center justify-center hover:bg-card/30 transition-colors"
                title="Expand Activity Log"
              >
                <span className="text-primary/60 text-xs [writing-mode:vertical-lr] rotate-180 tracking-widest uppercase font-bold">{ICONS.activity} Activity</span>
              </button>
            ) : (
              <div className="h-full flex flex-col overflow-hidden">
                <CombinedActivityLog onCollapse={() => setActivityCollapsed(true)} />
              </div>
            )}
          </div>

          {/* Right Sidebar: Files & Tasks */}
          <aside
            className="shrink-0 flex flex-col overflow-hidden bg-card/20 backdrop-blur-sm relative"
            style={{ width: filesWidth }}
          >
            {/* Resizer on the LEFT edge */}
            <div
              className="absolute top-0 left-0 w-1 h-full cursor-col-resize hover:bg-primary/40 transition-colors z-20"
              onMouseDown={startResizing('files')}
            />

            <div className="flex-1 flex flex-col min-h-0 pl-1">
              {/* Added pl-1 to avoid overlap with resizer */}
              <FileExplorer />
              <div className="border-t border-border/40 pt-2 flex-1 flex flex-col min-h-0">
                <WorkProductViewer />
              </div>
              <SessionContinuityWidget />
              <HeartbeatWidget />
              <div className="border-t border-border/40 pt-2 h-1/4 flex flex-col min-h-0">
                <TaskPanel />
              </div>
            </div>
          </aside>

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
