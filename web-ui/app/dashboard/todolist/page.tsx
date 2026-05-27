"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { formatDistanceToNow, parseISO } from "date-fns";
import { beginMutation } from "@/lib/api";
import { resolveTaskWorkspaceTarget } from "@/lib/taskWorkspaceTarget";
import { openViewer } from "@/lib/viewer/openViewer";
import { GoalArtifactsToggle, GoalBadge } from "@/components/GoalArtifactsPanel";

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
      todo_retry_count?: number | string | null;
    };
    // Set by the dashboard's Dispatch Mission UI when target_agent=vp.coder.primary
    // (see gateway_server.dashboard_todolist_quick_add) or by source_kind eligibility
    // (cody_demo_task etc., see services/self_briefing.GOAL_ELIGIBLE_SOURCE_KINDS).
    // Surfaced in the card via the /goal badge + GoalArtifactsPanel.
    use_goal_loop?: boolean;
    workflow_manifest?: {
      target_agent?: string;
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
  mission_summaries?: MissionSummary[];
};

type MissionChildSummary = {
  task_id: string;
  title?: string;
  status?: string;
  source_kind?: string;
  subtask_role?: string;
  phase_id?: string;
  canonical_execution_run_id?: string | null;
  canonical_execution_workspace?: string | null;
};

type MissionSummary = {
  workstream_id: string;
  root_task_id?: string;
  mission_title?: string;
  mission_status?: string;
  current_phase_id?: string | null;
  current_phase_title?: string | null;
  current_child_task_id?: string | null;
  child_counts?: {
    total?: number;
    open?: number;
    in_progress?: number;
    needs_review?: number;
    completed?: number;
    blocked?: number;
  };
  latest_artifacts?: string[];
  latest_workspace_dir?: string | null;
  latest_run_id?: string | null;
  children?: MissionChildSummary[];
  root_task?: {
    task_id?: string;
    title?: string;
    status?: string;
    source_kind?: string;
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
  mission_summary?: MissionSummary | null;
  mission_parent?: {
    task_id?: string;
    title?: string;
    status?: string;
    source_kind?: string;
  } | null;
  mission_children?: MissionChildSummary[];
  mission_workstream?: string | null;
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
  // Wall clock as state so render-time comparisons stay pure. 15s cadence is
  // fine — the only consumer is a 120s "heartbeat overdue" threshold.
  const [nowSec, setNowSec] = useState(() => Math.floor(Date.now() / 1000));
  const [error, setError] = useState("");
  const [countdown, setCountdown] = useState(AUTO_REFRESH_SECONDS);

  const [overview, setOverview] = useState<OverviewPayload | null>(null);

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
  // Hydrate deleted-task IDs from localStorage at mount via the useState
  // initializer (runs once, before paint, no effect-triggered setState).
  // SSR-safe: `window` is undefined server-side, fall through to empty set.
  const [deletedTaskIds, setDeletedTaskIds] = useState<Set<string>>(() => {
    if (typeof window === "undefined") return new Set();
    try {
      const stored = window.localStorage.getItem("ua.deleted_completed_tasks.v1");
      return stored ? new Set(JSON.parse(stored) as string[]) : new Set();
    } catch {
      return new Set();
    }
  });
  const [deleteAllPending, setDeleteAllPending] = useState(false);
  const [deleteAllNotAssignedPending, setDeleteAllNotAssignedPending] = useState(false);
  const [hoveredDeleteId, setHoveredDeleteId] = useState<string | null>(null);
  const [quickAddTitle, setQuickAddTitle] = useState("");
  const [quickAddPending, setQuickAddPending] = useState(false);
  const [expandedTaskId, setExpandedTaskId] = useState<string | null>(null);

  // Hermes Phase B.2 — operator failure-context for the unstick verbs.
  // Fetched lazily when a needs_review / blocked task drawer opens so the
  // operator sees last error, retry counters, prior assignments, and the
  // re_evaluation_context block before deciding which unstick verb to call.
  type FailureContext = {
    task_id: string;
    status: string | null;
    last_disposition: string;
    last_disposition_reason: string;
    heartbeat_retry_count: number;
    todo_retry_count: number;
    heartbeat_retry_limit: number | null;
    todo_retry_limit: number | null;
    last_side_effect_summary: string;
    re_evaluation_context: Record<string, unknown> | null;
    revision_round: number;
    rehydrated_at: string;
    rehydrated_by: string;
    max_retries: number | null;
    prior_assignments: Array<{
      assignment_id: string;
      agent_id: string | null;
      state: string | null;
      started_at: string | null;
      ended_at: string | null;
      result_summary: string | null;
    }>;
    prior_runs: Array<{
      run_id: string;
      task_id: string;
      assignment_id: string | null;
      agent_id: string | null;
      started_at: string | null;
      ended_at: string | null;
      outcome: string | null;
      summary: string | null;
      error: string | null;
      metadata: Record<string, unknown>;
    }>;
  };
  const [failureContext, setFailureContext] = useState<FailureContext | null>(null);
  const [failureContextLoading, setFailureContextLoading] = useState(false);

  // Single close-drawer callback used everywhere so failureContext clears
  // synchronously at the user action — not as a derived effect from
  // expandedTaskId, which would put setState in the effect body.
  const closeDrawer = useCallback(() => {
    setExpandedTaskId(null);
    setFailureContext(null);
  }, []);

  useEffect(() => {
    if (!expandedTaskId) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeDrawer();
    };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [expandedTaskId, closeDrawer]);


  // Persist deletedTaskIds to localStorage whenever it changes. Initial-mount
  // write is a no-op (same value just read from storage).
  useEffect(() => {
    try {
      localStorage.setItem("ua.deleted_completed_tasks.v1", JSON.stringify([...deletedTaskIds]));
    } catch { /* ignore */ }
  }, [deletedTaskIds]);

  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const countdownRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (background = false) => {
    // Yield to a microtask before any setState so callers can `void load(...)`
    // from a useEffect body without tripping react-hooks/set-state-in-effect.
    // The delay is imperceptible (<1ms) and preserves the loading-indicator UX.
    await Promise.resolve();
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

  // Wall-clock ticker — feeds nowSec so render-time time comparisons stay pure.
  useEffect(() => {
    const id = setInterval(() => setNowSec(Math.floor(Date.now() / 1000)), 15_000);
    return () => clearInterval(id);
  }, []);

  // Auto-refresh timer. The initial load is kicked off via a 0ms setTimeout so
  // it leaves the effect's synchronous tick before any setState fires — the
  // intervals only call setState inside their own callbacks (also outside the
  // effect body), keeping us clean of react-hooks/set-state-in-effect.
  useEffect(() => {
    const kickoff = setTimeout(() => { void load(false); }, 0);
    intervalRef.current = setInterval(() => {
      setCountdown(AUTO_REFRESH_SECONDS);
      void load(true);
    }, AUTO_REFRESH_SECONDS * 1000);
    countdownRef.current = setInterval(() => {
      setCountdown((c) => (c <= 1 ? AUTO_REFRESH_SECONDS : c - 1));
    }, 1000);
    return () => {
      clearTimeout(kickoff);
      if (intervalRef.current) clearInterval(intervalRef.current);
      if (countdownRef.current) clearInterval(countdownRef.current);
    };
  }, [load]);

  const handleTaskAction = useCallback(async (taskId: string, action: string, extra?: { reason?: string; note?: string }) => {
    setActionPendingTaskId(taskId);
    setOpenActionMenuId(null);
    try {
      const body: Record<string, string> = { action };
      if (extra?.reason) body.reason = extra.reason;
      if (extra?.note) body.note = extra.note;
      const res = await fetch(`${API_BASE}/api/v1/dashboard/todolist/tasks/${encodeURIComponent(taskId)}/action`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
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

  // Hermes Phase B.2 — fetch failure context for a wedged task so the
  // operator can decide which unstick verb to call (rehydrate /
  // re_evaluate / redirect_to / request_revision).
  const handleFetchFailureContext = useCallback(async (taskId: string) => {
    setFailureContextLoading(true);
    setFailureContext(null);
    try {
      const res = await fetch(
        `${API_BASE}/api/v1/dashboard/todolist/tasks/${encodeURIComponent(taskId)}/failure-context`,
        { cache: "no-store" },
      );
      if (!res.ok) {
        const payload = await res.json().catch(() => ({}));
        throw new Error(String(payload?.detail || `Failed to load failure context (${res.status})`));
      }
      const payload = (await res.json()) as FailureContext;
      setFailureContext(payload);
    } catch (err: any) {
      setError(err?.message || "Failed to load failure context.");
    } finally {
      setFailureContextLoading(false);
    }
  }, []);

  // Hermes Phase B.2 — unstick verb dispatcher. Wraps handleTaskAction
  // with verb-specific input prompts for redirect_to + request_revision.
  const handleUnstickVerb = useCallback(async (taskId: string, verb: string) => {
    let reason: string | undefined;
    let note: string | undefined;
    if (verb === "redirect_to") {
      // Caller provides the target VP slug (e.g. "vp.general.primary").
      const target = window.prompt(
        "Redirect to which agent? (e.g. vp.general.primary, vp.coder.primary, simone)",
        "vp.general.primary",
      );
      if (!target) return;
      reason = target.trim();
      if (!reason) return;
    } else if (verb === "request_revision") {
      // Caller provides feedback text.
      const feedback = window.prompt(
        "Revision feedback for the next agent attempt:",
        "",
      );
      if (!feedback) return;
      note = feedback.trim();
      if (!note) return;
    }
    await handleTaskAction(taskId, verb, { reason, note });
    // Refresh the failure context after the verb fires so the drawer
    // shows the post-rehydrate state (counters reset, revision_round
    // bumped, etc.) without requiring a manual reload.
    await handleFetchFailureContext(taskId);
  }, [handleTaskAction, handleFetchFailureContext]);

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

  // Quarantined-email card verbs: Archive (status=completed, stale=archived)
  // and Delete (status=cancelled, stale=dismissed). Both flow through the
  // existing dashboard endpoints so the Kanban / Mission Control views stay
  // consistent. Reload after success so the card disappears from this view.
  const handleArchiveQuarantined = useCallback(
    async (taskId: string) => {
      setActionPendingTaskId(taskId);
      try {
        const res = await fetch(
          `${API_BASE}/api/v1/dashboard/todolist/archive/${encodeURIComponent(taskId)}`,
          { method: "POST" },
        );
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          throw new Error(String(payload?.detail || `Archive failed (${res.status})`));
        }
        await load(true);
      } catch (err: any) {
        setError(err?.message || "Failed to archive task.");
      } finally {
        setActionPendingTaskId("");
      }
    },
    [load],
  );

  const handleDismissQuarantined = useCallback(
    async (taskId: string) => {
      if (typeof window !== "undefined" && !window.confirm("Delete this quarantined item? This cannot be undone.")) {
        return;
      }
      setActionPendingTaskId(taskId);
      try {
        const res = await fetch(
          `${API_BASE}/api/v1/dashboard/todolist/dismiss/${encodeURIComponent(taskId)}`,
          { method: "DELETE" },
        );
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          throw new Error(String(payload?.detail || `Delete failed (${res.status})`));
        }
        await load(true);
      } catch (err: any) {
        setError(err?.message || "Failed to delete task.");
      } finally {
        setActionPendingTaskId("");
      }
    },
    [load],
  );

  const handleDeleteAllCompleted = useCallback(async () => {
    setDeleteAllPending(true);
    const releaseMutation = beginMutation();
    const ids = (completedTasks?.items || []).map((i) => i.task_id);
    // Merge into existing set so previously-deleted IDs aren't lost
    setDeletedTaskIds((prev) => {
      const merged = new Set(prev);
      for (const id of ids) merged.add(id);
      return merged;
    });
    try {
      // Bulk endpoint parks EVERY status=completed row in one UPDATE. The
      // per-task loop only cleared the ~80 currently-loaded items, leaving
      // older completions to bubble up on reload — looking like the cleared
      // cards were coming back.
      await fetch(`${API_BASE}/api/v1/dashboard/todolist/completed`, { method: "DELETE" });
    } catch {
      // noop
    } finally {
      // Reload so the backend-hidden tasks are also gone from server-side data
      await load(true);
      releaseMutation();
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
    () => allQueueItems.filter((i) => String(i.board_lane || "") === "in_progress"),
    [allQueueItems],
  );
  const needsReviewItems = useMemo(
    () => allQueueItems.filter((i) => String(i.board_lane || "") === "needs_review"),
    [allQueueItems],
  );
  const blockedItems = useMemo(
    () => allQueueItems.filter((i) => String(i.board_lane || "") === "blocked"),
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
  const missionSummaries = useMemo(
    () => Array.isArray(overview?.mission_summaries) ? overview!.mission_summaries : [],
    [overview],
  );

  const handleDeleteAllNotAssigned = useCallback(async () => {
    if (!notAssignedItems.length) return;
    const confirmed = window.confirm(
      `Park all ${notAssignedItems.length} unassigned task${notAssignedItems.length === 1 ? "" : "s"}? They will be moved out of the active board.`,
    );
    if (!confirmed) return;
    setDeleteAllNotAssignedPending(true);
    const releaseMutation = beginMutation();
    try {
      await Promise.allSettled(
        notAssignedItems.map((item) =>
          fetch(`${API_BASE}/api/v1/dashboard/todolist/tasks/${encodeURIComponent(item.task_id)}/action`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ action: "park", reason: "bulk_delete_unassigned" }),
          }),
        ),
      );
    } catch {
      // noop — best-effort
    } finally {
      await load(true);
      releaseMutation();
      setDeleteAllNotAssignedPending(false);
    }
  }, [notAssignedItems, load]);

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
      const secsUntil = nextRun - nowSec;
      if (secsUntil < 0 && Math.abs(secsUntil) > 120) {
        alerts.push("Heartbeat overdue");
      }
    }
    if (hb.session_state_count === 0 && hb.session_count === 0) alerts.push("No sessions running");
    return alerts;
  }, [overview?.heartbeat, nowSec]);

  const todoDispatch = overview?.todo_dispatch;
  const lastResultState = String(todoDispatch?.last_result_state || todoDispatch?.last_dispatch_decision || "").trim();
  const lastResultDisplay = lastResultState || "No execution recorded";
  const lastResultSub = todoDispatch?.last_result_at
    ? `${formatTs(todoDispatch?.last_result_at || null)} · ${todoDispatch?.last_result_session_id || "unknown"}`
    : todoDispatch?.last_submitted_at
      ? `${formatTs(todoDispatch?.last_submitted_at || null)} · ${todoDispatch?.last_submitted_session_id || "unknown"}`
      : (todoDispatch?.last_failure_error || "No execution recorded");
  const lastResultClass = (() => {
    const state = lastResultState.toLowerCase();
    if (["completed", "delegated", "awaiting_final_delivery", "awaiting_review"].includes(state)) return "text-kcd-green";
    if (["accepted", "triaged", "in_progress"].includes(state)) return "text-kcd-cyan";
    if (["deferred", "cancelled"].includes(state)) return "text-kcd-amber";
    if (["failed", "invalid"].includes(state)) return "text-kcd-red";
    return "text-kcd-text";
  })();
  const todoDispatchAlerts = useMemo(() => {
    const alerts: string[] = [];
    if (todoDispatch?.sleeping_session_warning) {
      alerts.push("Last wake targeted a session that was not registered");
    }
    if (Number(todoDispatch?.pending_wake_count || 0) > 0) {
      alerts.push(`${todoDispatch?.pending_wake_count || 0} pending wake request${Number(todoDispatch?.pending_wake_count || 0) === 1 ? "" : "s"}`);
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
    // The dispatcher's exception handler at todo_dispatch_service.py:942-965
    // correctly marks an assignment as `failed` + reopens for retry whenever
    // *anything* between claim and successful execution_callback raises
    // (transient WebSocket hiccup, lock contention, network blip). On the
    // next dispatcher tick the task is re-claimed and usually succeeds —
    // standard self-healing. The data state is correct.
    //
    // What was wrong was the UI: it flashed the loud red "REOPENED · Last
    // run failed" tag on every first-cycle transient failure, so a brand-new
    // task that succeeded on retry looked alarming. Now the tag only fires
    // after MULTIPLE attempts have failed (retry_count > 1) — that's a
    // genuinely-stuck retry loop the user should investigate. A first-cycle
    // transient that auto-recovered is no longer surfaced as a failure.
    const todoRetryCount = Number(lastDispatch?.todo_retry_count || 0);
    const wasReopenedAfterFailure =
      boardLane === "not_assigned" &&
      String(lastDispatch?.last_assignment_state || "").toLowerCase() === "failed" &&
      todoRetryCount > 1;
      
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
              <span className="font-mono text-[9px] text-red-300/80 ml-2 truncate">
                from: {senderEmail}
              </span>
            )}
            {isQuarantined && (
              <div className="ml-auto flex items-center gap-1 shrink-0">
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleArchiveQuarantined(item.task_id);
                  }}
                  disabled={isPending}
                  title="Archive — flip to completed (audit retained)"
                  className="font-mono text-[9px] font-bold tracking-[0.1em] uppercase px-2 py-0.5 rounded-sm border border-red-400/40 bg-red-500/10 text-red-300 hover:bg-red-500/20 hover:text-red-200 disabled:opacity-50 transition-colors"
                >
                  Archive
                </button>
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    void handleDismissQuarantined(item.task_id);
                  }}
                  disabled={isPending}
                  title="Delete — cancel and remove from active queues"
                  className="font-mono text-[9px] font-bold tracking-[0.1em] uppercase px-2 py-0.5 rounded-sm border border-red-400/60 bg-red-500/20 text-red-200 hover:bg-red-500/30 hover:text-red-100 disabled:opacity-50 transition-colors"
                >
                  Delete
                </button>
              </div>
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
              {/* /goal badge — visible when the operator-dispatched task is /goal-eligible
                  (dashboard auto-sets use_goal_loop for vp.coder.primary targets, or the
                  source_kind is in GOAL_ELIGIBLE_SOURCE_KINDS like cody_demo_task). */}
              <GoalBadge active={Boolean(item.metadata?.use_goal_loop)} />
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
            {/* /goal-flow artifacts: lets the operator inspect the progression
                user prompt → BRIEF → ACCEPTANCE → goal_condition → COMPLETION
                without leaving the dashboard. Lazy-fetched on click. */}
            {item.metadata?.use_goal_loop && (
              <GoalArtifactsToggle taskId={item.task_id} />
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
                  // Workspace button on a Task Hub card is a REHYDRATE
                  // request: the task may be completed, paused, or
                  // mid-flight, but the user wants the canonical
                  // three-panel view of its trace. Do NOT pass
                  // attachMode:"tail" — that forces the ops-tail HTTP
                  // endpoint, which returns flat log lines that render
                  // as LogRows instead of the structured ToolRow cards
                  // (Input/Result collapsibles). The default mode lets
                  // app/page.tsx pull durable run.log + trace.json,
                  // populating tool calls, assistant turns, thinking
                  // blocks via extractHistoryFromTraceJson.
                  void openViewer({
                    session_id: target.sessionId,
                    run_id: target.runId,
                    role: "viewer",
                  });
                }}
                  className="px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-emerald-500/10 text-emerald-400 border-none rounded-sm cursor-pointer hover:bg-emerald-500/20 transition-colors inline-flex items-center gap-1">
                  <span className="text-[10px]">📂</span> Workspace
                </button>
              );
            })()}
            <button onClick={(e) => { e.stopPropagation(); void handleOpenTaskHistory(item.task_id); }} disabled={taskHistoryLoadingId === item.task_id}
              className="px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-cyan/10 text-kcd-cyan border-none rounded-sm cursor-pointer hover:bg-kcd-cyan/20 transition-colors disabled:opacity-40">
              {taskHistoryLoadingId === item.task_id ? "Loading…" : "📜 History"}
            </button>
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
          onClick={(e) => { e.stopPropagation(); closeDrawer(); }}
        >
          <div
            className="relative w-full max-w-2xl mx-4 rounded-xl border border-white/20 bg-kcd-surface-dim/95 backdrop-blur-lg shadow-2xl p-6 animate-in zoom-in-95 slide-in-from-bottom-2 duration-200"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => closeDrawer()}
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
                <button onClick={(e) => { e.stopPropagation(); void handleTaskAction(item.task_id, "park"); closeDrawer(); }} disabled={isPending}
                  className="px-3 py-1.5 font-mono text-[11px] font-bold tracking-wider uppercase bg-kcd-red/10 text-kcd-red border border-kcd-red/20 rounded-md cursor-pointer hover:bg-kcd-red/20 transition-colors disabled:opacity-40">
                  🗑 Trash It
                </button>
                <button onClick={(e) => { e.stopPropagation(); void handleWakeHeartbeat(item.task_id); closeDrawer(); }} disabled={wakePending}
                  className="px-3 py-1.5 font-mono text-[11px] font-bold tracking-wider uppercase bg-kcd-cyan/10 text-kcd-cyan border border-kcd-cyan/20 rounded-md cursor-pointer hover:bg-kcd-cyan/20 transition-colors disabled:opacity-40">
                  ⚡ {wakePending ? "Queueing…" : "Dispatch"}
                </button>
              </div>
            )}

            {/* Hermes Phase B.2 — failure-context block + unstick verb buttons.
                Visible only when the task is wedged in needs_review or blocked.
                Operator-only verbs; Simone-callable tool versions ship in Phase D. */}
            {(item.status === "needs_review" || item.status === "blocked") && (
              <div className="mt-4 p-3 border border-kcd-amber/30 bg-kcd-amber/5 rounded-md">
                <div className="flex items-center justify-between mb-2">
                  <h4 className="font-mono text-[10px] font-bold tracking-[0.1em] text-kcd-amber uppercase m-0">
                    Failure Context
                  </h4>
                  <button
                    onClick={(e) => { e.stopPropagation(); void handleFetchFailureContext(item.task_id); }}
                    disabled={failureContextLoading}
                    className="px-2 py-0.5 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-amber/10 text-kcd-amber border border-kcd-amber/20 rounded cursor-pointer hover:bg-kcd-amber/20 transition-colors disabled:opacity-40"
                  >
                    {failureContextLoading ? "Loading…" : (failureContext?.task_id === item.task_id ? "↻ Refresh" : "Load Context")}
                  </button>
                </div>
                {failureContext?.task_id === item.task_id && (
                  <div className="space-y-2 text-[11px] font-mono text-kcd-text-muted">
                    {failureContext.last_disposition_reason && (
                      <div>
                        <span className="text-kcd-text-dim">Last error:</span>{" "}
                        <span className="text-kcd-amber">{failureContext.last_disposition_reason}</span>
                      </div>
                    )}
                    <div className="flex flex-wrap gap-3">
                      <span>Heartbeat retries: <span className="text-kcd-text">{failureContext.heartbeat_retry_count}</span>{failureContext.heartbeat_retry_limit !== null ? ` / ${failureContext.heartbeat_retry_limit}` : ""}</span>
                      <span>ToDo retries: <span className="text-kcd-text">{failureContext.todo_retry_count}</span>{failureContext.todo_retry_limit !== null ? ` / ${failureContext.todo_retry_limit}` : ""}</span>
                      {failureContext.revision_round > 0 && (
                        <span>Revision round: <span className="text-kcd-text">{failureContext.revision_round}</span></span>
                      )}
                      {failureContext.max_retries !== null && (
                        <span>Max retries: <span className="text-kcd-text">{failureContext.max_retries}</span></span>
                      )}
                    </div>
                    {failureContext.last_side_effect_summary && (
                      <div>
                        <span className="text-kcd-text-dim">Side-effect evidence:</span>{" "}
                        <span className="text-kcd-text">{failureContext.last_side_effect_summary}</span>
                      </div>
                    )}
                    {failureContext.prior_assignments.length > 0 && (
                      <details className="mt-1">
                        <summary className="text-kcd-text-dim cursor-pointer hover:text-kcd-amber">
                          Prior assignments ({failureContext.prior_assignments.length})
                        </summary>
                        <ul className="mt-1 ml-3 space-y-1">
                          {failureContext.prior_assignments.map((a) => (
                            <li key={a.assignment_id} className="text-[10px]">
                              <span className="text-kcd-text">{a.agent_id || "?"}</span>
                              <span className="opacity-50"> · </span>
                              <span>{a.state || "?"}</span>
                              {a.result_summary && (
                                <>
                                  <span className="opacity-50"> · </span>
                                  <span className="text-kcd-text-dim">{a.result_summary.slice(0, 80)}</span>
                                </>
                              )}
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                    {failureContext.prior_runs.length > 0 && (
                      <details className="mt-1" open>
                        <summary className="text-kcd-text-dim cursor-pointer hover:text-kcd-amber">
                          Attempt history ({failureContext.prior_runs.length})
                        </summary>
                        <ul className="mt-1 ml-3 space-y-1.5">
                          {failureContext.prior_runs.map((r) => {
                            const isFailed = r.outcome && r.outcome !== "completed";
                            const outcomeClass = r.outcome === "completed"
                              ? "text-kcd-green"
                              : isFailed
                                ? "text-kcd-red"
                                : "text-kcd-text-dim";
                            return (
                              <li key={r.run_id} className="text-[10px] border-l border-kcd-text-dim/30 pl-2">
                                <div>
                                  <span className="text-kcd-text">{r.agent_id || "?"}</span>
                                  <span className="opacity-50"> · </span>
                                  <span className={outcomeClass}>{r.outcome || "in_progress"}</span>
                                  {r.started_at && (
                                    <>
                                      <span className="opacity-50"> · </span>
                                      <span className="text-kcd-text-dim">{r.started_at.slice(0, 19)}</span>
                                    </>
                                  )}
                                </div>
                                {r.summary && (
                                  <div className="text-kcd-text-dim ml-1 mt-0.5">{r.summary.slice(0, 140)}</div>
                                )}
                                {r.error && (
                                  <div className="text-kcd-red/80 ml-1 mt-0.5">error: {r.error.slice(0, 140)}</div>
                                )}
                              </li>
                            );
                          })}
                        </ul>
                      </details>
                    )}
                    {failureContext.rehydrated_at && (
                      <div className="text-[10px] italic text-kcd-text-dim">
                        Last rehydrated {failureContext.rehydrated_at.slice(0, 19)} by {failureContext.rehydrated_by || "?"}
                      </div>
                    )}
                  </div>
                )}
                {/* Unstick verb buttons — operator-only (Simone-callable in Phase D). */}
                <div className="mt-3 flex flex-wrap gap-2">
                  <button onClick={(e) => { e.stopPropagation(); void handleUnstickVerb(item.task_id, "rehydrate"); }} disabled={isPending}
                    className="px-3 py-1.5 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-cyan/10 text-kcd-cyan border border-kcd-cyan/20 rounded cursor-pointer hover:bg-kcd-cyan/20 transition-colors disabled:opacity-40"
                    title="Clean restart: status → open, retry counters reset. Preserves task body and history.">
                    Rehydrate
                  </button>
                  <button onClick={(e) => { e.stopPropagation(); void handleUnstickVerb(item.task_id, "re_evaluate"); }} disabled={isPending}
                    className="px-3 py-1.5 font-mono text-[10px] font-bold tracking-wider uppercase bg-indigo-500/10 text-indigo-400 border border-indigo-400/20 rounded cursor-pointer hover:bg-indigo-500/20 transition-colors disabled:opacity-40"
                    title="Rehydrate + attach failure-context block so the next agent claim judges from evidence.">
                    Re-evaluate
                  </button>
                  <button onClick={(e) => { e.stopPropagation(); void handleUnstickVerb(item.task_id, "redirect_to"); }} disabled={isPending}
                    className="px-3 py-1.5 font-mono text-[10px] font-bold tracking-wider uppercase bg-purple-500/10 text-purple-400 border border-purple-400/20 rounded cursor-pointer hover:bg-purple-500/20 transition-colors disabled:opacity-40"
                    title="Rehydrate + set metadata.preferred_vp so a different agent picks it up.">
                    Redirect To…
                  </button>
                  <button onClick={(e) => { e.stopPropagation(); void handleUnstickVerb(item.task_id, "request_revision"); }} disabled={isPending}
                    className="px-3 py-1.5 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-amber/10 text-kcd-amber border border-kcd-amber/20 rounded cursor-pointer hover:bg-kcd-amber/20 transition-colors disabled:opacity-40"
                    title="Rehydrate + append revision feedback as a comment + bump revision_round + give max_retries one more attempt.">
                    Request Revision…
                  </button>
                </div>
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
            <h3 className="text-[13px] font-semibold text-kcd-text leading-snug m-0 break-words">
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
            {(() => {
              const raw = item.last_assignment?.result_summary || item.metadata?.dispatch?.last_disposition_reason || "";
              if (!raw) return null;
              // Suppress internal auto-disposition error text on completed cards
              if (raw.startsWith("auto_disposition:") || raw.startsWith("todo_dispatch_failed:")) {
                const reason = item.metadata?.dispatch?.last_disposition_reason || "";
                if (reason === "todo_self_reviewed_after_delivery") {
                  return (
                    <p className="mt-1.5 text-[11px] italic text-kcd-text-dim border-l-2 border-white/10 pl-2 line-clamp-2">
                      ✓ Completed — delivery verified
                    </p>
                  );
                }
                return null; // Hide raw error text for other auto-disposition cases
              }
              return (
                <p className="mt-1.5 text-[11px] italic text-kcd-text-dim border-l-2 border-white/10 pl-2 line-clamp-2">
                  {raw}
                </p>
              );
            })()}
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
          <button onClick={(e) => { e.stopPropagation(); void handleOpenTaskHistory(item.task_id); }} disabled={taskHistoryLoadingId === item.task_id}
            className="px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-kcd-cyan/10 text-kcd-cyan border-none rounded-sm cursor-pointer hover:bg-kcd-cyan/20 transition-colors disabled:opacity-40">
            {taskHistoryLoadingId === item.task_id ? "Loading…" : "Review"}
          </button>
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
                // Rehydrate, not tail — see the inline rationale on the
                // sibling Workspace button above.
                void openViewer({
                  session_id: target.sessionId,
                  run_id: target.runId,
                  role: "viewer",
                });
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
          onClick={(e) => { e.stopPropagation(); closeDrawer(); }}
        >
          <div
            className="relative w-full max-w-2xl mx-4 rounded-xl border border-white/20 bg-kcd-surface-dim/95 backdrop-blur-lg shadow-2xl p-6 animate-in zoom-in-95 slide-in-from-bottom-2 duration-200"
            onClick={(e) => e.stopPropagation()}
          >
            <button
              onClick={() => closeDrawer()}
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

  const renderTaskHistoryPanel = () => (
    <section className="rounded-xl border border-border bg-background/70 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-sky-300">Work Item History</h2>
          <p className="text-xs text-muted-foreground">Assignment/evaluation trail and links to run artifacts.</p>
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
        <p className="text-xs text-muted-foreground italic">Select &quot;Review&quot; on any work item to load run history.</p>
      ) : (
        <div className="space-y-3 text-xs">
          <div className="rounded border border-border/70 bg-background/50 p-2">
            <div className="font-semibold text-foreground flex items-baseline gap-2 min-w-0">
              <span className="shrink-0">{taskHistory.task?.title || taskHistory.task?.task_id || "Work Item"}</span>
              {taskHistory.task?.description && (
                <span className="text-[11px] font-normal text-muted-foreground truncate">— {taskHistory.task.description.slice(0, 100)}{taskHistory.task.description.length > 100 ? "…" : ""}</span>
              )}
            </div>
            <div className="mt-1 flex items-center gap-2 text-muted-foreground">
              <span>{taskHistory.task?.task_id}</span>
              {taskHistory.task?.status && <span className="opacity-40">│</span>}
              {taskHistory.task?.status && <span className="text-[10px] uppercase tracking-wider">{taskHistory.task.status}</span>}
              {taskHistory.task?.board_lane && <><span className="opacity-40">│</span><span className="text-[10px] uppercase tracking-wider">{taskHistory.task.board_lane}</span></>}
              {taskHistory.task?.score !== undefined && <><span className="opacity-40">│</span><span className="text-[10px]">score {taskHistory.task.score}</span></>}
            </div>
          </div>
          {(taskHistory.email_mapping || taskHistory.reconciliation) && (
            <div className="rounded border border-border/70 bg-background/50 p-2">
              <div className="mb-1 text-[10px] uppercase tracking-[0.16em] text-muted-foreground">Forensics</div>
              <div className="space-y-1 text-[11px] text-foreground/80">
                {taskHistory.email_mapping?.thread_id && (
                  <div>
                    Email thread <span className="font-mono text-muted-foreground">{taskHistory.email_mapping.thread_id}</span>
                    {taskHistory.email_mapping.subject ? ` · ${taskHistory.email_mapping.subject}` : ""}
                  </div>
                )}
                {taskHistory.email_mapping?.sender_email && (
                  <div>Sender {taskHistory.email_mapping.sender_email}</div>
                )}
                {taskHistory.email_mapping?.email_sent_at && (
                  <div>Email sent {formatTs(taskHistory.email_mapping.email_sent_at)}</div>
                )}
                {taskHistory.delivery_mode && (
                  <div>Delivery mode {taskHistory.delivery_mode}</div>
                )}
                {taskHistory.canonical_execution?.session_id && (
                  <div>
                    Canonical execution <span className="font-mono text-muted-foreground">{taskHistory.canonical_execution.session_id}</span>
                    {taskHistory.canonical_execution.session_role ? ` · ${taskHistory.canonical_execution.session_role}` : ""}
                  </div>
                )}
                {taskHistory.reconciliation?.orphaned_in_progress && (
                  <div className="text-kcd-red">Flagged orphaned in-progress state</div>
                )}
                {taskHistory.reconciliation?.completion_unverified && (
                  <div className="text-kcd-amber">Completion is unverified and requires Simone review</div>
                )}
                {taskHistory.artifacts?.transcript_href && (
                  <a href={taskHistory.artifacts.transcript_href} className="text-sky-300 hover:underline">
                    Open transcript
                  </a>
                )}
                {taskHistory.artifacts?.run_log_href && (
                  <a href={taskHistory.artifacts.run_log_href} className="text-sky-300 hover:underline">
                    Open run log
                  </a>
                )}
              </div>
            </div>
          )}
          <div className="rounded border border-border/70 bg-background/50 p-2">
            {taskHistory.mission_summary && (
              <>
                <div className="mb-1 text-[10px] uppercase tracking-[0.16em] text-muted-foreground">Mission Context</div>
                <div className="space-y-1 text-[11px] text-foreground/80 mb-2">
                  <div>
                    <span className="font-semibold">{taskHistory.mission_summary.mission_title || taskHistory.mission_parent?.title || taskHistory.mission_workstream}</span>
                    {taskHistory.mission_summary.mission_status ? ` · ${taskHistory.mission_summary.mission_status}` : ""}
                  </div>
                  {taskHistory.mission_summary.current_phase_title && (
                    <div>Current phase {taskHistory.mission_summary.current_phase_title}</div>
                  )}
                  {taskHistory.mission_summary.child_counts && (
                    <div>
                      Children {taskHistory.mission_summary.child_counts.completed || 0}/{taskHistory.mission_summary.child_counts.total || 0} completed
                    </div>
                  )}
                  {(taskHistory.mission_children || []).length > 0 && (
                    <div className="pt-1">
                      <div className="text-[10px] uppercase tracking-[0.12em] text-muted-foreground mb-1">Phases</div>
                      <div className="space-y-1">
                        {(taskHistory.mission_children || []).slice(0, 6).map((child) => (
                          <div key={child.task_id} className="flex items-center gap-2">
                            <span className="font-mono text-[10px] text-muted-foreground">{child.subtask_role || child.phase_id || "phase"}</span>
                            <span>{child.title || child.task_id}</span>
                            <span className="opacity-40">·</span>
                            <span className="text-[10px] uppercase tracking-[0.08em]">{child.status || "unknown"}</span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </>
            )}
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
                    <div className="text-[10px] text-muted-foreground">
                      {row.session_role || "unknown-role"}{row.run_kind ? ` · ${row.run_kind}` : ""}
                    </div>
                    <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    {(() => {
                      const target = resolveTaskWorkspaceTarget({
                        links: row.links,
                        canonical_execution_session_id: row.session_id,
                        workflow_run_id: row.workflow_run_id,
                      });
                      if (!target) return null;
                      return (
                        <button
                          onClick={() => {
                            // Rehydrate, not tail — same rationale as the
                            // primary Workspace button above.
                            void openViewer({
                              session_id: target.sessionId,
                              run_id: target.runId,
                              role: "viewer",
                            });
                          }}
                          className="rounded border border-emerald-700/60 bg-emerald-900/20 px-2 py-0.5 text-[10px] uppercase tracking-wide text-emerald-300 hover:bg-emerald-900/35 cursor-pointer inline-flex items-center gap-1"
                        >
                          <span className="text-[9px]">📂</span> Workspace
                        </button>
                      );
                    })()}
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



        {/* ── NOW: Current Assignments (priority — moved to top) ── */}
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

      {/* ── Consolidated Dispatcher & Stats Strip ── */}
      <section className="backdrop-blur-sm bg-kcd-surface-dim/70 border border-white/[0.06] rounded-lg px-4 py-2.5">
        <div className="flex flex-wrap items-center gap-x-6 gap-y-1.5 font-mono text-[11px]">
          <span className="text-[9px] font-bold tracking-[0.1em] text-kcd-text-muted uppercase shrink-0">Dispatcher</span>
          <span className="text-kcd-text-muted">{formatTs(todoDispatch?.last_wake_requested_at || null) || "No wake"}</span>
          <span className="text-kcd-text-muted">Claim: <strong className={todoDispatch?.last_claimed_at ? "text-kcd-cyan" : "text-kcd-text-muted"}>{todoDispatch?.last_claimed_at ? `${todoDispatch?.last_claimed_task_count || 0} task(s)` : "none"}</strong></span>
          <span className="text-kcd-text-muted">Result: <strong className={lastResultClass}>{lastResultDisplay}</strong></span>
          <span className="text-kcd-text-muted">Wake Q: <strong className={Number(todoDispatch?.pending_wake_count || 0) > 0 ? "text-kcd-amber" : "text-kcd-text"}>{todoDispatch?.pending_wake_count || 0}</strong></span>
          <span className="h-3 w-px bg-white/10 mx-1" />
          <span className="text-[9px] font-bold tracking-[0.1em] text-kcd-text-muted uppercase shrink-0">Stats</span>
          <span className="text-kcd-text-muted">Eligible: <strong className="text-kcd-text">{overview?.queue_health?.dispatch_eligible || 0}</strong></span>
          <span className="text-kcd-text-muted">Agents: <strong className="text-kcd-cyan">{agentActivity?.active_agents || 0}</strong></span>
          <span className="text-kcd-text-muted">Backlog: <strong className="text-kcd-text">{agentActivity?.backlog_open || 0}</strong></span>

          <span className="text-kcd-text-muted">Rate: <strong className={completionRate24h !== null ? (completionRate24h >= 70 ? "text-kcd-green" : completionRate24h >= 40 ? "text-kcd-amber" : "text-kcd-red") : "text-kcd-text-muted"}>{completionRate24h !== null ? `${completionRate24h}%` : "—"}</strong></span>
          <span className="h-3 w-px bg-white/10 mx-1" />
          <span className="text-[9px] font-bold tracking-[0.1em] text-kcd-text-muted uppercase shrink-0">Efficiency</span>
          <span className="text-kcd-text-muted">1h: <strong className="text-kcd-text">{agentMetrics1h?.seized || 0}</strong>s · <strong className="text-kcd-cyan">{agentMetrics1h?.completed || 0}</strong>d · <strong className="text-kcd-red">{agentMetrics1h?.rejected || 0}</strong>r</span>
          <span className="text-kcd-text-muted">24h: <strong className="text-kcd-text">{agentMetrics24h?.seized || 0}</strong>s · <strong className="text-kcd-cyan">{agentMetrics24h?.completed || 0}</strong>d · <strong className="text-kcd-red">{agentMetrics24h?.rejected || 0}</strong>r</span>
        </div>
        {(todoDispatchAlerts.length > 0 || heartbeatAlerts.length > 0) && (
          <div className="mt-1.5 flex flex-wrap gap-2">
            {todoDispatchAlerts.map((alert, idx) => (
              <span key={`todo-alert-${idx}`} className="font-mono text-[10px] text-kcd-amber">⚠ {alert}</span>
            ))}
            {heartbeatAlerts.map((alert, idx) => (
              <span key={`hb-alert-${idx}`} className="font-mono text-[10px] text-kcd-text-muted">♥ {alert}</span>
            ))}
          </div>
        )}
      </section>

      {/* ── Kanban Time Horizon Board ──
       *
       * 5-column layout (post-2026-05-12). The 5th column ("Blocked")
       * was added because quarantined-email cards (status='blocked',
       * board_lane='blocked') were previously filtered out of every
       * Kanban lane and only surfaced as a 1-line counter at the bottom.
       * That made the inline Archive / Delete verbs added in PR #255
       * unreachable through the UI — the operator had no way to clear
       * a quarantined card without going through chat or the API.
       *
       * On screens narrower than xl the columns stack to single-column
       * (grid-cols-1); operator workflow on small viewports is unchanged.
       */}
      <div className="grid gap-3 grid-cols-1 xl:grid-cols-5" onClick={(e) => e.stopPropagation()}>
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
        <KanbanCol
          label="Blocked"
          icon="block"
          count={blockedItems.length}
          accentColor="#EF4444"
          emptyText="No blocked items. Quarantined emails and other holds will appear here."
        >
          {blockedItems.map((item, idx) => renderTaskCard(item, idx, true))}
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

      {missionSummaries.length > 0 && (
        <section className="backdrop-blur-sm bg-kcd-surface-dim/70 border border-white/[0.06] rounded-lg p-4">
          <div className="flex items-center justify-between gap-2 mb-3">
            <div>
              <h2 className="font-mono text-[11px] font-bold tracking-[0.1em] text-kcd-text-dim uppercase m-0">Mission Summaries</h2>
              <p className="text-xs text-kcd-text-muted m-0">Meaningful multi-phase work grouped above the child-task lanes.</p>
            </div>
            <span className="font-mono text-[10px] text-kcd-text-muted">{missionSummaries.length} active/recent</span>
          </div>
          <div className="grid gap-3 grid-cols-1 xl:grid-cols-2">
            {missionSummaries.map((mission) => {
              const currentChild = (mission.children || []).find((child) => child.task_id === mission.current_child_task_id) || mission.children?.[0];
              return (
                <div key={mission.workstream_id} className="rounded border border-border/70 bg-background/50 p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="font-semibold text-foreground truncate">{mission.mission_title || mission.workstream_id}</div>
                      <div className="mt-1 flex items-center gap-2 text-[10px] text-muted-foreground">
                        <span className="uppercase tracking-[0.08em]">{mission.mission_status || "open"}</span>
                        {mission.current_phase_title && <><span className="opacity-40">│</span><span>{mission.current_phase_title}</span></>}
                        {mission.child_counts?.total !== undefined && (
                          <><span className="opacity-40">│</span><span>{mission.child_counts.completed || 0}/{mission.child_counts.total || 0}</span></>
                        )}
                      </div>
                    </div>
                    {currentChild && (
                      <button
                        onClick={() => {
                          const target = resolveTaskWorkspaceTarget({
                            canonical_execution_run_id: currentChild.canonical_execution_run_id || undefined,
                            workflow_run_id: currentChild.canonical_execution_run_id || undefined,
                          });
                          if (target) {
                            void openViewer({
                              session_id: target.sessionId,
                              run_id: target.runId,
                              role: "viewer",
                            });
                          }
                        }}
                        className="px-2.5 py-1 font-mono text-[10px] font-bold tracking-wider uppercase bg-emerald-500/10 text-emerald-400 border-none rounded-sm cursor-pointer hover:bg-emerald-500/20 transition-colors inline-flex items-center gap-1"
                      >
                        <span className="text-[10px]">📂</span> Current Phase
                      </button>
                    )}
                  </div>
                  {(mission.latest_artifacts || []).length > 0 && (
                    <div className="mt-2 text-[11px] text-muted-foreground">
                      Latest artifacts: {(mission.latest_artifacts || []).slice(0, 3).join(", ")}
                    </div>
                  )}
                  {(mission.children || []).length > 0 && (
                    <div className="mt-3 space-y-1">
                      {(mission.children || []).slice(0, 4).map((child) => (
                        <div key={child.task_id} className="flex items-center gap-2 text-[11px]">
                          <span className="font-mono text-[10px] text-muted-foreground w-24 shrink-0 truncate">{child.subtask_role || child.phase_id || "phase"}</span>
                          <span className="truncate flex-1">{child.title || child.task_id}</span>
                          <span className="text-[10px] uppercase tracking-[0.08em] text-muted-foreground">{child.status || "open"}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      )}

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

      {/* ── Work Item History Detail ── */}
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
