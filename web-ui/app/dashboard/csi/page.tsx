"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatDateTimeTz, toEpochMs } from "@/lib/timezone";

type CSIReport = {
    id: number;
    report_type: string;
    report_data: any;
    usage: any;
    created_at: string;
};

type PipelineNotification = {
    id: string;
    kind: string;
    title: string;
    message: string;
    severity: string;
    created_at: string;
    metadata?: any;
};

type SelectedItem =
    | { type: "report"; data: CSIReport }
    | { type: "notification"; data: PipelineNotification }
    | null;

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

export default function CSIDashboard() {
    const [reports, setReports] = useState<CSIReport[]>([]);
    const [notifications, setNotifications] = useState<PipelineNotification[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [selectedItem, setSelectedItem] = useState<SelectedItem>(null);

    useEffect(() => {
        async function loadData() {
            try {
                const [repRes, notifRes] = await Promise.all([
                    fetch(`${API_BASE}/api/v1/dashboard/csi/reports`),
                    fetch(`${API_BASE}/api/v1/dashboard/notifications?limit=50`)
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
                        // Filter for CSI-related notifications
                        const csiNotifs = ndata.notifications.filter((n: PipelineNotification) => n.kind && n.kind.startsWith("csi"));
                        setNotifications(csiNotifs);
                    }
                }
            } catch (err: any) {
                setError(err.message);
            } finally {
                setLoading(false);
            }
        }
        loadData();
    }, []);

    const totalReports = reports.length;
    const lastReportTime = reports.length > 0 ? formatDateTimeTz(reports[0].created_at, { placeholder: "N/A" }) : "N/A";

    return (
        <div className="flex h-full flex-col gap-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight text-slate-100">Creator Signal Intelligence (CSI)</h1>
                    <p className="text-sm text-slate-400">Automated insight and trend analysis across defined watchlists.</p>
                </div>
                <button
                    onClick={() => window.location.reload()}
                    className="rounded-md bg-cyan-600/20 px-3 py-1.5 text-sm font-medium text-cyan-300 hover:bg-cyan-600/30 transition-colors border border-cyan-500/30"
                >
                    Refresh Feed
                </button>
            </div>

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
                                                    <span className="font-medium truncate pr-2">{n.title}</span>
                                                    <span className="text-[10px] opacity-70 shrink-0">{timeAgo(n.created_at)}</span>
                                                </div>
                                                <p className="mt-0.5 truncate text-xs opacity-80">{n.message}</p>
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
                                    <span className="text-xs text-slate-500">{formatDateTimeTz(selectedItem.data.created_at, { placeholder: "--" })}</span>
                                </div>
                                <h2 className="text-lg font-semibold text-slate-100">{selectedItem.data.title}</h2>
                                <p className="text-xs text-slate-400 font-mono mt-1">ID: {selectedItem.data.id} | Kind: {selectedItem.data.kind}</p>
                            </div>
                            <div className="p-6 overflow-y-auto flex-1 scrollbar-thin">
                                <h3 className="text-sm font-medium text-slate-300 mb-2 uppercase tracking-wide">Message Content</h3>
                                <div className="bg-slate-900/50 border border-slate-800 rounded-lg p-4 mb-6 whitespace-pre-wrap text-sm text-slate-300">
                                    {selectedItem.data.message}
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
                                                            <div className="text-xs text-cyan-400 border border-slate-800 bg-slate-900/50 rounded px-2 py-1 font-mono break-all select-all">{selectedItem.data.metadata.artifact_paths.markdown}</div>
                                                        </div>
                                                    )}
                                                    {selectedItem.data.metadata.artifact_paths.json && (
                                                        <div className="flex flex-col gap-1">
                                                            <span className="text-[10px] text-slate-500">JSON</span>
                                                            <div className="text-xs text-cyan-400 border border-slate-800 bg-slate-900/50 rounded px-2 py-1 font-mono break-all select-all">{selectedItem.data.metadata.artifact_paths.json}</div>
                                                        </div>
                                                    )}
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
                                            {selectedItem.data.report_type}
                                        </span>
                                        <span className="text-xs text-slate-400">{formatDateTimeTz(selectedItem.data.created_at, { placeholder: "--" })}</span>
                                    </div>
                                    <span className="text-xs text-slate-500 font-mono">Report #{selectedItem.data.id}</span>
                                </div>
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
                                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                            {selectedItem.data.report_data.markdown_content}
                                        </ReactMarkdown>
                                    ) : (
                                        <pre className="text-xs text-amber-500/80 overflow-x-auto p-4 bg-slate-900 rounded-lg border border-slate-800">
                                            {JSON.stringify(selectedItem.data.report_data, null, 2)}
                                        </pre>
                                    )}
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
