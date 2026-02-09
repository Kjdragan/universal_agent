"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { openOrFocusChatWindow } from "@/lib/chatWindow";

const API_BASE = "/api/dashboard/gateway";

type CronJob = {
  job_id: string;
  command: string;
  every_seconds?: number | null;
  cron_expr?: string | null;
  enabled: boolean;
  workspace_dir?: string | null;
  user_id?: string | null;
  running?: boolean;
  run_at?: string | number | null;
  next_run_at?: string | number | null;
  metadata?: Record<string, unknown> | null;
};

function toLocalDateTime(value?: string | number | null): string {
  if (value === null || value === undefined || value === "") return "n/a";
  if (typeof value === "number") return new Date(value * 1000).toLocaleString();
  const asNumber = Number(value);
  if (Number.isFinite(asNumber) && value.trim() !== "") {
    return new Date(asNumber * 1000).toLocaleString();
  }
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? String(value) : parsed.toLocaleString();
}

function parseErrorDetail(raw: string): string {
  if (!raw) return "Request failed.";
  try {
    const parsed = JSON.parse(raw) as { detail?: string };
    return parsed.detail || raw;
  } catch {
    return raw;
  }
}

function formatEverySeconds(value?: number | null): string {
  const seconds = Math.max(0, Number(value || 0));
  if (seconds <= 0) return "n/a";
  if (seconds % 86400 === 0) return `${seconds / 86400}d`;
  if (seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
}

function extractJobSessionId(job: CronJob): string {
  const metadata = (job.metadata || {}) as Record<string, unknown>;
  const fromMetadata = String(
    metadata.session_id
      || metadata.target_session_id
      || metadata.target_session
      || "",
  ).trim();
  if (fromMetadata) return fromMetadata;

  const workspace = String(job.workspace_dir || "").trim();
  const marker = "/AGENT_RUN_WORKSPACES/";
  const idx = workspace.lastIndexOf(marker);
  if (idx >= 0) {
    const tail = workspace.slice(idx + marker.length).split("/")[0];
    return tail || "";
  }
  return "";
}

export default function DashboardCronJobsPage() {
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [command, setCommand] = useState("");
  const [every, setEvery] = useState("30m");
  const [runAt, setRunAt] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/cron/jobs`);
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(parseErrorDetail(detail) || `Load failed (${res.status})`);
      }
      const data = await res.json();
      setJobs(data.jobs || []);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const createJob = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      if (!command.trim()) return;
      const runAtValue = runAt.trim();
      const everyValue = every.trim();
      const payload: Record<string, unknown> = { command: command.trim() };
      if (runAtValue) {
        payload.run_at = runAtValue;
        payload.delete_after_run = true;
      } else if (everyValue) {
        payload.every = everyValue;
      }
      const res = await fetch(`${API_BASE}/api/v1/cron/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const detail = await res.text();
        setError(parseErrorDetail(detail) || `Create failed (${res.status})`);
        return;
      }
      setCommand("");
      setRunAt("");
      await load();
    },
    [command, every, runAt, load],
  );

  const runNow = useCallback(async (jobId: string) => {
    const res = await fetch(`${API_BASE}/api/v1/cron/jobs/${encodeURIComponent(jobId)}/run`, { method: "POST" });
    if (!res.ok) {
      const detail = await res.text();
      setError(parseErrorDetail(detail) || `Run failed (${res.status})`);
      return;
    }
    await load();
  }, [load]);

  const deleteJob = useCallback(async (jobId: string) => {
    const res = await fetch(`${API_BASE}/api/v1/cron/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" });
    if (!res.ok) {
      const detail = await res.text();
      setError(parseErrorDetail(detail) || `Delete failed (${res.status})`);
      return;
    }
    await load();
  }, [load]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Cron Jobs</h1>
          <p className="text-sm text-slate-400">Schedule autonomous recurring tasks.</p>
        </div>
        <button
          type="button"
          onClick={load}
          className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-sm hover:bg-slate-800"
        >
          Refresh
        </button>
      </div>

      <form onSubmit={createJob} className="grid gap-2 rounded-xl border border-slate-800 bg-slate-900/70 p-4 md:grid-cols-[1fr,120px,240px,auto]">
        <input
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          placeholder="Command prompt for agent"
          className="rounded-md border border-slate-700 bg-slate-950/70 px-3 py-2 text-sm outline-none focus:border-cyan-500"
        />
        <input
          value={every}
          onChange={(e) => setEvery(e.target.value)}
          placeholder="every (e.g. 30m)"
          disabled={Boolean(runAt.trim())}
          className="rounded-md border border-slate-700 bg-slate-950/70 px-3 py-2 text-sm outline-none focus:border-cyan-500"
        />
        <input
          value={runAt}
          onChange={(e) => setRunAt(e.target.value)}
          placeholder="run at (e.g. 1am, tomorrow 9:15am, 2h, ISO)"
          className="rounded-md border border-slate-700 bg-slate-950/70 px-3 py-2 text-sm outline-none focus:border-cyan-500"
        />
        <button
          type="submit"
          className="rounded-md border border-cyan-700 bg-cyan-500/20 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-500/30"
        >
          Create
        </button>
      </form>
      <p className="text-xs text-slate-400">
        Set <span className="font-mono">run at</span> for one-shot jobs (auto delete after run), or leave it blank and use{" "}
        <span className="font-mono">every</span> for recurring jobs. Supported <span className="font-mono">run at</span> formats: natural
        phrases (`1am`, `tomorrow 9:15am`), relative duration (`30m`, `2h`), or ISO datetime.
      </p>

      {error && (
        <div className="rounded-lg border border-red-700/60 bg-red-900/20 p-3 text-sm text-red-200">{error}</div>
      )}

      <section className="space-y-2">
        {loading && <p className="text-sm text-slate-400">Loading…</p>}
        {!loading && jobs.length === 0 && (
          <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-sm text-slate-400">
            No cron jobs configured.
          </div>
        )}
        {jobs.map((job) => (
          <article key={job.job_id} className="rounded-lg border border-slate-800 bg-slate-900/70 p-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="font-mono text-sm text-slate-100">{job.command}</p>
                <p className="mt-1 text-xs text-slate-400">
                  {job.run_at
                    ? `one-shot at ${toLocalDateTime(job.run_at)}`
                    : job.cron_expr
                      ? `cron ${job.cron_expr}`
                      : `every ${formatEverySeconds(job.every_seconds)}`} ·{" "}
                  {job.running ? "running" : job.enabled ? "enabled" : "disabled"} · next: {toLocalDateTime(job.next_run_at)}
                </p>
                {(() => {
                  const sessionId = extractJobSessionId(job);
                  if (!sessionId) return null;
                  return (
                    <p className="mt-1 text-[11px] text-slate-500">
                      session: <span className="font-mono">{sessionId}</span>
                    </p>
                  );
                })()}
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => runNow(job.job_id)}
                  disabled={Boolean(job.running)}
                  className="rounded-md border border-emerald-700 bg-emerald-500/20 px-2 py-1 text-xs text-emerald-100 hover:bg-emerald-500/30"
                >
                  Run now
                </button>
                <button
                  type="button"
                  onClick={() => deleteJob(job.job_id)}
                  className="rounded-md border border-amber-700 bg-amber-500/20 px-2 py-1 text-xs text-amber-100 hover:bg-amber-500/30"
                >
                  Delete
                </button>
                {(() => {
                  const sessionId = extractJobSessionId(job);
                  if (!sessionId) return null;
                  return (
                    <button
                      type="button"
                      onClick={() =>
                        openOrFocusChatWindow({
                          sessionId,
                          attachMode: "tail",
                          role: "writer",
                        })
                      }
                      className="rounded-md border border-cyan-700 bg-cyan-500/20 px-2 py-1 text-xs text-cyan-100 hover:bg-cyan-500/30"
                    >
                      Open Session
                    </button>
                  );
                })()}
              </div>
            </div>
          </article>
        ))}
      </section>
    </div>
  );
}
