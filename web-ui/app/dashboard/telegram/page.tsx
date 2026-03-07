"use client";

import { useCallback, useEffect, useState } from "react";
import { Send, RefreshCw, MessageSquare, Zap, Radio, Hash, Clock, CheckCircle, AlertTriangle } from "lucide-react";

const API_BASE = "/api/dashboard/gateway";

type BotStatus = {
  service_active?: boolean;
  polling_mode?: string;
  allowed_user_ids?: string | null;
};

type Channel = {
  name: string;
  env_var: string;
  configured: boolean;
};

type RecentNotification = {
  id: string;
  kind: string;
  title: string;
  message: string;
  severity: string;
  status: string;
  created_at: string;
};

type TelegramSession = {
  session_id: string;
  user_id: string;
  status: string;
  last_activity: string;
};

type TelegramData = {
  bot?: BotStatus;
  notifier?: Record<string, unknown>;
  channels?: Channel[];
  recent_notifications?: RecentNotification[];
  telegram_sessions?: TelegramSession[];
};

function formatTime(value?: string | null): string {
  if (!value) return "--";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

function severityColor(severity: string): string {
  const s = severity.toLowerCase();
  if (s === "error") return "text-red-400";
  if (s === "warning") return "text-amber-400";
  if (s === "success") return "text-emerald-400";
  return "text-slate-400";
}

function severityIcon(severity: string) {
  const s = severity.toLowerCase();
  if (s === "error") return <AlertTriangle className="h-3.5 w-3.5 text-red-400" />;
  if (s === "warning") return <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />;
  if (s === "success") return <CheckCircle className="h-3.5 w-3.5 text-emerald-400" />;
  return <Radio className="h-3.5 w-3.5 text-slate-400" />;
}

export default function TelegramPage() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [data, setData] = useState<TelegramData>({});

  const load = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/telegram`, { cache: "no-store" });
      if (res.ok) {
        setData(await res.json());
      } else {
        const detail = await res.text().catch(() => "");
        throw new Error(`Failed (${res.status}): ${detail.slice(0, 200)}`);
      }
    } catch (err: unknown) {
      setError((err as Error)?.message || "Failed to load Telegram data.");
    } finally {
      if (silent) setRefreshing(false);
      else setLoading(false);
    }
  }, []);

  useEffect(() => { void load(false); }, [load]);
  useEffect(() => {
    const timer = setInterval(() => void load(true), 15_000);
    return () => clearInterval(timer);
  }, [load]);

  const bot = data.bot || {};
  const channels = data.channels || [];
  const notifications = data.recent_notifications || [];
  const sessions = data.telegram_sessions || [];

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-100">Telegram</h1>
          <p className="mt-0.5 text-sm text-slate-500">Bot status, channels, delivery activity, and sessions.</p>
        </div>
        <button
          onClick={() => void load(true)}
          disabled={refreshing}
          className="flex items-center gap-1.5 rounded-lg border border-white/[0.06] bg-white/[0.03] px-3 py-1.5 text-xs text-slate-300 transition hover:bg-white/[0.06]"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${refreshing ? "animate-spin" : ""}`} />
          Refresh
        </button>
      </div>

      {error && (
        <div className="rounded-xl border border-red-500/20 bg-red-500/5 px-4 py-3 text-sm text-red-300">{error}</div>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-20 text-slate-500 text-sm">
          <div className="h-4 w-4 animate-spin rounded-full border-2 border-slate-600 border-t-blue-400 mr-3" />
          Loading Telegram status...
        </div>
      ) : (
        <>
          {/* Status Cards */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
              <div className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider text-slate-500">
                <Zap className="h-3.5 w-3.5" />
                Bot Service
              </div>
              <div className="mt-3 flex items-center gap-2">
                <div className={`h-2.5 w-2.5 rounded-full ${bot.service_active ? "bg-emerald-500 shadow-[0_0_6px] shadow-emerald-500/50" : "bg-red-500"}`} />
                <span className="text-lg font-semibold text-slate-100">{bot.service_active ? "Active" : "Down"}</span>
              </div>
              <p className="mt-1 text-xs text-slate-500">{bot.polling_mode || "long_polling"}</p>
            </div>

            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
              <div className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider text-slate-500">
                <Hash className="h-3.5 w-3.5" />
                Channels
              </div>
              <div className="mt-3 text-lg font-semibold text-slate-100">{channels.filter(c => c.configured).length}/{channels.length}</div>
              <p className="mt-1 text-xs text-slate-500">configured</p>
            </div>

            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
              <div className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider text-slate-500">
                <Radio className="h-3.5 w-3.5" />
                Recent Activity
              </div>
              <div className="mt-3 text-lg font-semibold text-slate-100">{notifications.length}</div>
              <p className="mt-1 text-xs text-slate-500">notifications</p>
            </div>

            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
              <div className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider text-slate-500">
                <MessageSquare className="h-3.5 w-3.5" />
                Bot Sessions
              </div>
              <div className="mt-3 text-lg font-semibold text-slate-100">{sessions.length}</div>
              <p className="mt-1 text-xs text-slate-500">recent</p>
            </div>
          </div>

          {/* Channels */}
          <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-200">Telegram Channels</h2>
            <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-3">
              {channels.map((ch) => (
                <div key={ch.env_var} className="flex items-center gap-3 rounded-lg border border-white/[0.04] bg-white/[0.02] px-3 py-2.5">
                  <div className={`h-2 w-2 rounded-full ${ch.configured ? "bg-emerald-500" : "bg-slate-600"}`} />
                  <div className="flex-1 min-w-0">
                    <p className="text-sm text-slate-200 truncate">{ch.name}</p>
                    <p className="text-[10px] text-slate-500 font-mono">{ch.env_var}</p>
                  </div>
                  <span className={`text-[10px] ${ch.configured ? "text-emerald-400" : "text-slate-500"}`}>
                    {ch.configured ? "Active" : "Not set"}
                  </span>
                </div>
              ))}
            </div>
          </section>

          {/* Recent Notification Activity */}
          <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-200">Recent Delivery Activity</h2>
            {notifications.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-500">No recent Telegram-related activity.</p>
            ) : (
              <div className="space-y-2 max-h-[400px] overflow-y-auto">
                {notifications.map((n) => (
                  <div key={n.id} className="flex items-start gap-2.5 rounded-lg border border-white/[0.04] bg-white/[0.01] px-3 py-2">
                    {severityIcon(n.severity)}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <p className="text-xs font-medium text-slate-200 truncate">{n.title}</p>
                        <span className={`text-[10px] ${severityColor(n.severity)}`}>{n.severity}</span>
                      </div>
                      <p className="mt-0.5 text-[11px] text-slate-400 line-clamp-2">{n.message}</p>
                      <div className="mt-1 flex items-center gap-3 text-[10px] text-slate-500">
                        <span>{n.kind}</span>
                        <span>{formatTime(n.created_at)}</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Bot Sessions */}
          {sessions.length > 0 && (
            <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
              <h2 className="mb-3 text-sm font-semibold text-slate-200">Recent Bot Sessions</h2>
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-sm">
                  <thead className="text-[10px] uppercase tracking-wider text-slate-500">
                    <tr>
                      <th className="px-3 py-2">Session</th>
                      <th className="px-3 py-2">User</th>
                      <th className="px-3 py-2">Status</th>
                      <th className="px-3 py-2">Last Activity</th>
                    </tr>
                  </thead>
                  <tbody>
                    {sessions.map((s) => (
                      <tr key={s.session_id} className="border-t border-white/[0.04]">
                        <td className="px-3 py-2 font-mono text-xs text-slate-300">{s.session_id.slice(0, 30)}</td>
                        <td className="px-3 py-2 text-xs text-slate-400">{s.user_id || "--"}</td>
                        <td className="px-3 py-2 text-xs text-slate-400">{s.status || "--"}</td>
                        <td className="px-3 py-2 text-xs text-slate-500">{formatTime(s.last_activity)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </div>
  );
}
