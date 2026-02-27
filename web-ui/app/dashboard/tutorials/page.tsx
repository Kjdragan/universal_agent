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

type PipelineNotification = {
  id: string;
  kind: string;
  title: string;
  message: string;
  severity: string;
  created_at: string;
  metadata?: Record<string, unknown>;
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
  const [notifications, setNotifications] = useState<PipelineNotification[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [dispatchingRunPath, setDispatchingRunPath] = useState<string>("");
  const [dispatchStatus, setDispatchStatus] = useState<string>("");
  const [deletingRunPath, setDeletingRunPath] = useState<string>("");
  const [bootstrappingRunPath, setBootstrappingRunPath] = useState<string>("");
  const [seenRuns, setSeenRuns] = useState<Set<string>>(new Set());
  const [showNotifications, setShowNotifications] = useState(true);

  // Load seen runs from localStorage on mount
  useEffect(() => {
    setSeenRuns(getSeenRuns());
  }, []);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [runsRes, jobsRes, notifRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/dashboard/tutorials/runs?limit=120`),
        fetch(`${API_BASE}/api/v1/dashboard/tutorials/review-jobs?limit=120`),
        fetch(`${API_BASE}/api/v1/dashboard/tutorials/notifications?limit=20`),
      ]);
      const runsPayload = runsRes.ok ? await runsRes.json() : { runs: [] };
      const jobsPayload = jobsRes.ok ? await jobsRes.json() : { jobs: [] };
      const notifPayload = notifRes.ok ? await notifRes.json() : { notifications: [] };
      setRuns(Array.isArray(runsPayload.runs) ? (runsPayload.runs as TutorialRun[]) : []);
      setJobs(Array.isArray(jobsPayload.jobs) ? (jobsPayload.jobs as TutorialReviewJob[]) : []);
      setNotifications(
        Array.isArray(notifPayload.notifications)
          ? (notifPayload.notifications as PipelineNotification[])
          : [],
      );
    } catch (err: any) {
      setError(err?.message || "Failed to load tutorial backlog");
      setRuns([]);
      setJobs([]);
      setNotifications([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  // Auto-refresh notifications every 30s
  useEffect(() => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/api/v1/dashboard/tutorials/notifications?limit=20`);
        if (res.ok) {
          const data = await res.json();
          if (Array.isArray(data.notifications)) {
            setNotifications(data.notifications as PipelineNotification[]);
          }
        }
      } catch { }
    }, 30_000);
    return () => clearInterval(interval);
  }, []);

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
          body: JSON.stringify({ run_path: normalized }),
        });
        const payload = await res.json().catch(() => ({} as Record<string, unknown>));
        if (!res.ok) {
          const detail = asText((payload as Record<string, unknown>).detail) || `Create repo failed (${res.status})`;
          throw new Error(detail);
        }
        const repoDir = asText((payload as Record<string, unknown>).repo_dir);
        setDispatchStatus(repoDir ? `Repo created and synced: ${repoDir}` : "Repo created and synced.");
      } catch (err: any) {
        setDispatchStatus(err?.message || "Failed to create repo");
      } finally {
        setBootstrappingRunPath("");
      }
    },
    [],
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

      {/* ── Pipeline Notifications ── */}
      {visibleNotifications.length > 0 && (
        <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-3">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-[0.16em] text-slate-400">
              Pipeline Activity ({visibleNotifications.length})
            </h2>
            <button
              type="button"
              onClick={() => setShowNotifications((v) => !v)}
              className="text-[11px] text-slate-500 hover:text-slate-300"
            >
              {showNotifications ? "Hide" : "Show"}
            </button>
          </div>
          {showNotifications && (
            <div className="space-y-1.5">
              {visibleNotifications.slice(0, 8).map((n) => {
                const style = SEVERITY_STYLES[n.severity] || SEVERITY_STYLES.info;
                const dot = SEVERITY_DOTS[n.severity] || SEVERITY_DOTS.info;
                return (
                  <div
                    key={n.id}
                    className={`flex items-start gap-2 rounded border px-2.5 py-1.5 text-xs ${style}`}
                  >
                    <span className={`mt-1 h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
                    <div className="min-w-0 flex-1">
                      <span className="font-medium">{n.title}</span>
                      <span className="ml-1.5 text-[10px] opacity-70">{timeAgo(n.created_at)}</span>
                      {n.message && n.message !== n.title && (
                        <p className="mt-0.5 truncate opacity-80">{n.message}</p>
                      )}
                    </div>
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
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-[0.16em] text-slate-300">
          Processed Tutorial Runs ({runs.length})
        </h2>
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
            const sessionHref = chatSessionHref(asText(latestJob?.session_id));
            const viewHref =
              asText(run.run_storage_href) ||
              `/storage?scope=artifacts&path=${encodeURIComponent(runPath)}`;
            const isNew = !seenRuns.has(runPath);
            const implRequired = run.implementation_required;
            const hasCreateRepoScript = files.some((file) => asText(file.name).toLowerCase() === "create_new_repo.sh");
            const showCreateRepoAction = Boolean(implRequired || hasCreateRepoScript);
            return (
              <article key={runPath} className="rounded-lg border border-slate-800/80 bg-slate-950/60 px-3 py-2">
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
                    {showCreateRepoAction && (
                      <button
                        type="button"
                        onClick={() => void bootstrapRunRepo(runPath)}
                        disabled={bootstrappingRunPath === runPath || deletingRunPath === runPath || dispatchingRunPath === runPath}
                        className="rounded border border-amber-700/60 bg-amber-900/20 px-2 py-1 text-[11px] text-amber-100 hover:bg-amber-900/35 disabled:opacity-50"
                        title={
                          hasCreateRepoScript
                            ? "Create a ready-to-run repo by executing create_new_repo.sh on the server"
                            : "Create repo action (run may need bootstrap script regeneration first)"
                        }
                      >
                        {bootstrappingRunPath === runPath ? "Creating Repo..." : "Create Repo"}
                      </button>
                    )}
                    <button
                      type="button"
                      onClick={() => void deleteRun(runPath)}
                      disabled={deletingRunPath === runPath || dispatchingRunPath === runPath || bootstrappingRunPath === runPath}
                      title="Delete this tutorial run"
                      className="rounded border border-rose-700/60 bg-rose-900/20 px-2 py-1 text-[11px] text-rose-200 hover:bg-rose-900/35 disabled:opacity-50"
                    >
                      {deletingRunPath === runPath ? "Deleting..." : "Delete"}
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
              </article>
            );
          })}
        </div>
      </section>
    </div>
  );
}
