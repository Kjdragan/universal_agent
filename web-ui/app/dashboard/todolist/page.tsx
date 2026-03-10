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

  const [mode, setMode] = usePersistedMode("agent");
  const [includeCsi, setIncludeCsi] = useState(true);
  const [collapseCsi, setCollapseCsi] = useState(true);
  const [showNonCsiOnly, setShowNonCsiOnly] = useState(false);
  const [actionPendingTaskId, setActionPendingTaskId] = useState("");

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

      const [overviewRes, approvalsRes, agentRes, personalRes, activityRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/dashboard/todolist/overview`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/v1/dashboard/approvals/highlight`, { cache: "no-store" }),
        fetch(`${agentQueueUrl.pathname}${agentQueueUrl.search}`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/v1/dashboard/todolist/personal-queue?limit=200`, { cache: "no-store" }),
        fetch(`${API_BASE}/api/v1/dashboard/todolist/agent-activity`, { cache: "no-store" }),
      ]);

      if (!overviewRes.ok || !agentRes.ok || !personalRes.ok || !activityRes.ok) {
        const failures: Array<{ name: string; status: number; detail: string }> = [];
        const required = [
          { name: "overview", res: overviewRes },
          { name: "agent_queue", res: agentRes },
          { name: "personal_queue", res: personalRes },
          { name: "agent_activity", res: activityRes },
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

      const [overviewJson, agentJson, personalJson, activityJson] = await Promise.all([
        overviewRes.json(),
        agentRes.json(),
        personalRes.json(),
        activityRes.json(),
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

  const filteredAgentItems = useMemo(() => {
    const rows = Array.isArray(agentQueue?.items) ? agentQueue!.items : [];
    if (!showNonCsiOnly) return rows;
    return rows.filter((row) => String(row.source_kind || "") !== "csi");
  }, [agentQueue, showNonCsiOnly]);

  const nextActions = useMemo(() => filteredAgentItems.slice(0, 5), [filteredAgentItems]);

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
        </div>
      </div>

      <div className="mb-3 rounded border border-slate-800 bg-slate-950/40 p-3">
        <h3 className="mb-2 text-xs uppercase tracking-[0.16em] text-slate-400">Next Actions</h3>
        {nextActions.length === 0 ? (
          <p className="text-xs text-slate-500">No eligible agent actions right now.</p>
        ) : (
          <div className="space-y-2">
            {nextActions.map((item) => (
              <div key={`next-${item.task_id}`} className="flex items-center justify-between gap-2 rounded border border-slate-800/70 bg-slate-950/60 px-2 py-1.5 text-xs">
                <div className="min-w-0">
                  <div className="truncate text-slate-200">{item.title}</div>
                  <div className="text-[10px] text-slate-500">
                    {item.project_key} · score {item.score ?? 0} ({scoreBadge(item.score)})
                  </div>
                </div>
                <button
                  onClick={() => void handleTaskAction(item.task_id, "seize")}
                  disabled={actionPendingTaskId === item.task_id}
                  className="rounded border border-emerald-700/60 bg-emerald-800/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-emerald-200 hover:bg-emerald-800/30 disabled:opacity-50"
                >
                  Seize
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

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
                  <div>score {item.score ?? 0}</div>
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
                {item.status === "open" ? (
                  <button
                    onClick={() => void handleTaskAction(item.task_id, "seize")}
                    disabled={actionPendingTaskId === item.task_id}
                    className="rounded border border-emerald-700/60 bg-emerald-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-emerald-200 hover:bg-emerald-900/35 disabled:opacity-50"
                  >
                    Seize
                  </button>
                ) : null}
                <button
                  onClick={() => void handleTaskAction(item.task_id, "review")}
                  disabled={actionPendingTaskId === item.task_id}
                  className="rounded border border-sky-700/60 bg-sky-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-sky-200 hover:bg-sky-900/35 disabled:opacity-50"
                >
                  Review
                </button>
                <button
                  onClick={() => void handleTaskAction(item.task_id, "block")}
                  disabled={actionPendingTaskId === item.task_id}
                  className="rounded border border-amber-700/60 bg-amber-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-amber-200 hover:bg-amber-900/35 disabled:opacity-50"
                >
                  Block
                </button>
                <button
                  onClick={() => void handleTaskAction(item.task_id, "reject")}
                  disabled={actionPendingTaskId === item.task_id}
                  className="rounded border border-slate-700 bg-slate-900/80 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-300 hover:bg-slate-800 disabled:opacity-50"
                >
                  Reject
                </button>
                <button
                  onClick={() => void handleTaskAction(item.task_id, "complete")}
                  disabled={actionPendingTaskId === item.task_id}
                  className="rounded border border-indigo-700/60 bg-indigo-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-indigo-200 hover:bg-indigo-900/35 disabled:opacity-50"
                >
                  Complete
                </button>
                <button
                  onClick={() => void handleTaskAction(item.task_id, "park")}
                  disabled={actionPendingTaskId === item.task_id}
                  className="rounded border border-rose-700/60 bg-rose-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-rose-200 hover:bg-rose-900/35 disabled:opacity-50"
                >
                  Park
                </button>
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
          <p className="text-xs text-slate-400">Human-visible reminders plus prioritized approvals.</p>
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
                <h3 className="font-semibold text-slate-200">{item.title}</h3>
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

  const agentMetrics1h = agentActivity?.metrics?.["1h"];
  const agentMetrics24h = agentActivity?.metrics?.["24h"];

  if (loading) {
    return <div className="flex h-full items-center justify-center p-6 text-slate-400">Loading To Do Command Center V2...</div>;
  }

  return (
    <div className="relative flex h-full flex-col gap-4 pb-6">
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
          <p className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Dispatch Eligible</p>
          <p className="mt-1 text-2xl font-semibold text-slate-100">{overview?.queue_health?.dispatch_eligible || 0}</p>
        </article>
        <article className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
          <p className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Open CSI Incidents</p>
          <p className="mt-1 text-2xl font-semibold text-emerald-200">{overview?.csi_incident_summary?.open_incidents || 0}</p>
        </article>
        <article className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
          <p className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Pending Approvals</p>
          <p className="mt-1 text-2xl font-semibold text-amber-200">{approvalsHighlight?.pending_count || 0}</p>
        </article>
        <article className="rounded-lg border border-slate-800 bg-slate-900/60 p-3">
          <p className="text-[10px] uppercase tracking-[0.16em] text-slate-500">Active Agents</p>
          <p className="mt-1 text-2xl font-semibold text-cyan-200">{agentActivity?.active_agents || 0}</p>
        </article>
      </section>

      <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-[0.16em] text-slate-300">Heartbeat Runtime</h2>
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
          <div className="rounded border border-slate-800/70 bg-slate-950/50 p-2 text-xs">
            <div className="text-slate-500">Configured / Effective</div>
            <div className="mt-1 text-slate-100">
              {formatEvery(overview?.heartbeat?.configured_every_seconds)} / {formatEvery(overview?.heartbeat?.effective_default_every_seconds)}
            </div>
          </div>
          <div className="rounded border border-slate-800/70 bg-slate-950/50 p-2 text-xs">
            <div className="text-slate-500">Min Interval / Busy</div>
            <div className="mt-1 text-slate-100">
              {formatEvery(overview?.heartbeat?.min_interval_seconds)} / {overview?.heartbeat?.busy_sessions ?? 0}
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
    </div>
  );
}
