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
import { OpsPanel } from "@/components/OpsPanel";
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
        setFiles(data.files || []);
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
            {ICONS.folder} {mode === "artifacts" ? (path ? `Artifacts/.../${path.split("/").pop()}` : "Artifacts") : (path ? `.../${path.split("/").pop()}` : "Files")}
          </h2>
        </div>
        {!isCollapsed && (
          <div className="flex items-center gap-1">
            <button
              type="button"
              onClick={() => { setMode("session"); setPath(""); }}
              className={`text-[10px] px-2 py-1 rounded border ${mode === "session" ? "bg-primary/20 text-primary border-primary/30" : "bg-background/40 text-muted-foreground border-border/50 hover:bg-background/60"}`}
              title="Browse session files"
            >
              Session
            </button>
            <button
              type="button"
              onClick={() => { setMode("artifacts"); setPath(""); }}
              className={`text-[10px] px-2 py-1 rounded border ${mode === "artifacts" ? "bg-primary/20 text-primary border-primary/30" : "bg-background/40 text-muted-foreground border-border/50 hover:bg-background/60"}`}
              title="Browse persistent artifacts"
            >
              Artifacts
            </button>
            {path && (
              <button onClick={handleUp} className="text-xs hover:bg-black/20 p-1 rounded" title="Go Up">
                ⬆️
              </button>
            )}
          </div>
        )}
      </div>
      {!isCollapsed && (
        <div className="flex-1 overflow-y-auto scrollbar-thin p-1">
          {mode === "session" && !currentSession ? (
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
    if (!startTime) return;
    const interval = setInterval(() => {
      setDuration((Date.now() - startTime) / 1000);
    }, 1000); // Update every second
    return () => clearInterval(interval);
  }, [startTime]);

  const currentSession = useAgentStore((s) => s.currentSession);
  const sessionId = currentSession?.workspace ? currentSession.workspace.split('/').pop() : 'No Session';

  return (
    <div className="hidden md:flex items-center gap-3 mr-6 bg-secondary/20 px-3 py-1.5 rounded-md border border-border/50">
      <div className="flex items-center gap-2 text-[10px] tracking-wide">
        <span className="text-muted-foreground font-semibold">SESSION</span>
        <span className="font-mono text-primary truncate max-w-[150px]" title={sessionId}>{sessionId}</span>
      </div>
      <div className="w-px h-3 bg-border/50" />
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
        <span className="font-mono">{formatDuration(startTime ? duration : 0)}</span>
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

// --- Markdown Helper ---
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
        {React.Children.map(children, (child) => {
          if (typeof child === "string") {
            return <LinkifiedText text={child} />;
          }
          return child;
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
      {React.Children.map(children, (child) => {
        if (typeof child === "string") {
          return <LinkifiedText text={child} />;
        }
        return child;
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

function ChatMessage({ message }: { message: any }) {
  const isUser = message.role === "user";
  const formattedDelta = React.useMemo(() => {
    const delta = message.time_offset;
    if (delta !== undefined) {
      return delta > 0 ? `+${delta.toFixed(1)}s` : `0s`;
    }
    return new Date(message.timestamp).toLocaleTimeString();
  }, [message.time_offset, message.timestamp]);

  // Split content by distinct sections (double newlines)
  const contentSegments = message.content.split(/\n\n+/).filter(Boolean);

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

  // Assistant: Render segments as specific bubbles
  const author = message.author || "Primary Agent";
  const isResearch = author.toLowerCase().includes("research");
  const isSubagent = author.toLowerCase().includes("subagent");

  let icon = "🤖";
  let labelColor = "text-blue-400";
  let iconBg = "bg-blue-500/10";
  let iconBorder = "border-blue-500/20";

  if (isResearch) {
    icon = "🔍";
    labelColor = "text-purple-400";
    iconBg = "bg-purple-500/10";
    iconBorder = "border-purple-500/20";
  } else if (author.toLowerCase().includes("report") || author.toLowerCase().includes("writer")) {
    icon = "📝";
    labelColor = "text-orange-400";
    iconBg = "bg-orange-500/10";
    iconBorder = "border-orange-500/20";
  } else if (author.toLowerCase().includes("plan") || author.toLowerCase().includes("orchestra")) {
    icon = "🗺️";
    labelColor = "text-cyan-400";
    iconBg = "bg-cyan-500/10";
    iconBorder = "border-cyan-500/20";
  } else if (author.toLowerCase().includes("verify") || author.toLowerCase().includes("test")) {
    icon = "✅";
    labelColor = "text-green-400";
    iconBg = "bg-green-500/10";
    iconBorder = "border-green-500/20";
  } else if (author.toLowerCase().includes("image") || author.toLowerCase().includes("video")) {
    icon = "🎨";
    labelColor = "text-pink-400";
    iconBg = "bg-pink-500/10";
    iconBorder = "border-pink-500/20";
  } else if (isSubagent) {
    icon = "⚙️";
    labelColor = "text-emerald-400";
    iconBg = "bg-emerald-500/10";
    iconBorder = "border-emerald-500/20";
  }

  return (
    <div className="flex flex-col gap-4 mb-8">
      {/* Historical Thinking Block */}
      {message.thinking && <ThinkingBubble content={message.thinking} />}

      {contentSegments.map((segment: string, i: number) => (
        <div key={i} className="flex justify-start group">
          <div className="flex gap-3 max-w-[90%]">
            {/* Agent Icon per bubble */}
            <div className={`shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-lg border ${iconBg} ${iconBorder}`}>
              {icon}
            </div>
            <div className="flex flex-col flex-1">
              {/* Top Header: Author on left, Delta on right */}
              <div className="flex items-center justify-between mb-1">
                <div className={`text-[10px] uppercase tracking-wider font-medium ${labelColor}`}>
                  {author}
                </div>
                <div className="text-[9px] text-muted-foreground opacity-50">
                  {formattedDelta}
                </div>
              </div>
              <div className="bg-card border border-border/50 shadow-md rounded-xl p-4 text-sm leading-relaxed">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={markdownComponents}
                  className="prose prose-sm dark:prose-invert max-w-none"
                >
                  {segment}
                </ReactMarkdown>
              </div>
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

function ChatInterface() {
  const messages = useAgentStore((s) => s.messages);
  const toolCalls = useAgentStore((s) => s.toolCalls);
  const logs = useAgentStore((s) => s.logs);
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
            {/* Streaming Thinking Context */}
            {currentThinking && <ThinkingBubble content={currentThinking} />}

            {currentStreamingMessage && (
              <div className="flex justify-start mb-4">
                <div className="flex gap-3 max-w-[90%]">
                  {(() => {
                    const author = currentAuthor || "Primary Agent";
                    let icon = "🤖";
                    let iconBg = "bg-blue-500/10";
                    let iconBorder = "border-blue-500/20";
                    let labelColor = "text-blue-400";

                    if (author.toLowerCase().includes("research")) {
                      icon = "🔍";
                      iconBg = "bg-purple-500/10";
                      iconBorder = "border-purple-500/20";
                      labelColor = "text-purple-400";
                    } else if (author.toLowerCase().includes("report") || author.toLowerCase().includes("writer")) {
                      icon = "📝";
                      iconBg = "bg-orange-500/10";
                      iconBorder = "border-orange-500/20";
                      labelColor = "text-orange-400";
                    } else if (author.toLowerCase().includes("plan") || author.toLowerCase().includes("orchestra")) {
                      icon = "🗺️";
                      iconBg = "bg-cyan-500/10";
                      iconBorder = "border-cyan-500/20";
                      labelColor = "text-cyan-400";
                    } else if (author.toLowerCase().includes("verify") || author.toLowerCase().includes("test")) {
                      icon = "✅";
                      iconBg = "bg-green-500/10";
                      iconBorder = "border-green-500/20";
                      labelColor = "text-green-400";
                    } else if (author.toLowerCase().includes("image") || author.toLowerCase().includes("video")) {
                      icon = "🎨";
                      iconBg = "bg-pink-500/10";
                      iconBorder = "border-pink-500/20";
                      labelColor = "text-pink-400";
                    } else if (author.toLowerCase().includes("subagent")) {
                      icon = "⚙️";
                      iconBg = "bg-emerald-500/10";
                      iconBorder = "border-emerald-500/20";
                      labelColor = "text-emerald-400";
                    }

                    return (
                      <>
                        <div className={`shrink-0 w-8 h-8 rounded-full flex items-center justify-center text-lg border ${iconBg} ${iconBorder}`}>
                          {icon}
                        </div>
                        <div className="flex flex-col">
                          <div className={`text-[10px] uppercase tracking-wider font-medium mb-1 ${labelColor}`}>
                            {author}
                          </div>
                          <div className="bg-card border border-border/50 shadow-md rounded-xl p-4 text-sm leading-relaxed">
                            <div className="whitespace-pre-wrap">
                              {currentStreamingMessage}
                              <span className="inline-block w-2 h-4 bg-primary animate-pulse ml-1" />
                            </div>
                          </div>
                        </div>
                      </>
                    );
                  })()}
                </div>
              </div>
            )}
            {/* Active Process Indicator */}
            {connectionStatus === "processing" && !currentStreamingMessage && (
              <div className="flex justify-start mb-4">
                <div className="flex items-center gap-2 text-muted-foreground bg-secondary/10 px-4 py-2 rounded-xl text-xs animate-pulse">
                  {(() => {
                    // Check for running tools
                    const runningTool = toolCalls.find((tc: any) => tc.status === 'running');
                    if (runningTool) {
                      return (
                        <>
                          <span className="text-lg">⚙️</span>
                          <span>Executing: <span className="font-mono text-primary">{runningTool.name}</span>...</span>
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
                          <span className="text-lg">⚡</span>
                          <span className="truncate max-w-[500px]">{recentLog.prefix}: {recentLog.message}</span>
                        </>
                      );
                    }

                    // Default fallback
                    return (
                      <>
                        <span className="text-lg">🧠</span>
                        <span>Thinking...</span>
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

      {/* Input */}
      <div className="p-4 border-t border-border/50">
        <div className="flex gap-2">
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
            placeholder={connectionStatus === "processing" ? "Type to redirect (Enter to stop & send)..." : "Enter your query..."}
            disabled={connectionStatus === "disconnected" || connectionStatus === "connecting"}
            className="flex-1 bg-background/50 border border-border rounded-lg px-4 py-2 focus:outline-none focus:border-primary disabled:opacity-50"
          />
          {connectionStatus === "processing" ? (
            <button
              onClick={() => ws.sendCancel()}
              className="bg-red-500 hover:bg-red-600 text-white px-4 py-2 rounded-lg transition-colors flex items-center gap-2"
              title="Stop the current agent run"
            >
              ⏹ Stop
            </button>
          ) : (
            <button
              onClick={() => handleSend()}
              disabled={connectionStatus !== "connected" || isSending || !input.trim()}
              className="bg-primary hover:bg-primary/90 disabled:bg-primary/30 text-primary-foreground px-4 py-2 rounded-lg transition-colors"
            >
              {ICONS.send}
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
                onClick={() => setViewingFile({ name: file.name, path: file.path, type: 'file' })}
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
  const [rightWidth, setRightWidth] = useState(600); // Wider default (600px) as requested
  const [activityLogWidth, setActivityLogWidth] = useState(40); // % of center area

  // Resizing Logic
  const startResizing = (direction: 'left' | 'right' | 'center') => (mouseDownEvent: React.MouseEvent) => {
    mouseDownEvent.preventDefault();
    const startX = mouseDownEvent.clientX;

    if (direction === 'center') {
      // For center divider, we need to calculate based on the center panel's total width
      const centerPanel = (mouseDownEvent.target as HTMLElement).closest('main');
      if (!centerPanel) return;

      const onMouseMove = (mouseMoveEvent: MouseEvent) => {
        const rect = centerPanel.getBoundingClientRect();
        const relativeX = mouseMoveEvent.clientX - rect.left;
        const percentage = Math.max(20, Math.min(80, (relativeX / rect.width) * 100));
        setActivityLogWidth(percentage);
      };

      const onMouseUp = () => {
        document.body.style.cursor = 'default';
        document.removeEventListener("mousemove", onMouseMove);
        document.removeEventListener("mouseup", onMouseUp);
      };

      document.body.style.cursor = 'col-resize';
      document.addEventListener("mousemove", onMouseMove);
      document.addEventListener("mouseup", onMouseUp);
    } else {
      // Original sidebar resizing logic
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
    }
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
          <div className="flex-1 flex flex-col min-h-0">
            <FileExplorer />
            <div className="border-t border-border/50 pt-2 flex-1 flex flex-col min-h-0">
              <WorkProductViewer />
            </div>
            <div className="border-t border-border/50 pt-2 h-1/3 flex flex-col min-h-0">
              <TaskPanel />
            </div>
          </div>
          {/* Resizer */}
          <div
            className="absolute top-0 right-0 w-1 h-full cursor-col-resize hover:bg-primary/50 transition-colors z-20"
            onMouseDown={startResizing('left')}
          />
        </aside>

        {/* Center - Split: Activity Log (left) + Chat Interface (right) OR File Viewer */}
        <main className="flex-1 border-r border-border/50 min-w-0 bg-background/50 flex relative">
          {viewingFile ? (
            <FileViewer />
          ) : (
            <>
              {/* Activity Log - Left side (resizable) */}
              <div
                className="min-h-0 border-r border-border/50 bg-background/30 relative"
                style={{ width: `${activityLogWidth}%` }}
              >
                <div className="h-full flex flex-col">
                  <div className="p-2 border-b border-border/50 bg-secondary/10">
                    <h2 className="text-xs font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-2">
                      {ICONS.activity} Activity Log
                    </h2>
                  </div>
                  <div className="flex-1 overflow-hidden">
                    <CombinedActivityLog />
                  </div>
                </div>

                {/* Draggable Divider */}
                <div
                  className="absolute top-0 right-0 w-1 h-full cursor-col-resize hover:bg-primary/50 transition-colors z-20"
                  onMouseDown={startResizing('center')}
                />
              </div>

              {/* Chat Interface - Right side (fills remaining space) */}
              <div
                className="min-h-0 flex-1"
                style={{ width: `${100 - activityLogWidth}%` }}
              >
                <ChatInterface />
              </div>
            </>
          )}
        </main>

        {/* Right Sidebar - Ops Panel */}
        <aside
          className="shrink-0 flex flex-col h-full overflow-hidden relative bg-background/30 backdrop-blur-sm"
          style={{ width: rightWidth }}
        >
          {/* Resizer */}
          <div
            className="absolute top-0 left-0 w-1 h-full cursor-col-resize hover:bg-primary/50 transition-colors z-20"
            onMouseDown={startResizing('right')}
          />

          <div className="flex flex-col h-full overflow-y-auto scrollbar-thin">
            <OpsPanel />
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
    </div>
  );
}
