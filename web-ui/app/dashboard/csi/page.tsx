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

export default function CSIDashboard() {
    const [reports, setReports] = useState<CSIReport[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        async function loadReports() {
            try {
                const res = await fetch("/api/v1/dashboard/csi/reports");
                if (!res.ok) throw new Error(`HTTP ${res.status}`);
                const data = await res.json();
                if (data.status === "error" || data.status === "unavailable") {
                    throw new Error(data.detail || "CSI unavailable");
                }
                setReports(data.reports || []);
            } catch (err: any) {
                setError(err.message);
            } finally {
                setLoading(false);
            }
        }
        loadReports();
    }, []);

    const totalReports = reports.length;
    const lastReportTime = reports.length > 0 ? new Date(reports[0].created_at).toLocaleString() : "N/A";

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <h1 className="text-2xl font-bold tracking-tight text-slate-100">Creator Signal Intelligence (CSI)</h1>
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
        </div>
    );
}
