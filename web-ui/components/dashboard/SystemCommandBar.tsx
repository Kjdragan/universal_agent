"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

const API_BASE = "/api/dashboard/gateway";
const COMMAND_HISTORY_KEY = "ua.system_command_history.v1";
const COMMAND_HISTORY_MAX = 12;

type SystemCommandResponse = {
  ok?: boolean;
  intent?: string;
  lane?: string;
  interpreted?: Record<string, unknown>;
  todoist?: Record<string, unknown> | null;
  cron?: Record<string, unknown> | null;
  dry_run?: boolean;
};

type CommandHistoryEntry = {
  id: string;
  at: string;
  source_page: string;
  text: string;
  ok: boolean;
  intent?: string;
  lane?: string;
  todoist_task_id?: string;
  cron_job_id?: string;
  error?: string;
};

function asText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

type SystemCommandBarProps = {
  sourcePage: string;
};

export default function SystemCommandBar({ sourcePage }: SystemCommandBarProps) {
  const searchParams = useSearchParams();
  const [text, setText] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState<SystemCommandResponse | null>(null);
  const [history, setHistory] = useState<CommandHistoryEntry[]>([]);

  const placeholder = useMemo(() => {
    if (sourcePage.includes("/tutorials")) {
      return "Example: Add this tutorial package to Todoist and schedule review tomorrow at 9am";
    }
    if (sourcePage.includes("/cron-jobs")) {
      return "Example: Schedule daily autonomous briefing at 7am";
    }
    return "Type or dictate a system command (e.g., 'add this to Todoist for tonight at 2am')";
  }, [sourcePage]);

  const sourceContext = useMemo(() => {
    const query: Record<string, string> = {};
    searchParams.forEach((value, key) => {
      query[key] = value;
    });
    const selectionKeys = ["session_id", "active_session_id", "task_id", "job_id", "run_path", "event_id", "path", "preview"];
    const selection: Record<string, string> = {};
    for (const key of selectionKeys) {
      const value = query[key];
      if (value) selection[key] = value;
    }
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
    return {
      lane: "system_non_chat",
      route: sourcePage,
      query,
      selection,
      timezone,
      locale: typeof navigator !== "undefined" ? navigator.language : "",
      href: typeof window !== "undefined" ? window.location.href : "",
      user_agent: typeof navigator !== "undefined" ? navigator.userAgent : "",
      captured_at: new Date().toISOString(),
    };
  }, [searchParams, sourcePage]);

  useEffect(() => {
    try {
      if (typeof window === "undefined") return;
      const raw = window.localStorage.getItem(COMMAND_HISTORY_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw);
      if (!Array.isArray(parsed)) return;
      const rows = parsed
        .filter((row) => row && typeof row === "object")
        .slice(0, COMMAND_HISTORY_MAX) as CommandHistoryEntry[];
      setHistory(rows);
    } catch {
      // Ignore local history parse errors.
    }
  }, []);

  const persistHistory = (next: CommandHistoryEntry[]) => {
    setHistory(next);
    try {
      if (typeof window === "undefined") return;
      window.localStorage.setItem(COMMAND_HISTORY_KEY, JSON.stringify(next.slice(0, COMMAND_HISTORY_MAX)));
    } catch {
      // Ignore persistence failures (private mode/storage quota).
    }
  };

  const appendHistory = (entry: CommandHistoryEntry) => {
    const next = [entry, ...history.filter((row) => row.id !== entry.id)].slice(0, COMMAND_HISTORY_MAX);
    persistHistory(next);
  };

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault();
    const value = asText(text);
    if (!value) return;
    setSubmitting(true);
    setError("");
    try {
      const response = await fetch(`${API_BASE}/api/v1/dashboard/system/commands`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          text: value,
          source_page: sourcePage,
          source_context: sourceContext,
          timezone: typeof sourceContext.timezone === "string" ? sourceContext.timezone : "UTC",
        }),
      });
      const payload = (await response.json().catch(() => ({}))) as SystemCommandResponse & { detail?: string };
      if (!response.ok) {
        throw new Error(asText(payload.detail) || `Command failed (${response.status})`);
      }
      const payloadTodoistTask =
        payload.todoist && typeof payload.todoist === "object" && (payload.todoist as Record<string, unknown>).task
          ? ((payload.todoist as Record<string, unknown>).task as Record<string, unknown>)
          : undefined;
      const payloadCronJob =
        payload.cron && typeof payload.cron === "object" && (payload.cron as Record<string, unknown>).job
          ? ((payload.cron as Record<string, unknown>).job as Record<string, unknown>)
          : undefined;
      setResult(payload);
      appendHistory({
        id: `${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
        at: new Date().toISOString(),
        source_page: sourcePage,
        text: value,
        ok: true,
        lane: asText(payload.lane),
        intent: asText(payload.intent),
        todoist_task_id: asText(payloadTodoistTask?.id),
        cron_job_id: asText(payloadCronJob?.job_id),
      });
      setText("");
    } catch (err: any) {
      const errMsg = err?.message || "Failed to submit system command.";
      setError(errMsg);
      appendHistory({
        id: `${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
        at: new Date().toISOString(),
        source_page: sourcePage,
        text: value,
        ok: false,
        error: errMsg,
      });
      setResult(null);
    } finally {
      setSubmitting(false);
    }
  };

  const intent = asText(result?.intent);
  const lane = asText(result?.lane);
  const interpreted = result?.interpreted && typeof result.interpreted === "object"
    ? result.interpreted
    : {};
  const interpretedContent = asText((interpreted as Record<string, unknown>).content);
  const interpretedSchedule = asText((interpreted as Record<string, unknown>).schedule_text);
  const todoistBlock = result?.todoist && typeof result.todoist === "object"
    ? (result.todoist as Record<string, unknown>)
    : {};
  const todoistTask = todoistBlock.task && typeof todoistBlock.task === "object"
    ? (todoistBlock.task as Record<string, unknown>)
    : {};
  const cronBlock = result?.cron && typeof result.cron === "object"
    ? (result.cron as Record<string, unknown>)
    : {};
  const cronJob = cronBlock.job && typeof cronBlock.job === "object"
    ? (cronBlock.job as Record<string, unknown>)
    : {};
  const todoistTaskId = asText(todoistTask.id);
  const cronJobId = asText(cronJob.job_id);
  const historyRows = history.slice(0, 5);

  return (
    <section className="mb-4 rounded-xl border border-slate-800 bg-slate-900/70 p-3">
      <div className="mb-2 flex items-center justify-between">
        <h2 className="text-xs font-semibold uppercase tracking-[0.14em] text-slate-300">System Command</h2>
        <span className="text-[11px] text-slate-500">Natural language · non-chat lane</span>
      </div>
      <div className="mb-2 text-[11px] text-slate-500">
        route={sourcePage}
        {Object.keys(sourceContext.selection || {}).length > 0
          ? ` · selection_keys=${Object.keys(sourceContext.selection || {}).join(",")}`
          : ""}
      </div>
      <form onSubmit={handleSubmit} className="flex flex-col gap-2">
        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={placeholder}
          rows={2}
          className="w-full resize-y rounded-md border border-slate-700 bg-slate-950/70 px-3 py-2 text-sm text-slate-100 outline-none focus:border-cyan-500"
        />
        <div className="flex items-center justify-end">
          <button
            type="submit"
            disabled={submitting || !asText(text)}
            className="rounded-md border border-cyan-700/70 bg-cyan-900/30 px-3 py-1.5 text-xs text-cyan-100 hover:bg-cyan-900/45 disabled:opacity-50"
          >
            {submitting ? "Submitting..." : "Run Command"}
          </button>
        </div>
      </form>
      {error && (
        <div className="mt-2 rounded border border-rose-700/70 bg-rose-900/20 px-2 py-1 text-xs text-rose-200">
          {error}
        </div>
      )}
      {result && !error && (
        <div className="mt-2 rounded border border-emerald-700/40 bg-emerald-900/15 px-2 py-1 text-xs text-emerald-100">
          <div>
            lane={lane || "system"} · intent={intent || "unknown"}
          </div>
          {interpretedContent && <div>task={interpretedContent}</div>}
          {interpretedSchedule && <div>schedule={interpretedSchedule}</div>}
          {todoistTaskId && <div>todoist_task_id={todoistTaskId}</div>}
          {cronJobId && <div>cron_job_id={cronJobId}</div>}
        </div>
      )}
      <div className="mt-3 rounded border border-slate-800 bg-slate-950/30 p-2">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-[11px] uppercase tracking-[0.14em] text-slate-400">Recent Commands</span>
          <button
            type="button"
            onClick={() => persistHistory([])}
            className="text-[11px] text-slate-500 hover:text-slate-300"
          >
            Clear
          </button>
        </div>
        {historyRows.length === 0 && (
          <div className="text-xs text-slate-500">No command history yet on this browser.</div>
        )}
        <div className="space-y-1">
          {historyRows.map((row) => (
            <div key={row.id} className="rounded border border-slate-800/80 bg-slate-900/50 px-2 py-1 text-xs text-slate-300">
              <div className="flex items-center justify-between gap-2">
                <span className={row.ok ? "text-emerald-300" : "text-rose-300"}>
                  {row.ok ? "ok" : "error"} · {row.intent || "system_command"}
                </span>
                <button
                  type="button"
                  onClick={() => setText(row.text)}
                  className="text-[11px] text-cyan-300 hover:text-cyan-100"
                >
                  Reuse
                </button>
              </div>
              <div className="truncate text-slate-200">{row.text}</div>
              {!row.ok && row.error && <div className="truncate text-rose-300">{row.error}</div>}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
