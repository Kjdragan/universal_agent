"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { formatDistanceToNow, parseISO } from "date-fns";
import { useVirtualizer } from "@tanstack/react-virtual";

const API_BASE = "/api/dashboard/gateway";
const ACTIONABLE_PAGE_SIZE = 60;
const ENDPOINTS = {
    pipeline: `${API_BASE}/api/v1/dashboard/todolist/pipeline`,
    actionablePagedBase: `${API_BASE}/api/v1/dashboard/todolist/actionable_paged`,
    heartbeat: `${API_BASE}/api/v1/dashboard/todolist/heartbeat`,
    immediateScheduled: `${API_BASE}/api/v1/dashboard/todolist/project?project_key=immediate&section=scheduled&include_agent_ready=false&limit=100`,
};

const TODOLIST_CACHE_KEY = "ua_todolist_dashboard_cache_v1";
const CACHE_MAX_AGE_MS = 6 * 60 * 60 * 1000; // 6h

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
type EndpointDiagnostic = {
    endpoint: string;
    ok: boolean;
    status?: number;
    detail?: string;
};

type ActionableTask = {
    id: string;
    content: string;
    description: string;
    due?: { date: string; is_recurring: boolean; datetime?: string } | null;
    due_date?: string | null;
    due_datetime?: string | null;
    labels: string[];
    priority: number | string;
    project_id?: string | null;
    url: string;
    created_at: string;
    sub_agent?: string | null;
    section_id?: string | null;
    description_truncated?: boolean;
};

type ActionablePagination = {
    total: number;
    offset: number;
    limit: number;
    count: number;
    has_more: boolean;
};

type ToDoListCacheSnapshot = {
    savedAt: string;
    pipelineSummary: PipelineSummary | null;
    projectIds: Record<string, string>;
    selectedProjectKey: string | null;
    actionableTasks: ActionableTask[];
    actionablePagination: ActionablePagination;
    heartbeatCandidates: HeartbeatCandidate[];
    scheduledImmediateTasks: ActionableTask[];
    diagnostics: EndpointDiagnostic[];
    error: string;
};

let memoryCache: ToDoListCacheSnapshot | null = null;

function buildActionableEndpoint(opts?: {
    offset?: number;
    limit?: number;
    projectKey?: string | null;
}): string {
    const params = new URLSearchParams();
    params.set("offset", String(Math.max(0, Number(opts?.offset ?? 0) || 0)));
    params.set("limit", String(Math.max(1, Math.min(200, Number(opts?.limit ?? ACTIONABLE_PAGE_SIZE) || ACTIONABLE_PAGE_SIZE))));
    const projectKey = String(opts?.projectKey || "").trim();
    if (projectKey) {
        params.set("project_key", projectKey);
    }
    return `${ENDPOINTS.actionablePagedBase}?${params.toString()}`;
}

type HeartbeatCandidate = {
    dedupe_key: string;
    source_section: string;
    content: string;
    description: string;
    task_id: string;
    url: string;
    labels: string[];
};

type CsiTaskLinks = {
    isCsiTask: boolean;
    isInteresting: boolean;
    eventType: string;
    eventId: string;
    reportKey: string;
    artifactPath: string;
    notificationHref: string;
    reportHref: string;
    artifactHref: string;
    csiFeedHref: string;
};

const CSI_INTERESTING_EVENT_TYPES = new Set([
    "report_product_ready",
    "opportunity_bundle_ready",
    "rss_trend_report",
    "rss_insight_daily",
    "rss_insight_emerging",
    "analysis_task_completed",
]);

function extractFirstMatch(input: string, pattern: RegExp): string {
    const match = pattern.exec(input);
    return match?.[1] ? String(match[1]).trim() : "";
}

function deriveCsiTaskLinks(task: ActionableTask): CsiTaskLinks {
    const content = String(task.content || "");
    const description = String(task.description || "");
    const text = `${content}\n${description}`;
    const labels = Array.isArray(task.labels) ? task.labels : [];

    const isCsiTask = labels.some((label) => String(label).toLowerCase() === "csi")
        || labels.some((label) => String(label).toLowerCase().startsWith("csi-project:"))
        || /(^|\s)csi(\s|:)/i.test(content);

    if (!isCsiTask) {
        return {
            isCsiTask: false,
            isInteresting: false,
            eventType: "",
            eventId: "",
            reportKey: "",
            artifactPath: "",
            notificationHref: "",
            reportHref: "",
            artifactHref: "",
            csiFeedHref: "/dashboard/csi",
        };
    }

    const eventType = extractFirstMatch(text, /event_type:\s*([a-zA-Z0-9_.:-]+)/i).toLowerCase();
    const eventId = extractFirstMatch(text, /event_id:\s*([a-zA-Z0-9_.:-]+)/i);

    let reportKey = extractFirstMatch(text, /report_key:\s*([^\n\r]+)/i);
    if (!reportKey) {
        reportKey = extractFirstMatch(text, /"report_key"\s*:\s*"([^"]+)"/i);
    }
    reportKey = reportKey.replace(/["',]+$/g, "").trim();

    let artifactPath = extractFirstMatch(text, /Report Artifact:\s*([^\n\r]+)/i);
    if (!artifactPath) {
        artifactPath = extractFirstMatch(text, /"markdown"\s*:\s*"([^"]+)"/i);
    }
    if (!artifactPath) {
        artifactPath = extractFirstMatch(text, /"json"\s*:\s*"([^"]+)"/i);
    }
    artifactPath = artifactPath.replace(/["',]+$/g, "").trim();

    const reportishContent = /report|bundle|opportunit|insight|trend/i.test(content);
    const isInteresting = Boolean(
        reportKey
        || artifactPath
        || CSI_INTERESTING_EVENT_TYPES.has(eventType)
        || reportishContent
    );

    return {
        isCsiTask: true,
        isInteresting,
        eventType,
        eventId,
        reportKey,
        artifactPath,
        notificationHref: eventId ? `/dashboard/csi?event_id=${encodeURIComponent(eventId)}` : "",
        reportHref: reportKey ? `/dashboard/csi?report_key=${encodeURIComponent(reportKey)}` : "",
        artifactHref: artifactPath ? `/dashboard/csi?artifact_path=${encodeURIComponent(artifactPath)}` : "",
        csiFeedHref: "/dashboard/csi",
    };
}

// ── PAGE ─────────────────────────────────────────────────────────────────────

export default function ToDoListDashboardPage() {
    const [loading, setLoading] = useState(true);
    const [refreshing, setRefreshing] = useState(false);
    const [loadingMore, setLoadingMore] = useState(false);
    const [error, setError] = useState("");
    const [diagnostics, setDiagnostics] = useState<EndpointDiagnostic[]>([]);

    const [pipelineSummary, setPipelineSummary] = useState<PipelineSummary | null>(null);
    const [projectIds, setProjectIds] = useState<Record<string, string>>({});
    const [selectedProjectKey, setSelectedProjectKey] = useState<string | null>(null);
    const [actionableTasks, setActionableTasks] = useState<ActionableTask[]>([]);
    const [actionablePagination, setActionablePagination] = useState<ActionablePagination>({
        total: 0,
        offset: 0,
        limit: ACTIONABLE_PAGE_SIZE,
        count: 0,
        has_more: false,
    });
    const [heartbeatCandidates, setHeartbeatCandidates] = useState<HeartbeatCandidate[]>([]);
    const [scheduledImmediateTasks, setScheduledImmediateTasks] = useState<ActionableTask[]>([]);
    const actionableScrollRef = useRef<HTMLDivElement | null>(null);

    const applySnapshot = useCallback((snapshot: ToDoListCacheSnapshot) => {
        setPipelineSummary(snapshot.pipelineSummary);
        setProjectIds(snapshot.projectIds || {});
        setSelectedProjectKey(snapshot.selectedProjectKey || null);
        setActionableTasks(snapshot.actionableTasks || []);
        setActionablePagination(
            snapshot.actionablePagination || {
                total: 0,
                offset: 0,
                limit: ACTIONABLE_PAGE_SIZE,
                count: 0,
                has_more: false,
            }
        );
        setHeartbeatCandidates(snapshot.heartbeatCandidates || []);
        setScheduledImmediateTasks(snapshot.scheduledImmediateTasks || []);
        setDiagnostics(snapshot.diagnostics || []);
        setError(snapshot.error || "");
    }, []);

    const load = useCallback(async (opts?: { background?: boolean; projectKeyOverride?: string | null }) => {
        const background = Boolean(opts?.background);
        const effectiveProjectKey = opts?.projectKeyOverride !== undefined ? opts.projectKeyOverride : null;
        if (background) {
            setRefreshing(true);
        } else {
            setLoading(true);
        }
        try {
            const actionableEndpoint = buildActionableEndpoint({
                offset: 0,
                limit: ACTIONABLE_PAGE_SIZE,
                projectKey: effectiveProjectKey,
            });
            const requests = await Promise.all([
                fetch(ENDPOINTS.pipeline),
                fetch(actionableEndpoint),
                fetch(ENDPOINTS.heartbeat),
                fetch(ENDPOINTS.immediateScheduled),
            ]);
            const [pipeRes, actRes, hbRes, scheduledRes] = requests;

            const parsePayload = async (res: Response, endpoint: string) => {
                if (!res.ok) {
                    let detail = `HTTP ${res.status}`;
                    try {
                        const errPayload = await res.json();
                        if (typeof errPayload?.detail === "string" && errPayload.detail) {
                            detail = `${detail}: ${errPayload.detail}`;
                        }
                    } catch {
                        // Response may not be JSON; keep HTTP detail only.
                    }
                    return {
                        payload: null,
                        diagnostic: { endpoint, ok: false, status: res.status, detail } as EndpointDiagnostic,
                    };
                }
                const payload = await res.json();
                return {
                    payload,
                    diagnostic: { endpoint, ok: true, status: res.status } as EndpointDiagnostic,
                };
            };

            const [pipeParsed, actParsed, hbParsed, scheduledParsed] = await Promise.all([
                parsePayload(pipeRes, ENDPOINTS.pipeline),
                parsePayload(actRes, actionableEndpoint),
                parsePayload(hbRes, ENDPOINTS.heartbeat),
                parsePayload(scheduledRes, ENDPOINTS.immediateScheduled),
            ]);

            setDiagnostics([pipeParsed.diagnostic, actParsed.diagnostic, hbParsed.diagnostic, scheduledParsed.diagnostic]);

            const pipePayload = pipeParsed.payload;
            const actPayload = actParsed.payload;
            const hbPayload = hbParsed.payload;
            const scheduledPayload = scheduledParsed.payload;

            const cached = memoryCache;
            let nextPipelineSummary: PipelineSummary | null = cached?.pipelineSummary || null;
            let nextProjectIds: Record<string, string> = cached?.projectIds || {};
            let nextActionableTasks: ActionableTask[] = cached?.actionableTasks || [];
            let nextActionablePagination: ActionablePagination = cached?.actionablePagination || {
                total: 0,
                offset: 0,
                limit: ACTIONABLE_PAGE_SIZE,
                count: 0,
                has_more: false,
            };
            let nextHeartbeatCandidates: HeartbeatCandidate[] = cached?.heartbeatCandidates || [];
            let nextScheduledImmediateTasks: ActionableTask[] = cached?.scheduledImmediateTasks || [];

            if (pipePayload?.status === "ok" && pipePayload.pipeline_summary) {
                nextPipelineSummary = pipePayload.pipeline_summary as PipelineSummary;
                setPipelineSummary(nextPipelineSummary);
                if (pipePayload.project_ids && typeof pipePayload.project_ids === "object") {
                    nextProjectIds = pipePayload.project_ids as Record<string, string>;
                    setProjectIds(nextProjectIds);
                }
            }
            if (actPayload?.status === "ok" && Array.isArray(actPayload.actionable_tasks)) {
                nextActionableTasks = actPayload.actionable_tasks as ActionableTask[];
                nextActionablePagination = {
                    total: Number(actPayload?.pagination?.total || nextActionableTasks.length || 0),
                    offset: Number(actPayload?.pagination?.offset || 0),
                    limit: Number(actPayload?.pagination?.limit || ACTIONABLE_PAGE_SIZE),
                    count: Number(actPayload?.pagination?.count || nextActionableTasks.length || 0),
                    has_more: Boolean(actPayload?.pagination?.has_more),
                };
                setActionableTasks(nextActionableTasks);
                setActionablePagination(nextActionablePagination);
            }
            if (hbPayload?.status === "ok" && Array.isArray(hbPayload.heartbeat_candidates)) {
                nextHeartbeatCandidates = hbPayload.heartbeat_candidates as HeartbeatCandidate[];
                setHeartbeatCandidates(nextHeartbeatCandidates);
            }
            if (scheduledPayload?.status === "ok" && Array.isArray(scheduledPayload.tasks)) {
                nextScheduledImmediateTasks = scheduledPayload.tasks as ActionableTask[];
                setScheduledImmediateTasks(nextScheduledImmediateTasks);
            }
            const failed = [pipeParsed.diagnostic, actParsed.diagnostic, hbParsed.diagnostic, scheduledParsed.diagnostic].filter((item) => !item.ok);
            const nextDiagnostics = [pipeParsed.diagnostic, actParsed.diagnostic, hbParsed.diagnostic, scheduledParsed.diagnostic];
            setDiagnostics(nextDiagnostics);
            if (failed.length > 0) {
                setError("One or more To Do List API requests failed. See diagnostics below.");
            } else {
                setError("");
            }

            const snapshot: ToDoListCacheSnapshot = {
                savedAt: new Date().toISOString(),
                pipelineSummary: nextPipelineSummary,
                projectIds: nextProjectIds,
                selectedProjectKey: effectiveProjectKey || null,
                actionableTasks: nextActionableTasks,
                actionablePagination: nextActionablePagination,
                heartbeatCandidates: nextHeartbeatCandidates,
                scheduledImmediateTasks: nextScheduledImmediateTasks,
                diagnostics: nextDiagnostics,
                error: failed.length > 0 ? "One or more To Do List API requests failed. See diagnostics below." : "",
            };
            memoryCache = snapshot;
            if (typeof window !== "undefined") {
                window.sessionStorage.setItem(TODOLIST_CACHE_KEY, JSON.stringify(snapshot));
            }
        } catch (err: any) {
            setError(err?.message || "Failed to load To Do List data.");
        } finally {
            setLoading(false);
            setRefreshing(false);
        }
    }, []);

    const loadMoreActionable = useCallback(async () => {
        if (loadingMore || !actionablePagination.has_more) return;
        setLoadingMore(true);
        try {
            const endpoint = buildActionableEndpoint({
                offset: actionableTasks.length,
                limit: ACTIONABLE_PAGE_SIZE,
                projectKey: selectedProjectKey,
            });
            const response = await fetch(endpoint, { cache: "no-store" });
            if (!response.ok) {
                setError(`Failed loading additional tasks: HTTP ${response.status}`);
                return;
            }
            const payload = await response.json();
            if (payload?.status !== "ok" || !Array.isArray(payload.actionable_tasks)) {
                setError("Failed loading additional tasks: invalid payload");
                return;
            }

            const nextPage = payload.actionable_tasks as ActionableTask[];
            const merged = [...actionableTasks, ...nextPage];
            const dedupedMap = new Map<string, ActionableTask>();
            for (const task of merged) {
                const taskId = String(task.id || "");
                if (!taskId) continue;
                if (!dedupedMap.has(taskId)) dedupedMap.set(taskId, task);
            }
            const deduped = Array.from(dedupedMap.values());
            const nextPagination: ActionablePagination = {
                total: Number(payload?.pagination?.total || deduped.length || 0),
                offset: Number(payload?.pagination?.offset || actionableTasks.length),
                limit: Number(payload?.pagination?.limit || ACTIONABLE_PAGE_SIZE),
                count: Number(payload?.pagination?.count || nextPage.length || 0),
                has_more: Boolean(payload?.pagination?.has_more),
            };
            setActionableTasks(deduped);
            setActionablePagination(nextPagination);
            setError((prev) => (prev.startsWith("Failed loading additional tasks") ? "" : prev));

            const updatedSnapshot: ToDoListCacheSnapshot = {
                savedAt: new Date().toISOString(),
                pipelineSummary,
                projectIds,
                selectedProjectKey,
                actionableTasks: deduped,
                actionablePagination: nextPagination,
                heartbeatCandidates,
                scheduledImmediateTasks,
                diagnostics,
                error,
            };
            memoryCache = updatedSnapshot;
            if (typeof window !== "undefined") {
                window.sessionStorage.setItem(TODOLIST_CACHE_KEY, JSON.stringify(updatedSnapshot));
            }
        } catch (err: any) {
            setError(err?.message || "Failed loading additional tasks.");
        } finally {
            setLoadingMore(false);
        }
    }, [
        actionablePagination.has_more,
        actionableTasks,
        diagnostics,
        error,
        heartbeatCandidates,
        loadingMore,
        pipelineSummary,
        projectIds,
        scheduledImmediateTasks,
        selectedProjectKey,
    ]);

    useEffect(() => {
        let hydrated = false;
        let initialProjectKey: string | null = null;
        const now = Date.now();
        const inMemory = memoryCache;
        if (inMemory) {
            const age = now - Date.parse(inMemory.savedAt || "");
            if (Number.isFinite(age) && age <= CACHE_MAX_AGE_MS) {
                applySnapshot(inMemory);
                initialProjectKey = inMemory.selectedProjectKey || null;
                setLoading(false);
                hydrated = true;
            }
        }
        if (!hydrated && typeof window !== "undefined") {
            const raw = window.sessionStorage.getItem(TODOLIST_CACHE_KEY);
            if (raw) {
                try {
                    const parsed = JSON.parse(raw) as ToDoListCacheSnapshot;
                    const age = now - Date.parse(parsed.savedAt || "");
                    if (Number.isFinite(age) && age <= CACHE_MAX_AGE_MS) {
                        applySnapshot(parsed);
                        initialProjectKey = parsed.selectedProjectKey || null;
                        memoryCache = parsed;
                        setLoading(false);
                        hydrated = true;
                    }
                } catch {
                    // ignore invalid cache
                }
            }
        }
        void load({ background: hydrated, projectKeyOverride: initialProjectKey });
    }, [applySnapshot, load]);

    const getPriorityInfo = (priority: number | string | null | undefined) => {
        let normalized = 1;
        if (typeof priority === "number") {
            normalized = priority;
        } else if (typeof priority === "string") {
            const upper = priority.toUpperCase();
            if (upper.startsWith("P1")) normalized = 4;
            else if (upper.startsWith("P2")) normalized = 3;
            else if (upper.startsWith("P3")) normalized = 2;
            else normalized = 1;
        }
        switch (normalized) {
            case 4: return { label: "Urgent", color: "text-rose-400 border-rose-400/30 bg-rose-400/10" };
            case 3: return { label: "High", color: "text-amber-400 border-amber-400/30 bg-amber-400/10" };
            case 2: return { label: "Medium", color: "text-sky-400 border-sky-400/30 bg-sky-400/10" };
            default: return { label: "Normal", color: "text-slate-400 border-slate-700 bg-slate-800/50" };
        }
    };

    const proactiveSections = pipelineSummary
        ? (pipelineSummary["UA: Proactive Intelligence__sections"] as Record<string, number> | undefined)
        : undefined;
    const pipelineTaskCount = UA_PROJECTS.reduce((acc, project) => {
        const value = pipelineSummary?.[project.key];
        return typeof value === "number" ? acc + value : acc;
    }, 0);
    const hasCountMismatch = pipelineTaskCount !== actionablePagination.total;
    const showCountMismatch = !selectedProjectKey && hasCountMismatch;
    const selectedProjectLabel = selectedProjectKey
        ? UA_PROJECTS.find((project) => project.key === selectedProjectKey)?.label || selectedProjectKey
        : "All Projects";
    const actionableVirtualizer = useVirtualizer({
        count: actionableTasks.length,
        getScrollElement: () => actionableScrollRef.current,
        estimateSize: () => 180,
        overscan: 8,
    });
    const virtualActionableRows = actionableVirtualizer.getVirtualItems();
    const actionableVirtualHeight = actionableVirtualizer.getTotalSize();

    useEffect(() => {
        actionableVirtualizer.measure();
    }, [actionableTasks.length, actionableVirtualizer]);

    useEffect(() => {
        if (actionablePagination.offset === 0) {
            actionableVirtualizer.scrollToOffset(0);
        }
    }, [actionablePagination.offset, actionableVirtualizer]);

    if (loading) {
        return (
            <div className="flex h-full items-center justify-center p-6 text-slate-400">
                Loading To Do List integration data...
            </div>
        );
    }

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
                    onClick={() => { void load({ background: true, projectKeyOverride: selectedProjectKey }); }}
                    className="rounded-md border border-slate-700 bg-slate-800/80 px-3 py-1.5 text-sm font-medium text-slate-300 transition hover:bg-slate-700 hover:text-white"
                >
                    {refreshing ? "Refreshing..." : "Refresh"}
                </button>
            </div>

            {error && (
                <div className="rounded-lg border border-red-900/50 bg-red-900/20 px-4 py-3 text-sm text-red-200">
                    {error}
                </div>
            )}
            {diagnostics.length > 0 && diagnostics.some((item) => !item.ok) && (
                <div className="rounded-lg border border-amber-900/50 bg-amber-950/30 px-4 py-3 text-xs text-amber-200">
                    <div className="mb-2 font-semibold uppercase tracking-wide text-amber-300">API Diagnostics</div>
                    <div className="space-y-1">
                        {diagnostics.map((item) => (
                            <div key={item.endpoint} className="font-mono">
                                [{item.ok ? "ok" : "error"}] {item.endpoint} {item.status ? `(${item.status})` : ""}{item.detail ? ` - ${item.detail}` : ""}
                            </div>
                        ))}
                    </div>
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

                        const isSelected = selectedProjectKey === project.key;
                        return (
                            <button
                                key={project.key}
                                type="button"
                                onClick={() => {
                                    const nextProjectKey = selectedProjectKey === project.key ? null : project.key;
                                    setSelectedProjectKey(nextProjectKey);
                                    void load({ background: true, projectKeyOverride: nextProjectKey });
                                }}
                                className={`rounded-xl border p-4 flex flex-col gap-2 text-left transition ${project.accent} ${
                                    isSelected ? "ring-2 ring-cyan-400/70" : "hover:ring-1 hover:ring-cyan-500/40"
                                }`}
                                title={isSelected ? "Click to clear filter" : `Filter actionable queue by ${project.label}`}
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
                            </button>
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
                    🔥 Live Actionable Queue ({actionableTasks.length}/{actionablePagination.total || actionableTasks.length})
                </h2>
                {selectedProjectKey && (
                    <div className="mb-3 flex items-center gap-2 text-xs text-cyan-300">
                        <span className="rounded border border-cyan-800/70 bg-cyan-900/20 px-2 py-0.5">
                            Filter: {selectedProjectLabel}
                        </span>
                        <button
                            type="button"
                            onClick={() => {
                                setSelectedProjectKey(null);
                                void load({ background: true, projectKeyOverride: null });
                            }}
                            className="rounded border border-slate-700 bg-slate-800/80 px-2 py-0.5 text-[11px] text-slate-300 hover:bg-slate-700"
                        >
                            Clear
                        </button>
                    </div>
                )}
                {showCountMismatch && (
                    <div className="mb-3 rounded border border-amber-900/50 bg-amber-950/20 px-3 py-2 text-xs text-amber-200">
                        Pipeline task count is {pipelineTaskCount}, while actionable queue is {actionablePagination.total}. Actionable queue only includes `@agent-ready` and unblocked tasks, sorted newest → oldest.
                    </div>
                )}
                {actionableTasks.length === 0 ? (
                    <p className="text-sm text-slate-500 italic">No tasks are currently marked @agent-ready and unblocked.</p>
                ) : (
                    <div className="space-y-3">
                        <div className="text-xs text-slate-400">
                            Showing <span className="font-semibold text-slate-200">{actionableTasks.length}</span> of{" "}
                            <span className="font-semibold text-slate-200">{actionablePagination.total || actionableTasks.length}</span>{" "}
                            actionable tasks (newest → oldest)
                        </div>
                        <div
                            ref={actionableScrollRef}
                            className="max-h-[68vh] min-h-[18rem] overflow-y-auto rounded-lg border border-slate-800/80 bg-slate-950/20 p-2"
                        >
                            <div
                                className="relative w-full"
                                style={{
                                    height: `${actionableVirtualHeight}px`,
                                }}
                            >
                                {virtualActionableRows.map((virtualRow) => {
                                    const task = actionableTasks[virtualRow.index];
                                    if (!task) return null;
                                    const pInfo = getPriorityInfo(task.priority);
                                    const csiLinks = deriveCsiTaskLinks(task);
                                    return (
                                        <div
                                            key={task.id || String(virtualRow.key)}
                                            data-index={virtualRow.index}
                                            ref={actionableVirtualizer.measureElement}
                                            className="absolute left-0 top-0 w-full pb-3"
                                            style={{
                                                transform: `translateY(${virtualRow.start}px)`,
                                            }}
                                        >
                                            <article className="flex flex-col gap-2 rounded-lg border border-slate-800/80 bg-slate-950/60 p-3">
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
                                                        {csiLinks.isCsiTask && csiLinks.isInteresting && (
                                                            <div className="mt-2 flex flex-wrap items-center gap-1.5">
                                                                {csiLinks.reportHref && (
                                                                    <a
                                                                        href={csiLinks.reportHref}
                                                                        className="rounded border border-emerald-800/60 bg-emerald-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-emerald-300 hover:bg-emerald-900/35"
                                                                    >
                                                                        Open Report
                                                                    </a>
                                                                )}
                                                                {csiLinks.notificationHref && (
                                                                    <a
                                                                        href={csiLinks.notificationHref}
                                                                        className="rounded border border-sky-800/60 bg-sky-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-sky-300 hover:bg-sky-900/35"
                                                                    >
                                                                        Open CSI Event
                                                                    </a>
                                                                )}
                                                                {csiLinks.artifactHref && (
                                                                    <a
                                                                        href={csiLinks.artifactHref}
                                                                        className="rounded border border-violet-800/60 bg-violet-900/20 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-violet-300 hover:bg-violet-900/35"
                                                                    >
                                                                        Open Artifact
                                                                    </a>
                                                                )}
                                                                <a
                                                                    href={csiLinks.csiFeedHref}
                                                                    className="rounded border border-slate-700 bg-slate-900/80 px-2 py-1 text-[10px] font-semibold uppercase tracking-wide text-slate-300 hover:bg-slate-800"
                                                                >
                                                                    CSI Feed
                                                                </a>
                                                            </div>
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
                                                    {(task.due?.date || task.due_datetime || task.due_date) && (
                                                        <span className="text-amber-500/80">Due: {task.due_datetime || task.due_date || task.due?.date}</span>
                                                    )}
                                                    <span className="ml-auto text-[10px]">
                                                        Created: {formatDistanceToNow(parseISO(task.created_at), { addSuffix: true })}
                                                    </span>
                                                </div>
                                            </article>
                                        </div>
                                    );
                                })}
                            </div>
                        </div>
                        <div className="flex items-center justify-between rounded border border-slate-800/80 bg-slate-950/40 px-3 py-2 text-xs text-slate-400">
                            <span>
                                Loaded {actionableTasks.length} / {actionablePagination.total || actionableTasks.length}
                            </span>
                            {actionablePagination.has_more ? (
                                <button
                                    type="button"
                                    onClick={() => { void loadMoreActionable(); }}
                                    disabled={loadingMore}
                                    className="rounded border border-cyan-800/60 bg-cyan-900/20 px-2.5 py-1 text-[11px] font-semibold text-cyan-200 hover:bg-cyan-900/35 disabled:cursor-not-allowed disabled:opacity-60"
                                >
                                    {loadingMore ? "Loading..." : `Load ${ACTIONABLE_PAGE_SIZE} More`}
                                </button>
                            ) : (
                                <span className="text-[11px] text-slate-500">All actionable tasks loaded</span>
                            )}
                        </div>
                    </div>
                )}
            </section>

            {/* ── PERSONAL SCHEDULED REMINDERS ── */}
            <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                <h2 className="mb-2 text-sm font-semibold uppercase tracking-[0.16em] text-indigo-300">
                    🛌 Personal Scheduled Reminders ({scheduledImmediateTasks.length})
                </h2>
                <p className="mb-3 text-xs text-slate-400">
                    UA: Immediate Queue → Scheduled · non-`agent-ready` items (personal handoff reminders)
                </p>
                {scheduledImmediateTasks.length === 0 ? (
                    <p className="text-sm text-slate-500 italic">No scheduled personal reminders found.</p>
                ) : (
                    <div className="space-y-3">
                        {scheduledImmediateTasks.map((task) => {
                            const pInfo = getPriorityInfo(task.priority);
                            const dueText = task.due_datetime || task.due_date || task.due?.datetime || task.due?.date || "";
                            return (
                                <article key={task.id} className="rounded-lg border border-slate-800/80 bg-slate-950/60 p-3">
                                    <div className="flex items-start justify-between gap-3">
                                        <div>
                                            <h3 className="font-semibold text-slate-200">
                                                <a href={task.url} target="_blank" rel="noreferrer" className="hover:underline hover:text-cyan-400">
                                                    {task.content}
                                                </a>
                                            </h3>
                                            {task.description && (
                                                <p className="mt-1 text-xs text-slate-400 line-clamp-3">{task.description}</p>
                                            )}
                                        </div>
                                        <span className={`shrink-0 rounded border px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider ${pInfo.color}`}>
                                            {pInfo.label}
                                        </span>
                                    </div>
                                    <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-slate-500">
                                        {task.labels.map((label) => (
                                            <span key={`${task.id}-${label}`} className="rounded bg-slate-800/50 px-1.5 py-0.5 text-[10px]">
                                                @{label}
                                            </span>
                                        ))}
                                        {dueText && (
                                            <span className="text-indigo-300/90">Due: {dueText}</span>
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
