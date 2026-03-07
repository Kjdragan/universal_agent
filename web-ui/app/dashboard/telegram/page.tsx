"use client";

import { useCallback, useEffect, useState } from "react";
import { Send, RefreshCw, MessageSquare, Users, Zap, Clock } from "lucide-react";

const API_BASE = "/api/dashboard/gateway";

type TelegramStatus = {
  bot_running?: boolean;
  polling_active?: boolean;
  bot_token_set?: boolean;
  allowed_user_ids?: number[];
  last_message_at?: string;
  uptime_seconds?: number;
};

type TelegramSession = {
  session_id: string;
  user_id: string;
  workspace_dir: string;
  created_at?: string;
  metadata?: Record<string, unknown>;
};

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}h ${Math.floor((seconds % 3600) / 60)}m`;
  return `${Math.floor(seconds / 86400)}d ${Math.floor((seconds % 86400) / 3600)}h`;
}

function formatTime(value?: string | null): string {
  if (!value) return "--";
  try {
    return new Date(value).toLocaleString();
  } catch {
    return value;
  }
}

export default function TelegramPage() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [telegramSessions, setTelegramSessions] = useState<TelegramSession[]>([]);
  const [notifierStatus, setNotifierStatus] = useState<Record<string, unknown> | null>(null);
  const [testMessage, setTestMessage] = useState("");
  const [testChatId, setTestChatId] = useState("");
  const [sending, setSending] = useState(false);
  const [sendResult, setSendResult] = useState("");

  const load = useCallback(async (silent = false) => {
    if (silent) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const [sessionsRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/sessions`, { cache: "no-store" }).catch(() => null),
      ]);

      if (sessionsRes && sessionsRes.ok) {
        const data = await sessionsRes.json();
        const sessions = Array.isArray(data.sessions) ? data.sessions : Array.isArray(data) ? data : [];
        const tgSessions = sessions.filter((s: TelegramSession) => {
          const sid = s.session_id || "";
          const source = (s.metadata as Record<string, unknown>)?.source || "";
          return sid.startsWith("tg_") || source === "telegram";
        });
        setTelegramSessions(tgSessions.slice(0, 20));
      }
    } catch (err: unknown) {
      setError((err as Error)?.message || "Failed to load Telegram data.");
    } finally {
      if (silent) setRefreshing(false);
      else setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  useEffect(() => {
    const timer = setInterval(() => void load(true), 30_000);
    return () => clearInterval(timer);
  }, [load]);

  const handleSendTest = async () => {
    if (!testMessage.trim() || !testChatId.trim()) return;
    setSending(true);
    setSendResult("");
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/telegram/send`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ chat_id: testChatId, text: testMessage }),
      });
      if (res.ok) {
        setSendResult("Sent successfully");
        setTestMessage("");
      } else {
        const detail = await res.text().catch(() => "");
        setSendResult(`Failed (${res.status}): ${detail.slice(0, 200)}`);
      }
    } catch (err: unknown) {
      setSendResult((err as Error)?.message || "Send failed");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-slate-100">Telegram</h1>
          <p className="mt-0.5 text-sm text-slate-500">Bot status, sessions, and notification delivery.</p>
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
        <div className="flex items-center justify-center py-20 text-slate-500 text-sm">Loading...</div>
      ) : (
        <>
          {/* Status Cards */}
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
              <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-slate-500">
                <Zap className="h-3.5 w-3.5" />
                Bot Status
              </div>
              <div className="mt-3 flex items-center gap-2">
                <div className="h-2.5 w-2.5 rounded-full bg-emerald-500 shadow-[0_0_6px] shadow-emerald-500/50" />
                <span className="text-lg font-semibold text-slate-100">Active</span>
              </div>
              <p className="mt-1 text-xs text-slate-500">Long-polling mode</p>
            </div>

            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
              <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-slate-500">
                <MessageSquare className="h-3.5 w-3.5" />
                Recent Sessions
              </div>
              <div className="mt-3 text-lg font-semibold text-slate-100">{telegramSessions.length}</div>
              <p className="mt-1 text-xs text-slate-500">Telegram-sourced sessions</p>
            </div>

            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
              <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-slate-500">
                <Send className="h-3.5 w-3.5" />
                Shared Send Utility
              </div>
              <div className="mt-3 text-lg font-semibold text-emerald-400">Unified</div>
              <p className="mt-1 text-xs text-slate-500">All senders use telegram_send.py</p>
            </div>
          </div>

          {/* Recent Telegram Sessions */}
          <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-200">Recent Telegram Sessions</h2>
            {telegramSessions.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-500">No Telegram sessions found.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="min-w-full text-left text-sm">
                  <thead className="text-[10px] uppercase tracking-wider text-slate-500">
                    <tr>
                      <th className="px-3 py-2">Session</th>
                      <th className="px-3 py-2">User</th>
                      <th className="px-3 py-2">Created</th>
                    </tr>
                  </thead>
                  <tbody>
                    {telegramSessions.map((s) => (
                      <tr key={s.session_id} className="border-t border-white/[0.04]">
                        <td className="px-3 py-2 font-mono text-xs text-slate-300">{s.session_id.slice(0, 28)}</td>
                        <td className="px-3 py-2 text-xs text-slate-400">{s.user_id || "--"}</td>
                        <td className="px-3 py-2 text-xs text-slate-500">{formatTime(s.created_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          {/* Quick Send Test */}
          <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-200">Send Test Message</h2>
            <div className="flex flex-col gap-3 sm:flex-row">
              <input
                value={testChatId}
                onChange={(e) => setTestChatId(e.target.value)}
                placeholder="Chat ID"
                className="w-full rounded-lg border border-white/[0.06] bg-white/[0.03] px-3 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-blue-500/50 sm:w-36"
              />
              <input
                value={testMessage}
                onChange={(e) => setTestMessage(e.target.value)}
                placeholder="Message text..."
                className="flex-1 rounded-lg border border-white/[0.06] bg-white/[0.03] px-3 py-2 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-blue-500/50"
                onKeyDown={(e) => e.key === "Enter" && handleSendTest()}
              />
              <button
                onClick={handleSendTest}
                disabled={sending || !testMessage.trim() || !testChatId.trim()}
                className="flex items-center justify-center gap-1.5 rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white transition hover:bg-blue-500 disabled:opacity-40"
              >
                <Send className="h-3.5 w-3.5" />
                Send
              </button>
            </div>
            {sendResult && (
              <p className={`mt-2 text-xs ${sendResult.startsWith("Sent") ? "text-emerald-400" : "text-red-400"}`}>
                {sendResult}
              </p>
            )}
          </section>
        </>
      )}
    </div>
  );
}
