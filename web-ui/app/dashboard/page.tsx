"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8002";
const OPS_TOKEN = process.env.NEXT_PUBLIC_UA_OPS_TOKEN;

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
};

function headers(): Record<string, string> {
  return OPS_TOKEN ? { "X-UA-OPS-TOKEN": OPS_TOKEN } : {};
}

export default function DashboardPage() {
  const [summary, setSummary] = useState<SummaryResponse | null>(null);
  const [notifications, setNotifications] = useState<DashboardNotification[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [summaryRes, notificationsRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/dashboard/summary`, { headers: headers() }),
        fetch(`${API_BASE}/api/v1/dashboard/notifications?limit=30`, { headers: headers() }),
      ]);
      const summaryData = await summaryRes.json();
      const notificationsData = await notificationsRes.json();
      setSummary(summaryData);
      setNotifications(notificationsData.notifications || []);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
    const timer = window.setInterval(load, 8000);
    return () => window.clearInterval(timer);
  }, [load]);

  const cards = useMemo(
    () => [
      { label: "Active Sessions", value: summary?.sessions.active ?? 0 },
      { label: "Pending Approvals", value: summary?.approvals.pending ?? 0 },
      { label: "Unread Alerts", value: summary?.notifications.unread ?? 0 },
      { label: "Enabled Cron Jobs", value: summary?.cron.enabled ?? 0 },
    ],
    [summary],
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
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-300">Notification Center</h2>
          {loading && <span className="text-xs text-slate-500">Refreshing…</span>}
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
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
