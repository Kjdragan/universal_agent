"use client";

import { useCallback, useEffect, useState } from "react";

const API_BASE = "/api/dashboard/gateway";
const REFRESH_INTERVAL = 30_000;

/* ── Types ──────────────────────────────────────────────────────── */
type HeartbeatSession = {
  last_run?: string | number;
  busy: boolean;
  summary_text?: string;
  summary_ok?: boolean;
  artifacts_count?: number;
  suppressed_reason?: string;
};

type VpEntry = {
  vp_id: string;
  session_id?: string;
  status?: string;
  effective_status?: string;
  last_heartbeat_at?: string;
  lease_expires_at?: string;
};

type CronJob = {
  job_id: string;
  name: string;
  cron_expr: string;
  enabled: boolean;
  running: boolean;
  last_run_at?: string;
  next_run_at?: string;
};

type SystemHealth = {
  heartbeat: {
    available: boolean;
    interval_seconds: number;
    sessions?: Record<string, HeartbeatSession>;
  };
  vp_fleet: VpEntry[];
  cron: {
    available: boolean;
    jobs_total?: number;
    jobs_enabled?: number;
    jobs_running?: number;
    next_fire_at?: string;
    last_run_at?: string;
    jobs?: CronJob[];
    error?: string;
  };
  agentmail: {
    available: boolean;
    watcher_running?: boolean;
    ws_connected?: boolean;
    inbox_address?: string;
    queue_depth?: number;
    poll_interval?: number;
    messages_sent?: number;
    messages_received?: number;
    error?: string;
  };
  hooks: {
    available: boolean;
    active_dispatches?: number;
    concurrency_limit?: number;
  };
  task_hub: {
    available: boolean;
    counts?: Record<string, number>;
    total?: number;
    error?: string;
  };
  sessions: {
    active: number;
    session_ids: string[];
  };
  timestamp: number;
};

/* ── Helpers ─────────────────────────────────────────────────────── */
function ageSeconds(ts?: string | number | null): number {
  if (ts == null) return Infinity;
  const ms = typeof ts === "number" ? (ts < 1e12 ? ts * 1000 : ts) : Date.parse(ts);
  if (!ms || isNaN(ms)) return Infinity;
  return Math.max(0, (Date.now() - ms) / 1000);
}

function ageLabel(ts?: string | number | null): string {
  if (!ts) return "—";
  const s = ageSeconds(ts);
  if (!isFinite(s)) return "—";
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h ago`;
  return `${(s / 86400).toFixed(1)}d ago`;
}

function timeLabel(ts?: string | number | null): string {
  if (!ts) return "—";
  try {
    const d = new Date(typeof ts === "number" ? (ts < 1e12 ? ts * 1000 : ts) : ts);
    return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  } catch {
    return "—";
  }
}

/* Determine heartbeat health with interval-aware threshold */
function heartbeatHealth(
  age: number,
  intervalSeconds: number,
): "healthy" | "warning" | "stale" {
  const threshold = intervalSeconds + 300; // interval + 5min grace
  if (age < threshold * 0.5) return "healthy";
  if (age < threshold) return "warning";
  return "stale";
}

/* ── Sub-component: StatusOrb ─────────────────────── */
function StatusOrb({
  status,
  size = "md",
  pulse = false,
}: {
  status: "healthy" | "warning" | "stale" | "offline" | "running" | "idle";
  size?: "sm" | "md" | "lg";
  pulse?: boolean;
}) {
  const colors: Record<string, string> = {
    healthy: "bg-kcd-green shadow-[0_0_8px_rgba(74,222,128,0.4)]",
    running: "bg-kcd-cyan shadow-[0_0_8px_rgba(34,211,238,0.4)]",
    warning: "bg-kcd-amber shadow-[0_0_8px_rgba(238,152,0,0.4)]",
    stale: "bg-kcd-red shadow-[0_0_6px_rgba(239,68,68,0.3)]",
    offline: "bg-kcd-text-muted/40",
    idle: "bg-kcd-text-dim/50",
  };
  const sizes = { sm: "h-2 w-2", md: "h-3 w-3", lg: "h-4 w-4" };
  return (
    <span
      className={`inline-block rounded-full ${sizes[size]} ${colors[status] || colors.idle} ${
        pulse ? "animate-pulse" : ""
      }`}
    />
  );
}

/* ── Sub-component: SubsystemCard (click-to-expand) ─ */
function SubsystemCard({
  label,
  status,
  statusLabel,
  metric,
  metricLabel,
  children,
  icon,
}: {
  label: string;
  status: "healthy" | "warning" | "stale" | "offline" | "idle";
  statusLabel: string;
  metric?: string | number;
  metricLabel?: string;
  children?: React.ReactNode;
  icon: string;
}) {
  const [expanded, setExpanded] = useState(false);

  const statusColors: Record<string, string> = {
    healthy: "text-kcd-green",
    warning: "text-kcd-amber",
    stale: "text-kcd-red",
    offline: "text-kcd-text-muted",
    idle: "text-kcd-text-dim",
    running: "text-kcd-cyan",
  };

  return (
    <div className="relative group">
      <button
        onClick={() => children && setExpanded(!expanded)}
        className={`w-full text-left bg-kcd-surface-low/60 backdrop-blur-xl
          ${children ? "cursor-pointer hover:bg-kcd-surface-high/60" : "cursor-default"}
          transition-all duration-200 p-4
          ${expanded ? "border-l-2 border-l-kcd-cyan/40" : "border-l-2 border-l-transparent"}`}
      >
        {/* Top row: icon + label + status orb */}
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <span className="text-base opacity-70">{icon}</span>
            <span className="font-mono text-[11px] font-semibold uppercase tracking-widest text-kcd-text-dim">
              {label}
            </span>
          </div>
          <div className="flex items-center gap-2">
            <StatusOrb status={status} size="sm" pulse={status === "healthy"} />
            <span className={`font-mono text-[10px] uppercase tracking-wider ${statusColors[status]}`}>
              {statusLabel}
            </span>
          </div>
        </div>

        {/* Hero metric */}
        {metric !== undefined && (
          <div className="flex items-baseline gap-2">
            <span className="font-display text-2xl font-bold text-kcd-text tracking-tight">
              {metric}
            </span>
            {metricLabel && (
              <span className="font-mono text-[10px] text-kcd-text-muted uppercase tracking-wider">
                {metricLabel}
              </span>
            )}
          </div>
        )}

        {/* Expand hint */}
        {children && (
          <div className="absolute bottom-1 right-2 text-[9px] text-kcd-text-muted/40 font-mono">
            {expanded ? "▲ collapse" : "▼ details"}
          </div>
        )}
      </button>

      {/* Expanded detail panel */}
      {expanded && children && (
        <div
          className="border-l-2 border-l-kcd-cyan/20 bg-kcd-surface-dim/80 backdrop-blur-xl
            px-4 py-3 animate-slide-in text-xs space-y-2"
        >
          {children}
        </div>
      )}
    </div>
  );
}

/* ── Sub-component: OverallStatusBanner ─────────────── */
function OverallStatusBanner({
  health,
  subsystemCount,
  healthyCount,
  lastRefreshed,
  onRefresh,
  loading,
}: {
  health: SystemHealth | null;
  subsystemCount: number;
  healthyCount: number;
  lastRefreshed: Date | null;
  onRefresh: () => void;
  loading: boolean;
}) {
  const overall =
    !health
      ? "loading"
      : healthyCount === subsystemCount
        ? "operational"
        : healthyCount > subsystemCount / 2
          ? "degraded"
          : "critical";

  const bannerColors: Record<string, string> = {
    operational: "from-kcd-green/10 via-transparent to-transparent border-kcd-green/20",
    degraded: "from-kcd-amber/10 via-transparent to-transparent border-kcd-amber/20",
    critical: "from-kcd-red/10 via-transparent to-transparent border-kcd-red/20",
    loading: "from-kcd-cyan/5 via-transparent to-transparent border-kcd-cyan/10",
  };
  const statusText: Record<string, string> = {
    operational: "ALL SYSTEMS OPERATIONAL",
    degraded: "PARTIAL DEGRADATION",
    critical: "SYSTEMS CRITICAL",
    loading: "INITIALIZING…",
  };
  const statusTextColor: Record<string, string> = {
    operational: "text-kcd-green",
    degraded: "text-kcd-amber",
    critical: "text-kcd-red",
    loading: "text-kcd-cyan",
  };

  return (
    <div
      className={`bg-gradient-to-r ${bannerColors[overall]} border-l-2 p-5 flex items-center justify-between`}
    >
      <div className="flex items-center gap-4">
        <StatusOrb
          status={overall === "operational" ? "healthy" : overall === "degraded" ? "warning" : "stale"}
          size="lg"
          pulse={overall === "operational"}
        />
        <div>
          <div className={`font-mono text-sm font-bold tracking-wider ${statusTextColor[overall]}`}>
            {statusText[overall]}
          </div>
          <div className="font-mono text-[10px] text-kcd-text-muted mt-0.5">
            {healthyCount}/{subsystemCount} subsystems healthy
            {lastRefreshed && (
              <span className="ml-3 text-kcd-text-muted/60">
                updated {lastRefreshed.toLocaleTimeString()}
              </span>
            )}
          </div>
        </div>
      </div>
      <button
        onClick={onRefresh}
        disabled={loading}
        className="font-mono text-[10px] px-3 py-1.5 bg-kcd-surface-high/60 backdrop-blur-xl
          text-kcd-cyan hover:bg-kcd-cyan/10 transition-all duration-200
          disabled:opacity-40 uppercase tracking-widest"
      >
        {loading ? "syncing…" : "↻ refresh"}
      </button>
    </div>
  );
}

/* ── Sub-component: HeartbeatReportPanel ─────────────── */
function HeartbeatReportPanel({
  sessions,
  intervalSeconds,
}: {
  sessions: Record<string, HeartbeatSession>;
  intervalSeconds: number;
}) {
  const [expandedSession, setExpandedSession] = useState<string | null>(null);
  const entries = Object.entries(sessions);
  if (!entries.length) return null;

  return (
    <div className="space-y-2">
      <div className="font-mono text-[10px] font-semibold uppercase tracking-widest text-kcd-text-dim px-1">
        Heartbeat Sessions
      </div>
      {entries.map(([sid, session]) => {
        const age = ageSeconds(session.last_run);
        const health = session.busy ? "running" : heartbeatHealth(age, intervalSeconds);
        const isExpanded = expandedSession === sid;
        const displayName = sid.replace("daemon_", "").replace(/_/g, " ");

        return (
          <div key={sid}>
            <button
              onClick={() => setExpandedSession(isExpanded ? null : sid)}
              className={`w-full text-left bg-kcd-surface-low/40 backdrop-blur-xl p-3
                hover:bg-kcd-surface-high/40 transition-all duration-200
                ${isExpanded ? "border-l-2 border-l-kcd-cyan/40" : "border-l-2 border-l-transparent"}`}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <StatusOrb
                    status={health === "running" ? "running" : health}
                    size="sm"
                    pulse={health === "healthy" || health === "running"}
                  />
                  <span className="font-mono text-[11px] text-kcd-text capitalize">{displayName}</span>
                  {session.busy && (
                    <span className="font-mono text-[9px] px-1.5 py-0.5 bg-kcd-cyan/15 text-kcd-cyan uppercase tracking-wider">
                      running
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-3">
                  {session.summary_ok && (
                    <span className="font-mono text-[9px] px-1.5 py-0.5 bg-kcd-green/10 text-kcd-green">
                      OK
                    </span>
                  )}
                  <span className="font-mono text-[10px] text-kcd-text-muted">{ageLabel(session.last_run)}</span>
                  <span className="text-[9px] text-kcd-text-muted/40">{isExpanded ? "▲" : "▼"}</span>
                </div>
              </div>
            </button>

            {isExpanded && (
              <div className="bg-kcd-surface-dim/60 backdrop-blur-xl border-l-2 border-l-kcd-cyan/20 px-4 py-3 animate-slide-in space-y-2">
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[11px]">
                  <span className="text-kcd-text-muted">Last Run</span>
                  <span className="font-mono text-kcd-text text-right">{timeLabel(session.last_run)}</span>
                  <span className="text-kcd-text-muted">Interval</span>
                  <span className="font-mono text-kcd-text text-right">{Math.round(intervalSeconds / 60)}min</span>
                  <span className="text-kcd-text-muted">Artifacts</span>
                  <span className="font-mono text-kcd-text text-right">{session.artifacts_count ?? 0}</span>
                  {session.suppressed_reason && (
                    <>
                      <span className="text-kcd-amber">Suppressed</span>
                      <span className="font-mono text-kcd-amber text-right">{session.suppressed_reason}</span>
                    </>
                  )}
                </div>
                {session.summary_text && (
                  <div className="mt-2 pt-2 border-t border-kcd-surface-high/30">
                    <div className="font-mono text-[10px] text-kcd-text-dim mb-1 uppercase tracking-wider">
                      Last Report
                    </div>
                    <pre className="font-mono text-[10px] text-kcd-text/70 whitespace-pre-wrap leading-relaxed max-h-40 overflow-y-auto scrollbar-thin">
                      {session.summary_text}
                    </pre>
                  </div>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

/* ── Main Page Component ────────────────────────────────────────── */
export default function HeartbeatsPage() {
  const [health, setHealth] = useState<SystemHealth | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [lastRefreshed, setLastRefreshed] = useState<Date | null>(null);

  const fetchHealth = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/system-health`, { cache: "no-store" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: SystemHealth = await res.json();
      setHealth(data);
      setLastRefreshed(new Date());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchHealth();
    const iv = setInterval(() => void fetchHealth(), REFRESH_INTERVAL);
    return () => clearInterval(iv);
  }, [fetchHealth]);

  /* ── Compute subsystem statuses ───────────────────────────── */
  type SubStatus = "healthy" | "warning" | "stale" | "offline" | "idle";

  const computeStatuses = (): Record<string, SubStatus> => {
    if (!health) return {};
    const interval = health.heartbeat.interval_seconds || 1800;
    const threshold = interval + 300;

    // Heartbeat
    let hbStatus: SubStatus = "offline";
    if (health.heartbeat.available) {
      const sessions = health.heartbeat.sessions || {};
      const entries = Object.values(sessions);
      if (entries.length === 0) {
        hbStatus = "idle";
      } else {
        const anyBusy = entries.some((s) => s.busy);
        const allOk = entries.every((s) => {
          const age = ageSeconds(s.last_run);
          return s.busy || age < threshold;
        });
        hbStatus = anyBusy ? "healthy" : allOk ? "healthy" : "warning";
      }
    }

    // Cron
    let cronStatus: SubStatus = "offline";
    if (health.cron.available && !health.cron.error) {
      cronStatus = (health.cron.jobs_enabled || 0) > 0 ? "healthy" : "idle";
    }

    // AgentMail
    let mailStatus: SubStatus = "offline";
    if (health.agentmail.available && !health.agentmail.error) {
      mailStatus = health.agentmail.watcher_running ? "healthy" : "warning";
    }

    // Hooks
    let hooksStatus: SubStatus = "offline";
    if (health.hooks.available) {
      hooksStatus = "healthy";
    }

    // Task Hub
    let taskStatus: SubStatus = "offline";
    if (health.task_hub.available && !health.task_hub.error) {
      taskStatus = "healthy";
    }

    return {
      heartbeat: hbStatus,
      cron: cronStatus,
      agentmail: mailStatus,
      hooks: hooksStatus,
      task_hub: taskStatus,
    };
  };

  const statuses = computeStatuses();
  const subsystemCount = Object.keys(statuses).length;
  const healthyCount = Object.values(statuses).filter((s) => s === "healthy").length;

  /* ── Task Hub counts ────────────────────────────────────────── */
  const taskCounts = health?.task_hub?.counts || {};
  const openTasks = (taskCounts["open"] || 0) + (taskCounts["in_progress"] || 0);
  const completedTasks = taskCounts["completed"] || 0;

  return (
    <div className="flex h-full flex-col bg-kcd-bg">
      {/* ── Page Header ─────────────────────────────────────────── */}
      <header className="flex h-12 shrink-0 items-center justify-between px-5 border-b border-kcd-surface-high/30">
        <div className="flex items-center gap-3">
          <span className="text-kcd-cyan/60 text-lg">◈</span>
          <h1 className="font-display text-sm font-bold text-kcd-text uppercase tracking-wider">
            System Operations
          </h1>
        </div>
        <div className="font-mono text-[10px] text-kcd-text-muted/50">
          {health && (
            <span>
              {health.sessions.active} active session{health.sessions.active !== 1 ? "s" : ""}
            </span>
          )}
        </div>
      </header>

      <main className="flex-1 overflow-auto p-5 space-y-4">
        {/* ── Overall Status Banner ─────────────────────────────── */}
        <OverallStatusBanner
          health={health}
          subsystemCount={subsystemCount}
          healthyCount={healthyCount}
          lastRefreshed={lastRefreshed}
          onRefresh={() => void fetchHealth()}
          loading={loading}
        />

        {/* ── Subsystem Health Grid ─────────────────────────────── */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-px bg-kcd-surface-high/10">
          {/* Heartbeat */}
          <SubsystemCard
            icon="💓"
            label="Heartbeat"
            status={statuses.heartbeat || "offline"}
            statusLabel={statuses.heartbeat || "offline"}
            metric={
              health?.heartbeat.sessions
                ? Object.values(health.heartbeat.sessions).some((s) => s.busy)
                  ? "ACTIVE"
                  : `${Math.round((health.heartbeat.interval_seconds || 1800) / 60)}m`
                : "—"
            }
            metricLabel={
              health?.heartbeat.sessions
                ? Object.values(health.heartbeat.sessions).some((s) => s.busy)
                  ? "cycle running"
                  : "cycle interval"
                : ""
            }
          >
            {health?.heartbeat.sessions && (
              <div className="space-y-1">
                {Object.entries(health.heartbeat.sessions).map(([sid, s]) => (
                  <div key={sid} className="flex justify-between items-center">
                    <span className="font-mono text-[10px] text-kcd-text-dim capitalize">
                      {sid.replace("daemon_", "").replace(/_/g, " ")}
                    </span>
                    <span className="font-mono text-[10px] text-kcd-text-muted">
                      {s.busy ? "running now" : ageLabel(s.last_run)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </SubsystemCard>

          {/* Cron */}
          <SubsystemCard
            icon="⏰"
            label="Cron"
            status={statuses.cron || "offline"}
            statusLabel={statuses.cron || "offline"}
            metric={health?.cron.jobs_enabled ?? "—"}
            metricLabel="jobs active"
          >
            {health?.cron.jobs && health.cron.jobs.length > 0 && (
              <div className="space-y-1">
                {health.cron.jobs.map((j) => (
                  <div key={j.job_id} className="flex justify-between items-center">
                    <div className="flex items-center gap-1.5">
                      <StatusOrb status={j.running ? "running" : j.enabled ? "healthy" : "idle"} size="sm" />
                      <span className="font-mono text-[10px] text-kcd-text-dim">{j.name}</span>
                    </div>
                    <span className="font-mono text-[9px] text-kcd-text-muted">
                      {j.next_run_at ? `next ${timeLabel(j.next_run_at)}` : j.cron_expr}
                    </span>
                  </div>
                ))}
                {health.cron.last_run_at && (
                  <div className="text-[9px] text-kcd-text-muted/60 font-mono pt-1">
                    Last run: {ageLabel(health.cron.last_run_at)}
                  </div>
                )}
              </div>
            )}
          </SubsystemCard>

          {/* AgentMail */}
          <SubsystemCard
            icon="📬"
            label="AgentMail"
            status={statuses.agentmail || "offline"}
            statusLabel={
              health?.agentmail.available
                ? health.agentmail.watcher_running
                  ? "watching"
                  : "paused"
                : "offline"
            }
            metric={health?.agentmail.queue_depth ?? "—"}
            metricLabel="in queue"
          >
            {health?.agentmail.available && (
              <div className="space-y-1">
                <div className="flex justify-between">
                  <span className="text-[10px] text-kcd-text-muted">WebSocket</span>
                  <span className={`font-mono text-[10px] ${health.agentmail.ws_connected ? "text-kcd-green" : "text-kcd-text-muted"}`}>
                    {health.agentmail.ws_connected ? "connected" : "disconnected"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[10px] text-kcd-text-muted">Poll Interval</span>
                  <span className="font-mono text-[10px] text-kcd-text">
                    {health.agentmail.poll_interval ? `${health.agentmail.poll_interval}s` : "—"}
                  </span>
                </div>
                <div className="flex justify-between">
                  <span className="text-[10px] text-kcd-text-muted">Sent / Received</span>
                  <span className="font-mono text-[10px] text-kcd-text">
                    {health.agentmail.messages_sent ?? 0} / {health.agentmail.messages_received ?? 0}
                  </span>
                </div>
                {health.agentmail.error && (
                  <div className="text-[9px] text-kcd-red font-mono">{health.agentmail.error}</div>
                )}
              </div>
            )}
          </SubsystemCard>

          {/* Hooks */}
          <SubsystemCard
            icon="🔗"
            label="Hooks"
            status={statuses.hooks || "offline"}
            statusLabel={statuses.hooks || "offline"}
            metric={
              health?.hooks.available
                ? `${health.hooks.active_dispatches ?? 0}/${health.hooks.concurrency_limit ?? 3}`
                : "—"
            }
            metricLabel="slots used"
          />

          {/* Task Hub */}
          <SubsystemCard
            icon="📋"
            label="Task Hub"
            status={statuses.task_hub || "offline"}
            statusLabel={statuses.task_hub || "offline"}
            metric={openTasks}
            metricLabel="active tasks"
          >
            {health?.task_hub.counts && (
              <div className="space-y-1">
                {Object.entries(health.task_hub.counts).map(([status, count]) => (
                  <div key={status} className="flex justify-between">
                    <span className="text-[10px] text-kcd-text-muted capitalize">{status.replace(/_/g, " ")}</span>
                    <span className="font-mono text-[10px] text-kcd-text">{count}</span>
                  </div>
                ))}
                <div className="flex justify-between pt-1 border-t border-kcd-surface-high/20">
                  <span className="text-[10px] text-kcd-text-dim font-semibold">Total</span>
                  <span className="font-mono text-[10px] text-kcd-text font-semibold">{health.task_hub.total}</span>
                </div>
              </div>
            )}
          </SubsystemCard>
        </div>

        {/* ── Heartbeat Report Panel ───────────────────────────── */}
        {health?.heartbeat.sessions && Object.keys(health.heartbeat.sessions).length > 0 && (
          <HeartbeatReportPanel
            sessions={health.heartbeat.sessions}
            intervalSeconds={health.heartbeat.interval_seconds || 1800}
          />
        )}

        {/* ── VP Fleet Panel ──────────────────────────────────── */}
        {health?.vp_fleet && health.vp_fleet.length > 0 && (
          <div className="space-y-2">
            <div className="font-mono text-[10px] font-semibold uppercase tracking-widest text-kcd-text-dim px-1">
              VP Fleet Workers
            </div>
            <div className="grid gap-px md:grid-cols-2 bg-kcd-surface-high/10">
              {health.vp_fleet.map((vp) => {
                const age = ageSeconds(vp.last_heartbeat_at);
                const vpHealth: SubStatus =
                  vp.effective_status === "active" || vp.status === "active"
                    ? age < 120
                      ? "healthy"
                      : age < 300
                        ? "warning"
                        : "stale"
                    : "idle";
                const leaseSeconds = vp.lease_expires_at
                  ? Math.max(0, (new Date(vp.lease_expires_at).getTime() - Date.now()) / 1000)
                  : NaN;

                return (
                  <div
                    key={vp.vp_id}
                    className="bg-kcd-surface-low/40 backdrop-blur-xl p-4 hover:bg-kcd-surface-high/40 transition-all duration-200"
                  >
                    <div className="flex items-center justify-between mb-2">
                      <span className="font-mono text-[11px] font-semibold uppercase tracking-wider text-kcd-text-dim">
                        {vp.vp_id}
                      </span>
                      <div className="flex items-center gap-2">
                        <StatusOrb status={vpHealth} size="sm" pulse={vpHealth === "healthy"} />
                        <span
                          className={`font-mono text-[10px] uppercase ${
                            vpHealth === "healthy"
                              ? "text-kcd-green"
                              : vpHealth === "warning"
                                ? "text-kcd-amber"
                                : vpHealth === "stale"
                                  ? "text-kcd-red"
                                  : "text-kcd-text-muted"
                          }`}
                        >
                          {vp.effective_status || vp.status || "unknown"}
                        </span>
                      </div>
                    </div>
                    <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-[10px]">
                      <span className="text-kcd-text-muted">Last Heartbeat</span>
                      <span className="font-mono text-kcd-text text-right">{ageLabel(vp.last_heartbeat_at)}</span>
                      {Number.isFinite(leaseSeconds) && (
                        <>
                          <span className="text-kcd-text-muted">Lease TTL</span>
                          <span
                            className={`font-mono text-right ${
                              leaseSeconds < 30 ? "text-kcd-red" : leaseSeconds < 60 ? "text-kcd-amber" : "text-kcd-text"
                            }`}
                          >
                            {Math.round(leaseSeconds)}s
                          </span>
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* ── Error display ────────────────────────────────────── */}
        {error && (
          <div className="font-mono text-[11px] text-kcd-red p-3 bg-kcd-red/5 border-l-2 border-l-kcd-red/40">
            Connection error: {error}
          </div>
        )}

        {/* ── Telemetry Strip ──────────────────────────────────── */}
        {health && (
          <div className="bg-kcd-surface-high/20 px-4 py-2 flex items-center justify-between font-mono text-[9px] text-kcd-text-muted/50 uppercase tracking-wider">
            <span>
              hb interval: {Math.round((health.heartbeat.interval_seconds || 1800) / 60)}min
            </span>
            <span>
              sessions: {health.sessions.active}
            </span>
            <span>
              tasks: {health.task_hub.total ?? 0} total · {completedTasks} completed
            </span>
            <span>epoch: {Math.round(health.timestamp)}</span>
          </div>
        )}
      </main>
    </div>
  );
}
