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

/** Derive the real content source from event_type.
 *  The `source` field is just the pipeline name (csi_analytics, csi_ingester_batch).
 *  The actual content source lives in `event_type`: reddit_trend_report, threads_trend_report, etc. */
function contentSource(eventType: string): "reddit" | "threads" | "youtube" | "global" | "unknown" {
    const t = (eventType || "").toLowerCase();
    if (t.includes("reddit")) return "reddit";
    if (t.includes("threads")) return "threads";
    if (t.includes("rss") || t.includes("youtube")) return "youtube";
    if (t.includes("global") || t.includes("batch") || t.includes("brief")) return "global";
    return "unknown";
}

/** Friendly label for a content source */
function contentSourceLabel(cs: ReturnType<typeof contentSource>): string {
    switch (cs) {
        case "reddit": return "Reddit";
        case "threads": return "Threads";
        case "youtube": return "YouTube";
        case "global": return "Global Brief";
        default: return "Other";
    }
}

function sourceColor(source: string): string {
    const cs = contentSource(source);
    switch (cs) {
        case "youtube": return "text-red-400 bg-red-400/15 border-red-400/30";
        case "reddit": return "text-orange-400 bg-orange-400/15 border-orange-400/30";
        case "threads": return "text-purple-400 bg-purple-400/15 border-purple-400/30";
        case "global": return "text-sky-400 bg-sky-400/15 border-sky-400/30";
        default: return "text-foreground/80 bg-muted-foreground/15 border-muted-foreground/30";
    }
}

/** Color-coded dot icon for rapid source identification */
function sourceIcon(source: string): string {
    const cs = contentSource(source);
    switch (cs) {
        case "youtube": return "🔴";
        case "reddit": return "🟠";
        case "threads": return "🟣";
        case "global": return "🔵";
        default: return "⚪";
    }
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

/** Auto-derive a summary from markdown content when the summary field is empty.
 *  Extracts the first 1-2 meaningful non-heading lines as a preview snippet. */
function extractSummary(digest: CSIDigest): string {
    const raw = (digest.summary ?? "").trim();
    if (raw && raw.toLowerCase() !== "no summary") return raw;

    const md = digest.full_report_md || "";
    if (!md) return "";

    const lines = md.split("\n").map((l) => l.trim()).filter(Boolean);
    const snippets: string[] = [];
    for (const line of lines) {
        // Skip headings, horizontal rules, table separators
        if (/^#{1,6}\s/.test(line)) continue;
        if (/^[-=]{3,}$/.test(line)) continue;
        if (/^\|[-:| ]+\|$/.test(line)) continue;
        if (/^\*{3,}$/.test(line)) continue;
        // Skip lines that are the same as the title
        if (line === extractHeadline(digest)) continue;
        // Clean markdown formatting for summary display
        const cleaned = line
            .replace(/\*\*/g, "")
            .replace(/\*/g, "")
            .replace(/`/g, "")
            .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
            .trim();
        if (cleaned.length > 8) {
            snippets.push(cleaned);
            if (snippets.length >= 2) break;
        }
    }
    const result = snippets.join(" · ");
    return result.length > 200 ? result.slice(0, 197) + "…" : result;
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
            // Filter out empty stubs (csi_analytics trend reports with no content)
            const allDigests: CSIDigest[] = data.digests || [];
            const contentDigests = allDigests.filter(
                (d: CSIDigest) => !!(d.summary || d.full_report_md),
            );
            setDigests(contentDigests);
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

    /** Group by content-source derived from event_type, not the pipeline "source" field */
    const sources = useMemo(() => {
        const set = new Set<string>();
        digests.forEach((d) => set.add(contentSource(d.event_type)));
        set.delete("unknown");
        return Array.from(set).sort();
    }, [digests]);

    const filteredDigests = useMemo(() => {
        if (sourceFilter === "all") return digests;
        return digests.filter((d) => contentSource(d.event_type) === sourceFilter);
    }, [digests, sourceFilter]);

    /* ── Summary stats ───────────────────────────────────────────────── */

    const latestTime = digests.length > 0 ? formatDateTimeTz(digests[0].created_at, { placeholder: "N/A" }) : "N/A";

    const sourceMix = useMemo(() => {
        const counts: Record<string, number> = {};
        digests.forEach((d) => {
            const cs = contentSource(d.event_type);
            counts[cs] = (counts[cs] || 0) + 1;
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

    async function dismissDigest(digestId: string, evt: React.MouseEvent) {
        evt.stopPropagation();
        try {
            const resp = await fetch(`${API_BASE}/api/v1/dashboard/csi/digests/${encodeURIComponent(digestId)}`, { method: "DELETE" });
            if (!resp.ok) return;
            setDigests((prev) => prev.filter((d) => d.id !== digestId));
            if (selectedDigest?.id === digestId) setSelectedDigest(null);
        } catch { /* silent */ }
    }

    async function clearAllDigests() {
        if (!confirm(`Clear all ${filteredDigests.length} reports from the list?\nNew reports will still come in on their regular schedule.`)) return;
        setPurgeBusy(true);
        try {
            const resp = await fetch(`${API_BASE}/api/v1/dashboard/csi/digests`, { method: "DELETE" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const payload = await resp.json().catch(() => ({}));
            setPurgeStatus(`Cleared ${payload.cleared || 0} reports.`);
            setSelectedDigest(null);
            await loadData();
        } catch (err: any) {
            setPurgeStatus(`Clear failed: ${err.message}`);
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
        <div className="flex h-full flex-col" style={{ minHeight: 0 }}>
            {/* ─── Compact Header Bar ──────────────────────────────── */}
            <div className="flex items-center justify-between gap-3 pb-2 shrink-0">
                <div className="flex items-center gap-4 min-w-0">
                    <div className="min-w-0">
                        <h1 className="text-lg font-bold tracking-tight text-foreground leading-tight">
                            Creator Signal Intelligence
                        </h1>
                    </div>
                    {/* Inline stats */}
                    {!loading && (
                        <div className="hidden md:flex items-center gap-3 text-xs text-muted-foreground shrink-0">
                            <span className="font-medium text-foreground/70">{totalDigests}</span>
                            <span>reports</span>
                            <span className="text-muted/40">·</span>
                            <span>Latest: {latestTime}</span>
                            {digests.length > 0 && (
                                <span className="opacity-60">({timeAgo(digests[0].created_at)})</span>
                            )}
                            <span className="text-muted/40">·</span>
                            {Object.entries(sourceMix)
                                .sort((a, b) => b[1] - a[1])
                                .map(([src, count]) => (
                                    <span
                                        key={src}
                                        className="font-medium"
                                        title={contentSourceLabel(src as ReturnType<typeof contentSource>)}
                                    >
                                        {sourceIcon(src)}{count}
                                    </span>
                                ))}
                        </div>
                    )}
                </div>
                <div className="flex items-center gap-1.5 shrink-0">
                    <button
                        onClick={clearAllDigests}
                        disabled={purgeBusy || digests.length === 0}
                        className="rounded-md bg-muted/30 px-2.5 py-1 text-xs font-medium text-foreground/70 hover:bg-muted/50 transition-colors border border-border disabled:opacity-40"
                    >
                        {purgeBusy ? "…" : "Clear All"}
                    </button>
                    <button
                        onClick={purgeData}
                        disabled={purgeBusy}
                        className="rounded-md bg-amber-600/20 px-2.5 py-1 text-xs font-medium text-amber-200 hover:bg-amber-600/30 transition-colors border border-accent/30 disabled:opacity-60"
                    >
                        {purgeBusy ? "…" : "Purge Stale"}
                    </button>
                    <button
                        onClick={() => void loadData()}
                        className="rounded-md bg-primary/20 px-2.5 py-1 text-xs font-medium text-primary hover:bg-primary/30 transition-colors border border-primary/30"
                    >
                        ↻
                    </button>
                </div>
            </div>

            {purgeStatus && (
                <div className="rounded-md border border-border bg-background/60 px-3 py-1.5 text-xs text-foreground/80 mb-1.5 shrink-0">
                    {purgeStatus}
                </div>
            )}

            {/* ─── Source Filter Pills ─────────────────────────────── */}
            <div className="flex items-center gap-1 flex-wrap pb-2 shrink-0">
                <button
                    onClick={() => setSourceFilter("all")}
                    className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold transition-colors ${
                        sourceFilter === "all"
                            ? "border-primary/40 bg-primary/20 text-primary/80"
                            : "border-border bg-background/50 text-muted-foreground hover:bg-card/60"
                    }`}
                >
                    All ({digests.length})
                </button>
                {sources.map((src) => (
                    <button
                        key={src}
                        onClick={() => setSourceFilter(src)}
                        className={`rounded-full border px-2.5 py-0.5 text-[11px] font-semibold transition-colors ${
                            sourceFilter === src
                                ? `border-primary/40 bg-primary/20 ${sourceColor(src).split(" ")[0]}`
                                : "border-border bg-background/50 text-muted-foreground hover:bg-card/60"
                        }`}
                    >
                        {sourceIcon(src)} {contentSourceLabel(src as ReturnType<typeof contentSource>)} ({sourceMix[src] || 0})
                    </button>
                ))}
            </div>

            {/* ─── Error State ─────────────────────────────────────── */}
            {error && (
                <div className="rounded-xl border border-red-400/25 bg-red-400/10 p-3 text-sm text-red-400/80 shrink-0 mb-2">
                    <span className="font-semibold">Error:</span> {error}
                </div>
            )}

            {/* ─── Loading State ───────────────────────────────────── */}
            {loading && (
                <div className="flex items-center justify-center py-20 text-muted-foreground flex-1">
                    <div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-cyan-400" />
                    <span className="ml-3 text-sm">Loading digests…</span>
                </div>
            )}

            {/* ─── Empty State ─────────────────────────────────────── */}
            {!loading && !error && filteredDigests.length === 0 && (
                <div className="flex flex-col items-center justify-center py-20 text-center flex-1">
                    <div className="text-4xl mb-3">📡</div>
                    <h3 className="text-lg font-semibold text-foreground/80">No Digests Yet</h3>
                    <p className="text-sm text-muted-foreground mt-1 max-w-sm">
                        CSI trend reports will appear here as they are generated by your hourly analysis pipeline.
                    </p>
                </div>
            )}

            {/* ─── List/Detail Split ──────────────────────────────── */}
            {!loading && !error && filteredDigests.length > 0 && (
                <div className="grid grid-cols-1 lg:grid-cols-12 gap-3 flex-1 min-h-0 overflow-hidden">
                    {/* Digest List — compact left panel */}
                    <div className="lg:col-span-4 rounded-xl border border-border bg-background/50 backdrop-blur flex flex-col min-h-0 overflow-hidden">
                        <div className="sticky top-0 z-10 bg-background/90 backdrop-blur border-b border-border px-3 py-2">
                            <h2 className="text-xs font-semibold text-foreground/80 tracking-wide uppercase">
                                Reports
                                <span className="ml-1.5 text-[10px] text-muted-foreground font-normal normal-case">
                                    {filteredDigests.length}
                                </span>
                            </h2>
                        </div>
                        <div className="divide-y divide-slate-800/60 overflow-y-auto flex-1">
                            {filteredDigests.map((digest) => (
                                <button
                                    key={digest.id}
                                    onClick={() => {
                                        setSelectedDigest(digest);
                                        setSendStatus(null);
                                        setSendComment("");
                                    }}
                                    className={`w-full text-left px-3 py-2.5 transition-colors hover:bg-card/40 ${
                                        selectedDigest?.id === digest.id
                                            ? "bg-primary/10 border-l-2 border-l-cyan-400"
                                            : "border-l-2 border-l-transparent"
                                    }`}
                                >
                                    <div className="flex items-start gap-2">
                                        {/* Source icon — instant visual ID */}
                                        <span className="text-sm mt-0.5 shrink-0" title={contentSourceLabel(contentSource(digest.event_type))}>
                                            {sourceIcon(digest.event_type)}
                                        </span>
                                        <div className="flex-1 min-w-0">
                                            <div className="flex items-center justify-between gap-1.5 mb-0.5">
                                                <span className="text-[12px] font-semibold text-foreground leading-snug line-clamp-2">
                                                    {extractHeadline(digest)}
                                                </span>
                                                <div className="flex items-center gap-1 shrink-0">
                                                    <span className="text-[10px] text-muted-foreground whitespace-nowrap">
                                                        {timeAgo(digest.created_at)}
                                                    </span>
                                                    <span
                                                        role="button"
                                                        tabIndex={0}
                                                        onClick={(e) => dismissDigest(digest.id, e)}
                                                        className="text-muted-foreground/40 hover:text-red-400 transition-colors text-xs leading-none px-0.5"
                                                        title="Dismiss report"
                                                    >
                                                        ×
                                                    </span>
                                                </div>
                                            </div>
                                            <p className="text-[11px] text-muted-foreground/70 line-clamp-2 leading-relaxed">
                                                {extractSummary(digest) || "No summary"}
                                            </p>
                                        </div>
                                    </div>
                                </button>
                            ))}
                        </div>
                    </div>

                    {/* Detail / Reading Pane — expanded right panel */}
                    <div className="lg:col-span-8 rounded-xl border border-border bg-background/50 backdrop-blur flex flex-col min-h-0 overflow-hidden">
                        {!selectedDigest ? (
                            <div className="flex flex-col items-center justify-center h-full py-20 text-center">
                                <div className="text-3xl mb-3 opacity-40">←</div>
                                <p className="text-sm text-muted-foreground">
                                    Select a report to read
                                </p>
                            </div>
                        ) : (
                            <div className="flex flex-col h-full min-h-0">
                                {/* Detail Header — compact */}
                                <div className="shrink-0 bg-background/90 backdrop-blur border-b border-border px-5 py-2.5">
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0">
                                            <h2 className="text-base font-bold text-foreground leading-tight">
                                                {extractHeadline(selectedDigest)}
                                            </h2>
                                            <div className="flex items-center gap-2 mt-1 flex-wrap">
                                                <span className={`text-xs font-medium ${sourceColor(selectedDigest.event_type).split(" ")[0]}`}>
                                                    {sourceIcon(selectedDigest.event_type)} {contentSourceLabel(contentSource(selectedDigest.event_type))}
                                                </span>
                                                <span className="text-xs text-muted">•</span>
                                                <span className="text-xs text-muted-foreground">
                                                    {formatDateTimeTz(selectedDigest.created_at, { placeholder: "--" })}
                                                </span>
                                                {selectedDigest.source_types && selectedDigest.source_types.length > 0 && (
                                                    <>
                                                        <span className="text-xs text-muted">•</span>
                                                        {selectedDigest.source_types.map((st) => (
                                                            <span
                                                                key={st}
                                                                className="rounded bg-muted-foreground/10 px-1.5 py-0.5 text-[10px] text-muted-foreground"
                                                            >
                                                                {st}
                                                            </span>
                                                        ))}
                                                    </>
                                                )}
                                            </div>
                                        </div>
                                        <button
                                            onClick={() => setSelectedDigest(null)}
                                            className="shrink-0 rounded p-1 text-muted-foreground hover:text-foreground/80 hover:bg-card/60 transition-colors"
                                            title="Close"
                                        >
                                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                                            </svg>
                                        </button>
                                    </div>
                                </div>

                                {/* Report Content — scrollable markdown reader */}
                                <div className="flex-1 overflow-y-auto px-6 py-5">
                                    {selectedDigest.full_report_md ? (
                                        <div className="prose prose-invert prose-sm max-w-none
                                            prose-headings:text-foreground prose-headings:font-semibold prose-headings:mt-5 prose-headings:mb-2
                                            prose-h1:text-xl prose-h2:text-lg prose-h3:text-base
                                            prose-p:text-foreground/85 prose-p:leading-relaxed prose-p:my-2
                                            prose-li:text-foreground/85 prose-li:my-0.5
                                            prose-strong:text-foreground prose-strong:font-semibold
                                            prose-a:text-primary prose-a:no-underline hover:prose-a:underline
                                            prose-code:text-primary/90 prose-code:bg-primary/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs
                                            prose-pre:bg-background/60 prose-pre:border prose-pre:border-border prose-pre:rounded-lg
                                            prose-blockquote:border-l-primary/40 prose-blockquote:bg-primary/5 prose-blockquote:rounded-r-lg prose-blockquote:py-1 prose-blockquote:px-4
                                            prose-table:text-sm prose-th:text-foreground/80 prose-td:text-foreground/70
                                            prose-hr:border-border"
                                        >
                                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                                {selectedDigest.full_report_md}
                                            </ReactMarkdown>
                                        </div>
                                    ) : selectedDigest.summary ? (
                                        <div className="prose prose-invert prose-sm max-w-none prose-p:text-foreground/85 prose-p:leading-relaxed">
                                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                                {selectedDigest.summary}
                                            </ReactMarkdown>
                                        </div>
                                    ) : (
                                        <p className="text-sm text-muted-foreground italic">No report content available.</p>
                                    )}
                                </div>

                                {/* Send to Simone Bar — fixed at bottom */}
                                <div className="shrink-0 border-t border-border bg-background/95 backdrop-blur px-5 py-2">
                                    <div className="flex items-center gap-2">
                                        <input
                                            type="text"
                                            value={sendComment}
                                            onChange={(e) => setSendComment(e.target.value)}
                                            placeholder="Add a note for Simone (optional)…"
                                            className="flex-1 rounded-md border border-border bg-card/60 px-3 py-1.5 text-sm text-foreground placeholder-muted-foreground focus:outline-none focus:border-primary/50 focus:ring-1 focus:ring-primary/20"
                                        />
                                        <button
                                            onClick={() => void sendToSimone(selectedDigest)}
                                            disabled={sendBusy}
                                            className="rounded-md bg-primary/20 px-3 py-1.5 text-xs font-medium text-primary hover:bg-primary/30 transition-colors border border-primary/30 disabled:opacity-60 whitespace-nowrap"
                                        >
                                            {sendBusy ? "Sending…" : "📨 Send to Simone"}
                                        </button>
                                    </div>
                                    {sendStatus && (
                                        <div className={`mt-1.5 text-xs ${sendStatus.startsWith("✓") ? "text-primary" : "text-secondary"}`}>
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
