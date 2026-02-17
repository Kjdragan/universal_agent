"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { openOrFocusChatWindow } from "@/lib/chatWindow";
import { fetchSessionDirectory, deleteSessionDirectoryEntry, SessionDirectoryItem } from "@/lib/sessionDirectory";

const API_BASE = "/api/dashboard/gateway";

type SummaryResponse = {
  sessions: { active: number; total: number };
  approvals: { pending: number; total: number };
  cron: { total: number; enabled: number };
  notifications: { unread: number; total: number };
  deployment_profile?: { profile: string };
};

type DashboardNotification = {
  id: string;
  title: string;
  kind: string;
  message: string;
  severity: string;
  status: string;
  created_at: string;
  session_id?: string | null;
  metadata?: Record<string, unknown>;
};

type CoderVpSessionSnapshot = {
  status?: string;
  session_id?: string;
};

type CoderVpEvent = {
  event_type?: string;
  payload?: Record<string, unknown> | null;
  created_at?: string;
};

type CoderVpMetricsSnapshot = {
  generated_at?: string;
  vp_id?: string;
  session?: CoderVpSessionSnapshot | null;
  mission_counts?: Record<string, number>;
  fallback?: {
    missions_with_fallback?: number;
    missions_considered?: number;
    rate?: number;
  };
  latency_seconds?: {
    count?: number;
    avg_seconds?: number | null;
    p95_seconds?: number | null;
    max_seconds?: number | null;
  };
  recent_events?: CoderVpEvent[];
};

type CoderVpDashboardResponse = {
  status?: string;
  detail?: string;
  metrics?: CoderVpMetricsSnapshot | null;
};

const EMPTY_SUMMARY: SummaryResponse = {
  sessions: { active: 0, total: 0 },
  approvals: { pending: 0, total: 0 },
  cron: { total: 0, enabled: 0 },
  notifications: { unread: 0, total: 0 },
  deployment_profile: { profile: "local_workstation" },
};

function formatLocalDateTime(value?: string | number | null): string {
  if (!value) return "--";
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return String(value);
  return parsed.toLocaleString([], {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

export default function DashboardPage() {
  const router = useRouter();
  const sessionSectionRef = useRef<HTMLElement>(null);
  const notificationSectionRef = useRef<HTMLElement>(null);
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [notifications, setNotifications] = useState<DashboardNotification[]>([]);
  const [sessionDirectory, setSessionDirectory] = useState<SessionDirectoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [updatingId, setUpdatingId] = useState<string | null>(null);
  const [bulkUpdating, setBulkUpdating] = useState(false);
  const [sessionFilter, setSessionFilter] = useState<"all" | "active">("all");
  const [notificationFilter, setNotificationFilter] = useState<"all" | "unread">("all");
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [selectedSessions, setSelectedSessions] = useState<Set<string>>(new Set());
  const [deletingIds, setDeletingIds] = useState<Set<string>>(new Set());
  const [commandText, setCommandText] = useState("");
  const [commandSending, setCommandSending] = useState(false);
  const [coderVpSnapshot, setCoderVpSnapshot] = useState<CoderVpDashboardResponse>({
    status: "loading",
    metrics: null,
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [summaryRes, notificationsRes, coderVpRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/dashboard/summary`),
        fetch(`${API_BASE}/api/v1/dashboard/notifications?limit=30`),
        fetch(`${API_BASE}/api/v1/dashboard/metrics/coder-vp?vp_id=vp.coder.primary&mission_limit=50&event_limit=200`),
      ]);
      const summaryData = summaryRes.ok
        ? await summaryRes.json()
        : EMPTY_SUMMARY;
      const notificationsData = notificationsRes.ok
        ? await notificationsRes.json()
        : { notifications: [] };
      const coderVpData = coderVpRes.ok
        ? (await coderVpRes.json()) as CoderVpDashboardResponse
        : {
            status: "unavailable",
            detail: `CODER VP metrics unavailable (${coderVpRes.status})`,
            metrics: null,
          };
      const sessions = await fetchSessionDirectory(120);
      setSummary({
        ...EMPTY_SUMMARY,
        ...(summaryData || {}),
        sessions: {
          ...EMPTY_SUMMARY.sessions,
          ...((summaryData && (summaryData as Partial<SummaryResponse>).sessions) || {}),
        },
        approvals: {
          ...EMPTY_SUMMARY.approvals,
          ...((summaryData && (summaryData as Partial<SummaryResponse>).approvals) || {}),
        },
        cron: {
          ...EMPTY_SUMMARY.cron,
          ...((summaryData && (summaryData as Partial<SummaryResponse>).cron) || {}),
        },
        notifications: {
          ...EMPTY_SUMMARY.notifications,
          ...((summaryData && (summaryData as Partial<SummaryResponse>).notifications) || {}),
        },
      });
      setNotifications(
        Array.isArray(notificationsData.notifications)
          ? notificationsData.notifications.filter(
              (item: DashboardNotification) => item.status !== "dismissed",
            )
          : [],
      );
      setCoderVpSnapshot({
        status: coderVpData.status || "unavailable",
        detail: coderVpData.detail,
        metrics: coderVpData.metrics || null,
      });
      setSessionDirectory(sessions);
    } finally {
      setLoading(false);
    }
  }, []);

  const updateNotificationStatus = useCallback(
    async (
      id: string,
      status: "acknowledged" | "snoozed" | "dismissed" | "read",
      note?: string,
      snoozeMinutes?: number,
    ) => {
      setUpdatingId(id);
      try {
        const res = await fetch(`${API_BASE}/api/v1/dashboard/notifications/${encodeURIComponent(id)}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ status, note, snooze_minutes: snoozeMinutes }),
        });
        if (!res.ok) return;
        const data = await res.json();
        const updated = data.notification as DashboardNotification;
        setNotifications((prev) => {
          if (updated.status === "dismissed") {
            return prev.filter((item) => item.id !== id);
          }
          return prev.map((item) => (item.id === id ? updated : item));
        });
      } finally {
        setUpdatingId((prev) => (prev === id ? null : prev));
      }
    },
    [],
  );

  const bulkUpdateContinuityAlerts = useCallback(
    async (
      status: "acknowledged" | "snoozed" | "dismissed",
      note: string,
      snoozeMinutes?: number,
    ) => {
      setBulkUpdating(true);
      try {
        const res = await fetch(`${API_BASE}/api/v1/dashboard/notifications/bulk`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            status,
            note,
            kind: "continuity_alert",
            current_status: "new",
            snooze_minutes: snoozeMinutes,
            limit: 500,
          }),
        });
        if (!res.ok) return;
        await load();
      } finally {
        setBulkUpdating(false);
      }
    },
    [load],
  );

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 8000);
    return () => window.clearInterval(timer);
  }, [load]);

  const handleCardClick = useCallback(
    (label: string) => {
      switch (label) {
        case "Active Sessions":
          setSessionFilter("active");
          sessionSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
          break;
        case "Pending Approvals":
          router.push("/dashboard/approvals");
          break;
        case "Unread Alerts":
          setNotificationFilter("unread");
          notificationSectionRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
          break;
        case "Enabled Cron Jobs":
          router.push("/dashboard/cron-jobs");
          break;
      }
    },
    [router],
  );

  const cards = useMemo(
    () => [
      { label: "Active Sessions", value: summary?.sessions?.active ?? 0 },
      { label: "Pending Approvals", value: summary?.approvals?.pending ?? 0 },
      { label: "Unread Alerts", value: summary?.notifications?.unread ?? 0 },
      { label: "Enabled Cron Jobs", value: summary?.cron?.enabled ?? 0 },
    ],
    [summary],
  );
  const openContinuityAlerts = useMemo(
    () =>
      notifications.filter(
        (item) => item.kind === "continuity_alert" && item.status === "new",
      ),
    [notifications],
  );
  const visibleNotifications = useMemo(
    () =>
      notificationFilter === "unread"
        ? notifications.filter((item) => item.status === "new")
        : notifications,
    [notificationFilter, notifications],
  );

  const deleteAllVisibleNotifications = useCallback(async () => {
    const targetCount = visibleNotifications.length;
    if (targetCount === 0) return;
    if (!window.confirm(`Delete ${targetCount} notification${targetCount > 1 ? "s" : ""}?`)) return;
    setBulkUpdating(true);
    try {
      const body: Record<string, unknown> = {
        status: "dismissed",
        note: "deleted in dashboard bulk action",
        limit: 1000,
      };
      if (notificationFilter === "unread") {
        body.current_status = "new";
      }
      const res = await fetch(`${API_BASE}/api/v1/dashboard/notifications/bulk`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (!res.ok) return;
      await load();
    } finally {
      setBulkUpdating(false);
    }
  }, [load, notificationFilter, visibleNotifications.length]);
  const coderVpMetrics = coderVpSnapshot.metrics || null;
  const coderFallback = coderVpMetrics?.fallback;
  const coderLatency = coderVpMetrics?.latency_seconds;
  const fallbackRateText =
    typeof coderFallback?.rate === "number"
      ? `${(coderFallback.rate * 100).toFixed(1)}%`
      : "--";
  const p95LatencyText =
    typeof coderLatency?.p95_seconds === "number"
      ? `${coderLatency.p95_seconds.toFixed(1)}s`
      : "--";
  const recentFallbackEvents = useMemo(
    () =>
      (coderVpMetrics?.recent_events || [])
        .filter((event) => event.event_type === "vp.mission.fallback")
        .slice(-3)
        .reverse(),
    [coderVpMetrics],
  );

  const formatFallbackEvent = useCallback((event: CoderVpEvent): string => {
    const payload = event.payload;
    if (!payload || typeof payload !== "object") return "fallback recorded";
    const error = typeof payload.error === "string" ? payload.error : "";
    const reason = typeof payload.reason === "string" ? payload.reason : "";
    return error || reason || "fallback recorded";
  }, []);

  const SOURCE_FILTERS = ["all", "chat", "cron", "telegram", "hook", "local", "api"] as const;

  const inferSourceCategory = useCallback((session: SessionDirectoryItem) => {
    const sid = session.session_id.toLowerCase();
    if (sid.startsWith("tg_")) return "telegram";
    if (sid.startsWith("session_hook_")) return "hook";
    if (sid.startsWith("session_")) return "chat";
    if (sid.startsWith("cron_")) return "cron";
    if (sid.startsWith("api_")) return "api";
    return "local";
  }, []);

  const filteredSessions = useMemo(() => {
    let list = sessionDirectory;
    if (sessionFilter === "active") {
      list = list.filter((s) => s.status === "active");
    }
    if (sourceFilter !== "all") {
      list = list.filter((s) => inferSourceCategory(s) === sourceFilter);
    }
    return list;
  }, [sessionDirectory, sessionFilter, sourceFilter, inferSourceCategory]);

  const toggleSession = useCallback((id: string) => {
    setSelectedSessions((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleAllVisible = useCallback(() => {
    const visibleIds = filteredSessions.map((s) => s.session_id);
    setSelectedSessions((prev) => {
      const allSelected = visibleIds.every((id) => prev.has(id));
      if (allSelected) return new Set();
      return new Set(visibleIds);
    });
  }, [filteredSessions]);

  const deleteSession = useCallback(async (id: string) => {
    if (!window.confirm(`Delete session ${id}?`)) return;
    setDeletingIds((prev) => new Set(prev).add(id));
    try {
      await deleteSessionDirectoryEntry(id);
      setSessionDirectory((prev) => prev.filter((s) => s.session_id !== id));
      setSelectedSessions((prev) => { const next = new Set(prev); next.delete(id); return next; });
    } catch (err) {
      console.error("Delete failed:", err);
    } finally {
      setDeletingIds((prev) => { const next = new Set(prev); next.delete(id); return next; });
    }
  }, []);

  const bulkDeleteSelected = useCallback(async () => {
    const ids = Array.from(selectedSessions);
    if (ids.length === 0) return;
    if (!window.confirm(`Delete ${ids.length} selected session${ids.length > 1 ? "s" : ""}?`)) return;
    setDeletingIds(new Set(ids));
    const succeeded: string[] = [];
    for (const id of ids) {
      try {
        await deleteSessionDirectoryEntry(id);
        succeeded.push(id);
      } catch { /* continue */ }
    }
    setSessionDirectory((prev) => prev.filter((s) => !succeeded.includes(s.session_id)));
    setSelectedSessions(new Set());
    setDeletingIds(new Set());
  }, [selectedSessions]);

  const sendQuickCommand = useCallback(async () => {
    const text = commandText.trim();
    if (!text) return;

    const targetAgent = text.startsWith("@") ? text.split(" ")[0].substring(1) : "system-configuration-agent";
    const prefixed = text.startsWith("@") ? text : `@system-configuration-agent ${text}`;

    // Find existing session for this agent to resume context
    // Ideally we look for a session named "session_{agent_name}"
    // But failing that, we just open a fresh session.
    // For system configuration, let's try to reuse if we see one in the directory.
    let targetSessionId = "";
    const agentSession = sessionDirectory.find(s =>
      s.session_id.includes(targetAgent) ||
      (s.owner === targetAgent)
    );

    if (agentSession) {
      targetSessionId = agentSession.session_id;
    }

    // If we have a target session, reuse it. Otherwise let the chat window handle creation (default behavior)
    // But passing ?message= will auto-send.
    openOrFocusChatWindow({
      sessionId: targetSessionId || undefined,
      role: "writer",
    });

    // We need to wait a bit for the window to open/focus before clearing? 
    // Actually the URL param handles the message passing.
    // But wait - if we construct URL with message, openOrFocusChatWindow needs to support it.
    // It currently takes options but buildChatUrl doesn't support 'message'. 
    // We should fix openOrFocusChatWindow or just construct URL manually here for now to be safe.

    const params = new URLSearchParams();
    if (targetSessionId) params.set("session_id", targetSessionId);
    params.set("role", "writer");
    params.set("message", prefixed);

    const chatUrl = `/?${params.toString()}`;
    const w = window.open(chatUrl, "ua-chat-window");
    if (w) w.focus();

    setCommandText("");
  }, [commandText, sessionDirectory]);

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-slate-400">
            Profile: {summary?.deployment_profile?.profile ?? "local_workstation"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() =>
              openOrFocusChatWindow({
                role: "writer",
                newSession: true,
                focusInput: true,
              })
            }
            className="rounded-lg border border-emerald-700/60 bg-emerald-600/15 px-3 py-1.5 text-sm text-emerald-200 hover:bg-emerald-600/25"
          >
            New Session
          </button>
          <button
            type="button"
            onClick={load}
            className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-sm hover:bg-slate-800"
          >
            Refresh
          </button>
        </div>
      </div>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <article
            key={card.label}
            className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 cursor-pointer transition hover:border-cyan-700/50 hover:bg-slate-800/70"
            onClick={() => handleCardClick(card.label)}
            role="button"
            tabIndex={0}
            onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") handleCardClick(card.label); }}
          >
            <p className="text-xs uppercase tracking-[0.16em] text-slate-400">{card.label}</p>
            <p className="mt-2 text-3xl font-semibold text-cyan-200">{card.value}</p>
          </article>
        ))}
      </section>

      <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <div>
            <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-300">CODER VP Rollout</h2>
            <p className="text-[11px] text-slate-500">
              VP: {coderVpMetrics?.vp_id || "vp.coder.primary"} · status: {coderVpSnapshot.status || "unknown"}
            </p>
          </div>
          <button
            type="button"
            onClick={load}
            className="rounded border border-slate-700 bg-slate-900/50 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800/60"
          >
            Refresh VP
          </button>
        </div>

        <div className="grid gap-2 sm:grid-cols-2 xl:grid-cols-4">
          <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Fallback Rate</p>
            <p className="mt-1 text-xl font-semibold text-amber-200">{fallbackRateText}</p>
          </div>
          <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">p95 Latency</p>
            <p className="mt-1 text-xl font-semibold text-cyan-200">{p95LatencyText}</p>
          </div>
          <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Missions Considered</p>
            <p className="mt-1 text-xl font-semibold text-slate-100">{coderFallback?.missions_considered ?? 0}</p>
          </div>
          <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3">
            <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Fallback Missions</p>
            <p className="mt-1 text-xl font-semibold text-rose-200">{coderFallback?.missions_with_fallback ?? 0}</p>
          </div>
        </div>

        <div className="mt-3 grid gap-2 md:grid-cols-2">
          <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3 text-xs text-slate-300">
            <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Session</p>
            <p className="mt-1">Runtime status: {coderVpMetrics?.session?.status || "unknown"}</p>
            <p className="mt-1">Session ID: {coderVpMetrics?.session?.session_id || "--"}</p>
            <p className="mt-1 text-slate-500">Generated: {formatLocalDateTime(coderVpMetrics?.generated_at)}</p>
          </div>
          <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3 text-xs text-slate-300">
            <p className="text-[10px] uppercase tracking-[0.12em] text-slate-500">Mission mix</p>
            <div className="mt-1 space-y-1">
              <p>completed: {coderVpMetrics?.mission_counts?.completed ?? 0}</p>
              <p>running: {coderVpMetrics?.mission_counts?.running ?? 0}</p>
              <p>failed: {coderVpMetrics?.mission_counts?.failed ?? 0}</p>
            </div>
          </div>
        </div>

        {recentFallbackEvents.length > 0 && (
          <div className="mt-3 rounded-lg border border-amber-800/60 bg-amber-950/20 p-3 text-xs">
            <p className="text-[10px] uppercase tracking-[0.12em] text-amber-300">Recent fallback signals</p>
            <div className="mt-2 space-y-1 text-amber-100">
              {recentFallbackEvents.map((event, idx) => (
                <p key={`${event.created_at || "fallback"}-${idx}`}>
                  {formatLocalDateTime(event.created_at) || "event"}: {formatFallbackEvent(event)}
                </p>
              ))}
            </div>
          </div>
        )}

        {coderVpSnapshot.detail && (
          <p className="mt-2 text-xs text-amber-400">{coderVpSnapshot.detail}</p>
        )}
      </section>

      {/* Quick Command Input */}
      <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="flex items-center gap-3">
          <span className="text-xs uppercase tracking-[0.16em] text-slate-400 shrink-0">Quick Command</span>
          <input
            type="text"
            value={commandText}
            onChange={(e) => setCommandText(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendQuickCommand(); } }}
            placeholder="e.g. delete all sessions except the current one…"
            className="flex-1 rounded-lg border border-slate-700 bg-slate-950/60 px-3 py-2 text-sm text-slate-200 placeholder-slate-500 outline-none focus:border-cyan-700/60"
          />
          <button
            type="button"
            onClick={sendQuickCommand}
            disabled={commandSending || !commandText.trim()}
            className="rounded-lg border border-cyan-700 bg-cyan-900/25 px-4 py-2 text-sm text-cyan-200 hover:bg-cyan-900/40 disabled:opacity-40 transition"
          >
            Send →
          </button>
        </div>
        <p className="mt-1.5 text-[10px] text-slate-500">Routes to the system-configuration-agent by default. Prefix with @agent-name to target a different agent.</p>
      </section>

      <section ref={sessionSectionRef} className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 scroll-mt-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-300">Session Directory</h2>
            {sessionFilter === "active" && (
              <button
                type="button"
                onClick={() => setSessionFilter("all")}
                className="flex items-center gap-1 rounded-full border border-cyan-700/60 bg-cyan-900/20 px-2 py-0.5 text-[10px] text-cyan-200 hover:bg-cyan-900/40 transition"
              >
                Active only
                <span className="ml-0.5">×</span>
              </button>
            )}
          </div>
          <span className="text-xs text-slate-500">
            {filteredSessions.length} session{filteredSessions.length !== 1 ? "s" : ""}
          </span>
        </div>

        {/* Source filter bar */}
        <div className="mb-3 flex flex-wrap gap-1.5">
          {SOURCE_FILTERS.map((src) => (
            <button
              key={src}
              type="button"
              onClick={() => { setSourceFilter(src); setSelectedSessions(new Set()); }}
              className={[
                "rounded-full px-2.5 py-1 text-[11px] capitalize transition border",
                sourceFilter === src
                  ? "border-cyan-600 bg-cyan-900/30 text-cyan-200"
                  : "border-slate-700 bg-slate-800/40 text-slate-400 hover:text-slate-200",
              ].join(" ")}
            >
              {src}
            </button>
          ))}
        </div>

        {/* Bulk action bar */}
        {selectedSessions.size > 0 && (
          <div className="mb-3 flex items-center gap-3 rounded-lg border border-rose-800/50 bg-rose-950/20 px-3 py-2">
            <span className="text-xs text-rose-200">{selectedSessions.size} selected</span>
            <button
              type="button"
              onClick={bulkDeleteSelected}
              disabled={deletingIds.size > 0}
              className="rounded border border-rose-700 bg-rose-900/25 px-3 py-1 text-[11px] text-rose-200 hover:bg-rose-900/40 disabled:opacity-50 transition"
            >
              Delete Selected
            </button>
            <button
              type="button"
              onClick={() => setSelectedSessions(new Set())}
              className="text-[11px] text-slate-400 hover:text-slate-200"
            >
              Clear
            </button>
          </div>
        )}

        {/* Select all toggle */}
        {filteredSessions.length > 0 && (
          <div className="mb-2 flex items-center justify-between">
            <div className="flex items-center gap-2">
              <input
                type="checkbox"
                checked={filteredSessions.length > 0 && filteredSessions.every((s) => selectedSessions.has(s.session_id))}
                onChange={toggleAllVisible}
                className="h-3.5 w-3.5 rounded border-slate-600 bg-slate-900 accent-cyan-500"
              />
              <span className="text-[11px] text-slate-400">Select all visible</span>
            </div>

            <button
              onClick={async () => {
                const count = filteredSessions.length;
                if (!window.confirm(`Delete ALL ${count} visible sessions? This cannot be undone.`)) return;
                const ids = filteredSessions.map(s => s.session_id);
                setDeletingIds(new Set(ids));
                for (const id of ids) {
                  try { await deleteSessionDirectoryEntry(id); } catch { /* ignore */ }
                }
                // We rely on state update from directory refresh or just optimistically clear
                setSessionDirectory(prev => prev.filter(s => !ids.includes(s.session_id)));
                setDeletingIds(new Set());
                setSelectedSessions(new Set());
              }}
              className="rounded border border-red-900/50 bg-red-900/10 px-2 py-0.5 text-xs text-red-400 hover:bg-red-900/30 transition"
            >
              Delete All Visible
            </button>
          </div>
        )}

        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {filteredSessions.map((session) => (
            <article key={session.session_id} className={`rounded-lg border p-3 transition ${selectedSessions.has(session.session_id) ? "border-cyan-700/60 bg-cyan-950/20" : "border-slate-800/80 bg-slate-950/50"} ${deletingIds.has(session.session_id) ? "opacity-40" : ""}`}>
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-2 min-w-0">
                  <input
                    type="checkbox"
                    checked={selectedSessions.has(session.session_id)}
                    onChange={() => toggleSession(session.session_id)}
                    className="h-3.5 w-3.5 shrink-0 rounded border-slate-600 bg-slate-900 accent-cyan-500"
                  />
                  <p className="truncate font-mono text-xs text-slate-200">{session.session_id}</p>
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                  <span className="text-[11px] text-slate-500">{session.status}</span>
                  <button
                    type="button"
                    onClick={() => deleteSession(session.session_id)}
                    disabled={deletingIds.has(session.session_id)}
                    title="Delete session"
                    className="rounded p-0.5 text-rose-400/60 hover:text-rose-300 hover:bg-rose-900/25 transition disabled:opacity-30"
                  >
                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" strokeWidth={1.5} stroke="currentColor" className="h-3.5 w-3.5">
                      <path strokeLinecap="round" strokeLinejoin="round" d="m14.74 9-.346 9m-4.788 0L9.26 9m9.968-3.21c.342.052.682.107 1.022.166m-1.022-.165L18.16 19.673a2.25 2.25 0 0 1-2.244 2.077H8.084a2.25 2.25 0 0 1-2.244-2.077L4.772 5.79m14.456 0a48.108 48.108 0 0 0-3.478-.397m-12 .562c.34-.059.68-.114 1.022-.165m0 0a48.11 48.11 0 0 1 3.478-.397m7.5 0v-.916c0-1.18-.91-2.164-2.09-2.201a51.964 51.964 0 0 0-3.32 0c-1.18.037-2.09 1.022-2.09 2.201v.916m7.5 0a48.667 48.667 0 0 0-7.5 0" />
                    </svg>
                  </button>
                </div>
              </div>
              <p className="mt-1 text-[11px] text-slate-400">
                {inferSourceCategory(session)} · {session.owner}
              </p>
              {session.description ? (
                <p
                  className="mt-1 text-[11px] text-slate-300/90 truncate"
                  title={session.description}
                >
                  {session.description}
                </p>
              ) : (
                <p className="mt-1 text-[11px] text-slate-600 italic truncate">no description yet</p>
              )}
              <p className="mt-1 text-[11px] text-slate-500">
                memory: {session.memory_mode}
              </p>
              <p className="mt-1 text-[11px] text-slate-500">
                last activity: {formatLocalDateTime(session.last_activity)}
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
                {!session.session_id.startsWith("vp_") && (
                  <button
                    type="button"
                    className="rounded border border-cyan-700 bg-cyan-900/25 px-2 py-1 text-[11px] text-cyan-200 hover:bg-cyan-900/35"
                    onClick={() =>
                      openOrFocusChatWindow({
                        sessionId: session.session_id,
                        attachMode: "tail",
                        role: "writer",
                      })
                    }
                  >
                    Open Writer
                  </button>
                )}
                <button
                  type="button"
                  className="rounded border border-amber-700 bg-amber-900/20 px-2 py-1 text-[11px] text-amber-200 hover:bg-amber-900/30"
                  onClick={() =>
                    openOrFocusChatWindow({
                      sessionId: session.session_id,
                      attachMode: "tail",
                      role: "viewer",
                    })
                  }
                >
                  Open Viewer
                </button>
              </div>
            </article>
          ))}
          {filteredSessions.length === 0 && (
            <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3 text-sm text-slate-400">
              {sessionDirectory.length === 0 ? "No sessions discovered yet." : "No sessions match the current filter."}
            </div>
          )}
        </div>
      </section >

      <section ref={notificationSectionRef} className="rounded-xl border border-slate-800 bg-slate-900/70 p-4 scroll-mt-4">
        <div className="mb-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-300">Notification Center</h2>
            {notificationFilter === "unread" && (
              <button
                type="button"
                onClick={() => setNotificationFilter("all")}
                className="flex items-center gap-1 rounded-full border border-cyan-700/60 bg-cyan-900/20 px-2 py-0.5 text-[10px] text-cyan-200 hover:bg-cyan-900/40 transition"
              >
                Unread only
                <span className="ml-0.5">×</span>
              </button>
            )}
          </div>
          <div className="flex items-center gap-2">
            {visibleNotifications.length > 0 && (
              <button
                type="button"
                onClick={deleteAllVisibleNotifications}
                disabled={bulkUpdating}
                className="rounded border border-rose-800/70 bg-rose-900/20 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-900/35 disabled:opacity-50"
              >
                Delete All ({visibleNotifications.length})
              </button>
            )}
            {openContinuityAlerts.length > 0 && (
              <>
                <button
                  type="button"
                  onClick={() => bulkUpdateContinuityAlerts("acknowledged", "acknowledged in dashboard bulk action")}
                  disabled={bulkUpdating}
                  className="rounded border border-emerald-800/70 bg-emerald-900/20 px-2 py-1 text-[11px] text-emerald-200 hover:bg-emerald-900/35 disabled:opacity-50"
                >
                  Ack All Continuity ({openContinuityAlerts.length})
                </button>
                <button
                  type="button"
                  onClick={() => bulkUpdateContinuityAlerts("snoozed", "snoozed in dashboard bulk action", 30)}
                  disabled={bulkUpdating}
                  className="rounded border border-amber-800/70 bg-amber-900/20 px-2 py-1 text-[11px] text-amber-200 hover:bg-amber-900/35 disabled:opacity-50"
                >
                  Snooze All 30m
                </button>
                <button
                  type="button"
                  onClick={() => bulkUpdateContinuityAlerts("dismissed", "dismissed in dashboard bulk action")}
                  disabled={bulkUpdating}
                  className="rounded border border-slate-700 bg-slate-900/50 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800/60 disabled:opacity-50"
                >
                  Dismiss All
                </button>
              </>
            )}
            {loading && <span className="text-xs text-slate-500">Refreshing…</span>}
          </div>
        </div>
        <div className="space-y-2">
          {notifications.length === 0 && (
            <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3 text-sm text-slate-400">
              No notifications yet.
            </div>
          )}
          {visibleNotifications.map((item) => (
            <div key={item.id} className="rounded-lg border border-slate-800/80 bg-slate-950/60 p-3">
              <div className="flex items-center justify-between">
                <p className="text-sm font-semibold">{item.title}</p>
                <span className="text-[11px] uppercase tracking-[0.14em] text-slate-500">{item.status}</span>
              </div>
              <p className="mt-1 text-sm text-slate-300">{item.message}</p>
              <p className="mt-2 text-[11px] text-slate-500">
                {item.kind} · {item.session_id || "global"} · {item.created_at}
              </p>
              {item.kind === "continuity_alert" && (
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="rounded border border-emerald-800/70 bg-emerald-900/20 px-2 py-1 text-[11px] text-emerald-200 hover:bg-emerald-900/35 disabled:opacity-50"
                    onClick={() => updateNotificationStatus(item.id, "acknowledged", "acknowledged in dashboard")}
                    disabled={updatingId === item.id}
                  >
                    Acknowledge
                  </button>
                  <button
                    type="button"
                    className="rounded border border-amber-800/70 bg-amber-900/20 px-2 py-1 text-[11px] text-amber-200 hover:bg-amber-900/35 disabled:opacity-50"
                    onClick={() => updateNotificationStatus(item.id, "snoozed", "snoozed in dashboard", 30)}
                    disabled={updatingId === item.id}
                  >
                    Snooze 30m
                  </button>
                  <button
                    type="button"
                    className="rounded border border-slate-700 bg-slate-900/50 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800/60 disabled:opacity-50"
                    onClick={() => updateNotificationStatus(item.id, "dismissed", "dismissed in dashboard")}
                    disabled={updatingId === item.id}
                  >
                    Dismiss
                  </button>
                </div>
              )}
              {item.kind !== "continuity_alert" && item.status === "new" && (
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    type="button"
                    className="rounded border border-slate-700 bg-slate-900/50 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800/60 disabled:opacity-50"
                    onClick={() => updateNotificationStatus(item.id, "read", "read in dashboard")}
                    disabled={updatingId === item.id}
                  >
                    Mark Read
                  </button>
                  <button
                    type="button"
                    className="rounded border border-rose-800/70 bg-rose-900/20 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-900/35 disabled:opacity-50"
                    onClick={() => updateNotificationStatus(item.id, "dismissed", "deleted in dashboard")}
                    disabled={updatingId === item.id}
                  >
                    Delete
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </section>
    </div >
  );
}
