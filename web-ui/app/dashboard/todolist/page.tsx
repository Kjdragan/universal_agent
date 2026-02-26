"use client";

import { useCallback, useEffect, useState } from "react";
import { formatDistanceToNow, parseISO } from "date-fns";

const API_BASE = "";

// ── PROJECT DEFINITIONS ──────────────────────────────────────────────────────

const UA_PROJECTS = [
    {
        key: "UA: Mission & Identity",
        label: "Mission & Identity",
        description: "Core missions from soul doc, identity, and values",
        icon: "🎯",
        accent: "text-amber-400 border-amber-800/50 bg-amber-950/30",
        badge: "bg-amber-900/40 border-amber-700/40 text-amber-200",
    },
    {
        key: "UA: Memory Insights",
        label: "Memory Insights",
        description: "Surfaced from memory analysis, profile updates, knowledge gaps",
        icon: "🧠",
        accent: "text-violet-400 border-violet-800/50 bg-violet-950/30",
        badge: "bg-violet-900/40 border-violet-700/40 text-violet-200",
    },
    {
        key: "UA: Proactive Intelligence",
        label: "Proactive Intelligence",
        description: "Heartbeat & cron candidates — Simone can self-dispatch",
        icon: "⚡",
        accent: "text-sky-400 border-sky-800/50 bg-sky-950/30",
        badge: "bg-sky-900/40 border-sky-700/40 text-sky-200",
        hasSections: true,
    },
    {
        key: "UA: CSI Actions",
        label: "CSI Actions",
        description: "CSI-surfaced signals raised for review or investigation",
        icon: "📡",
        accent: "text-emerald-400 border-emerald-800/50 bg-emerald-950/30",
        badge: "bg-emerald-900/40 border-emerald-700/40 text-emerald-200",
    },
    {
        key: "UA: Immediate Queue",
        label: "Immediate Queue",
        description: "24-hour catch-all: user or agent closes right now",
        icon: "🔥",
        accent: "text-rose-400 border-rose-800/50 bg-rose-950/30",
        badge: "bg-rose-900/40 border-rose-700/40 text-rose-200",
    },
];

const PROACTIVE_SECTIONS: Record<string, string> = {
    inbox: "Inbox",
    triaging: "Triaging",
    heartbeat_candidate: "Heartbeat",
    approved: "Approved",
    in_implementation: "In Progress",
    parked: "Parked",
};

// ── TYPES ────────────────────────────────────────────────────────────────────

type PipelineSummary = Record<string, number | Record<string, number>>;

type ActionableTask = {
    id: string;
    content: string;
    description: string;
    due?: { date: string; is_recurring: boolean; datetime?: string } | null;
    labels: string[];
    priority: number;
    url: string;
    created_at: string;
    sub_agent?: string | null;
};

type HeartbeatCandidate = {
    dedupe_key: string;
    source_section: string;
    content: string;
    description: string;
    task_id: string;
    url: string;
    labels: string[];
};

// ── PAGE ─────────────────────────────────────────────────────────────────────

export default function ToDoListDashboardPage() {
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState("");

    const [pipelineSummary, setPipelineSummary] = useState<PipelineSummary | null>(null);
    const [actionableTasks, setActionableTasks] = useState<ActionableTask[]>([]);
    const [heartbeatCandidates, setHeartbeatCandidates] = useState<HeartbeatCandidate[]>([]);

    const load = useCallback(async () => {
        setLoading(true);
        setError("");
        try {
            const [pipeRes, actRes, hbRes] = await Promise.all([
                fetch(`${API_BASE}/api/v1/dashboard/todolist/pipeline`),
                fetch(`${API_BASE}/api/v1/dashboard/todolist/actionable`),
                fetch(`${API_BASE}/api/v1/dashboard/todolist/heartbeat`),
            ]);

            const pipePayload = pipeRes.ok ? await pipeRes.json() : null;
            const actPayload = actRes.ok ? await actRes.json() : null;
            const hbPayload = hbRes.ok ? await hbRes.json() : null;

            if (pipePayload?.status === "ok" && pipePayload.pipeline_summary) {
                setPipelineSummary(pipePayload.pipeline_summary);
            }
            if (actPayload?.status === "ok" && Array.isArray(actPayload.actionable_tasks)) {
                setActionableTasks(actPayload.actionable_tasks);
            }
            if (hbPayload?.status === "ok" && Array.isArray(hbPayload.heartbeat_candidates)) {
                setHeartbeatCandidates(hbPayload.heartbeat_candidates);
            }
        } catch (err: any) {
            setError(err?.message || "Failed to load To Do List data.");
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => { void load(); }, [load]);

    const getPriorityInfo = (priority: number) => {
        switch (priority) {
            case 4: return { label: "Urgent", color: "text-rose-400 border-rose-400/30 bg-rose-400/10" };
            case 3: return { label: "High", color: "text-amber-400 border-amber-400/30 bg-amber-400/10" };
            case 2: return { label: "Medium", color: "text-sky-400 border-sky-400/30 bg-sky-400/10" };
            default: return { label: "Normal", color: "text-slate-400 border-slate-700 bg-slate-800/50" };
        }
    };

    if (loading) {
        return (
            <div className="flex h-full items-center justify-center p-6 text-slate-400">
                Loading To Do List integration data...
            </div>
        );
    }

    const proactiveSections = pipelineSummary
        ? (pipelineSummary["UA: Proactive Intelligence__sections"] as Record<string, number> | undefined)
        : undefined;

    return (
        <div className="flex h-full flex-col gap-5">
            {/* Header */}
            <div className="flex items-start justify-between">
                <div>
                    <h1 className="text-xl font-semibold tracking-tight">To Do List — Task Command Center</h1>
                    <p className="text-sm text-slate-400">
                        All 5 UA project buckets · Agents and users route tasks here · Simone monitors proactively
                    </p>
                </div>
                <button
                    onClick={load}
                    className="rounded-md border border-slate-700 bg-slate-800/80 px-3 py-1.5 text-sm font-medium text-slate-300 transition hover:bg-slate-700 hover:text-white"
                >
                    Refresh
                </button>
            </div>

            {error && (
                <div className="rounded-lg border border-red-900/50 bg-red-900/20 px-4 py-3 text-sm text-red-200">
                    {error}
                </div>
            )}

            {/* ── 5 PROJECT CARDS ── */}
            <section>
                <h2 className="mb-3 text-xs font-semibold uppercase tracking-widest text-slate-500">
                    5 Project Buckets
                </h2>
                <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
                    {UA_PROJECTS.map((project) => {
                        const count = pipelineSummary
                            ? typeof pipelineSummary[project.key] === "number"
                                ? (pipelineSummary[project.key] as number)
                                : 0
                            : null;

                        return (
                            <div
                                key={project.key}
                                className={`rounded-xl border p-4 flex flex-col gap-2 transition ${project.accent}`}
                            >
                                <div className="flex items-center justify-between">
                                    <span className="text-lg">{project.icon}</span>
                                    {count !== null && (
                                        <span className={`rounded-full border px-2 py-0.5 text-xs font-bold ${project.badge}`}>
                                            {count}
                                        </span>
                                    )}
                                </div>
                                <div>
                                    <div className="font-semibold text-sm text-slate-100">{project.label}</div>
                                    <div className="text-[11px] text-slate-400 mt-0.5 leading-snug">{project.description}</div>
                                </div>

                                {/* Proactive section breakdown */}
                                {project.hasSections && proactiveSections && (
                                    <div className="mt-2 pt-2 border-t border-slate-700/50 grid grid-cols-2 gap-1">
                                        {Object.entries(PROACTIVE_SECTIONS).map(([key, label]) => (
                                            <div key={key} className="flex items-center justify-between text-[10px]">
                                                <span className="text-slate-500">{label}</span>
                                                <span className="font-bold text-slate-300">{proactiveSections[key] ?? 0}</span>
                                            </div>
                                        ))}
                                    </div>
                                )}
                            </div>
                        );
                    })}
                </div>
            </section>

            {/* ── IMMEDIATE ACTIONABLE QUEUE ── */}
            <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                <h2 className="mb-3 flex items-center gap-2 text-sm font-semibold uppercase tracking-[0.16em] text-slate-300">
                    <span className="relative flex h-2 w-2">
                        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-rose-400 opacity-75"></span>
                        <span className="relative inline-flex h-2 w-2 rounded-full bg-rose-500"></span>
                    </span>
                    🔥 Live Actionable Queue ({actionableTasks.length})
                </h2>
                {actionableTasks.length === 0 ? (
                    <p className="text-sm text-slate-500 italic">No tasks are currently marked @agent-ready and unblocked.</p>
                ) : (
                    <div className="space-y-3">
                        {actionableTasks.map((task) => {
                            const pInfo = getPriorityInfo(task.priority);
                            return (
                                <article key={task.id} className="flex flex-col gap-2 rounded-lg border border-slate-800/80 bg-slate-950/60 p-3">
                                    <div className="flex items-start justify-between">
                                        <div>
                                            <h3 className="font-semibold text-slate-200">
                                                <a href={task.url} target="_blank" rel="noreferrer" className="hover:underline hover:text-cyan-400">
                                                    {task.content}
                                                </a>
                                            </h3>
                                            {task.description && (
                                                <p className="mt-1 line-clamp-2 text-xs text-slate-400">{task.description}</p>
                                            )}
                                        </div>
                                        <span className={`ml-4 shrink-0 rounded border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${pInfo.color}`}>
                                            {pInfo.label}
                                        </span>
                                    </div>
                                    <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                                        {task.sub_agent && (
                                            <span className="rounded bg-slate-800/80 px-1.5 py-0.5 font-mono text-[10px] text-cyan-300">
                                                {task.sub_agent}
                                            </span>
                                        )}
                                        {task.labels.filter(l => l !== "agent-ready").map(label => (
                                            <span key={label} className="rounded bg-slate-800/50 px-1.5 py-0.5 text-[10px]">
                                                @{label}
                                            </span>
                                        ))}
                                        {task.due?.date && (
                                            <span className="text-amber-500/80">Due: {task.due.date}</span>
                                        )}
                                        <span className="ml-auto text-[10px]">
                                            Created: {formatDistanceToNow(parseISO(task.created_at), { addSuffix: true })}
                                        </span>
                                    </div>
                                </article>
                            );
                        })}
                    </div>
                )}
            </section>

            {/* ── HEARTBEAT CANDIDATES ── */}
            {heartbeatCandidates.length > 0 && (
                <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                    <h2 className="mb-3 text-sm font-semibold uppercase tracking-[0.16em] text-sky-400/90">
                        ⚡ Heartbeat Autonomy Candidates ({heartbeatCandidates.length})
                    </h2>
                    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                        {heartbeatCandidates.map(candidate => (
                            <article key={candidate.task_id} className="rounded-lg border border-slate-800/60 bg-slate-950/40 p-3">
                                <div className="flex items-center justify-between mb-2">
                                    <span className="rounded bg-sky-900/30 border border-sky-800/50 px-1.5 py-0.5 text-[9px] uppercase tracking-wider text-sky-300">
                                        {candidate.source_section}
                                    </span>
                                    <a href={candidate.url} target="_blank" rel="noreferrer" className="text-[10px] text-slate-500 hover:text-cyan-400">
                                        View ↗
                                    </a>
                                </div>
                                <h3 className="text-sm font-medium text-slate-200 line-clamp-2">{candidate.content}</h3>
                                {candidate.description && (
                                    <p className="mt-1.5 text-xs text-slate-400 line-clamp-3">{candidate.description}</p>
                                )}
                            </article>
                        ))}
                    </div>
                </section>
            )}
        </div>
    );
}
