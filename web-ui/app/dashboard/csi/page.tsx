"use client";

import { useCallback, useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatDateTimeTz, toEpochMs } from "@/lib/timezone";

type CSIReport = {
    id: number | string;
    report_type: string;
    report_class?: string;
    window_hours?: number | null;
    source_mix?: Record<string, number>;
    divergence_score?: number;
    divergence_note?: string;
    metadata?: Record<string, any>;
    report_data: any;
    usage: any;
    created_at: string;
};

type PipelineNotification = {
    id: string;
    kind: string;
    title: string;
    message: string;
    summary?: string;
    full_message?: string;
    severity: string;
    status?: string;
    created_at: string;
    metadata?: any;
};

type SelectedItem =
    | { type: "report"; data: CSIReport }
    | { type: "notification"; data: PipelineNotification }
    | null;

type CSIHealth = {
    status: string;
    stale_pipelines?: Array<{ event_type: string }>;
    undelivered_last_24h?: number;
    dead_letter_last_24h?: number;
    timezone?: string;
    source_health?: Array<{
        source: string;
        status: string;
        lag_minutes?: number | null;
        events_last_48h?: number;
        events_last_6h?: number;
        throughput_per_hour_6h?: number;
        failures_last_24h?: number;
        last_seen?: string | null;
    }>;
    overnight_continuity?: {
        window_start_utc?: string;
        window_end_utc?: string;
        window_start_local?: string;
        window_end_local?: string;
        checks?: Array<{
            event_type: string;
            expected_runs: number;
            observed_runs: number;
            missing_runs: number;
            status: string;
            expected_max_lag_minutes: number;
        }>;
    };
};

type CSISpecialistLoop = {
    topic_key: string;
    topic_label: string;
    status: string;
    confidence_target: number;
    confidence_score: number;
    follow_up_budget_remaining: number;
    events_count: number;
    updated_at: string;
};

type CSIOpportunity = {
    opportunity_id: string;
    title: string;
    thesis: string;
    source_mix?: Record<string, number>;
    evidence_refs?: string[];
    novelty_score?: number;
    confidence_score?: number;
    risk_flags?: string[];
    recommended_action?: string;
    followup_task_template?: string;
};

type CSIOpportunityBundle = {
    bundle_id: string;
    report_key?: string;
    window_start_utc?: string;
    window_end_utc?: string;
    confidence_method?: string;
    quality_summary?: {
        signal_volume?: number;
        freshness_minutes?: number;
        delivery_health?: string;
        coverage_score?: number;
    };
    source_mix?: Record<string, number>;
    opportunities?: CSIOpportunity[];
    artifact_paths?: { markdown?: string; json?: string };
    created_at: string;
};

const API_BASE = "/api/dashboard/gateway";

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

function timeAgo(dateStr: string): string {
    const ts = toEpochMs(dateStr);
    if (ts === null) return "--";
    const delta = Math.max(0, (Date.now() - ts) / 1000);
    if (delta < 60) return "just now";
    if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
    if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
    return `${Math.floor(delta / 86400)}d ago`;
}

function normalizeArtifactPath(rawPath: string): string {
    const cleaned = String(rawPath || "").trim().replace(/\\/g, "/");
    if (!cleaned) return "";
    if (cleaned.startsWith("/")) {
        const marker = "/artifacts/";
        const idx = cleaned.indexOf(marker);
        if (idx >= 0) return cleaned.slice(idx + marker.length).replace(/^\/+/, "");
        return cleaned.replace(/^\/+/, "");
    }
    if (cleaned.startsWith("artifacts/")) return cleaned.slice("artifacts/".length);
    return cleaned;
}

function artifactPathFromHref(href: string): string {
    const raw = String(href || "").trim();
    if (!raw) return "";
    if (raw.startsWith("/opt/universal_agent/artifacts/")) {
        return normalizeArtifactPath(raw);
    }
    if (raw.startsWith("artifacts/")) {
        return normalizeArtifactPath(raw);
    }
    try {
        const parsed = new URL(raw, "http://local");
        if (parsed.pathname.includes("/storage")) {
            const fromPreview = parsed.searchParams.get("preview");
            if (fromPreview) return normalizeArtifactPath(fromPreview);
            const fromPath = parsed.searchParams.get("path");
            if (fromPath) return normalizeArtifactPath(fromPath);
        }
        if (parsed.pathname.includes("/artifacts/files/")) {
            const rel = parsed.pathname.split("/artifacts/files/")[1] || "";
            return normalizeArtifactPath(decodeURIComponent(rel));
        }
    } catch {
        // Not a parseable URL; ignore.
    }
    return "";
}

export default function CSIDashboard() {
    const [reports, setReports] = useState<CSIReport[]>([]);
    const [notifications, setNotifications] = useState<PipelineNotification[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [selectedItem, setSelectedItem] = useState<SelectedItem>(null);
    const [purgeBusy, setPurgeBusy] = useState(false);
    const [purgeStatus, setPurgeStatus] = useState<string | null>(null);
    const [previewPath, setPreviewPath] = useState<string | null>(null);
    const [previewLabel, setPreviewLabel] = useState<string | null>(null);
    const [previewContent, setPreviewContent] = useState<string>("");
    const [previewLoading, setPreviewLoading] = useState(false);
    const [previewError, setPreviewError] = useState<string | null>(null);
    const [health, setHealth] = useState<CSIHealth | null>(null);
    const [loops, setLoops] = useState<CSISpecialistLoop[]>([]);
    const [opportunityBundles, setOpportunityBundles] = useState<CSIOpportunityBundle[]>([]);
    const [deepLinkApplied, setDeepLinkApplied] = useState(false);

    const openArtifactPreview = useCallback(async (path: string, label: string) => {
        const normalized = normalizeArtifactPath(path);
        if (!normalized) {
            setPreviewError("Invalid artifact path.");
            return;
        }
        setPreviewPath(normalized);
        setPreviewLabel(label);
        setPreviewLoading(true);
        setPreviewError(null);
        setPreviewContent("");
        try {
            const resp = await fetch(`${API_BASE}/api/vps/file?scope=artifacts&path=${encodeURIComponent(normalized)}`);
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            let text = await resp.text();
            if (normalized.toLowerCase().endsWith(".json")) {
                try {
                    text = JSON.stringify(JSON.parse(text), null, 2);
                } catch {
                    // Keep raw text if payload is not valid JSON.
                }
            }
            setPreviewContent(text);
        } catch (err: any) {
            setPreviewError(err?.message || "Failed to load artifact preview.");
        } finally {
            setPreviewLoading(false);
        }
    }, []);

    const loadData = useCallback(async () => {
        try {
            const [repRes, notifRes, healthRes, loopsRes, oppRes] = await Promise.all([
                fetch(`${API_BASE}/api/v1/dashboard/csi/reports`, { cache: "no-store" }),
                fetch(`${API_BASE}/api/v1/dashboard/notifications?limit=100&source_domain=csi`, { cache: "no-store" }),
                fetch(`${API_BASE}/api/v1/dashboard/csi/health`, { cache: "no-store" }),
                fetch(`${API_BASE}/api/v1/dashboard/csi/specialist-loops?limit=8`, { cache: "no-store" }),
                fetch(`${API_BASE}/api/v1/dashboard/csi/opportunities?limit=5`, { cache: "no-store" }),
            ]);

            if (!repRes.ok) throw new Error(`HTTP ${repRes.status}`);
            const data = await repRes.json();
            if (data.status === "error" || data.status === "unavailable") {
                throw new Error(data.detail || "CSI unavailable");
            }
            setReports(data.reports || []);

            if (notifRes.ok) {
                const ndata = await notifRes.json();
                if (Array.isArray(ndata.notifications)) {
                    const csiNotifs = ndata.notifications.filter(
                        (n: PipelineNotification) =>
                            (n.kind && n.kind.startsWith("csi")) || String(n.kind || "").includes("simone_handoff")
                    );
                    setNotifications(csiNotifs);
                }
            }
            if (healthRes.ok) {
                const hData = await healthRes.json();
                setHealth(hData as CSIHealth);
            }
            if (loopsRes.ok) {
                const loopsData = await loopsRes.json();
                if (Array.isArray(loopsData.loops)) {
                    setLoops(loopsData.loops as CSISpecialistLoop[]);
                }
            }
            if (oppRes.ok) {
                const oppData = await oppRes.json();
                if (Array.isArray(oppData.bundles)) {
                    setOpportunityBundles(oppData.bundles as CSIOpportunityBundle[]);
                } else {
                    setOpportunityBundles([]);
                }
            }
            setError(null);
        } catch (err: any) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void loadData();
        const timer = window.setInterval(() => void loadData(), 30000);
        return () => window.clearInterval(timer);
    }, [loadData]);

    useEffect(() => {
        if (deepLinkApplied || loading) return;
        const params = new URLSearchParams(window.location.search);
        const reportKey = String(params.get("report_key") || "").trim();
        const artifactPath = String(params.get("artifact_path") || "").trim();
        if (!reportKey && !artifactPath) {
            setDeepLinkApplied(true);
            return;
        }

        if (reportKey) {
            const matchedReport = reports.find((report) => String(report?.report_data?.report_key || report?.metadata?.report_key || "").trim() === reportKey);
            if (matchedReport) {
                setSelectedItem({ type: "report", data: matchedReport });
            }
        }

        if (artifactPath) {
            window.setTimeout(() => {
                void openArtifactPreview(artifactPath, "Linked Artifact");
            }, 0);
        }
        setDeepLinkApplied(true);
    }, [deepLinkApplied, loading, openArtifactPreview, reports]);

    useEffect(() => {
        setPreviewPath(null);
        setPreviewLabel(null);
        setPreviewContent("");
        setPreviewError(null);
        setPreviewLoading(false);
    }, [selectedItem?.type, selectedItem?.data?.id]);

    const totalReports = reports.length;
    const lastReportTime = reports.length > 0 ? formatDateTimeTz(reports[0].created_at, { placeholder: "N/A" }) : "N/A";
    const latestBundle = opportunityBundles.length > 0 ? opportunityBundles[0] : null;

    async function purgeCsiSessions() {
        if (!confirm("Purge older CSI hook sessions and keep only the latest 2?")) return;
        setPurgeBusy(true);
        setPurgeStatus(null);
        try {
            const resp = await fetch(`${API_BASE}/api/v1/ops/sessions/csi/purge`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({
                    dry_run: false,
                    keep_latest: 2,
                    older_than_minutes: 30,
                    include_active: false,
                }),
            });
            const payload = await resp.json().catch(() => ({}));
            if (!resp.ok) {
                const detail = (payload && payload.detail) ? String(payload.detail) : `HTTP ${resp.status}`;
                throw new Error(detail);
            }
            const deletedCount = Array.isArray(payload.deleted) ? payload.deleted.length : 0;
            setPurgeStatus(`CSI session cleanup complete. Deleted ${deletedCount} session(s).`);
            window.setTimeout(() => window.location.reload(), 900);
        } catch (err: any) {
            setPurgeStatus(`CSI session cleanup failed: ${err.message || "unknown error"}`);
        } finally {
            setPurgeBusy(false);
        }
    }

    return (
        <div className="flex h-full flex-col gap-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight text-slate-100">Creator Signal Intelligence (CSI)</h1>
                    <p className="text-sm text-slate-400">Automated insight and trend analysis across defined watchlists.</p>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={purgeCsiSessions}
                        disabled={purgeBusy}
                        className="rounded-md bg-amber-600/20 px-3 py-1.5 text-sm font-medium text-amber-200 hover:bg-amber-600/30 transition-colors border border-amber-500/30 disabled:opacity-60"
                    >
                        {purgeBusy ? "Cleaning..." : "Clean CSI Sessions"}
                    </button>
                    <button
                        onClick={() => void loadData()}
                        className="rounded-md bg-cyan-600/20 px-3 py-1.5 text-sm font-medium text-cyan-300 hover:bg-cyan-600/30 transition-colors border border-cyan-500/30"
                    >
                        Refresh Feed
                    </button>
                </div>
            </div>
            {purgeStatus && (
                <div className="rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-300">
                    {purgeStatus}
                </div>
            )}

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
                <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 shadow-sm backdrop-blur">
                    <div className="text-sm font-medium text-slate-400">Total Reports (Recent)</div>
                    <div className="mt-2 flex items-baseline gap-2">
                        <span className="text-3xl font-bold text-slate-100">{loading ? "..." : totalReports}</span>
                    </div>
                </div>
                <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 shadow-sm backdrop-blur">
                    <div className="text-sm font-medium text-slate-400">Latest Report Time</div>
                    <div className="mt-2 flex items-baseline gap-2">
                        <span className="text-lg font-bold text-slate-100">{loading ? "..." : lastReportTime}</span>
                    </div>
                </div>
                <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 shadow-sm backdrop-blur">
                    <div className="text-sm font-medium text-slate-400">CSI Pipeline Health</div>
                    <div className="mt-2 flex items-center gap-2">
                        <span className={`text-lg font-bold ${(health?.stale_pipelines?.length || 0) > 0 ? "text-amber-300" : "text-emerald-300"}`}>
                            {loading ? "..." : `${health?.stale_pipelines?.length || 0} stale`}
                        </span>
                    </div>
                    <div className="mt-1 text-xs text-slate-500">
                        DLQ 24h: {health?.dead_letter_last_24h ?? 0} | Undelivered 24h: {health?.undelivered_last_24h ?? 0}
                    </div>
                </div>
                <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 shadow-sm backdrop-blur">
                    <div className="text-sm font-medium text-slate-400">Latest Opportunity Bundle</div>
                    <div className="mt-2 flex items-center gap-2">
                        <span className="text-lg font-bold text-cyan-200">
                            {loading ? "..." : `${latestBundle?.opportunities?.length || 0} ranked`}
                        </span>
                    </div>
                    <div className="mt-1 text-xs text-slate-500">
                        Method: {latestBundle?.confidence_method || "n/a"} | Coverage: {latestBundle?.quality_summary?.coverage_score ?? 0}
                    </div>
                </div>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 shadow-sm backdrop-blur">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="text-sm font-semibold tracking-wide text-slate-300">CSI Overnight Continuity</h2>
                    <span className="text-[11px] text-slate-500">
                        Timezone: {health?.timezone || "UTC"}
                    </span>
                </div>
                <div className="mt-1 text-[11px] text-slate-500">
                    Local window: {health?.overnight_continuity?.window_start_local ? formatDateTimeTz(health.overnight_continuity.window_start_local, { placeholder: "--" }) : "--"} →{" "}
                    {health?.overnight_continuity?.window_end_local ? formatDateTimeTz(health.overnight_continuity.window_end_local, { placeholder: "--" }) : "--"}
                </div>
                <div className="mt-3 grid gap-4 xl:grid-cols-2">
                    <div className="overflow-auto rounded border border-slate-800">
                        <table className="w-full text-left text-xs">
                            <thead className="bg-slate-900/70 text-slate-400">
                                <tr>
                                    <th className="px-2 py-1.5">Pipeline</th>
                                    <th className="px-2 py-1.5">Expected</th>
                                    <th className="px-2 py-1.5">Observed</th>
                                    <th className="px-2 py-1.5">Missing</th>
                                </tr>
                            </thead>
                            <tbody>
                                {(health?.overnight_continuity?.checks || []).slice(0, 12).map((row) => (
                                    <tr key={`overnight-${row.event_type}`} className="border-t border-slate-800/60">
                                        <td className="px-2 py-1.5 text-slate-300">{row.event_type}</td>
                                        <td className="px-2 py-1.5 text-slate-400">{row.expected_runs}</td>
                                        <td className="px-2 py-1.5 text-slate-400">{row.observed_runs}</td>
                                        <td className={`px-2 py-1.5 ${row.missing_runs > 0 ? "text-amber-300" : "text-emerald-300"}`}>
                                            {row.missing_runs}
                                        </td>
                                    </tr>
                                ))}
                                {(!health?.overnight_continuity?.checks || health?.overnight_continuity?.checks?.length === 0) && (
                                    <tr>
                                        <td className="px-2 py-2 text-slate-500" colSpan={4}>
                                            No overnight continuity checks available yet.
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>

                    <div className="overflow-auto rounded border border-slate-800">
                        <table className="w-full text-left text-xs">
                            <thead className="bg-slate-900/70 text-slate-400">
                                <tr>
                                    <th className="px-2 py-1.5">Source</th>
                                    <th className="px-2 py-1.5">Status</th>
                                    <th className="px-2 py-1.5">Lag (min)</th>
                                    <th className="px-2 py-1.5">6h / hr</th>
                                </tr>
                            </thead>
                            <tbody>
                                {(health?.source_health || []).slice(0, 10).map((row) => (
                                    <tr key={`source-${row.source}`} className="border-t border-slate-800/60">
                                        <td className="px-2 py-1.5 text-slate-300">{row.source}</td>
                                        <td
                                            className={`px-2 py-1.5 ${
                                                row.status === "ok" ? "text-emerald-300" : row.status === "degraded" ? "text-amber-300" : "text-rose-300"
                                            }`}
                                        >
                                            {row.status}
                                        </td>
                                        <td className="px-2 py-1.5 text-slate-400">
                                            {typeof row.lag_minutes === "number" ? row.lag_minutes : "--"}
                                        </td>
                                        <td className="px-2 py-1.5 text-slate-400">
                                            {row.events_last_6h ?? 0} / {row.throughput_per_hour_6h ?? 0}
                                        </td>
                                    </tr>
                                ))}
                                {(!health?.source_health || health?.source_health?.length === 0) && (
                                    <tr>
                                        <td className="px-2 py-2 text-slate-500" colSpan={4}>
                                            No source health samples available yet.
                                        </td>
                                    </tr>
                                )}
                            </tbody>
                        </table>
                    </div>
                </div>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 shadow-sm backdrop-blur">
                <h2 className="text-sm font-semibold tracking-wide text-slate-300">Trend Specialist Loops</h2>
                <div className="mt-2 overflow-auto rounded border border-slate-800">
                    <table className="w-full text-left text-xs">
                        <thead className="bg-slate-900/70 text-slate-400">
                            <tr>
                                <th className="px-2 py-1.5">Topic</th>
                                <th className="px-2 py-1.5">Status</th>
                                <th className="px-2 py-1.5">Confidence</th>
                                <th className="px-2 py-1.5">Budget</th>
                            </tr>
                        </thead>
                        <tbody>
                            {loops.map((loop) => (
                                <tr key={loop.topic_key} className="border-t border-slate-800/60">
                                    <td className="px-2 py-1.5 text-slate-300">{loop.topic_label}</td>
                                    <td
                                        className={`px-2 py-1.5 ${
                                            loop.status === "closed" ? "text-emerald-300" : loop.status === "budget_exhausted" ? "text-amber-300" : "text-sky-300"
                                        }`}
                                    >
                                        {loop.status}
                                    </td>
                                    <td className="px-2 py-1.5 text-slate-400">
                                        {loop.confidence_score} / {loop.confidence_target}
                                    </td>
                                    <td className="px-2 py-1.5 text-slate-400">{loop.follow_up_budget_remaining}</td>
                                </tr>
                            ))}
                            {loops.length === 0 && (
                                <tr>
                                    <td className="px-2 py-2 text-slate-500" colSpan={4}>
                                        No specialist loops tracked yet.
                                    </td>
                                </tr>
                            )}
                        </tbody>
                    </table>
                </div>
            </div>

            <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 shadow-sm backdrop-blur">
                <div className="flex flex-wrap items-center justify-between gap-2">
                    <h2 className="text-sm font-semibold tracking-wide text-slate-300">Latest Opportunity Bundle</h2>
                    <span className="text-[11px] text-slate-500">
                        {latestBundle?.created_at ? formatDateTimeTz(latestBundle.created_at, { placeholder: "--" }) : "No bundle yet"}
                    </span>
                </div>
                {!latestBundle ? (
                    <div className="mt-3 text-xs text-slate-500">No opportunity bundles available yet.</div>
                ) : (
                    <div className="mt-3 space-y-3">
                        <div className="rounded border border-slate-800 bg-slate-950/60 px-3 py-2 text-xs text-slate-300">
                            <div>Bundle: <span className="font-mono text-slate-400">{latestBundle.bundle_id || "--"}</span></div>
                            <div className="mt-1">
                                Window: {latestBundle.window_start_utc ? formatDateTimeTz(latestBundle.window_start_utc, { placeholder: "--" }) : "--"} →{" "}
                                {latestBundle.window_end_utc ? formatDateTimeTz(latestBundle.window_end_utc, { placeholder: "--" }) : "--"}
                            </div>
                            <div className="mt-1">
                                Signal volume: {latestBundle.quality_summary?.signal_volume ?? 0} | Freshness (min): {latestBundle.quality_summary?.freshness_minutes ?? "--"} | Delivery: {latestBundle.quality_summary?.delivery_health || "--"}
                            </div>
                            <div className="mt-2 flex flex-wrap gap-2">
                                {latestBundle.artifact_paths?.markdown && (
                                    <button
                                        type="button"
                                        onClick={() => openArtifactPreview(latestBundle.artifact_paths?.markdown || "", "Opportunity Markdown")}
                                        className="rounded border border-cyan-700/40 bg-cyan-700/10 px-2 py-1 text-[11px] text-cyan-200 hover:bg-cyan-700/20"
                                    >
                                        Preview Bundle Markdown
                                    </button>
                                )}
                                {latestBundle.artifact_paths?.json && (
                                    <button
                                        type="button"
                                        onClick={() => openArtifactPreview(latestBundle.artifact_paths?.json || "", "Opportunity JSON")}
                                        className="rounded border border-slate-700 bg-slate-900/60 px-2 py-1 text-[11px] text-slate-300 hover:bg-slate-800/60"
                                    >
                                        Preview Bundle JSON
                                    </button>
                                )}
                            </div>
                        </div>
                        <div className="overflow-auto rounded border border-slate-800">
                            <table className="w-full text-left text-xs">
                                <thead className="bg-slate-900/70 text-slate-400">
                                    <tr>
                                        <th className="px-2 py-1.5">Opportunity</th>
                                        <th className="px-2 py-1.5">Confidence</th>
                                        <th className="px-2 py-1.5">Novelty</th>
                                        <th className="px-2 py-1.5">Sources</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {(latestBundle.opportunities || []).slice(0, 8).map((opp) => (
                                        <tr key={opp.opportunity_id || opp.title} className="border-t border-slate-800/60">
                                            <td className="px-2 py-1.5 text-slate-300">
                                                <div className="font-medium">{opp.title}</div>
                                                <div className="text-[11px] text-slate-500 line-clamp-2">{opp.thesis}</div>
                                            </td>
                                            <td className="px-2 py-1.5 text-slate-300">{opp.confidence_score ?? "--"}</td>
                                            <td className="px-2 py-1.5 text-slate-300">{opp.novelty_score ?? "--"}</td>
                                            <td className="px-2 py-1.5 text-slate-400">
                                                {Object.entries(opp.source_mix || {}).map(([k, v]) => `${k}:${v}`).join(", ") || "--"}
                                            </td>
                                        </tr>
                                    ))}
                                    {(latestBundle.opportunities || []).length === 0 && (
                                        <tr>
                                            <td className="px-2 py-2 text-slate-500" colSpan={4}>
                                                No opportunities ranked in latest bundle.
                                            </td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>
                    </div>
                )}
            </div>

            <div className="grid flex-1 grid-cols-1 gap-6 lg:grid-cols-3 min-h-0">
                {/* ── LEFT PANEL: Lists ── */}
                <div className="flex flex-col gap-6 lg:col-span-1 overflow-y-auto pr-2 scrollbar-thin">

                    {/* Pipeline Notifications List */}
                    <div className="rounded-xl border border-slate-800 bg-slate-900/50 shadow-sm backdrop-blur shrink-0">
                        <div className="border-b border-slate-800 bg-slate-900/80 px-4 py-3 sticky top-0 z-10">
                            <h2 className="text-sm font-semibold tracking-wide text-slate-300">
                                CSI Pipeline Notifications
                            </h2>
                        </div>
                        {notifications.length === 0 && !loading ? (
                            <div className="text-sm text-slate-400 py-4 px-4">No recent CSI notifications.</div>
                        ) : (
                            <div className="p-2 space-y-1">
                                {notifications.map((n) => {
                                    const style = SEVERITY_STYLES[n.severity] || SEVERITY_STYLES.info;
                                    const dot = SEVERITY_DOTS[n.severity] || SEVERITY_DOTS.info;
                                    const isSelected = selectedItem?.type === "notification" && selectedItem.data.id === n.id;
                                    return (
                                        <button
                                            key={n.id}
                                            onClick={() => setSelectedItem({ type: "notification", data: n })}
                                            className={`w-full text-left flex items-start gap-2 rounded border px-3 py-2 text-sm transition-all focus:outline-none focus:ring-1 focus:ring-cyan-500 hover:brightness-110 ${style} ${isSelected ? 'ring-1 ring-cyan-500 shadow-[0_0_10px_rgba(6,182,212,0.2)]' : ''}`}
                                        >
                                            <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
                                            <div className="min-w-0 flex-1">
                                                <div className="flex items-center justify-between">
                                                    <span className="font-medium pr-2">{n.title}</span>
                                                    <span className="text-[10px] opacity-70 shrink-0">{timeAgo(n.created_at)}</span>
                                                </div>
                                                <p className="mt-0.5 line-clamp-2 text-xs opacity-80">{n.summary || n.message}</p>
                                            </div>
                                        </button>
                                    );
                                })}
                            </div>
                        )}
                    </div>

                    {/* Reports List */}
                    <div className="rounded-xl border border-slate-800 bg-slate-900/50 shadow-sm backdrop-blur flex-1 flex flex-col min-h-[300px]">
                        <div className="border-b border-slate-800 bg-slate-900/80 px-4 py-3 sticky top-0 z-10">
                            <h2 className="text-sm font-semibold tracking-wide text-slate-300">Recent Insights & Trends</h2>
                        </div>

                        {loading ? (
                            <div className="p-8 text-center text-slate-400">Loading CSI feed...</div>
                        ) : error ? (
                            <div className="p-8 text-center text-rose-400">Error loading feed: {error}</div>
                        ) : reports.length === 0 ? (
                            <div className="p-8 text-center text-slate-400">No CSI reports found in the database.</div>
                        ) : (
                            <div className="divide-y divide-slate-800/60 overflow-y-auto">
                                {reports.map((report) => {
                                    const isSelected = selectedItem?.type === "report" && selectedItem.data.id === report.id;
                                    return (
                                        <button
                                            key={report.id}
                                            onClick={() => setSelectedItem({ type: "report", data: report })}
                                            className={`w-full text-left p-4 hover:bg-slate-800/40 transition-colors focus:outline-none ${isSelected ? 'bg-slate-800/60 border-l-2 border-l-cyan-500' : 'border-l-2 border-l-transparent'}`}
                                        >
                                            <div className="flex items-center justify-between mb-2">
                                                <span className="px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 text-xs font-medium lowercase tracking-wide">
                                                    {report.report_type}
                                                </span>
                                                <span className="text-[10px] text-slate-500">
                                                    {timeAgo(report.created_at)}
                                                </span>
                                            </div>
                                            <div className="text-sm text-slate-300 line-clamp-2">
                                                {report.report_data?.markdown_content || "Data payload (No markdown content)"}
                                            </div>
                                        </button>
                                    );
                                })}
                            </div>
                        )}
                    </div>
                </div>

                {/* ── RIGHT PANEL: Detail View ── */}
                <div className="lg:col-span-2 flex flex-col rounded-xl border border-slate-800 bg-slate-950/80 shadow-inner overflow-hidden min-h-[500px]">
                    {!selectedItem ? (
                        <div className="flex-1 flex flex-col items-center justify-center p-8 text-slate-500">
                            <svg className="w-12 h-12 mb-4 opacity-20" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
                            </svg>
                            <p>Select a notification or report from the left panel to view details.</p>
                        </div>
                    ) : selectedItem.type === "notification" ? (
                        <div className="flex flex-col h-full">
                            <div className="border-b border-slate-800 bg-slate-900 px-6 py-4 shrink-0">
                                <div className="flex items-center gap-2 mb-1">
                                    <span className={`px-2 py-0.5 rounded text-[10px] uppercase font-bold tracking-wider ${SEVERITY_STYLES[selectedItem.data.severity] || SEVERITY_STYLES.info}`}>
                                        {selectedItem.data.severity}
                                    </span>
                                    <span
                                        className="text-xs text-slate-500"
                                        title={`UTC: ${formatDateTimeTz(selectedItem.data.created_at, { timeZone: "UTC", placeholder: "--" })}`}
                                    >
                                        Local: {formatDateTimeTz(selectedItem.data.created_at, { placeholder: "--" })}
                                    </span>
                                </div>
                                <h2 className="text-lg font-semibold text-slate-100">{selectedItem.data.title}</h2>
                                <p className="text-xs text-slate-400 font-mono mt-1">ID: {selectedItem.data.id} | Kind: {selectedItem.data.kind}</p>
                                <div className="mt-2 flex gap-2">
                                    <button
                                        type="button"
                                        className="rounded border border-cyan-700/60 bg-cyan-600/15 px-2 py-1 text-[11px] text-cyan-100 hover:bg-cyan-600/25"
                                        onClick={async () => {
                                            const instruction = window.prompt("Add context for Simone:");
                                            if (!instruction || !instruction.trim()) return;
                                            const resp = await fetch(
                                                `${API_BASE}/api/v1/dashboard/activity/${encodeURIComponent(String(selectedItem.data.id))}/send-to-simone`,
                                                {
                                                    method: "POST",
                                                    headers: { "Content-Type": "application/json" },
                                                    body: JSON.stringify({ instruction: instruction.trim() }),
                                                },
                                            );
                                            if (!resp.ok) {
                                                const txt = await resp.text();
                                                alert(`Failed to send to Simone: ${txt}`);
                                                return;
                                            }
                                            alert("Sent to Simone.");
                                        }}
                                    >
                                        Send to Simone
                                    </button>
                                </div>
                            </div>
                            <div className="p-6 overflow-y-auto flex-1 scrollbar-thin">
                                <h3 className="text-sm font-medium text-slate-300 mb-2 uppercase tracking-wide">Message Content</h3>
                                <div className="bg-slate-900/50 border border-slate-800 rounded-lg p-4 mb-6 whitespace-pre-wrap text-sm text-slate-300">
                                    {selectedItem.data.full_message || selectedItem.data.message}
                                </div>

                                {selectedItem.data.metadata && Object.keys(selectedItem.data.metadata).length > 0 && (
                                    <>
                                        <h3 className="text-sm font-medium text-slate-300 mb-2 uppercase tracking-wide">Metadata</h3>
                                        <div className="bg-slate-950 border border-slate-800/80 rounded-lg p-4 flex flex-col gap-4">
                                            {selectedItem.data.metadata.artifact_paths && (
                                                <div className="space-y-1">
                                                    <h4 className="text-xs font-semibold text-slate-400">ARTIFACT FILES</h4>
                                                    {selectedItem.data.metadata.artifact_paths.markdown && (
                                                        <div className="flex flex-col gap-1">
                                                            <span className="text-[10px] text-slate-500">MARKDOWN</span>
                                                            <button
                                                                type="button"
                                                                onClick={() => openArtifactPreview(selectedItem.data.metadata.artifact_paths.markdown, "Markdown")}
                                                                className="text-left text-xs text-cyan-400 border border-slate-800 bg-slate-900/50 rounded px-2 py-1 font-mono break-all hover:border-cyan-500/40 hover:bg-cyan-950/10 transition-colors"
                                                                title="Preview markdown artifact"
                                                            >
                                                                {selectedItem.data.metadata.artifact_paths.markdown}
                                                            </button>
                                                        </div>
                                                    )}
                                                    {selectedItem.data.metadata.artifact_paths.json && (
                                                        <div className="flex flex-col gap-1">
                                                            <span className="text-[10px] text-slate-500">JSON</span>
                                                            <button
                                                                type="button"
                                                                onClick={() => openArtifactPreview(selectedItem.data.metadata.artifact_paths.json, "JSON")}
                                                                className="text-left text-xs text-cyan-400 border border-slate-800 bg-slate-900/50 rounded px-2 py-1 font-mono break-all hover:border-cyan-500/40 hover:bg-cyan-950/10 transition-colors"
                                                                title="Preview JSON artifact"
                                                            >
                                                                {selectedItem.data.metadata.artifact_paths.json}
                                                            </button>
                                                        </div>
                                                    )}
                                                </div>
                                            )}
                                            {previewPath && (
                                                <div className="space-y-2 pt-2 border-t border-slate-800/50">
                                                    <h4 className="text-xs font-semibold text-slate-400">
                                                        ARTIFACT PREVIEW {previewLabel ? `(${previewLabel})` : ""}
                                                    </h4>
                                                    <div className="text-[10px] text-slate-500 font-mono break-all">{previewPath}</div>
                                                    <div className="max-h-80 overflow-auto rounded border border-slate-800 bg-slate-900/60 p-3">
                                                        {previewLoading ? (
                                                            <div className="text-xs text-slate-400">Loading preview...</div>
                                                        ) : previewError ? (
                                                            <div className="text-xs text-rose-400">Preview error: {previewError}</div>
                                                        ) : previewPath.toLowerCase().endsWith(".md") ? (
                                                            <div className="prose prose-invert prose-sm max-w-none prose-pre:bg-slate-900 prose-pre:border prose-pre:border-slate-800 prose-a:text-cyan-400">
                                                            <ReactMarkdown
                                                                remarkPlugins={[remarkGfm]}
                                                                components={{
                                                                    a: ({ href = "", children }) => {
                                                                        const artifactPath = artifactPathFromHref(String(href));
                                                                        if (artifactPath) {
                                                                            return (
                                                                                <button
                                                                                    type="button"
                                                                                    onClick={() => openArtifactPreview(artifactPath, "Markdown Link")}
                                                                                    className="text-cyan-400 underline underline-offset-2 hover:text-cyan-300"
                                                                                >
                                                                                    {children}
                                                                                </button>
                                                                            );
                                                                        }
                                                                        return (
                                                                            <a
                                                                                href={href}
                                                                                target="_blank"
                                                                                rel="noopener noreferrer"
                                                                                className="text-cyan-400 underline underline-offset-2 hover:text-cyan-300"
                                                                            >
                                                                                {children}
                                                                            </a>
                                                                        );
                                                                    },
                                                                }}
                                                            >
                                                                {previewContent || "_Empty file_"}
                                                            </ReactMarkdown>
                                                        </div>
                                                        ) : (
                                                            <pre className="text-[11px] text-slate-300 whitespace-pre-wrap break-words font-mono">
                                                                {previewContent || "Empty file"}
                                                            </pre>
                                                        )}
                                                    </div>
                                                </div>
                                            )}
                                            <div className="overflow-x-auto pt-2 border-t border-slate-800/50">
                                                <h4 className="text-xs font-semibold text-slate-400 mb-1">RAW JSON</h4>
                                                <pre className="text-[10px] text-slate-500 font-mono leading-relaxed">
                                                    {JSON.stringify(selectedItem.data.metadata, null, 2)}
                                                </pre>
                                            </div>
                                        </div>
                                    </>
                                )}
                            </div>
                        </div>
                    ) : (
                        <div className="flex flex-col h-full">
                            <div className="border-b border-slate-800 bg-slate-900 px-6 py-4 shrink-0">
                                <div className="flex flex-wrap items-center gap-4 justify-between">
                                    <div className="flex items-center gap-3">
                                        <span className="px-2.5 py-1 rounded bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 text-xs font-semibold uppercase tracking-wide">
                                            {selectedItem.data.report_class || selectedItem.data.report_type}
                                        </span>
                                        {typeof selectedItem.data.window_hours === "number" && selectedItem.data.window_hours > 0 && (
                                            <span className="text-[11px] rounded border border-slate-700 px-2 py-0.5 text-slate-300">
                                                {selectedItem.data.window_hours}h window
                                            </span>
                                        )}
                                        <span
                                            className="text-xs text-slate-400"
                                            title={`UTC: ${formatDateTimeTz(selectedItem.data.created_at, { timeZone: "UTC", placeholder: "--" })}`}
                                        >
                                            {formatDateTimeTz(selectedItem.data.created_at, { placeholder: "--" })}
                                        </span>
                                    </div>
                                    <span className="text-xs text-slate-500 font-mono">Report #{selectedItem.data.id}</span>
                                </div>
                                {typeof selectedItem.data.divergence_score === "number" && (
                                    <div className="mt-2 rounded border border-slate-800 bg-slate-950/70 px-3 py-2 text-[11px] text-slate-300">
                                        Daily/Emerging divergence: {selectedItem.data.divergence_score}
                                        {selectedItem.data.divergence_note ? ` — ${selectedItem.data.divergence_note}` : ""}
                                    </div>
                                )}
                                {selectedItem.data.usage && (
                                    <div className="mt-3 flex items-center gap-3 text-xs text-slate-500">
                                        <div className="flex items-center gap-1.5" title="Prompt Tokens">
                                            <span className="w-2 h-2 rounded-full bg-slate-600"></span>
                                            {selectedItem.data.usage.prompt_tokens} prompt
                                        </div>
                                        <div className="flex items-center gap-1.5" title="Completion Tokens">
                                            <span className="w-2 h-2 rounded-full bg-emerald-600"></span>
                                            {selectedItem.data.usage.completion_tokens} completion
                                        </div>
                                        <div className="font-mono bg-slate-900 px-2 py-0.5 rounded border border-slate-800 text-slate-400">
                                            {(selectedItem.data.usage.completion_tokens || 0) + (selectedItem.data.usage.prompt_tokens || 0)} total
                                        </div>
                                    </div>
                                )}
                            </div>
                            <div className="p-6 overflow-y-auto flex-1 scrollbar-thin">
                                <div className="prose prose-invert prose-sm max-w-none prose-pre:bg-slate-900 prose-pre:border prose-pre:border-slate-800 prose-a:text-cyan-400 hover:prose-a:text-cyan-300 prose-headings:text-slate-200">
                                    {selectedItem.data.report_data?.markdown_content ? (
                                        <ReactMarkdown
                                            remarkPlugins={[remarkGfm]}
                                            components={{
                                                a: ({ href = "", children }) => {
                                                    const artifactPath = artifactPathFromHref(String(href));
                                                    if (artifactPath) {
                                                        return (
                                                            <button
                                                                type="button"
                                                                onClick={() => openArtifactPreview(artifactPath, "Report Link")}
                                                                className="text-cyan-400 underline underline-offset-2 hover:text-cyan-300"
                                                            >
                                                                {children}
                                                            </button>
                                                        );
                                                    }
                                                    return (
                                                        <a
                                                            href={href}
                                                            target="_blank"
                                                            rel="noopener noreferrer"
                                                            className="text-cyan-400 underline underline-offset-2 hover:text-cyan-300"
                                                        >
                                                            {children}
                                                        </a>
                                                    );
                                                },
                                            }}
                                        >
                                            {selectedItem.data.report_data.markdown_content}
                                        </ReactMarkdown>
                                    ) : (
                                        <pre className="text-xs text-amber-500/80 overflow-x-auto p-4 bg-slate-900 rounded-lg border border-slate-800">
                                            {JSON.stringify(selectedItem.data.report_data, null, 2)}
                                        </pre>
                                    )}
                                </div>
                                {previewPath && (
                                    <div className="mt-6 space-y-2 border-t border-slate-800/50 pt-4">
                                        <h4 className="text-xs font-semibold text-slate-400">
                                            ARTIFACT PREVIEW {previewLabel ? `(${previewLabel})` : ""}
                                        </h4>
                                        <div className="text-[10px] text-slate-500 font-mono break-all">{previewPath}</div>
                                        <div className="max-h-80 overflow-auto rounded border border-slate-800 bg-slate-900/60 p-3">
                                            {previewLoading ? (
                                                <div className="text-xs text-slate-400">Loading preview...</div>
                                            ) : previewError ? (
                                                <div className="text-xs text-rose-400">Preview error: {previewError}</div>
                                            ) : previewPath.toLowerCase().endsWith(".md") ? (
                                                <div className="prose prose-invert prose-sm max-w-none prose-pre:bg-slate-900 prose-pre:border prose-pre:border-slate-800 prose-a:text-cyan-400">
                                                    <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                                        {previewContent || "_Empty file_"}
                                                    </ReactMarkdown>
                                                </div>
                                            ) : (
                                                <pre className="text-[11px] text-slate-300 whitespace-pre-wrap break-words font-mono">
                                                    {previewContent || "Empty file"}
                                                </pre>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
