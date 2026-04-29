"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { formatDistanceToNow, parseISO } from "date-fns";
import { openOrFocusChatWindow } from "@/lib/chatWindow";
import { resolveTaskWorkspaceTarget } from "@/lib/taskWorkspaceTarget";

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
  board_lane?: string;
  assigned_agent_id?: string | null;
  assigned_session_id?: string | null;
  assignment_state?: string | null;
  requires_simone_review?: boolean;
  delivery_mode?: string | null;
  session_role?: string | null;
  run_kind?: string | null;
  canonical_execution_session_id?: string | null;
  canonical_execution_run_id?: string | null;
  canonical_execution_workspace?: string | null;
  links?: {
    workspace_name?: string | null;
    session_id?: string | null;
  } | null;
  reconciliation?: {
    orphaned_in_progress?: boolean;
    completion_unverified?: boolean;
  };
  metadata?: {
    sender_email?: string;
    dispatch?: {
      last_assignment_state?: string | null;
      last_assignment_ended_at?: string | null;
      last_provider_session_id?: string | null;
      last_disposition_reason?: string | null;
    };
  };
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
  todo_dispatch?: {
    last_wake_requested_at?: string | null;
    last_wake_requested_session_id?: string | null;
    last_wake_registered?: boolean | null;
    last_claimed_at?: string | null;
    last_claimed_session_id?: string | null;
    last_claimed_task_count?: number;
    last_submitted_at?: string | null;
    last_submitted_session_id?: string | null;
    last_dispatch_decision?: string | null;
    last_result_at?: string | null;
    last_result_session_id?: string | null;
    last_result_state?: string | null;
    last_result_detail?: string | null;
    last_deferred_at?: string | null;
    last_deferred_session_id?: string | null;
    last_deferred_reason?: string | null;
    last_failure_at?: string | null;
    last_failure_session_id?: string | null;
    last_failure_error?: string | null;
    last_no_tasks_at?: string | null;
    last_no_tasks_session_id?: string | null;
    last_processing_started_at?: string | null;
    last_processing_session_id?: string | null;
    last_idle_at?: string | null;
    last_idle_session_id?: string | null;
    registered_sessions?: string[];
    registered_session_count?: number;
    pending_wake_sessions?: string[];
    pending_wake_count?: number;
    busy_sessions?: string[];
    busy_session_count?: number;
    sleeping_session_warning?: boolean;
  };
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
  transcript_href?: string;
  transcript_path?: string;
  workspace_dir?: string;
  workspace_name?: string;
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
    workflow_run_id?: string | null;
  } | null;
  links?: TaskHistoryLinks;
  metadata?: any;
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
  workflow_run_id?: string | null;
  state: string;
  started_at?: string;
  ended_at?: string;
  result_summary?: string;
  session_role?: string | null;
  run_kind?: string | null;
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
  email_mapping?: {
    thread_id?: string;
    subject?: string;
    sender_email?: string;
    status?: string;
    message_count?: number;
    provider_session_id?: string;
    email_sent_at?: string;
  } | null;
  reconciliation?: {
    orphaned_in_progress?: boolean;
    completion_unverified?: boolean;
  };
  delivery_mode?: string;
  canonical_execution?: {
    session_id?: string | null;
    run_id?: string | null;
    workspace_dir?: string | null;
    session_role?: string | null;
    run_kind?: string | null;
  };
  artifacts?: TaskHistoryLinks;
};

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatTs(ts?: string | null): string {
  if (!ts) return "";
  try {
    const raw = String(ts).trim();
    const normalized = raw.includes("T") && !/(Z|[+-]\d{2}:\d{2})$/i.test(raw) ? `${raw}Z` : raw;
    return formatDistanceToNow(parseISO(normalized), { addSuffix: true });
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
    "convergence-brief": "bg-kcd-green/10 text-kcd-green",
    "insight-brief": "bg-kcd-amber/10 text-kcd-amber",
    convergence_detection: "bg-kcd-green/10 text-kcd-green",
    insight_detection: "bg-kcd-amber/10 text-kcd-amber",
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
  headerAction?: React.ReactNode;
  children: React.ReactNode;
};

function KanbanCol({ label, icon, count, accentColor, emptyText, headerAction, children }: KanbanColProps) {
  return (
    <div className="flex flex-col bg-white/5 border-none rounded-none min-h-0 overflow-hidden transition-all duration-300">
      <div className="flex items-center justify-between px-3.5 py-2.5 border-b border-white/[0.06]">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-lg" style={{ color: accentColor }}>{icon}</span>
          <span className="font-mono text-[11px] font-bold tracking-[0.1em] uppercase" style={{ color: accentColor }}>{label}</span>
        </div>
        <div className="flex items-center gap-2">
          {headerAction}
          <span className="font-mono text-[10px] font-bold px-2 py-0.5 rounded-sm" style={{ background: `${accentColor}18`, color: accentColor }}>{count}</span>
        </div>
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

  const [agentQueue, setAgentQueue] = useState<AgentQueuePayload | null>(null);
  const [agentActivity, setAgentActivity] = useState<AgentActivity | null>(null);
  const [completedTasks, setCompletedTasks] = useState<CompletedTasksPayload | null>(null);

  const [openActionMenuId, setOpenActionMenuId] = useState<string | null>(null);
  const [actionPendingTaskId, setActionPendingTaskId] = useState("");
  const [wakePending, setWakePending] = useState(false);
  const [selectedTaskDetails, setSelectedTaskDetails] = useState<any | null>(null);
  const [selectedSessionDetail, setSelectedSessionDetail] = useState<any | null>(null);
  const [sessionDetailLoading, setSessionDetailLoading] = useState("");
  const [deletedTaskIds, setDeletedTaskIds] = useState<Set<string>>(new Set());
  const [deletedTaskIdsHydrated, setDeletedTaskIdsHydrated] = useState(false);
  const [deleteAllPending, setDeleteAllPending] = useState(false);
  const [deleteAllNotAssignedPending, setDeleteAllNotAssignedPending] = useState(false);
  const [hoveredDeleteId, setHoveredDeleteId] = useState<string | null>(null);
  const [quickAddTitle, setQuickAddTitle] = useState("");
  const [quickAddPending, setQuickAddPending] = useState(false);
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null);

  useEffect(() => {
    if (!expandedTaskId) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpandedTaskId(null);
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [expandedTaskId]);


  useEffect(() => {
    try {
      const stored = localStorage.getItem("ua.deleted_completed_tasks.v1");
      if (stored) {
        setDeletedTaskIds(new Set(JSON.parse(stored) as string[]));
      }
    } catch {
      // Ignore localStorage corruption and fall back to an empty set.
    } finally {
      setDeletedTaskIdsHydrated(true);
    }


  }, []);

  // Persist deletedTaskIds to localStorage whenever it changes
  useEffect(() => {
    if (!deletedTaskIdsHydrated) return;
    try {
      localStorage.setItem("ua.deleted_completed_tasks.v1", JSON.stringify([...deletedTaskIds]));
    } catch { /* ignore */ }
  }, [deletedTaskIds, deletedTaskIdsHydrated]);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (background = false) => {
    if (background) setRefreshing(true);
    else setLoading(true);
    if (!background) setError("");
    try {
      const agentQueueUrl = new URL(`${API_BASE}/api/v1/dashboard/todolist/agent-queue`, window.location.origin);
      agentQueueUrl.searchParams.set("limit", "120");

      const [overviewRes, agentRes, activityRes, completedRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/dashboard/todolist/overview`, { cache: "no-store" }),
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

      setOverview(overviewJson as OverviewPayload);
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

  const notAssignedItems = useMemo(
    () => allQueueItems.filter((i) => String(i.board_lane || "") === "not_assigned"),
    [allQueueItems],
  );
  const inProgressItems = useMemo(
    () => allQueueItems.filter((i)ake_count || 0} pending wake request${Number(todoDispatch?.pending_wake_count || 0) === 1 ? "" : "s"}`);
    }
    if ((todoDispatch?.last_dispatch_decision || "").toLowerCase() === "busy") {
      alerts.push("Last dispatch was rejected because the target was busy");
    }
    if (Number(todoDispatch?.busy_session_count || 0) > 0) {
      alerts.push(`${todoDispatch?.busy_session_count || 0} executor session${Number(todoDispatch?.busy_session_count || 0) === 1 ? "" : "s"} currently busy`);
    }
    if (todoDispatch?.last_deferred_reason) {
      alerts.push(`Deferred: ${todoDispatch.last_deferred_reason}`);
    }
    return alerts;
  }, [todoDispatch]);

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
    const isExpanded = expandedTaskId === item.task_id;
    const isPending = actionPendingTaskId === item.task_id;
    const pCls = priorityColorClass(item.priority);
    const boardLane = String(item.board_lane || "");
    const isProcessing = boardLane === "in_progress";
    const isAgentStaging = item.status === "pending_review";
    const isHumanReview = item.status === "needs_review";
    const isAwaitingReview = boardLane === "needs_review" && !isAgentStaging && !isHumanReview; // Fallback
    const isOrphaned = Boolean(item.reconciliation?.orphaned_in_progress);
    const lastDispatch = item.metadata?.dispatch;

    // Security-specific flags
    const itemLabels = item.labels || [];
    const isSecurityAlert = itemLabels.includes("external-untriaged") || itemLabels.includes("security-untriaged");
    const isQuarantined = itemLabels.includes("quarantined");
    const senderEmail = String(item.metadata?.sender_email || "");
    const wasReopenedAfterFailure =
      boardLane === "not_assigned" && String(lastDispatch?.last_assignment_state || "").toLowerCase() === "failed";
      
    // Pre-calculate href for navigation (title and human review badge)
    const taskHref = taskSourceUrl(item.task_id, item.source_kind, item.url, item.source_ref);

    return (
      <>
      <article
        key={item.task_id}
        onClick={() => setExpandedTaskId(item.task_id)}
        className={[
          "group relative rounded-none p-3 transition-all duration-200 bg-[#0b1326]/70 backdrop-blur-md border border-white/10 hover:border-white/20 hover:-translate-y-[1px] cursor-pointer",
          item.must_complete ? "border-l-2 border-l-kcd-red" : "",
          isProcessing ? "processing-bar border-l-2 border-l-kcd-green" : "",
          (isSecurityAlert || isQuarantined) ? "border-l-2 border-l-red-500 border-red-500/30 bg-red-950/20" : "",
          (!isSecurityAlert && !isQuarantined && (isHumanReview || isAwaitingReview)) ? "border-l-2 border-l-kcd-amber" : "",
          isAgentStaging ? "border-l-2 border-l-indigo-400" : "",
        ].filter(Boolean).join(" ")}
      >
        {/* ── Security Alert Badge (external/quarantined) ── */}
        {(isSecurityAlert || isQuarantined) && (
          <div className="flex items-center gap-2 mb-2 px-2 py-1.5 bg-red-500/[0.12] border border-red-500/30 rounded-sm">
            <span className="inline-block w-2 h-2 rounded-full bg-red-500 animate-pulse" />
            <span className="material-symbols-outlined text-xs text-red-400">shield</span>
            <span className="font-mono text-[9px] font-bold tracking-[0.1em] text-red-400 uppercase">
              {isQuarantined ? "⛔ Quarantined" : "⚠ External Sender — Security Review Required"}
            </span>
            {senderEmail && (
              <span className="font-mono text-[9px] text-red-300/80 ml-auto">
                from: {senderEmail}
              </span>
            )}
          </div>
        )}
        {/* Processing / Review Status Badge */}
        {isProcessing && (
          <div className="flex items-center gap-1.5 mb-2 px-2 py-1 bg-kcd-green/[0.08] border border-kcd-green/20 rounded-sm">
            <span className="inline-block w-2 h-2 rounded-full bg-kcd-green animate-pulse" />
            <span className="font-mono text-[9px] font-bold tracking-[0.1em] text-kcd-green uppercase">Processing</span>
            <span className="font-mono text-[9px] text-kcd-text-muted">· Agent working</span>
          </div>
        )}
        {/* Agent Staging Badge (Simone's pipeline) */}
        {isAgentStaging && (
          <div className="flex items-center gap-1.5 mb-2 px-2 py-1 bg-indigo-500/[0.08] border border-indigo-400/20 rounded-sm">
            <span className="material-symbols-outlined text-xs text-indigo-400">auto_awesome</span>
            <span className="font-mono text-[9px] font-bold tracking-[0.1em] text-indigo-400 uppercase">VP Staging (Simone to Review)</span>
            <span className="font-mono text-[9px] text-kcd-text-muted">· Autonomous pipeline staging</span>
          </div>
        )}
        {/* Human Review Needed Badge (Fallback & Distinct) */}
        {(isHumanReview || isAwaitingReview) && (
          <div className="flex items-center mb-2 px-2 py-1 bg-kcd-amber/[0.08] border border-kcd-amber/20 rounded-sm hover:bg-kcd-amber/[0.15] hover:border-kcd-amber/40 transition-colors w-fit">
            {taskHref ? (
              taskHref.startsWith("http") ? (
                <a href={taskHref} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} className="flex items-center gap-1.5 no-underline cursor-pointer group/reviewbadge">
                  <span className="material-symbols-outlined text-xs text-kcd-amber group-hover/reviewbadge:scale-110 transition-transform">rate_review</span>
                  <span className="font-mono text-[9px] font-bold tracking-[0.1em] text-kcd-amber uppercase">Human Review Needed</span>
                  <span className="material-symbols-outlined text-[10px] text-kcd-amber ml-1">open_in_new</span>
                </a>
              ) : (
                <Link href={taskHref} onClick={(e) => e.stopPropagation()} className="flex items-center gap-1.5 no-underline cursor-pointer group/reviewbadge">
                  <span className="material-symbols-outlined text-xs text-kcd-amber group-hover/reviewbadge:scale-110 transition-transform">rate_review</span>
                  <span className="font-mono text-[9px] font-bold tracking-[0.1em] text-kcd-amber uppercase">Human Review Needed</span>
                  <span className="material-symbols-outlined text-[10px] text-kcd-amber ml-1">arrow_forward</span>
                </Link>
              )
            ) : (
              <div className="flex items-center gap-1.5">
                <span className="material-symbols-outlined text-xs text-kcd-amber">rate_review</span>
                <span className="font-mono text-[9px] font-bold tracking-[0.1em] text-kcd-amber uppercase">Human Review Needed</span>
                <span className="font-mono text-[9px] text-kcd-text-muted">· Run finished</span>
              </div>
            )}
          </div>
        )}
        {isOrphaned && (
          <div className="flex items-center gap-1.5 mb-2 px-2 py-1 bg-kcd-red/[0.08] border border-kcd-red/20 rounded-sm">
            <span className="material-symbols-outlined text-xs text-kcd-red">error</span>
            <span className="font-mono text-[9px] font-bold tracking-[0.1em] text-kcd-red uppercase">Orphaned</span>
            <span className="font-mono text-[9px] text-kcd-text-muted">· Reconciliation needed</span>
          </div>
        )}
        {wasReopenedAfterFailure && (
          <div className="flex items-center gap-1.5 mb-2 px-2 py-1 bg-kcd-red/[0.08] border border-kcd-red/20 rounded-sm">
            <span className="material-symbols-outlined text-xs text-kcd-red">replay</span>
            <span className="font-mono text-[9px] font-bold tracking-[0.1em] text-kcd-red uppercase">Reopened</span>
            <span className="font-mono text-[9px] text-kcd-text-muted">
              · Last run failed {formatTs(lastDispatch?.last_assignment_ended_at || null) || "recently"}
            </span>
          </div>
        )}
        {onDelete && (
          <button onClick={(e) => { e.stopPropagation(); onDelete(item.task_id); }} disabled={isPending} title="Remove from queue"
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
          {item.delivery_mode && <><span className="opacity-40">│</span><span>{item.delivery_mode}</span></>}
          {item.session_role && <><span className="opacity-40">│</span><span>{item.session_role}</span></>}
          {item.assigned_agent_id && <><span className="opacity-40">│</span><span>{item.assigned_agent_id}</span></>}
          {item.assignment_state && <><span className="opacity-40">│</span><span>{item.assignment_state}</span></>}
          {wasReopenedAfterFailure && <><span className="opacity-40">│</span><span className="text-kcd-red">retryable after failed run</span></>}
          {item.due_at && <><span className="opacity-40">│</span><span className="text-kcd-amber">Due {item.due_at}</span></>}
          {item.updated_at && <><span className="opacity-40">│</span><span>Updated {formatTs(item.updated_at)}</span></>}
          {dispatchThreshold > 0 && Number(item.score ?? 0) < dispatchThreshold && (
            <><span className="opacity-40">│</span><span className="text-kcd-amber">below threshold {dispatchThreshold}</span></>
          )}
        </div>
        {showActions && (
          <div className="mt-2 flex flex-wrap items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity duration-200" onClick={(e) => e.stopPropagation()}>
            <button onClick={() => void handleTaskAction(item.task_id, "park")} disabled={isPending}
              className="px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-red/10 text-kcd-red border-none rounded-sm cursor-pointer hover:bg-kcd-red/20 transition-colors disabled:opacity-40">
              🗑 Trash It
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
            {(() => {
              const target = resolveTaskWorkspaceTarget(item);
              if (!target) return null;
              return (
                <button onClick={() => {
                  openOrFocusChatWindow({ ...target, attachMode: "tail", role: "viewer" });
                }}
                  className="px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-emerald-500/10 text-emerald-400 border-none rounded-sm cursor-pointer hover:bg-emerald-500/20 transition-colors inline-flex items-center gap-1">
                  <span className="text-[10px]">📂</span> Workspace
                </button>
              );
            })()}
            <button onClick={(e) => { e.stopPropagation(); setSelectedTaskDetails(item); }}
              className="px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-indigo/10 text-kcd-indigo border-none rounded-sm cursor-pointer hover:bg-kcd-indigo/20 transition-colors">
              🔍 Inspect
            </button>
          </div>
        )}
      </article>

      {/* Expanded fly-out overlay */}
      {isExpanded && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm animate-in fade-in duration-150"
          onClick={(e) => { e.stopPropagation(); setExpandedTaskId(null); }}
        >
          <div
            className="relative w-full max-w-2xl mx-4 rounded-xl border border-white/20 bg-kcd-surface-dim/95 backdrop-blur-lg shadow-2xl p-6 animate-in zoom-in-95 slide-in-from-bottom-2 duration-200"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setExpandedTaskId(null)}
              className="absolute top-4 right-4 text-muted-foreground hover:text-foreground transition-colors text-lg leading-none"
              title="Close (Esc)"
            >
              ✕
            </button>
            <div className="flex flex-wrap items-center gap-2 mb-3 pr-8">
              <span className="font-mono text-[10px] font-bold text-kcd-text-muted">#{idx + 1}</span>
              {sourceKindPill(item.source_kind)}
              {item.must_complete && <span className="font-mono text-[9px] font-bold px-1.5 py-0.5 bg-kcd-red/10 text-kcd-red tracking-wider uppercase">MUST</span>}
              <div className={`font-mono text-[11px] font-bold ml-auto ${pCls}`}>{priorityText(item.priority)}</div>
            </div>
            <h3 className="text-base font-semibold text-kcd-text leading-snug mb-3">
              {item.title}
            </h3>
            {item.description && (
              <div className="text-[13px] text-kcd-text-muted leading-relaxed whitespace-pre-wrap break-words max-h-[50vh] overflow-y-auto scrollbar-thin p-3 bg-black/20 rounded-md border border-white/5">
                {item.description}
              </div>
            )}
            <div className="mt-4 flex flex-wrap items-center gap-2 font-mono text-[11px] text-kcd-text-muted bg-white/5 p-2 rounded">
              {item.project_key && <span>{item.project_key}</span>}
              {item.delivery_mode && <><span className="opacity-40">│</span><span>{item.delivery_mode}</span></>}
              {item.session_role && <><span className="opacity-40">│</span><span>{item.session_role}</span></>}
              {item.assigned_agent_id && <><span className="opacity-40">│</span><span>{item.assigned_agent_id}</span></>}
              {item.assignment_state && <><span className="opacity-40">│</span><span>{item.assignment_state}</span></>}
              {item.due_at && <><span className="opacity-40">│</span><span className="text-kcd-amber">Due {item.due_at}</span></>}
              {item.updated_at && <><span className="opacity-40">│</span><span>Updated {formatTs(item.updated_at)}</span></>}
            </div>
            {showActions && (
              <div className="mt-4 flex flex-wrap items-center gap-2">
                <button onClick={(e) => { e.stopPropagation(); void handleTaskAction(item.task_id, "park"); setExpandedTaskId(null); }} disabled={isPending}
                  className="px-3 py-1.5 font-mono text-[11px] font-bold tracking-wider uppercase bg-kcd-red/10 text-kcd-red border border-kcd-red/20 rounded-md cursor-pointer hover:bg-kcd-red/20 transition-colors disabled:opacity-40">
                  🗑 Trash It
                </button>
                <button onClick={(e) => { e.stopPropagation(); void handleWakeHeartbeat(item.task_id); setExpandedTaskId(null); }} disabled={wakePending}
                  className="px-3 py-1.5 font-mono text-[11px] font-bold tracking-wider uppercase bg-kcd-cyan/10 text-kcd-cyan border border-kcd-cyan/20 rounded-md cursor-pointer hover:bg-kcd-cyan/20 transition-colors disabled:opacity-40">
                  ⚡ {wakePending ? "Queueing…" : "Dispatch"}
                </button>
              </div>
            )}
          </div>
        </div>
      )}
      </>
    );
  };

  const renderCompletedCard = (item: CompletedTaskItem) => {
    const isExpanded = expandedTaskId === item.task_id;
    const pCls = priorityColorClass(item.priority);
    return (
      <>
      <article key={`completed-${item.task_id}`}
        onClick={() => setExpandedTaskId(item.task_id)}
        className="group relative rounded-md p-3 transition-all duration-200 bg-kcd-surface-low border border-white/[0.15] hover:bg-kcd-surface-high/80 cursor-pointer">
        <button onClick={(e) => { e.stopPropagation(); void handleDeleteCompletedTask(item.task_id); }} title="Delete"
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
                    ? <a href={href} target="_blank" rel="noopener noreferrer" onClick={(e) => e.stopPropagation()} className="text-kcd-text no-underline hover:text-kcd-cyan transition-colors">{item.title}</a>
                    : <Link href={href} onClick={(e) => e.stopPropagation()} className="text-kcd-text no-underline hover:text-kcd-cyan transition-colors">{item.title}</Link>;
                }
                return item.title;
              })()}
            </h3>
            {item.description && <p className="mt-1 text-[11px] text-kcd-text-muted leading-snug line-clamp-2">{item.description}</p>}
            {(item.last_assignment?.result_summary || item.metadata?.dispatch?.last_disposition_reason) && (
              <p className="mt-1.5 text-[11px] italic text-kcd-text-dim border-l-2 border-white/10 pl-2 line-clamp-2">
                {item.last_assignment?.result_summary || item.metadata?.dispatch?.last_disposition_reason}
              </p>
            )}
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
        <div className="mt-2 flex flex-wrap items-center gap-1.5 opacity-0 group-hover:opacity-100 transition-opacity duration-200" onClick={(e) => e.stopPropagation()}>
          <button onClick={(e) => { e.stopPropagation(); setSelectedTaskDetails(item); }}
            className="px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-indigo/10 text-kcd-indigo border-none rounded-sm cursor-pointer hover:bg-kcd-indigo/20 transition-colors">
            Inspect
          </button>
          {(() => {
            const target = resolveTaskWorkspaceTarget({
              links: item.links,
              workflow_run_id: item.last_assignment?.workflow_run_id,
            });
            if (!target) return null;
            return (
              <button onClick={(e) => {
                e.stopPropagation();
                openOrFocusChatWindow({ ...target, attachMode: "tail", role: "viewer" });
              }}
                className="px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-emerald-500/10 text-emerald-400 border-none rounded-sm cursor-pointer hover:bg-emerald-500/20 transition-colors inline-flex items-center gap-1">
                <span className="text-[10px]">📂</span> Workspace
              </button>
            );
          })()}
        </div>
      </article>

      {/* Expanded fly-out overlay for completed cards */}
      {isExpanded && (
        <div
          className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 backdrop-blur-sm animate-in fade-in duration-150"
          onClick={(e) => { e.stopPropagation(); setExpandedTaskId(null); }}
        >
          <div
            className="relative w-full max-w-2xl mx-4 rounded-xl border border-white/20 bg-kcd-surface-dim/95 backdrop-blur-lg shadow-2xl p-6 animate-in zoom-in-95 slide-in-from-bottom-2 duration-200"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => setExpandedTaskId(null)}
              className="absolute top-4 right-4 text-muted-foreground hover:text-foreground transition-colors text-lg leading-none"
              title="Close (Esc)"
            >
              ✕
            </button>
            <div className="flex flex-wrap items-center gap-2 mb-3 pr-8">
              {sourceKindPill(item.source_kind)}
              <div className={`font-mono text-[11px] font-bold ml-auto ${pCls}`}>{priorityText(item.priority)}</div>
            </div>
            <h3 className="text-base font-semibold text-kcd-text leading-snug mb-3">
              {item.title}
            </h3>
            {item.description && (
              <div className="text-[13px] text-kcd-text-muted leading-relaxed whitespace-pre-wrap break-words max-h-[50vh] overflow-y-auto scrollbar-thin p-3 bg-black/20 rounded-md border border-white/5">
                {item.description}
              </div>
            )}
            <div className="mt-4 flex flex-wrap items-center gap-2 font-mono text-[11px] text-kcd-text-muted bg-white/5 p-2 rounded">
              {item.project_key && <span>{item.project_key}</span>}
              <span className="opacity-40">│</span>
              <span>Done {formatTs(item.completed_at || item.updated_at)}</span>
              {item.last_assignment?.agent_id && <><span className="opacity-40">│</span><span className="text-kcd-text-dim">{item.last_assignment.agent_id}</span></>}
            </div>
          </div>
        </div>
      )}
      </>
    );
  };

  // ── Work item details modal ───────────────────────────────────────────────────

  const renderTaskDetailsModal = () => {
    if (!selectedTaskDetails) return null;
    return (
      <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
        <div className="flex max-h-full w-full max-w-4xl flex-col rounded-xl border border-border bg-background shadow-2xl">
          <div className="flex items-center justify-between border-b border-border bg-background/50 p-4">
            <div>
              <h2 className="text-lg font-semibold text-foreground">Work Item Details</h2>
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

  // ── Work item history panel ───────────────────────────────────────────────────




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




      {/* ── Kanban Time Horizon Board ── */}
      <div className="grid gap-3 grid-cols-1 xl:grid-cols-4" onClick={(e) => e.stopPropagation()}>
        <KanbanCol
          label="Not Assigned"
          icon="schedule"
          count={notAssignedItems.length}
          accentColor="#22D3EE"
          emptyText="No unassigned tasks."
          headerAction={
            notAssignedItems.length > 0 ? (
              <button
                onClick={() => void handleDeleteAllNotAssigned()}
                disabled={deleteAllNotAssignedPending}
                className="px-2 py-0.5 font-mono text-[9px] font-bold tracking-wider uppercase bg-kcd-red/10 text-kcd-red border-none rounded-sm cursor-pointer hover:bg-kcd-red/20 transition-colors disabled:opacity-40"
              >
                {deleteAllNotAssignedPending ? "Deleting…" : "Delete All"}
              </button>
            ) : undefined
          }
        >
          {notAssignedItems.map((item, idx) => renderTaskCard(item, idx, true))}
        </KanbanCol>
        <KanbanCol label="In Progress" icon="bolt" count={inProgressItems.length} accentColor="#4ADE80" emptyText="Nothing actively in progress.">
          {inProgressItems.map((item, idx) => renderTaskCard(item, idx, true, (id) => void handleTaskAction(id, "park")))}
        </KanbanCol>
        <KanbanCol label="Needs Review" icon="rate_review" count={needsReviewItems.length} accentColor="#F59E0B" emptyText="Nothing awaiting Simone review.">
          {needsReviewItems.map((item, idx) => renderTaskCard(item, idx, true))}
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

      {blockedItems.length > 0 && (
        <div className="font-mono text-[10px] text-kcd-text-muted">
          Blocked items are excluded from the main board lanes: <span className="text-kcd-amber">{blockedItems.length}</span>
        </div>
      )}



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
