"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { openOrFocusChatWindow } from "@/lib/chatWindow";
import { formatDateTimeTz } from "@/lib/timezone";

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

type CronRun = {
  run_id: string;
  job_id: string;
  status: string;
  scheduled_at?: number | null;
  started_at: number;
  finished_at?: number | null;
  error?: string | null;
  output_preview?: string | null;
};

function toLocalDateTime(value?: string | number | null): string {
  return formatDateTimeTz(value, { placeholder: "n/a" });
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

function extractComposioConnectLink(raw?: string | null): string | null {
  if (!raw) return null;
  const text = String(raw);
  const m = text.match(/https?:\/\/connect\.composio\.dev\/link\/[A-Za-z0-9_-]+/);
  return m ? m[0] : null;
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
  const [runsByJob, setRunsByJob] = useState<Record<string, CronRun | undefined>>({});
  const [command, setCommand] = useState("");
  const [scheduleTime, setScheduleTime] = useState("in 30 minutes");
  const [repeat, setRepeat] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editForm, setEditForm] = useState<{ command: string; schedule: string }>({ command: "", schedule: "" });

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [jobsRes, runsRes] = await Promise.all([
        fetch(`${API_BASE}/api/v1/cron/jobs`),
        fetch(`${API_BASE}/api/v1/cron/runs?limit=500`),
      ]);

      if (!jobsRes.ok) {
        const detail = await jobsRes.text();
        throw new Error(parseErrorDetail(detail) || `Load failed (${jobsRes.status})`);
      }
      const jobsData = await jobsRes.json();
      setJobs(jobsData.jobs || []);

      if (runsRes.ok) {
        const runsData = await runsRes.json();
        const runs = (runsData.runs || []) as CronRun[];
        const latest: Record<string, CronRun> = {};
        for (const r of runs) {
          const key = String(r.job_id || "").trim();
          if (!key) continue;
          const cur = latest[key];
          if (!cur || Number(r.started_at || 0) > Number(cur.started_at || 0)) {
            latest[key] = r;
          }
        }
        setRunsByJob(latest);
      } else {
        // Runs are best-effort; don't block job list rendering on transient issues.
        setRunsByJob({});
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const submitCommand = useCallback(
    async (event: FormEvent) => {
      event.preventDefault();
      if (!command.trim()) return;
      setError(null);
      setSuccessMsg(null);

      // Build NL command text — combine schedule/repeat context into the text
      // so the LLM has full context for interpretation.
      let nlText = command.trim();
      const scheduleValue = scheduleTime.trim();
      if (scheduleValue) {
        nlText += ` | Schedule: ${scheduleValue}`;
      }
      if (repeat) {
        nlText += " | Repeat: yes";
      }

      const userTz = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
      const payload = {
        text: nlText,
        timezone: userTz,
      };

      setLoading(true);
      try {
        const res = await fetch(`${API_BASE}/api/v1/cron/commands`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        });
        if (!res.ok) {
          const detail = await res.text();
          setError(parseErrorDetail(detail) || `Command failed (${res.status})`);
          return;
        }
        const data = await res.json();
        const intent = data.intent || "create";
        const reason = data.interpreted?.reason || "";
        setSuccessMsg(`✅ ${intent.charAt(0).toUpperCase() + intent.slice(1)}d successfully.${reason ? " — " + reason : ""}`);
        setCommand("");
        await load();
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setLoading(false);
      }
    },
    [command, scheduleTime, repeat, load],
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

  const startEditing = useCallback((job: CronJob) => {
    const currentSchedule = job.run_at
      ? `one-shot at ${toLocalDateTime(job.run_at)}`
      : job.cron_expr
        ? `cron ${job.cron_expr}`
        : `every ${formatEverySeconds(job.every_seconds)}`;

    setEditingId(job.job_id);
    setEditForm({
      command: job.command,
      schedule: currentSchedule
    });
  }, []);

  const saveEdit = useCallback(async (jobId: string) => {
    const { command, schedule } = editForm;
    if (!command.trim()) {
      setError("Command cannot be empty.");
      return;
    }

    // Save both command and schedule
    // We update command first
    const ok = await updateJob(jobId, { command: command.trim() });
    if (!ok) return;

    // Then schedule if changed (simple check, backend handles parsing)
    if (schedule.trim()) {
      await updateJob(jobId, { schedule_time: schedule.trim() });
    }

    setEditingId(null);
  }, [editForm, updateJob]);

  const cancelEdit = useCallback(() => {
    setEditingId(null);
    setEditForm({ command: "", schedule: "" });
  }, []);

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Chron Jobs</h1>
          <p className="text-sm text-muted-foreground">Schedule autonomous recurring tasks.</p>
        </div>
        <div className="flex items-center gap-2">
          <Link
            href="/dashboard"
            className="rounded-lg border border-primary/30 bg-primary/15 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary/90 hover:bg-primary/25"
          >
            Back to Home
          </Link>
          <button
            type="button"
            onClick={load}
            className="rounded-lg border border-border bg-card/60 px-3 py-1.5 text-sm hover:bg-card"
          >
            Refresh
          </button>
        </div>
      </div>

      <form onSubmit={submitCommand} className="grid gap-2 rounded-xl border border-border bg-background/70 p-4 md:grid-cols-[1fr,230px,130px,auto]">
        <input
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          placeholder="Describe what to do — create, edit, or manage a cron job"
          className="rounded-md border border-border bg-background/70 px-3 py-2 text-sm outline-none focus:border-primary"
        />
        <input
          value={scheduleTime}
          onChange={(e) => setScheduleTime(e.target.value)}
          placeholder="Schedule (optional)"
          className="rounded-md border border-border bg-background/70 px-3 py-2 text-sm outline-none focus:border-primary"
        />
        <div className="flex items-center gap-2 rounded-md border border-border bg-background/70 px-3 py-2 text-sm">
          <input
            id="repeat"
            type="checkbox"
            checked={repeat}
            onChange={(e) => setRepeat(e.target.checked)}
            className="h-4 w-4 accent-cyan-500"
          />
          <label htmlFor="repeat" className="select-none text-foreground">
            Repeat
          </label>
        </div>
        <button
          type="submit"
          disabled={loading}
          className="rounded-md border border-primary/30 bg-primary/20 px-3 py-2 text-sm text-primary/90 hover:bg-primary/30 disabled:opacity-50"
        >
          {loading ? "Processing…" : "Submit"}
        </button>
      </form>
      <p className="text-xs text-muted-foreground">
        Use natural language to <strong>create</strong>, <strong>edit</strong>, <strong>delete</strong>, <strong>run</strong>, or <strong>enable/disable</strong> cron jobs.
        Example: <span className="font-mono">&quot;Change the daily briefing to 7:30 am&quot;</span> or <span className="font-mono">&quot;Run the news summary now&quot;</span>.
      </p>

      {successMsg && (
        <div className="rounded-lg border border-green-700/60 bg-green-900/20 p-3 text-sm text-green-200">{successMsg}</div>
      )}

      {error && (
        <div className="rounded-lg border border-red-700/60 bg-red-900/20 p-3 text-sm text-red-200">{error}</div>
      )}

      <section className="space-y-2">
        {loading && <p className="text-sm text-muted-foreground">Loading…</p>}
        {!loading && jobs.length === 0 && (
          <div className="rounded-lg border border-border bg-background/60 p-3 text-sm text-muted-foreground">
            No chron jobs configured.
          </div>
        )}
        {jobs.map((job) => (
          <article key={job.job_id} className="rounded-lg border border-border bg-background/70 p-3">
            <div className="flex items-center justify-between gap-3">
              <div className="flex-1">
                {editingId === job.job_id ? (
                  <div className="space-y-2">
                    <input
                      value={editForm.command}
                      onChange={(e) => setEditForm(prev => ({ ...prev, command: e.target.value }))}
                      className="w-full rounded-md border border-border bg-background px-2 py-1 text-sm font-mono text-foreground outline-none focus:border-primary"
                      placeholder="Command"
                      autoFocus
                    />
                    <input
                      value={editForm.schedule}
                      onChange={(e) => setEditForm(prev => ({ ...prev, schedule: e.target.value }))}
                      className="w-full rounded-md border border-border bg-background px-2 py-1 text-xs font-mono text-foreground/80 outline-none focus:border-primary"
                      placeholder="Schedule (e.g. every 30m)"
                      onKeyDown={(e) => { if (e.key === "Enter") saveEdit(job.job_id); }}
                    />
                  </div>
                ) : (
                  <>
                    <p className="font-mono text-sm text-foreground">{job.command}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {job.run_at
                        ? `one-shot at ${toLocalDateTime(job.run_at)}`
                        : job.cron_expr
                          ? `cron ${job.cron_expr}`
                          : `every ${formatEverySeconds(job.every_seconds)}`} ·{" "}
                      {job.running ? "running" : job.enabled ? "enabled" : "disabled"} · next: {toLocalDateTime(job.next_run_at)}
                      {job.timeout_seconds ? ` · timeout ${job.timeout_seconds}s` : ""}
                    </p>
                    {job.running && (
                      <div className="mt-2 inline-flex items-center gap-2 rounded-md border border-primary/30 bg-primary/10 px-2 py-1 text-[11px] text-primary/80">
                        <span className="h-1.5 w-1.5 rounded-full bg-primary animate-pulse" />
                        Running now... latest persisted run details are shown below.
                      </div>
                    )}
                    {(() => {
                      const run = runsByJob[job.job_id];
                      if (!run) return null;
                      const link = extractComposioConnectLink(run.output_preview || "") || extractComposioConnectLink(run.error || "");
                      const status = String(run.status || "unknown").toLowerCase();
                      const isAuth = status === "auth_required" || Boolean(link);
                      const runLabel = job.running ? "latest recorded run" : "last run";
                      return (
                        <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
                          <span className="text-muted-foreground">
                            {runLabel}: <span className="font-mono text-foreground/80">{status}</span> · {toLocalDateTime(run.started_at)}
                          </span>
                          {run.error && (
                            <span className="text-secondary/90">
                              error: <span className="font-mono">{String(run.error).slice(0, 140)}</span>
                            </span>
                          )}
                          {isAuth && link && (
                            <a
                              href={link}
                              target="_blank"
                              rel="noreferrer"
                              className="rounded-md border border-amber-700 bg-amber-500/15 px-2 py-1 text-[11px] text-amber-200 hover:bg-amber-500/25"
                              title="Open Composio to connect Gmail (required for this job to send email)."
                            >
                              Connect Gmail
                            </a>
                          )}
                          {isAuth && !link && (
                            <span className="text-amber-200/90">
                              auth required (open run logs for the connect link)
                            </span>
                          )}
                        </div>
                      );
                    })()}
                    {(() => {
                      const sessionId = extractJobSessionId(job);
                      if (!sessionId) return null;
                      return (
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          session: <span className="font-mono">{sessionId}</span>
                        </p>
                      );
                    })()}
                  </>
                )}
              </div>
              <div className="flex items-center gap-2">
                {editingId === job.job_id ? (
                  <>
                    <button
                      type="button"
                      onClick={() => saveEdit(job.job_id)}
                      className="rounded-md border border-primary/30 bg-primary/15 px-2 py-1 text-xs text-primary/80 hover:bg-primary/25"
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={cancelEdit}
                      className="rounded-md border border-border bg-card/60 px-2 py-1 text-xs text-foreground/80 hover:bg-card"
                    >
                      Cancel
                    </button>
                  </>
                ) : (
                  <>
                    <button
                      type="button"
                      onClick={() => runNow(job.job_id)}
                      disabled={Boolean(job.running)}
                      className="rounded-md border border-primary/30 bg-primary/20 px-2 py-1 text-xs text-primary/90 hover:bg-primary/30"
                    >
                      Run
                    </button>
                    <button
                      type="button"
                      onClick={() => startEditing(job)}
                      className="rounded-md border border-sky-700 bg-sky-500/20 px-2 py-1 text-xs text-sky-100 hover:bg-sky-500/30"
                    >
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => deleteJob(job.job_id)}
                      className="rounded-md border border-amber-700 bg-accent/20 px-2 py-1 text-xs text-amber-100 hover:bg-amber-500/30"
                    >
                      Del
                    </button>
                  </>
                )}

                {(() => {
                  if (editingId === job.job_id) return null;
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
                      className="rounded-md border border-primary/30 bg-primary/20 px-2 py-1 text-xs text-primary/90 hover:bg-primary/30"
                    >
                      Open
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
