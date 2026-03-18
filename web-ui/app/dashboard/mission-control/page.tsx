"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Activity, BarChart3, Bell, Briefcase, CheckCircle, Clock, Download, Loader2, RefreshCw, Timer, TrendingUp, Cpu, XCircle } from "lucide-react";
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

type DispatchQueueItem = {
  task_id: string;
  title: string;
  rank?: number;
  eligible?: boolean;
  skip_reason?: string | null;
  built_at?: string;
  updated_at?: string;
  status?: string;
  priority?: number;
  source_kind?: string;
};

type DispatchQueuePayload = {
  status: string;
  queue_build_id?: string;
  items: DispatchQueueItem[];
  eligible_total?: number;
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
  if (p === 3) return "text-orange-400 bg-orange-500/10";
  if (p === 2) return "text-yellow-400 bg-yellow-500/10";
  return "text-slate-400 bg-slate-500/10";
}

function statusColor(status?: string): string {
  const s = (status || "").toLowerCase();
  if (s === "completed" || s === "done") return "text-green-400 bg-green-500/10";
  if (s === "in_progress" || s === "running") return "text-blue-400 bg-blue-500/10";
  if (s === "blocked" || s === "failed") return "text-red-400 bg-red-500/10";
  if (s === "pending" || s === "queued") return "text-yellow-400 bg-yellow-500/10";
  return "text-slate-400 bg-slate-500/10";
}

// Loading skeleton component
function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded bg-slate-700/50 ${className}`} />;
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

// Active Tasks Panel (Task Queue Overview)
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
      setError(err.message || "Failed to load active tasks");
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  if (loading) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-slate-400" />
            <h2 className="text-sm font-medium text-slate-300">Active Tasks</h2>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-300"
            >
              <option value="all">All Status</option>
              <option value="pending">Pending</option>
              <option value="in_progress">In Progress</option>
              <option value="blocked">Blocked</option>
              <option value="completed">Completed</option>
            </select>
            <span className="text-xs text-slate-500">{data?.pagination?.total || 0} total</span>
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
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <BarChart3 className="h-4 w-4 text-slate-400" />
            <h2 className="text-sm font-medium text-slate-300">Active Tasks</h2>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={statusFilter}
              onChange={(e) => setStatusFilter(e.target.value)}
              className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-300"
            >
              <option value="all">All Status</option>
              <option value="pending">Pending</option>
              <option value="in_progress">In Progress</option>
              <option value="blocked">Blocked</option>
              <option value="completed">Completed</option>
            </select>
            <span className="text-xs text-slate-500">{data?.pagination?.total || 0} total</span>
          </div>
        </div>
        <div className="flex flex-col items-center justify-center py-6 text-center">
          <XCircle className="mb-2 h-8 w-8 text-red-400" />
          <p className="text-sm text-slate-500">{error}</p>
          <button
            onClick={load}
            className="mt-3 flex items-center gap-1 rounded bg-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-600"
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
    <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <BarChart3 className="h-4 w-4 text-slate-400" />
          <h2 className="text-sm font-medium text-slate-300">Active Tasks</h2>
        </div>
        <div className="flex items-center gap-2">
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-300"
          >
            <option value="all">All Status</option>
            <option value="pending">Pending</option>
            <option value="in_progress">In Progress</option>
            <option value="blocked">Blocked</option>
            <option value="completed">Completed</option>
          </select>
          <span className="text-xs text-slate-500">{data?.pagination?.total || 0} total</span>
        </div>
      </div>

      {items.length === 0 ? (
        <div className="flex flex-1 items-center justify-center py-8">
          <p className="text-sm text-slate-500">No active tasks</p>
        </div>
      ) : (
        <div className="max-h-80 space-y-2 overflow-y-auto">
          {items.map((item) => (
            <Link
              key={item.task_id}
              href={`/dashboard/todolist?mode=agent&focus=${item.task_id}`}
              className="block rounded-lg border border-slate-700/50 bg-slate-800/30 p-3 transition-colors hover:border-slate-600 hover:bg-slate-800/50"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-slate-200 leading-snug">{item.title}</p>
                  {item.description && (
                    <p className="mt-0.5 text-xs text-slate-500 leading-snug line-clamp-2">{item.description}</p>
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
                  <span className="rounded bg-slate-700 px-1.5 py-0.5 text-xs text-slate-400">
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
    load();
  }, [load, refreshKey]);

  if (loading) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-4 flex items-center gap-2">
          <Activity className="h-4 w-4 text-slate-400" />
          <h2 className="text-sm font-medium text-slate-300">System Status</h2>
        </div>
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-slate-500" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-4 flex items-center gap-2">
          <Activity className="h-4 w-4 text-slate-400" />
          <h2 className="text-sm font-medium text-slate-300">System Status</h2>
        </div>
        <div className="flex flex-col items-center justify-center py-6 text-center">
          <XCircle className="mb-2 h-8 w-8 text-red-400" />
          <p className="text-sm text-slate-500">{error}</p>
          <button
            onClick={load}
            className="mt-3 flex items-center gap-1 rounded bg-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-600"
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
    <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
      <div className="mb-4 flex items-center gap-2">
        <Activity className="h-4 w-4 text-slate-400" />
        <h2 className="text-sm font-medium text-slate-300">System Status</h2>
      </div>

      <div className="space-y-4">
        {/* Gateway Status */}
        <div className="flex items-center justify-between rounded-lg bg-slate-800/30 p-3">
          <div className="flex items-center gap-3">
            {isHealthy ? (
              <CheckCircle className="h-6 w-6 text-green-400" />
            ) : (
              <XCircle className="h-6 w-6 text-red-400" />
            )}
            <div>
              <p className="text-sm font-medium text-slate-200">Gateway</p>
              <p className="text-xs text-slate-500">
                {isHealthy ? "All systems operational" : "System issues detected"}
              </p>
            </div>
          </div>
          <span
            className={`rounded px-2 py-1 text-xs font-medium ${
              isHealthy ? "bg-green-500/10 text-green-400" : "bg-red-500/10 text-red-400"
            }`}
          >
            {data?.status || "unknown"}
          </span>
        </div>

        {/* Database Status */}
        {data?.db_status && (
          <div className="flex items-center justify-between rounded-lg bg-slate-800/30 p-3">
            <div className="flex items-center gap-3">
              {data.db_status === "connected" ? (
                <CheckCircle className="h-5 w-5 text-green-400" />
              ) : (
                <XCircle className="h-5 w-5 text-red-400" />
              )}
              <div>
                <p className="text-sm text-slate-200">Database</p>
                <p className="text-xs text-slate-500">
                  {data.db_error || data.db_status}
                </p>
              </div>
            </div>
            <span
              className={`rounded px-2 py-1 text-xs ${
                data.db_status === "connected"
                  ? "bg-green-500/10 text-green-400"
                  : "bg-red-500/10 text-red-400"
              }`}
            >
              {data.db_status}
            </span>
          </div>
        )}

        {/* Last Check */}
        {data?.timestamp && (
          <div className="text-center text-xs text-slate-500">
            Last checked: {formatTs(data.timestamp)}
          </div>
        )}
      </div>
    </div>
  );
}

const CLEARED_EVENTS_LS_KEY = "mc_cleared_events_before";

// Recent Events Panel
function RecentEventsPanel() {
  const [loading, setLoading] = useState(true);
  const { refreshKey } = useContext(RefreshContext);
  const [error, setError] = useState<string | null>(null);
  const [data, setData] = useState<DispatchQueuePayload | null>(null);
  const [clearedBefore, setClearedBefore] = useState<string | null>(null);

  // Load the "cleared before" timestamp from localStorage on mount
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
      const res = await fetch(`${API_BASE}/api/v1/dashboard/todolist/dispatch-queue?limit=20`, {
        cache: "no-store",
      });
      if (!res.ok) {
        throw new Error(`Failed to load: ${res.status}`);
      }
      const json = await res.json();
      setData(json as DispatchQueuePayload);
    } catch (err: any) {
      setError(err.message || "Failed to load recent events");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  const handleClearAll = () => {
    const now = new Date().toISOString();
    try {
      localStorage.setItem(CLEARED_EVENTS_LS_KEY, now);
    } catch { /* ignore */ }
    setClearedBefore(now);
  };

  if (loading) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-4 flex items-center gap-2">
          <Clock className="h-4 w-4 text-slate-400" />
          <h2 className="text-sm font-medium text-slate-300">Recent Events</h2>
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
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-4 flex items-center gap-2">
          <Clock className="h-4 w-4 text-slate-400" />
          <h2 className="text-sm font-medium text-slate-300">Recent Events</h2>
        </div>
        <div className="flex flex-col items-center justify-center py-6 text-center">
          <XCircle className="mb-2 h-8 w-8 text-red-400" />
          <p className="text-sm text-slate-500">{error}</p>
          <button
            onClick={load}
            className="mt-3 flex items-center gap-1 rounded bg-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-600"
          >
            <RefreshCw className="h-3 w-3" />
            Retry
          </button>
        </div>
      </div>
    );
  }

  // Sort by updated_at descending (most recent first), then filter out cleared items
  const allItems = (data?.items || []).slice().sort((a, b) => {
    const ta = a.updated_at ? new Date(a.updated_at).getTime() : 0;
    const tb = b.updated_at ? new Date(b.updated_at).getTime() : 0;
    return tb - ta;
  });

  const clearedTs = clearedBefore ? new Date(clearedBefore).getTime() : null;
  const items = clearedTs
    ? allItems.filter((item) => {
        if (!item.updated_at) return false;
        return new Date(item.updated_at).getTime() > clearedTs;
      })
    : allItems;

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Clock className="h-4 w-4 text-slate-400" />
          <h2 className="text-sm font-medium text-slate-300">Recent Events</h2>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-xs text-slate-500">{data?.eligible_total || 0} eligible</span>
          {items.length > 0 && (
            <button
              onClick={handleClearAll}
              className="rounded border border-slate-700 bg-slate-800 px-2 py-0.5 text-xs text-slate-400 hover:bg-slate-700 hover:text-slate-300"
            >
              Clear All
            </button>
          )}
        </div>
      </div>

      {items.length === 0 ? (
        <div className="flex flex-1 items-center justify-center py-8">
          <p className="text-sm text-slate-500">No recent events</p>
        </div>
      ) : (
        <div className="max-h-80 space-y-2 overflow-y-auto">
          {items.map((item) => (
            <div
              key={item.task_id}
              className="rounded-lg border border-slate-700/50 bg-slate-800/30 p-3"
            >
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium text-slate-200 leading-snug">{item.title}</p>
                </div>
                {item.eligible !== undefined && (
                  <span
                    className={`flex-shrink-0 rounded px-1.5 py-0.5 text-xs ${
                      item.eligible
                        ? "bg-green-500/10 text-green-400"
                        : "bg-yellow-500/10 text-yellow-400"
                    }`}
                  >
                    {item.eligible ? "eligible" : "skipped"}
                  </span>
                )}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                {item.rank !== undefined && (
                  <span className="text-xs text-slate-500">Rank: {item.rank}</span>
                )}
                {item.source_kind && (
                  <span className="rounded bg-slate-700 px-1.5 py-0.5 text-xs text-slate-400">
                    {item.source_kind}
                  </span>
                )}
                {item.updated_at && (
                  <span className="text-xs text-slate-500">{formatTs(item.updated_at)}</span>
                )}
              </div>
              {item.skip_reason && (
                <p className="mt-1 text-xs text-yellow-400/80">Skip reason: {item.skip_reason}</p>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

type CSINotificationItem = {
  id: string;
  kind: string;
  title?: string;
  body?: string;
  source_domain?: string;
  created_at?: string;
  updated_at?: string;
  status?: string;
};

// CSI Signals Panel -- fetches from the working notifications endpoint filtered by source_domain=csi
function CSISignalsPanel() {
  const [loading, setLoading] = useState(true);
  const { refreshKey } = useContext(RefreshContext);
  const [error, setError] = useState<string | null>(null);
  const [items, setItems] = useState<CSINotificationItem[]>([]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/dashboard/notifications?source_domain=csi&limit=10`,
        { cache: "no-store" }
      );
      if (!res.ok) throw new Error(`Failed: ${res.status}`);
      const json = await res.json();
      const notifications: CSINotificationItem[] = Array.isArray(json.notifications)
        ? json.notifications
        : [];
      // Sort most recent first
      notifications.sort((a, b) => {
        const ta = a.updated_at || a.created_at || "";
        const tb = b.updated_at || b.created_at || "";
        return tb.localeCompare(ta);
      });
      setItems(notifications);
    } catch (err: any) {
      setError(err.message || "Failed to load CSI signals");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  if (loading) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-4 flex items-center gap-2">
          <TrendingUp className="h-4 w-4 text-purple-400" />
          <h2 className="text-sm font-medium text-slate-300">CSI Signals</h2>
        </div>
        <div className="flex items-center justify-center py-8">
          <Loader2 className="h-6 w-6 animate-spin text-slate-500" />
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-4 flex items-center gap-2">
          <TrendingUp className="h-4 w-4 text-purple-400" />
          <h2 className="text-sm font-medium text-slate-300">CSI Signals</h2>
        </div>
        <div className="flex flex-col items-center justify-center py-6 text-center">
          <XCircle className="mb-2 h-8 w-8 text-red-400" />
          <p className="text-sm text-slate-500">{error}</p>
          <button
            onClick={load}
            className="mt-3 flex items-center gap-1 rounded bg-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-600"
          >
            <RefreshCw className="h-3 w-3" />
            Retry
          </button>
        </div>
      </div>
    );
  }

  const signals = items;

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <TrendingUp className="h-4 w-4 text-purple-400" />
          <h2 className="text-sm font-medium text-slate-300">CSI Signals</h2>
        </div>
        <span className="text-xs text-slate-500">{signals.length} signals</span>
      </div>

      {signals.length === 0 ? (
        <div className="flex flex-1 items-center justify-center py-8">
          <p className="text-sm text-slate-500">No recent CSI signals</p>
        </div>
      ) : (
        <div className="max-h-80 space-y-2 overflow-y-auto">
          {signals.map((signal) => (
            <div
              key={signal.id}
              className="rounded-lg border border-slate-700/50 bg-slate-800/30 p-3 transition-colors hover:border-slate-600"
            >
              <div className="min-w-0">
                <p className="text-sm font-medium text-slate-200 leading-snug">
                  {signal.title || signal.kind || "CSI Signal"}
                </p>
                {signal.body && (
                  <p className="mt-1 text-xs text-slate-400 line-clamp-2">{signal.body}</p>
                )}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-2">
                <span className="rounded bg-purple-500/10 px-1.5 py-0.5 text-xs text-purple-400">
                  {signal.kind}
                </span>
                {signal.status && (
                  <span className="text-xs text-slate-500">{signal.status}</span>
                )}
                <span className="text-xs text-slate-500">
                  {formatTs(signal.updated_at || signal.created_at)}
                </span>
              </div>
            </div>
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
    percent >= 85 ? "bg-red-500" : percent >= 70 ? "bg-yellow-500" : "bg-green-500";
  const textColor =
    percent >= 85 ? "text-red-400" : percent >= 70 ? "text-yellow-400" : "text-green-400";
  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between text-xs">
        <span className="text-slate-400">{label}</span>
        <span className={textColor}>
          {value}{unit || ""} ({percent}%)
        </span>
      </div>
      <div className="h-1.5 w-full rounded-full bg-slate-700">
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
    load();
  }, [load, refreshKey]);

  if (loading) {
    return (
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-4 flex items-center gap-2">
          <Cpu className="h-4 w-4 text-slate-400" />
          <h2 className="text-sm font-medium text-slate-300">System Resources</h2>
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
      <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-4 flex items-center gap-2">
          <Cpu className="h-4 w-4 text-slate-400" />
          <h2 className="text-sm font-medium text-slate-300">System Resources</h2>
        </div>
        <div className="flex flex-col items-center justify-center py-6 text-center">
          <XCircle className="mb-2 h-8 w-8 text-red-400" />
          <p className="text-sm text-slate-500">{error}</p>
          <button
            onClick={load}
            className="mt-3 flex items-center gap-1 rounded bg-slate-700 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-600"
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
      ? "bg-yellow-500/10 text-yellow-400 ring-yellow-500/20"
      : "bg-green-500/10 text-green-400 ring-green-500/20";

  return (
    <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
      <div className="mb-4 flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Cpu className="h-4 w-4 text-slate-400" />
          <h2 className="text-sm font-medium text-slate-300">System Resources</h2>
        </div>
        <span className={`rounded px-2 py-0.5 text-xs font-medium ring-1 ${statusColor}`}>
          {data.overall_status}
        </span>
      </div>

      <div className="space-y-3">
        {/* CPU */}
        <div className="space-y-1">
          <div className="flex items-center justify-between text-xs">
            <span className="text-slate-400">CPU Load</span>
            <span className="text-slate-300">
              {m.cpu_load_1m.toFixed(2)} / {m.cpu_cores} cores
            </span>
          </div>
          <div className="h-1.5 w-full rounded-full bg-slate-700">
            <div
              className={`h-1.5 rounded-full transition-all duration-500 ${
                m.load_per_core >= 1 ? "bg-red-500" : m.load_per_core >= 0.7 ? "bg-yellow-500" : "bg-green-500"
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
        <div className="flex items-center justify-between rounded-lg bg-slate-800/30 px-3 py-2">
          <span className="text-xs text-slate-400">Active Sessions</span>
          <span className={`text-sm font-medium ${
            m.active_agent_sessions > 50 ? "text-red-400" : m.active_agent_sessions > 30 ? "text-yellow-400" : "text-green-400"
          }`}>
            {m.active_agent_sessions}
          </span>
        </div>

        {/* Error count */}
        <div className="flex items-center justify-between rounded-lg bg-slate-800/30 px-3 py-2">
          <span className="text-xs text-slate-400">Errors (30m)</span>
          <span className={`text-sm font-medium ${
            m.gateway_errors_30m > 50 ? "text-red-400" : m.gateway_errors_30m > 10 ? "text-yellow-400" : "text-green-400"
          }`}>
            {m.gateway_errors_30m}
          </span>
        </div>

        {/* Updated timestamp */}
        {data.generated_at_utc && (
          <div className="text-center text-xs text-slate-600">
            Updated {formatTs(data.generated_at_utc)}
          </div>
        )}
      </div>
    </div>
  );
}


// Heartbeat status chip -- shows last system-resources check time + status dot
function HeartbeatChip() {
  const { refreshKey } = useContext(RefreshContext);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);
  const [overallStatus, setOverallStatus] = useState<"ok" | "warn" | "critical" | null>(null);

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/system-resources`, { cache: "no-store" });
      if (!res.ok) return;
      const json = await res.json();
      setGeneratedAt(json.generated_at_utc ?? null);
      setOverallStatus(json.overall_status ?? null);
    } catch {
      // silently ignore -- chip stays empty
    }
  }, []);

  useEffect(() => {
    load();
  }, [load, refreshKey]);

  if (!generatedAt) return null;

  const dotColor =
    overallStatus === "critical"
      ? "bg-red-500"
      : overallStatus === "warn"
      ? "bg-yellow-500"
      : "bg-green-500";

  return (
    <span className="inline-flex items-center gap-1.5 rounded-full border border-slate-700/50 bg-slate-800/50 px-2.5 py-1 text-xs text-slate-400">
      <span className={`inline-block h-2 w-2 rounded-full ${dotColor}`} />
      <Activity className="h-3 w-3 text-slate-500" />
      Last check: {formatTs(generatedAt)}
    </span>
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

  return (
    <RefreshContext.Provider value={{ refreshKey, lastRefresh, isRefreshing }}>
      <div className="flex h-full flex-col gap-6">
        {/* Page Header */}
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-500/10 ring-1 ring-blue-500/20">
              <Activity className="h-5 w-5 text-blue-400" />
            </div>
            <div>
              <h1 className="text-lg font-semibold text-slate-100">Mission Control</h1>
              <p className="text-sm text-slate-500">Centralized task monitoring and system overview</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <HeartbeatChip />
            {lastRefresh && (
              <div className="flex items-center gap-1.5 text-xs text-slate-500">
                <Timer className="h-3.5 w-3.5" />
                Updated {formatDistanceToNow(lastRefresh, { addSuffix: true })}
              </div>
            )}
          </div>
        </div>

        {/* Quick Actions Bar */}
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-3">
          <div className="flex flex-wrap gap-2">
            <button
              onClick={triggerRefresh}
              disabled={isRefreshing}
              disabled={isRefreshing}
              className="flex items-center gap-2 rounded-lg bg-slate-800 px-3 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-700 disabled:opacity-60"
            >
              <RefreshCw className={`h-4 w-4 ${isRefreshing ? "animate-spin" : ""}`} />
              Refresh All
            </button>
            <button
              onClick={triggerRefresh}
              disabled={isRefreshing}
              className="flex items-center gap-2 rounded-lg bg-slate-800 px-3 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-700 disabled:opacity-60"
            >
              <Activity className={`h-4 w-4 ${isRefreshing ? "animate-pulse" : ""}`} />
              Run Health Check
            </button>
            <button
              onClick={() => router.push("/dashboard/notifications")}
              className="flex items-center gap-2 rounded-lg bg-slate-800 px-3 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-700"
            >
              <Bell className="h-4 w-4" />
              View Notifications
            </button>
            <button
              onClick={() => router.push("/dashboard/todolist")}
              className="flex items-center gap-2 rounded-lg bg-slate-800 px-3 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-700"
            >
              <Briefcase className="h-4 w-4" />
              Freelance Board
            </button>
            <button
              onClick={exportReport}
              className="flex items-center gap-2 rounded-lg bg-slate-800 px-3 py-2 text-sm text-slate-300 transition-colors hover:bg-slate-700"
            >
              <Download className="h-4 w-4" />
              Export Report
            </button>
          </div>
        </div>

        {/* Main Content Grid - Top row: 3 cols, Bottom row: 2 cols */}
        <div className="grid flex-1 gap-4 md:grid-cols-3">
          <ActiveTasksPanel />
          <SystemStatusPanel />
          <SystemResourcesPanel />
        </div>
        <div className="grid flex-1 gap-4 md:grid-cols-2">
          <RecentEventsPanel />
          <CSISignalsPanel />
        </div>
      </div>
    </RefreshContext.Provider>
  );
}
