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
import { OpsProvider } from "@/components/OpsDropdowns";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { LinkifiedText, PathLink, linkify } from "@/components/LinkifiedText";
import { formatTimeTz } from "@/lib/timezone";

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
  maximize: "⛶",
  minimize: "❐",
};

type HydratedChatMessage = {
  role: "user" | "assistant";
  content: string;
};

type HydratedActivityLog = {
  message: string;
  level: string;
  prefix: string;
  event_kind?: string;
  metadata?: Record<string, unknown>;
};

type VpMetricsMission = {
  mission_id?: string;
  status?: string;
  objective?: string;
  updated_at?: string;
};

type VpMetricsEvent = {
  event_id?: string;
  mission_id?: string;
  event_type?: string;
  created_at?: string;
  payload?: Record<string, unknown> | null;
};

type VpMetricsPayload = {
  vp_id?: string;
  session?: {
    status?: string;
    last_heartbeat_at?: string;
    lease_expires_at?: string;
    lease_owner?: string;
    last_error?: string;
  };
  mission_counts?: Record<string, number>;
  event_counts?: Record<string, number>;
  recent_missions?: VpMetricsMission[];
  recent_events?: VpMetricsEvent[];
  recent_session_events?: VpMetricsEvent[];
};

type VpHydrationSnapshot = {
  vpId: string;
  messages: HydratedChatMessage[];
  logs: HydratedActivityLog[];
  warning?: string;
};

const RUN_LOG_USER_LINE = /^\[\d{2}:\d{2}:\d{2}\]\s+👤\s+USER:\s*(.+)$/;
const RUN_LOG_ASSISTANT_LINE = /^\[\d{2}:\d{2}:\d{2}\]\s+🤖\s+ASSISTANT:\s*(.+)$/;

const RUN_LOG_LEVEL_LINE = /^\[\d{2}:\d{2}:\d{2}\]\s+([A-Z]+)\s+([\s\S]+)$/;

function extractHistoryFromRunLog(raw: string): { messages: HydratedChatMessage[], logs: HydratedActivityLog[] } {
  const messages: HydratedChatMessage[] = [];
  const logs: HydratedActivityLog[] = [];
  const lines = raw.split(/\r?\n/);
  for (const line of lines) {
    const userMatch = line.match(RUN_LOG_USER_LINE);
    if (userMatch?.[1]) {
      const content = userMatch[1].trim();
      if (content) messages.push({ role: "user", content });
      continue;
    }

    const assistantMatch = line.match(RUN_LOG_ASSISTANT_LINE);
    if (assistantMatch?.[1]) {
      const content = assistantMatch[1].trim();
      if (content) messages.push({ role: "assistant", content });
      continue;
    }

    const levelMatch = line.match(RUN_LOG_LEVEL_LINE);
    if (levelMatch?.[1] && levelMatch?.[2]) {
      const level = levelMatch[1];
      const message = levelMatch[2].trim();
      if (message) {
        logs.push({
          message,
          level,
          prefix: "rehydrated",
        });
      }
    }
  }
  return { messages: messages.slice(-80), logs: logs.slice(-200) };
}

function vpIdFromObserverSession(sessionId: string): string {
  const sid = String(sessionId || "").trim();
  if (!sid) return "";
  if (/^vp\./i.test(sid)) {
    return sid.replace(/\.external$/i, "");
  }
  if (!/^vp_/i.test(sid)) {
    return "";
  }
  const normalized = sid
    .replace(/^vp_/i, "vp.")
    .replace(/_external$/i, "")
    .replace(/_/g, ".");
  return normalized;
}

function vpEventLevel(eventType: string): string {
  const normalized = String(eventType || "").trim().toLowerCase();
  if (normalized.endsWith(".failed")) return "ERROR";
  if (normalized.endsWith(".cancelled") || normalized.endsWith(".cancel_requested")) return "WARN";
  return "INFO";
}

function workspaceRelativePathFromAbsolute(pathValue: string): string {
  const normalized = String(pathValue || "").replace(/\\/g, "/");
  if (!normalized) return "";
  const marker = "/AGENT_RUN_WORKSPACES/";
  if (normalized.startsWith("AGENT_RUN_WORKSPACES/")) {
    return normalized.slice("AGENT_RUN_WORKSPACES/".length);
  }
  const idx = normalized.indexOf(marker);
  if (idx >= 0) {
    return normalized.slice(idx + marker.length);
  }
  return "";
}

function parseIsoMillis(value: string): number {
  const parsed = Date.parse(String(value || ""));
  return Number.isFinite(parsed) ? parsed : 0;
}

async function fetchVpHydrationSnapshot(sessionId: string): Promise<VpHydrationSnapshot | null> {
  const vpId = vpIdFromObserverSession(sessionId);
  if (!vpId) return null;

  const query = `vp_id=${encodeURIComponent(vpId)}&mission_limit=30&event_limit=80`;
  const candidates = [
    `${API_BASE}/api/v1/ops/metrics/vp?${query}`,
    `/api/dashboard/gateway/api/v1/ops/metrics/vp?${query}`,
  ];
  let response: Response | null = null;
  const seen = new Set<string>();
  for (const candidate of candidates) {
    if (!candidate || seen.has(candidate)) continue;
    seen.add(candidate);
    try {
      const current = await fetch(candidate);
      if (current.ok) {
        response = current;
        break;
      }
    } catch {
      continue;
    }
  }
  if (!response) return null;

  const payload = (await response.json()) as VpMetricsPayload;
  const recentMissions = Array.isArray(payload.recent_missions) ? payload.recent_missions : [];
  const recentEvents = Array.isArray(payload.recent_events) ? payload.recent_events : [];
  const recentSessionEvents = Array.isArray(payload.recent_session_events) ? payload.recent_session_events : [];

  const messages: HydratedChatMessage[] = recentMissions
    .slice()
    .sort((a, b) => String(a.updated_at || "").localeCompare(String(b.updated_at || "")))
    .map((mission) => {
      const missionId = String(mission.mission_id || "").trim() || "unknown_mission";
      const status = String(mission.status || "unknown").trim();
      const objective = String(mission.objective || "").trim();
      return {
        role: "assistant",
        content: `[${status.toUpperCase()}] ${missionId}${objective ? `\nObjective: ${objective}` : ""}`,
      };
    });

  const eventRows = [...recentEvents, ...recentSessionEvents];
  const logs: HydratedActivityLog[] = eventRows
    .slice()
    .sort((a, b) => String(a.created_at || "").localeCompare(String(b.created_at || "")))
    .map((eventRow) => {
      const eventType = String(eventRow.event_type || "unknown").trim();
      const missionId = String(eventRow.mission_id || "").trim();
      const eventPayload =
        eventRow.payload && typeof eventRow.payload === "object" ? eventRow.payload : {};
      const resultRef = String(eventPayload.result_ref || "").trim();
      const sourceSession = String(eventPayload.source_session_id || "").trim();
      const objective = String(eventPayload.objective || "").trim();
      return {
        message: [
          `VP lifecycle event ${eventType}`,
          missionId ? `mission=${missionId}` : "",
          resultRef ? `result_ref=${resultRef}` : "",
          sourceSession ? `source_session=${sourceSession}` : "",
          objective ? `objective=${objective}` : "",
        ]
          .filter(Boolean)
          .join(" | "),
        level: vpEventLevel(eventType),
        prefix: "VP",
        event_kind: "vp_mission_event",
        metadata: {
          event_id: eventRow.event_id || undefined,
          mission_id: missionId || undefined,
          event_type: eventType || undefined,
          payload: eventPayload,
        },
      };
    });
  const missionCounts = payload.mission_counts && typeof payload.mission_counts === "object"
    ? payload.mission_counts
    : {};
  const eventCounts = payload.event_counts && typeof payload.event_counts === "object"
    ? payload.event_counts
    : {};
  const queuedCount = Number(missionCounts.queued || 0);
  const claimedCount = Number(eventCounts["vp.mission.claimed"] || 0);
  const startedCount = Number(eventCounts["vp.mission.started"] || 0);
  const heartbeatAt = parseIsoMillis(String(payload.session?.last_heartbeat_at || ""));
  const heartbeatAgeMs = heartbeatAt > 0 ? Date.now() - heartbeatAt : Number.POSITIVE_INFINITY;
  const heartbeatStale = heartbeatAgeMs > 3 * 60 * 1000;
  let warning: string | undefined;
  if (queuedCount > 0 && claimedCount === 0 && startedCount === 0 && heartbeatStale) {
    const status = String(payload.session?.status || "unknown");
    warning = `VP worker appears inactive for ${vpId}: queued=${queuedCount}, claimed=0, started=0, session_status=${status}.`;
  }

  return { vpId, messages, logs, warning };
}


// =============================================================================
// Components
// =============================================================================

function FileViewer() {
  const viewingFile = useAgentStore((s) => s.viewingFile);
  const setViewingFile = useAgentStore((s) => s.setViewingFile);
  const currentSession = useAgentStore((s) => s.currentSession);
  const [isMaximized, setIsMaximized] = useState(false);

  const isHtml = viewingFile?.name.endsWith(".html") ?? false;
  const isPdf = viewingFile?.name.endsWith(".pdf") ?? false;
  const isImage = viewingFile?.name.match(/\.(png|jpg|jpeg|gif|webp)$/i) ?? false;

  const encodePath = (p: string) => p.split("/").map(encodeURIComponent).join("/");

  const fileUrl = viewingFile
    ? viewingFile.type === "artifact"
      ? `${API_BASE}/api/artifacts/files/${encodePath(viewingFile.path)}`
      : viewingFile.type === "vps_workspace"
        ? `${API_BASE}/api/vps/file?scope=workspaces&path=${encodeURIComponent(viewingFile.path)}`
        : viewingFile.type === "vps_artifact"
          ? `${API_BASE}/api/vps/file?scope=artifacts&path=${encodeURIComponent(viewingFile.path)}`
          : currentSession?.session_id
            ? `${API_BASE}/api/files/${currentSession.session_id}/${encodePath(viewingFile.path)}`
            : ""
    : "";

  useEffect(() => {
    if (!viewingFile || !fileUrl || isHtml || isPdf || isImage || viewingFile.content) return;
    const controller = new AbortController();
    fetch(fileUrl, { signal: controller.signal })
      .then(res => res.text())
      .then(text => {
        if (viewingFile.name.endsWith(".json")) {
          try {
            const obj = JSON.parse(text);
            text = JSON.stringify(obj, null, 2);
          } catch (e) { }
        }
        const current = useAgentStore.getState().viewingFile;
        if (!current || current.path !== viewingFile.path) return;
        useAgentStore.getState().setViewingFile({ ...viewingFile, content: text });
      })
      .catch(err => {
        if (err?.name === "AbortError") return;
        console.error("Failed to fetch file content:", err);
      });
    return () => controller.abort();
  }, [viewingFile, fileUrl, isHtml, isPdf, isImage]);

  if (!viewingFile) return null;

  return (
    <div className={`flex flex-col bg-slate-950 animate-in fade-in transition-all duration-300 ${isMaximized ? "fixed inset-0 z-[60] h-screen w-screen" : "h-full w-full relative"
      }`}>
      <div className="h-10 border-b border-slate-800 flex items-center justify-between px-4 bg-slate-900/60 shrink-0">
        <div className="flex items-center gap-2 truncate pr-4">
          <span className="text-lg shrink-0">{ICONS.file}</span>
          <span className="font-semibold text-sm truncate">{viewingFile.name}</span>
          <span className="text-xs text-muted-foreground ml-2 opacity-50 truncate hidden sm:inline">{viewingFile.path}</span>
        </div>
        <div className="flex items-center gap-1 shrink-0">
          <button
            onClick={() => setIsMaximized(!isMaximized)}
            className="p-1 px-2 hover:bg-cyan-500/20 rounded text-cyan-400 border border-transparent hover:border-cyan-500/30 transition-all text-sm"
            title={isMaximized ? "Restore" : "Full Screen"}
          >
            {isMaximized ? ICONS.minimize : ICONS.maximize}
          </button>
          <button
            onClick={() => window.open(fileUrl, '_blank')}
            className="p-1 hover:bg-black/10 rounded text-muted-foreground hover:text-foreground transition-colors"
            title="Download/Open External"
          >
            {ICONS.download}
          </button>
          <button
            onClick={() => {
              setIsMaximized(false);
              setViewingFile(null);
            }}
            className="p-1 hover:bg-red-500/10 rounded text-muted-foreground hover:text-red-500 transition-colors"
            title="Close Preview"
          >
            {ICONS.close}
          </button>
        </div>
      </div>
      <div className={`flex-1 overflow-hidden relative ${isImage ? "bg-slate-950/40" : "bg-white"}`}>
        {isImage ? (
          <div className="w-full h-full flex items-center justify-center p-4">
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={fileUrl}
              alt={viewingFile.name}
              className="max-w-full max-h-full object-contain shadow-2xl"
            />
          </div>
        ) : (isHtml || isPdf) ? (
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

const API_BASE = "";

function FileExplorer() {
  const currentSession = useAgentStore((s) => s.currentSession);
  const setViewingFile = useAgentStore((s) => s.setViewingFile);
  const [mode, setMode] = useState<"session" | "artifacts" | "vps_workspaces" | "vps_artifacts">("session");
  const [path, setPath] = useState("");
  const [files, setFiles] = useState<any[]>([]);
  const [loading, setLoading] = useState(false);
  const [isCollapsed, setIsCollapsed] = useState(false);
  const [syncingVps, setSyncingVps] = useState(false);

  useEffect(() => {
    const sessionId = currentSession?.session_id;
    if (mode === "session" && !sessionId) return;

    // eslint-disable-next-line react-hooks/set-state-in-effect
    setLoading(true);
    const url = mode === "artifacts"
      ? `${API_BASE}/api/artifacts?path=${encodeURIComponent(path)}`
      : mode === "vps_workspaces"
        ? `${API_BASE}/api/vps/files?scope=workspaces&path=${encodeURIComponent(path)}`
        : mode === "vps_artifacts"
          ? `${API_BASE}/api/vps/files?scope=artifacts&path=${encodeURIComponent(path)}`
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

  const handleSyncVps = () => {
    if (syncingVps) return;
    setSyncingVps(true);
    fetch(`${API_BASE}/api/vps/sync/now`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({}),
    })
      .then(async (res) => {
        if (!res.ok) {
          const data = await res.json().catch(() => ({}));
          throw new Error(data?.detail || `Sync failed (${res.status})`);
        }
      })
      .then(() => {
        const scope = mode === "vps_artifacts" ? "artifacts" : "workspaces";
        return fetch(`${API_BASE}/api/vps/files?scope=${scope}&path=${encodeURIComponent(path)}`);
      })
      .then((res) => res.json())
      .then((data) => {
        const sortedFiles = (data.files || []).sort((a: any, b: any) => {
          if (a.is_dir === b.is_dir) return a.name.localeCompare(b.name);
          return a.is_dir ? -1 : 1;
        });
        setFiles(sortedFiles);
      })
      .catch((err) => console.error("Failed to sync VPS mirror:", err))
      .finally(() => setSyncingVps(false));
  };

  const handleNavigate = (itemName: string, isDir: boolean) => {
    if (!isDir) {
      // Open file preview
      const fullPath = path ? `${path}/${itemName}` : itemName;
      const fileType =
        mode === "artifacts"
          ? "artifact"
          : mode === "vps_workspaces"
            ? "vps_workspace"
            : mode === "vps_artifacts"
              ? "vps_artifact"
              : "file";
      setViewingFile({ name: itemName, path: fullPath, type: fileType });
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
            {mode === "artifacts"
              ? (path ? `Artifacts/.../${path.split("/").pop()}` : "Artifacts")
              : mode === "vps_workspaces"
                ? (path ? `VPS WS/.../${path.split("/").pop()}` : "VPS Workspaces")
                : mode === "vps_artifacts"
                  ? (path ? `VPS Artifacts/.../${path.split("/").pop()}` : "VPS Artifacts")
                  : (path ? `.../${path.split("/").pop()}` : "Files")}
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
            <button
              type="button"
              onClick={() => { setMode("vps_workspaces"); setPath(""); }}
              className={`text-[9px] px-2 py-1 rounded border font-medium transition-all ${mode === "vps_workspaces" ? "bg-primary/20 text-primary border-primary/40" : "bg-card/40 text-muted-foreground/70 border-border/40 hover:bg-card/60"}`}
              title="Browse mirrored VPS workspaces"
            >
              VPS WS
            </button>
            <button
              type="button"
              onClick={() => { setMode("vps_artifacts"); setPath(""); }}
              className={`text-[9px] px-2 py-1 rounded border font-medium transition-all ${mode === "vps_artifacts" ? "bg-primary/20 text-primary border-primary/40" : "bg-card/40 text-muted-foreground/70 border-border/40 hover:bg-card/60"}`}
              title="Browse mirrored VPS artifacts"
            >
              VPS ART
            </button>
            {(mode === "vps_workspaces" || mode === "vps_artifacts") && (
              <button
                type="button"
                onClick={handleSyncVps}
                disabled={syncingVps}
                className="text-[9px] px-2 py-1 rounded border font-medium transition-all bg-emerald-600/15 text-emerald-300 border-emerald-700/40 hover:bg-emerald-600/25 disabled:opacity-50"
                title="Sync VPS mirror now"
              >
                {syncingVps ? "SYNC..." : "SYNC VPS"}
              </button>
            )}
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
      <div className="flex h-10 items-center gap-2 px-3 rounded-lg bg-slate-900/60 border border-slate-700/80">
        <div
          className={`w-2 h-2 rounded-full ${config.color} ${config.pulse ? "status-pulse" : ""
            }`}
        />
        <span className={`text-[11px] font-semibold uppercase tracking-[0.12em] ${config.textColor}`}>
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
    <div className="hidden md:flex h-10 items-center gap-3 mr-2 px-3 rounded-lg bg-slate-900/60 border border-slate-700/80 tactical-panel min-w-fit">
      <div className="flex items-center gap-2 text-[11px] tracking-[0.1em]">
        <span className="font-mono text-cyan-300 whitespace-nowrap" title={sessionId}>{sessionId}</span>
      </div>
      <div className="w-px h-4 bg-slate-700/70" />
      <div className="flex items-center gap-1.5 text-[11px] tracking-[0.1em]">
        <span className="text-slate-400 font-semibold">TOKENS</span>
        <span className="font-mono text-slate-200">{tokenUsage.total.toLocaleString()}</span>
      </div>
      <div className="w-px h-4 bg-slate-700/70" />
      <div className="flex items-center gap-1.5 text-[11px] tracking-[0.1em]">
        <span className="text-slate-400 font-semibold">TOOLS</span>
        <span className="font-mono text-slate-200">{toolCallCount}</span>
      </div>
      <div className="w-px h-4 bg-slate-700/70" />
      <div className="flex items-center gap-1.5 text-[11px] tracking-[0.1em]">
        <span className="text-slate-400 font-semibold">TIME</span>
        <span className="font-mono text-slate-200">{formatDuration(startTime ? duration : 0)}</span>
      </div>
      <div className="w-px h-4 bg-slate-700/70" />
      <div className="flex items-center gap-1.5 text-[11px] tracking-[0.1em]">
        <span className="text-slate-400 font-semibold">ITERS</span>
        <span className="font-mono text-slate-200">{iterationCount}</span>
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
  // Simone (default lane)
  return { icon: "🤖", labelColor: "text-blue-400", iconBg: "bg-blue-500/10", iconBorder: "border-blue-500/20", borderAccent: "border-l-blue-500/40" };
}

function displayAuthorName(author: string): string {
  const normalized = String(author || "").trim().toLowerCase();
  if (normalized === "primary agent") return "Simone";
  return author;
}

function ChatMessage({ message }: { message: any }) {
  const isUser = message.role === "user";
  const isSystem = message.role === "system";
  const formattedDelta = React.useMemo(() => {
    const delta = message.time_offset;
    if (delta !== undefined) {
      return delta > 0 ? `+${delta.toFixed(1)}s` : `0s`;
    }
    return formatTimeTz(message.timestamp, { placeholder: "--:--:--" });
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

  if (isSystem) {
    return (
      <div className="mb-5 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3">
        <div className="flex items-center justify-between gap-2 text-[10px] uppercase tracking-wider">
          <span className="font-semibold text-amber-300">System Notice</span>
          <span className="text-amber-200/70">{formattedDelta}</span>
        </div>
        <div className="mt-1 text-sm text-amber-100 leading-relaxed">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={markdownComponents}
            className="prose prose-sm dark:prose-invert max-w-none"
          >
            {message.content}
          </ReactMarkdown>
        </div>
      </div>
    );
  }

  // Assistant: single consolidated bubble per message
  const author = message.author || "Simone";
  const authorDisplay = displayAuthorName(author);
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
                {authorDisplay}
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
  const [historyHydrationNotice, setHistoryHydrationNotice] = useState<string | null>(null);
  const [hydrationError, setHydrationError] = useState<string | null>(null);
  const [requestedSessionIdFromUrl, setRequestedSessionIdFromUrl] = useState("");
  const connectionStatus = useAgentStore((s) => s.connectionStatus);
  const ws = getWebSocket();
  const inputRef = React.useRef<HTMLInputElement>(null);
  const handleSendRef = React.useRef<(textOverride?: string) => Promise<void>>(async () => { });
  const hydratedSessionIdsRef = React.useRef<Set<string>>(new Set());
  const lastSessionIdRef = React.useRef<string>("");
  const effectiveSessionId = (currentSession?.session_id || requestedSessionIdFromUrl || "").trim();
  const isVpObserverSession = /^vp_/i.test(effectiveSessionId);

  const focusInput = React.useCallback(() => {
    if (chatRole === "viewer") return;
    const el = inputRef.current;
    if (!el) return;
    el.focus();
    const len = el.value.length;
    el.setSelectionRange(len, len);
  }, [chatRole]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    setRequestedSessionIdFromUrl((params.get("session_id") || "").trim());
    const role = (params.get("role") || "").trim().toLowerCase();
    const nextRole = role === "viewer" ? "viewer" : "writer";
    setChatRole(nextRole);
    // Pre-fill input from ?message= query param (used by dashboard Quick Command)
    const prefill = (params.get("message") || "").trim();
    const shouldFocusInput = params.get("focus_input") === "1";

    if (prefill) {
      setInput(prefill);
    }

    if ((prefill || shouldFocusInput) && nextRole !== "viewer") {
      window.requestAnimationFrame(() => {
        focusInput();
      });
    }

    if (prefill || shouldFocusInput) {
      // Clean up one-shot URL flags so refresh doesn't repeat actions.
      const url = new URL(window.location.href);
      url.searchParams.delete("message");
      url.searchParams.delete("focus_input");
      window.history.replaceState({}, "", url.toString());
    }
  }, [focusInput]);

  useEffect(() => {
    if (!isVpObserverSession) return;
    setChatRole("viewer");
  }, [isVpObserverSession]);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const handleFocusInput = () => {
      window.requestAnimationFrame(() => {
        focusInput();
      });
    };
    window.addEventListener("ua:focus-input", handleFocusInput);
    return () => window.removeEventListener("ua:focus-input", handleFocusInput);
  }, [focusInput]);

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
  handleSendRef.current = handleSend;


  // Handle pending query after cancellation
  useEffect(() => {
    if (connectionStatus === "connected" && pendingQuery) {
      const query = pendingQuery;
      setPendingQuery(null);
      void handleSendRef.current(query);
    }
  }, [connectionStatus, pendingQuery]);

  const messagesEndRef = React.useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, currentStreamingMessage]);

  useEffect(() => {
    if (!effectiveSessionId) return;
    if (!lastSessionIdRef.current) {
      lastSessionIdRef.current = effectiveSessionId;
      return;
    }
    if (lastSessionIdRef.current === effectiveSessionId) return;

    const store = useAgentStore.getState();
    store.clearMessages();
    store.clearToolCalls();
    store.clearLogs();
    store.clearWorkProducts();
    store.setCurrentThinking("");
    store.finishStream();
    setHistoryHydrationNotice(null);
    setHydrationError(null);
    hydratedSessionIdsRef.current.clear();
    lastSessionIdRef.current = effectiveSessionId;
  }, [effectiveSessionId]);

  useEffect(() => {
    const sessionId = effectiveSessionId;
    if (!sessionId) return;
    const vpWorkspaceRel = workspaceRelativePathFromAbsolute(currentSession?.workspace || "");
    const hydrationKey = isVpObserverSession
      ? `${sessionId}::${vpWorkspaceRel || "no_workspace"}`
      : sessionId;
    setHistoryHydrationNotice(null);
    if (hydratedSessionIdsRef.current.has(hydrationKey)) return;

    // If stream events have already populated the timeline, avoid duplicate hydration
    // for normal sessions. VP observer sessions still need VP event/log hydration.
    if (useAgentStore.getState().messages.length > 0 && !isVpObserverSession) {
      hydratedSessionIdsRef.current.add(hydrationKey);
      return;
    }

    let cancelled = false;
    setHydrationError(null);
    (async () => {
      try {
        const store = useAgentStore.getState();
        let hydratedMessageCount = 0;
        let hydratedLogCount = 0;

        const runLogUrl = isVpObserverSession && vpWorkspaceRel
          ? `${API_BASE}/api/vps/file?scope=workspaces&path=${encodeURIComponent(`${vpWorkspaceRel}/run.log`)}`
          : `${API_BASE}/api/files/${encodeURIComponent(sessionId)}/run.log`;
        const response = await fetch(runLogUrl);
        if (response.ok) {
          const raw = await response.text();
          const { messages, logs } = extractHistoryFromRunLog(raw);
          if (!cancelled && messages.length > 0 && store.messages.length === 0) {
            for (const msg of messages) {
              store.addMessage({
                role: msg.role,
                content: msg.content,
                time_offset: 0,
                is_complete: true,
              });
            }
            hydratedMessageCount += messages.length;
          }
          if (!cancelled && logs.length > 0 && store.logs.length === 0) {
            for (const log of logs) {
              store.addLog(log);
            }
            hydratedLogCount += logs.length;
          }
        } else if (response.status !== 404) {
          // Non-404 failures indicate a connectivity/proxy issue
          if (!cancelled) {
            setHydrationError(`Failed to load session history (HTTP ${response.status}). The gateway API may not be reachable.`);
          }
        }

        if (!cancelled && isVpObserverSession) {
          const vpSnapshot = await fetchVpHydrationSnapshot(sessionId);
          if (vpSnapshot) {
            if (store.messages.length === 0 && vpSnapshot.messages.length > 0) {
              for (const msg of vpSnapshot.messages.slice(-20)) {
                store.addMessage({
                  role: msg.role,
                  content: msg.content,
                  time_offset: 0,
                  is_complete: true,
                  author: "VP Observer",
                });
              }
              hydratedMessageCount += Math.min(vpSnapshot.messages.length, 20);
            }
            if (vpSnapshot.logs.length > 0) {
              for (const log of vpSnapshot.logs) {
                store.addLog(log);
              }
              hydratedLogCount += vpSnapshot.logs.length;
            }
            if (vpSnapshot.warning) {
              store.addMessage({
                role: "system",
                content: vpSnapshot.warning,
                time_offset: 0,
                is_complete: true,
                author: "VP Orchestrator",
              });
            }
          }
        }

        if (hydratedMessageCount > 0 || hydratedLogCount > 0) {
          const fragments = [
            hydratedMessageCount > 0 ? `${hydratedMessageCount} messages` : "",
            hydratedLogCount > 0 ? `${hydratedLogCount} activity events` : "",
          ].filter(Boolean);
          setHistoryHydrationNotice(`Hydrated ${fragments.join(" + ")} for ${sessionId}`);
        } else if (!cancelled && store.messages.length === 0) {
          setHistoryHydrationNotice(`No run history found for ${sessionId}`);
        }
      } catch (error) {
        console.warn("Failed to rehydrate session history", error);
        if (!cancelled) {
          setHydrationError(`Session history rehydration failed: ${(error as Error).message || "unknown error"}. Check gateway connectivity.`);
        }
      } finally {
        hydratedSessionIdsRef.current.add(hydrationKey);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [effectiveSessionId, isVpObserverSession, currentSession?.workspace]);

  return (
    <div className="flex flex-col h-full">
      {/* Messages */}
      <div className="flex-1 overflow-y-auto scrollbar-thin p-4">
        {historyHydrationNotice && (
          <div className="mb-3 rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-[10px] uppercase tracking-wider text-emerald-300">
            {historyHydrationNotice}
          </div>
        )}
        {hydrationError && (
          <div className="mb-3 rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-[10px] uppercase tracking-wider text-rose-300 flex items-center justify-between">
            <span>{hydrationError}</span>
            <button
              type="button"
              onClick={() => {
                setHydrationError(null);
                setHistoryHydrationNotice(null);
                hydratedSessionIdsRef.current.clear();
              }}
              className="ml-2 shrink-0 rounded border border-rose-500/40 px-2 py-0.5 text-[9px] text-rose-200 hover:bg-rose-500/20"
            >
              Retry
            </button>
          </div>
        )}
        {chatRole === "viewer" && (
          <div className="mb-3 rounded border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[10px] uppercase tracking-wider text-amber-300">
            {isVpObserverSession
              ? "VP Observer Mode: This lane is controlled by Simone. You can monitor only."
              : "Viewer mode: read-only attachment. Open as writer to send messages."}
          </div>
        )}
        {isVpObserverSession && (
          <div className="mb-3 rounded border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-[10px] uppercase tracking-wider text-rose-300">
            Commands are disabled in this session. Use the primary Simone chat to direct CODIE.
          </div>
        )}
        {sessionAttachMode === "tail" && effectiveSessionId && (
          <div className="mb-3 flex items-center justify-between rounded border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-[10px] uppercase tracking-wider text-emerald-300">
            <span>Attached Session (Tail): <span className="font-mono">{effectiveSessionId}</span></span>
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
              const author = currentAuthor || "Simone";
              const authorDisplay = displayAuthorName(author);
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
                        {authorDisplay}
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
            ref={inputRef}
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
              (chatRole === "viewer" || isVpObserverSession)
                ? "Viewer mode: input disabled"
                : connectionStatus === "processing"
                  ? "Type to redirect (Enter to stop & send)..."
                  : "Enter your neural query..."
            }
            disabled={chatRole === "viewer" || isVpObserverSession}
            className="flex-1 bg-slate-950/50 border border-slate-800 rounded-lg px-4 py-3 text-sm focus:outline-none focus:border-cyan-500/50 focus:shadow-glow-sm disabled:opacity-50 transition-all font-mono"
          />
          {connectionStatus === "processing" && chatRole !== "viewer" && !isVpObserverSession ? (
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
              disabled={chatRole === "viewer" || isVpObserverSession || isSending || !input.trim()}
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

void FileExplorer;
void WorkProductViewer;

export default function HomePage() {
  const connectionStatus = useAgentStore((s) => s.connectionStatus);
  const viewingFile = useAgentStore((s) => s.viewingFile); // Sub to viewing state
  const ws = getWebSocket();
  const [authSession, setAuthSession] = useState<DashboardAuthSession | null>(null);
  const [loadingAuth, setLoadingAuth] = useState(true);
  const [authError, setAuthError] = useState<string | null>(null);

  // Layout State
  const [activityCollapsed, setActivityCollapsed] = useState(false);
  const [chatCollapsed, setChatCollapsed] = useState(false);

  // Responsive State
  const [activeMobileTab, setActiveMobileTab] = useState<'chat' | 'activity' | 'dashboard'>('chat');

  const handleStartNewSession = () => {
    const store = useAgentStore.getState();
    store.reset();
    store.setCurrentSession(null);
    store.setSessionAttachMode("default");
    ws.startNewSession();
    setActiveMobileTab("chat");
    if (typeof window !== "undefined") {
      const url = new URL(window.location.href);
      url.searchParams.delete("session_id");
      url.searchParams.delete("attach");
      url.searchParams.set("focus_input", "1");
      window.history.replaceState({}, "", url.toString());
      window.dispatchEvent(new Event("ua:focus-input"));
    }
  };

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
    const requestedNewSession = (params?.get("new_session") || "").trim() === "1";

    if (requestedNewSession) {
      const store = useAgentStore.getState();
      store.reset();
      store.setCurrentSession(null);
      store.setSessionAttachMode("default");
      ws.startNewSession();
      if (typeof window !== "undefined") {
        const url = new URL(window.location.href);
        url.searchParams.delete("new_session");
        url.searchParams.delete("session_id");
        url.searchParams.delete("attach");
        window.history.replaceState({}, "", url.toString());
      }
    } else if (requestedSessionId) {
      const store = useAgentStore.getState();
      store.setSessionAttachMode(requestedAttach === "tail" ? "tail" : "default");
      if (!store.currentSession?.session_id) {
        store.setCurrentSession({
          session_id: requestedSessionId,
          workspace: "",
          user_id: "observer",
          session_url: undefined,
          logfire_enabled: false,
        });
      }
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
        <header className="h-14 border-b border-slate-800/80 bg-slate-900/85 backdrop-blur-md flex items-center px-3 shrink-0 z-20 relative gap-2">

          {/* Left: Logo & Brand */}
          <div className="flex items-center gap-2 shrink-0 h-full">
            {/* Logo Image */}
            <div className="relative h-full w-24 md:w-32 py-2">
              <Image
                src="/simon_logo_v2.png"
                alt="Simon"
                fill
                className="object-contain object-left"
                priority
              />
            </div>
            <a
              href="/storage?tab=explorer"
              className="inline-flex h-10 w-11 items-center justify-center rounded-lg border border-slate-700/80 bg-slate-900/60 text-slate-300 transition-colors hover:border-cyan-500/50 hover:bg-slate-800/60 hover:text-cyan-200"
              title="Open File Browser"
              aria-label="Open File Browser"
            >
              <span className="inline-flex items-center justify-center text-xl leading-none">
                🗂️
              </span>
            </a>
            <a
              href="/dashboard"
              className="hidden md:inline-flex h-10 w-11 items-center justify-center rounded-lg border border-slate-700/80 bg-slate-900/60 text-xl text-slate-300 transition hover:border-cyan-500/50 hover:bg-slate-800/60 hover:text-cyan-200"
              title="Dashboard Home"
              aria-label="Dashboard Home"
            >
              🏠
            </a>
          </div>

          {/* Center: Ops dropdown buttons - Hidden on Mobile */}
          <div className="hidden md:flex items-center gap-1.5 shrink-0">
            {([
              { key: "sessions", label: "Sessions", icon: "📋", href: "/dashboard/sessions", iconOnly: false },
              { key: "calendar", label: "Calendar", icon: "🗓️", href: "/dashboard/calendar", iconOnly: true },
              { key: "skills", label: "Skills", icon: "🧩", href: "/dashboard/skills", iconOnly: false },
              { key: "channels", label: "Channels", icon: "📡", href: "/dashboard/channels", iconOnly: false },
              { key: "approvals", label: "Approvals", icon: "✅", href: "/dashboard/approvals", iconOnly: false },
              { key: "events", label: "Events", icon: "⚡", href: "/dashboard/events", iconOnly: false },
              { key: "config", label: "Config", icon: "⚙️", href: "/dashboard/config", iconOnly: false },
              { key: "continuity", label: "Continuity", icon: "📈", href: "/dashboard/continuity", iconOnly: false },
            ] as const).map((item) => (
              <a
                key={item.key}
                href={item.href}
                target="_blank"
                rel="noopener noreferrer"
                className={item.iconOnly
                  ? "inline-flex h-10 w-11 items-center justify-center rounded-lg border border-slate-700/80 bg-slate-900/60 text-xl text-slate-300 transition hover:border-cyan-500/50 hover:bg-slate-800/60 hover:text-cyan-200"
                  : "flex h-10 items-center gap-1.5 px-2.5 rounded-lg border border-slate-700/80 bg-slate-900/60 text-[12px] font-semibold uppercase tracking-[0.12em] text-slate-300 transition hover:border-cyan-500/50 hover:bg-slate-800/60 hover:text-cyan-200"}
                title={`Open ${item.label} in a new tab`}
                aria-label={`Open ${item.label} in a new tab`}
              >
                {item.iconOnly ? (
                  <span className="leading-none">{item.icon}</span>
                ) : (
                  <>
                    <span className="text-[12px]">{item.icon}</span>
                    <span>{item.label}</span>
                    <span className="text-[9px] text-slate-500">↗</span>
                  </>
                )}
              </a>
            ))}
          </div>

          {/* Right: Metrics, Status */}
          <div className="ml-auto flex items-center gap-2 md:gap-2">
            <button
              type="button"
              onClick={handleStartNewSession}
              className="hidden md:inline-flex h-10 items-center rounded-lg border border-emerald-700/60 bg-emerald-600/15 px-3 text-[12px] font-semibold uppercase tracking-[0.14em] text-emerald-200 hover:border-emerald-400/60 hover:bg-emerald-600/25"
              title="Start a fresh chat session"
            >
              New Session
            </button>
            {/* Mobile/Tablet Menu Button could go here */}

            <HeaderMetrics />
            <ConnectionIndicator />
          </div>
        </header>

        {/* Main Content Area: Chat + Activity only */}
        <div className="flex-1 flex flex-col md:flex-row overflow-hidden relative pb-14 md:pb-0">
          {/* PANEL 1: CHAT / VIEWER */}
          <main
            className={`
              min-w-0 bg-background/30 relative flex-col border-r border-border/40
              ${activeMobileTab === 'chat' ? 'flex' : 'hidden md:flex'}
              ${chatCollapsed ? 'md:w-10 md:shrink-0 md:flex-none' : 'md:basis-1/2 md:flex-1'}
            `}
          >
            {chatCollapsed ? (
              <button
                type="button"
                onClick={() => setChatCollapsed(false)}
                className="hidden md:flex h-full w-10 items-center justify-center hover:bg-card/30 transition-colors border-r border-slate-700/50 bg-slate-900/40"
                title="Expand Chat Panel"
              >
                <span className="text-primary/60 text-xs [writing-mode:vertical-lr] rotate-180 tracking-widest uppercase font-bold whitespace-nowrap">{ICONS.chat} Chat ▶</span>
              </button>
            ) : (
              <>
                <button
                  type="button"
                  onClick={() => setChatCollapsed(true)}
                  className="hidden md:inline-flex absolute top-2 right-14 z-20 h-7 w-7 items-center justify-center rounded border border-slate-700 bg-slate-900/70 text-slate-300 hover:border-cyan-500/50 hover:text-cyan-200"
                  title="Collapse Chat Panel"
                >
                  ◀
                </button>
                {viewingFile ? (
                  <div className="flex-1 flex overflow-hidden">
                    <div className="flex-1 min-w-0 bg-background/30 flex relative">
                      <FileViewer />
                    </div>
                  </div>
                ) : (
                  <ChatInterface />
                )}
              </>
            )}
          </main>

          {/* PANEL 2: ACTIVITY LOG */}
          <div
            className={`
              bg-slate-900/20 relative transition-all duration-300 flex-col
              ${activeMobileTab === 'activity' ? 'flex w-full' : 'hidden md:flex'}
              ${activityCollapsed ? 'w-10 shrink-0' : chatCollapsed ? 'md:flex-1 md:basis-full' : 'md:basis-1/2 md:flex-1'}
            `}
          >
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
        </div>

        {/* MOBILE DASHBOARD MENU (Visible only on Mobile AND tab=='dashboard') */}
        {activeMobileTab === 'dashboard' && (
          <div className="flex-1 flex flex-col overflow-hidden bg-slate-950/95 pb-20 md:hidden">
            <div className="p-4 space-y-2 overflow-y-auto">
              <h2 className="text-sm font-bold uppercase tracking-widest text-muted-foreground border-b border-border/40 pb-2 mb-2">Dashboard Menu</h2>

              {[
                { key: "sessions", label: "Sessions", icon: "📋", href: "/dashboard/sessions" },
                { key: "skills", label: "Skills", icon: "🧩", href: "/dashboard/skills" },
                { key: "storage", label: "Storage", icon: "📁", href: "/storage" },
                { key: "calendar", label: "Calendar", icon: "🗓️", href: "/dashboard/calendar" },
                { key: "channels", label: "Channels", icon: "📡", href: "/dashboard/channels" },
                { key: "approvals", label: "Approvals", icon: "✅", href: "/dashboard/approvals" },
                { key: "events", label: "Events", icon: "⚡", href: "/dashboard/events" },
                { key: "config", label: "Config", icon: "⚙️", href: "/dashboard/config" },
                { key: "continuity", label: "Continuity", icon: "📈", href: "/dashboard/continuity" },
              ].map((item) => (
                <a
                  key={item.key}
                  href={item.href}
                  className="w-full text-left p-4 rounded-lg border border-border/40 bg-card/20 hover:bg-card/40 active:bg-card/60 transition-all flex items-center gap-3"
                >
                  <span className="text-xl">{item.icon}</span>
                  <span className="font-bold uppercase tracking-wider text-sm">{item.label}</span>
                  <span className="ml-auto opacity-50">›</span>
                </a>
              ))}

              <a
                href="/dashboard"
                className="block rounded-lg border border-border/50 bg-card/40 px-4 py-3 text-center text-sm uppercase tracking-widest text-muted-foreground hover:border-primary/40 hover:text-primary mt-4"
              >
                Go to Dashboard Shell
              </a>
            </div>
          </div>
        )}

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
