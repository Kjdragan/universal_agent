"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Activity, AlertTriangle, ArrowRight, BarChart3, Bell, Briefcase, CheckCircle, ClipboardList, Clock, FileText, Loader2, RefreshCw, Timer, Trash2, XCircle } from "lucide-react";
import { formatDistanceToNow, parseISO } from "date-fns";

const API_BASE = "/api/dashboard/gateway";
const REFRESH_INTERVAL = 30_000; // 30 seconds

// Types for API responses
type AgentQueueItem = {
  task_id: string;
  title: string;
  description?: string;
  project_key?: string;
  priority?: number;
  labels?: string[];
  status?: string;
  must_complete?: boolean;
  incident_key?: string | null;
  score?: number;
  created_at?: string;
  updated_at?: string;
  due_at?: string | null;
  source_kind?: string;
  stale_state?: string;
  links?: {
    workspace_name?: string | null;
    session_id?: string | null;
  } | null;
};

type AgentQueuePayload = {
  status: string;
  items: AgentQueueItem[];
  pagination: {
    total: number;
    offset: number;
    limit: number;
    count: number;
    has_more: boolean;
  };
};

type HealthPayload = {
  status: "healthy" | "unhealthy";
  timestamp: string;
  version?: string;
  db_status?: string;
  db_error?: string | null;
};

// Helper functions
function formatTs(ts?: string | null): string {
  if (!ts) return "";
  try {
    return formatDistanceToNow(parseISO(ts), { addSuffix: true });
  } catch {
    return ts;
  }
}

function priorityText(priority?: number): string {
  const p = Number(priority || 1);
  if (p >= 4) return "Urgent";
  if (p === 3) return "High";
  if (p === 2) return "Medium";
  return "Normal";
}

function priorityColor(priority?: number): string {
  const p = Number(priority || 1);
  if (p >= 4) return "text-red-400 bg-red-500/10";
  if (p === 3) return "text-accent bg-accent/10";
  if (p === 2) return "text-accent bg-accent/10";
  return "text-muted-foreground bg-muted-foreground/10";
}

function statusColor(status?: string): string {
  const s = (status || "").toLowerCase();
  if (s === "completed" || s === "done") return "text-primary bg-primary/10";
  if (s === "in_progress" || s === "running") return "text-primary bg-primary/10";
  if (s === "blocked" || s === "failed") return "text-red-400 bg-red-500/10";
  if (s === "pending" || s === "queued") return "text-accent bg-accent/10";
  return "text-muted-foreground bg-muted-foreground/10";
}

// Loading skeleton component
function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-card/50/50 ${className}`} />;
}

// Coordinated refresh context -- single timer drives all panels
type RefreshContextType = {
  refreshKey: number;
  lastRefresh: Date | null;
  isRefreshing: boolean;
};

const RefreshContext = createContext<RefreshContextType>({
  refreshKey: 0,
  lastRefresh: null,
  isRefreshing: false,
});

// Current Work Panel (Task Queue Overview)
function ActiveTasksPanel() {
  const [loading, setLoading] = useState(true);
  const { refreshKey } = useContext(RefreshContext);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<AgentQueuePayload | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [dismissingId, setDismissingId] = useState<string | null>(null);

  const dismissTask = useCallback(async (taskId: string) => {
    setDismissingId(taskId);
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/todolist/dismiss/${encodeURIComponent(taskId)}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(`Dismiss failed: ${res.status}`);
      // Remove from local state immediately for responsiveness
      setData((prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          items: prev.items.filter((item) => item.task_id !== taskId),
          pagination: { ...prev.pagination, total: Math.max(0, prev.pagination.total - 1) },
        };
      });
    } catch (e) {
      console.error("Failed to dismiss task:", e);
    } finally {
      setDismissingId(null);
    }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const statusParam = statusFilter === "all" ? "" : `&status=${statusFilter}`;
      const res = await fetch(`${API_BASE}/api/v1/dashboard/todolist/agent-queue?offset=0&limit=10${statusParam}`, {
        cache: "no-store",
      });
      if (!res.ok) {
        throw new Error(`Failed to load: ${res.status}`);
      }
      const json = await res.json();
      setData(json as AgentQueuePayload);
    } catch (err: any) {
      setError(err.message || "Failed to load current work");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void load();
    });
    return () => {
      cancelled = true;
    };
  }, [load, refreshKey]);

  if (loading) {
    return (
      <div className="min-w-0 rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
        <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-sm font-medium text-foreground/80">Current Work</h2>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded border border-border bg-card px-2 py-1 text-xs text-foreground/80"
            >
              <option value="all">All Status</option>
              <option value="pending">Pending</option>
              <option value="in_progress">In Progress</option>
              <option value="blocked">Blocked</option>
              <option value="completed">Completed</option>
            </select>
            <span className="text-xs text-muted-foreground">{data?.pagination?.total || 0} total</span>
          </div>
        </div>
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-3 w-1/2" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="min-w-0 rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-sm font-medium text-foreground/80">Current Work</h2>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded border border-border bg-card px-2 py-1 text-xs text-foreground/80"
            >
              <option value="all">All Status</option>
              <option value="pending">Pending</option>
              <option value="in_progress">In Progress</option>
              <option value="blocked">Blocked</option>
              <option value="completed">Completed</option>
            </select>
            <span className="text-xs text-muted-foreground">{data?.pagination?.total || 0} total</span>
          </div>
        </div>
        <div className="flex flex-col items-center justify-center py-6 text-center">
          <XCircle className="mb-2 h-8 w-8 text-red-400" />
          <p className="text-sm text-muted-foreground">{error}</p>
          <button
            onClick={load}
            className="mt-3 flex items-center gap-1 rounded bg-card/50 px-3 py-1.5 text-xs text-foreground/80 hover:bg-muted"
          >
            <RefreshCw className="h-3 w-3" />
            Retry
          </button>
        </div>
      </div>
    );
  }

  const items = data?.items || [];

  return (
    <div className="min-w-0 rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">Current Work</h2>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded border border-border bg-card px-2 py-1 text-xs text-foreground/80"
          >
            <option value="all">All Status</option>
            <option value="pending">Pending</option>
            <option value="in_progress">In Progress</option>
            <option value="blocked">Blocked</option>
            <option value="completed">Completed</option>
          </select>
          <span className="text-xs text-muted-foreground">{data?.pagination?.total || 0} total</span>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="flex flex-1 items-center justify-center py-8">
          <p className="text-sm text-muted-foreground">No current work</p>
        </div>
      ) : (
        <div className="max-h-80 space-y-2 overflow-y-auto">
          {items.map((item) => (
            <Link
              key={item.task_id}
              href={`/dashboard/todolist?mode=agent&focus=${item.task_id}`}
              className="group block min-w-0 overflow-hidden rounded-lg border border-border/50 bg-card/30 p-3 transition-colors hover:border-border hover:bg-card/50"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <p className="break-words text-sm font-medium text-foreground leading-snug">{item.title}</p>
                  {item.description && (
                    <p className="mt-0.5 break-words text-xs text-muted-foreground leading-snug line-clamp-2">{item.description}</p>
                  )}
                </div>
                <button
                  onClick={(e) => {
                    e.preventDefault();
                    e.stopPropagation();
                    void dismissTask(item.task_id);
                  }}
                  disabled={dismissingId === item.task_id}
                  title="Dismiss this work item"
                  className="opacity-0 group-hover:opacity-100 transition-opacity flex-shrink-0 rounded p-1 text-muted-foreground hover:text-red-400 hover:bg-red-500/10"
                >
                  {dismissingId === item.task_id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Trash2 className="h-3.5 w-3.5" />
                  )}
                </button>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {item.status && (
                  <span className={`rounded px-1.5 py-0.5 text-xs ${statusColor(item.status)}`}>
                    {item.status}
                  </span>
                )}
                <span className={`rounded px-1.5 py-0.5 text-xs ${priorityColor(item.priority)}`}>
                  {priorityText(item.priority)}
                </span>
                {item.project_key && (
                  <span className="rounded bg-card/50 px-1.5 py-0.5 text-xs text-muted-foreground">
                    {item.project_key}
                  </span>
                )}
                {(item.created_at || item.updated_at) && (
                  <span
                    className="text-xs text-muted-foreground/70"
                    title={`Created: ${item.created_at || "—"} · Updated: ${item.updated_at || "—"}`}
                  >
                    🕐 {formatTs(item.created_at || item.updated_at)}
                    {item.updated_at && item.created_at && item.updated_at !== item.created_at
                      ? ` · updated ${formatTs(item.updated_at)}`
                      : ""}
                  </span>
                )}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

// System Status Panel
function SystemStatusPanel() {
  const [loading, setLoading] = useState(true);
  const { refreshKey } = useContext(RefreshContext);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<HealthPayload | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/health`, { cache: "no-store" });
      if (!res.ok) {
        throw new Error(`Failed to load: ${res.status}`);
      }
      const json = await res.json();
      setData(json as HealthPayload);
    } catch (err: any) {
      setError(err.message || "Failed to load system status");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void load();
    });
    return () => {
      cancelled = true;
    };
  }, [load, refreshKey]);

  if (loading) {
    return (
      <div className="min-w-0 rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
        <div className="mb-4 flex items-center gap-2">
          <Activity className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">System Status</h2>
        </div>
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
        <div className="mb-4 flex items-center gap-2">
          <Activity className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">System Status</h2>
        </div>
        <div className="flex flex-col items-center justify-center py-6 text-center">
          <XCircle className="mb-2 h-8 w-8 text-red-400" />
          <p className="text-sm text-muted-foreground">{error}</p>
          <button
            onClick={load}
            className="mt-3 flex items-center gap-1 rounded bg-card/50 px-3 py-1.5 text-xs text-foreground/80 hover:bg-muted"
          >
            <RefreshCw className="h-3 w-3" />
            Retry
          </button>
        </div>
      </div>
    );
  }

  const isHealthy = data?.status === "healthy";

  return (
    <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
      <div className="mb-4 flex items-center gap-2">
        <Activity className="h-4 w-4 text-muted-foreground" />
        <h2 className="text-sm font-medium text-foreground/80">System Status</h2>
      </div>

      <div className="space-y-4">
        {/* Gateway Status */}
        <div className="flex items-center justify-between rounded-lg bg-card/30 p-3">
          <div className="flex items-center gap-3">
            {isHealthy ? (
              <CheckCircle className="h-6 w-6 text-primary" />
            ) : (
              <XCircle className="h-6 w-6 text-red-400" />
            )}
            <div>
              <p className="text-sm font-medium text-foreground">Gateway</p>
              <p className="text-xs text-muted-foreground">
                {isHealthy ? "All systems operational" : "System issues detected"}
              </p>
            </div>
          </div>
          <span
            className={`rounded px-2 py-1 text-xs font-medium ${
              isHealthy ? "bg-primary/10 text-primary" : "bg-red-500/10 text-red-400"
            }`}
          >
            {data?.status || "unknown"}
          </span>
        </div>

        {/* Database Status */}
        {data?.db_status && (
          <div className="flex items-center justify-between rounded-lg bg-card/30 p-3">
            <div className="flex items-center gap-3">
              {data.db_status === "connected" ? (
                <CheckCircle className="h-5 w-5 text-primary" />
              ) : (
                <XCircle className="h-5 w-5 text-red-400" />
              )}
              <div>
                <p className="text-sm text-foreground">Database</p>
                <p className="text-xs text-muted-foreground">
                  {data.db_error || data.db_status}
                </p>
              </div>
            </div>
            <span
              className={`rounded px-2 py-1 text-xs ${
                data.db_status === "connected"
                  ? "bg-primary/10 text-primary"
                  : "bg-red-500/10 text-red-400"
              }`}
            >
              {data.db_status}
            </span>
          </div>
        )}

        {/* Last Check */}
        {data?.timestamp && (
          <div className="text-center text-xs text-muted-foreground">
            Last checked: {formatTs(data.timestamp)}
          </div>
        )}
      </div>
    </div>
  );
}

type DashboardSituation = {
  id: string;
  kind?: string;
  title?: string;
  summary?: string;
  priority?: "high" | "medium" | "low" | string;
  status?: string;
  requires_action?: boolean;
  tags?: string[];
  created_at_utc?: string;
  updated_at_utc?: string;
  source_domain?: string;
  primary_href?: string;
  knowledge_block?: {
    source?: string;
    event_ids?: string[];
    task_ids?: string[];
    session_id?: string | null;
    recommended_action?: string;
    handoff_prompt?: string;
    evidence?: Record<string, unknown>;
  };
};

function situationPriorityBadge(priority?: string): { color: string; label: string; icon: ReactNode } {
  const p = (priority || "low").toLowerCase();
  if (p === "high") {
    return { color: "bg-red-500/10 text-red-300 border-red-500/25", label: "high", icon: <AlertTriangle className="h-3.5 w-3.5" /> };
  }
  if (p === "medium") {
    return { color: "bg-accent/10 text-accent border-accent/25", label: "medium", icon: <Clock className="h-3.5 w-3.5" /> };
  }
  return { color: "bg-primary/10 text-primary border-primary/25", label: "low", icon: <CheckCircle className="h-3.5 w-3.5" /> };
}

// Operator Brief Panel — curated situation cards backed by /api/v1/dashboard/situations.
function OperatorBriefPanel() {
  const [loading, setLoading] = useState(true);
  const { refreshKey } = useContext(RefreshContext);
  const [error, setError] = useState<string | null>(null);
  const [situations, setSituations] = useState<DashboardSituation[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/situations?limit=10`, {
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`Failed to load: ${res.status}`);
      const json = await res.json();
      const items: DashboardSituation[] = Array.isArray(json.situations) ? json.situations : [];
      setSituations(items);
    } catch (err: any) {
      setError(err.message || "Failed to load operator brief");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void load();
    });
    return () => {
      cancelled = true;
    };
  }, [load, refreshKey]);

  if (loading) {
    return (
      <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
        <div className="mb-4 flex items-center gap-2">
          <ClipboardList className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">Operator Brief</h2>
        </div>
        <div className="space-y-3">
          {[1, 2, 3].map((i) => (
            <div key={i} className="space-y-2">
              <Skeleton className="h-4 w-3/4" />
              <Skeleton className="h-3 w-1/2" />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
        <div className="mb-4 flex items-center gap-2">
          <ClipboardList className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">Operator Brief</h2>
        </div>
        <div className="flex flex-col items-center justify-center py-6 text-center">
          <XCircle className="mb-2 h-8 w-8 text-red-400" />
          <p className="text-sm text-muted-foreground">{error}</p>
          <button
            onClick={load}
            className="mt-3 flex items-center gap-1 rounded bg-card/50 px-3 py-1.5 text-xs text-foreground/80 hover:bg-muted"
          >
            <RefreshCw className="h-3 w-3" />
            Retry
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="min-w-0 rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <ClipboardList className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">Operator Brief</h2>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted-foreground">{situations.length} situation{situations.length === 1 ? "" : "s"}</span>
          <Link
            href="/dashboard/events"
            className="inline-flex items-center gap-1 rounded border border-border bg-card px-2 py-0.5 text-xs text-muted-foreground hover:bg-card/50 hover:text-foreground/80"
          >
            Event Log
            <ArrowRight className="h-3 w-3" />
          </Link>
        </div>
      </div>

      {situations.length === 0 ? (
        <div className="flex flex-1 items-center justify-center py-8">
          <p className="text-sm text-muted-foreground">No operator-relevant situations right now.</p>
        </div>
      ) : (
        <div className="max-h-[30rem] space-y-3 overflow-y-auto pr-1">
          {situations.map((situation) => {
            const badge = situationPriorityBadge(situation.priority);
            const kb = situation.knowledge_block || {};
            return (
              <div
                key={situation.id}
                className="min-w-0 overflow-hidden rounded-lg border border-border/50 bg-card/30 p-3"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="break-words text-sm font-medium text-foreground leading-snug">{situation.title || "Situation"}</p>
                    {situation.summary && (
                      <p className="mt-0.5 break-words text-xs text-muted-foreground leading-snug line-clamp-2">
                        {situation.summary}
                      </p>
                    )}
                  </div>
                  <span className={`inline-flex flex-shrink-0 items-center gap-1 rounded border px-1.5 py-0.5 text-xs ${badge.color}`}>
                    {badge.icon}
                    {badge.label}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {(situation.tags || []).slice(0, 6).map((tag) => (
                    <span key={tag} className="rounded bg-card/50 px-1.5 py-0.5 text-xs text-muted-foreground">
                      {tag}
                    </span>
                  ))}
                  {situation.status && (
                    <span className="text-xs text-muted-foreground">{situation.status}</span>
                  )}
                  <span className="text-xs text-muted-foreground">
                    {formatTs(situation.updated_at_utc || situation.created_at_utc)}
                  </span>
                </div>
                {kb.recommended_action && (
                  <p className="mt-2 text-xs text-foreground/75">
                    <span className="text-muted-foreground">Recommended:</span> {kb.recommended_action}
                  </p>
                )}
                <div className="mt-3 flex flex-wrap items-center gap-2">
                  {situation.primary_href && (
                    <Link
                      href={situation.primary_href}
                      className="inline-flex items-center gap-1 rounded border border-primary/30 bg-primary/10 px-2 py-1 text-[11px] text-primary/80 hover:bg-primary/20"
                    >
                      Open Source
                      <ArrowRight className="h-3 w-3" />
                    </Link>
                  )}
                  <details className="group">
                    <summary className="inline-flex cursor-pointer items-center gap-1 rounded border border-border bg-background/40 px-2 py-1 text-[11px] text-foreground/80 hover:bg-card/60">
                      <FileText className="h-3 w-3" />
                      Knowledge Block
                    </summary>
                    <div className="mt-2 max-h-48 overflow-y-auto rounded border border-border/60 bg-background/60 p-2 text-[11px] leading-relaxed text-muted-foreground">
                      {kb.task_ids && kb.task_ids.length > 0 && <p>Tasks: {kb.task_ids.join(", ")}</p>}
                      {kb.event_ids && kb.event_ids.length > 0 && <p>Events: {kb.event_ids.join(", ")}</p>}
                      {kb.session_id && <p>Session: {kb.session_id}</p>}
                      {kb.handoff_prompt && (
                        <pre className="mt-2 whitespace-pre-wrap font-mono text-[10px] text-foreground/70">{kb.handoff_prompt}</pre>
                      )}
                    </div>
                  </details>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

function OperatingPosturePanel() {
  const links = [
    { href: "/dashboard/events", label: "Event Log", detail: "Raw notifications, diagnostics, and source events", icon: Bell },
    { href: "/dashboard/todolist", label: "Task Hub", detail: "Durable missions, approvals, dispatch, and history", icon: Briefcase },
    { href: "/dashboard/csi", label: "CSI", detail: "Content and signal intelligence workbench", icon: BarChart3 },
  ];

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <SystemStatusPanel />
      <div className="min-w-0 rounded-none border border-white/10 bg-[#0b1326]/70 p-4 backdrop-blur-md">
        <div className="mb-3 flex items-center gap-2">
          <ArrowRight className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">Deep Dives</h2>
        </div>
        <div className="space-y-2">
          {links.map(({ href, label, detail, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className="block min-w-0 rounded-lg border border-border/50 bg-card/25 p-3 transition-colors hover:border-border hover:bg-card/45"
            >
              <div className="flex items-start gap-2">
                <Icon className="mt-0.5 h-4 w-4 flex-shrink-0 text-muted-foreground" />
                <div className="min-w-0">
                  <p className="text-sm font-medium text-foreground/85">{label}</p>
                  <p className="mt-0.5 break-words text-xs leading-snug text-muted-foreground">{detail}</p>
                </div>
              </div>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}


/**
 * Mission Control Dashboard
 *
 * Operator-awareness surface:
 * - Operator Brief: curated situations with knowledge blocks
 * - Current Work: recent durable Task Hub missions
 * - Operating Posture: compact health and navigation to deeper tools
 */
export default function MissionControlPage() {
  const router = useRouter();
  const [refreshKey, setRefreshKey] = useState(0);
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const triggerRefresh = useCallback(() => {
    setIsRefreshing(true);
    setRefreshKey((k) => k + 1);
    setLastRefresh(new Date());
    // Brief spin indicator -- clear after panels have had time to fire their loads
    setTimeout(() => setIsRefreshing(false), 1500);
  }, []);

  // Single coordinated interval -- drives all child panels via context
  useEffect(() => {
    queueMicrotask(triggerRefresh); // initial load
    intervalRef.current = setInterval(triggerRefresh, REFRESH_INTERVAL);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [triggerRefresh]);

  return (
    <RefreshContext.Provider value={{ refreshKey, lastRefresh, isRefreshing }}>
      <div className="flex h-full min-w-0 flex-col gap-6">
        {/* Page Header */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 ring-1 ring-primary/20">
              <Activity className="h-5 w-5 text-primary" />
            </div>
            <div>
              <h1 className="text-lg font-semibold text-foreground">Mission Control</h1>
              <p className="text-sm text-muted-foreground">Operator brief, current work, and system posture</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            {lastRefresh && (
              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <Timer className="h-3.5 w-3.5" />
                Updated {formatDistanceToNow(lastRefresh, { addSuffix: true })}
              </div>
            )}
          </div>
        </div>

        {/* Quick Actions Bar */}
        <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-3">
          <div className="flex flex-wrap gap-2">
            <button
              onClick={triggerRefresh}
              disabled={isRefreshing}
              className="flex items-center gap-2 rounded-lg bg-card px-3 py-2 text-sm text-foreground/80 transition-colors hover:bg-card/50 disabled:opacity-60"
            >
              <RefreshCw className={`h-4 w-4 ${isRefreshing ? "animate-spin" : ""}`} />
              Refresh All
            </button>
            <button
              onClick={() => router.push("/dashboard/events")}
              className="flex items-center gap-2 rounded-lg bg-card px-3 py-2 text-sm text-foreground/80 transition-colors hover:bg-card/50"
            >
              <Bell className="h-4 w-4" />
              Event Log
            </button>
            <button
              onClick={() => router.push("/dashboard/todolist")}
              className="flex items-center gap-2 rounded-lg bg-card px-3 py-2 text-sm text-foreground/80 transition-colors hover:bg-card/50"
            >
              <Briefcase className="h-4 w-4" />
              Task Hub
            </button>
          </div>
        </div>

        <div className="grid min-w-0 flex-1 gap-4 xl:grid-cols-[minmax(0,1.8fr)_minmax(280px,0.8fr)]">
          <div className="flex min-w-0 flex-col gap-4">
            <OperatorBriefPanel />
            <ActiveTasksPanel />
          </div>
          <OperatingPosturePanel />
        </div>
      </div>
    </RefreshContext.Provider>
  );
}
