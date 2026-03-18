"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { RefreshCw, MessageSquare, Zap, Radio, Hash, CheckCircle, CheckCircle2, AlertTriangle, RotateCcw, Trash2, XCircle } from "lucide-react";

const API_BASE = "/api/dashboard/gateway";

type BotStatus = {
  service_active?: boolean;
  enabled_by_policy?: boolean;
  service_scope?: string;
  polling_mode?: string;
  allowed_user_ids?: string | null;
};

type Channel = {
  name: string;
  env_var: string;
  configured: boolean;
};

type ActivityEvent = {
  id: string;
  kind: string;
  title: string;
  message: string;
  severity: string;
  status: string;
  requires_action?: boolean;
  session_id?: string;
  created_at: string;
  updated_at?: string;
  actions?: ActivityAction[];
  metadata?: Record<string, unknown>;
};

type ActivityAction = {
  id: string;
  label: string;
  type: string;
  href?: string;
};

type TelegramSession = {
  session_id: string;
  user_id: string;
  status: string;
  last_activity: string;
};

type ActiveTutorialRun = {
  run_key: string;
  session_id: string;
  video_id: string;
  title: string;
  stage: string;
  kind: string;
  status: string;
  severity: string;
  created_at: string;
  message: string;
};

type ResolvedEvent = ActivityEvent & { resolution_type?: string };

type TelegramCounts = {
  pipeline_activity?: number;
  active_tutorial_runs?: number;
  recent_failures?: number;
  resolved_failures?: number;
  actionable_alerts?: number;
  recovery_events?: number;
  telegram_sessions?: number;
};

type TelegramData = {
  bot?: BotStatus;
  notifier?: Record<string, unknown>;
  channels?: Channel[];
  recent_notifications?: ActivityEvent[];
  pipeline_activity?: ActivityEvent[];
  recent_failures?: ActivityEvent[];
  resolved_failures?: ResolvedEvent[];
  actionable_alerts?: ActivityEvent[];
  recovery_events?: ActivityEvent[];
  active_tutorial_runs?: ActiveTutorialRun[];
  telegram_sessions?: TelegramSession[];
  counts?: TelegramCounts;
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

function sectionHeading(label: string, count: number): string {
  return `${label}${count > 0 ? ` (${count})` : ""}`;
}

function stageLabel(raw: string): string {
  const stage = (raw || "").trim().toLowerCase();
  if (!stage) return "queued";
  return stage.replace(/_/g, " ");
}

function normalizeErrorMessage(status: number, raw: string): string {
  const trimmed = String(raw || "").trim();
  if (!trimmed) return `Failed (${status})`;
  try {
    const parsed = JSON.parse(trimmed) as { detail?: string; error?: string };
    const detail = String(parsed?.detail || "").trim();
    if (detail) return `Failed (${status}): ${detail}`;
    const error = String(parsed?.error || "").trim();
    if (error) return `Failed (${status}): ${error}`;
  } catch {
    // non-json
  }
  if (trimmed.includes("<html") || trimmed.includes("<!DOCTYPE")) {
    return `Failed (${status}): Gateway upstream unavailable.`;
  }
  return `Failed (${status}): ${trimmed.slice(0, 200)}`;
}

export default function TelegramPage() {
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const [data, setData] = useState<TelegramData>({});
  const [deletingIds, setDeletingIds] = useState<Record<string, boolean>>({});
  const [deletingAllActivity, setDeletingAllActivity] = useState(false);

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
        throw new Error(normalizeErrorMessage(res.status, detail));
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

  const removeActivityIdsFromState = useCallback((ids: string[]) => {
    const idSet = new Set(ids.filter(Boolean));
    if (idSet.size === 0) return;
    setData((current) => {
      const filterEvents = (items?: ActivityEvent[]) => (items || []).filter((item) => !idSet.has(item.id));
      const nextPipelineActivity = filterEvents(current.pipeline_activity);
      const nextRecentNotifications = filterEvents(current.recent_notifications);
      const nextFailures = filterEvents(current.recent_failures);
      const nextActionable = filterEvents(current.actionable_alerts);
      const nextRecovery = filterEvents(current.recovery_events);
      const nextCounts = {
        ...current.counts,
        pipeline_activity: nextPipelineActivity.length,
        recent_failures: nextFailures.length,
        actionable_alerts: nextActionable.length,
        recovery_events: nextRecovery.length,
      };
      return {
        ...current,
        pipeline_activity: nextPipelineActivity,
        recent_notifications: nextRecentNotifications,
        recent_failures: nextFailures,
        actionable_alerts: nextActionable,
        recovery_events: nextRecovery,
        counts: nextCounts,
      };
    });
  }, []);

  const deleteActivityEvent = useCallback(async (eventId: string) => {
    const normalized = String(eventId || "").trim();
    if (!normalized) return;
    setDeletingIds((current) => ({ ...current, [normalized]: true }));
    try {
      const res = await fetch(`${API_BASE}/api/v1/dashboard/activity/${encodeURIComponent(normalized)}`, {
        method: "DELETE",
      });
      if (!res.ok) {
        const detail = await res.text().catch(() => "");
        throw new Error(normalizeErrorMessage(res.status, detail));
      }
      removeActivityIdsFromState([normalized]);
    } catch (err: unknown) {
      setError((err as Error)?.message || "Failed to delete delivery activity event.");
    } finally {
      setDeletingIds((current) => {
        const next = { ...current };
        delete next[normalized];
        return next;
      });
    }
  }, [removeActivityIdsFromState]);

  const bot = data.bot || {};
  const botEnabled = bot.enabled_by_policy !== false;
  const botStateLabel = !botEnabled ? "Disabled" : bot.service_active ? "Active" : "Down";
  const botStateDotClass = !botEnabled
    ? "bg-slate-500"
    : bot.service_active
      ? "bg-emerald-500 shadow-[0_0_6px] shadow-emerald-500/50"
      : "bg-red-500";
  const botStateDetail = !botEnabled
    ? "disabled by runtime policy"
    : bot.service_scope
      ? `${bot.polling_mode || "long_polling"} (${bot.service_scope})`
      : (bot.polling_mode || "long_polling");
  const channels = data.channels || [];
  const notifications = data.recent_notifications || [];
  const pipelineActivity = data.pipeline_activity || notifications;
  const failures = data.recent_failures || [];
  const resolvedFailures: ResolvedEvent[] = data.resolved_failures || [];
  const actionable = data.actionable_alerts || [];
  const recoveryEvents = data.recovery_events || [];
  const activeTutorialRuns = data.active_tutorial_runs || [];
  const sessions = data.telegram_sessions || [];
  const counts = data.counts || {};

  const deleteAllDeliveryActivity = useCallback(async () => {
    const ids = pipelineActivity.map((item) => item.id).filter(Boolean);
    if (ids.length === 0) return;
    setDeletingAllActivity(true);
    setError("");
    try {
      const results = await Promise.all(
        ids.map(async (id) => {
          const res = await fetch(`${API_BASE}/api/v1/dashboard/activity/${encodeURIComponent(id)}`, {
            method: "DELETE",
          });
          return { id, ok: res.ok, detail: res.ok ? "" : await res.text().catch(() => "") };
        }),
      );
      const failed = results.filter((item) => !item.ok);
      const deleted = results.filter((item) => item.ok).map((item) => item.id);
      if (deleted.length > 0) removeActivityIdsFromState(deleted);
      if (failed.length > 0) {
        throw new Error(normalizeErrorMessage(500, failed[0]?.detail || "Failed deleting one or more activity items."));
      }
    } catch (err: unknown) {
      setError((err as Error)?.message || "Failed to delete delivery activity events.");
      void load(true);
    } finally {
      setDeletingAllActivity(false);
    }
  }, [load, pipelineActivity, removeActivityIdsFromState]);

  function actionPriority(action: ActivityAction): number {
    const id = String(action.id || "").trim().toLowerCase();
    const priorityMap: Record<string, number> = {
      open_tutorial_artifacts: 1,
      open_repo: 2,
      open_review_artifacts: 3,
      open_report: 4,
      open_artifact: 5,
      open_findings: 6,
      open_investigation: 7,
      open_session: 8,
      view_csi: 9,
      view: 10,
      copy_runbook_command: 11,
    };
    return priorityMap[id] ?? 50;
  }

  function primaryActivityActions(event: ActivityEvent): ActivityAction[] {
    const actions = Array.isArray(event.actions) ? event.actions : [];
    return actions
      .filter((action) => {
        const id = String(action.id || "").trim().toLowerCase();
        if (!id) return false;
        return !["mark_read", "snooze", "unsnooze", "pin", "unpin", "send_to_simone"].includes(id);
      })
      .sort((left, right) => actionPriority(left) - actionPriority(right))
      .slice(0, 2);
  }

  async function handleActivityAction(event: ActivityEvent, action: ActivityAction): Promise<void> {
    const id = String(action.id || "").trim().toLowerCase();
    if (action.type === "link") return;
    if (id === "copy_runbook_command") {
      const command = String(event.metadata?.primary_runbook_command || "").trim();
      if (!command) return;
      try {
        await navigator.clipboard.writeText(command);
      } catch {
        setError("Failed to copy runbook command.");
      }
    }
  }

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
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-6">
            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
              <div className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider text-slate-500">
                <Zap className="h-3.5 w-3.5" />
                Bot Service
              </div>
              <div className="mt-3 flex items-center gap-2">
                <div className={`h-2.5 w-2.5 rounded-full ${botStateDotClass}`} />
                <span className="text-lg font-semibold text-slate-100">{botStateLabel}</span>
              </div>
              <p className="mt-1 text-xs text-slate-500">{botStateDetail}</p>
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
                Pipeline Activity
              </div>
              <div className="mt-3 text-lg font-semibold text-slate-100">{counts.pipeline_activity ?? pipelineActivity.length}</div>
              <p className="mt-1 text-xs text-slate-500">events</p>
            </div>

            <div className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
              <div className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider text-slate-500">
                <MessageSquare className="h-3.5 w-3.5" />
                Active Tutorials
              </div>
              <div className="mt-3 text-lg font-semibold text-slate-100">{counts.active_tutorial_runs ?? activeTutorialRuns.length}</div>
              <p className="mt-1 text-xs text-slate-500">in pipeline</p>
            </div>

            <div
              className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 cursor-pointer hover:border-rose-500/30 transition-colors"
              onClick={() => document.getElementById("section-failures")?.scrollIntoView({ behavior: "smooth", block: "start" })}
              title="Jump to Failures section"
            >
              <div className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider text-slate-500">
                <AlertTriangle className="h-3.5 w-3.5" />
                Failures
              </div>
              <div className="mt-3 text-lg font-semibold text-slate-100">{counts.recent_failures ?? failures.length}</div>
              <p className="mt-1 text-xs text-slate-500">recent warnings/errors</p>
            </div>

            <div
              className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 cursor-pointer hover:border-emerald-500/30 transition-colors"
              onClick={() => document.getElementById("section-recovery")?.scrollIntoView({ behavior: "smooth", block: "start" })}
              title="Jump to Recovery section"
            >
              <div className="flex items-center gap-2 text-[10px] font-medium uppercase tracking-wider text-slate-500">
                <RotateCcw className="h-3.5 w-3.5" />
                Recovery
              </div>
              <div className="mt-3 text-lg font-semibold text-slate-100">{counts.recovery_events ?? recoveryEvents.length}</div>
              <p className="mt-1 text-xs text-slate-500">queued/recovered</p>
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

          {/* Ongoing Tutorial Pipeline */}
          <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-200">
              {sectionHeading("Ongoing Tutorial Pipeline", activeTutorialRuns.length)}
            </h2>
            {activeTutorialRuns.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-500">No active tutorial runs detected.</p>
            ) : (
              <div className="space-y-2 max-h-[360px] overflow-y-auto">
                {activeTutorialRuns.map((run) => (
                  <div key={run.run_key} className="rounded-lg border border-white/[0.04] bg-white/[0.01] px-3 py-2.5">
                    <div className="flex items-center justify-between gap-2">
                      <p className="truncate text-xs font-medium text-slate-200">{run.title || run.video_id || run.run_key}</p>
                      <span className={`text-[10px] ${severityColor(run.severity)}`}>{stageLabel(run.stage)}</span>
                    </div>
                    <p className="mt-1 text-[11px] text-slate-400 line-clamp-2">{run.message || run.kind}</p>
                    <div className="mt-1 flex items-center gap-3 text-[10px] text-slate-500">
                      <span>{run.video_id ? `video:${run.video_id}` : run.kind}</span>
                      <span>{formatTime(run.created_at)}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* Actionable Alerts */}
          <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-200">
              {sectionHeading("Actionable Alerts", actionable.length)}
            </h2>
            {actionable.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-500">No actionable Telegram pipeline alerts.</p>
            ) : (
              <div className="space-y-2 max-h-[280px] overflow-y-auto">
                {actionable.map((n) => (
                  <div key={n.id} className="flex items-start gap-2.5 rounded-lg border border-amber-500/20 bg-amber-500/5 px-3 py-2">
                    {severityIcon(n.severity)}
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium text-slate-200 truncate">{n.title}</p>
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

          {/* Failures */}
          <section id="section-failures" className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 scroll-mt-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-200">
              {sectionHeading("Recent Failures & Warnings", failures.length)}
            </h2>
            {failures.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-500">No recent warning/error events.</p>
            ) : (
              <div className="space-y-2 max-h-[320px] overflow-y-auto">
                {failures.map((n) => (
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

          {/* Recently Resolved */}
          {resolvedFailures.length > 0 && (
            <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
              <h2 className="mb-3 text-sm font-semibold text-slate-200">
                {sectionHeading("Recently Resolved", resolvedFailures.length)}
              </h2>
              <div className="space-y-2 max-h-[260px] overflow-y-auto">
                {resolvedFailures.map((n) => {
                  const isAutoRecovery = n.resolution_type === "auto_recovery";
                  return (
                    <div
                      key={n.id}
                      className={`flex items-start gap-2.5 rounded-lg border px-3 py-2 ${
                        isAutoRecovery
                          ? "border-emerald-500/20 bg-emerald-500/5"
                          : "border-white/[0.04] bg-white/[0.01] opacity-60"
                      }`}
                    >
                      {isAutoRecovery ? (
                        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-400" />
                      ) : (
                        <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-500" />
                      )}
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2">
                          <p className={`text-xs font-medium truncate ${isAutoRecovery ? "text-slate-200" : "text-slate-400 line-through"}`}>
                            {n.title}
                          </p>
                          <span
                            className={`inline-flex items-center rounded-full px-1.5 py-0.5 text-[9px] font-semibold ${
                              isAutoRecovery
                                ? "bg-emerald-500/20 text-emerald-300"
                                : "bg-slate-600/30 text-slate-400"
                            }`}
                          >
                            {isAutoRecovery ? "Auto-recovered" : "Dismissed"}
                          </span>
                        </div>
                        <p className="mt-0.5 text-[11px] text-slate-400 line-clamp-2">{n.message}</p>
                        <div className="mt-1 flex items-center gap-3 text-[10px] text-slate-500">
                          <span>{n.kind}</span>
                          <span>{formatTime(n.updated_at || n.created_at)}</span>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            </section>
          )}

          {/* Recovery */}
          <section id="section-recovery" className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4 scroll-mt-4">
            <h2 className="mb-3 text-sm font-semibold text-slate-200">
              {sectionHeading("Recovery Events", recoveryEvents.length)}
            </h2>
            {recoveryEvents.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-500">No recovery events observed.</p>
            ) : (
              <div className="space-y-2 max-h-[260px] overflow-y-auto">
                {recoveryEvents.map((n) => (
                  <div key={n.id} className="flex items-start gap-2.5 rounded-lg border border-emerald-500/20 bg-emerald-500/5 px-3 py-2">
                    {severityIcon(n.severity)}
                    <div className="flex-1 min-w-0">
                      <p className="text-xs font-medium text-slate-200 truncate">{n.title}</p>
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

          {/* Recent Notification Activity */}
          <section className="rounded-xl border border-white/[0.06] bg-white/[0.02] p-4">
            <div className="mb-3 flex items-center justify-between gap-3">
              <h2 className="text-sm font-semibold text-slate-200">Recent Delivery Activity</h2>
              {pipelineActivity.length > 0 && (
                <button
                  type="button"
                  onClick={() => void deleteAllDeliveryActivity()}
                  disabled={deletingAllActivity}
                  className="rounded border border-rose-700/60 bg-rose-900/20 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-900/35 disabled:opacity-40"
                >
                  {deletingAllActivity ? "Deleting..." : "Delete All"}
                </button>
              )}
            </div>
            {pipelineActivity.length === 0 ? (
              <p className="py-6 text-center text-sm text-slate-500">No recent Telegram-related activity.</p>
            ) : (
              <div className="space-y-2 max-h-[400px] overflow-y-auto">
                {pipelineActivity.map((n) => (
                  <div key={n.id} className="group flex items-start gap-2.5 rounded-lg border border-white/[0.04] bg-white/[0.01] px-3 py-2">
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
                      {primaryActivityActions(n).length > 0 && (
                        <div className="mt-2 flex flex-wrap items-center gap-2">
                          {primaryActivityActions(n).map((action) => (
                            action.type === "link" && action.href ? (
                              <Link
                                key={`${n.id}:${action.id}`}
                                href={action.href}
                                className="rounded border border-cyan-700/60 bg-cyan-900/20 px-2 py-1 text-[11px] text-cyan-100 hover:bg-cyan-900/35"
                              >
                                {action.label}
                              </Link>
                            ) : (
                              <button
                                key={`${n.id}:${action.id}`}
                                type="button"
                                onClick={() => void handleActivityAction(n, action)}
                                className="rounded border border-cyan-700/60 bg-cyan-900/20 px-2 py-1 text-[11px] text-cyan-100 hover:bg-cyan-900/35"
                              >
                                {action.label}
                              </button>
                            )
                          ))}
                        </div>
                      )}
                    </div>
                    <button
                      type="button"
                      aria-label="Delete activity item"
                      onClick={() => void deleteActivityEvent(n.id)}
                      disabled={Boolean(deletingIds[n.id])}
                      className="opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 rounded p-1 text-rose-200 hover:bg-rose-900/30 disabled:opacity-40"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
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
