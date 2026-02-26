"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatDistanceToNow } from "date-fns";

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
    const delta = (Date.now() - new Date(dateStr).getTime()) / 1000;
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
                        // Filter for CSI-related notifications or just show all if none
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
    const lastReportTime = reports.length > 0 ? new Date(reports[0].created_at).toLocaleString() : "N/A";

    return (
        <div className="space-y-6">
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

            <div className="rounded-xl border border-slate-800 bg-slate-900/50 overflow-hidden shadow-sm backdrop-blur">
                <div className="border-b border-slate-800 bg-slate-900/80 px-4 py-3">
                    <h2 className="text-sm font-semibold tracking-wide text-slate-300">Recent Insights & Trends</h2>
                </div>

                {loading ? (
                    <div className="p-8 text-center text-slate-400">Loading CSI feed...</div>
                ) : error ? (
                    <div className="p-8 text-center text-rose-400">Error loading feed: {error}</div>
                ) : reports.length === 0 ? (
                    <div className="p-8 text-center text-slate-400">No CSI reports found in the database.</div>
                ) : (
                    <div className="divide-y divide-slate-800/60 max-h-[800px] overflow-y-auto scrollbar-thin">
                        {reports.map((report) => (
                            <div key={report.id} className="p-4 hover:bg-slate-800/30 transition-colors">
                                <div className="flex items-center justify-between mb-3">
                                    <div className="flex items-center gap-2">
                                        <span className="px-2 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/30 text-xs font-medium lowercase tracking-wide">
                                            {report.report_type}
                                        </span>
                                        <span className="text-xs text-slate-500">
                                            {formatDistanceToNow(new Date(report.created_at), { addSuffix: true })}
                                        </span>
                                    </div>
                                </div>

                                <div className="prose prose-invert prose-sm max-w-none prose-pre:bg-slate-950 prose-pre:border prose-pre:border-slate-800 text-slate-300">
                                    {report.report_data?.markdown_content ? (
                                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                            {report.report_data.markdown_content}
                                        </ReactMarkdown>
                                    ) : (
                                        <pre className="text-xs text-slate-400 overflow-x-auto p-4 bg-slate-950 rounded-lg">
                                            {JSON.stringify(report.report_data, null, 2)}
                                        </pre>
                                    )}
                                </div>

                                {report.usage && (
                                    <div className="mt-4 flex items-center gap-4 text-xs text-slate-500">
                                        <div className="flex items-center gap-1.5" title="Prompt Tokens">
                                            <span className="w-2 h-2 rounded-full bg-slate-600"></span>
                                            {report.usage.prompt_tokens} prompt
                                        </div>
                                        <div className="flex items-center gap-1.5" title="Completion Tokens">
                                            <span className="w-2 h-2 rounded-full bg-emerald-600"></span>
                                            {report.usage.completion_tokens} completion
                                        </div>
                                        <div className="font-mono bg-slate-950 px-1.5 py-0.5 rounded border border-slate-800 text-slate-400">
                                            {(report.usage.completion_tokens || 0) + (report.usage.prompt_tokens || 0)} total tokens
                                        </div>
                                    </div>
                                )}
                            </div>
                        ))}
                    </div>
                )}
            </div>

            {/* Notification Panel */}
            <section className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 shadow-sm backdrop-blur">
                <div className="mb-3 flex items-center justify-between">
                    <h2 className="text-sm font-semibold tracking-wide text-slate-300">
                        CSI Pipeline Notifications
                    </h2>
                </div>
                {notifications.length === 0 && !loading ? (
                    <div className="text-sm text-slate-400 py-2">No recent CSI notifications.</div>
                ) : (
                    <div className="space-y-1.5 max-h-64 overflow-y-auto scrollbar-thin">
                        {notifications.map((n) => {
                            const style = SEVERITY_STYLES[n.severity] || SEVERITY_STYLES.info;
                            const dot = SEVERITY_DOTS[n.severity] || SEVERITY_DOTS.info;
                            return (
                                <div
                                    key={n.id}
                                    className={`flex items-start gap-2 rounded border px-3 py-2 text-sm ${style}`}
                                >
                                    <span className={`mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full ${dot}`} />
                                    <div className="min-w-0 flex-1">
                                        <span className="font-medium">{n.title}</span>
                                        <span className="ml-2 text-xs opacity-70">{timeAgo(n.created_at)}</span>
                                        {n.message && n.message !== n.title && (
                                            <p className="mt-0.5 truncate text-xs opacity-80">{n.message}</p>
                                        )}
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </section>
        </div>
    );
}
