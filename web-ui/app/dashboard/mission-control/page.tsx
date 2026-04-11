"use client";

import { CapacityGovernorPanel } from "@/components/dashboard/CapacityGovernorPanel";
import { PipelineStatsPanel } from "@/components/dashboard/PipelineStatsPanel";
import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Activity, BarChart3, Bell, Briefcase, CheckCircle, Clock, DollarSign, Download, Heart, Loader2, RefreshCw, Timer, TrendingUp, Cpu, XCircle, Zap } from "lucide-react";
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
  updated_at?: string;
  due_at?: string | null;
  source_kind?: string;
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

// Active Work Items Panel (Task Queue Overview)
function ActiveTasksPanel() {
  const [loading, setLoading] = useState(true);
  const { refreshKey } = useContext(RefreshContext);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<AgentQueuePayload | null>(null);
  const [statusFilter, setStatusFilter] = useState<string>("all");

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
      setError(err.message || "Failed to load active work items");
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
      <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-sm font-medium text-foreground/80">Active Work Items</h2>
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
      <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-muted-foreground" />
            <h2 className="text-sm font-medium text-foreground/80">Active Work Items</h2>
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
    <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">Active Work Items</h2>
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
          <p className="text-sm text-muted-foreground">No active work items</p>
        </div>
      ) : (
        <div className="max-h-80 space-y-2 overflow-y-auto">
          {items.map((item) => (
            <Link
              key={item.task_id}
              href={`/dashboard/todolist?mode=agent&focus=${item.task_id}`}
              className="block rounded-lg border border-border/50 bg-card/30 p-3 transition-colors hover:border-border hover:bg-card/50"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-foreground leading-snug">{item.title}</p>
                  {item.description && (
                    <p className="mt-0.5 text-xs text-muted-foreground leading-snug line-clamp-2">{item.description}</p>
                  )}
                </div>
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
      <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
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

const CLEARED_EVENTS_LS_KEY = "mc_cleared_events_before";

// Activity Event type for the /api/v1/dashboard/events endpoint
type ActivityEvent = {
  id: string;
  event_class?: string;
  source_domain?: string;
  kind?: string;
  title?: string;
  summary?: string;
  severity?: string;
  status?: string;
  requires_action?: boolean;
  created_at_utc?: string;
  updated_at_utc?: string;
  session_id?: string;
  metadata?: Record<string, unknown>;
};

function severityBadge(severity?: string): { color: string; label: string } {
  const s = (severity || "info").toLowerCase();
  if (s === "error" || s === "critical") return { color: "bg-red-500/10 text-red-400", label: s };
  if (s === "warning" || s === "warn") return { color: "bg-accent/10 text-accent", label: "warn" };
  if (s === "success") return { color: "bg-primary/10 text-primary", label: s };
  return { color: "bg-muted-foreground/10 text-muted-foreground", label: s };
}

function sourceDomainIcon(domain?: string): string {
  const d = (domain || "").toLowerCase();
  if (d === "csi") return "📊";
  if (d === "heartbeat") return "💓";
  if (d === "tutorial") return "🎬";
  if (d === "cron") return "⏰";
  if (d === "simone" || d === "agentmail") return "📧";
  if (d === "continuity") return "🔄";
  return "⚡";
}

// Recent Events Panel — wired to /api/v1/dashboard/events (real activity events)
function RecentEventsPanel() {
  const [loading, setLoading] = useState(true);
  const { refreshKey } = useContext(RefreshContext);
  const [error, setError] = useState<string | null>(null);
  const [events, setEvents] = useState<ActivityEvent[]>([]);
  const [clearedBefore, setClearedBefore] = useState<string | null>(null);
  const [showFocusMode, setShowFocusMode] = useState(true);

  useEffect(() => {
    try {
      const stored = localStorage.getItem(CLEARED_EVENTS_LS_KEY);
      if (stored) setClearedBefore(stored);
    } catch { /* localStorage unavailable */ }
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/events?limit=20&all_noise=${!showFocusMode}`, {
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`Failed to load: ${res.status}`);
      const json = await res.json();
      const items: ActivityEvent[] = Array.isArray(json.events) ? json.events : [];
      setEvents(items);
    } catch (err: any) {
      setError(err.message || "Failed to load recent events");
    } finally {
      setLoading(false);
    }
  }, [showFocusMode]);

  useEffect(() => {
    let cancelled = false;
    queueMicrotask(() => {
      if (!cancelled) void load();
    });
    return () => {
      cancelled = true;
    };
  }, [load, refreshKey, showFocusMode]);

  const handleClearAll = () => {
    const now = new Date().toISOString();
    try { localStorage.setItem(CLEARED_EVENTS_LS_KEY, now); } catch { /* ignore */ }
    setClearedBefore(now);
  };

  if (loading) {
    return (
      <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
        <div className="mb-4 flex items-center gap-2">
          <Clock className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">Recent Events</h2>
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
          <Clock className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">Recent Events</h2>
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

  const clearedTs = clearedBefore ? new Date(clearedBefore).getTime() : null;
  const isHidden = (e: ActivityEvent) => {
    const st = (e.status || "").toLowerCase();
    return st === "dismissed" || st === "resolved";
  };
  const items = clearedTs
    ? events.filter((e) => {
        if (isHidden(e)) return false;
        const ts = e.created_at_utc || e.updated_at_utc || "";
        if (!ts) return false;
        return new Date(ts).getTime() > clearedTs;
      })
    : events.filter((e) => !isHidden(e));

  return (
    <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
      <div className="mb-4 flex items-center justify-between">
        <Link href="/dashboard/events" className="flex items-center gap-2 hover:opacity-80 transition-opacity">
          <Clock className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">Recent Events</h2>
        </Link>
        <div className="flex items-center gap-2">
          <label className="text-[11px] text-muted-foreground font-medium flex items-center cursor-pointer gap-1.5 mr-2">
            <input 
              type="checkbox" 
              checked={showFocusMode}
              onChange={(e) => setShowFocusMode(e.target.checked)}
              className="accent-amber-600 cursor-pointer h-3 w-3"
            />
            Focus Mode
          </label>
          <span className="text-xs text-muted-foreground">{items.length} shown</span>
          {items.length > 0 && (
            <button
              onClick={handleClearAll}
              className="rounded border border-border bg-card px-2 py-0.5 text-xs text-muted-foreground hover:bg-card/50 hover:text-foreground/80"
            >
              Clear All
            </button>
          )}
        </div>
      </div>

      {items.length === 0 ? (
        <div className="flex flex-1 items-center justify-center py-8">
          <p className="text-sm text-muted-foreground">No recent events</p>
        </div>
      ) : (
        <div className="max-h-80 space-y-2 overflow-y-auto">
          {items.map((event) => {
            const sev = severityBadge(event.severity);
            return (
              <div
                key={event.id}
                className="rounded-lg border border-border/50 bg-card/30 p-3"
              >
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-foreground leading-snug">
                      {sourceDomainIcon(event.source_domain)} {event.title || event.kind || "Event"}
                    </p>
                    {event.summary && (
                      <p className="mt-0.5 text-xs text-muted-foreground leading-snug line-clamp-2">
                        {event.summary}
                      </p>
                    )}
                  </div>
                  <span className={`flex-shrink-0 rounded px-1.5 py-0.5 text-xs ${sev.color}`}>
                    {sev.label}
                  </span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-2">
                  {event.source_domain && (
                    <span className="rounded bg-card/50 px-1.5 py-0.5 text-xs text-muted-foreground">
                      {event.source_domain}
                    </span>
                  )}
                  {event.status && event.status !== "new" && (
                    <span className="text-xs text-muted-foreground">{event.status}</span>
                  )}
                  <span className="text-xs text-muted-foreground">
                    {formatTs(event.created_at_utc || event.updated_at_utc)}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

type CSIDigestItem = {
  id: string;
  event_id: string;
  source: string;
  event_type: string;
  title: string;
  summary: string;
  created_at: string;
};

function csiSourceIcon(eventType: string): string {
  const t = (eventType || "").toLowerCase();
  if (t.includes("reddit")) return "🟠";
  if (t.includes("threads")) return "🟣";
  if (t.includes("rss") || t.includes("youtube")) return "🔴";
  if (t.includes("global") || t.includes("batch") || t.includes("brief")) return "🔵";
  return "📊";
}

// CSI Signals Panel — wired to /api/v1/dashboard/csi/digests (actual CSI reports)
function CSISignalsPanel() {
  const [loading, setLoading] = useState(true);
  const { refreshKey } = useContext(RefreshContext);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<CSIDigestItem[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/dashboard/csi/digests?limit=10`,
        { cache: "no-store" }
      );
      if (!res.ok) throw new Error(`Failed: ${res.status}`);
      const json = await res.json();
      const digests: CSIDigestItem[] = Array.isArray(json.digests) ? json.digests : [];
      setItems(digests);
    } catch (err: any) {
      setError(err.message || "Failed to load CSI signals");
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
          <TrendingUp className="h-4 w-4 text-secondary" />
          <h2 className="text-sm font-medium text-foreground/80">CSI Signals</h2>
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
          <TrendingUp className="h-4 w-4 text-secondary" />
          <h2 className="text-sm font-medium text-foreground/80">CSI Signals</h2>
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
    <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
      <div className="mb-4 flex items-center justify-between">
        <Link href="/dashboard/csi" className="flex items-center gap-2 hover:opacity-80 transition-opacity">
          <TrendingUp className="h-4 w-4 text-secondary" />
          <h2 className="text-sm font-medium text-foreground/80">CSI Signals</h2>
        </Link>
        <span className="text-xs text-muted-foreground">{items.length} signals</span>
      </div>

      {items.length === 0 ? (
        <div className="flex flex-1 items-center justify-center py-8">
          <p className="text-sm text-muted-foreground">No recent CSI signals</p>
        </div>
      ) : (
        <div className="max-h-80 space-y-2 overflow-y-auto">
          {items.map((digest) => (
            <Link
              key={digest.id}
              href="/dashboard/csi"
              className="block rounded-lg border border-border/50 bg-card/30 p-3 transition-colors hover:border-border hover:bg-card/50"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-foreground leading-snug line-clamp-2">
                  {csiSourceIcon(digest.event_type)} {digest.title || "CSI Report"}
                </p>
                {digest.summary && (
                  <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{digest.summary}</p>
                )}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span className="rounded bg-secondary/10 px-1.5 py-0.5 text-xs text-secondary">
                  {digest.event_type?.replace(/_/g, " ") || digest.source}
                </span>
                <span className="text-xs text-muted-foreground">
                  {formatTs(digest.created_at)}
                </span>
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

type SystemResourcesPayload = {
  version: number;
  overall_status: "ok" | "warn" | "critical";
  generated_at_utc: string;
  summary: string;
  metrics: {
    cpu_load_1m: number;
    cpu_load_5m: number;
    cpu_load_15m: number;
    cpu_cores: number;
    load_per_core: number;
    ram_used_gb: number;
    ram_total_gb: number;
    ram_percent: number;
    swap_used_gb: number;
    swap_total_gb: number;
    swap_percent: number;
    disk_used_gb: number;
    disk_total_gb: number;
    disk_percent: number;
    active_agent_sessions: number;
    gateway_errors_30m: number;
    dispatch_concurrency: number;
  };
  findings: Array<{
    metric: string;
    value: number | string;
    threshold?: number;
    status: string;
    message: string;
  }>;
};

function MetricBar({ label, value, percent, unit }: { label: string; value: number; percent: number; unit?: string }) {
  const color =
    percent >= 85 ? "bg-red-500" : percent >= 70 ? "bg-accent" : "bg-primary";
  const textColor =
    percent >= 85 ? "text-red-400" : percent >= 70 ? "text-accent" : "text-primary";
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-muted-foreground">{label}</span>
        <span className={textColor}>
          {value}{unit || ""} ({percent}%)
        </span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-card/50">
        <div
          className={`h-1.5 rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${Math.min(percent, 100)}%` }}
        />
      </div>
    </div>
  );
}

function SystemResourcesPanel() {
  const [loading, setLoading] = useState(true);
  const { refreshKey } = useContext(RefreshContext);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<SystemResourcesPayload | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/system-resources`, {
        cache: "no-store",
      });
      if (!res.ok) throw new Error(`Failed: ${res.status}`);
      const json = await res.json();
      setData(json as SystemResourcesPayload);
      setError(null);
    } catch (err: any) {
      setError(err.message || "Failed to load");
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
          <Cpu className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">System Resources</h2>
        </div>
        <div className="space-y-3">
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-4 w-full" />
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
        <div className="mb-4 flex items-center gap-2">
          <Cpu className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">System Resources</h2>
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

  const m = data?.metrics;
  if (!m) return null;

  const statusColor =
    data.overall_status === "critical"
      ? "bg-red-500/10 text-red-400 ring-red-500/20"
      : data.overall_status === "warn"
      ? "bg-accent/10 text-accent ring-yellow-500/20"
      : "bg-primary/10 text-primary ring-green-500/20";

  return (
    <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Cpu className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">System Resources</h2>
        </div>
        <span className={`rounded px-2 py-0.5 text-xs font-medium ring-1 ${statusColor}`}>
          {data.overall_status}
        </span>
      </div>

      <div className="space-y-3">
        {/* CPU */}
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className="text-muted-foreground">CPU Load</span>
            <span className="text-foreground/80">
              {m.cpu_load_1m.toFixed(2)} / {m.cpu_cores} cores
            </span>
          </div>
          <div className="h-1.5 w-full rounded-full bg-card/50">
            <div
              className={`h-1.5 rounded-full transition-all duration-500 ${
                m.load_per_core >= 1 ? "bg-red-500" : m.load_per_core >= 0.7 ? "bg-accent" : "bg-primary"
              }`}
              style={{ width: `${Math.min(m.load_per_core * 100, 100)}%` }}
            />
          </div>
        </div>

        {/* RAM */}
        <MetricBar label="RAM" value={m.ram_used_gb} percent={m.ram_percent} unit=" GiB" />

        {/* Swap */}
        <MetricBar label="Swap" value={m.swap_used_gb} percent={m.swap_percent} unit=" GiB" />

        {/* Disk */}
        <MetricBar label="Disk" value={m.disk_used_gb} percent={m.disk_percent} unit=" GB" />

        {/* Session count */}
        <div className="flex items-center justify-between rounded-lg bg-card/30 px-3 py-2">
          <span className="text-xs text-muted-foreground">Active Sessions</span>
          <span className={`text-sm font-medium ${
            m.active_agent_sessions > 50 ? "text-red-400" : m.active_agent_sessions > 30 ? "text-accent" : "text-primary"
          }`}>
            {m.active_agent_sessions}
          </span>
        </div>

        {/* Error count */}
        <div className="flex items-center justify-between rounded-lg bg-card/30 px-3 py-2">
          <span className="text-xs text-muted-foreground">Errors (30m)</span>
          <span className={`text-sm font-medium ${
            m.gateway_errors_30m > 50 ? "text-red-400" : m.gateway_errors_30m > 10 ? "text-accent" : "text-primary"
          }`}>
            {m.gateway_errors_30m}
          </span>
        </div>

        {/* Updated timestamp */}
        {data.generated_at_utc && (
          <div className="text-center text-xs text-muted">
            Updated {formatTs(data.generated_at_utc)}
          </div>
        )}
      </div>
    </div>
  );
}


// Heartbeat status chip -- shows live session heartbeat status
function HeartbeatChip() {
  const { refreshKey } = useContext(RefreshContext);
  const [sessions, setSessions] = useState<number>(0);
  const [busyCount, setBusyCount] = useState<number>(0);
  const [lastRun, setLastRun] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/heartbeat/last`, { cache: "no-store" });
      if (!res.ok) return;
      const json = await res.json();
      const heartbeats = json.heartbeats || {};
      const keys = Object.keys(heartbeats);
      setSessions(keys.length);
      setBusyCount(keys.filter((k: string) => heartbeats[k].busy).length);
      // Get most recent last_run across sessions
      const runs = keys
        .map((k: string) => heartbeats[k].last_run)
        .filter(Boolean)
        .sort()
        .reverse();
      setLastRun(runs[0] || null);
    } catch {
      // silently ignore
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

  if (sessions === 0) return null;

  const dotColor = busyCount > 0 ? "bg-accent animate-pulse" : "bg-primary";

  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-border/50 bg-card/50 px-2.5 py-1 text-xs text-muted-foreground">
      <span className={`inline-block h-2 w-2 rounded-full ${dotColor}`} />
      <Heart className="h-3 w-3 text-muted-foreground" />
      {busyCount > 0 ? `${busyCount} running` : "idle"}
      {lastRun && <span className="text-muted-foreground/60">· last {formatTs(lastRun)}</span>}
    </span>
  );
}


// Freelance Pipeline types
type FreelanceOpportunity = {
  id: string;
  title: string;
  platform: string;
  rate?: string;
  fit_score?: number;
  status: string;
  created_at: string;
};

type FreelanceApplication = {
  id: string;
  position_title: string;
  platform: string;
  company?: string;
  status: string;
  created_at: string;
};

type FreelancePipelineSummary = {
  opportunities: FreelanceOpportunity[];
  applications: FreelanceApplication[];
  stats: {
    total_opportunities: number;
    active_applications: number;
    draft_applications: number;
    submitted_applications: number;
    responses: number;
    interviews: number;
    success_rate: number;
  };
};

// Freelance Pipeline Panel
function FreelancePipelinePanel() {
  const [loading, setLoading] = useState(true);
  const { refreshKey } = useContext(RefreshContext);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<FreelancePipelineSummary | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/freelance/pipeline`, {
        cache: "no-store",
      });
      if (!res.ok) {
        throw new Error(`Failed to load: ${res.status}`);
      }
      const json = await res.json();
      setData(json as FreelancePipelineSummary);
    } catch (err: any) {
      setError(err.message || "Failed to load freelance pipeline");
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
          <Briefcase className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">Freelance Pipeline</h2>
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
          <Briefcase className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">Freelance Pipeline</h2>
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

  const { opportunities, applications, stats } = data || {
    opportunities: [],
    applications: [],
    stats: { total_opportunities: 0, active_applications: 0, draft_applications: 0, submitted_applications: 0, responses: 0, interviews: 0, success_rate: 0 }
  };

  return (
    <div className="rounded-none border border-white/10 bg-[#0b1326]/70 backdrop-blur-md p-4">
      <div className="mb-4 flex items-center justify-between">
        <Link href="/dashboard/todolist" className="flex items-center gap-2 hover:opacity-80 transition-opacity">
          <Briefcase className="h-4 w-4 text-muted-foreground" />
          <h2 className="text-sm font-medium text-foreground/80">Freelance Pipeline</h2>
        </Link>
        <span className="text-xs text-muted-foreground">
          {stats.total_opportunities} opps · {stats.active_applications} apps
        </span>
      </div>

      {/* Stats Row */}
      <div className="mb-3 grid grid-cols-5 gap-2 text-center">
        <div className="rounded-lg bg-card/30 p-2">
          <p className="text-lg font-bold text-foreground">{stats.draft_applications}</p>
          <p className="text-xs text-muted-foreground">Drafts</p>
        </div>
        <div className="rounded-lg bg-card/30 p-2">
          <p className="text-lg font-bold text-foreground">{stats.submitted_applications}</p>
          <p className="text-xs text-muted-foreground">Sent</p>
        </div>
        <div className="rounded-lg bg-card/30 p-2">
          <p className="text-lg font-bold text-accent">{stats.responses}</p>
          <p className="text-xs text-muted-foreground">Replies</p>
        </div>
        <div className="rounded-lg bg-card/30 p-2">
          <p className="text-lg font-bold text-accent">{stats.interviews}</p>
          <p className="text-xs text-muted-foreground">Calls</p>
        </div>
        <div className="rounded-lg bg-card/30 p-2">
          <p className="text-lg font-bold text-primary">{stats.success_rate.toFixed(0)}%</p>
          <p className="text-xs text-muted-foreground">Win %</p>
        </div>
      </div>

      {/* Applications List */}
      {applications.length > 0 && (
        <div className="max-h-40 space-y-1.5 overflow-y-auto">
          {applications.slice(0, 4).map((app) => (
            <div
              key={app.id}
              className="rounded-lg border border-border/50 bg-card/30 p-2"
            >
              <div className="flex items-start justify-between gap-2">
                <p className="text-sm font-medium text-foreground leading-snug line-clamp-1">
                  {app.position_title}
                </p>
                <span className={`flex-shrink-0 rounded px-1.5 py-0.5 text-xs ${statusColor(app.status)}`}>
                  {app.status}
                </span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-2">
                <span className="rounded bg-card/50 px-1.5 py-0.5 text-xs text-muted-foreground">
                  {app.platform}
                </span>
                <span className="text-xs text-muted-foreground">
                  {formatTs(app.created_at)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {applications.length === 0 && (
        <div className="flex flex-1 items-center justify-center py-4">
          <p className="text-sm text-muted-foreground">No active applications</p>
        </div>
      )}
    </div>
  );
}

/**
 * Mission Control Dashboard
 *
 * A centralized task monitoring interface with five main panels:
 * - Active Tasks: Currently running tasks and operations
 * - System Status: Health and status of system components
 * - System Resources: Live VPS metrics (CPU, RAM, Swap, Disk, Sessions)
 * - Recent Events: Latest events and notifications
 * - CSI Signals: Content & Signal Intelligence feed
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
    triggerRefresh(); // initial load
    intervalRef.current = setInterval(triggerRefresh, REFRESH_INTERVAL);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
    };
  }, [triggerRefresh]);

  const exportReport = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/system-resources`, { cache: "no-store" });
      if (!res.ok) throw new Error(`Failed: ${res.status}`);
      const data = await res.json();
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `mission-control-report-${Date.now()}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch {
      // silently fail -- could add toast notification later
    }
  };

  const [heartbeatLoading, setHeartbeatLoading] = useState(false);
  const [heartbeatResult, setHeartbeatResult] = useState<string | null>(null);

  const triggerHeartbeat = useCallback(async () => {
    setHeartbeatLoading(true);
    setHeartbeatResult(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/heartbeat/wake`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reason: "mission_control_manual", mode: "now" }),
      });
      if (!res.ok) throw new Error(`Failed: ${res.status}`);
      const json = await res.json();
      setHeartbeatResult(`Queued ${json.count ?? json.session_id ?? "?"} session(s)`);
    } catch (err: any) {
      setHeartbeatResult(err.message || "Failed");
    } finally {
      setHeartbeatLoading(false);
      // Clear result after 5s
      setTimeout(() => setHeartbeatResult(null), 5000);
    }
  }, []);

  return (
    <RefreshContext.Provider value={{ refreshKey, lastRefresh, isRefreshing }}>
      <div className="flex h-full flex-col gap-6">
        {/* Page Header */}
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 ring-1 ring-primary/20">
              <Activity className="h-5 w-5 text-primary" />
            </div>
            <div>
              <h1 className="text-lg font-semibold text-foreground">Mission Control</h1>
              <p className="text-sm text-muted-foreground">Centralized task monitoring and system overview</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <HeartbeatChip />
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
              onClick={triggerHeartbeat}
              disabled={heartbeatLoading}
              className="flex items-center gap-2 rounded-lg bg-card px-3 py-2 text-sm text-foreground/80 transition-colors hover:bg-card/50 disabled:opacity-60"
            >
              <Zap className={`h-4 w-4 ${heartbeatLoading ? "animate-pulse" : ""}`} />
              {heartbeatLoading ? "Triggering..." : "Trigger Heartbeat"}
            </button>
            {heartbeatResult && (
              <span className="flex items-center gap-1 rounded-lg bg-primary/10 px-3 py-2 text-xs text-primary">
                {heartbeatResult}
              </span>
            )}
            <button
              onClick={() => router.push("/dashboard/notifications")}
              className="flex items-center gap-2 rounded-lg bg-card px-3 py-2 text-sm text-foreground/80 transition-colors hover:bg-card/50"
            >
              <Bell className="h-4 w-4" />
              View Notifications
            </button>
            <button
              onClick={() => router.push("/dashboard/todolist")}
              className="flex items-center gap-2 rounded-lg bg-card px-3 py-2 text-sm text-foreground/80 transition-colors hover:bg-card/50"
            >
              <Briefcase className="h-4 w-4" />
              Freelance Board
            </button>
            <button
              onClick={exportReport}
              className="flex items-center gap-2 rounded-lg bg-card px-3 py-2 text-sm text-foreground/80 transition-colors hover:bg-card/50"
            >
              <Download className="h-4 w-4" />
              Export Report
            </button>
          </div>
        </div>

        {/* Main Content Grid - Top row: 3 cols, Bottom row: 2 cols */}
        <div className="grid flex-1 gap-4 lg:grid-cols-4">
          <div className="col-span-1 lg:col-span-2 flex flex-col gap-4">
            <ActiveTasksPanel />
            <RecentEventsPanel />
          </div>
          <div className="col-span-1 flex flex-col gap-4">
            <SystemStatusPanel />
            <CapacityGovernorPanel refreshKey={refreshKey} />
            <SystemResourcesPanel />
          </div>
          <div className="col-span-1 flex flex-col gap-4">
            <PipelineStatsPanel refreshKey={refreshKey} />
            <CSISignalsPanel />
            <FreelancePipelinePanel />
          </div>
        </div>
      </div>
    </RefreshContext.Provider>
  );
}
