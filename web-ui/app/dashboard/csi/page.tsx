"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
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

function contentSource(eventType: string): "reddit" | "threads" | "youtube" | "global" | "unknown" {
    const t = (eventType || "").toLowerCase();
    if (t.includes("reddit")) return "reddit";
    if (t.includes("threads")) return "threads";
    if (t.includes("rss") || t.includes("youtube")) return "youtube";
    if (t.includes("global") || t.includes("batch") || t.includes("brief")) return "global";
    return "unknown";
}

function contentSourceLabel(cs: ReturnType<typeof contentSource>): string {
    switch (cs) {
        case "reddit": return "Reddit";
        case "threads": return "Threads";
        case "youtube": return "YouTube";
        case "global": return "Global Brief";
        default: return "Other";
    }
}

/** Source icon as JSX — Material Symbols for Reddit/Threads/Global, unicode for YouTube */
function SourceIcon({ source, size = 16 }: { source: string; size?: number }) {
    const cs = contentSource(source);
    switch (cs) {
        case "youtube":
            return <span className="text-[#ef4444] leading-none" style={{ fontSize: size }}>▶</span>;
        case "reddit":
            return <span className="material-symbols-outlined text-[#f97316] leading-none" style={{ fontSize: size }}>sensors</span>;
        case "threads":
            return <span className="material-symbols-outlined text-[#a855f7] leading-none" style={{ fontSize: size }}>alternate_email</span>;
        case "global":
            return <span className="material-symbols-outlined text-primary leading-none" style={{ fontSize: size }}>language</span>;
        default:
            return <span className="material-symbols-outlined text-muted-foreground leading-none" style={{ fontSize: size }}>article</span>;
    }
}

/** Source type badge abbreviations */
function sourceTypeBadge(cs: ReturnType<typeof contentSource>): string {
    switch (cs) {
        case "youtube": return "YT";
        case "reddit": return "RD";
        case "threads": return "TH";
        case "global": return "GB";
        default: return "??";
    }
}

function extractHeadline(digest: CSIDigest): string {
    const raw = (digest.title ?? "").trim();
    const isGeneric =
        !raw ||
        raw.toLowerCase() === "headline" ||
        raw.toLowerCase() === "untitled" ||
        raw.toLowerCase() === "untitled report";
    if (!isGeneric) return raw;
    const md = digest.full_report_md || digest.summary || "";
    if (!md) return raw || "Untitled Report";
    const lines = md.split("\n").map((l) => l.trim()).filter(Boolean);
    for (let i = 0; i < lines.length; i++) {
        const line = lines[i];
        if (/^#{1,4}\s*(Headline|Summary|Overview|Report)\s*$/i.test(line)) {
            for (let j = i + 1; j < lines.length; j++) {
                const next = lines[j].trim();
                if (!next) continue;
                if (/^#{1,4}\s/.test(next)) break;
                return next.length > 120 ? next.slice(0, 117) + "…" : next;
            }
            continue;
        }
        if (!/^#{1,4}\s/.test(line)) {
            return line.length > 120 ? line.slice(0, 117) + "…" : line;
        }
    }
    return raw || "Untitled Report";
}

function extractSummary(digest: CSIDigest): string {
    const raw = (digest.summary ?? "").trim();
    if (raw && raw.toLowerCase() !== "no summary") return raw;
    const md = digest.full_report_md || "";
    if (!md) return "";
    const lines = md.split("\n").map((l) => l.trim()).filter(Boolean);
    const snippets: string[] = [];
    for (const line of lines) {
        if (/^#{1,6}\s/.test(line)) continue;
        if (/^[-=]{3,}$/.test(line)) continue;
        if (/^\|[-:| ]+\|$/.test(line)) continue;
        if (/^\*{3,}$/.test(line)) continue;
        if (line === extractHeadline(digest)) continue;
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
    const router = useRouter();
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
    const [expandedSummaries, setExpandedSummaries] = useState<Set<string>>(new Set());

    /* ── Data Loading ─────────────────────────────────────────────────── */

    const loadData = useCallback(async () => {
        try {
            const resp = await fetch(`${API_BASE}/api/v1/dashboard/csi/digests?limit=100`, { cache: "no-store" });
            if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
            const data = await resp.json();
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

    function toggleSummaryExpand(id: string, evt: React.MouseEvent) {
        evt.stopPropagation();
        setExpandedSummaries((prev) => {
            const next = new Set(prev);
            if (next.has(id)) next.delete(id);
            else next.add(id);
            return next;
        });
    }

    /* ── Render ────────────────────────────────────────────────────────── */

    return (
        <div className="flex h-full flex-col font-display" style={{ minHeight: 0 }}>
            {/* ─── Header Bar ──────────────────────────────────────── */}
            <header className="flex items-center justify-between w-full px-6 border-b border-border/30 bg-background h-14 shrink-0">
                <div className="flex items-center gap-6">
                    <span className="text-[22px] font-bold tracking-tight text-foreground">
                        Creator Signal Intelligence
                    </span>
                    {/* Inline stats */}
                    {!loading && (
                        <div className="hidden lg:flex items-center gap-4 text-[13px] font-medium tracking-tight text-muted-foreground uppercase">
                            <span>{totalDigests} reports</span>
                            <span className="opacity-30">•</span>
                            <span>Latest: {latestTime}</span>
                            {digests.length > 0 && (
                                <span className="opacity-60 normal-case">({timeAgo(digests[0].created_at)})</span>
                            )}
                            <div className="flex items-center gap-3 ml-2 normal-case">
                                {Object.entries(sourceMix)
                                    .sort((a, b) => b[1] - a[1])
                                    .map(([src, count]) => (
                                        <span key={src} className="flex items-center gap-1.5">
                                            <SourceIcon source={src} size={16} />
                                            <span>{count}</span>
                                        </span>
                                    ))}
                            </div>
                        </div>
                    )}
                </div>
                <nav className="flex items-center gap-2">
                    <button
                        onClick={clearAllDigests}
                        disabled={purgeBusy || digests.length === 0}
                        className="px-3 py-1 text-[13px] font-medium text-muted-foreground hover:bg-card/40 hover:text-primary transition-all duration-200 rounded-lg disabled:opacity-40"
                    >
                        {purgeBusy ? "…" : "Clear All"}
                    </button>
                    <button
                        onClick={purgeData}
                        disabled={purgeBusy}
                        className="px-3 py-1 text-[13px] font-medium text-accent border border-accent/20 hover:bg-accent/10 transition-all duration-200 rounded-lg disabled:opacity-60"
                    >
                        {purgeBusy ? "…" : "Purge Stale"}
                    </button>
                    <button
                        onClick={() => void loadData()}
                        className="px-3 py-1 text-[13px] font-medium text-accent bg-card/30 hover:bg-card/50 transition-all duration-200 rounded-lg flex items-center gap-1"
                    >
                        <span className="material-symbols-outlined text-sm">refresh</span>
                        Refresh
                    </button>
                    <div className="w-px h-4 bg-border/30 mx-2" />
                    <span
                        onClick={() => {
                            const url = "/?new_session=1&focus_input=1";
                            const w = window.open(url, "ua-chat-window");
                            if (w) w.focus();
                        }}
                        className="material-symbols-outlined text-muted-foreground cursor-pointer hover:text-primary transition-colors text-[20px]"
                        title="Chat"
                    >
                        chat
                    </span>
                    <span
                        onClick={() => router.push("/dashboard")}
                        className="material-symbols-outlined text-muted-foreground cursor-pointer hover:text-primary transition-colors text-[20px]"
                        title="Home"
                    >
                        home
                    </span>
                    <span className="material-symbols-outlined text-muted-foreground cursor-pointer hover:text-primary transition-colors text-[20px]" title="Notifications">
                        notifications
                    </span>
                    <span className="material-symbols-outlined text-muted-foreground cursor-pointer hover:text-primary transition-colors text-[20px]" title="Settings">
                        settings
                    </span>
                </nav>
            </header>

            {/* ─── Purge Status ─────────────────────────────────────── */}
            {purgeStatus && (
                <div className="rounded-md border border-border bg-background/60 px-4 py-1.5 text-xs text-foreground/80 mx-6 mt-2 shrink-0">
                    {purgeStatus}
                </div>
            )}

            {/* ─── Source Filter Pills ─────────────────────────────── */}
            <div className="py-3 bg-background px-6 flex items-center gap-2 border-b border-border/20 shrink-0">
                <button
                    onClick={() => setSourceFilter("all")}
                    className={`px-3 h-6 flex items-center text-[11px] font-bold uppercase tracking-widest rounded-full transition-colors ${
                        sourceFilter === "all"
                            ? "bg-primary text-primary-foreground"
                            : "text-muted-foreground hover:bg-card/40"
                    }`}
                >
                    All ({digests.length})
                </button>
                {sources.map((src) => (
                    <button
                        key={src}
                        onClick={() => setSourceFilter(src)}
                        className={`px-3 h-6 flex items-center gap-1.5 text-[11px] font-bold uppercase tracking-widest rounded-full transition-colors ${
                            sourceFilter === src
                                ? "bg-primary/20 text-primary border border-primary/30"
                                : "text-muted-foreground hover:bg-card/40"
                        }`}
                    >
                        <SourceIcon source={src} size={14} />
                        {contentSourceLabel(src as ReturnType<typeof contentSource>)} ({sourceMix[src] || 0})
                    </button>
                ))}
            </div>

            {/* ─── Error State ─────────────────────────────────────── */}
            {error && (
                <div className="rounded-xl border border-red-400/25 bg-red-400/10 p-3 text-sm text-red-400/80 shrink-0 mx-6 mt-2">
                    <span className="font-semibold">Error:</span> {error}
                </div>
            )}

            {/* ─── Loading State ───────────────────────────────────── */}
            {loading && (
                <div className="flex items-center justify-center py-20 text-muted-foreground flex-1">
                    <div className="h-6 w-6 animate-spin rounded-full border-2 border-border border-t-primary" />
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

            {/* ─── Main Content: Centered Two-Panel Layout ──────── */}
            {!loading && !error && filteredDigests.length > 0 && (
                <main className="flex-1 flex overflow-hidden justify-center">
                    <div className="max-w-[1076px] w-full flex h-full" style={{ gap: 96 }}>

                        {/* ── Left Panel: Reports List ──────────────── */}
                        <aside className="w-[420px] shrink-0 bg-background flex flex-col border-r border-border/30 h-full">
                            <div className="p-4 flex items-center justify-between shrink-0">
                                <span className="text-[10px] font-bold tracking-[0.1em] text-muted-foreground uppercase">
                                    Reports
                                </span>
                                <span className="bg-card/40 text-[9px] px-1.5 py-0.5 rounded text-primary border border-primary/20">
                                    LIVE FEED
                                </span>
                            </div>
                            <div className="flex-1 overflow-y-auto scrollbar-thin">
                                {filteredDigests.map((digest) => {
                                    const isSelected = selectedDigest?.id === digest.id;
                                    const isExpanded = expandedSummaries.has(digest.id);
                                    const summary = extractSummary(digest);
                                    return (
                                        <article
                                            key={digest.id}
                                            onClick={() => {
                                                setSelectedDigest(digest);
                                                setSendStatus(null);
                                                setSendComment("");
                                            }}
                                            className={`p-4 cursor-pointer border-l-2 transition-colors group relative ${
                                                isSelected
                                                    ? "bg-card/40 border-l-primary"
                                                    : "hover:bg-card/20 border-l-transparent"
                                            }`}
                                        >
                                            <div className="flex items-start gap-3">
                                                <span className="mt-1 shrink-0">
                                                    <SourceIcon source={digest.event_type} size={16} />
                                                </span>
                                                <div className="flex-1 min-w-0">
                                                    <div className="flex justify-between items-start gap-2 mb-1">
                                                        <h3 className="text-[15px] font-semibold text-foreground line-clamp-2 leading-tight">
                                                            {extractHeadline(digest)}
                                                        </h3>
                                                        <span className={`text-[11px] font-medium px-1 rounded shrink-0 ${
                                                            isSelected
                                                                ? "text-primary bg-primary/10"
                                                                : "text-muted-foreground opacity-50"
                                                        }`}>
                                                            {timeAgo(digest.created_at)}
                                                        </span>
                                                    </div>
                                                    {summary && (
                                                        <>
                                                            <p className={`text-[13px] text-muted-foreground leading-[1.6] ${
                                                                isExpanded ? "" : "line-clamp-3"
                                                            }`}>
                                                                {summary}
                                                            </p>
                                                            {summary.length > 100 && (
                                                                <button
                                                                    onClick={(e) => toggleSummaryExpand(digest.id, e)}
                                                                    className="text-[11px] text-muted-foreground mt-1 hover:text-primary transition-colors"
                                                                >
                                                                    {isExpanded ? "Show less ▲" : "Show more ▼"}
                                                                </button>
                                                            )}
                                                        </>
                                                    )}
                                                </div>
                                            </div>
                                            {/* Trash icon — visible on hover */}
                                            <button
                                                onClick={(e) => dismissDigest(digest.id, e)}
                                                className="absolute bottom-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity duration-200"
                                                title="Delete report"
                                            >
                                                <span className="material-symbols-outlined text-[18px] text-primary hover:text-red-400 transition-colors">
                                                    delete
                                                </span>
                                            </button>
                                        </article>
                                    );
                                })}
                            </div>
                        </aside>

                        {/* ── Right Panel: Report Reader ────────────── */}
                        <section className="w-[560px] shrink-0 bg-background flex flex-col relative h-full">
                            {!selectedDigest ? (
                                <div className="flex flex-col items-center justify-center h-full py-20 text-center">
                                    <div className="text-3xl mb-3 opacity-40">←</div>
                                    <p className="text-sm text-muted-foreground">
                                        Select a report to read
                                    </p>
                                </div>
                            ) : (
                                <>
                                    {/* Scrollable content */}
                                    <div className="flex-1 overflow-y-auto pr-12 pl-0 py-8 scrollbar-thin">

                                        {/* Report Header */}
                                        <header className="mb-8">
                                            <div className="flex items-center gap-3 mb-4">
                                                <span className="bg-primary/10 text-primary text-[10px] font-bold px-2 py-0.5 rounded tracking-widest uppercase">
                                                    {contentSourceLabel(contentSource(selectedDigest.event_type))}
                                                </span>
                                                <span className="text-[11px] text-muted-foreground font-medium">
                                                    {formatDateTimeTz(selectedDigest.created_at, { placeholder: "--" })}
                                                </span>
                                                {/* Source type badges */}
                                                {selectedDigest.source_types && selectedDigest.source_types.length > 0 && (
                                                    <div className="flex gap-1 ml-auto">
                                                        {selectedDigest.source_types.map((st) => (
                                                            <span
                                                                key={st}
                                                                className="w-5 h-5 bg-card rounded flex items-center justify-center text-[10px] text-foreground border border-border/30"
                                                            >
                                                                {sourceTypeBadge(contentSource(st))}
                                                            </span>
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                            <h1 className="text-2xl font-bold text-foreground tracking-tight leading-tight mb-4">
                                                {extractHeadline(selectedDigest)}
                                            </h1>
                                            <div className="flex items-center gap-4 text-muted-foreground">
                                                <div className="flex items-center gap-2">
                                                    <div className="w-6 h-6 rounded-full bg-card flex items-center justify-center border border-border/30">
                                                        <span className="material-symbols-outlined text-primary text-[14px]">smart_toy</span>
                                                    </div>
                                                    <span className="text-xs font-medium">Intelligence System Alpha</span>
                                                </div>
                                                <span className="text-[10px] uppercase tracking-widest">
                                                    Confidence Score: 98%
                                                </span>
                                            </div>
                                        </header>

                                        {/* Report Body — Markdown Renderer */}
                                        {selectedDigest.full_report_md ? (
                                            <div className="prose prose-invert prose-sm max-w-none font-display
                                                prose-headings:text-foreground prose-headings:font-semibold
                                                prose-h2:text-[16px] prose-h2:border-b prose-h2:border-border/20 prose-h2:pb-2 prose-h2:mb-3 prose-h2:mt-5
                                                prose-h3:text-[14px] prose-h3:text-primary/80 prose-h3:mb-2 prose-h3:mt-4
                                                prose-p:text-muted-foreground prose-p:text-[13px] prose-p:leading-[1.6] prose-p:my-2
                                                prose-li:text-muted-foreground prose-li:text-[13px] prose-li:leading-[1.6] prose-li:my-0.5
                                                prose-ul:space-y-1 prose-ul:mb-3
                                                prose-strong:text-foreground prose-strong:font-semibold
                                                prose-a:text-primary prose-a:no-underline hover:prose-a:underline
                                                prose-code:text-primary/90 prose-code:bg-primary/10 prose-code:px-1 prose-code:py-0.5 prose-code:rounded prose-code:text-xs
                                                prose-pre:bg-[hsl(136,28%,7%)] prose-pre:border prose-pre:border-border prose-pre:rounded-lg
                                                prose-blockquote:border-l-2 prose-blockquote:border-l-primary prose-blockquote:bg-primary/5 prose-blockquote:rounded-r-lg prose-blockquote:py-1 prose-blockquote:px-4 prose-blockquote:italic
                                                prose-table:text-sm prose-th:text-muted-foreground prose-th:text-[11px] prose-th:uppercase prose-th:tracking-wider prose-th:font-semibold
                                                prose-td:text-foreground/70
                                                prose-hr:border-border"
                                            >
                                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                                    {selectedDigest.full_report_md}
                                                </ReactMarkdown>
                                            </div>
                                        ) : selectedDigest.summary ? (
                                            <div className="prose prose-invert prose-sm max-w-none font-display prose-p:text-muted-foreground prose-p:text-[13px] prose-p:leading-[1.6]">
                                                <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                                    {selectedDigest.summary}
                                                </ReactMarkdown>
                                            </div>
                                        ) : (
                                            <p className="text-sm text-muted-foreground italic">No report content available.</p>
                                        )}

                                        {/* Bottom padding for action bar */}
                                        <div className="h-20" />
                                    </div>

                                    {/* Pinned Bottom Action Bar */}
                                    <footer className="absolute bottom-0 left-0 right-0 h-14 bg-background/95 backdrop-blur-md border-t border-border/30 px-6 flex items-center gap-4 z-10">
                                        <div className="flex-1 bg-card/30 border border-border/50 rounded-lg px-4 h-9 flex items-center focus-within:border-primary/50 transition-all">
                                            <span className="material-symbols-outlined text-muted-foreground text-[18px] mr-2">edit_note</span>
                                            <input
                                                type="text"
                                                value={sendComment}
                                                onChange={(e) => setSendComment(e.target.value)}
                                                placeholder="Add a note for Simone (optional)..."
                                                className="bg-transparent border-none focus:ring-0 focus:outline-none text-xs w-full text-foreground placeholder:text-muted-foreground/40"
                                            />
                                        </div>
                                        <button
                                            onClick={() => void sendToSimone(selectedDigest)}
                                            disabled={sendBusy}
                                            className="bg-accent hover:bg-accent/90 transition-all text-primary-foreground px-4 h-9 rounded-lg text-xs font-bold flex items-center gap-2 shadow-[0_0_20px_rgba(212,160,86,0.2)] disabled:opacity-60"
                                        >
                                            <span className="material-symbols-outlined text-[18px]">send</span>
                                            {sendBusy ? "Sending…" : "Send to Simone"}
                                        </button>
                                    </footer>

                                    {/* Send status */}
                                    {sendStatus && (
                                        <div className={`absolute bottom-16 left-6 right-6 text-xs z-10 ${sendStatus.startsWith("✓") ? "text-primary" : "text-secondary"}`}>
                                            {sendStatus}
                                        </div>
                                    )}
                                </>
                            )}
                        </section>

                    </div>
                </main>
            )}
        </div>
    );
}
