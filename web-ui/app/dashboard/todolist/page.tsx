"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { formatDistanceToNow, parseISO } from "date-fns";

const API_BASE = "/api/dashboard/gateway";
const AUTO_REFRESH_SECONDS = 30;

/* ── Design Tokens (Stitch: Kinetic Command Deck) ────────────────── */

const T = {
  bg: "#0b1326",
  surfaceDim: "#0f1a33",
  surfaceLow: "#131f3d",
  surfaceHigh: "#1a2847",
  surfaceBright: "#223054",
  cyan: "#22D3EE",
  cyanDim: "rgba(34,211,238,0.12)",
  cyanGhost: "rgba(34,211,238,0.20)",
  amber: "#EE9800",
  amberDim: "rgba(238,152,0,0.12)",
  green: "#4ADE80",
  greenDim: "rgba(74,222,128,0.12)",
  red: "#EF4444",
  redDim: "rgba(239,68,68,0.12)",
  indigo: "#818CF8",
  indigoDim: "rgba(129,140,248,0.12)",
  textPrimary: "#E2E8F0",
  textSecondary: "#BBC9CD",
  textMuted: "#64748B",
  ghostBorder: "rgba(187,201,205,0.15)",
  fontMono: "'JetBrains Mono', 'Fira Code', monospace",
  fontUi: "'Inter', system-ui, sans-serif",
};

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
  const colors: Record<string, string> = {
    task_hub: T.cyan,
    internal: T.cyan,
    approval: T.amber,
    email: T.indigo,
    csi: T.textMuted,
  };
  const c = colors[k] ?? T.textMuted;
  return (
    <span
      style={{
        fontFamily: T.fontMono,
        fontSize: 9,
        fontWeight: 700,
        letterSpacing: "0.08em",
        padding: "2px 6px",
        background: `${c}18`,
        color: c,
        textTransform: "uppercase",
      }}
    >
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
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        background: T.surfaceDim,
        border: `1px solid ${T.ghostBorder}`,
        minHeight: 0,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          padding: "10px 14px",
          borderBottom: `1px solid ${T.ghostBorder}`,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <span
            className="material-symbols-outlined"
            style={{ fontSize: 18, color: accentColor }}
          >
            {icon}
          </span>
          <span
            style={{
              fontFamily: T.fontMono,
              fontSize: 11,
              fontWeight: 700,
              letterSpacing: "0.1em",
              color: accentColor,
              textTransform: "uppercase",
            }}
          >
            {label}
          </span>
        </div>
        <span
          style={{
            fontFamily: T.fontMono,
            fontSize: 10,
            fontWeight: 700,
            padding: "2px 8px",
            background: `${accentColor}18`,
            color: accentColor,
          }}
        >
          {count}
        </span>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: 10, maxHeight: "60vh" }}>
        {count === 0 ? (
          <p style={{ fontSize: 12, color: T.textMuted, fontStyle: "italic", padding: 8 }}>{emptyText}</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>{children}</div>
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
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center", height: "100%", gap: 16, padding: 24, background: T.bg, fontFamily: T.fontUi }}>
        <span style={{ fontFamily: T.fontMono, fontSize: 12, color: T.textMuted }}>Loading Task Hub…</span>
      </div>
    );
  }

  // ── Inline style helpers for action buttons ────────────────────────────────

  const actionBtn = (color: string, bg: string): React.CSSProperties => ({
    background: bg,
    color,
    border: "none",
    padding: "4px 10px",
    fontFamily: T.fontMono,
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: "0.05em",
    cursor: "pointer",
    textTransform: "uppercase",
  });

  const menuBtn = (color: string): React.CSSProperties => ({
    background: "transparent",
    color,
    border: "none",
    padding: "4px 10px",
    fontFamily: T.fontMono,
    fontSize: 10,
    fontWeight: 700,
    letterSpacing: "0.05em",
    cursor: "pointer",
    textTransform: "uppercase",
    textAlign: "left" as const,
    width: "100%",
  });

  // ── Sub-renders ───────────────────────────────────────────────────────────────

  const renderTaskCard = (item: AgentQueueItem, idx: number, showActions = true, onDelete?: (id: string) => void) => {
    const isPending = actionPendingTaskId === item.task_id;
    const pColor = (() => { const p = Number(item.priority || 1); if (p >= 4) return T.red; if (p === 3) return T.amber; if (p === 2) return T.cyan; return T.textMuted; })();
    return (
      <article
        key={item.task_id}
        style={{
          position: "relative",
          background: T.surfaceLow,
          border: `1px solid ${T.ghostBorder}`,
          borderLeft: item.must_complete ? `3px solid ${T.red}` : `1px solid ${T.ghostBorder}`,
          padding: 12,
          transition: "background 0.15s",
        }}
        onMouseEnter={(e) => { (e.currentTarget as HTMLElement).style.background = T.surfaceHigh; }}
        onMouseLeave={(e) => { (e.currentTarget as HTMLElement).style.background = T.surfaceLow; }}
      >
        {onDelete ? (
          <button
            onClick={() => onDelete(item.task_id)}
            disabled={isPending}
            title="Remove from queue"
            style={{ position: "absolute", right: 8, top: 8, background: "none", border: "none", cursor: "pointer", color: T.textMuted, fontSize: 16, padding: 2, opacity: 0.6 }}
            onMouseEnter={(e) => { (e.target as HTMLElement).style.color = T.red; (e.target as HTMLElement).style.opacity = "1"; }}
            onMouseLeave={(e) => { (e.target as HTMLElement).style.color = T.textMuted; (e.target as HTMLElement).style.opacity = "0.6"; }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 16 }}>delete</span>
          </button>
        ) : null}
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8, paddingRight: onDelete ? 24 : 0 }}>
          <div style={{ minWidth: 0 }}>
            <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6, marginBottom: 4 }}>
              <span style={{ fontFamily: T.fontMono, fontSize: 9, fontWeight: 700, color: T.textMuted }}>#{idx + 1}</span>
              {sourceKindPill(item.source_kind)}
              {item.must_complete ? (
                <span style={{ fontFamily: T.fontMono, fontSize: 9, fontWeight: 700, padding: "2px 6px", background: T.redDim, color: T.red, letterSpacing: "0.08em", textTransform: "uppercase" }}>
                  MUST COMPLETE
                </span>
              ) : null}
            </div>
            <h3 style={{ fontSize: 13, fontWeight: 600, color: T.textPrimary, lineHeight: 1.35, margin: 0 }}>
              {(() => {
                const href = taskSourceUrl(item.task_id, item.source_kind, item.url, item.source_ref);
                if (href) {
                  const isExternal = href.startsWith("http");
                  return isExternal ? (
                    <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: T.textPrimary, textDecoration: "none" }}>{item.title}</a>
                  ) : (
                    <Link href={href} style={{ color: T.textPrimary, textDecoration: "none" }}>{item.title}</Link>
                  );
                }
                return item.title;
              })()}
            </h3>
            {item.description ? (
              <p style={{ margin: "4px 0 0", fontSize: 11, color: T.textMuted, lineHeight: 1.4, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{item.description}</p>
            ) : null}
          </div>
          <div style={{ textAlign: "right", flexShrink: 0 }}>
            <div style={{ fontFamily: T.fontMono, fontSize: 10, fontWeight: 700, color: pColor }}>{priorityText(item.priority)}</div>
            {item.score !== undefined ? (
              <div style={{ fontFamily: T.fontMono, fontSize: 9, color: T.textMuted, marginTop: 2 }}>score {item.score} · Q{item.score_confidence ?? 0}</div>
            ) : null}
          </div>
        </div>

        <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6, fontFamily: T.fontMono, fontSize: 10, color: T.textMuted }}>
          {item.project_key ? <span>{item.project_key}</span> : null}
          {item.due_at ? (<><span style={{ opacity: 0.4 }}>│</span><span style={{ color: T.amber }}>Due {item.due_at}</span></>) : null}
          {item.updated_at ? (<><span style={{ opacity: 0.4 }}>│</span><span>Updated {formatTs(item.updated_at)}</span></>) : null}
          {dispatchThreshold > 0 && Number(item.score ?? 0) < dispatchThreshold ? (
            <><span style={{ opacity: 0.4 }}>│</span><span style={{ color: T.amber }}>below threshold {dispatchThreshold}</span></>
          ) : null}
        </div>

        {showActions ? (
          <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6 }}>
            <button onClick={() => void handleTaskAction(item.task_id, "complete")} disabled={isPending} style={actionBtn(T.green, T.greenDim)}>
              Complete
            </button>
            <button onClick={() => void handleWakeHeartbeat(item.task_id)} disabled={wakePending} style={actionBtn(T.cyan, T.cyanDim)}>
              {wakePending ? "Queueing…" : "Dispatch"}
            </button>
            <div style={{ position: "relative" }}>
              <button
                onClick={() => setOpenActionMenuId(openActionMenuId === item.task_id ? null : item.task_id)}
                style={{ ...actionBtn(T.textSecondary, T.surfaceHigh), padding: "4px 8px" }}
              >
                ▾
              </button>
              {openActionMenuId === item.task_id && (
                <div style={{ position: "absolute", right: 0, top: "100%", zIndex: 10, marginTop: 4, width: 130, display: "flex", flexDirection: "column", gap: 2, background: T.surfaceDim, border: `1px solid ${T.ghostBorder}`, padding: 4, boxShadow: "0 8px 32px rgba(0,0,0,0.4)" }}>
                  {item.status === "open" && (
                    <button onClick={() => void handleTaskAction(item.task_id, "seize")} disabled={isPending} style={menuBtn(T.cyan)}>Seize</button>
                  )}
                  <button onClick={() => void handleTaskAction(item.task_id, "review")} disabled={isPending} style={menuBtn(T.textSecondary)}>Mark Review</button>
                  <button onClick={() => void handleTaskAction(item.task_id, "block")} disabled={isPending} style={menuBtn(T.amber)}>Block</button>
                  <button onClick={() => void handleTaskAction(item.task_id, "park")} disabled={isPending} style={menuBtn(T.red)}>Park
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
      style={{ position: "relative", background: T.surfaceLow, border: `1px solid ${T.ghostBorder}`, padding: 12, transition: "background 0.15s" }}
      onMouseEnter={(e) => { setHoveredDeleteId(item.task_id); (e.currentTarget as HTMLElement).style.background = T.surfaceHigh; }}
      onMouseLeave={(e) => { setHoveredDeleteId(null); (e.currentTarget as HTMLElement).style.background = T.surfaceLow; }}
    >
      <button
        onClick={() => void handleDeleteCompletedTask(item.task_id)}
        style={{ position: "absolute", right: 8, top: 8, background: "none", border: "none", cursor: "pointer", color: T.textMuted, fontSize: 16, padding: 2, opacity: hoveredDeleteId === item.task_id ? 1 : 0, transition: "opacity 0.15s" }}
        title="Delete"
      >
        <span className="material-symbols-outlined" style={{ fontSize: 16 }}>delete</span>
      </button>
      <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8, paddingRight: 24 }}>
        <div style={{ minWidth: 0 }}>
          <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6, marginBottom: 4 }}>
            {sourceKindPill(item.source_kind)}
          </div>
          <h3 style={{ fontSize: 13, fontWeight: 600, color: T.textPrimary, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", margin: 0 }}>
            {(() => {
              const sessionHref = item.links?.session_href;
              const sourceHref = taskSourceUrl(item.task_id, item.source_kind, undefined, item.source_ref);
              const href = sessionHref || sourceHref;
              if (href) {
                const isExternal = href.startsWith("http");
                return isExternal ? (
                  <a href={href} target="_blank" rel="noopener noreferrer" style={{ color: T.textPrimary, textDecoration: "none" }}>{item.title}</a>
                ) : (
                  <Link href={href} style={{ color: T.textPrimary, textDecoration: "none" }}>{item.title}</Link>
                );
              }
              return item.title;
            })()}
          </h3>
          {item.description ? <p style={{ margin: "4px 0 0", fontSize: 11, color: T.textMuted, lineHeight: 1.4, display: "-webkit-box", WebkitLineClamp: 2, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{item.description}</p> : null}
        </div>
        <div style={{ fontFamily: T.fontMono, fontSize: 10, fontWeight: 700, flexShrink: 0, textAlign: "right", color: (() => { const p = Number(item.priority || 1); if (p >= 4) return T.red; if (p === 3) return T.amber; if (p === 2) return T.cyan; return T.textMuted; })() }}>
          {priorityText(item.priority)}
        </div>
      </div>
      <div style={{ marginTop: 6, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6, fontFamily: T.fontMono, fontSize: 10, color: T.textMuted }}>
        {item.project_key ? <span>{item.project_key}</span> : null}
        <span style={{ opacity: 0.4 }}>│</span>
        <span>Done {formatTs(item.completed_at || item.updated_at)}</span>
        {item.last_assignment?.agent_id ? (
          <><span style={{ opacity: 0.4 }}>│</span><span style={{ color: T.textSecondary }}>{item.last_assignment.agent_id}</span></>
        ) : null}
      </div>
      <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6 }}>
        <button onClick={() => void handleOpenTaskHistory(item.task_id)} disabled={taskHistoryLoadingId === item.task_id} style={actionBtn(T.cyan, T.cyanDim)}>
          {taskHistoryLoadingId === item.task_id ? "Loading…" : "Review"}
        </button>
        <button onClick={() => setSelectedTaskDetails(item)} style={actionBtn(T.indigo, T.indigoDim)}>
          Inspect
        </button>
        {item.links?.session_id ? (
          <button onClick={() => void handleOpenSession(String(item.links!.session_id))} disabled={sessionDetailLoading === String(item.links!.session_id)} style={actionBtn(T.indigo, T.indigoDim)}>
            {sessionDetailLoading === String(item.links!.session_id) ? "Loading…" : "Session"}
          </button>
        ) : null}
        {item.links?.run_log_href ? (
          <a href={String(item.links.run_log_href)} style={{ ...actionBtn(T.cyan, T.cyanDim), textDecoration: "none" }}>
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
                      {(row.links?.session_id || row.session_id) ? (
                        <button
                          onClick={() => void handleOpenSession(String(row.links?.session_id || row.session_id))}
                          disabled={sessionDetailLoading === String(row.links?.session_id || row.session_id)}
                          className="rounded border border-indigo-700/60 bg-indigo-900/20 px-2 py-0.5 text-[10px] uppercase tracking-wide text-indigo-200 hover:bg-indigo-900/35 disabled:opacity-50"
                        >
                          {sessionDetailLoading === String(row.links?.session_id || row.session_id) ? "Loading…" : "Session"}
                        </button>
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
    <div style={{ position: "relative", display: "flex", flexDirection: "column", height: "100%", gap: 16, paddingBottom: 24, background: T.bg, fontFamily: T.fontUi, color: T.textPrimary }} onClick={() => setOpenActionMenuId(null)}>
      {renderTaskDetailsModal()}
      {renderSessionDetailModal()}

      {/* ── Header ── */}
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "16px 20px", borderBottom: `1px solid ${T.ghostBorder}`, background: T.surfaceDim }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <span className="material-symbols-outlined" style={{ fontSize: 22, color: T.cyan }}>task_alt</span>
          <div>
            <h1 style={{ fontFamily: T.fontMono, fontSize: 15, fontWeight: 700, letterSpacing: "0.06em", margin: 0, color: T.cyan, textTransform: "uppercase" }}>Task Hub</h1>
            <p style={{ fontSize: 11, color: T.textMuted, margin: 0 }}>{allQueueItems.length} open · {(agentActivity?.active_agents || 0)} active · {completionRate24h !== null ? `${completionRate24h}%` : "—"} rate</p>
          </div>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 8 }}>
          <span style={{ fontFamily: T.fontMono, fontSize: 10, color: T.textMuted }}>
            {refreshing ? "Refreshing…" : `next ${countdown}s`}
          </span>
          <button
            onClick={() => { setCountdown(AUTO_REFRESH_SECONDS); void load(true); }}
            style={actionBtn(T.textSecondary, T.surfaceHigh)}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 14, verticalAlign: "middle", marginRight: 4 }}>refresh</span>
            Refresh
          </button>
          <button
            onClick={() => void handleWakeHeartbeat()}
            disabled={wakePending}
            style={actionBtn(T.cyan, T.cyanDim)}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 14, verticalAlign: "middle", marginRight: 4 }}>favorite</span>
            {wakePending ? "Queueing…" : "Heartbeat"}
          </button>
          <Link
            href="/dashboard/approvals"
            style={{ ...actionBtn(T.amber, T.amberDim), textDecoration: "none" }}
          >
            Approvals
            {(approvalsHighlight?.pending_count || 0) > 0 ? (
              <span style={{ marginLeft: 6, padding: "1px 5px", background: T.amber, color: T.bg, fontFamily: T.fontMono, fontSize: 9, fontWeight: 700 }}>
                {approvalsHighlight!.pending_count}
              </span>
            ) : null}
          </Link>
        </div>
      </div>

      {error ? (
        <div style={{ margin: "0 20px", padding: "8px 12px", background: T.redDim, border: `1px solid ${T.ghostBorder}`, color: T.red, fontSize: 12 }}>{error}</div>
      ) : null}

      {/* ── Content area ── */}
      <div style={{ padding: "0 20px", display: "flex", flexDirection: "column", gap: 16, flex: 1, minHeight: 0 }}>

      {/* ── Summary Cards ── */}
      <section style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
        {[
          { label: `Dispatch Eligible${dispatchThreshold > 0 ? ` (≥${dispatchThreshold})` : ""}`, value: overview?.queue_health?.dispatch_eligible || 0, sub: `${overview?.queue_health?.dispatch_queue_size || 0} in queue`, color: T.textPrimary },
          { label: "Active Agents", value: agentActivity?.active_agents || 0, sub: `${(agentActivity?.active_assignments || []).length} assignments`, color: T.cyan },
          { label: "Backlog Open", value: agentActivity?.backlog_open || 0, sub: "total queued", color: T.textPrimary },
          { label: "Approvals Pending", value: approvalsHighlight?.pending_count || 0, sub: "awaiting decision", color: T.amber },
          { label: "Completion Rate", value: completionRate24h !== null ? `${completionRate24h}%` : "—", sub: "24h completed / rejected", color: completionRate24h !== null ? (completionRate24h >= 70 ? T.green : completionRate24h >= 40 ? T.amber : T.red) : T.textMuted },
        ].map((card) => (
          <article key={card.label} style={{ background: T.surfaceDim, border: `1px solid ${T.ghostBorder}`, padding: 12 }}>
            <p style={{ fontFamily: T.fontMono, fontSize: 9, fontWeight: 700, letterSpacing: "0.1em", color: T.textMuted, textTransform: "uppercase", margin: 0 }}>{card.label}</p>
            <p style={{ fontSize: 22, fontWeight: 600, color: card.color, margin: "4px 0 0" }}>{card.value}</p>
            <p style={{ fontSize: 10, color: T.textMuted, margin: "4px 0 0" }}>{card.sub}</p>
          </article>
        ))}
      </section>

      {/* ── NOW: Current Assignments ── */}
      <section style={{ background: T.surfaceDim, border: `1px solid ${T.ghostBorder}`, padding: 16 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
          <span className="material-symbols-outlined" style={{ fontSize: 18, color: T.cyan }}>bolt</span>
          <h2 style={{ fontFamily: T.fontMono, fontSize: 11, fontWeight: 700, letterSpacing: "0.1em", color: T.cyan, textTransform: "uppercase", margin: 0 }}>
            Now — Active ({(agentActivity?.active_assignments || []).length})
          </h2>
        </div>
        {(agentActivity?.active_assignments || []).length === 0 ? (
          <p style={{ fontSize: 12, color: T.textMuted, fontStyle: "italic" }}>No agents currently working. Queue a heartbeat to dispatch work.</p>
        ) : (
          <div style={{ display: "grid", gap: 8, gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))" }}>
            {(agentActivity?.active_assignments || []).map((a) => (
              <div key={a.assignment_id} style={{ background: T.cyanDim, border: `1px solid ${T.cyanGhost}`, padding: 12 }}>
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8 }}>
                  <div>
                    <div style={{ fontFamily: T.fontMono, fontSize: 9, fontWeight: 700, letterSpacing: "0.08em", color: T.cyan, textTransform: "uppercase" }}>{a.agent_id}</div>
                    <div style={{ fontSize: 13, fontWeight: 500, color: T.textPrimary, marginTop: 4, lineHeight: 1.35 }}>{a.title}</div>
                  </div>
                  <span style={{ fontFamily: T.fontMono, fontSize: 10, fontWeight: 700, flexShrink: 0, color: (() => { const p = Number(a.priority || 1); if (p >= 4) return T.red; if (p === 3) return T.amber; if (p === 2) return T.cyan; return T.textMuted; })() }}>{priorityText(a.priority)}</span>
                </div>
                <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6, fontFamily: T.fontMono, fontSize: 10, color: T.textMuted }}>
                  {a.project_key ? <span>{a.project_key}</span> : null}
                  <span style={{ opacity: 0.4 }}>│</span>
                  <span>Started {formatTs(a.started_at)}</span>
                </div>
              </div>
            ))}
          </div>
        )}
      </section>

      {/* ── Agent Efficiency Strip ── */}
      <section style={{ background: T.surfaceDim, border: `1px solid ${T.ghostBorder}`, padding: 12, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        <span style={{ fontFamily: T.fontMono, fontSize: 9, fontWeight: 700, letterSpacing: "0.1em", color: T.textMuted, textTransform: "uppercase", flexShrink: 0 }}>Agent Efficiency</span>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 16, fontFamily: T.fontMono, fontSize: 11 }}>
          <span style={{ color: T.textMuted }}>1h: <strong style={{ color: T.textPrimary }}>{agentMetrics1h?.seized || 0}</strong> seized · <strong style={{ color: T.cyan }}>{agentMetrics1h?.completed || 0}</strong> done · <strong style={{ color: T.red }}>{agentMetrics1h?.rejected || 0}</strong> rejected</span>
          <span style={{ color: T.textMuted }}>24h: <strong style={{ color: T.textPrimary }}>{agentMetrics24h?.seized || 0}</strong> seized · <strong style={{ color: T.cyan }}>{agentMetrics24h?.completed || 0}</strong> done · <strong style={{ color: T.red }}>{agentMetrics24h?.rejected || 0}</strong> rejected</span>
        </div>
      </section>

      {/* ── Kanban Time Horizon Board ── */}
      <div style={{ display: "grid", gap: 12, gridTemplateColumns: "repeat(auto-fit, minmax(320px, 1fr))" }} onClick={(e) => e.stopPropagation()}>
        <KanbanCol label="Future" icon="schedule" count={futureItems.length} accentColor={T.cyan} emptyText="No queued tasks.">
          {futureItems.map((item, idx) => renderTaskCard(item, idx, true))}
        </KanbanCol>

        <KanbanCol label="In Progress" icon="bolt" count={nowItems.length} accentColor={T.green} emptyText="Nothing actively in progress.">
          {nowItems.map((item, idx) => renderTaskCard(item, idx, true, (id) => void handleTaskAction(id, "park")))}
        </KanbanCol>

        <KanbanCol label="Past" icon="check_circle" count={visibleCompletedRows.length} accentColor={T.textMuted} emptyText="No completed tasks yet.">
          <>
            {visibleCompletedRows.length > 1 ? (
              <div style={{ display: "flex", justifyContent: "flex-end" }}>
                <button
                  onClick={() => void handleDeleteAllCompleted()}
                  disabled={deleteAllPending}
                  style={actionBtn(T.red, T.redDim)}
                >
                  {deleteAllPending ? "Clearing…" : "Clear All"}
                </button>
              </div>
            ) : null}
            {visibleCompletedRows.slice(0, 20).map((item) => renderCompletedCard(item))}
          </>
        </KanbanCol>
      </div>

      {/* ── Allocation Breakdown ── */}
      {allQueueItems.length > 0 ? (
        <section style={{ background: T.surfaceDim, border: `1px solid ${T.ghostBorder}`, padding: 16 }}>
          <h2 style={{ fontFamily: T.fontMono, fontSize: 11, fontWeight: 700, letterSpacing: "0.1em", color: T.textSecondary, textTransform: "uppercase", margin: "0 0 12px" }}>Work Allocation</h2>
          <div style={{ display: "grid", gap: 24, gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))" }}>
            <div>
              <h3 style={{ fontFamily: T.fontMono, fontSize: 9, fontWeight: 700, letterSpacing: "0.1em", color: T.textMuted, textTransform: "uppercase", margin: "0 0 8px" }}>By Source</h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {allocationBySource.map(([kind, count]) => {
                  const pct = Math.round((count / allQueueItems.length) * 100);
                  return (
                    <div key={kind} style={{ display: "grid", alignItems: "center", gap: 12, gridTemplateColumns: "8rem 1fr 4rem", fontSize: 11 }}>
                      <div style={{ overflow: "hidden" }}>{sourceKindPill(kind)}</div>
                      <div style={{ background: T.surfaceBright, height: 4, minWidth: 0 }}>
                        <div style={{ height: 4, background: T.cyan, transition: "width 0.3s", width: `${pct}%` }} />
                      </div>
                      <span style={{ textAlign: "right", color: T.textMuted, fontFamily: T.fontMono, fontSize: 10, whiteSpace: "nowrap" }}>{count} ({pct}%)</span>
                    </div>
                  );
                })}
              </div>
            </div>
            <div>
              <h3 style={{ fontFamily: T.fontMono, fontSize: 9, fontWeight: 700, letterSpacing: "0.1em", color: T.textMuted, textTransform: "uppercase", margin: "0 0 8px" }}>By Project</h3>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {allocationByProject.map(([proj, count]) => {
                  const pct = Math.round((count / allQueueItems.length) * 100);
                  return (
                    <div key={proj} style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 11 }}>
                      <span style={{ width: 112, flexShrink: 0, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", color: T.textSecondary }}>{proj}</span>
                      <div style={{ flex: 1, background: T.surfaceBright, height: 4 }}>
                        <div style={{ height: 4, background: T.indigo, width: `${pct}%` }} />
                      </div>
                      <span style={{ width: 56, textAlign: "right", color: T.textMuted, fontFamily: T.fontMono, fontSize: 10 }}>{count} ({pct}%)</span>
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
        <section style={{ background: T.amberDim, border: `1px solid ${T.ghostBorder}`, padding: 12, display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12 }}>
          <span style={{ fontFamily: T.fontMono, fontSize: 9, fontWeight: 700, letterSpacing: "0.1em", color: T.amber, textTransform: "uppercase", flexShrink: 0 }}>Heartbeat</span>
          {heartbeatAlerts.map((alert) => (
            <span key={alert} style={{ padding: "2px 8px", background: T.amberDim, border: `1px solid ${T.ghostBorder}`, fontFamily: T.fontMono, fontSize: 10, color: T.amber }}>
              {alert}
            </span>
          ))}
          <span style={{ fontFamily: T.fontMono, fontSize: 10, color: T.textMuted }}>
            next {formatEpochTs(overview?.heartbeat?.nearest_next_run_epoch)} · interval {formatEvery(overview?.heartbeat?.heartbeat_effective_interval_seconds ?? overview?.heartbeat?.effective_default_every_seconds)}
          </span>
        </section>
      ) : null}

      </div>{/* end content area */}
    </div>
  );
}
