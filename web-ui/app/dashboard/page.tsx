"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { openOrFocusChatWindow } from "@/lib/chatWindow";
import { fetchSessionDirectory, SessionDirectoryItem } from "@/lib/sessionDirectory";

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

const EMPTY_SUMMARY: SummaryResponse = {
  sessions: { active: 0, total: 0 },
  approvals: { pending: 0, total: 0 },
  cron: { total: 0, enabled: 0 },
  notifications: { unread: 0, total: 0 },
  deployment_profile: { profile: "local_workstation" },
};

export default function DashboardPage() {
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [notifications, setNotifications] = useState<DashboardNotification[]>([]);
  const [sessionDirectory, setSessionDirectory] = useState<SessionDirectoryItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [updatingId, setUpdatingId] = useState<string | null>(null);
  const [bulkUpdating, setBulkUpdating] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [summaryRes, notificationsRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/dashboard/summary`),
        fetch(`${API_BASE}/api/v1/dashboard/notifications?limit=30`),
      ]);
      const summaryData = summaryRes.ok
        ? await summaryRes.json()
        : EMPTY_SUMMARY;
      const notificationsData = notificationsRes.ok
        ? await notificationsRes.json()
        : { notifications: [] };
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
      setNotifications(Array.isArray(notificationsData.notifications) ? notificationsData.notifications : []);
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
        setNotifications((prev) => prev.map((item) => (item.id === id ? updated : item)));
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Dashboard</h1>
          <p className="text-sm text-slate-400">
            Profile: {summary?.deployment_profile?.profile ?? "local_workstation"}
          </p>
        </div>
        <button
          type="button"
          onClick={load}
          className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-sm hover:bg-slate-800"
        >
          Refresh
        </button>
      </div>

      <section className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        {cards.map((card) => (
          <article key={card.label} className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
            <p className="text-xs uppercase tracking-[0.16em] text-slate-400">{card.label}</p>
            <p className="mt-2 text-3xl font-semibold text-cyan-200">{card.value}</p>
          </article>
        ))}
      </section>

      <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-300">Session Directory</h2>
          <span className="text-xs text-slate-500">{sessionDirectory.length} sessions</span>
        </div>
        <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {sessionDirectory.slice(0, 18).map((session) => (
            <article key={session.session_id} className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3">
              <div className="flex items-center justify-between gap-2">
                <p className="truncate font-mono text-xs text-slate-200">{session.session_id}</p>
                <span className="text-[11px] text-slate-500">{session.status}</span>
              </div>
              <p className="mt-1 text-[11px] text-slate-400">
                {session.source} · {session.owner}
              </p>
              <p className="mt-1 text-[11px] text-slate-500">
                memory: {session.memory_mode}
              </p>
              <div className="mt-2 flex flex-wrap gap-2">
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
          {sessionDirectory.length === 0 && (
            <div className="rounded-lg border border-slate-800/80 bg-slate-950/50 p-3 text-sm text-slate-400">
              No sessions discovered yet.
            </div>
          )}
        </div>
      </section>

      <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-300">Notification Center</h2>
          <div className="flex items-center gap-2">
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
          {notifications.map((item) => (
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
                <div className="mt-2">
                  <button
                    type="button"
                    className="rounded border border-slate-700 bg-slate-900/50 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800/60 disabled:opacity-50"
                    onClick={() => updateNotificationStatus(item.id, "read", "read in dashboard")}
                    disabled={updatingId === item.id}
                  >
                    Mark Read
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
