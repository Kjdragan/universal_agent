"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { formatDateTimeTz, toEpochMs } from "@/lib/timezone";
import Image from "next/image";

/* ── Design Tokens (Stitch: Kinetic Command Deck) ───────────────────── */

const T = {
    bg: "#0b1326",
    surfaceDim: "#0f1a33",
    surfaceLow: "#131f3d",
    surfaceHigh: "#1a2847",
    surfaceBright: "#223054",
    cyan: "#22D3EE",
    cyanDim: "rgba(34,211,238,0.12)",
    cyanGhost: "rgba(34,211,238,0.20)",
    amber: "#EE9800",
    amberDim: "rgba(238,152,0,0.12)",
    green: "#4ADE80",
    red: "#EF4444",
    redDim: "rgba(239,68,68,0.12)",
    textPrimary: "#E2E8F0",
    textSecondary: "#BBC9CD",
    textMuted: "#64748B",
    ghostBorder: "rgba(187,201,205,0.15)",
    fontMono: "'JetBrains Mono', 'Fira Code', monospace",
    fontUi: "'Inter', system-ui, sans-serif",
};

/* ── Types ──────────────────────────────────────────────────────────── */

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

/* ── Source Mapping ─────────────────────────────────────────────────── */

type SourceKey = "reddit" | "threads" | "youtube" | "global" | "unknown";

function contentSource(eventType: string): SourceKey {
    const t = (eventType || "").toLowerCase();
    if (t.includes("reddit")) return "reddit";
    if (t.includes("threads")) return "threads";
    if (t.includes("rss") || t.includes("youtube")) return "youtube";
    if (t.includes("global") || t.includes("batch") || t.includes("brief")) return "global";
    return "unknown";
}

const SOURCE_META: Record<SourceKey, { label: string; icon: string | null; color: string; badge: string }> = {
    reddit:  { label: "Reddit",       icon: "/assets/icons/sources/reddit.png",  color: "#f97316", badge: "RD" },
    threads: { label: "Threads",      icon: "/assets/icons/sources/threads.png", color: "#a855f7", badge: "TH" },
    youtube: { label: "YouTube",      icon: "/assets/icons/sources/youtube.png", color: "#ef4444", badge: "YT" },
    global:  { label: "Global Brief", icon: null,                                 color: T.cyan,    badge: "GB" },
    unknown: { label: "Other",        icon: null,                                 color: T.textMuted, badge: "??" },
};

/* ── Helpers ────────────────────────────────────────────────────────── */

function timeAgo(dateStr: string): string {
    const ts = toEpochMs(dateStr);
    if (ts === null) return "--";
    const delta = Math.max(0, (Date.now() - ts) / 1000);
    if (delta < 60) return "just now";
    if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
    if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
    return `${Math.floor(delta / 86400)}d ago`;
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

/* ── Source Icon Sub-component ──────────────────────────────────────── */

function SourceIcon({ source, size = 20 }: { source: string; size?: number }) {
    const meta = SOURCE_META[contentSource(source)];
    if (meta.icon) {
        return (
            <Image
                src={meta.icon}
                alt={meta.label}
                width={size}
                height={size}
                style={{ borderRadius: 0, objectFit: "contain" }}
            />
        );
    }
    /* Fallback for global / unknown — use material icon */
    const iconName = contentSource(source) === "global" ? "language" : "article";
    return (
        <span
            className="material-symbols-outlined"
            style={{ fontSize: size, color: meta.color, lineHeight: 1 }}
        >
            {iconName}
        </span>
    );
}

/* ── Component ─────────────────────────────────────────────────────── */

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

    /* ── Data Loading ─────────────────────────────────────────────── */

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
        } catch (err: unknown) {
            setError(err instanceof Error ? err.message : String(err));
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        void loadData();
        const timer = window.setInterval(() => void loadData(), 30_000);
        return () => window.clearInterval(timer);
    }, [loadData]);

    /* ── Source filters ───────────────────────────────────────────── */

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

    /* ── Summary stats ───────────────────────────────────────────── */

    const latestTime = digests.length > 0 ? formatDateTimeTz(digests[0].created_at, { placeholder: "N/A" }) : "N/A";

    const sourceMix = useMemo(() => {
        const counts: Record<string, number> = {};
        digests.forEach((d) => {
            const cs = contentSource(d.event_type);
            counts[cs] = (counts[cs] || 0) + 1;
        });
        return counts;
    }, [digests]);

    /* ── Actions ──────────────────────────────────────────────────── */

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
        } catch (err: unknown) {
            setPurgeStatus(`Purge failed: ${err instanceof Error ? err.message : String(err)}`);
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
        } catch (err: unknown) {
            setPurgeStatus(`Clear failed: ${err instanceof Error ? err.message : String(err)}`);
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
        } catch (err: unknown) {
            setSendStatus(`Send failed: ${err instanceof Error ? err.message : String(err)}`);
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

    /* ── Render ────────────────────────────────────────────────────── */

    return (
        <div
            style={{
                background: T.bg,
                color: T.textPrimary,
                fontFamily: T.fontUi,
                display: "flex",
                flexDirection: "column",
                height: "100%",
                minHeight: 0,
            }}
        >
            {/* ═══════════ Header Bar ═══════════ */}
            <header
                style={{
                    background: T.surfaceDim,
                    padding: "0 24px",
                    height: 56,
                    display: "flex",
                    alignItems: "center",
                    justifyContent: "space-between",
                    borderBottom: `1px solid ${T.ghostBorder}`,
                    flexShrink: 0,
                }}
            >
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                    <span className="material-symbols-outlined" style={{ fontSize: 22, color: T.cyan }}>
                        sensors
                    </span>
                    <h1
                        style={{
                            fontFamily: T.fontMono,
                            fontSize: 15,
                            fontWeight: 700,
                            letterSpacing: "0.1em",
                            margin: 0,
                            color: T.textPrimary,
                        }}
                    >
                        CSI FEED
                    </h1>
                    {/* Telemetry strip */}
                    {!loading && (
                        <div
                            style={{
                                display: "flex",
                                alignItems: "center",
                                gap: 16,
                                marginLeft: 20,
                                fontFamily: T.fontMono,
                                fontSize: 11,
                                color: T.textMuted,
                            }}
                        >
                            <span>{totalDigests} reports</span>
                            <span style={{ opacity: 0.3 }}>│</span>
                            <span>Latest: {latestTime}</span>
                            {digests.length > 0 && (
                                <span style={{ color: T.cyan }}>({timeAgo(digests[0].created_at)})</span>
                            )}
                            <span style={{ opacity: 0.3 }}>│</span>
                            {Object.entries(sourceMix)
                                .sort((a, b) => b[1] - a[1])
                                .map(([src, count]) => (
                                    <span key={src} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
                                        <SourceIcon source={src} size={14} />
                                        <span>{count}</span>
                                    </span>
                                ))}
                        </div>
                    )}
                </div>
                <nav style={{ display: "flex", alignItems: "center", gap: 4 }}>
                    <HeaderBtn label="CLEAR ALL" onClick={clearAllDigests} disabled={purgeBusy || digests.length === 0} />
                    <HeaderBtn label="PURGE STALE" onClick={purgeData} disabled={purgeBusy} accent />
                    <HeaderBtn label="↻ REFRESH" onClick={() => void loadData()} />
                    <div style={{ width: 1, height: 20, background: T.ghostBorder, margin: "0 8px" }} />
                    <NavIcon icon="chat" title="Chat" onClick={() => { const w = window.open("/?new_session=1&focus_input=1", "ua-chat-window"); if (w) w.focus(); }} />
                    <NavIcon icon="home" title="Home" onClick={() => router.push("/dashboard")} />
                </nav>
            </header>

            {/* ═══════════ Purge Status ═══════════ */}
            {purgeStatus && (
                <div
                    style={{
                        background: T.surfaceLow,
                        padding: "6px 24px",
                        fontFamily: T.fontMono,
                        fontSize: 11,
                        color: T.textSecondary,
                        borderBottom: `1px solid ${T.ghostBorder}`,
                        flexShrink: 0,
                    }}
                >
                    {purgeStatus}
                </div>
            )}

            {/* ═══════════ Source Filter Tabs ═══════════ */}
            <div
                style={{
                    background: T.surfaceDim,
                    padding: "0 24px",
                    display: "flex",
                    gap: 0,
                    borderBottom: `1px solid ${T.ghostBorder}`,
                    flexShrink: 0,
                }}
            >
                <FilterTab
                    label="ALL"
                    active={sourceFilter === "all"}
                    onClick={() => setSourceFilter("all")}
                    count={digests.length}
                />
                {sources.map((src) => (
                    <FilterTab
                        key={src}
                        label={SOURCE_META[src as SourceKey]?.label.toUpperCase() ?? src.toUpperCase()}
                        active={sourceFilter === src}
                        onClick={() => setSourceFilter(src)}
                        count={sourceMix[src] || 0}
                        icon={<SourceIcon source={src} size={14} />}
                    />
                ))}
            </div>

            {/* ═══════════ Error State ═══════════ */}
            {error && (
                <div
                    style={{
                        background: T.redDim,
                        color: T.red,
                        padding: "8px 24px",
                        fontFamily: T.fontMono,
                        fontSize: 12,
                        flexShrink: 0,
                    }}
                >
                    ⚠ {error}
                </div>
            )}

            {/* ═══════════ Loading State ═══════════ */}
            {loading && (
                <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <span style={{ fontFamily: T.fontMono, fontSize: 12, color: T.textMuted }}>
                        Loading digests…
                    </span>
                </div>
            )}

            {/* ═══════════ Empty State ═══════════ */}
            {!loading && !error && filteredDigests.length === 0 && (
                <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
                    <span className="material-symbols-outlined" style={{ fontSize: 48, color: T.textMuted, opacity: 0.4, marginBottom: 12 }}>
                        satellite_alt
                    </span>
                    <span style={{ fontFamily: T.fontMono, fontSize: 13, fontWeight: 600, color: T.textSecondary }}>
                        NO DIGESTS YET
                    </span>
                    <span style={{ fontSize: 12, color: T.textMuted, marginTop: 4, maxWidth: 320, textAlign: "center" }}>
                        CSI trend reports will appear here as they are generated by your hourly analysis pipeline.
                    </span>
                </div>
            )}

            {/* ═══════════ Main Two-Panel Layout ═══════════ */}
            {!loading && !error && filteredDigests.length > 0 && (
                <main style={{ flex: 1, display: "flex", overflow: "hidden" }}>
                    {/* ── Left Panel: Report List ── */}
                    <aside
                        style={{
                            width: 420,
                            minWidth: 420,
                            background: T.bg,
                            borderRight: `1px solid ${T.ghostBorder}`,
                            display: "flex",
                            flexDirection: "column",
                            height: "100%",
                        }}
                    >
                        <div style={{ padding: "12px 16px", display: "flex", alignItems: "center", justifyContent: "space-between", flexShrink: 0 }}>
                            <span
                                style={{
                                    fontFamily: T.fontMono,
                                    fontSize: 10,
                                    fontWeight: 700,
                                    letterSpacing: "0.1em",
                                    color: T.textMuted,
                                }}
                            >
                                REPORTS
                            </span>
                            <span
                                style={{
                                    fontFamily: T.fontMono,
                                    fontSize: 9,
                                    fontWeight: 700,
                                    letterSpacing: "0.05em",
                                    padding: "2px 8px",
                                    background: T.cyanDim,
                                    color: T.cyan,
                                }}
                            >
                                LIVE FEED
                            </span>
                        </div>
                        <div style={{ flex: 1, overflowY: "auto" }}>
                            {filteredDigests.map((digest) => {
                                const isSelected = selectedDigest?.id === digest.id;
                                const isExpanded = expandedSummaries.has(digest.id);
                                const summary = extractSummary(digest);
                                return (
                                    <ReportCard
                                        key={digest.id}
                                        digest={digest}
                                        summary={summary}
                                        isSelected={isSelected}
                                        isExpanded={isExpanded}
                                        onSelect={() => {
                                            setSelectedDigest(digest);
                                            setSendStatus(null);
                                            setSendComment("");
                                        }}
                                        onToggleExpand={(e) => toggleSummaryExpand(digest.id, e)}
                                        onDismiss={(e) => dismissDigest(digest.id, e)}
                                    />
                                );
                            })}
                        </div>
                    </aside>

                    {/* ── Right Panel: Report Reader ── */}
                    <section
                        style={{
                            flex: 1,
                            minWidth: 0,
                            background: T.surfaceDim,
                            display: "flex",
                            flexDirection: "column",
                            position: "relative",
                            height: "100%",
                        }}
                    >
                        {!selectedDigest ? (
                            <div style={{ flex: 1, display: "flex", flexDirection: "column", alignItems: "center", justifyContent: "center" }}>
                                <span style={{ fontSize: 32, opacity: 0.3, color: T.textMuted, marginBottom: 8 }}>←</span>
                                <span style={{ fontFamily: T.fontMono, fontSize: 12, color: T.textMuted }}>Select a report to read</span>
                            </div>
                        ) : (
                            <>
                                {/* Scrollable content */}
                                <div style={{ flex: 1, overflowY: "auto", padding: "32px 40px 80px 32px" }}>
                                    {/* Report Header */}
                                    <header style={{ marginBottom: 32 }}>
                                        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
                                            <span
                                                style={{
                                                    fontFamily: T.fontMono,
                                                    fontSize: 10,
                                                    fontWeight: 700,
                                                    padding: "3px 10px",
                                                    background: T.cyanDim,
                                                    color: T.cyan,
                                                    letterSpacing: "0.1em",
                                                }}
                                            >
                                                {SOURCE_META[contentSource(selectedDigest.event_type)]?.label.toUpperCase()}
                                            </span>
                                            <span style={{ fontFamily: T.fontMono, fontSize: 11, color: T.textMuted }}>
                                                {formatDateTimeTz(selectedDigest.created_at, { placeholder: "--" })}
                                            </span>
                                            {/* Source type badges */}
                                            {selectedDigest.source_types && selectedDigest.source_types.length > 0 && (
                                                <div style={{ display: "flex", gap: 4, marginLeft: "auto" }}>
                                                    {selectedDigest.source_types.map((st) => (
                                                        <span
                                                            key={st}
                                                            style={{
                                                                width: 22,
                                                                height: 22,
                                                                background: T.surfaceHigh,
                                                                display: "flex",
                                                                alignItems: "center",
                                                                justifyContent: "center",
                                                                fontFamily: T.fontMono,
                                                                fontSize: 9,
                                                                fontWeight: 700,
                                                                color: T.textSecondary,
                                                            }}
                                                        >
                                                            {SOURCE_META[contentSource(st)]?.badge}
                                                        </span>
                                                    ))}
                                                </div>
                                            )}
                                        </div>
                                        <h1
                                            style={{
                                                fontSize: 22,
                                                fontWeight: 700,
                                                color: T.textPrimary,
                                                letterSpacing: "-0.02em",
                                                lineHeight: 1.25,
                                                marginBottom: 16,
                                            }}
                                        >
                                            {extractHeadline(selectedDigest)}
                                        </h1>
                                        <div style={{ display: "flex", alignItems: "center", gap: 16 }}>
                                            <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                                                <Image
                                                    src="/assets/avatars/simone.png"
                                                    alt="Simone"
                                                    width={24}
                                                    height={24}
                                                    style={{ borderRadius: 0, objectFit: "cover" }}
                                                />
                                                <span style={{ fontFamily: T.fontMono, fontSize: 11, color: T.textSecondary }}>
                                                    Intelligence System Alpha
                                                </span>
                                            </div>
                                            <span
                                                style={{
                                                    fontFamily: T.fontMono,
                                                    fontSize: 10,
                                                    letterSpacing: "0.1em",
                                                    color: T.textMuted,
                                                }}
                                            >
                                                CONFIDENCE: 98%
                                            </span>
                                        </div>
                                    </header>

                                    {/* Report Body — Markdown */}
                                    {selectedDigest.full_report_md ? (
                                        <div
                                            className="prose prose-invert prose-sm max-w-none"
                                            style={{
                                                fontFamily: T.fontUi,
                                                color: T.textSecondary,
                                                fontSize: 13,
                                                lineHeight: 1.7,
                                                /* Stitch overrides via CSS variables */
                                                ["--tw-prose-body" as string]: T.textSecondary,
                                                ["--tw-prose-headings" as string]: T.textPrimary,
                                                ["--tw-prose-links" as string]: T.cyan,
                                                ["--tw-prose-bold" as string]: T.textPrimary,
                                                ["--tw-prose-code" as string]: T.cyan,
                                                ["--tw-prose-quotes" as string]: T.textSecondary,
                                                ["--tw-prose-quote-borders" as string]: T.cyan,
                                                ["--tw-prose-hr" as string]: T.ghostBorder,
                                                ["--tw-prose-th-borders" as string]: T.ghostBorder,
                                                ["--tw-prose-td-borders" as string]: T.ghostBorder,
                                            }}
                                        >
                                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                                {selectedDigest.full_report_md}
                                            </ReactMarkdown>
                                        </div>
                                    ) : selectedDigest.summary ? (
                                        <div
                                            className="prose prose-invert prose-sm max-w-none"
                                            style={{ fontFamily: T.fontUi, color: T.textSecondary, fontSize: 13, lineHeight: 1.7 }}
                                        >
                                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                                {selectedDigest.summary}
                                            </ReactMarkdown>
                                        </div>
                                    ) : (
                                        <p style={{ fontSize: 13, color: T.textMuted, fontStyle: "italic" }}>
                                            No report content available.
                                        </p>
                                    )}
                                </div>

                                {/* Pinned Bottom Action Bar */}
                                <footer
                                    style={{
                                        position: "absolute",
                                        bottom: 0,
                                        left: 0,
                                        right: 0,
                                        height: 56,
                                        background: `${T.surfaceDim}f0`,
                                        backdropFilter: "blur(12px)",
                                        borderTop: `1px solid ${T.ghostBorder}`,
                                        padding: "0 24px",
                                        display: "flex",
                                        alignItems: "center",
                                        gap: 12,
                                        zIndex: 10,
                                    }}
                                >
                                    <div
                                        style={{
                                            flex: 1,
                                            background: T.surfaceLow,
                                            padding: "0 12px",
                                            height: 36,
                                            display: "flex",
                                            alignItems: "center",
                                            gap: 8,
                                        }}
                                    >
                                        <span className="material-symbols-outlined" style={{ fontSize: 16, color: T.textMuted }}>
                                            edit_note
                                        </span>
                                        <input
                                            type="text"
                                            value={sendComment}
                                            onChange={(e) => setSendComment(e.target.value)}
                                            placeholder="Add a note for Simone (optional)..."
                                            style={{
                                                background: "transparent",
                                                border: "none",
                                                outline: "none",
                                                fontFamily: T.fontUi,
                                                fontSize: 12,
                                                color: T.textPrimary,
                                                width: "100%",
                                            }}
                                        />
                                    </div>
                                    <button
                                        onClick={() => void sendToSimone(selectedDigest)}
                                        disabled={sendBusy}
                                        style={{
                                            background: sendBusy ? T.amberDim : T.amber,
                                            color: sendBusy ? T.amber : T.bg,
                                            border: "none",
                                            padding: "0 16px",
                                            height: 36,
                                            fontFamily: T.fontMono,
                                            fontSize: 11,
                                            fontWeight: 700,
                                            letterSpacing: "0.05em",
                                            cursor: sendBusy ? "wait" : "pointer",
                                            display: "flex",
                                            alignItems: "center",
                                            gap: 8,
                                        }}
                                    >
                                        <Image
                                            src="/assets/avatars/simone.png"
                                            alt="Simone"
                                            width={20}
                                            height={20}
                                            style={{ borderRadius: 0, objectFit: "cover" }}
                                        />
                                        {sendBusy ? "SENDING…" : "SEND TO SIMONE"}
                                    </button>
                                </footer>

                                {/* Send status */}
                                {sendStatus && (
                                    <div
                                        style={{
                                            position: "absolute",
                                            bottom: 60,
                                            left: 24,
                                            right: 24,
                                            fontFamily: T.fontMono,
                                            fontSize: 11,
                                            color: sendStatus.startsWith("✓") ? T.cyan : T.amber,
                                            zIndex: 10,
                                        }}
                                    >
                                        {sendStatus}
                                    </div>
                                )}
                            </>
                        )}
                    </section>
                </main>
            )}
        </div>
    );
}

/* ── Sub-components ────────────────────────────────────────────────── */

function HeaderBtn({ label, onClick, disabled, accent }: { label: string; onClick: () => void; disabled?: boolean; accent?: boolean }) {
    return (
        <button
            onClick={onClick}
            disabled={disabled}
            style={{
                background: accent ? T.amberDim : "transparent",
                color: accent ? T.amber : T.textMuted,
                border: "none",
                padding: "6px 12px",
                fontFamily: T.fontMono,
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: "0.05em",
                cursor: disabled ? "default" : "pointer",
                opacity: disabled ? 0.4 : 1,
                transition: "all 0.15s",
            }}
        >
            {label}
        </button>
    );
}

function NavIcon({ icon, title, onClick }: { icon: string; title: string; onClick: () => void }) {
    return (
        <span
            className="material-symbols-outlined"
            onClick={onClick}
            title={title}
            style={{
                fontSize: 20,
                color: T.textMuted,
                cursor: "pointer",
                padding: 4,
                transition: "color 0.15s",
            }}
            onMouseEnter={(e) => { (e.target as HTMLElement).style.color = T.cyan; }}
            onMouseLeave={(e) => { (e.target as HTMLElement).style.color = T.textMuted; }}
        >
            {icon}
        </span>
    );
}

function FilterTab({
    label,
    active,
    onClick,
    count,
    icon,
}: {
    label: string;
    active: boolean;
    onClick: () => void;
    count: number;
    icon?: React.ReactNode;
}) {
    return (
        <button
            onClick={onClick}
            style={{
                background: active ? T.cyanDim : "transparent",
                color: active ? T.cyan : T.textMuted,
                border: "none",
                borderBottom: active ? `2px solid ${T.cyan}` : "2px solid transparent",
                padding: "10px 16px",
                fontFamily: T.fontMono,
                fontSize: 11,
                fontWeight: 600,
                letterSpacing: "0.05em",
                cursor: "pointer",
                display: "flex",
                alignItems: "center",
                gap: 6,
                transition: "all 0.15s",
            }}
        >
            {icon}
            {label}
            <span
                style={{
                    background: active ? T.cyan : T.ghostBorder,
                    color: active ? T.bg : T.textMuted,
                    fontSize: 9,
                    fontWeight: 700,
                    padding: "1px 5px",
                    minWidth: 16,
                    textAlign: "center",
                }}
            >
                {count}
            </span>
        </button>
    );
}

function ReportCard({
    digest,
    summary,
    isSelected,
    isExpanded,
    onSelect,
    onToggleExpand,
    onDismiss,
}: {
    digest: CSIDigest;
    summary: string;
    isSelected: boolean;
    isExpanded: boolean;
    onSelect: () => void;
    onToggleExpand: (e: React.MouseEvent) => void;
    onDismiss: (e: React.MouseEvent) => void;
}) {
    const [hovered, setHovered] = useState(false);
    return (
        <article
            onClick={onSelect}
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
            style={{
                padding: "14px 16px",
                cursor: "pointer",
                borderLeft: isSelected ? `3px solid ${T.cyan}` : "3px solid transparent",
                background: isSelected ? T.cyanDim : hovered ? T.surfaceLow : "transparent",
                transition: "all 0.12s",
                position: "relative",
                display: "flex",
                gap: 10,
            }}
        >
            {/* Source Icon */}
            <span style={{ marginTop: 2, flexShrink: 0 }}>
                <SourceIcon source={digest.event_type} size={22} />
            </span>
            <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 8, marginBottom: 4 }}>
                    <h3
                        style={{
                            fontSize: 14,
                            fontWeight: 600,
                            color: T.textPrimary,
                            lineHeight: 1.3,
                            margin: 0,
                            overflow: "hidden",
                            display: "-webkit-box",
                            WebkitLineClamp: 2,
                            WebkitBoxOrient: "vertical",
                        }}
                    >
                        {extractHeadline(digest)}
                    </h3>
                    <span
                        style={{
                            fontFamily: T.fontMono,
                            fontSize: 10,
                            color: isSelected ? T.cyan : T.textMuted,
                            flexShrink: 0,
                            whiteSpace: "nowrap",
                        }}
                    >
                        {timeAgo(digest.created_at)}
                    </span>
                </div>
                {summary && (
                    <>
                        <p
                            style={{
                                fontSize: 12,
                                color: T.textMuted,
                                lineHeight: 1.6,
                                margin: 0,
                                overflow: isExpanded ? "visible" : "hidden",
                                display: isExpanded ? "block" : "-webkit-box",
                                WebkitLineClamp: isExpanded ? undefined : 3,
                                WebkitBoxOrient: "vertical",
                            }}
                        >
                            {summary}
                        </p>
                        {summary.length > 100 && (
                            <button
                                onClick={onToggleExpand}
                                style={{
                                    background: "none",
                                    border: "none",
                                    fontFamily: T.fontMono,
                                    fontSize: 10,
                                    color: T.textMuted,
                                    cursor: "pointer",
                                    padding: 0,
                                    marginTop: 4,
                                }}
                            >
                                {isExpanded ? "Show less ▲" : "Show more ▼"}
                            </button>
                        )}
                    </>
                )}
            </div>
            {/* Trash icon on hover */}
            {hovered && (
                <button
                    onClick={onDismiss}
                    title="Delete report"
                    style={{
                        position: "absolute",
                        bottom: 8,
                        right: 8,
                        background: "none",
                        border: "none",
                        cursor: "pointer",
                        padding: 2,
                    }}
                >
                    <Image
                        src="/assets/icons/system/trash.png"
                        alt="Delete"
                        width={16}
                        height={16}
                        style={{ opacity: 0.7 }}
                    />
                </button>
            )}
        </article>
    );
}
