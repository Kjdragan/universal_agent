"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

const API_BASE = process.env.NEXT_PUBLIC_GATEWAY_URL || "http://localhost:8002";

type CronJob = {
  id: string;
  command: string;
  every?: string | null;
  enabled: boolean;
  workspace_dir?: string | null;
  user_id?: string | null;
  run_at?: string | null;
  next_run_at?: string | null;
};

export default function DashboardCronJobsPage() {
  const [jobs, setJobs] = useState<CronJob[]>([]);
  const [command, setCommand] = useState("");
  const [every, setEvery] = useState("30m");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`${API_BASE}/api/v1/cron/jobs`);
      if (!res.ok) throw new Error(`Load failed (${res.status})`);
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
      const payload = { command: command.trim(), every: every.trim() || undefined };
      const res = await fetch(`${API_BASE}/api/v1/cron/jobs`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const detail = await res.text();
        setError(detail || `Create failed (${res.status})`);
        return;
      }
      setCommand("");
      await load();
    },
    [command, every, load],
  );

  const runNow = useCallback(async (id: string) => {
    await fetch(`${API_BASE}/api/v1/cron/jobs/${encodeURIComponent(id)}/run`, { method: "POST" });
    await load();
  }, [load]);

  const deleteJob = useCallback(async (id: string) => {
    await fetch(`${API_BASE}/api/v1/cron/jobs/${encodeURIComponent(id)}`, { method: "DELETE" });
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

      <form onSubmit={createJob} className="grid gap-2 rounded-xl border border-slate-800 bg-slate-900/70 p-4 md:grid-cols-[1fr,120px,auto]">
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
          className="rounded-md border border-slate-700 bg-slate-950/70 px-3 py-2 text-sm outline-none focus:border-cyan-500"
        />
        <button
          type="submit"
          className="rounded-md border border-cyan-700 bg-cyan-500/20 px-3 py-2 text-sm text-cyan-100 hover:bg-cyan-500/30"
        >
          Create
        </button>
      </form>

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
          <article key={job.id} className="rounded-lg border border-slate-800 bg-slate-900/70 p-3">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="font-mono text-sm text-slate-100">{job.command}</p>
                <p className="mt-1 text-xs text-slate-400">
                  {job.every ? `every ${job.every}` : "one-shot"} · {job.enabled ? "enabled" : "disabled"} · next:{" "}
                  {job.next_run_at || "n/a"}
                </p>
              </div>
              <div className="flex items-center gap-2">
                <button
                  type="button"
                  onClick={() => runNow(job.id)}
                  className="rounded-md border border-emerald-700 bg-emerald-500/20 px-2 py-1 text-xs text-emerald-100 hover:bg-emerald-500/30"
                >
                  Run now
                </button>
                <button
                  type="button"
                  onClick={() => deleteJob(job.id)}
                  className="rounded-md border border-amber-700 bg-amber-500/20 px-2 py-1 text-xs text-amber-100 hover:bg-amber-500/30"
                >
                  Delete
                </button>
              </div>
            </div>
          </article>
        ))}
      </section>
    </div>
  );
}
