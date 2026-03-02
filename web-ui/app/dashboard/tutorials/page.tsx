"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { formatDateTimeTz, toEpochMs } from "@/lib/timezone";

const API_BASE = "/api/dashboard/gateway";

type TutorialFile = {
  label?: string;
  name?: string;
  rel_path?: string;
  storage_href?: string;
  api_url?: string;
};

type TutorialRun = {
  run_path: string;
  title?: string;
  status?: string;
  created_at?: string;
  video_url?: string;
  video_id?: string;
  run_storage_href?: string;
  files?: TutorialFile[];
  implementation_required?: boolean;
};

type TutorialReviewJob = {
  job_id: string;
  status?: string;
  queued_at?: string;
  completed_at?: string;
  title?: string;
  tutorial_run_path?: string;
  review_run_path?: string;
  session_id?: string;
};

type TutorialBootstrapJob = {
  job_id: string;
  status?: string;
  queued_at?: string;
  claimed_at?: string;
  completed_at?: string;
  tutorial_run_path?: string;
  repo_name?: string;
  target_root?: string;
  repo_dir?: string;
  repo_open_uri?: string;
  repo_open_hint?: string;
  worker_id?: string;
  error?: string;
};

type PipelineNotification = {
  id: string;
  kind: string;
  title: string;
  message: string;
  severity: string;
  created_at: string;
  metadata?: Record<string, unknown>;
};

type WatcherStatus = {
  enabled: boolean;
  playlist_id?: string;
  poll_interval_seconds?: number;
  last_poll_at?: string;
  last_poll_ok?: boolean;
  last_error?: string;
  seen_count?: number;
  dispatched_total?: number;
  poll_count?: number;
  reason?: string;
  telegram?: { configured: boolean; bot_token_set: boolean; chat_id_set: boolean };
};

function asText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function encodePath(path: string): string {
  return path
    .split("/")
    .map((segment) => encodeURIComponent(segment))
    .join("/");
}

function toFileUri(path: string): string {
  const normalized = asText(path);
  if (!normalized) return "";
  if (normalized.startsWith("file://")) return normalized;
  return `file://${encodeURI(normalized)}`;
}

function formatDate(value?: string): string {
  return formatDateTimeTz(value, { placeholder: value || "--" });
}

function chatSessionHref(sessionId?: string): string {
  const sid = asText(sessionId);
  if (!sid) return "";
  const params = new URLSearchParams({
    session_id: sid,
    attach: "tail",
    role: "viewer",
  });
  return `/?${params.toString()}`;
}

function timeAgo(dateStr: string): string {
  const ts = toEpochMs(dateStr);
  if (ts === null) return "--";
  const delta = Math.max(0, (Date.now() - ts) / 1000);
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

async function fetchWithTimeout(input: RequestInfo | URL, init: RequestInit, timeoutMs = 12000): Promise<Response> {
  const controller = new AbortController();
  const timeoutHandle = window.setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(input, { ...init, signal: controller.signal });
  } finally {
    window.clearTimeout(timeoutHandle);
  }
}

const KIND_EMOJI: Record<string, string> = {
  youtube_playlist_new_video: "🎬",
  youtube_playlist_dispatch_failed: "⚠️",
  youtube_tutorial_started: "▶️",
  youtube_tutorial_progress: "⏳",
  youtube_tutorial_ready: "✅",
  youtube_tutorial_failed: "❌",
  youtube_ingest_failed: "❌",
  hook_dispatch_queue_overflow: "⚠️",
  youtube_hook_recovery_queued: "🔁",
  tutorial_review_ready: "📋",
  tutorial_review_failed: "❌",
};

const SEVERITY_STYLES: Record<string, string> = {
  success: "border-emerald-600/50 bg-emerald-900/20 text-emerald-200",
  error: "border-rose-600/50 bg-rose-900/20 text-rose-200",
  warning: "border-amber-600/50 bg-amber-900/20 text-amber-200",
  info: "border-sky-600/50 bg-sky-900/20 text-sky-200",
};

const SEVERITY_DOTS: Record<string, string> = {
  success: "bg-emerald-400",
  error: "bg-rose-400",
  warning: "bg-amber-400",
  info: "bg-sky-400",
};

// ─── localStorage helpers for NEW badges ───────────────────
const SEEN_KEY = "ua_tutorials_seen_runs";

function getSeenRuns(): Set<string> {
  try {
    const raw = localStorage.getItem(SEEN_KEY);
    return raw ? new Set(JSON.parse(raw)) : new Set();
  } catch {
    return new Set();
  }
}

function markRunsSeen(runPaths: string[]) {
  try {
    const seen = getSeenRuns();
    for (const p of runPaths) seen.add(p);
    localStorage.setItem(SEEN_KEY, JSON.stringify([...seen]));
  } catch { }
}

export default function DashboardTutorialsPage() {
  const [runs, setRuns] = useState<TutorialRun[]>([]);
  const [jobs, setJobs] = useState<TutorialReviewJob[]>([]);
  const [bootstrapJobs, setBootstrapJobs] = useState<TutorialBootstrapJob[]>([]);
  const [notifications, setNotifications] = useState<PipelineNotification[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [dispatchingRunPath, setDispatchingRunPath] = useState<string>("");
  const [dispatchStatus, setDispatchStatus] = useState<string>("");
  const [deletingRunPath, setDeletingRunPath] = useState<string>("");
  const [deletingAllRuns, setDeletingAllRuns] = useState(false);
  const [clearingNotifications, setClearingNotifications] = useState(false);
  const [dismissingNotificationId, setDismissingNotificationId] = useState<string>("");
  const [bootstrappingRunPath, setBootstrappingRunPath] = useState<string>("");
  const [seenRuns, setSeenRuns] = useState<Set<string>>(new Set());
  const [showNotifications, setShowNotifications] = useState(true);
  const [watcherStatus, setWatcherStatus] = useState<WatcherStatus | null>(null);
  const [pollingNow, setPollingNow] = useState(false);
  const [pollResult, setPollResult] = useState<string>("");

  // Load seen runs from localStorage on mount
  useEffect(() => {
    setSeenRuns(getSeenRuns());
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [runsRes, jobsRes, bootstrapRes, notifRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/dashboard/tutorials/runs?limit=120`),
        fetch(`${API_BASE}/api/v1/dashboard/tutorials/review-jobs?limit=120`),
        fetch(`${API_BASE}/api/v1/dashboard/tutorials/bootstrap-jobs?limit=120`),
        fetch(`${API_BASE}/api/v1/dashboard/tutorials/notifications?limit=20`),
      ]);
      const runsPayload = runsRes.ok ? await runsRes.json() : { runs: [] };
      const jobsPayload = jobsRes.ok ? await jobsRes.json() : { jobs: [] };
      const bootstrapPayload = bootstrapRes.ok ? await bootstrapRes.json() : { jobs: [] };
      const notifPayload = notifRes.ok ? await notifRes.json() : { notifications: [] };
      setRuns(Array.isArray(runsPayload.runs) ? (runsPayload.runs as TutorialRun[]) : []);
      setJobs(Array.isArray(jobsPayload.jobs) ? (jobsPayload.jobs as TutorialReviewJob[]) : []);
      setBootstrapJobs(
        Array.isArray(bootstrapPayload.jobs)
          ? (bootstrapPayload.jobs as TutorialBootstrapJob[])
          : [],
      );
      setNotifications(
        Array.isArray(notifPayload.notifications)
          ? (notifPayload.notifications as PipelineNotification[])
          : [],
      );
    } catch (err: any) {
      setError(err?.message || "Failed to load tutorial backlog");
      setRuns([]);
      setJobs([]);
      setBootstrapJobs([]);
      setNotifications([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const fetchWatcherStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/youtube-playlist-watcher`);
      if (res.ok) setWatcherStatus(await res.json() as WatcherStatus);
    } catch { }
  }, []);

  useEffect(() => { void fetchWatcherStatus(); }, [fetchWatcherStatus]);

  const pollNow = useCallback(async () => {
    setPollingNow(true);
    setPollResult("");
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/youtube-playlist-watcher/poll`, { method: "POST" });
      const data = await res.json().catch(() => ({})) as Record<string, unknown>;
      if (!res.ok) {
        setPollResult(`Poll failed: ${asText(data.detail) || res.status}`);
      } else {
        const n = Number(data.new_dispatched ?? 0);
        setPollResult(n > 0 ? `Dispatched ${n} new video${n !== 1 ? "s" : ""}` : "No new videos found");
      }
      await fetchWatcherStatus();
    } catch (err: unknown) {
      setPollResult(`Poll error: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setPollingNow(false);
    }
  }, [fetchWatcherStatus]);

  // Auto-refresh notifications and watcher status every 30s
  useEffect(() => {
    const hasActiveBootstrapJobs = bootstrapJobs.some((job) => {
      const status = asText(job.status).toLowerCase();
      return status === "queued" || status === "running";
    });
    const refreshMs = hasActiveBootstrapJobs ? 5_000 : 30_000;
    const interval = setInterval(async () => {
      try {
        const [notifRes, bootstrapRes] = await Promise.all([
          fetch(`${API_BASE}/api/v1/dashboard/tutorials/notifications?limit=20`),
          fetch(`${API_BASE}/api/v1/dashboard/tutorials/bootstrap-jobs?limit=120`),
        ]);
        if (notifRes.ok) {
          const data = await notifRes.json();
          if (Array.isArray(data.notifications)) {
            setNotifications(data.notifications as PipelineNotification[]);
          }
        }
        if (bootstrapRes.ok) {
          const data = await bootstrapRes.json();
          if (Array.isArray(data.jobs)) {
            setBootstrapJobs(data.jobs as TutorialBootstrapJob[]);
          }
        }
      } catch { }
      void fetchWatcherStatus();
    }, refreshMs);
    return () => clearInterval(interval);
  }, [bootstrapJobs, fetchWatcherStatus]);

  // Mark currently visible runs as seen after initial load
  useEffect(() => {
    if (!loading && runs.length > 0) {
      // Defer marking to next tick so badge shows briefly
      const timer = setTimeout(() => {
        markRunsSeen(runs.map((r) => r.run_path));
        setSeenRuns(getSeenRuns());
      }, 3000);
      return () => clearTimeout(timer);
    }
  }, [loading, runs]);

  const latestJobByRun = useMemo(() => {
    const map = new Map<string, TutorialReviewJob>();
    for (const job of jobs) {
      const runPath = asText(job.tutorial_run_path);
      if (!runPath || map.has(runPath)) continue;
      map.set(runPath, job);
    }
    return map;
  }, [jobs]);

  const latestBootstrapByRun = useMemo(() => {
    const map = new Map<string, TutorialBootstrapJob>();
    for (const job of bootstrapJobs) {
      const runPath = asText(job.tutorial_run_path);
      if (!runPath || map.has(runPath)) continue;
      map.set(runPath, job);
    }
    return map;
  }, [bootstrapJobs]);

  // Hides notifications for videos that have successfully completed processing
  // (so they don't clog up the notification area once the artifact is visible)
  const visibleNotifications = useMemo(() => {
    const completedVideoIds = new Set(
      runs
        .filter((r) => r.status === "full" || r.status === "degraded_transcript_only")
        .map((r) => asText(r.video_id))
        .filter(Boolean)
    );
    return notifications.filter((n) => {
      const vid = asText(n.metadata?.video_id);
      return !vid || !completedVideoIds.has(vid);
    });
  }, [runs, notifications]);

  const dispatchToSimone = useCallback(
    async (runPath: string) => {
      const normalized = asText(runPath);
      if (!normalized) return;
      setDispatchingRunPath(normalized);
      setDispatchStatus("");
      try {
        const res = await fetch(`${API_BASE}/api/v1/dashboard/tutorials/review`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ run_path: normalized }),
        });
        const payload = await res.json().catch(() => ({}));
        if (!res.ok) {
          const detail = asText((payload as Record<string, unknown>).detail) || `Dispatch failed (${res.status})`;
          throw new Error(detail);
        }
        setDispatchStatus(`Queued Simone review for ${normalized}`);
        await load();
      } catch (err: any) {
        setDispatchStatus(err?.message || "Failed to queue review");
      } finally {
        setDispatchingRunPath("");
      }
    },
    [load],
  );

  const deleteRun = useCallback(
    async (runPath: string) => {
      const normalized = asText(runPath);
      if (!normalized) return;
      if (!window.confirm(`Delete this tutorial run?\n${normalized}\n\nThis cannot be undone.`)) return;
      setDeletingRunPath(normalized);
      try {
        const res = await fetch(
          `${API_BASE}/api/v1/dashboard/tutorials/runs?run_path=${encodeURIComponent(normalized)}`,
          { method: "DELETE" },
        );
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          const detail = asText((payload as Record<string, unknown>).detail) || `Delete failed (${res.status})`;
          throw new Error(detail);
        }
        await load();
      } catch (err: any) {
        setDispatchStatus(err?.message || "Failed to delete run");
      } finally {
        setDeletingRunPath("");
      }
    },
    [load],
  );

  const dismissNotification = useCallback(
    async (notificationId: string) => {
      const normalized = asText(notificationId);
      if (!normalized) return;
      setDismissingNotificationId(normalized);
      setNotifications((prev) => prev.filter((item) => asText(item.id) !== normalized));
      try {
        const res = await fetchWithTimeout(
          `${API_BASE}/api/v1/dashboard/notifications/${encodeURIComponent(normalized)}`,
          {
            method: "PATCH",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ status: "dismissed", note: "deleted in tutorials panel" }),
          },
        );
        if (!res.ok) {
          const payload = await res.json().catch(() => ({}));
          const detail = asText((payload as Record<string, unknown>).detail) || `Delete failed (${res.status})`;
          throw new Error(detail);
        }
        await load();
      } catch (err: any) {
        setDispatchStatus(err?.message || "Failed to delete notification");
      } finally {
        setDismissingNotificationId("");
      }
    },
    [load],
  );

  const clearAllNotifications = useCallback(
    async () => {
      const targetCount = visibleNotifications.length;
      if (targetCount === 0) return;
      if (!window.confirm(`Delete all ${targetCount} pipeline notification${targetCount > 1 ? "s" : ""}?`)) return;
      setClearingNotifications(true);
      const targetIds = new Set(visibleNotifications.map((n) => asText(n.id)).filter(Boolean));
      setNotifications((prev) => prev.filter((item) => !targetIds.has(asText(item.id))));
      try {
        const uniqueKinds = Array.from(
          new Set(visibleNotifications.map((n) => asText(n.kind)).filter(Boolean)),
        );
        if (uniqueKinds.length === 0) return;
        const results = await Promise.allSettled(
          uniqueKinds.map((kind) =>
            fetchWithTimeout(
              `${API_BASE}/api/v1/dashboard/notifications/bulk`,
              {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                  status: "dismissed",
                  kind,
                  limit: 1000,
                  note: "deleted in tutorials panel bulk action",
                }),
              },
            ),
          ),
        );
        const failed = results.filter(
          (result) => result.status === "rejected" || (result.status === "fulfilled" && !result.value.ok),
        ).length;
        if (failed > 0) {
          setDispatchStatus(
            `Deleted with partial failure (${failed}/${results.length} kind request${results.length === 1 ? "" : "s"} failed).`,
          );
        }
        await load();
      } catch (err: any) {
        setDispatchStatus(err?.message || "Failed to clear notifications");
        await load();
      } finally {
        setClearingNotifications(false);
      }
    },
    [load, visibleNotifications],
  );

  const deleteAllRuns = useCallback(
    async () => {
      const targetCount = runs.length;
      if (targetCount === 0) return;
      if (!window.confirm(`Delete all ${targetCount} processed tutorial run${targetCount > 1 ? "s" : ""}?\n\nThis cannot be undone.`)) {
        return;
      }
      setDeletingAllRuns(true);
      try {
        const results = await Promise.allSettled(
          runs.map((run) =>
            fetch(
              `${API_BASE}/api/v1/dashboard/tutorials/runs?run_path=${encodeURIComponent(asText(run.run_path))}`,
              { method: "DELETE" },
            ),
          ),
        );
        const failed = results.filter(
          (result) => result.status === "rejected" || (result.status === "fulfilled" && !result.value.ok),
        ).length;
        if (failed > 0) {
          setDispatchStatus(`Deleted ${targetCount - failed}/${targetCount} runs. ${failed} failed.`);
        }
        await load();
      } catch (err: any) {
        setDispatchStatus(err?.message || "Failed to delete all runs");
      } finally {
        setDeletingAllRuns(false);
      }
    },
    [load, runs],
  );

  const bootstrapRunRepo = useCallback(
    async (runPath: string) => {
      const normalized = asText(runPath);
      if (!normalized) return;
      setBootstrappingRunPath(normalized);
      setDispatchStatus("");
      try {
        const res = await fetch(`${API_BASE}/api/v1/dashboard/tutorials/bootstrap-repo`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ run_path: normalized, execution_target: "local" }),
        });
        const payload = await res.json().catch(() => ({} as Record<string, unknown>));
        if (!res.ok) {
          const detail = asText((payload as Record<string, unknown>).detail) || `Create repo failed (${res.status})`;
          throw new Error(detail);
        }
        const queued = Boolean((payload as Record<string, unknown>).queued);
        if (queued) {
          const jobId = asText((payload as Record<string, unknown>).job_id);
          const reused = Boolean((payload as Record<string, unknown>).existing_job_reused);
          setDispatchStatus(
            reused
              ? (jobId
                ? `Job ${jobId} is already in progress. Local worker will continue execution.`
                : "A repo creation job is already in progress.")
              : (jobId
                ? `Queued local repo creation job ${jobId}. Run the local bootstrap worker to execute it.`
                : "Queued local repo creation job."),
          );
          await load();
          return;
        }
        const repoDir = asText((payload as Record<string, unknown>).repo_dir);
        setDispatchStatus(repoDir ? `Repo created and synced: ${repoDir}` : "Repo created and synced.");
      } catch (err: any) {
        setDispatchStatus(err?.message || "Failed to create repo");
      } finally {
        setBootstrappingRunPath("");
      }
    },
    [load],
  );

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Tutorial Backlog</h1>
          <p className="text-sm text-slate-400">
            Review generated YouTube tutorial artifacts and optionally send them to Simone for fit analysis.
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          className="rounded border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs uppercase tracking-wider text-slate-200 hover:bg-slate-800/80"
        >
          {loading ? "Refreshing..." : "Refresh"}
        </button>
      </div>

      {/* ── Playlist Watcher Status ── */}
      {watcherStatus !== null && (
        <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <span className="text-sm font-semibold text-slate-300">📺 Playlist Watcher</span>
              {watcherStatus.enabled ? (
                <span className="rounded bg-emerald-900/60 border border-emerald-700/50 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-emerald-300">Active</span>
              ) : (
                <span className="rounded bg-slate-800 border border-slate-700/50 px-1.5 py-0.5 text-[10px] font-semibold uppercase text-slate-400">
                  {watcherStatus.reason === "not_initialized" ? "Not Initialized" : "Disabled"}
                </span>
              )}
              {watcherStatus.telegram?.configured && (
                <span title="Telegram notifications configured" className="rounded bg-sky-900/50 border border-sky-700/40 px-1.5 py-0.5 text-[10px] text-sky-300">✈️ Telegram</span>
              )}
            </div>
            <div className="flex items-center gap-2">
              {pollResult && (
                <span className={`text-[11px] ${pollResult.startsWith("Poll") ? "text-rose-300" : "text-emerald-300"}`}>
                  {pollResult}
                </span>
              )}
              {watcherStatus.enabled && (
                <button
                  type="button"
                  onClick={() => void pollNow()}
                  disabled={pollingNow}
                  className="rounded border border-sky-700/60 bg-sky-900/25 px-2 py-1 text-[11px] text-sky-100 hover:bg-sky-900/40 disabled:opacity-50"
                >
                  {pollingNow ? "Polling..." : "Poll Now"}
                </button>
              )}
            </div>
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-400">
            {watcherStatus.playlist_id && (
              <span>Playlist: <code className="text-slate-300">{watcherStatus.playlist_id}</code></span>
            )}
            {watcherStatus.poll_interval_seconds !== undefined && (
              <span>Interval: {watcherStatus.poll_interval_seconds}s</span>
            )}
            {watcherStatus.last_poll_at && (
              <span>
                Last poll: <span className={watcherStatus.last_poll_ok === false ? "text-rose-300" : "text-slate-300"}>
                  {timeAgo(watcherStatus.last_poll_at)}
                  {watcherStatus.last_poll_ok === false && " ✗"}
                </span>
              </span>
            )}
            {watcherStatus.seen_count !== undefined && (
              <span>Seen videos: {watcherStatus.seen_count}</span>
            )}
            {watcherStatus.dispatched_total !== undefined && (
              <span>Dispatched total: {watcherStatus.dispatched_total}</span>
            )}
            {watcherStatus.last_error && (
              <span className="text-rose-300" title={watcherStatus.last_error}>Error: {watcherStatus.last_error.slice(0, 60)}</span>
            )}
          </div>
        </section>
      )}

      {/* ── Pipeline Notifications ── */}
      {visibleNotifications.length > 0 && (
        <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">
              Pipeline Activity ({visibleNotifications.length})
            </h2>
            <div className="flex items-center gap-2">
              <button
                type="button"
                onClick={() => void clearAllNotifications()}
                disabled={clearingNotifications || visibleNotifications.length === 0}
                className="rounded border border-rose-700/60 bg-rose-900/20 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-900/35 disabled:opacity-50"
              >
                {clearingNotifications ? "Deleting..." : "Delete All"}
              </button>
              <button
                type="button"
                onClick={() => setShowNotifications((v) => !v)}
                className="text-[11px] text-slate-500 hover:text-slate-300"
              >
                {showNotifications ? "Hide" : "Show"}
              </button>
            </div>
          </div>
          {showNotifications && (
            <div className="space-y-1.5">
              {visibleNotifications.slice(0, 8).map((n) => {
                const style = SEVERITY_STYLES[n.severity] || SEVERITY_STYLES.info;
                const dot = SEVERITY_DOTS[n.severity] || SEVERITY_DOTS.info;
                const kindEmoji = KIND_EMOJI[n.kind];
                const videoUrl = typeof n.metadata?.video_url === "string" ? n.metadata.video_url : "";
                return (
                  <div
                    key={n.id}
                    className={`group flex items-start gap-2 rounded border px-2.5 py-1.5 text-xs ${style}`}
                  >
                    {kindEmoji ? (
                      <span className="mt-0.5 shrink-0 text-sm leading-none" aria-hidden="true">{kindEmoji}</span>
                    ) : (
                      <span className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
                    )}
                    <div className="min-w-0 flex-1">
                      <span className="font-medium">{n.title}</span>
                      <span className="ml-1.5 text-[10px] opacity-70">{timeAgo(n.created_at)}</span>
                      {n.message && n.message !== n.title && (
                        <p className="mt-0.5 truncate opacity-80">{n.message}</p>
                      )}
                      {videoUrl && (
                        <a href={videoUrl} target="_blank" rel="noopener noreferrer" className="mt-0.5 block text-[10px] text-cyan-300 underline underline-offset-2 opacity-80 hover:opacity-100">Watch video →</a>
                      )}
                    </div>
                    <button
                      type="button"
                      onClick={() => void dismissNotification(n.id)}
                      disabled={dismissingNotificationId === n.id || clearingNotifications}
                      title="Delete notification"
                      aria-label="Delete notification"
                      className="opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 rounded p-1 text-rose-200 hover:bg-rose-900/30 disabled:opacity-40"
                    >
                      <span aria-hidden="true">🗑</span>
                    </button>
                  </div>
                );
              })}
            </div>
          )}
        </section>
      )}

      {dispatchStatus && (
        <div className="rounded border border-cyan-700/60 bg-cyan-900/20 px-3 py-2 text-sm text-cyan-100">
          {dispatchStatus}
        </div>
      )}
      {error && (
        <div className="rounded border border-rose-700/60 bg-rose-900/20 px-3 py-2 text-sm text-rose-100">
          {error}
        </div>
      )}

      <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
        <div className="mb-3 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold uppercase tracking-[0.16em] text-slate-300">
            Processed Tutorial Runs ({runs.length})
          </h2>
          <button
            type="button"
            onClick={() => void deleteAllRuns()}
            disabled={deletingAllRuns || runs.length === 0}
            className="rounded border border-rose-700/60 bg-rose-900/20 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-900/35 disabled:opacity-50"
          >
            {deletingAllRuns ? "Deleting..." : "Delete All"}
          </button>
        </div>
        <div className="space-y-3">
          {runs.length === 0 && (
            <div className="rounded border border-slate-800 bg-slate-950/50 px-3 py-4 text-sm text-slate-400">
              No tutorial runs found yet.
            </div>
          )}
          {runs.map((run) => {
            const runPath = asText(run.run_path);
            const files = Array.isArray(run.files) ? run.files : [];
            const latestJob = latestJobByRun.get(runPath);
            const latestJobStatus = asText(latestJob?.status).toLowerCase();
            const latestBootstrapJob = latestBootstrapByRun.get(runPath);
            const latestBootstrapStatus = asText(latestBootstrapJob?.status).toLowerCase();
            const bootstrapPending = latestBootstrapStatus === "queued" || latestBootstrapStatus === "running";
            const sessionHref = chatSessionHref(asText(latestJob?.session_id));
            const viewHref =
              asText(run.run_storage_href) ||
              `/storage?scope=artifacts&path=${encodeURIComponent(runPath)}`;
            const isNew = !seenRuns.has(runPath);
            const implRequired = run.implementation_required;
            const hasCreateRepoScript = files.some((file) => asText(file.name).toLowerCase() === "create_new_repo.sh");
            const showCreateRepoAction = Boolean(implRequired || hasCreateRepoScript);
            return (
              <article key={runPath} className="group rounded-lg border border-slate-800/80 bg-slate-950/60 px-3 py-2">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold text-slate-100">
                      {asText(run.title) || runPath}
                      {isNew && (
                        <span className="ml-2 inline-block rounded bg-cyan-500/90 px-1.5 py-0.5 text-[10px] font-bold uppercase leading-none text-slate-950">
                          NEW
                        </span>
                      )}
                    </p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-2 text-[11px] text-slate-400">
                      <span>created={formatDate(run.created_at)}</span>
                      {implRequired !== undefined && (
                        <>
                          <span>·</span>
                          <span
                            className={
                              implRequired
                                ? "text-violet-300"
                                : "text-slate-500"
                            }
                          >
                            {implRequired ? "🔧 Code Implementation" : "📝 Concept Only"}
                          </span>
                        </>
                      )}
                    </p>
                    {asText(run.video_url) && (
                      <a
                        href={asText(run.video_url)}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="mt-1 inline-block text-xs text-cyan-300 underline underline-offset-2"
                      >
                        Open Video
                      </a>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-1.5">
                    <Link
                      href={viewHref}
                      className="rounded border border-cyan-700/60 bg-cyan-900/25 px-2 py-1 text-[11px] text-cyan-100 hover:bg-cyan-900/40"
                    >
                      View Results
                    </Link>
                    {sessionHref && (
                      <a
                        href={sessionHref}
                        target="_blank"
                        rel="noopener noreferrer"
                        className="rounded border border-violet-700/60 bg-violet-900/20 px-2 py-1 text-[11px] text-violet-100 hover:bg-violet-900/35"
                      >
                        {latestJobStatus === "running" ? "Watch" : "Rehydrate"}
                      </a>
                    )}
                    <button
                      type="button"
                      onClick={() => void dispatchToSimone(runPath)}
                      disabled={dispatchingRunPath === runPath || deletingRunPath === runPath || bootstrappingRunPath === runPath}
                      className="rounded border border-emerald-700/60 bg-emerald-900/20 px-2 py-1 text-[11px] text-emerald-100 hover:bg-emerald-900/35 disabled:opacity-50"
                    >
                      {dispatchingRunPath === runPath ? "Queueing..." : "Send to Simone"}
                    </button>
                    {latestBootstrapStatus === "completed" || latestBootstrapStatus === "success" ? (
                      <div className="flex items-center gap-1.5">
                        <span className="rounded border border-emerald-700/60 bg-emerald-900/40 px-2 py-1 text-[11px] text-emerald-100">
                          Repo Ready
                        </span>
                        {toFileUri(asText(latestBootstrapJob?.repo_open_uri) || asText(latestBootstrapJob?.repo_dir)) && (
                          <a
                            href={toFileUri(asText(latestBootstrapJob?.repo_open_uri) || asText(latestBootstrapJob?.repo_dir))}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="rounded border border-cyan-700/60 bg-cyan-900/25 px-2 py-1 text-[11px] text-cyan-100 hover:bg-cyan-900/40"
                            title={asText(latestBootstrapJob?.repo_open_hint) || "Open local repository folder"}
                          >
                            Open Folder
                          </a>
                        )}
                        {asText(latestBootstrapJob?.repo_dir) && (
                          <div className="group relative flex items-center">
                            <span
                              className="cursor-text select-all rounded bg-slate-800/80 border border-slate-700/50 px-1.5 py-0.5 text-[10px] font-mono text-slate-300"
                              title="Copy this path to your terminal"
                            >
                              {asText(latestBootstrapJob!.repo_dir)}
                            </span>
                          </div>
                        )}
                      </div>
                    ) : showCreateRepoAction && (
                      <button
                        type="button"
                        onClick={() => void bootstrapRunRepo(runPath)}
                        disabled={
                          bootstrappingRunPath === runPath
                          || deletingRunPath === runPath
                          || dispatchingRunPath === runPath
                          || bootstrapPending
                        }
                        className={`rounded border px-2 py-1 text-[11px] transition-colors disabled:opacity-50 ${bootstrapPending
                            ? "border-amber-500 bg-amber-900/60 text-amber-200 animate-pulse shadow-[0_0_10px_rgba(245,158,11,0.2)]"
                            : "border-amber-700/60 bg-amber-900/20 text-amber-100 hover:bg-amber-900/35"
                          }`}
                        title={
                          hasCreateRepoScript
                            ? "Queue local repo creation using implementation/create_new_repo.sh via desktop worker"
                            : "Create repo action (run may need bootstrap script regeneration first)"
                        }
                      >
                        {bootstrappingRunPath === runPath
                          ? "Queueing..."
                          : bootstrapPending
                            ? (latestBootstrapStatus === "running" ? "Creating (Local Worker)..." : "Queued (Waiting on Worker)")
                            : "Create Repo"}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => void deleteRun(runPath)}
                      disabled={deletingRunPath === runPath || dispatchingRunPath === runPath || bootstrappingRunPath === runPath || deletingAllRuns}
                      title="Delete this tutorial run"
                      aria-label="Delete this tutorial run"
                      className="opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100 rounded border border-rose-700/60 bg-rose-900/20 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-900/35 disabled:opacity-40"
                    >
                      {deletingRunPath === runPath ? "Deleting..." : "🗑"}
                    </button>
                  </div>
                </div>
                {files.length > 0 && (
                  <div className="mt-1.5 grid gap-1 sm:grid-cols-2">
                    {files.map((file, index) => {
                      const relPath = asText(file.rel_path);
                      if (!relPath) return null;
                      const label = asText(file.label) || asText(file.name) || relPath;
                      const storageHref = asText(file.storage_href);
                      const apiHref = asText(file.api_url) || `${API_BASE}/api/artifacts/files/${encodePath(relPath)}`;
                      const href = storageHref || apiHref;
                      return (
                        <a
                          key={`${runPath}-${relPath}-${index}`}
                          href={href}
                          target="_blank"
                          rel="noopener noreferrer"
                          className="truncate rounded border border-slate-800 bg-slate-900/60 px-2 py-1 text-[11px] text-cyan-300 hover:bg-slate-800/70"
                          title={relPath}
                        >
                          {label}
                        </a>
                      );
                    })}
                  </div>
                )}
                {latestJob && (
                  <p className="mt-1.5 text-[11px] text-slate-400">
                    Latest Simone review job: {asText(latestJob.status) || "unknown"} ({formatDate(latestJob.queued_at)})
                  </p>
                )}
                {latestBootstrapJob && (
                  <p
                    className={`mt-1 text-[11px] ${asText(latestBootstrapJob.error) ? "text-rose-300" : "text-slate-400"
                      }`}
                    title={asText(latestBootstrapJob.error)}
                  >
                    Local repo bootstrap: {asText(latestBootstrapJob.status) || "unknown"} (
                    {formatDate(
                      asText(latestBootstrapJob.completed_at)
                      || asText(latestBootstrapJob.claimed_at)
                      || asText(latestBootstrapJob.queued_at),
                    )}
                    )
                    {asText(latestBootstrapJob.repo_dir) && (
                      <> · {asText(latestBootstrapJob.repo_dir)}</>
                    )}
                    {asText(latestBootstrapJob.error) && (
                      <> · {asText(latestBootstrapJob.error)}</>
                    )}
                  </p>
                )}
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}
