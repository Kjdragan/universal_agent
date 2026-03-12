"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { formatDistanceToNow, parseISO } from "date-fns";

const API_BASE = "/api/dashboard/gateway";
const MODE_STORAGE_KEY = "ua_todolist_mode_v2";

type Mode = "agent" | "personal" | "split";

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
  collapsed_count?: number;
  score?: number;
  score_confidence?: number;
  stale_state?: string;
  seizure_state?: string;
  mirror_status?: string;
  updated_at?: string;
  due_at?: string | null;
  source_kind?: string;
  metadata?: {
    csi?: {
      routing_state?: string;
      human_intervention_reason?: string;
    };
  };
};

type PersonalQueueItem = {
  task_id: string;
  title: string;
  description?: string;
  project_key?: string;
  priority?: number;
  labels?: string[];
  status?: string;
  updated_at?: string;
  due_at?: string | null;
  source_kind?: string;
  metadata?: {
    csi?: {
      routing_state?: string;
      human_intervention_reason?: string;
    };
  };
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
      rejection_reasons?: Array<{ reason: string; count: number }>;
    };
  };
  backlog_open: number;
};

type OverviewPayload = {
  status: string;
  mode_default?: Mode;
  approvals_pending?: number;
  queue_health?: {
    dispatch_queue_size: number;
    dispatch_eligible: number;
    threshold?: number;
    csi_agent_actionable_open?: number;
    csi_human_open?: number;
    csi_incubating_hidden?: number;
    status_counts: Record<string, number>;
    source_counts: Record<string, number>;
  };
  csi_incident_summary?: { open_incidents: number };
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
  banner?: {
    show: boolean;
    text: string;
    focus_href: string;
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

type PersonalQueuePayload = {
  status: string;
  items: PersonalQueueItem[];
  approval_priority_rows: ApprovalRow[];
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

function usePersistedMode(defaultMode: Mode): [Mode, (next: Mode) => void] {
  const [mode, setMode] = useState<Mode>(() => {
    if (typeof window === "undefined") return defaultMode;
    try {
      const stored = localStorage.getItem(MODE_STORAGE_KEY);
      if (stored === "agent" || stored === "personal" || stored === "split") {
        return stored;
      }
    } catch {
      // noop
    }
    return defaultMode;
  });

  const setPersisted = useCallback((next: Mode) => {
    setMode(next);
    try {
      localStorage.setItem(MODE_STORAGE_KEY, next);
    } catch {
      // noop
    }
  }, []);

  return [mode, setPersisted];
}

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

function formatEvery(seconds?: number): string {
  const value = Number(seconds || 0);
  if (!value || Number.isNaN(value)) return "n/a";
  if (value % 3600 === 0) return `${value / 3600}h`;
  if (value % 60 === 0) return `${value / 60}m`;
  return `${value}s`;
}

function scoreBadge(score?: number): string {
  const value = Number(score || 0);
  if (value >= 9) return "9-10";
  if (value >= 8) return "8";
  if (value >= 7) return "7";
  return "<7";
}

function priorityText(priority?: number): string {
  const p = Number(priority || 1);
  if (p >= 4) return "Urgent";
  if (p === 3) return "High";
  if (p === 2) return "Medium";
  return "Normal";
}

function isGatewayUpstreamUnavailable(status: number, detail: string): boolean {
  if (status !== 502) return false;
  return detail.toLowerCase().includes("gateway upstream unavailable");
}

export default function ToDoListDashboardPage() {
  const searchParams = useSearchParams();
  const modeParam = String(searchParams?.get("mode") || "").trim().toLowerCase();
  const focus = String(searchParams?.get("focus") || "").trim().toLowerCase();

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const [overview, setOverview] = useState<OverviewPayload | null>(null);
  const [approvalsHighlight, setApprovalsHighlight] = useState<ApprovalHighlightPayload | null>(null);
  const [agentQueue, setAgentQueue] = useState<AgentQueuePayload | null>(null);
  const [personalQueue, setPersonalQueue] = useState<PersonalQueuePayload | null>(null);
  const [agentActivity, setAgentActivity] = useState<AgentActivity | null>(null);
  const [completedTasks, setCompletedTasks] = useState<CompletedTasksPayload | null>(null);

  const [mode, setMode] = usePersistedMode("agent");
  const [includeCsi, setIncludeCsi] = useState(true);
  const [collapseCsi, setCollapseCsi] = useState(true);
  const [showNonCsiOnly, setShowNonCsiOnly] = useState(false);
  const [openActionMenuId, setOpenActionMenuId] = useState<string | null>(null);
  const [actionPendingTaskId, setActionPendingTaskId] = useState("");
  const [wakePending, setWakePending] = useState(false);
  const [taskHistory, setTaskHistory] = useState<TaskHistoryPayload | null>(null);
  const [taskHistoryLoadingId, setTaskHistoryLoadingId] = useState("");
  const [selectedTaskDetails, setSelectedTaskDetails] = useState<any | null>(null);

  const approvalsRef = useRef<HTMLDivElement | null>(null);

  const load = useCallback(async (background = false) => {
    if (background) setRefreshing(true);
    else setLoading(true);
    if (!background) setError("");
    try {
      const agentQueueUrl = new URL(`${API_BASE}/api/v1/dashboard/todolist/agent-queue`, window.location.origin);
      agentQueueUrl.searchParams.set("limit", "120");
      agentQueueUrl.searchParams.set("include_csi", includeCsi ? "1" : "0");
      agentQueueUrl.searchParams.set("collapse_csi", collapseCsi ? "1" : "0");

      const [overviewRes, approvalsRes, agentRes, personalRes, activityRes, completedRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/dashboard/todolist/overview`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/v1/dashboard/approvals/highlight`, { cache: "no-store" }),
        fetch(`${agentQueueUrl.pathname}${agentQueueUrl.search}`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/v1/dashboard/todolist/personal-queue?limit=200`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/v1/dashboard/todolist/agent-activity`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/v1/dashboard/todolist/completed?limit=80`, { cache: "no-store" }),
      ]);

      if (!overviewRes.ok || !agentRes.ok || !personalRes.ok || !activityRes.ok || !completedRes.ok) {
        const failures: Array<{ name: string; status: number; detail: string }> = [];
        const required = [
          { name: "overview", res: overviewRes },
          { name: "agent_queue", res: agentRes },
          { name: "personal_queue", res: personalRes },
          { name: "agent_activity", res: activityRes },
          { name: "completed_tasks", res: completedRes },
        ];
        for (const item of required) {
          if (item.res.ok) continue;
          const detail = await item.res.text().catch(() => "");
          failures.push({ name: item.name, status: item.res.status, detail });
        }
        if (
          failures.length > 0
          && failures.every((f) => isGatewayUpstreamUnavailable(f.status, f.detail))
        ) {
          throw new Error("Gateway is temporarily unavailable. Please retry in a few seconds.");
        }
        const compact = failures.map((f) => `${f.name}:${f.status}`).join(", ");
        throw new Error(`To Do V2 endpoints failed to load (${compact})`);
      }

      const [overviewJson, agentJson, personalJson, activityJson, completedJson] = await Promise.all([
        overviewRes.json(),
        agentRes.json(),
        personalRes.json(),
        activityRes.json(),
        completedRes.json(),
      ]);
      const approvalsJson = approvalsRes.ok
        ? await approvalsRes.json()
        : ({
            status: "degraded",
            pending_count: 0,
            approvals: [],
            banner: { show: false, text: "", focus_href: "/dashboard/todolist?mode=personal&focus=approvals" },
          } as ApprovalHighlightPayload);

      setOverview(overviewJson as OverviewPayload);
      setApprovalsHighlight(approvalsJson as ApprovalHighlightPayload);
      setAgentQueue(agentJson as AgentQueuePayload);
      setPersonalQueue(personalJson as PersonalQueuePayload);
      setAgentActivity(activityJson as AgentActivity);
      setCompletedTasks(completedJson as CompletedTasksPayload);

      if (!background && (!localStorage.getItem(MODE_STORAGE_KEY))) {
        const defaultMode = (overviewJson?.mode_default || "agent") as Mode;
        if (defaultMode === "agent" || defaultMode === "personal" || defaultMode === "split") {
          setMode(defaultMode);
        }
      }

      setError("");
    } catch (err: any) {
      if (!background) {
        setError(err?.message || "Failed to load To Do data.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, [collapseCsi, includeCsi, setMode]);

  useEffect(() => {
    void load(false);
  }, [load]);

  useEffect(() => {
    if (modeParam === "agent" || modeParam === "personal" || modeParam === "split") {
      setMode(modeParam);
    }
  }, [modeParam, setMode]);

  useEffect(() => {
    if (focus === "approvals" && approvalsRef.current) {
      approvalsRef.current.scrollIntoView({ behavior: "smooth", block: "start" });
      if (mode !== "personal") setMode("personal");
    }
  }, [focus, mode, setMode]);

  const handleTaskAction = useCallback(async (taskId: string, action: string) => {
    setActionPendingTaskId(taskId);
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

  const filteredAgentItems = useMemo(() => {
    const rows = Array.isArray(agentQueue?.items) ? agentQueue!.items : [];
    if (!showNonCsiOnly) return rows;
    return rows.filter((row) => String(row.source_kind || "") !== "csi");
  }, [agentQueue, showNonCsiOnly]);

  const dispatchThreshold = Number(overview?.queue_health?.threshold || 0);

  const renderAgentPanel = (compact = false) => (
    <section className={`rounded-xl border border-slate-800 bg-slate-900/70 ${compact ? "p-3" : "p-4"}`}>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-sky-300">
            Agent Queue ({filteredAgentItems.length}/{agentQueue?.pagination?.total || filteredAgentItems.length})
          </h2>
          <p className="text-xs text-slate-400">Prioritized internal dispatch queue with CSI incident collapse.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-[11px]">
          <label className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-800/70 px-2 py-1">
            <input type="checkbox" checked={includeCsi} onChange={(e) => setIncludeCsi(e.target.checked)} /> CSI
          </label>
          <label className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-800/70 px-2 py-1">
            <input type="checkbox" checked={collapseCsi} onChange={(e) => setCollapseCsi(e.target.checked)} /> Collapse CSI
          </label>
          <label className="inline-flex items-center gap-1 rounded border border-slate-700 bg-slate-800/70 px-2 py-1">
            <input type="checkbox" checked={showNonCsiOnly} onChange={(e) => setShowNonCsiOnly(e.target.checked)} /> Non-CSI spotlight
          </label>
          <button
            onClick={() => { void load(true); }}
            className="rounded border border-cyan-800/60 bg-cyan-900/20 px-2 py-1 text-cyan-200 hover:bg-cyan-900/35"
          >
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
          <button
            onClick={() => { void handleWakeHeartbeat(); }}
            disabled={wakePending}
            className="rounded border border-emerald-700/60 bg-emerald-900/20 px-2 py-1 text-emerald-200 hover:bg-emerald-900/35 disabled:opacity-50"
          >
            {wakePending ? "Queueing..." : "Run Next Heartbeat"}
          </button>
        </div>
      </div>

      {filteredAgentItems.length > 0 && (overview?.queue_health?.dispatch_eligible || 0) === 0 ? (
        <div className="mb-3 rounded border border-amber-800/60 bg-amber-950/20 px-3 py-2 text-xs text-amber-100">
          Backlog exists, but nothing is dispatchable right now.
          {dispatchThreshold > 0 ? ` Current agent threshold is ${dispatchThreshold}.` : ""}
        </div>
      ) : null}

      <div className="space-y-2 max-h-[56vh] overflow-y-auto pr-1">
        {filteredAgentItems.length === 0 ? (
          <p className="text-sm text-slate-500 italic">No agent queue items available.</p>
        ) : (
          filteredAgentItems.map((item) => (
            <article key={item.task_id} className="rounded-lg border border-slate-800/80 bg-slate-950/60 p-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <h3 className="font-semibold text-slate-200">{item.title}</h3>
                    {item.must_complete ? (
                      <span className="rounded border border-rose-700/60 bg-rose-900/25 px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-wide text-rose-200">Must Complete</span>
                    ) : null}
                    {String(item.source_kind || "") === "csi" ? (
                      <span className="rounded border border-emerald-800/60 bg-emerald-900/20 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-emerald-300">CSI</span>
                    ) : null}
                  </div>
                  {item.description ? (
                    <p className="mt-1 text-xs text-slate-400 line-clamp-2">{item.description}</p>
                  ) : null}
                </div>
                <div className="text-right text-[10px] text-slate-400">
                  <div>{priorityText(item.priority)}</div>
                  <div>score {item.score ?? 0} · Q {item.score_confidence ?? 0}</div>
                  {dispatchThreshold > 0 && Number(item.score ?? 0) < dispatchThreshold ? (
                    <div className="text-amber-300">below threshold {dispatchThreshold}</div>
                  ) : null}
                  {item.collapsed_count && item.collapsed_count > 1 ? (
                    <div className="text-emerald-300">{item.collapsed_count} incident items</div>
                  ) : null}
                </div>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-500">
                <span>{item.project_key}</span>
                <span>•</span>
                <span>{item.status}</span>
                {item.due_at ? (<><span>•</span><span className="text-amber-300">Due {item.due_at}</span></>) : null}
                {item.updated_at ? (<><span>•</span><span>Updated {formatTs(item.updated_at)}</span></>) : null}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <button
                  onClick={() => void handleTaskAction(item.task_id, "complete")}
                  disabled={actionPendingTaskId === item.task_id}
                  className="rounded border border-indigo-700/60 bg-indigo-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-indigo-200 hover:bg-indigo-900/35 disabled:opacity-50"
                >
                  Complete
                </button>
                <button
                  onClick={() => void handleWakeHeartbeat(item.task_id)}
                  disabled={wakePending}
                  className="rounded border border-emerald-700/60 bg-emerald-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-emerald-200 hover:bg-emerald-900/35 disabled:opacity-50"
                >
                  Force Next Heartbeat
                </button>

                <div className="relative ml-2">
                  <button
                    onClick={() => setOpenActionMenuId(openActionMenuId === item.task_id ? null : item.task_id)}
                    className="rounded border border-slate-700 bg-slate-800/80 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-300 hover:bg-slate-700"
                  >
                    Advanced ▾
                  </button>
                  {openActionMenuId === item.task_id && (
                    <div className="absolute right-0 top-full z-10 mt-1 flex w-32 flex-col gap-1 rounded border border-slate-700 bg-slate-900 p-1 shadow-xl">
                      {item.status === "open" && (
                        <button
                          onClick={() => { setOpenActionMenuId(null); void handleTaskAction(item.task_id, "seize"); }}
                          disabled={actionPendingTaskId === item.task_id}
                          className="w-full rounded bg-transparent px-2 py-1 text-left text-[10px] font-semibold uppercase tracking-wide text-emerald-200 hover:bg-emerald-900/35 disabled:opacity-50"
                        >
                          Seize
                        </button>
                      )}
                      <button
                        onClick={() => { setOpenActionMenuId(null); void handleTaskAction(item.task_id, "review"); }}
                        disabled={actionPendingTaskId === item.task_id}
                        className="w-full rounded bg-transparent px-2 py-1 text-left text-[10px] font-semibold uppercase tracking-wide text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                      >
                        Mark Review
                      </button>
                      <button
                        onClick={() => { setOpenActionMenuId(null); void handleTaskAction(item.task_id, "block"); }}
                        disabled={actionPendingTaskId === item.task_id}
                        className="w-full rounded bg-transparent px-2 py-1 text-left text-[10px] font-semibold uppercase tracking-wide text-amber-200 hover:bg-amber-900/35 disabled:opacity-50"
                      >
                        Block
                      </button>
                      <button
                        onClick={() => { setOpenActionMenuId(null); void handleTaskAction(item.task_id, "reject"); }}
                        disabled={actionPendingTaskId === item.task_id}
                        className="w-full rounded bg-transparent px-2 py-1 text-left text-[10px] font-semibold uppercase tracking-wide text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                      >
                        Reject
                      </button>
                      <button
                        onClick={() => { setOpenActionMenuId(null); void handleTaskAction(item.task_id, "park"); }}
                        disabled={actionPendingTaskId === item.task_id}
                        className="w-full rounded bg-transparent px-2 py-1 text-left text-[10px] font-semibold uppercase tracking-wide text-rose-200 hover:bg-rose-900/35 disabled:opacity-50"
                      >
                        Park
                      </button>
                    </div>
                  )}
                </div>
              </div>
            </article>
          ))
        )}
      </div>
    </section>
  );

  const renderPersonalPanel = (compact = false) => (
    <section className={`rounded-xl border border-slate-800 bg-slate-900/70 ${compact ? "p-3" : "p-4"}`}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-indigo-300">
            Personal Queue ({personalQueue?.items?.length || 0})
          </h2>
          <p className="text-xs text-slate-400">Human-visible tasks plus CSI escalations and prioritized approvals.</p>
        </div>
      </div>

      <div ref={approvalsRef} className="mb-3 rounded border border-amber-800/50 bg-amber-950/20 p-3">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-xs font-semibold uppercase tracking-[0.16em] text-amber-300">Priority Approvals</h3>
          <Link href="/dashboard/approvals" className="text-[11px] text-amber-200 hover:text-amber-100">Open Approvals</Link>
        </div>
        {approvalsHighlight?.approvals?.length ? (
          <div className="space-y-1.5">
            {approvalsHighlight.approvals.slice(0, compact ? 3 : 8).map((row) => (
              <div key={row.approval_id} className="rounded border border-amber-700/40 bg-amber-900/15 px-2 py-1.5 text-xs">
                <div className="font-semibold text-amber-100">{row.title}</div>
                <div className="text-[10px] text-amber-200/90">priority {row.priority} · {row.status}</div>
              </div>
            ))}
          </div>
        ) : (
          <p className="text-xs text-slate-500">No pending approvals.</p>
        )}
      </div>

      <div className="space-y-2 max-h-[56vh] overflow-y-auto pr-1">
        {(personalQueue?.items || []).length === 0 ? (
          <p className="text-sm text-slate-500 italic">No personal tasks available.</p>
        ) : (
          (personalQueue?.items || []).map((item) => (
            <article key={item.task_id} className="rounded-lg border border-slate-800/80 bg-slate-950/60 p-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <h3 className="font-semibold text-slate-200">{item.title}</h3>
                    {String(item.source_kind || "") === "csi" ? (
                      <span className="rounded border border-amber-700/60 bg-amber-900/25 px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-amber-200">CSI Escalation</span>
                    ) : null}
                  </div>
                  {String(item.source_kind || "") === "csi" && item.metadata?.csi?.human_intervention_reason ? (
                    <p className="mt-1 text-[11px] text-amber-200">{item.metadata.csi.human_intervention_reason}</p>
                  ) : null}
                </div>
                <span className="text-[10px] text-slate-400">{priorityText(item.priority)}</span>
              </div>
              {item.description ? <p className="mt-1 text-xs text-slate-400 line-clamp-2">{item.description}</p> : null}
              <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-500">
                <span>{item.project_key}</span>
                <span>•</span>
                <span>{item.status}</span>
                {item.due_at ? (<><span>•</span><span className="text-amber-300">Due {item.due_at}</span></>) : null}
                {item.updated_at ? (<><span>•</span><span>Updated {formatTs(item.updated_at)}</span></>) : null}
              </div>
            </article>
          ))
        )}
      </div>
    </section>
  );

  const completedRows = useMemo(
    () => (Array.isArray(completedTasks?.items) ? completedTasks!.items : []),
    [completedTasks],
  );

  const renderCompletedPanel = (compact = false) => (
    <section className={`rounded-xl border border-slate-800 bg-slate-900/70 ${compact ? "p-3" : "p-4"}`}>
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-emerald-300">
            Completed Agent Jobs ({completedRows.length})
          </h2>
          <p className="text-xs text-slate-400">Most recent finished tasks with session and run-log links.</p>
        </div>
      </div>
      <div className="space-y-2 max-h-[42vh] overflow-y-auto pr-1">
        {completedRows.length === 0 ? (
          <p className="text-sm text-slate-500 italic">No completed agent jobs yet.</p>
        ) : (
          completedRows.slice(0, compact ? 8 : 20).map((item) => (
            <article key={`completed-${item.task_id}`} className="rounded-lg border border-slate-800/80 bg-slate-950/60 p-3">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <h3 className="truncate font-semibold text-slate-200">{item.title}</h3>
                  {item.description ? <p className="mt-1 text-xs text-slate-400 line-clamp-2">{item.description}</p> : null}
                </div>
                <div className="text-right text-[10px] text-slate-400">
                  <div>{priorityText(item.priority)}</div>
                  <div>{item.status || "completed"}</div>
                </div>
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-500">
                <span>{item.project_key || "immediate"}</span>
                <span>•</span>
                <span>Completed {formatTs(item.completed_at || item.updated_at)}</span>
                {item.last_assignment?.agent_id ? (
                  <>
                    <span>•</span>
                    <span>{item.last_assignment.agent_id}</span>
                  </>
                ) : null}
              </div>
              <div className="mt-2 flex flex-wrap items-center gap-1.5">
                <button
                  onClick={() => void handleOpenTaskHistory(item.task_id)}
                  disabled={taskHistoryLoadingId === item.task_id}
                  className="rounded border border-sky-700/60 bg-sky-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-sky-200 hover:bg-sky-900/35 disabled:opacity-50"
                >
                  {taskHistoryLoadingId === item.task_id ? "Loading..." : "Review"}
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
                    className="rounded border border-emerald-700/60 bg-emerald-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-emerald-200 hover:bg-emerald-900/35"
                  >
                    Run Log
                  </a>
                ) : null}
              </div>
            </article>
          ))
        )}
      </div>
    </section>
  );

  const renderTaskHistoryPanel = () => (
    <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
      <div className="mb-3 flex items-center justify-between gap-2">
        <div>
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-sky-300">Task History</h2>
          <p className="text-xs text-slate-400">Assignment/evaluation trail and links to session artifacts.</p>
        </div>
        {taskHistory ? (
          <button
            onClick={() => setTaskHistory(null)}
            className="rounded border border-slate-700 bg-slate-800/80 px-2 py-1 text-[10px] uppercase tracking-wide text-slate-300 hover:bg-slate-700"
          >
            Clear
          </button>
        ) : null}
      </div>
      {!taskHistory ? (
        <p className="text-xs text-slate-500">Select Review on any task to load run history.</p>
      ) : (
        <div className="space-y-3 text-xs">
          <div className="rounded border border-slate-800/70 bg-slate-950/50 p-2">
            <div className="font-semibold text-slate-100">{taskHistory.task?.title || taskHistory.task?.task_id || "Task"}</div>
            <div className="mt-1 text-slate-400">{taskHistory.task?.task_id}</div>
          </div>
          <div className="rounded border border-slate-800/70 bg-slate-950/50 p-2">
            <div className="mb-1 text-[10px] uppercase tracking-[0.16em] text-slate-500">
              Assignments ({taskHistory.assignments?.length || 0})
            </div>
            {(taskHistory.assignments || []).length === 0 ? (
              <p className="text-slate-500">No assignment history.</p>
            ) : (
              <div className="space-y-1.5">
                {(taskHistory.assignments || []).slice(0, 10).map((row) => (
                  <div key={row.assignment_id} className="rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5">
                    <div className="text-slate-200">
                      <span className="font-semibold">{row.agent_id || "unknown-agent"}</span> · {row.state}
                    </div>
                    <div className="text-[10px] text-slate-500">
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
                          className="rounded border border-emerald-700/60 bg-emerald-900/20 px-2 py-0.5 text-[10px] uppercase tracking-wide text-emerald-200 hover:bg-emerald-900/35"
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
          <div className="rounded border border-slate-800/70 bg-slate-950/50 p-2">
            <div className="mb-1 text-[10px] uppercase tracking-[0.16em] text-slate-500">
              Evaluations ({taskHistory.evaluations?.length || 0})
            </div>
            {(taskHistory.evaluations || []).length === 0 ? (
              <p className="text-slate-500">No evaluation records.</p>
            ) : (
              <div className="space-y-1.5">
                {(taskHistory.evaluations || []).slice(0, 12).map((row) => (
                  <div key={row.id} className="rounded border border-slate-800 bg-slate-900/50 px-2 py-1.5">
                    <div className="text-slate-200">
                      <span className="font-semibold">{row.decision || "n/a"}</span> · {row.reason || "n/a"}
                    </div>
                    <div className="text-[10px] text-slate-500">
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

  const agentMetrics1h = agentActivity?.metrics?.["1h"];
  const agentMetrics24h = agentActivity?.metrics?.["24h"];

  if (loading) {
    return <div className="flex h-full items-center justify-center p-6 text-slate-400">Loading To Do Command Center V2...</div>;
  }

  const renderTaskDetailsModal = () => {
    if (!selectedTaskDetails) return null;
    return (
      <div className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm">
        <div className="flex max-h-full w-full max-w-4xl flex-col rounded-xl border border-slate-700 bg-slate-900 shadow-2xl">
          <div className="flex items-center justify-between border-b border-slate-800 bg-slate-950/50 p-4">
            <div>
              <h2 className="text-lg font-semibold text-slate-100">Task Details</h2>
              <p className="text-xs text-slate-400">{selectedTaskDetails.task_id}</p>
            </div>
            <button
              onClick={() => setSelectedTaskDetails(null)}
              className="rounded p-1 text-slate-400 transition-colors hover:bg-slate-800 hover:text-slate-100"
            >
              ✕
            </button>
          </div>
          <div className="overflow-y-auto p-4 text-sm text-slate-300">
            <pre className="break-all rounded border border-slate-800 bg-slate-950 p-4 font-mono text-[11px] text-emerald-300 whitespace-pre-wrap">
              {JSON.stringify(selectedTaskDetails, null, 2)}
            </pre>
          </div>
          <div className="flex-none flex justify-end border-t border-slate-800 bg-slate-950/50 p-4">
            <button
              onClick={() => setSelectedTaskDetails(null)}
              className="rounded border border-slate-700 bg-slate-800 px-4 py-2 text-sm font-semibold text-slate-200 transition-colors hover:bg-slate-700"
            >
              Close
            </button>
          </div>
        </div>
      </div>
    );
  };

  return (
    <div className="relative flex h-full flex-col gap-4 pb-6">
      {renderTaskDetailsModal()}
      {approvalsHighlight?.banner?.show ? (
        <div className="sticky top-0 z-20 rounded-lg border border-amber-700/50 bg-amber-950/90 px-3 py-2 text-xs text-amber-100 backdrop-blur">
          <div className="flex items-center justify-between gap-3">
            <div>
              <span className="font-semibold uppercase tracking-wide">Outstanding Approval:</span>{" "}
              {approvalsHighlight.banner.text}
            </div>
            <Link
              href={approvalsHighlight.banner.focus_href || "/dashboard/todolist?mode=personal&focus=approvals"}
              className="rounded border border-amber-600/70 bg-amber-800/25 px-2 py-1 font-semibold uppercase tracking-wide text-amber-100 hover:bg-amber-800/35"
            >
              Review Now
            </Link>
          </div>
        </div>
      ) : null}

      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">To Do List - Task Command Center V2</h1>
          <p className="text-sm text-slate-400">Internal-first orchestration with selective Todoist mirror and memory-safe learning.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <div className="inline-flex rounded-lg border border-slate-700 bg-slate-800/70 p-1 text-xs">
            <button onClick={() => setMode("agent")} className={`rounded px-2 py-1 ${mode === "agent" ? "bg-cyan-700/40 text-cyan-100" : "text-slate-300"}`}>Agent</button>
            <button onClick={() => setMode("personal")} className={`rounded px-2 py-1 ${mode === "personal" ? "bg-indigo-700/40 text-indigo-100" : "text-slate-300"}`}>Personal</button>
            <button onClick={() => setMode("split")} className={`rounded px-2 py-1 ${mode === "split" ? "bg-violet-700/40 text-violet-100" : "text-slate-300"}`}>Split</button>
          </div>
          <button
            onClick={() => { void load(true); }}
            className="rounded border border-slate-700 bg-slate-800/80 px-3 py-1.5 text-xs text-slate-300 hover:bg-slate-700"
          >
            {refreshing ? "Refreshing..." : "Refresh"}
          </button>
        </div>
      </div>

      {error ? (
        <div className="rounded-lg border border-rose-800/60 bg-rose-950/30 px-3 py-2 text-sm text-rose-200">{error}</div>
      ) : null}

      <section className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
        <article className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
          <p className="text-[10px] uppercase tracking-[0.16em] text-slate-500">
            Dispatch Eligible{dispatchThreshold > 0 ? ` (>= ${dispatchThreshold})` : ""}
          </p>
          <p className="mt-1 text-2xl font-semibold text-slate-100">{overview?.queue_health?.dispatch_eligible || 0}</p>
          <p className="mt-1 text-[11px] text-slate-500">
            {overview?.queue_health?.dispatch_queue_size || 0} visible agent queue item(s)
          </p>
        </article>
        <Link href="/dashboard/csi#notifications" className="block rounded-lg border border-slate-800 bg-slate-900/60 p-3 transition-colors hover:border-emerald-700/60 hover:bg-slate-900/80">
          <p className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Open CSI Incidents</p>
          <p className="mt-1 text-2xl font-semibold text-emerald-200">{overview?.csi_incident_summary?.open_incidents || 0}</p>
          <p className="mt-1 text-[11px] text-slate-500">Open CSI notifications</p>
        </Link>
        <article className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
          <p className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Human Intervention Required</p>
          <p className="mt-1 text-2xl font-semibold text-amber-200">{overview?.queue_health?.csi_human_open || 0}</p>
          <p className="mt-1 text-[11px] text-slate-500">CSI escalations waiting on a human</p>
        </article>
        <article className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
          <p className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Active Agents</p>
          <p className="mt-1 text-2xl font-semibold text-cyan-200">{agentActivity?.active_agents || 0}</p>
        </article>
      </section>

      <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-[0.16em] text-slate-300">Heartbeat Runtime</h2>
        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-5">
          <div className="rounded border border-slate-800/70 bg-slate-950/50 p-2 text-xs">
            <div className="text-slate-500">Heartbeat Config / Effective</div>
            <div className="mt-1 text-slate-100">
              {formatEvery(overview?.heartbeat?.configured_every_seconds)} / {formatEvery(overview?.heartbeat?.heartbeat_effective_interval_seconds ?? overview?.heartbeat?.effective_default_every_seconds)}
            </div>
          </div>
          <div className="rounded border border-slate-800/70 bg-slate-950/50 p-2 text-xs">
            <div className="text-slate-500">Autonomous Cron / Min</div>
            <div className="mt-1 text-slate-100">
              {formatEvery(overview?.heartbeat?.cron_interval_seconds ?? undefined)} / {formatEvery(overview?.heartbeat?.min_interval_seconds)}
            </div>
          </div>
          <div className="rounded border border-slate-800/70 bg-slate-950/50 p-2 text-xs">
            <div className="text-slate-500">Interval Source / Busy</div>
            <div className="mt-1 text-slate-100">
              {overview?.heartbeat?.heartbeat_interval_source || "default"} / {overview?.heartbeat?.busy_sessions ?? 0}
            </div>
          </div>
          <div className="rounded border border-slate-800/70 bg-slate-950/50 p-2 text-xs">
            <div className="text-slate-500">Last Run / Next Run</div>
            <div className="mt-1 text-slate-100">
              {formatEpochTs(overview?.heartbeat?.latest_last_run_epoch)} / {formatEpochTs(overview?.heartbeat?.nearest_next_run_epoch)}
            </div>
          </div>
          <div className="rounded border border-slate-800/70 bg-slate-950/50 p-2 text-xs">
            <div className="text-slate-500">Sessions / State Files</div>
            <div className="mt-1 text-slate-100">
              {overview?.heartbeat?.session_count ?? 0} / {overview?.heartbeat?.session_state_count ?? 0}
            </div>
          </div>
        </div>
      </section>

      <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-[0.16em] text-slate-300">Agent Efficiency</h2>
        <p className="mb-2 text-xs text-slate-500">
          This measures recent agent seizure/completion activity, not total backlog depth.
        </p>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded border border-slate-800/70 bg-slate-950/50 p-2 text-xs">
            <div className="text-slate-500">1h new / seized</div>
            <div className="mt-1 text-slate-100">{agentMetrics1h?.new || 0} / {agentMetrics1h?.seized || 0}</div>
          </div>
          <div className="rounded border border-slate-800/70 bg-slate-950/50 p-2 text-xs">
            <div className="text-slate-500">1h completed / rejected</div>
            <div className="mt-1 text-slate-100">{agentMetrics1h?.completed || 0} / {agentMetrics1h?.rejected || 0}</div>
          </div>
          <div className="rounded border border-slate-800/70 bg-slate-950/50 p-2 text-xs">
            <div className="text-slate-500">24h completed / rejected</div>
            <div className="mt-1 text-slate-100">{agentMetrics24h?.completed || 0} / {agentMetrics24h?.rejected || 0}</div>
          </div>
          <div className="rounded border border-slate-800/70 bg-slate-950/50 p-2 text-xs">
            <div className="text-slate-500">Backlog Open</div>
            <div className="mt-1 text-slate-100">{agentActivity?.backlog_open || 0}</div>
          </div>
        </div>
        {(agentActivity?.active_assignments || []).length > 0 ? (
          <div className="mt-3">
            <h3 className="mb-1 text-xs uppercase tracking-[0.16em] text-slate-500">Current Assignments</h3>
            <div className="space-y-1 max-h-32 overflow-y-auto">
              {(agentActivity?.active_assignments || []).map((a) => (
                <div key={a.assignment_id} className="rounded border border-slate-800/70 bg-slate-950/50 px-2 py-1 text-xs text-slate-300">
                  <span className="font-semibold text-slate-100">{a.agent_id}</span> - {a.title}
                </div>
              ))}
            </div>
          </div>
        ) : null}
      </section>

      {mode === "agent" ? renderAgentPanel(false) : null}
      {mode === "personal" ? renderPersonalPanel(false) : null}
      {mode === "split" ? (
        <div className="grid gap-3 xl:grid-cols-2">
          {renderAgentPanel(true)}
          {renderPersonalPanel(true)}
        </div>
      ) : null}

      {mode === "split" ? (
        <div className="grid gap-3 xl:grid-cols-2">
          {renderCompletedPanel(true)}
          {renderTaskHistoryPanel()}
        </div>
      ) : (
        <>
          {renderCompletedPanel(false)}
          {renderTaskHistoryPanel()}
        </>
      )}
    </div>
  );
}
