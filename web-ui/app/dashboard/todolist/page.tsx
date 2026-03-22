"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { formatDistanceToNow, parseISO } from "date-fns";

const API_BASE = "/api/dashboard/gateway";
const AUTO_REFRESH_SECONDS = 30;

// ── Types ─────────────────────────────────────────────────────────────────────

type AgentQueueItem = {
  task_id: string;
  title: string;
  description?: string;
  project_key?: string;
  priority?: number;
  labels?: string[];
  status?: string;
  must_complete?: boolean;
  score?: number;
  score_confidence?: number;
  stale_state?: string;
  seizure_state?: string;
  updated_at?: string;
  due_at?: string | null;
  source_kind?: string;
  url?: string;
};

type ApprovalRow = {
  approval_id: string;
  title: string;
  status: string;
  priority: number;
  focus_href: string;
  created_at?: string | number;
};

type AgentActivity = {
  active_agents: number;
  active_assignments: Array<{
    assignment_id: string;
    task_id: string;
    title: string;
    agent_id: string;
    state: string;
    started_at: string;
    project_key: string;
    priority: number;
  }>;
  metrics: {
    [windowKey: string]: {
      new: number;
      seized: number;
      rejected: number;
      completed: number;
    };
  };
  backlog_open: number;
};

type OverviewPayload = {
  status: string;
  approvals_pending?: number;
  queue_health?: {
    dispatch_queue_size: number;
    dispatch_eligible: number;
    threshold?: number;
    status_counts: Record<string, number>;
    source_counts: Record<string, number>;
  };
  agent_activity?: {
    active_agents: number;
    active_assignments: number;
    backlog_open: number;
  };
  heartbeat?: {
    enabled: boolean;
    configured_every_seconds: number;
    min_interval_seconds: number;
    effective_default_every_seconds: number;
    cron_interval_seconds?: number | null;
    heartbeat_effective_interval_seconds?: number | null;
    heartbeat_interval_source?: string;
    session_count: number;
    session_state_count: number;
    busy_sessions: number;
    latest_last_run_epoch?: number | null;
    nearest_next_run_epoch?: number | null;
  };
};

type ApprovalHighlightPayload = {
  status: string;
  pending_count: number;
  approvals: ApprovalRow[];
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

type TaskHistoryLinks = {
  session_id?: string;
  session_href?: string;
  run_log_href?: string;
  run_log_path?: string;
};

type CompletedTaskItem = {
  task_id: string;
  title: string;
  description?: string;
  project_key?: string;
  priority?: number;
  status?: string;
  updated_at?: string;
  completed_at?: string;
  source_kind?: string;
  last_assignment?: {
    assignment_id?: string;
    agent_id?: string;
    state?: string;
    started_at?: string;
    ended_at?: string;
    result_summary?: string;
    session_id?: string;
  } | null;
  links?: TaskHistoryLinks;
};

type CompletedTasksPayload = {
  status: string;
  items: CompletedTaskItem[];
};

type TaskAssignmentHistory = {
  assignment_id: string;
  task_id: string;
  agent_id: string;
  session_id?: string;
  state: string;
  started_at?: string;
  ended_at?: string;
  result_summary?: string;
  links?: TaskHistoryLinks;
};

type TaskEvaluationHistory = {
  id: string;
  task_id: string;
  evaluated_at?: string;
  agent_id?: string;
  decision?: string;
  reason?: string;
  score?: number;
  score_confidence?: number;
};

type TaskHistoryPayload = {
  status: string;
  task: AgentQueueItem;
  assignments: TaskAssignmentHistory[];
  evaluations: TaskEvaluationHistory[];
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatTs(ts?: string | null): string {
  if (!ts) return "";
  try {
    return formatDistanceToNow(parseISO(ts), { addSuffix: true });
  } catch {
    return ts;
  }
}

function formatEpochTs(epoch?: number | null): string {
  const value = Number(epoch || 0);
  if (!value || Number.isNaN(value)) return "n/a";
  try {
    return formatDistanceToNow(new Date(value * 1000), { addSuffix: true });
  } catch {
    return "n/a";
  }
}

function formatEvery(seconds?: number | null): string {
  const value = Number(seconds || 0);
  if (!value || Number.isNaN(value)) return "n/a";
  if (value % 3600 === 0) return `${value / 3600}h`;
  if (value % 60 === 0) return `${value / 60}m`;
  return `${value}s`;
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
  if (p >= 4) return "text-secondary";
  if (p === 3) return "text-accent";
  if (p === 2) return "text-sky-300";
  return "text-muted-foreground";
}

function sourceKindPill(kind?: string) {
  const k = String(kind || "internal").toLowerCase();
  const styles: Record<string, string> = {
    todoist: "border-teal-700/60 bg-teal-900/25 text-teal-200",
    internal: "border-sky-700/60 bg-sky-900/25 text-sky-200",
    approval: "border-amber-700/60 bg-amber-900/25 text-amber-200",
    csi: "border-border/60 bg-card/40 text-muted-foreground",
  };
  const style = styles[k] ?? "border-border/60 bg-card/40 text-muted-foreground";
  return (
    <span className={`rounded border px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${style}`}>
      {k}
    </span>
  );
}

function isGatewayUpstreamUnavailable(status: number, detail: string): boolean {
  return status === 502 && detail.toLowerCase().includes("gateway upstream unavailable");
}

/** Derive the external/internal reference URL for a task based on its source. */
function taskSourceUrl(taskId: string, sourceKind?: string, explicitUrl?: string): string | null {
  if (explicitUrl) return explicitUrl;
  const k = String(sourceKind || "").toLowerCase();
  if (k === "todoist") {
    // task_id for Todoist tasks is the raw Todoist task ID
    return `https://app.todoist.com/app/task/${encodeURIComponent(taskId)}`;
  }
  if (k === "approval") {
    return "/dashboard/approvals";
  }
  // internal / system_command / csi / etc. — no external reference
  return null;
}

// ── Main Component ────────────────────────────────────────────────────────────

// ── KanbanCol (stable identity — must be outside the page component to
//    prevent React from remounting on every re-render, which would reset
//    scroll position inside each column) ───────────────────────────────────

type KanbanColProps = {
  label: string;
  emoji: string;
  count: number;
  accentClass: string;
  headerClass: string;
  emptyText: string;
  children: React.ReactNode;
};

function KanbanCol({ label, emoji, count, accentClass, headerClass, emptyText, children }: KanbanColProps) {
  return (
    <div className={`flex flex-col rounded-xl border bg-background/60 ${accentClass}`}>
      <div className="flex items-center justify-between border-b border-border/80 px-4 py-3">
        <div className="flex items-center gap-2">
          <span className="text-base">{emoji}</span>
          <h2 className={`text-sm font-semibold uppercase tracking-[0.14em] ${headerClass}`}>{label}</h2>
        </div>
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${headerClass} bg-card/60`}>{count}</span>
      </div>
      <div className="flex-1 space-y-2 overflow-y-auto p-3 max-h-[60vh]">
        {count === 0 ? (
          <p className="text-xs text-muted italic pt-2">{emptyText}</p>
        ) : children}
      </div>
    </div>
  );
}

export default function ToDoListDashboardPage() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [countdown, setCountdown] = useState(AUTO_REFRESH_SECONDS);

  const [overview, setOverview] = useState<OverviewPayload | null>(null);
  const [approvalsHighlight, setApprovalsHighlight] = useState<ApprovalHighlightPayload | null>(null);
  const [agentQueue, setAgentQueue] = useState<AgentQueuePayload | null>(null);
  const [agentActivity, setAgentActivity] = useState<AgentActivity | null>(null);
  const [completedTasks, setCompletedTasks] = useState<CompletedTasksPayload | null>(null);

  const [openActionMenuId, setOpenActionMenuId] = useState<string | null>(null);
  const [actionPendingTaskId, setActionPendingTaskId] = useState("");
  const [wakePending, setWakePending] = useState(false);
  const [taskHistory, setTaskHistory] = useState<TaskHistoryPayload | null>(null);
  const [taskHistoryLoadingId, setTaskHistoryLoadingId] = useState("");
  const [selectedTaskDetails, setSelectedTaskDetails] = useState<any | null>(null);
  const [deletedTaskIds, setDeletedTaskIds] = useState<Set<string>>(() => {
    try {
      const stored = localStorage.getItem("ua.deleted_completed_tasks.v1");
      if (stored) return new Set(JSON.parse(stored) as string[]);
    } catch { /* ignore */ }
    return new Set();
  });
  const [deleteAllPending, setDeleteAllPending] = useState(false);
  const [hoveredDeleteId, setHoveredDeleteId] = useState<string | null>(null);

  // Persist deletedTaskIds to localStorage whenever it changes
  useEffect(() => {
    try {
      localStorage.setItem("ua.deleted_completed_tasks.v1", JSON.stringify([...deletedTaskIds]));
    } catch { /* ignore */ }
  }, [deletedTaskIds]);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (background = false) => {
    if (background) setRefreshing(true);
    else setLoading(true);
    if (!background) setError("");
    try {
      const agentQueueUrl = new URL(`${API_BASE}/api/v1/dashboard/todolist/agent-queue`, window.location.origin);
      agentQueueUrl.searchParams.set("limit", "120");

      const [overviewRes, approvalsRes, agentRes, activityRes, completedRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/dashboard/todolist/overview`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/v1/dashboard/approvals/highlight`, { cache: "no-store" }),
        fetch(`${agentQueueUrl.pathname}${agentQueueUrl.search}`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/v1/dashboard/todolist/agent-activity`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/v1/dashboard/todolist/completed?limit=80`, { cache: "no-store" }),
      ]);

      const required = [
        { name: "overview", res: overviewRes },
        { name: "agent_queue", res: agentRes },
        { name: "agent_activity", res: activityRes },
        { name: "completed_tasks", res: completedRes },
      ];

      const failures: Array<{ name: string; status: number; detail: string }> = [];
      for (const item of required) {
        if (item.res.ok) continue;
        const detail = await item.res.text().catch(() => "");
        failures.push({ name: item.name, status: item.res.status, detail });
      }
      if (failures.length > 0) {
        if (failures.every((f) => isGatewayUpstreamUnavailable(f.status, f.detail))) {
          throw new Error("Gateway is temporarily unavailable. Please retry in a few seconds.");
        }
        const compact = failures.map((f) => `${f.name}:${f.status}`).join(", ");
        throw new Error(`Endpoints failed (${compact})`);
      }

      const [overviewJson, agentJson, activityJson, completedJson] = await Promise.all([
        overviewRes.json(),
        agentRes.json(),
        activityRes.json(),
        completedRes.json(),
      ]);
      const approvalsJson = approvalsRes.ok
        ? await approvalsRes.json()
        : { status: "degraded", pending_count: 0, approvals: [] };

      setOverview(overviewJson as OverviewPayload);
      setApprovalsHighlight(approvalsJson as ApprovalHighlightPayload);
      setAgentQueue(agentJson as AgentQueuePayload);
      setAgentActivity(activityJson as AgentActivity);
      setCompletedTasks(completedJson as CompletedTasksPayload);
      setError("");
    } catch (err: any) {
      if (!background) setError(err?.message || "Failed to load task data.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  // Auto-refresh timer
  useEffect(() => {
    void load(false);
    intervalRef.current = setInterval(() => {
      setCountdown(AUTO_REFRESH_SECONDS);
      void load(true);
    }, AUTO_REFRESH_SECONDS * 1000);
    countdownRef.current = setInterval(() => {
      setCountdown((c) => (c <= 1 ? AUTO_REFRESH_SECONDS : c - 1));
    }, 1000);
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current);
      if (countdownRef.current) clearInterval(countdownRef.current);
    };
  }, [load]);

  const handleTaskAction = useCallback(async (taskId: string, action: string) => {
    setActionPendingTaskId(taskId);
    setOpenActionMenuId(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/todolist/tasks/${encodeURIComponent(taskId)}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ action }),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(String(payload?.detail || `Action failed (${res.status})`));
      }
      await load(true);
    } catch (err: any) {
      setError(err?.message || "Task action failed.");
    } finally {
      setActionPendingTaskId("");
    }
  }, [load]);

  const handleOpenTaskHistory = useCallback(async (taskId: string) => {
    setTaskHistoryLoadingId(taskId);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/dashboard/todolist/tasks/${encodeURIComponent(taskId)}/history?limit=120`,
        { cache: "no-store" },
      );
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(String(payload?.detail || `History failed (${res.status})`));
      }
      const payload = await res.json();
      setTaskHistory(payload as TaskHistoryPayload);
      setError("");
    } catch (err: any) {
      setError(err?.message || "Failed to load task history.");
    } finally {
      setTaskHistoryLoadingId("");
    }
  }, []);

  const handleWakeHeartbeat = useCallback(async (taskId?: string) => {
    setWakePending(true);
    try {
      const reason = taskId ? `todolist_force:${taskId}` : "todolist_force_next";
      const res = await fetch(`${API_BASE}/api/v1/heartbeat/wake`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "next", reason }),
      });
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(String(payload?.detail || `Wake failed (${res.status})`));
      }
      await load(true);
      setError("");
    } catch (err: any) {
      setError(err?.message || "Failed to queue next heartbeat.");
    } finally {
      setWakePending(false);
    }
  }, [load]);

  const handleDeleteCompletedTask = useCallback(async (taskId: string) => {
    setDeletedTaskIds((prev) => new Set([...prev, taskId]));
    try {
      await fetch(`${API_BASE}/api/v1/dashboard/todolist/completed/${encodeURIComponent(taskId)}`, { method: "DELETE" });
    } catch {
      // optimistic delete — local state already updated
    }
  }, []);

  const handleDeleteAllCompleted = useCallback(async () => {
    setDeleteAllPending(true);
    const ids = (completedTasks?.items || []).map((i) => i.task_id);
    // Merge into existing set so previously-deleted IDs aren't lost
    setDeletedTaskIds((prev) => {
      const merged = new Set(prev);
      for (const id of ids) merged.add(id);
      return merged;
    });
    try {
      // Await all DELETEs before reloading to avoid race condition
      await Promise.allSettled(
        ids.map((id) =>
          fetch(`${API_BASE}/api/v1/dashboard/todolist/completed/${encodeURIComponent(id)}`, { method: "DELETE" }),
        ),
      );
    } catch {
      // noop
    } finally {
      // Reload so the backend-hidden tasks are also gone from server-side data
      await load(true);
      setDeleteAllPending(false);
    }
  }, [completedTasks, load]);

  // ── Derived data ────────────────────────────────────────────────────────────

  const allQueueItems = useMemo(() => Array.isArray(agentQueue?.items) ? agentQueue!.items : [], [agentQueue]);

  // Time-horizon buckets
  const futureItems = useMemo(
    () => allQueueItems.filter((i) => ["open", "parked", "blocked"].includes(String(i.status || "open"))),
    [allQueueItems],
  );
  const nowItems = useMemo(
    () => allQueueItems.filter((i) => ["in_progress", "needs_review"].includes(String(i.status || ""))),
    [allQueueItems],
  );

  const completedRows = useMemo(
    () => (Array.isArray(completedTasks?.items) ? completedTasks!.items : []),
    [completedTasks],
  );
  const visibleCompletedRows = useMemo(
    () => completedRows.filter((r) => !deletedTaskIds.has(r.task_id)),
    [completedRows, deletedTaskIds],
  );

  // Allocation breakdown: source_kind counts
  const allocationBySource = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const item of allQueueItems) {
      const k = String(item.source_kind || "internal");
      counts[k] = (counts[k] || 0) + 1;
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [allQueueItems]);

  // Allocation breakdown: project_key counts
  const allocationByProject = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const item of allQueueItems) {
      const k = String(item.project_key || "—");
      counts[k] = (counts[k] || 0) + 1;
    }
    return Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 8);
  }, [allQueueItems]);

  const agentMetrics1h = agentActivity?.metrics?.["1h"];
  const agentMetrics24h = agentActivity?.metrics?.["24h"];

  const completionRate24h = useMemo(() => {
    const completed = agentMetrics24h?.completed || 0;
    const rejected = agentMetrics24h?.rejected || 0;
    const total = completed + rejected;
    if (!total) return null;
    return Math.round((completed / total) * 100);
  }, [agentMetrics24h]);

  const dispatchThreshold = Number(overview?.queue_health?.threshold || 0);

  // Heartbeat alert logic: only surface when something is interesting
  const heartbeatAlerts = useMemo(() => {
    const hb = overview?.heartbeat;
    if (!hb) return [];
    const alerts: string[] = [];
    if (!hb.enabled) alerts.push("Heartbeat disabled");
    if (hb.busy_sessions > 0) alerts.push(`${hb.busy_sessions} busy session${hb.busy_sessions > 1 ? "s" : ""}`);
    const nextRun = hb.nearest_next_run_epoch;
    if (nextRun) {
      const secsUntil = nextRun - Math.floor(Date.now() / 1000);
      if (secsUntil < 0 && Math.abs(secsUntil) > 120) {
        alerts.push("Heartbeat overdue");
      }
    }
    if (hb.session_state_count === 0 && hb.session_count === 0) alerts.push("No sessions running");
    return alerts;
  }, [overview?.heartbeat]);

  // ── Loading state ────────────────────────────────────────────────────────────

  if (loading) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-muted-foreground">
        <div className="h-8 w-8 animate-spin rounded-full border-2 border-border border-t-sky-400" />
        <span className="text-sm">Loading Task Command Center…</span>
      </div>
    );
  }

  // ── Sub-renders ───────────────────────────────────────────────────────────────

  const renderTaskCard = (item: AgentQueueItem, idx: number, showActions = true) => {
    const isPending = actionPendingTaskId === item.task_id;
    return (
      <article
        key={item.task_id}
        className={`rounded-lg border bg-background/60 p-3 transition-colors hover:border-border/80 ${
          item.must_complete
            ? "border-l-2 border-l-rose-500/70 border-t-slate-800/80 border-r-slate-800/80 border-b-slate-800/80"
            : "border-border/70"
        }`}
      >
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5 mb-1">
              <span className="text-[10px] font-bold text-muted tabular-nums">#{idx + 1}</span>
              {sourceKindPill(item.source_kind)}
              {item.must_complete ? (
                <span className="rounded border border-red-400/30 bg-red-400/10 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-red-400/80">
                  Must Complete
                </span>
              ) : null}
            </div>
            <h3 className="font-semibold text-foreground text-sm leading-snug">
              {(() => {
                const href = taskSourceUrl(item.task_id, item.source_kind, item.url);
                if (href) {
                  const isExternal = href.startsWith("http");
                  return isExternal ? (
                    <a href={href} target="_blank" rel="noopener noreferrer"
                       className="hover:text-sky-300 hover:underline transition-colors" title="Open in source">
                      {item.title}
                    </a>
                  ) : (
                    <Link href={href} className="hover:text-sky-300 hover:underline transition-colors" title="Open reference">
                      {item.title}
                    </Link>
                  );
                }
                return item.title;
              })()}
            </h3>
            {item.description ? (
              <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{item.description}</p>
            ) : null}
          </div>
          <div className="text-right text-[10px] shrink-0">
            <div className={`font-semibold ${priorityColor(item.priority)}`}>{priorityText(item.priority)}</div>
            {item.score !== undefined ? (
              <div className="text-muted-foreground mt-0.5">score {item.score} · Q {item.score_confidence ?? 0}</div>
            ) : null}
          </div>
        </div>

        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
          {item.project_key ? <span className="text-muted-foreground">{item.project_key}</span> : null}
          {item.due_at ? (
            <><span>•</span><span className="text-accent">Due {item.due_at}</span></>
          ) : null}
          {item.updated_at ? (
            <><span>•</span><span>Updated {formatTs(item.updated_at)}</span></>
          ) : null}
          {dispatchThreshold > 0 && Number(item.score ?? 0) < dispatchThreshold ? (
            <><span>•</span><span className="text-accent">below threshold {dispatchThreshold}</span></>
          ) : null}
        </div>

        {showActions ? (
          <div className="mt-2 flex flex-wrap items-center gap-1.5">
            <button
              onClick={() => void handleTaskAction(item.task_id, "complete")}
              disabled={isPending}
              className="rounded border border-indigo-700/60 bg-indigo-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-indigo-200 hover:bg-indigo-900/35 disabled:opacity-50"
            >
              Complete
            </button>
            <button
              onClick={() => void handleWakeHeartbeat(item.task_id)}
              disabled={wakePending}
              className="rounded border border-primary/30/60 bg-primary/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-primary/80 hover:bg-primary/20 disabled:opacity-50"
            >
              {wakePending ? "Queueing…" : "Dispatch Now"}
            </button>
            <div className="relative">
              <button
                onClick={() => setOpenActionMenuId(openActionMenuId === item.task_id ? null : item.task_id)}
                className="rounded border border-border bg-card/80 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-foreground/80 hover:bg-card/50"
              >
                ▾
              </button>
              {openActionMenuId === item.task_id && (
                <div className="absolute right-0 top-full z-10 mt-1 flex w-32 flex-col gap-1 rounded border border-border bg-background p-1 shadow-xl">
                  {item.status === "open" && (
                    <button
                      onClick={() => void handleTaskAction(item.task_id, "seize")}
                      disabled={isPending}
                      className="w-full rounded px-2 py-1 text-left text-[10px] font-semibold uppercase tracking-wide text-primary/80 hover:bg-primary/20 disabled:opacity-50"
                    >
                      Seize
                    </button>
                  )}
                  <button
                    onClick={() => void handleTaskAction(item.task_id, "review")}
                    disabled={isPending}
                    className="w-full rounded px-2 py-1 text-left text-[10px] font-semibold uppercase tracking-wide text-foreground/80 hover:bg-card disabled:opacity-50"
                  >
                    Mark Review
                  </button>
                  <button
                    onClick={() => void handleTaskAction(item.task_id, "block")}
                    disabled={isPending}
                    className="w-full rounded px-2 py-1 text-left text-[10px] font-semibold uppercase tracking-wide text-amber-200 hover:bg-amber-900/35 disabled:opacity-50"
                  >
                    Block
                  </button>
                  <button
                    onClick={() => void handleTaskAction(item.task_id, "park")}
                    disabled={isPending}
                    className="w-full rounded px-2 py-1 text-left text-[10px] font-semibold uppercase tracking-wide text-red-400/80 hover:bg-red-400/20 disabled:opacity-50"
                  >
                    Park
                  </button>
                </div>
              )}
            </div>
          </div>
        ) : null}
      </article>
    );
  };

  const renderCompletedCard = (item: CompletedTaskItem) => (
    <article
      key={`completed-${item.task_id}`}
      className="group relative rounded-lg border border-border/70 bg-background/60 p-3 transition-colors hover:border-border/80"
      onMouseEnter={() => setHoveredDeleteId(item.task_id)}
      onMouseLeave={() => setHoveredDeleteId(null)}
    >
      <button
        onClick={() => void handleDeleteCompletedTask(item.task_id)}
        className={`absolute right-2 top-2 rounded p-1 text-muted transition-opacity hover:bg-red-400/15 hover:text-secondary ${
          hoveredDeleteId === item.task_id ? "opacity-100" : "opacity-0"
        }`}
        title="Delete"
      >
        🗑
      </button>
      <div className="flex items-start justify-between gap-2 pr-6">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-1.5 mb-1">
            {sourceKindPill(item.source_kind)}
          </div>
          <h3 className="truncate font-semibold text-foreground text-sm">
            {(() => {
              // Prefer session link, then Todoist/source URL
              const sessionHref = item.links?.session_href;
              const sourceHref = taskSourceUrl(item.task_id, item.source_kind);
              const href = sessionHref || sourceHref;
              if (href) {
                const isExternal = href.startsWith("http");
                return isExternal ? (
                  <a href={href} target="_blank" rel="noopener noreferrer"
                     className="hover:text-sky-300 hover:underline transition-colors" title="Open in source">
                    {item.title}
                  </a>
                ) : (
                  <Link href={href} className="hover:text-sky-300 hover:underline transition-colors" title="Open session">
                    {item.title}
                  </Link>
                );
              }
              return item.title;
            })()}
          </h3>
          {item.description ? <p className="mt-1 text-xs text-muted-foreground line-clamp-2">{item.description}</p> : null}
        </div>
        <div className={`text-right text-[10px] shrink-0 font-semibold ${priorityColor(item.priority)}`}>
          {priorityText(item.priority)}
        </div>
      </div>
      <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
        {item.project_key ? <span className="text-muted-foreground">{item.project_key}</span> : null}
        <span>•</span>
        <span>Done {formatTs(item.completed_at || item.updated_at)}</span>
        {item.last_assignment?.agent_id ? (
          <><span>•</span><span className="text-foreground/80">{item.last_assignment.agent_id}</span></>
        ) : null}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        <button
          onClick={() => void handleOpenTaskHistory(item.task_id)}
          disabled={taskHistoryLoadingId === item.task_id}
          className="rounded border border-sky-700/60 bg-sky-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-sky-200 hover:bg-sky-900/35 disabled:opacity-50"
        >
          {taskHistoryLoadingId === item.task_id ? "Loading…" : "Review"}
        </button>
        <button
          onClick={() => setSelectedTaskDetails(item)}
          className="rounded border border-fuchsia-700/60 bg-fuchsia-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-fuchsia-200 hover:bg-fuchsia-900/35"
        >
          Inspect
        </button>
        {item.links?.session_href ? (
          <Link
            href={String(item.links.session_href)}
            className="rounded border border-indigo-700/60 bg-indigo-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-indigo-200 hover:bg-indigo-900/35"
          >
            Session
          </Link>
        ) : null}
        {item.links?.run_log_href ? (
          <a
            href={String(item.links.run_log_href)}
            className="rounded border border-primary/30/60 bg-primary/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-primary/80 hover:bg-primary/20"
          >
            Run Log
          </a>
        ) : null}
      </div>
    </article>
  );

  // ── Task details modal ────────────────────────────────────────────────────────

  const renderTaskDetailsModal = () => {
    if (!selectedTaskDetails) return null;
    return (
      <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
        <div className="flex max-h-full w-full max-w-4xl flex-col rounded-xl border border-border bg-background shadow-2xl">
          <div className="flex items-center justify-between border-b border-border bg-background/50 p-4">
            <div>
              <h2 className="text-lg font-semibold text-foreground">Task Details</h2>
              <p className="text-xs text-muted-foreground">{selectedTaskDetails.task_id}</p>
            </div>
            <button
              onClick={() => setSelectedTaskDetails(null)}
              className="rounded p-1 text-muted-foreground transition-colors hover:bg-card hover:text-foreground"
            >
              ✕
            </button>
          </div>
          <div className="overflow-y-auto p-4 text-sm text-foreground/80">
            <pre className="break-all rounded border border-border bg-background p-4 font-mono text-[11px] text-primary whitespace-pre-wrap">
              {JSON.stringify(selectedTaskDetails, null, 2)}
            </pre>
          </div>
          <div className="flex-none flex justify-end border-t border-border bg-background/50 p-4">
            <button
              onClick={() => setSelectedTaskDetails(null)}
              className="rounded border border-border bg-card px-4 py-2 text-sm font-semibold text-foreground transition-colors hover:bg-card/50"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    );
  };

  // ── Task history panel ────────────────────────────────────────────────────────

  const renderTaskHistoryPanel = () => (
    <section className="rounded-xl border border-border bg-background/70 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-sky-300">Task History</h2>
          <p className="text-xs text-muted-foreground">Assignment/evaluation trail and links to session artifacts.</p>
        </div>
        {taskHistory ? (
          <button
            onClick={() => setTaskHistory(null)}
            className="rounded border border-border bg-card/80 px-2 py-1 text-[10px] uppercase tracking-wide text-foreground/80 hover:bg-card/50"
          >
            Clear
          </button>
        ) : null}
      </div>
      {!taskHistory ? (
        <p className="text-xs text-muted-foreground italic">Select "Review" on any task to load run history.</p>
      ) : (
        <div className="space-y-3 text-xs">
          <div className="rounded border border-border/70 bg-background/50 p-2">
            <div className="font-semibold text-foreground">{taskHistory.task?.title || taskHistory.task?.task_id || "Task"}</div>
            <div className="mt-1 text-muted-foreground">{taskHistory.task?.task_id}</div>
          </div>
          <div className="rounded border border-border/70 bg-background/50 p-2">
            <div className="mb-1 text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
              Assignments ({taskHistory.assignments?.length || 0})
            </div>
            {(taskHistory.assignments || []).length === 0 ? (
              <p className="text-muted-foreground">No assignment history.</p>
            ) : (
              <div className="space-y-1.5">
                {(taskHistory.assignments || []).slice(0, 10).map((row) => (
                  <div key={row.assignment_id} className="rounded border border-border bg-background/50 px-2 py-1.5">
                    <div className="text-foreground">
                      <span className="font-semibold">{row.agent_id || "unknown-agent"}</span> · {row.state}
                    </div>
                    <div className="text-[10px] text-muted-foreground">
                      started {formatTs(row.started_at)} · ended {formatTs(row.ended_at)}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                      {row.links?.session_href ? (
                        <Link
                          href={String(row.links.session_href)}
                          className="rounded border border-indigo-700/60 bg-indigo-900/20 px-2 py-0.5 text-[10px] uppercase tracking-wide text-indigo-200 hover:bg-indigo-900/35"
                        >
                          Session
                        </Link>
                      ) : null}
                      {row.links?.run_log_href ? (
                        <a
                          href={String(row.links.run_log_href)}
                          className="rounded border border-primary/30/60 bg-primary/10 px-2 py-0.5 text-[10px] uppercase tracking-wide text-primary/80 hover:bg-primary/20"
                        >
                          Run Log
                        </a>
                      ) : null}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
          <div className="rounded border border-border/70 bg-background/50 p-2">
            <div className="mb-1 text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
              Evaluations ({taskHistory.evaluations?.length || 0})
            </div>
            {(taskHistory.evaluations || []).length === 0 ? (
              <p className="text-muted-foreground">No evaluation records.</p>
            ) : (
              <div className="space-y-1.5">
                {(taskHistory.evaluations || []).slice(0, 12).map((row) => (
                  <div key={row.id} className="rounded border border-border bg-background/50 px-2 py-1.5">
                    <div className="text-foreground">
                      <span className="font-semibold">{row.decision || "n/a"}</span> · {row.reason || "n/a"}
                    </div>
                    <div className="text-[10px] text-muted-foreground">
                      score {row.score ?? 0} ({row.score_confidence ?? 0}) · {formatTs(row.evaluated_at)}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>
      )}
    </section>
  );



  // ── Main render ────────────────────────────────────────────────────────────────

  return (
    <div className="relative flex h-full flex-col gap-4 pb-6" onClick={() => setOpenActionMenuId(null)}>
      {renderTaskDetailsModal()}

      {/* ── Header ── */}
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Task Command Center</h1>
          <p className="text-sm text-muted-foreground">Mission allocation across past · current · future work</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-[11px] text-muted-foreground tabular-nums">
            {refreshing ? "Refreshing…" : `Auto-refresh in ${countdown}s`}
          </span>
          <button
            onClick={() => { setCountdown(AUTO_REFRESH_SECONDS); void load(true); }}
            className="rounded border border-border bg-card/80 px-3 py-1.5 text-xs text-foreground/80 hover:bg-card/50"
          >
            Refresh
          </button>
          <button
            onClick={() => void handleWakeHeartbeat()}
            disabled={wakePending}
            className="rounded border border-primary/30/60 bg-primary/10 px-3 py-1.5 text-xs font-semibold text-primary/80 hover:bg-primary/20 disabled:opacity-50"
          >
            {wakePending ? "Queueing…" : "Run Heartbeat"}
          </button>
          <Link
            href="/dashboard/approvals"
            className="rounded border border-amber-700/60 bg-amber-900/20 px-3 py-1.5 text-xs font-semibold text-amber-200 hover:bg-amber-900/35"
          >
            Approvals
            {(approvalsHighlight?.pending_count || 0) > 0 ? (
              <span className="ml-1.5 rounded-full bg-amber-600 px-1.5 py-0.5 text-[9px] font-bold text-white">
                {approvalsHighlight!.pending_count}
              </span>
            ) : null}
          </Link>
        </div>
      </div>

      {error ? (
        <div className="rounded-lg border border-red-400/30 bg-red-400/10 px-3 py-2 text-sm text-red-400/80">{error}</div>
      ) : null}

      {/* ── Summary Cards ── */}
      <section className="grid gap-2 sm:grid-cols-3 lg:grid-cols-5">
        <article className="rounded-lg border border-border bg-background/60 p-3">
          <p className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">
            Dispatch Eligible{dispatchThreshold > 0 ? ` (≥${dispatchThreshold})` : ""}
          </p>
          <p className="mt-1 text-2xl font-semibold text-foreground">{overview?.queue_health?.dispatch_eligible || 0}</p>
          <p className="mt-1 text-[11px] text-muted-foreground">{overview?.queue_health?.dispatch_queue_size || 0} in queue</p>
        </article>
        <article className="rounded-lg border border-border bg-background/60 p-3">
          <p className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">Active Agents</p>
          <p className="mt-1 text-2xl font-semibold text-primary/80">{agentActivity?.active_agents || 0}</p>
          <p className="mt-1 text-[11px] text-muted-foreground">{(agentActivity?.active_assignments || []).length} assignment{(agentActivity?.active_assignments || []).length !== 1 ? "s" : ""}</p>
        </article>
        <article className="rounded-lg border border-border bg-background/60 p-3">
          <p className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">Backlog Open</p>
          <p className="mt-1 text-2xl font-semibold text-foreground">{agentActivity?.backlog_open || 0}</p>
          <p className="mt-1 text-[11px] text-muted-foreground">total queued</p>
        </article>
        <article className="rounded-lg border border-border bg-background/60 p-3">
          <p className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">Approvals Pending</p>
          <p className="mt-1 text-2xl font-semibold text-amber-200">{approvalsHighlight?.pending_count || 0}</p>
          <p className="mt-1 text-[11px] text-muted-foreground">awaiting decision</p>
        </article>
        <article className="rounded-lg border border-border bg-background/60 p-3">
          <p className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">Completion Rate</p>
          <p className={`mt-1 text-2xl font-semibold ${completionRate24h !== null ? (completionRate24h >= 70 ? "text-primary/80" : completionRate24h >= 40 ? "text-amber-200" : "text-secondary") : "text-muted-foreground"}`}>
            {completionRate24h !== null ? `${completionRate24h}%` : "—"}
          </p>
          <p className="mt-1 text-[11px] text-muted-foreground">24h completed / rejected</p>
        </article>
      </section>

      {/* ── NOW: Current Assignments ── */}
      <section className="rounded-xl border border-border bg-background/70 p-4">
        <div className="mb-3 flex items-center gap-2">
          <span className="text-base">⚡</span>
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-primary">
            Now — Active Assignments ({(agentActivity?.active_assignments || []).length})
          </h2>
        </div>
        {(agentActivity?.active_assignments || []).length === 0 ? (
          <p className="text-sm text-muted italic">No agents currently working. Queue a heartbeat to dispatch work.</p>
        ) : (
          <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-3">
            {(agentActivity?.active_assignments || []).map((a) => (
              <div
                key={a.assignment_id}
                className="rounded-lg border border-primary/25 bg-primary/10 p-3"
              >
                <div className="flex items-start justify-between gap-2">
                  <div>
                    <div className="text-[10px] font-semibold uppercase tracking-wide text-primary">{a.agent_id}</div>
                    <div className="mt-1 text-sm font-medium text-foreground leading-snug">{a.title}</div>
                  </div>
                  <span className={`text-[10px] shrink-0 font-semibold ${priorityColor(a.priority)}`}>{priorityText(a.priority)}</span>
                </div>
                <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-muted-foreground">
                  {a.project_key ? <span className="text-muted-foreground">{a.project_key}</span> : null}
                  <span>•</span>
                  <span>Started {formatTs(a.started_at)}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ── Agent Efficiency Strip ── */}
      <section className="rounded-xl border border-border bg-background/70 p-3">
        <div className="flex flex-wrap items-center gap-4 text-xs">
          <span className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground shrink-0">Agent Efficiency</span>
          <div className="flex flex-wrap gap-4">
            <span className="text-muted-foreground">1h: <strong className="text-foreground">{agentMetrics1h?.seized || 0}</strong> seized · <strong className="text-primary">{agentMetrics1h?.completed || 0}</strong> done · <strong className="text-secondary">{agentMetrics1h?.rejected || 0}</strong> rejected</span>
            <span className="text-muted-foreground">24h: <strong className="text-foreground">{agentMetrics24h?.seized || 0}</strong> seized · <strong className="text-primary">{agentMetrics24h?.completed || 0}</strong> done · <strong className="text-secondary">{agentMetrics24h?.rejected || 0}</strong> rejected</span>
          </div>
        </div>
      </section>

      {/* ── Kanban Time Horizon Board ── */}
      <div className="grid gap-3 lg:grid-cols-3" onClick={(e) => e.stopPropagation()}>
        {/* FUTURE */}
        <KanbanCol
          label="Future"
          emoji="📅"
          count={futureItems.length}
          accentClass="border-border"
          headerClass="text-sky-300"
          emptyText="No queued tasks."
        >
          {futureItems.map((item, idx) => renderTaskCard(item, idx, true))}
        </KanbanCol>

        {/* NOW (in-progress from queue) */}
        <KanbanCol
          label="In Progress"
          emoji="⚡"
          count={nowItems.length}
          accentClass="border-primary/25"
          headerClass="text-primary"
          emptyText="Nothing actively in progress."
        >
          {nowItems.map((item, idx) => renderTaskCard(item, idx, true))}
        </KanbanCol>

        {/* PAST */}
        <KanbanCol
          label="Past"
          emoji="✅"
          count={visibleCompletedRows.length}
          accentClass="border-border"
          headerClass="text-foreground/80"
          emptyText="No completed tasks yet."
        >
          <>
            {visibleCompletedRows.length > 1 ? (
              <div className="flex justify-end">
                <button
                  onClick={() => void handleDeleteAllCompleted()}
                  disabled={deleteAllPending}
                  className="mb-1 rounded border border-red-400/30 bg-red-400/10 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-secondary hover:bg-red-400/15 disabled:opacity-50"
                >
                  {deleteAllPending ? "Clearing…" : "🗑 Clear All"}
                </button>
              </div>
            ) : null}
            {visibleCompletedRows.slice(0, 20).map((item) => renderCompletedCard(item))}
          </>
        </KanbanCol>
      </div>

      {/* ── Allocation Breakdown ── */}
      {allQueueItems.length > 0 ? (
        <section className="rounded-xl border border-border bg-background/70 p-4">
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-[0.16em] text-foreground/80">
            Work Allocation
          </h2>
          <div className="grid gap-6 sm:grid-cols-2">
            <div>
              <h3 className="mb-2 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">By Source</h3>
              <div className="space-y-2">
                {allocationBySource.map(([kind, count]) => {
                  const pct = Math.round((count / allQueueItems.length) * 100);
                  return (
                    <div key={kind} className="grid items-center gap-3 text-xs" style={{ gridTemplateColumns: "8rem 1fr 4rem" }}>
                      <div className="overflow-hidden">{sourceKindPill(kind)}</div>
                      <div className="rounded-full bg-card/60 h-1.5 min-w-0">
                        <div className="h-1.5 rounded-full bg-sky-600/60 transition-all" style={{ width: `${pct}%` }} />
                      </div>
                      <span className="text-right text-muted-foreground tabular-nums whitespace-nowrap">{count} ({pct}%)</span>
                    </div>
                  );
                })}
              </div>
            </div>
            <div>
              <h3 className="mb-2 text-[10px] uppercase tracking-[0.14em] text-muted-foreground">By Project</h3>
              <div className="space-y-1.5">
                {allocationByProject.map(([proj, count]) => {
                  const pct = Math.round((count / allQueueItems.length) * 100);
                  return (
                    <div key={proj} className="flex items-center gap-2 text-xs">
                      <span className="w-28 shrink-0 truncate text-foreground/80">{proj}</span>
                      <div className="flex-1 rounded-full bg-card/60 h-1.5">
                        <div
                          className="h-1.5 rounded-full bg-secondary/20"
                          style={{ width: `${pct}%` }}
                        />
                      </div>
                      <span className="w-14 text-right text-muted-foreground tabular-nums">{count} ({pct}%)</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </section>
      ) : null}

      {/* ── Task History Detail ── */}
      {renderTaskHistoryPanel()}

      {/* ── Heartbeat Status (only when interesting) ── */}
      {heartbeatAlerts.length > 0 ? (
        <section className="rounded-xl border border-amber-800/40 bg-amber-950/20 p-3">
          <div className="flex flex-wrap items-center gap-3">
            <span className="text-[10px] uppercase tracking-[0.16em] text-accent shrink-0">Heartbeat</span>
            {heartbeatAlerts.map((alert) => (
              <span key={alert} className="rounded border border-amber-700/50 bg-amber-900/20 px-2 py-0.5 text-[11px] text-amber-200">
                {alert}
              </span>
            ))}
            <span className="text-[10px] text-muted-foreground">
              next {formatEpochTs(overview?.heartbeat?.nearest_next_run_epoch)} · interval {formatEvery(overview?.heartbeat?.heartbeat_effective_interval_seconds ?? overview?.heartbeat?.effective_default_every_seconds)}
            </span>
          </div>
        </section>
      ) : null}
    </div>
  );
}
