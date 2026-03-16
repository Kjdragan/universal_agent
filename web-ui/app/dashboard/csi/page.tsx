"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatDateTimeTz, toEpochMs } from "@/lib/timezone";

/* ── Types ──────────────────────────────────────────────────────────────── */

type CSIDigest = {
    id: string;
    event_id: string;
    source: string;
    event_type: string;
    title: string;
    summary: string;
    full_report_md: string;
    source_types: string[];
    created_at: string;
};

const API_BASE = "/api/dashboard/gateway";

/* ── Helpers ────────────────────────────────────────────────────────────── */

function timeAgo(dateStr: string): string {
    const ts = toEpochMs(dateStr);
    if (ts === null) return "--";
    const delta = Math.max(0, (Date.now() - ts) / 1000);
    if (delta < 60) return "just now";
    if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
    if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
    return `${Math.floor(delta / 86400)}d ago`;
}

function sourceLabel(source: string): string {
    return source
        .replace(/_/g, " ")
        .replace(/\b\w/g, (c) => c.toUpperCase());
}

function sourceColor(source: string): string {
    const s = source.toLowerCase();
    if (s.includes("rss") || s.includes("youtube")) return "text-rose-300 bg-rose-500/15 border-rose-500/30";
    if (s.includes("reddit")) return "text-orange-300 bg-orange-500/15 border-orange-500/30";
    if (s.includes("threads")) return "text-purple-300 bg-purple-500/15 border-purple-500/30";
    if (s.includes("global") || s.includes("brief")) return "text-cyan-300 bg-cyan-500/15 border-cyan-500/30";
    return "text-slate-300 bg-slate-500/15 border-slate-500/30";
}

function eventTypeIcon(eventType: string): string {
    const t = eventType.toLowerCase();
    if (t.includes("trend")) return "📊";
    if (t.includes("brief")) return "📋";
    if (t.includes("daily") || t.includes("summary")) return "📅";
    if (t.includes("digest")) return "📰";
    return "📄";
}

/** Derive a real headline for a digest.
 *  The batch-brief generator often stores `title = "Headline"` literally,
 *  with the actual headline buried in the markdown body as:
 *    ### Headline\n<actual headline text>
 *  This helper extracts the real text in that case. */
function extractHeadline(digest: CSIDigest): string {
    const raw = (digest.title ?? "").trim();
    const isGeneric =
        !raw ||
        raw.toLowerCase() === "headline" ||
        raw.toLowerCase() === "untitled" ||
        raw.toLowerCase() === "untitled report";

    if (!isGeneric) return raw;

    // Try to extract from full_report_md or summary
    const md = digest.full_report_md || digest.summary || "";
    if (!md) return raw || "Untitled Report";

    const lines = md.split("\n").map((l) => l.trim()).filter(Boolean);
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        // Skip markdown headings that are just "Headline" or "### Headline"
        if (/^#{1,4}\s*(Headline|Summary|Overview|Report)\s*$/i.test(line)) {
            // Grab the NEXT non-empty, non-heading line as the real headline
            for (let j = i + 1; j < lines.length; j++) {
                const next = lines[j].trim();
                if (!next) continue;
                if (/^#{1,4}\s/.test(next)) break; // hit another heading, stop
                // Truncate if very long
                return next.length > 120 ? next.slice(0, 117) + "…" : next;
            }
            continue;
        }
        // If the first line itself is meaningful text (not a heading)
        if (!/^#{1,4}\s/.test(line)) {
            return line.length > 120 ? line.slice(0, 117) + "…" : line;
        }
    }
    return raw || "Untitled Report";
}

/* ── Component ──────────────────────────────────────────────────────────── */

export default function CSIDashboard() {
    const [digests, setDigests] = useState<CSIDigest[]>([]);
    const [totalDigests, setTotalDigests] = useState(0);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [selectedDigest, setSelectedDigest] = useState<CSIDigest | null>(null);
    const [sourceFilter, setSourceFilter] = useState<string>("all");
    const [purgeBusy, setPurgeBusy] = useState(false);
    const [purgeStatus, setPurgeStatus] = useState<string | null>(null);
    const [sendBusy, setSendBusy] = useState(false);
    const [sendStatus, setSendStatus] = useState<string | null>(null);
    const [sendComment, setSendComment] = useState("");

    /* ── Data Loading ─────────────────────────────────────────────────── */

    const loadData = useCallback(async () => {
        try {
            const resp = await fetch(`${API_BASE}/api/v1/dashboard/csi/digests?limit=100`, { cache: "no-store" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
            setDigests(data.digests || []);
            setTotalDigests(data.total || 0);
            setError(null);
        } catch (err: any) {
            setError(err.message);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void loadData();
        const timer = window.setInterval(() => void loadData(), 30_000);
        return () => window.clearInterval(timer);
    }, [loadData]);

    /* ── Source filters ───────────────────────────────────────────────── */

    const sources = useMemo(() => {
        const set = new Set<string>();
        digests.forEach((d) => set.add(d.source));
        return Array.from(set).sort();
    }, [digests]);

    const filteredDigests = useMemo(() => {
        if (sourceFilter === "all") return digests;
        return digests.filter((d) => d.source === sourceFilter);
    }, [digests, sourceFilter]);

    /* ── Summary cards ───────────────────────────────────────────────── */

    const latestTime = digests.length > 0 ? formatDateTimeTz(digests[0].created_at, { placeholder: "N/A" }) : "N/A";

    const sourceMix = useMemo(() => {
        const counts: Record<string, number> = {};
        digests.forEach((d) => {
            counts[d.source] = (counts[d.source] || 0) + 1;
        });
        return counts;
    }, [digests]);

    /* ── Actions ──────────────────────────────────────────────────────── */

    async function purgeData() {
        if (!confirm("Purge all stale CSI data from the database?\nThis clears old notifications, specialist loops, and task hub items.")) return;
        setPurgeBusy(true);
        setPurgeStatus(null);
        try {
            const resp = await fetch(`${API_BASE}/api/v1/dashboard/csi/purge`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
            });
            const payload = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(payload?.detail || `HTTP ${resp.status}`);
            setPurgeStatus(`Purged ${payload.total_purged || 0} items.`);
            setSelectedDigest(null);
            await loadData();
        } catch (err: any) {
            setPurgeStatus(`Purge failed: ${err.message}`);
        } finally {
            setPurgeBusy(false);
        }
    }

    async function sendToSimone(digest: CSIDigest) {
        setSendBusy(true);
        setSendStatus(null);
        try {
            const resp = await fetch(
                `${API_BASE}/api/v1/dashboard/csi/digests/${encodeURIComponent(digest.id)}/send-to-simone`,
                {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ comment: sendComment }),
                },
            );
            const payload = await resp.json().catch(() => ({}));
            if (!resp.ok) throw new Error(payload?.detail || `HTTP ${resp.status}`);
            setSendStatus("✓ Sent to Simone");
            setSendComment("");
        } catch (err: any) {
            setSendStatus(`Send failed: ${err.message}`);
        } finally {
            setSendBusy(false);
        }
    }

    /* ── Render ────────────────────────────────────────────────────────── */

    return (
        <div className="flex h-full flex-col gap-5">
            {/* ─── Header ──────────────────────────────────────────── */}
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-2xl font-bold tracking-tight text-slate-100">
                        Creator Signal Intelligence
                    </h1>
                    <p className="text-sm text-slate-400">
                        Trend reports and digests from your watchlists
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <button
                        onClick={purgeData}
                        disabled={purgeBusy}
                        className="rounded-md bg-amber-600/20 px-3 py-1.5 text-sm font-medium text-amber-200 hover:bg-amber-600/30 transition-colors border border-amber-500/30 disabled:opacity-60"
                    >
                        {purgeBusy ? "Purging…" : "Purge Stale Data"}
                    </button>
                    <button
                        onClick={() => void loadData()}
                        className="rounded-md bg-cyan-600/20 px-3 py-1.5 text-sm font-medium text-cyan-300 hover:bg-cyan-600/30 transition-colors border border-cyan-500/30"
                    >
                        Refresh
                    </button>
                </div>
            </div>

            {purgeStatus && (
                <div className="rounded-md border border-slate-700 bg-slate-900/60 px-3 py-2 text-xs text-slate-300">
                    {purgeStatus}
                </div>
            )}

            {/* ─── Summary Cards ───────────────────────────────────── */}
            <div className="grid gap-4 md:grid-cols-3">
                <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 shadow-sm backdrop-blur">
                    <div className="text-sm font-medium text-slate-400">Total Digests</div>
                    <div className="mt-2">
                        <span className="text-3xl font-bold text-slate-100">{loading ? "…" : totalDigests}</span>
                    </div>
                    <div className="mt-1 text-xs text-slate-500">
                        {filteredDigests.length !== digests.length
                            ? `${filteredDigests.length} shown (filtered)`
                            : "In memory"}
                    </div>
                </div>

                <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 shadow-sm backdrop-blur">
                    <div className="text-sm font-medium text-slate-400">Latest Report</div>
                    <div className="mt-2">
                        <span className="text-lg font-bold text-slate-100">{loading ? "…" : latestTime}</span>
                    </div>
                    <div className="mt-1 text-xs text-slate-500">
                        {digests.length > 0 ? timeAgo(digests[0].created_at) : "No reports yet"}
                    </div>
                </div>

                <div className="rounded-xl border border-slate-800 bg-slate-900/50 p-4 shadow-sm backdrop-blur">
                    <div className="text-sm font-medium text-slate-400">Source Mix</div>
                    <div className="mt-2 flex flex-wrap gap-1.5">
                        {loading ? (
                            <span className="text-lg font-bold text-slate-100">…</span>
                        ) : Object.keys(sourceMix).length === 0 ? (
                            <span className="text-sm text-slate-500">No data</span>
                        ) : (
                            Object.entries(sourceMix)
                                .sort((a, b) => b[1] - a[1])
                                .map(([src, count]) => (
                                    <span
                                        key={src}
                                        className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${sourceColor(src)}`}
                                    >
                                        {sourceLabel(src)} {count}
                                    </span>
                                ))
                        )}
                    </div>
                </div>
            </div>

            {/* ─── Source Filter Tabs ──────────────────────────────── */}
            <div className="flex items-center gap-1.5 flex-wrap">
                <button
                    onClick={() => setSourceFilter("all")}
                    className={`rounded-md border px-3 py-1 text-xs font-semibold transition-colors ${
                        sourceFilter === "all"
                            ? "border-cyan-500/60 bg-cyan-600/20 text-cyan-200"
                            : "border-slate-700 bg-slate-900/50 text-slate-400 hover:bg-slate-800/60"
                    }`}
                >
                    All ({digests.length})
                </button>
                {sources.map((src) => (
                    <button
                        key={src}
                        onClick={() => setSourceFilter(src)}
                        className={`rounded-md border px-3 py-1 text-xs font-semibold transition-colors ${
                            sourceFilter === src
                                ? "border-cyan-500/60 bg-cyan-600/20 text-cyan-200"
                                : "border-slate-700 bg-slate-900/50 text-slate-400 hover:bg-slate-800/60"
                        }`}
                    >
                        {sourceLabel(src)} ({sourceMix[src] || 0})
                    </button>
                ))}
            </div>

            {/* ─── Error State ─────────────────────────────────────── */}
            {error && (
                <div className="rounded-xl border border-rose-700/40 bg-rose-900/20 p-4 text-sm text-rose-200">
                    <span className="font-semibold">Error:</span> {error}
                </div>
            )}

            {/* ─── Loading State ───────────────────────────────────── */}
            {loading && (
                <div className="flex items-center justify-center py-20 text-slate-400">
                    <div className="h-6 w-6 animate-spin rounded-full border-2 border-slate-600 border-t-cyan-400" />
                    <span className="ml-3 text-sm">Loading digests…</span>
                </div>
            )}

            {/* ─── Empty State ─────────────────────────────────────── */}
            {!loading && !error && filteredDigests.length === 0 && (
                <div className="flex flex-col items-center justify-center py-20 text-center">
                    <div className="text-4xl mb-3">📡</div>
                    <h3 className="text-lg font-semibold text-slate-300">No Digests Yet</h3>
                    <p className="text-sm text-slate-500 mt-1 max-w-sm">
                        CSI trend reports will appear here as they are generated by your hourly analysis pipeline.
                    </p>
                </div>
            )}

            {/* ─── List/Detail Split ──────────────────────────────── */}
            {!loading && !error && filteredDigests.length > 0 && (
                <div className="grid grid-cols-1 lg:grid-cols-5 gap-4 flex-1 min-h-0">
                    {/* Digest List */}
                    <div className="lg:col-span-2 rounded-xl border border-slate-800 bg-slate-900/50 backdrop-blur overflow-auto max-h-[calc(100vh-26rem)]">
                        <div className="sticky top-0 z-10 bg-slate-900/90 backdrop-blur border-b border-slate-800 px-4 py-2.5">
                            <h2 className="text-sm font-semibold text-slate-300 tracking-wide">
                                Recent Reports
                                <span className="ml-2 text-xs text-slate-500 font-normal">
                                    {filteredDigests.length}
                                </span>
                            </h2>
                        </div>
                        <div className="divide-y divide-slate-800/60">
                            {filteredDigests.map((digest) => (
                                <button
                                    key={digest.id}
                                    onClick={() => {
                                        setSelectedDigest(digest);
                                        setSendStatus(null);
                                        setSendComment("");
                                    }}
                                    className={`w-full text-left px-4 py-3 transition-colors hover:bg-slate-800/40 ${
                                        selectedDigest?.id === digest.id
                                            ? "bg-cyan-900/20 border-l-2 border-l-cyan-400"
                                            : "border-l-2 border-l-transparent"
                                    }`}
                                >
                                    <div className="flex items-start justify-between gap-2">
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center gap-1.5 mb-1">
                                                <span className="text-sm">{eventTypeIcon(digest.event_type)}</span>
                                                <span className="text-sm font-medium text-slate-200 truncate">
                                                    {extractHeadline(digest)}
                                                </span>
                                            </div>
                                            <p className="text-xs text-slate-400 line-clamp-2">
                                                {digest.summary || "No summary"}
                                            </p>
                                        </div>
                                        <div className="flex flex-col items-end gap-1 shrink-0">
                                            <span className="text-[10px] text-slate-500 whitespace-nowrap">
                                                {timeAgo(digest.created_at)}
                                            </span>
                                            <span
                                                className={`rounded-full border px-1.5 py-0.5 text-[10px] font-medium ${sourceColor(digest.source)}`}
                                            >
                                                {sourceLabel(digest.source)}
                                            </span>
                                        </div>
                                    </div>
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Detail Pane */}
                    <div className="lg:col-span-3 rounded-xl border border-slate-800 bg-slate-900/50 backdrop-blur overflow-auto max-h-[calc(100vh-26rem)]">
                        {!selectedDigest ? (
                            <div className="flex flex-col items-center justify-center h-full py-20 text-center">
                                <div className="text-3xl mb-3 opacity-40">←</div>
                                <p className="text-sm text-slate-500">
                                    Select a report to read
                                </p>
                            </div>
                        ) : (
                            <div className="flex flex-col h-full">
                                {/* Detail Header */}
                                <div className="sticky top-0 z-10 bg-slate-900/90 backdrop-blur border-b border-slate-800 px-5 py-3">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0">
                                            <h2 className="text-lg font-bold text-slate-100 leading-tight">
                                                {eventTypeIcon(selectedDigest.event_type)}{" "}
                                                {extractHeadline(selectedDigest)}
                                            </h2>
                                            <div className="flex items-center gap-2 mt-1.5 flex-wrap">
                                                <span
                                                    className={`rounded-full border px-2 py-0.5 text-[11px] font-medium ${sourceColor(selectedDigest.source)}`}
                                                >
                                                    {sourceLabel(selectedDigest.source)}
                                                </span>
                                                <span className="text-xs text-slate-500">
                                                    {formatDateTimeTz(selectedDigest.created_at, { placeholder: "--" })}
                                                </span>
                                                <span className="text-xs text-slate-600">•</span>
                                                <span className="text-xs text-slate-500">
                                                    {selectedDigest.event_type}
                                                </span>
                                            </div>
                                        </div>
                                        <button
                                            onClick={() => setSelectedDigest(null)}
                                            className="shrink-0 rounded p-1 text-slate-500 hover:text-slate-300 hover:bg-slate-800/60 transition-colors"
                                            title="Close"
                                        >
                                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                                            </svg>
                                        </button>
                                    </div>
                                </div>

                                {/* Report Content */}
                                <div className="flex-1 px-5 py-4 overflow-auto">
                                    {selectedDigest.full_report_md ? (
                                        <div className="prose prose-invert prose-sm max-w-none prose-headings:text-slate-200 prose-p:text-slate-300 prose-li:text-slate-300 prose-strong:text-slate-200 prose-a:text-cyan-400 prose-code:text-cyan-300 prose-pre:bg-slate-950/60 prose-pre:border prose-pre:border-slate-800">
                                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                                {selectedDigest.full_report_md}
                                            </ReactMarkdown>
                                        </div>
                                    ) : selectedDigest.summary ? (
                                        <div className="prose prose-invert prose-sm max-w-none prose-p:text-slate-300">
                                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                                {selectedDigest.summary}
                                            </ReactMarkdown>
                                        </div>
                                    ) : (
                                        <p className="text-sm text-slate-500 italic">No report content available.</p>
                                    )}
                                </div>

                                {/* Send to Simone Bar */}
                                <div className="sticky bottom-0 border-t border-slate-800 bg-slate-900/95 backdrop-blur px-5 py-3">
                                    <div className="flex items-center gap-2">
                                        <input
                                            type="text"
                                            value={sendComment}
                                            onChange={(e) => setSendComment(e.target.value)}
                                            placeholder="Add a note for Simone (optional)…"
                                            className="flex-1 rounded-md border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-sm text-slate-200 placeholder-slate-500 focus:outline-none focus:border-cyan-500/50 focus:ring-1 focus:ring-cyan-500/20"
                                        />
                                        <button
                                            onClick={() => void sendToSimone(selectedDigest)}
                                            disabled={sendBusy}
                                            className="rounded-md bg-cyan-600/20 px-4 py-1.5 text-sm font-medium text-cyan-300 hover:bg-cyan-600/30 transition-colors border border-cyan-500/30 disabled:opacity-60 whitespace-nowrap"
                                        >
                                            {sendBusy ? "Sending…" : "📨 Send to Simone"}
                                        </button>
                                    </div>
                                    {sendStatus && (
                                        <div className={`mt-2 text-xs ${sendStatus.startsWith("✓") ? "text-emerald-300" : "text-rose-300"}`}>
                                            {sendStatus}
                                        </div>
                                    )}
                                </div>
                            </div>
                        )}
                    </div>
                </div>
            )}
        </div>
    );
}
