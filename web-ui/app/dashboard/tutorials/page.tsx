"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";

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
  run_storage_href?: string;
  files?: TutorialFile[];
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
  const parsed = value ? new Date(value) : null;
  if (!parsed || Number.isNaN(parsed.getTime())) return value || "--";
  return parsed.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
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

export default function DashboardTutorialsPage() {
  const [runs, setRuns] = useState<TutorialRun[]>([]);
  const [jobs, setJobs] = useState<TutorialReviewJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [dispatchingRunPath, setDispatchingRunPath] = useState<string>("");
  const [dispatchStatus, setDispatchStatus] = useState<string>("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [runsRes, jobsRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/dashboard/tutorials/runs?limit=120`),
        fetch(`${API_BASE}/api/v1/dashboard/tutorials/review-jobs?limit=120`),
      ]);
      const runsPayload = runsRes.ok ? await runsRes.json() : { runs: [] };
      const jobsPayload = jobsRes.ok ? await jobsRes.json() : { jobs: [] };
      setRuns(Array.isArray(runsPayload.runs) ? (runsPayload.runs as TutorialRun[]) : []);
      setJobs(Array.isArray(jobsPayload.jobs) ? (jobsPayload.jobs as TutorialReviewJob[]) : []);
    } catch (err: any) {
      setError(err?.message || "Failed to load tutorial backlog");
      setRuns([]);
      setJobs([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const latestJobByRun = useMemo(() => {
    const map = new Map<string, TutorialReviewJob>();
    for (const job of jobs) {
      const runPath = asText(job.tutorial_run_path);
      if (!runPath || map.has(runPath)) continue;
      map.set(runPath, job);
    }
    return map;
  }, [jobs]);

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

  return (
    <div className="flex h-full flex-col gap-4">
      <div className="flex items-center justify-between">
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
            return (
              <article key={runPath} className="rounded-lg border border-slate-800/80 bg-slate-950/60 p-3">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="text-sm font-semibold text-slate-100">{asText(run.title) || runPath}</p>
                    <p className="mt-1 text-xs text-slate-400">
                      status={asText(run.status) || "unknown"} · created={formatDate(run.created_at)}
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
                  <div className="flex flex-wrap gap-2">
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
                        {latestJobStatus === "running" ? "Watch Live" : "Rehydrate Session"}
                      </a>
                    )}
                    <button
                      type="button"
                      onClick={() => void dispatchToSimone(runPath)}
                      disabled={dispatchingRunPath === runPath}
                      className="rounded border border-emerald-700/60 bg-emerald-900/20 px-2 py-1 text-[11px] text-emerald-100 hover:bg-emerald-900/35 disabled:opacity-50"
                    >
                      {dispatchingRunPath === runPath ? "Queueing..." : "Send to Simone"}
                    </button>
                  </div>
                </div>
                {files.length > 0 && (
                  <div className="mt-2 grid gap-1 sm:grid-cols-2">
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
                  <p className="mt-2 text-[11px] text-slate-400">
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
