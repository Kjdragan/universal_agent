"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { openOrFocusChatWindow } from "@/lib/chatWindow";

const API_BASE = "/api/dashboard/gateway";

type CronJob = {
  job_id: string;
  command: string;
  every_seconds?: number | null;
  cron_expr?: string | null;
  timeout_seconds?: number | null;
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
  const [scheduleTime, setScheduleTime] = useState("in 30 minutes");
  const [repeat, setRepeat] = useState(false);
  const [timeoutSeconds, setTimeoutSeconds] = useState("900");
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
      const scheduleValue = scheduleTime.trim();
      if (!scheduleValue) {
        setError("Enter a schedule time like 'in 20 minutes' or '4:30 pm'.");
        return;
      }
      const payload: Record<string, unknown> = {
        command: command.trim(),
        schedule_time: scheduleValue,
        repeat,
      };
      const timeoutValue = timeoutSeconds.trim();
      if (timeoutValue) {
        const parsed = Number.parseInt(timeoutValue, 10);
        if (!Number.isFinite(parsed) || parsed <= 0) {
          setError("Timeout must be a positive number of seconds.");
          return;
        }
        payload.timeout_seconds = parsed;
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
      await load();
    },
    [command, scheduleTime, repeat, timeoutSeconds, load],
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

  const updateJob = useCallback(async (jobId: string, payload: Record<string, unknown>) => {
    const res = await fetch(`${API_BASE}/api/v1/cron/jobs/${encodeURIComponent(jobId)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const detail = await res.text();
      setError(parseErrorDetail(detail) || `Update failed (${res.status})`);
      return false;
    }
    await load();
    return true;
  }, [load]);

  const editJobText = useCallback(async (job: CronJob) => {
    const nextText = window.prompt("Edit chron job text", job.command || "");
    if (nextText === null) return;
    const trimmed = nextText.trim();
    if (!trimmed) {
      setError("Chron job text cannot be empty.");
      return;
    }
    await updateJob(job.job_id, { command: trimmed });
  }, [updateJob]);

  const changeJobScheduleWithNaturalLanguage = useCallback(async (job: CronJob) => {
    const currentSchedule = job.run_at
      ? `one-shot at ${toLocalDateTime(job.run_at)}`
      : job.cron_expr
        ? `chron ${job.cron_expr}`
        : `every ${formatEverySeconds(job.every_seconds)}`;
    const nextInstruction = window.prompt(
      `Current schedule: ${currentSchedule}\n\nDescribe the new schedule in natural language.\nExamples: "every 30 minutes", "tomorrow 9:15 am", "in 2 hours".`,
      "",
    );
    if (nextInstruction === null) return;
    const trimmed = nextInstruction.trim();
    if (!trimmed) {
      setError("Schedule instruction cannot be empty.");
      return;
    }
    await updateJob(job.job_id, { schedule_time: trimmed });
  }, [updateJob]);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Chron Jobs</h1>
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

      <form onSubmit={createJob} className="grid gap-2 rounded-xl border border-slate-800 bg-slate-900/70 p-4 md:grid-cols-[1fr,230px,130px,130px,auto]">
        <input
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          placeholder="Command prompt for agent"
          className="rounded-md border border-slate-700 bg-slate-950/70 px-3 py-2 text-sm outline-none focus:border-cyan-500"
        />
        <input
          value={scheduleTime}
          onChange={(e) => setScheduleTime(e.target.value)}
          placeholder="Time (e.g. in 20 minutes, 4:30 pm)"
          className="rounded-md border border-slate-700 bg-slate-950/70 px-3 py-2 text-sm outline-none focus:border-cyan-500"
        />
        <div className="flex items-center gap-2 rounded-md border border-slate-700 bg-slate-950/70 px-3 py-2 text-sm">
          <input
            id="repeat"
            type="checkbox"
            checked={repeat}
            onChange={(e) => setRepeat(e.target.checked)}
            className="h-4 w-4 accent-cyan-500"
          />
          <label htmlFor="repeat" className="select-none text-slate-200">
            Repeat
          </label>
        </div>
        <div className="flex items-center gap-2">
          <input
            value={timeoutSeconds}
            onChange={(e) => setTimeoutSeconds(e.target.value)}
            placeholder="Timeout (sec)"
            className="w-full rounded-md border border-slate-700 bg-slate-950/70 px-3 py-2 text-sm outline-none focus:border-cyan-500"
          />
        </div>
        <button
          type="submit"
          className="rounded-md border border-cyan-700 bg-cyan-500/20 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-500/30"
        >
          Create
        </button>
      </form>
      <p className="text-xs text-slate-400">
        Enter a natural time like <span className="font-mono">in 20 minutes</span> or <span className="font-mono">4:30 pm</span>. With{" "}
        <span className="font-mono">Repeat</span> on, interval phrases repeat (e.g. <span className="font-mono">in 30 minutes</span>) and
        clock times run daily (e.g. <span className="font-mono">4:30 pm</span>).
      </p>

      {error && (
        <div className="rounded-lg border border-red-700/60 bg-red-900/20 p-3 text-sm text-red-200">{error}</div>
      )}

      <section className="space-y-2">
        {loading && <p className="text-sm text-slate-400">Loading…</p>}
        {!loading && jobs.length === 0 && (
          <div className="rounded-lg border border-slate-800 bg-slate-900/60 p-3 text-sm text-slate-400">
            No chron jobs configured.
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
                      ? `chron ${job.cron_expr}`
                      : `every ${formatEverySeconds(job.every_seconds)}`} ·{" "}
                  {job.running ? "running" : job.enabled ? "enabled" : "disabled"} · next: {toLocalDateTime(job.next_run_at)}
                  {job.timeout_seconds ? ` · timeout ${job.timeout_seconds}s` : ""}
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
                  onClick={() => editJobText(job)}
                  className="rounded-md border border-sky-700 bg-sky-500/20 px-2 py-1 text-xs text-sky-100 hover:bg-sky-500/30"
                >
                  Edit text
                </button>
                <button
                  type="button"
                  onClick={() => changeJobScheduleWithNaturalLanguage(job)}
                  className="rounded-md border border-violet-700 bg-violet-500/20 px-2 py-1 text-xs text-violet-100 hover:bg-violet-500/30"
                >
                  Change Schedule
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
