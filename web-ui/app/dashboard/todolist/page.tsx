"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { formatDistanceToNow, parseISO } from "date-fns";

const API_BASE = "/api/dashboard/gateway";
const AUTO_REFRESH_SECONDS = 30;

/* ── KCD accent color class mapping helpers ──────────────────────── */

/** Map a priority number to a Tailwind text-color class */
function priorityColorClass(priority?: number): string {
  const p = Number(priority || 1);
  if (p >= 4) return "text-kcd-red";
  if (p === 3) return "text-kcd-amber";
  if (p === 2) return "text-kcd-cyan";
  return "text-kcd-text-muted";
}

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
  source_ref?: string;
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
  labels?: string[];
  updated_at?: string;
  completed_at?: string;
  source_kind?: string;
  source_ref?: string;
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
  const colorMap: Record<string, string> = {
    task_hub: "bg-kcd-cyan/10 text-kcd-cyan",
    internal: "bg-kcd-cyan/10 text-kcd-cyan",
    approval: "bg-kcd-amber/10 text-kcd-amber",
    email: "bg-kcd-indigo/10 text-kcd-indigo",
    csi: "bg-kcd-text-muted/10 text-kcd-text-muted",
  };
  const cls = colorMap[k] ?? "bg-kcd-text-muted/10 text-kcd-text-muted";
  return (
    <span className={`font-mono text-[9px] font-bold tracking-[0.08em] px-1.5 py-0.5 uppercase ${cls}`}>
      {k}
    </span>
  );
}

function isGatewayUpstreamUnavailable(status: number, detail: string): boolean {
  return status === 502 && detail.toLowerCase().includes("gateway upstream unavailable");
}

/** Derive the external/internal reference URL for a task based on its source. */
function taskSourceUrl(taskId: string, sourceKind?: string, explicitUrl?: string, sourceRef?: string): string | null {
  if (explicitUrl) return explicitUrl;
  const k = String(sourceKind || "").toLowerCase();
  if (k === "approval") {
    return "/dashboard/approvals";
  }
  if (k === "email" && sourceRef) {
    const threadId = sourceRef.startsWith("agentmail_thread:") ? sourceRef.slice(17) : sourceRef;
    if (threadId) {
      return `/dashboard/mail?thread=${encodeURIComponent(threadId)}`;
    }
  }
  return null;
}

// ── Main Component ────────────────────────────────────────────────────────────

// ── KanbanCol (stable identity — must be outside the page component to
//    prevent React from remounting on every re-render, which would reset
//    scroll position inside each column) ───────────────────────────────────

type KanbanColProps = {
  label: string;
  icon: string;
  count: number;
  accentColor: string;
  emptyText: string;
  children: React.ReactNode;
};

function KanbanCol({ label, icon, count, accentColor, emptyText, children }: KanbanColProps) {
  return (
    <div className="flex flex-col bg-white/5 border-none rounded-none min-h-0 overflow-hidden transition-all duration-300">
      <div className="flex items-center justify-between px-3.5 py-2.5 border-b border-white/[0.06]">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-lg" style={{ color: accentColor }}>{icon}</span>
          <span className="font-mono text-[11px] font-bold tracking-[0.1em] uppercase" style={{ color: accentColor }}>{label}</span>
        </div>
        <span className="font-mono text-[10px] font-bold px-2 py-0.5 rounded-sm" style={{ background: `${accentColor}18`, color: accentColor }}>{count}</span>
      </div>
      <div className="flex-1 overflow-y-auto p-2.5 max-h-[60vh] scrollbar-thin">
        {count === 0 ? (
          <p className="text-xs text-kcd-text-muted italic p-2">{emptyText}</p>
        ) : (
          <div className="flex flex-col gap-2">{children}</div>
        )}
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
  const [selectedSessionDetail, setSelectedSessionDetail] = useState<any | null>(null);
  const [sessionDetailLoading, setSessionDetailLoading] = useState("");
  const [deletedTaskIds, setDeletedTaskIds] = useState<Set<string>>(() => {
    try {
      const stored = localStorage.getItem("ua.deleted_completed_tasks.v1");
      if (stored) return new Set(JSON.parse(stored) as string[]);
    } catch { /* ignore */ }
    return new Set();
  });
  const [deleteAllPending, setDeleteAllPending] = useState(false);
  const [hoveredDeleteId, setHoveredDeleteId] = useState<string | null>(null);
  const [quickAddTitle, setQuickAddTitle] = useState("");
  const [quickAddPending, setQuickAddPending] = useState(false);
  const [morningReport, setMorningReport] = useState<any>(null);
  const [morningReportExpanded, setMorningReportExpanded] = useState(() => new Date().getHours() < 12);

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

  const handleQuickAdd = useCallback(async () => {
    const title = quickAddTitle.trim();
    if (!title || quickAddPending) return;
    setQuickAddPending(true);
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/todolist/tasks`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      if (!res.ok) throw new Error("Failed to add task");
      setQuickAddTitle("");
      await load(true);
    } catch (err: any) {
      setError(err?.message || "Failed to add task.");
    } finally {
      setQuickAddPending(false);
    }
  }, [quickAddTitle, quickAddPending, load]);

  // Fetch morning report on mount — time-aware (shows overnight agent activity)
  useEffect(() => {
    fetch(`${API_BASE}/api/v1/dashboard/todolist/morning-report`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => { if (data?.report) setMorningReport(data.report); })
      .catch(() => {});
  }, []);

  const handleOpenSession = useCallback(async (sessionId: string) => {
    setSessionDetailLoading(sessionId);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/ops/sessions/${encodeURIComponent(sessionId)}`,
        { cache: "no-store" },
      );
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(String(payload?.detail || `Session fetch failed (${res.status})`));
      }
      const payload = await res.json();
      setSelectedSessionDetail(payload?.session || payload);
      setError("");
    } catch (err: any) {
      setError(err?.message || "Failed to load session details.");
    } finally {
      setSessionDetailLoading("");
    }
  }, []);

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
      <div className="flex flex-col h-full bg-kcd-bg font-display p-5 gap-4">
        <div className="h-14 rounded-xl bg-kcd-surface-dim/80 animate-pulse" />
        <div className="h-12 rounded-lg bg-kcd-surface-dim/60 animate-pulse" />
        <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
          {[...Array(5)].map((_, i) => <div key={i} className="h-20 rounded-lg bg-kcd-surface-dim/60 animate-pulse" style={{ animationDelay: `${i * 100}ms` }} />)}
        </div>
        <div className="h-32 rounded-lg bg-kcd-surface-dim/50 animate-pulse" />
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-3 flex-1">
          {[...Array(3)].map((_, i) => <div key={i} className="h-60 rounded-lg bg-kcd-surface-dim/50 animate-pulse" style={{ animationDelay: `${i * 150}ms` }} />)}
        </div>
      </div>
    );
  }

  // (actionBtn / menuBtn helpers removed — all buttons now use Tailwind classes)

  // ── Sub-renders ───────────────────────────────────────────────────────────────

  const renderTaskCard = (item: AgentQueueItem, idx: number, showActions = true, onDelete?: (id: string) => void) => {
    const isPending = actionPendingTaskId === item.task_id;
    const pCls = priorityColorClass(item.priority);
    const isProcessing = String(item.status || "") === "in_progress";
    const isAwaitingReview = String(item.status || "") === "needs_review";
    return (
      <article
        key={item.task_id}
        className={[
          "group relative rounded-none p-3 transition-all duration-200 bg-[#0b1326]/70 backdrop-blur-md border border-white/10 hover:border-white/20 hover:-translate-y-[1px]",
          item.must_complete ? "border-l-2 border-l-kcd-red" : "",
          isProcessing ? "processing-bar border-l-2 border-l-kcd-green" : "",
          isAwaitingReview ? "border-l-2 border-l-kcd-amber" : "",
        ].filter(Boolean).join(" ")}
      >
        {/* Processing / Review Status Badge */}
        {isProcessing && (
          <div className="flex items-center gap-1.5 mb-2 px-2 py-1 bg-kcd-green/[0.08] border border-kcd-green/20 rounded-sm">
            <span className="inline-block w-2 h-2 rounded-full bg-kcd-green animate-pulse" />
            <span className="font-mono text-[9px] font-bold tracking-[0.1em] text-kcd-green uppercase">Processing</span>
            <span className="font-mono text-[9px] text-kcd-text-muted">· Agent working</span>
          </div>
        )}
        {isAwaitingReview && (
          <div className="flex items-center gap-1.5 mb-2 px-2 py-1 bg-kcd-amber/[0.08] border border-kcd-amber/20 rounded-sm">
            <span className="material-symbols-outlined text-xs text-kcd-amber">rate_review</span>
            <span className="font-mono text-[9px] font-bold tracking-[0.1em] text-kcd-amber uppercase">Awaiting Review</span>
            <span className="font-mono text-[9px] text-kcd-text-muted">· Run finished</span>
          </div>
        )}
        {onDelete && (
          <button onClick={() => onDelete(item.task_id)} disabled={isPending} title="Remove from queue"
            className="absolute right-2 top-2 bg-transparent border-none cursor-pointer text-kcd-text-muted opacity-0 group-hover:opacity-70 hover:!opacity-100 hover:!text-kcd-red transition-all duration-150 p-0.5">
            <span className="material-symbols-outlined text-base">delete</span>
          </button>
        )}
        <div className={`flex items-start justify-between gap-2 ${onDelete ? "pr-6" : ""}`}>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5 mb-1">
              <span className="font-mono text-[9px] font-bold text-kcd-text-muted">#{idx + 1}</span>
              {sourceKindPill(item.source_kind)}
              {item.must_complete && <span className="font-mono text-[9px] font-bold px-1.5 py-0.5 bg-kcd-red/10 text-kcd-red tracking-wider uppercase">MUST</span>}
            </div>
            <h3 className="text-[13px] font-semibold text-kcd-text leading-snug m-0">
              {(() => {
                const href = taskSourceUrl(item.task_id, item.source_kind, item.url, item.source_ref);
                if (href) {
                  const isExternal = href.startsWith("http");
                  return isExternal
                    ? <a href={href} target="_blank" rel="noopener noreferrer" className="text-kcd-text no-underline hover:text-kcd-cyan transition-colors">{item.title}</a>
                    : <Link href={href} className="text-kcd-text no-underline hover:text-kcd-cyan transition-colors">{item.title}</Link>;
                }
                return item.title;
              })()}
            </h3>
            {item.description && (
              <p className="mt-1 text-[11px] text-kcd-text-muted leading-snug line-clamp-2">{item.description}</p>
            )}
          </div>
          <div className="text-right shrink-0">
            <div className={`font-mono text-[10px] font-bold ${pCls}`}>{priorityText(item.priority)}</div>
            {item.score !== undefined && <div className="font-mono text-[9px] text-kcd-text-muted mt-0.5">score {item.score} · Q{item.score_confidence ?? 0}</div>}
          </div>
        </div>

        {/* Subtask / Question Queue / Pipeline Additions */}
        <div className="mt-2 text-xs">
          {item.source_kind === 'brainstorm' && (
            <div className="flex items-center gap-1 text-[9px] tracking-widest font-mono text-muted-foreground uppercase mb-2">
              <span className={item.labels?.includes('raw_idea') ? 'text-kcd-cyan' : ''}>raw_idea</span> {'→'} 
              <span className={item.labels?.includes('interviewing') ? 'text-kcd-cyan' : ''}>interviewing</span> {'→'} 
              <span className={item.labels?.includes('exploring') ? 'text-kcd-cyan' : ''}>exploring</span>
            </div>
          )}
          {item.labels?.includes('needs_input') && (
            <div className="mt-2 bg-black/20 p-2 border border-white/5 rounded-none">
              <span className="text-[10px] text-kcd-cyan uppercase tracking-widest font-mono mb-1 block">Question Queue: Pending Information</span>
              <div className="flex gap-2">
                <input type="text" placeholder="Provide answer..." className="text-xs bg-white/5 border border-white/10 px-2 py-1 flex-1 text-white outline-none rounded-none" />
                <button className="bg-kcd-cyan/20 text-kcd-cyan px-2 py-1 text-[10px] uppercase font-bold tracking-widest rounded-none hover:bg-kcd-cyan/30">Answer</button>
              </div>
            </div>
          )}
          {(item.labels?.includes('parent') || item.title.includes('Epic')) && (
            <details className="mt-2 text-kcd-text-muted cursor-pointer group/sub">
              <summary className="text-[10px] uppercase tracking-widest font-mono select-none hover:text-kcd-cyan transition-colors">↳ View Subtasks (2)</summary>
              <div className="pl-4 mt-1 border-l border-white/10 space-y-1 py-1">
                <div className="text-[11px] flex justify-between items-center"><span className="truncate">Research context</span> <span className="text-[9px] text-green-400">DONE</span></div>
                <div className="text-[11px] flex justify-between items-center"><span className="truncate">Draft proposal</span> <span className="text-[9px] text-kcd-amber">PENDING</span></div>
              </div>
            </details>
          )}
        </div>

        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 font-mono text-[10px] text-kcd-text-muted">
          {item.project_key && <span>{item.project_key}</span>}
          {item.due_at && <><span className="opacity-40">│</span><span className="text-kcd-amber">Due {item.due_at}</span></>}
          {item.updated_at && <><span className="opacity-40">│</span><span>Updated {formatTs(item.updated_at)}</span></>}
          {dispatchThreshold > 0 && Number(item.score ?? 0) < dispatchThreshold && (
            <><span className="opacity-40">│</span><span className="text-kcd-amber">below threshold {dispatchThreshold}</span></>
          )}
        </div>
        {showActions && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity duration-200" onClick={(e) => e.stopPropagation()}>
            <button onClick={() => void handleTaskAction(item.task_id, "complete")} disabled={isPending}
              className="px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-green/10 text-kcd-green border-none rounded-sm cursor-pointer hover:bg-kcd-green/20 transition-colors disabled:opacity-40">
              ✓ Complete
            </button>
            <button onClick={() => void handleWakeHeartbeat(item.task_id)} disabled={wakePending}
              className="px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-cyan/10 text-kcd-cyan border-none rounded-sm cursor-pointer hover:bg-kcd-cyan/20 transition-colors disabled:opacity-40">
              ⚡ {wakePending ? "Queueing…" : "Dispatch"}
            </button>
            <div className="relative">
              <button onClick={() => setOpenActionMenuId(openActionMenuId === item.task_id ? null : item.task_id)}
                className="px-2 py-1 font-mono text-[10px] font-bold bg-kcd-surface-high text-kcd-text-dim border-none rounded-sm cursor-pointer hover:bg-kcd-surface-bright transition-colors">▾</button>
              {openActionMenuId === item.task_id && (
                <div className="absolute right-0 top-full z-10 mt-1 w-32 flex flex-col gap-0.5 backdrop-blur-xl bg-kcd-surface-dim/95 border border-white/[0.1] rounded-md p-1 shadow-xl shadow-black/40 animate-fade-in">
                  {item.status === "open" && <button onClick={() => void handleTaskAction(item.task_id, "seize")} disabled={isPending} className="w-full text-left bg-transparent text-kcd-cyan border-none px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase cursor-pointer hover:bg-kcd-cyan/10 rounded-sm transition-colors">Seize</button>}
                  <button onClick={() => void handleTaskAction(item.task_id, "review")} disabled={isPending} className="w-full text-left bg-transparent text-kcd-text-dim border-none px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase cursor-pointer hover:bg-white/5 rounded-sm transition-colors">Review</button>
                  <button onClick={() => void handleTaskAction(item.task_id, "block")} disabled={isPending} className="w-full text-left bg-transparent text-kcd-amber border-none px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase cursor-pointer hover:bg-kcd-amber/10 rounded-sm transition-colors">Block</button>
                  <button onClick={() => void handleTaskAction(item.task_id, "park")} disabled={isPending} className="w-full text-left bg-transparent text-kcd-red border-none px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase cursor-pointer hover:bg-kcd-red/10 rounded-sm transition-colors">Park</button>
                </div>
              )}
            </div>
          </div>
        )}
      </article>
    );
  };

  const renderCompletedCard = (item: CompletedTaskItem) => {
    const pCls = priorityColorClass(item.priority);
    return (
      <article key={`completed-${item.task_id}`}
        className="group relative rounded-md p-3 transition-all duration-200 bg-kcd-surface-low border border-white/[0.15] hover:bg-kcd-surface-high/80">
        <button onClick={() => void handleDeleteCompletedTask(item.task_id)} title="Delete"
          className="absolute right-2 top-2 bg-transparent border-none cursor-pointer text-kcd-text-muted opacity-0 group-hover:opacity-70 hover:!opacity-100 hover:!text-kcd-red transition-all duration-150 p-0.5">
          <span className="material-symbols-outlined text-base">delete</span>
        </button>
        <div className="flex items-start justify-between gap-2 pr-6">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5 mb-1">{sourceKindPill(item.source_kind)}</div>
            <h3 className="text-[13px] font-semibold text-kcd-text truncate m-0">
              {(() => {
                const href = item.links?.session_href || taskSourceUrl(item.task_id, item.source_kind, undefined, item.source_ref);
                if (href) {
                  const isExternal = href.startsWith("http");
                  return isExternal
                    ? <a href={href} target="_blank" rel="noopener noreferrer" className="text-kcd-text no-underline hover:text-kcd-cyan transition-colors">{item.title}</a>
                    : <Link href={href} className="text-kcd-text no-underline hover:text-kcd-cyan transition-colors">{item.title}</Link>;
                }
                return item.title;
              })()}
            </h3>
            {item.description && <p className="mt-1 text-[11px] text-kcd-text-muted leading-snug line-clamp-2">{item.description}</p>}
          </div>
          <div className={`font-mono text-[10px] font-bold shrink-0 text-right ${pCls}`}>{priorityText(item.priority)}</div>
        </div>

        {/* Subtask / Question Queue / Pipeline Additions */}
        <div className="mt-2 text-xs">
          {item.source_kind === 'brainstorm' && (
            <div className="flex items-center gap-1 text-[9px] tracking-widest font-mono text-muted-foreground uppercase mb-2">
              <span className={item.labels?.includes('raw_idea') ? 'text-kcd-cyan' : ''}>raw_idea</span> {'→'} 
              <span className={item.labels?.includes('interviewing') ? 'text-kcd-cyan' : ''}>interviewing</span> {'→'} 
              <span className={item.labels?.includes('exploring') ? 'text-kcd-cyan' : ''}>exploring</span>
            </div>
          )}
          {item.labels?.includes('needs_input') && (
            <div className="mt-2 bg-black/20 p-2 border border-white/5 rounded-none">
              <span className="text-[10px] text-kcd-cyan uppercase tracking-widest font-mono mb-1 block">Question Queue: Pending Information</span>
              <div className="flex gap-2">
                <input type="text" placeholder="Provide answer..." className="text-xs bg-white/5 border border-white/10 px-2 py-1 flex-1 text-white outline-none rounded-none" />
                <button className="bg-kcd-cyan/20 text-kcd-cyan px-2 py-1 text-[10px] uppercase font-bold tracking-widest rounded-none hover:bg-kcd-cyan/30">Answer</button>
              </div>
            </div>
          )}
          {(item.labels?.includes('parent') || item.title.includes('Epic')) && (
            <details className="mt-2 text-kcd-text-muted cursor-pointer group/sub">
              <summary className="text-[10px] uppercase tracking-widest font-mono select-none hover:text-kcd-cyan transition-colors">↳ View Subtasks (2)</summary>
              <div className="pl-4 mt-1 border-l border-white/10 space-y-1 py-1">
                <div className="text-[11px] flex justify-between items-center"><span className="truncate">Research context</span> <span className="text-[9px] text-green-400">DONE</span></div>
                <div className="text-[11px] flex justify-between items-center"><span className="truncate">Draft proposal</span> <span className="text-[9px] text-kcd-amber">PENDING</span></div>
              </div>
            </details>
          )}
        </div>

        <div className="mt-1.5 flex flex-wrap items-center gap-1.5 font-mono text-[10px] text-kcd-text-muted">
          {item.project_key && <span>{item.project_key}</span>}
          <span className="opacity-40">│</span>
          <span>Done {formatTs(item.completed_at || item.updated_at)}</span>
          {item.last_assignment?.agent_id && <><span className="opacity-40">│</span><span className="text-kcd-text-dim">{item.last_assignment.agent_id}</span></>}
        </div>
        <div className="mt-2 flex flex-wrap items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity duration-200">
          <button onClick={() => void handleOpenTaskHistory(item.task_id)} disabled={taskHistoryLoadingId === item.task_id}
            className="px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-cyan/10 text-kcd-cyan border-none rounded-sm cursor-pointer hover:bg-kcd-cyan/20 transition-colors disabled:opacity-40">
            {taskHistoryLoadingId === item.task_id ? "Loading…" : "Review"}
          </button>
          <button onClick={() => setSelectedTaskDetails(item)}
            className="px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-indigo/10 text-kcd-indigo border-none rounded-sm cursor-pointer hover:bg-kcd-indigo/20 transition-colors">
            Inspect
          </button>
          {item.links?.session_id && (
            <Link href={`/dashboard/sessions?session_id=${encodeURIComponent(String(item.links!.session_id))}`}
              className="px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-emerald-500/10 text-emerald-400 no-underline border-none rounded-sm cursor-pointer hover:bg-emerald-500/20 transition-colors inline-flex items-center gap-1">
              <span className="text-[10px]">📂</span> Workspace
            </Link>
          )}
        </div>
      </article>
    );
  };

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

  // ── Session detail modal ──────────────────────────────────────────────────────

  const renderSessionDetailModal = () => {
    if (!selectedSessionDetail) return null;
    const s = selectedSessionDetail;
    const rows: Array<{ label: string; value: React.ReactNode }> = [
      { label: "Session ID", value: s.session_id || "—" },
      { label: "Status", value: s.status || "—" },
      { label: "Source", value: s.source || s.channel || "—" },
      { label: "Owner", value: s.owner || "—" },
      { label: "Description", value: s.description || "—" },
      { label: "Created", value: formatTs(s.created_at) || "—" },
      { label: "Last Activity", value: formatTs(s.last_activity) || "—" },
      { label: "Active Runs", value: String(s.active_runs ?? 0) },
      { label: "Active Connections", value: String(s.active_connections ?? 0) },
      { label: "Has Run Log", value: s.has_run_log ? "Yes" : "No" },
      { label: "Has Memory", value: s.has_memory ? "Yes" : "No" },
      { label: "Checkpoint", value: s.has_checkpoint ? "Available" : "None" },
    ];
    if (s.checkpoint_original_request) {
      rows.push({ label: "Original Request", value: s.checkpoint_original_request });
    }
    if (s.heartbeat_summary) {
      rows.push({ label: "Heartbeat", value: s.heartbeat_summary });
    }
    if (s.last_run_source) {
      rows.push({ label: "Last Run Source", value: s.last_run_source });
    }
    if (s.terminal_reason) {
      rows.push({ label: "Terminal Reason", value: s.terminal_reason });
    }

    const sessionTabHref = `/dashboard/sessions?session_id=${encodeURIComponent(s.session_id || "")}`;

    return (
      <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
        <div className="flex max-h-full w-full max-w-2xl flex-col rounded-xl border border-border bg-background shadow-2xl">
          <div className="flex items-center justify-between border-b border-border bg-background/50 p-4">
            <div>
              <h2 className="text-lg font-semibold text-foreground">Session Details</h2>
              <p className="text-xs text-muted-foreground">{s.session_id || ""}</p>
            </div>
            <button
              onClick={() => setSelectedSessionDetail(null)}
              className="rounded p-1 text-muted-foreground transition-colors hover:bg-card hover:text-foreground"
            >
              ✕
            </button>
          </div>
          <div className="overflow-y-auto p-4 text-sm">
            <div className="space-y-2">
              {rows.map((row, idx) => (
                <div key={idx} className="flex items-start gap-3 rounded border border-border/50 bg-background/40 px-3 py-2">
                  <span className="w-36 shrink-0 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{row.label}</span>
                  <span className="text-foreground/80 break-all text-[13px]">{row.value}</span>
                </div>
              ))}
            </div>
          </div>
          <div className="flex-none flex items-center justify-between border-t border-border bg-background/50 p-4">
            <Link
              href={sessionTabHref}
              className="text-xs text-sky-300 hover:text-sky-200 hover:underline transition-colors"
            >
              Open in Sessions Tab →
            </Link>
            <button
              onClick={() => setSelectedSessionDetail(null)}
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
            <div className="font-semibold text-foreground flex items-baseline gap-2 min-w-0">
              <span className="shrink-0">{taskHistory.task?.title || taskHistory.task?.task_id || "Task"}</span>
              {taskHistory.task?.description && (
                <span className="text-[11px] font-normal text-muted-foreground truncate">— {taskHistory.task.description.slice(0, 100)}{taskHistory.task.description.length > 100 ? "…" : ""}</span>
              )}
            </div>
            <div className="mt-1 flex items-center gap-2 text-muted-foreground">
              <span>{taskHistory.task?.task_id}</span>
              {taskHistory.task?.status && <span className="opacity-40">│</span>}
              {taskHistory.task?.status && <span className="text-[10px] uppercase tracking-wider">{taskHistory.task.status}</span>}
              {taskHistory.task?.score !== undefined && <><span className="opacity-40">│</span><span className="text-[10px]">score {taskHistory.task.score}</span></>}
            </div>
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
                      {(row.links?.session_id || row.session_id) ? (
                        <Link
                          href={`/dashboard/sessions?session_id=${encodeURIComponent(String(row.links?.session_id || row.session_id))}`}
                          className="rounded border border-emerald-700/60 bg-emerald-900/20 px-2 py-0.5 text-[10px] uppercase tracking-wide text-emerald-300 hover:bg-emerald-900/35 no-underline inline-flex items-center gap-1"
                        >
                          <span className="text-[9px]">📂</span> Workspace
                        </Link>
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
    <div className="relative flex flex-col h-full gap-0 bg-kcd-bg font-display text-kcd-text" onClick={() => setOpenActionMenuId(null)}>
      {renderTaskDetailsModal()}
      {renderSessionDetailModal()}

      {/* ── Header ── */}
      <header className="sticky top-0 z-30 flex flex-wrap items-center justify-between gap-3 px-5 py-3 border-b border-white/[0.06] backdrop-blur-xl bg-kcd-surface-dim/90 shadow-lg shadow-black/20">
        <div className="flex items-center gap-2.5">
          <span className="material-symbols-outlined text-[22px] text-kcd-cyan">task_alt</span>
          <div>
            <h1 className="font-mono text-[15px] font-bold tracking-[0.06em] text-kcd-cyan uppercase m-0">Task Hub</h1>
            <p className="text-[11px] text-kcd-text-muted m-0">{allQueueItems.length} open · {agentActivity?.active_agents || 0} active · {completionRate24h !== null ? `${completionRate24h}%` : "—"} rate</p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-mono text-[10px] text-kcd-text-muted">{refreshing ? "Refreshing…" : `next ${countdown}s`}</span>
          <button onClick={() => { setCountdown(AUTO_REFRESH_SECONDS); void load(true); }}
            className="flex items-center gap-1 px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-surface-high text-kcd-text-dim border-none rounded-sm cursor-pointer hover:bg-kcd-surface-bright transition-colors">
            <span className="material-symbols-outlined text-sm">refresh</span> Refresh
          </button>
          <button onClick={() => void handleWakeHeartbeat()} disabled={wakePending}
            className="flex items-center gap-1 px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-cyan/10 text-kcd-cyan border-none rounded-sm cursor-pointer hover:bg-kcd-cyan/20 transition-colors disabled:opacity-40">
            <span className="material-symbols-outlined text-sm">favorite</span> {wakePending ? "Queueing…" : "Heartbeat"}
          </button>
          <Link href="/dashboard/approvals"
            className="flex items-center gap-1 px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-amber/10 text-kcd-amber no-underline rounded-sm hover:bg-kcd-amber/20 transition-colors">
            Approvals
            {(approvalsHighlight?.pending_count || 0) > 0 && (
              <span className="ml-1 px-1.5 py-px bg-kcd-amber text-kcd-bg font-mono text-[9px] font-bold rounded-sm">{approvalsHighlight!.pending_count}</span>
            )}
          </Link>
        </div>
      </header>

      {error && <div className="mx-5 mt-3 px-3 py-2 bg-kcd-red/10 border border-kcd-red/20 rounded-md text-kcd-red text-xs animate-fade-in">{error}</div>}

      {/* ── Scrollable Content ── */}
      <div className="flex-1 overflow-y-auto px-5 pt-4 pb-6 space-y-4 scrollbar-thin">

        {/* ── Quick-Add Bar ── */}
        <div className="backdrop-blur-xl bg-kcd-surface-dim/90 border border-white/[0.1] rounded-xl shadow-xl shadow-black/20 p-3 flex items-center gap-3 transition-all duration-300 focus-within:border-kcd-cyan/30 focus-within:shadow-glow-cyan-md">
          <span className="material-symbols-outlined text-xl text-kcd-cyan/60">add_task</span>
          <input
            className="flex-1 bg-transparent text-kcd-text text-sm placeholder:text-kcd-text-muted/50 outline-none border-none"
            placeholder="Quick add a task — press Enter to submit"
            value={quickAddTitle}
            onChange={(e) => setQuickAddTitle(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && quickAddTitle.trim()) void handleQuickAdd(); }}
          />
          <button onClick={() => void handleQuickAdd()} disabled={!quickAddTitle.trim() || quickAddPending}
            className="px-4 py-1.5 bg-kcd-cyan/15 text-kcd-cyan font-mono text-[10px] font-bold tracking-wider uppercase rounded-md hover:bg-kcd-cyan/25 transition-all disabled:opacity-30 disabled:cursor-not-allowed border-none cursor-pointer">
            {quickAddPending ? "Adding…" : "Add Task"}
          </button>
        </div>

        {/* ── Morning Report Banner ── */}
        {morningReport && (
          <div className="backdrop-blur-md bg-kcd-surface-dim/70 border border-white/[0.06] rounded-lg overflow-hidden transition-all duration-300 animate-slide-in">
            <button onClick={() => setMorningReportExpanded(!morningReportExpanded)}
              className="w-full flex items-center justify-between px-4 py-2.5 bg-transparent border-none cursor-pointer text-left hover:bg-white/[0.02] transition-colors">
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined text-kcd-amber text-lg">auto_awesome</span>
                <span className="font-mono text-[11px] font-bold tracking-[0.08em] text-kcd-amber uppercase">Overnight Activity Report</span>
              </div>
              <span className={`material-symbols-outlined text-kcd-text-muted text-lg transition-transform duration-200 ${morningReportExpanded ? "rotate-180" : ""}`}>expand_more</span>
            </button>
            {morningReportExpanded && (
              <div className="px-4 pb-3 text-[12px] text-kcd-text-dim leading-relaxed border-t border-white/[0.04] pt-3 animate-fade-in">
                {morningReport.greeting && <p className="text-kcd-text font-medium mb-2">{morningReport.greeting}</p>}
                {morningReport.summary && <p className="text-kcd-text-dim">{morningReport.summary}</p>}
                {Array.isArray(morningReport.priorities) && morningReport.priorities.length > 0 && (
                  <div className="mt-2">
                    <span className="font-mono text-[9px] font-bold tracking-wider text-kcd-text-muted uppercase">Priorities</span>
                    <ul className="mt-1 space-y-1 list-none p-0 m-0">{morningReport.priorities.map((p: any, i: number) => (
                      <li key={i} className="text-[11px] text-kcd-text-dim">• {typeof p === "string" ? p : p.title || p.description || JSON.stringify(p)}</li>
                    ))}</ul>
                  </div>
                )}
                {!morningReport.greeting && !morningReport.summary && (
                  <pre className="text-[10px] text-kcd-text-muted font-mono whitespace-pre-wrap">{JSON.stringify(morningReport, null, 2)}</pre>
                )}
              </div>
            )}
          </div>
        )}

        {/* ── Summary Cards ── */}
        <section className="grid grid-cols-2 md:grid-cols-5 gap-3">
          {[
            { label: `Dispatch Eligible${dispatchThreshold > 0 ? ` (≥${dispatchThreshold})` : ""}`, value: overview?.queue_health?.dispatch_eligible || 0, sub: `${overview?.queue_health?.dispatch_queue_size || 0} in queue`, cls: "text-kcd-text" },
            { label: "Active Agents", value: agentActivity?.active_agents || 0, sub: `${(agentActivity?.active_assignments || []).length} assignments`, cls: "text-kcd-cyan" },
            { label: "Backlog Open", value: agentActivity?.backlog_open || 0, sub: "total queued", cls: "text-kcd-text" },
            { label: "Approvals Pending", value: approvalsHighlight?.pending_count || 0, sub: "awaiting decision", cls: "text-kcd-amber" },
            { label: "Completion Rate", value: completionRate24h !== null ? `${completionRate24h}%` : "—", sub: "24h completed / rejected", cls: completionRate24h !== null ? (completionRate24h >= 70 ? "text-kcd-green" : completionRate24h >= 40 ? "text-kcd-amber" : "text-kcd-red") : "text-kcd-text-muted" },
          ].map((card, i) => (
            <article key={card.label}
              className={`backdrop-blur-sm bg-kcd-surface-dim/70 border border-white/[0.06] rounded-lg p-3 hover:border-kcd-cyan/20 hover:shadow-glow-cyan transition-all duration-300 group animate-fade-in-stagger [animation-delay:${i * 80}ms]`}>
              <p className="font-mono text-[9px] font-bold tracking-[0.1em] text-kcd-text-muted uppercase m-0">{card.label}</p>
              <p className={`text-xl font-semibold mt-1 m-0 transition-colors group-hover:brightness-110 ${card.cls}`}>{card.value}</p>
              <p className="text-[10px] text-kcd-text-muted mt-1 m-0">{card.sub}</p>
            </article>
          ))}
        </section>

        {/* ── NOW: Current Assignments ── */}
        <section className="backdrop-blur-sm bg-kcd-surface-dim/70 border border-white/[0.06] rounded-lg p-4">
        <div className="flex items-center gap-2 mb-3">
          <span className="material-symbols-outlined text-lg text-kcd-cyan">bolt</span>
          <h2 className="font-mono text-[11px] font-bold tracking-[0.1em] text-kcd-cyan uppercase m-0">Now — Active ({(agentActivity?.active_assignments || []).length})</h2>
        </div>
        {(agentActivity?.active_assignments || []).length === 0 ? (
          <p className="text-xs text-kcd-text-muted italic">No agents currently working. Queue a heartbeat to dispatch work.</p>
        ) : (
          <div className="grid gap-2 grid-cols-1 sm:grid-cols-2 xl:grid-cols-3">
            {(agentActivity?.active_assignments || []).map((a) => {
              const aPCls = priorityColorClass(a.priority);
              return (
                <div key={a.assignment_id} className="bg-kcd-cyan/[0.08] border border-kcd-cyan/20 rounded-md p-3 hover:bg-kcd-cyan/[0.12] transition-colors">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <div className="font-mono text-[9px] font-bold tracking-wider text-kcd-cyan uppercase">{a.agent_id}</div>
                      <div className="text-[13px] font-medium text-kcd-text mt-1 leading-snug">{a.title}</div>
                    </div>
                    <span className={`font-mono text-[10px] font-bold shrink-0 ${aPCls}`}>{priorityText(a.priority)}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap items-center gap-1.5 font-mono text-[10px] text-kcd-text-muted">
                    {a.project_key && <span>{a.project_key}</span>}
                    <span className="opacity-40">│</span>
                    <span>Started {formatTs(a.started_at)}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </section>

      {/* ── Agent Efficiency Strip ── */}
      <section className="backdrop-blur-sm bg-kcd-surface-dim/70 border border-white/[0.06] rounded-lg px-4 py-2.5 flex flex-wrap items-center gap-4">
        <span className="font-mono text-[9px] font-bold tracking-[0.1em] text-kcd-text-muted uppercase shrink-0">Agent Efficiency</span>
        <div className="flex flex-wrap gap-4 font-mono text-[11px]">
          <span className="text-kcd-text-muted">1h: <strong className="text-kcd-text">{agentMetrics1h?.seized || 0}</strong> seized · <strong className="text-kcd-cyan">{agentMetrics1h?.completed || 0}</strong> done · <strong className="text-kcd-red">{agentMetrics1h?.rejected || 0}</strong> rejected</span>
          <span className="text-kcd-text-muted">24h: <strong className="text-kcd-text">{agentMetrics24h?.seized || 0}</strong> seized · <strong className="text-kcd-cyan">{agentMetrics24h?.completed || 0}</strong> done · <strong className="text-kcd-red">{agentMetrics24h?.rejected || 0}</strong> rejected</span>
        </div>
      </section>

      {/* ── Kanban Time Horizon Board ── */}
      <div className="grid gap-3 grid-cols-1 lg:grid-cols-3" onClick={(e) => e.stopPropagation()}>
        <KanbanCol label="Future" icon="schedule" count={futureItems.length} accentColor="#22D3EE" emptyText="No queued tasks.">
          {futureItems.map((item, idx) => renderTaskCard(item, idx, true))}
        </KanbanCol>
        <KanbanCol label="In Progress" icon="bolt" count={nowItems.length} accentColor="#4ADE80" emptyText="Nothing actively in progress.">
          {nowItems.map((item, idx) => renderTaskCard(item, idx, true, (id) => void handleTaskAction(id, "park")))}
        </KanbanCol>
        <KanbanCol label="Completed" icon="check_circle" count={visibleCompletedRows.length} accentColor="#4ADE80" emptyText="No completed tasks yet.">
          <>
            {visibleCompletedRows.length > 1 && (
              <div className="flex justify-end">
                <button onClick={() => void handleDeleteAllCompleted()} disabled={deleteAllPending}
                  className="px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-red/10 text-kcd-red border-none rounded-sm cursor-pointer hover:bg-kcd-red/20 transition-colors disabled:opacity-40">
                  {deleteAllPending ? "Clearing…" : "Clear All"}
                </button>
              </div>
            )}
            {visibleCompletedRows.slice(0, 20).map((item) => renderCompletedCard(item))}
          </>
        </KanbanCol>
      </div>

      {/* ── Allocation Breakdown ── */}
      {allQueueItems.length > 0 && (
        <section className="backdrop-blur-sm bg-kcd-surface-dim/70 border border-white/[0.06] rounded-lg p-4">
          <h2 className="font-mono text-[11px] font-bold tracking-[0.1em] text-kcd-text-dim uppercase m-0 mb-3">Work Allocation</h2>
          <div className="grid gap-6 grid-cols-1 md:grid-cols-2">
            <div>
              <h3 className="font-mono text-[9px] font-bold tracking-[0.1em] text-kcd-text-muted uppercase m-0 mb-2">By Source</h3>
              <div className="flex flex-col gap-2">
                {allocationBySource.map(([kind, count]) => {
                  const pct = Math.round((count / allQueueItems.length) * 100);
                  return (
                    <div key={kind} className="grid items-center gap-3 text-[11px]" style={{ gridTemplateColumns: "8rem 1fr 4rem" }}>
                      <div className="overflow-hidden">{sourceKindPill(kind)}</div>
                      <div className="bg-kcd-surface-bright h-1 min-w-0 rounded-full overflow-hidden">
                        <div className="h-1 bg-kcd-cyan rounded-full transition-all duration-500" style={{ width: `${pct}%` }} />
                      </div>
                      <span className="text-right text-kcd-text-muted font-mono text-[10px] whitespace-nowrap">{count} ({pct}%)</span>
                    </div>
                  );
                })}
              </div>
            </div>
            <div>
              <h3 className="font-mono text-[9px] font-bold tracking-[0.1em] text-kcd-text-muted uppercase m-0 mb-2">By Project</h3>
              <div className="flex flex-col gap-1.5">
                {allocationByProject.map(([proj, count]) => {
                  const pct = Math.round((count / allQueueItems.length) * 100);
                  return (
                    <div key={proj} className="flex items-center gap-2 text-[11px]">
                      <span className="w-28 shrink-0 truncate text-kcd-text-dim">{proj}</span>
                      <div className="flex-1 bg-kcd-surface-bright h-1 rounded-full overflow-hidden">
                        <div className="h-1 bg-kcd-indigo rounded-full transition-all duration-500" style={{ width: `${pct}%` }} />
                      </div>
                      <span className="w-14 text-right text-kcd-text-muted font-mono text-[10px]">{count} ({pct}%)</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </section>
      )}

      {/* ── Task History Detail ── */}
      {renderTaskHistoryPanel()}

      {/* ── Heartbeat Status ── */}
      {heartbeatAlerts.length > 0 && (
        <section className="bg-kcd-amber/[0.08] border border-kcd-amber/20 rounded-lg px-4 py-2.5 flex flex-wrap items-center gap-3">
          <span className="font-mono text-[9px] font-bold tracking-[0.1em] text-kcd-amber uppercase shrink-0">Heartbeat</span>
          {heartbeatAlerts.map((alert) => (
            <span key={alert} className="px-2 py-0.5 bg-kcd-amber/10 border border-kcd-amber/20 font-mono text-[10px] text-kcd-amber rounded-sm">{alert}</span>
          ))}
          <span className="font-mono text-[10px] text-kcd-text-muted">
            next {formatEpochTs(overview?.heartbeat?.nearest_next_run_epoch)} · interval {formatEvery(overview?.heartbeat?.heartbeat_effective_interval_seconds ?? overview?.heartbeat?.effective_default_every_seconds)}
          </span>
        </section>
      )}

      </div>{/* end scrollable content */}
    </div>
  );
}
