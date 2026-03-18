"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

const API_BASE = "/api/dashboard/gateway";

/** Format ISO timestamp to abbreviated human-readable form, e.g. "Mar 14, 4:35 PM" */
function formatDate(iso: string | undefined): string {
    if (!iso) return "";
    try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return iso;
        const now = new Date();
        const sameYear = d.getFullYear() === now.getFullYear();
        const month = d.toLocaleString("en-US", { month: "short" });
        const day = d.getDate();
        const time = d.toLocaleString("en-US", { hour: "numeric", minute: "2-digit", hour12: true });
        if (sameYear) return `${month} ${day}, ${time}`;
        return `${month} ${day} '${String(d.getFullYear()).slice(2)}, ${time}`;
    } catch {
        return iso;
    }
}

type Approval = {
    approval_id: string;
    task_id?: string;
    phase_id?: string;
    status: string;
    raw_status?: string;
    title?: string;
    summary?: string;
    requested_by?: string;
    approved_by?: string;
    created_at?: string;
    updated_at?: string;
    priority?: number;
    focus_href?: string;
    approval_source?: string;
    source_kind?: string;
    metadata?: Record<string, unknown>;
};

/** Pull a plain-English summary out of the raw approval data. */
function humanSummary(approval: Approval): string {
    const meta = approval.metadata || {};
    const subject = (meta.subject_json ?? meta.subject ?? meta.description ?? "") as string;

    // Try to parse subject_json if it's a string
    let subjectObj: Record<string, unknown> = {};
    if (typeof subject === "string" && subject.trim().startsWith("{")) {
        try { subjectObj = JSON.parse(subject); } catch { /* ignore */ }
    } else if (typeof subject === "object" && subject !== null) {
        subjectObj = subject as Record<string, unknown>;
    }

    const title = approval.title || "";

    // Auto-remediation failure
    if (title.toLowerCase().includes("auto-remediation") || title.toLowerCase().includes("remediation failed")) {
        const degraded = (subjectObj.degraded_sources as string[] | undefined) || [];
        const failing = (subjectObj.failing_sources as string[] | undefined) || [];
        const executed = (subjectObj.executed_actions as Array<Record<string, unknown>> | undefined) || [];
        const failed = executed.filter((a) => !a.success);
        const health = (subjectObj.health_status as string) || "unknown";
        const parts: string[] = [];
        if (failing.length) parts.push(`Failing sources: ${failing.join(", ")}`);
        if (degraded.length) parts.push(`Degraded sources: ${degraded.join(", ")}`);
        if (failed.length) {
            const details = failed.map((a) => {
                const r = (a.result as Record<string, unknown>) || {};
                return `${a.source} (${r.detail || "failed"})`;
            });
            parts.push(`Failed actions: ${details.join("; ")}`);
        }
        return `Delivery pipeline health is ${health}. The CSI system tried to replay failed messages automatically but it didn't work. ${parts.join(". ")}`;
    }

    // SLO breach
    if (title.toLowerCase().includes("slo") || title.toLowerCase().includes("reliability")) {
        const breaches = (subjectObj.breaches as Array<Record<string, unknown>> | undefined) || [];
        const metrics = (subjectObj.metrics as Record<string, unknown> | undefined) || {};
        const ratio = metrics.delivery_success_ratio as number | undefined;
        const parts: string[] = [];
        if (ratio !== undefined) parts.push(`Delivery success rate: ${(ratio * 100).toFixed(1)}% (target: 98%)`);
        for (const b of breaches) {
            if (b.code === "canary_regression_frequency_exceeds_max") {
                parts.push(`Canary regressions: ${b.actual} (max allowed: ${b.threshold})`);
            }
        }
        return `Daily delivery metrics fell below target yesterday. ${parts.join(". ")} The system needs to re-run failed delivery jobs.`;
    }

    // Brief review
    if (title.toLowerCase().includes("brief") || title.toLowerCase().includes("trend brief")) {
        const slot = (subjectObj.slot_display ?? subjectObj.slot ?? "") as string;
        const date = (subjectObj.local_date ?? "") as string;
        return `Scheduled reminder to review the CSI global trend brief${slot ? ` (${slot} slot)` : ""}${date ? ` for ${date}` : ""}. The agent reads this artifact and acknowledges it — no human action needed.`;
    }

    // Fallback
    return approval.summary || "Review and take action on this approval request.";
}

/** Determine if this is a human or agent approval based on metadata. */
function reviewerLabel(approval: Approval): { label: string; color: string } {
    const meta = approval.metadata || {};
    const csi = (meta.csi || {}) as Record<string, unknown>;
    const routing = (csi.routing_state as string) || "";
    const reason = (csi.human_intervention_reason as string) || (csi.routing_reason as string) || "";

    // Explicit routing state from task_hub
    if (routing === "human_intervention_required") {
        return { label: "Human Review", color: "bg-red-500/15 text-red-400 border border-red-500/30" };
    }
    if (routing === "agent_actionable") {
        return { label: "Agent Review", color: "bg-primary/15 text-primary border border-primary/30" };
    }

    // Infer from title / reason
    const titleLc = (approval.title || "").toLowerCase();
    if (
        reason.toLowerCase().includes("human") ||
        reason.toLowerCase().includes("persistent_failure") ||
        titleLc.includes("auto-remediation")
    ) {
        return { label: "Human Review", color: "bg-red-500/15 text-red-400 border border-red-500/30" };
    }
    if (titleLc.includes("brief") || titleLc.includes("slo")) {
        return { label: "Agent Review", color: "bg-primary/15 text-primary border border-primary/30" };
    }

    return { label: "Needs Review", color: "bg-amber-500/15 text-accent border border-accent/30" };
}

export default function ApprovalsPage() {
    const [approvals, setApprovals] = useState<Approval[]>([]);
    const [loading, setLoading] = useState(true);
    const [updatingId, setUpdatingId] = useState<string | null>(null);
    const [statusFilter, setStatusFilter] = useState<string>("pending");
    const [errorMsg, setErrorMsg] = useState<string | null>(null);
    const [clearing, setClearing] = useState(false);

    const load = useCallback(async () => {
        setLoading(true);
        try {
            const url =
                statusFilter === "all"
                    ? `${API_BASE}/api/v1/ops/approvals`
                    : `${API_BASE}/api/v1/ops/approvals?status=${encodeURIComponent(statusFilter)}`;
            const res = await fetch(url);
            if (!res.ok) {
                setApprovals([]);
                return;
            }
            const data = await res.json();
            setApprovals(Array.isArray(data.approvals) ? data.approvals : []);
        } finally {
            setLoading(false);
        }
    }, [statusFilter]);

    useEffect(() => {
        load();
        const timer = window.setInterval(load, 6000);
        return () => window.clearInterval(timer);
    }, [load]);

    const updateApproval = useCallback(
        async (approvalId: string, status: "approved" | "rejected") => {
            setUpdatingId(approvalId);
            setErrorMsg(null);
            try {
                const res = await fetch(
                    `${API_BASE}/api/v1/ops/approvals/${encodeURIComponent(approvalId)}`,
                    {
                        method: "PATCH",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ status }),
                    },
                );
                if (!res.ok) {
                    let detail = `Failed to ${status} (HTTP ${res.status})`;
                    try {
                        const errBody = await res.json();
                        if (errBody.detail) detail = String(errBody.detail);
                    } catch { /* ignore parse errors */ }
                    setErrorMsg(detail);
                    return;
                }
                const data = await res.json();
                const updated = data.approval as Approval;
                setApprovals((prev) =>
                    prev.map((item) => (item.approval_id === approvalId ? updated : item)),
                );
            } catch (err) {
                setErrorMsg(`Network error: ${err instanceof Error ? err.message : String(err)}`);
            } finally {
                setUpdatingId((prev) => (prev === approvalId ? null : prev));
            }
        },
        [],
    );

    const clearAll = useCallback(async () => {
        if (!window.confirm("Clear ALL approvals? This cannot be undone.")) return;
        setClearing(true);
        setErrorMsg(null);
        try {
            const res = await fetch(`${API_BASE}/api/v1/ops/approvals`, { method: "DELETE" });
            if (!res.ok) {
                let detail = `Clear failed (HTTP ${res.status})`;
                try {
                    const errBody = await res.json();
                    if (errBody.detail) detail = String(errBody.detail);
                } catch { /* ignore */ }
                setErrorMsg(detail);
                return;
            }
            await load();
        } catch (err) {
            setErrorMsg(`Network error: ${err instanceof Error ? err.message : String(err)}`);
        } finally {
            setClearing(false);
        }
    }, [load]);

    const statusColor = (status: string) => {
        switch (status) {
            case "pending":
                return "text-accent";
            case "approved":
                return "text-primary";
            case "rejected":
                return "text-secondary";
            default:
                return "text-muted-foreground";
        }
    };

    const FILTER_OPTIONS = ["all", "pending", "approved", "rejected"] as const;

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-xl font-semibold tracking-tight">Approvals</h1>
                    <p className="text-sm text-muted-foreground">
                        Review and manage execution approval requests.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Link
                        href="/"
                        className="rounded-lg border border-primary/30 bg-primary/15 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-primary/90 hover:bg-primary/25"
                    >
                        Back to Home
                    </Link>
                    <div className="flex rounded-lg border border-border bg-card/60 text-xs">
                        {FILTER_OPTIONS.map((opt) => (
                            <button
                                key={opt}
                                type="button"
                                onClick={() => setStatusFilter(opt)}
                                className={[
                                    "px-2.5 py-1.5 capitalize transition",
                                    statusFilter === opt
                                        ? "bg-primary/15 text-primary/80"
                                        : "text-muted-foreground hover:text-foreground",
                                ].join(" ")}
                            >
                                {opt}
                            </button>
                        ))}
                    </div>
                    <button
                        type="button"
                        onClick={clearAll}
                        disabled={clearing || approvals.length === 0}
                        className="rounded-lg border border-red-400/30 bg-red-400/10 px-3 py-1.5 text-xs font-semibold text-red-400/80 hover:bg-red-400/20 disabled:opacity-40"
                    >
                        {clearing ? "Clearing…" : "Clear All"}
                    </button>
                    <button
                        type="button"
                        onClick={load}
                        className="rounded-lg border border-border bg-card/60 px-3 py-1.5 text-sm hover:bg-card"
                    >
                        Refresh
                    </button>
                </div>
            </div>

            {errorMsg && (
                <div className="rounded-xl border border-red-400/30 bg-red-400/10 p-3 text-sm text-red-400/80 flex items-center justify-between">
                    <span>⚠️ {errorMsg}</span>
                    <button type="button" onClick={() => setErrorMsg(null)} className="text-secondary hover:text-red-400/80 text-xs ml-3">Dismiss</button>
                </div>
            )}

            {loading && approvals.length === 0 && (
                <div className="rounded-xl border border-border bg-background/70 p-6 text-center text-sm text-muted-foreground">
                    Loading approvals…
                </div>
            )}

            {!loading && approvals.length === 0 && (
                <div className="rounded-xl border border-border bg-background/70 p-6 text-center text-sm text-muted-foreground">
                    No approvals found{statusFilter !== "all" ? ` with status "${statusFilter}"` : ""}.
                </div>
            )}

            <div className="space-y-3">
                {approvals.map((approval) => {
                    const reviewer = reviewerLabel(approval);
                    const summary = humanSummary(approval);
                    return (
                        <article
                            key={approval.approval_id}
                            className="rounded-xl border border-border bg-background/70 p-4"
                        >
                            <div className="flex items-start justify-between gap-3">
                                <div className="min-w-0 flex-1">
                                    {/* Title row with status + reviewer badge */}
                                    <div className="flex flex-wrap items-center gap-2 mb-1">
                                        <p className="text-sm font-semibold text-foreground leading-snug">
                                            {approval.title || approval.summary || approval.approval_id}
                                        </p>
                                        <span
                                            className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${statusColor(approval.status)}`}
                                        >
                                            {approval.status}
                                        </span>
                                        <span
                                            className={`rounded px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wider ${reviewer.color}`}
                                        >
                                            {reviewer.label}
                                        </span>
                                    </div>

                                    {/* Plain-English summary */}
                                    <p className="text-xs text-foreground/80 leading-relaxed mb-2">{summary}</p>

                                    {/* Compact ID */}
                                    <p className="font-mono text-[11px] text-muted mb-1">
                                        {approval.approval_id}
                                    </p>

                                    {/* Metadata row */}
                                    <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
                                        {approval.requested_by && <span>Requested by: {approval.requested_by}</span>}
                                        {approval.approved_by && <span>Decided by: {approval.approved_by}</span>}
                                        {approval.created_at && <span>Created: {formatDate(approval.created_at)}</span>}
                                        {approval.updated_at && <span>Updated: {formatDate(approval.updated_at)}</span>}
                                        {approval.priority !== undefined && <span>Priority: {approval.priority}</span>}
                                        {approval.raw_status && <span>Task status: {approval.raw_status}</span>}
                                        {approval.approval_source && <span>Source: {approval.approval_source}</span>}
                                    </div>
                                </div>

                                <div className="flex shrink-0 flex-wrap items-center gap-2">
                                    {approval.focus_href && (
                                        <Link
                                            href={approval.focus_href}
                                            className="rounded border border-primary/30 bg-primary/10 px-3 py-1.5 text-xs text-primary/80 hover:bg-primary/20"
                                        >
                                            Open
                                        </Link>
                                    )}
                                    {approval.status === "pending" && (
                                        <>
                                        <button
                                            type="button"
                                            onClick={() => updateApproval(approval.approval_id, "approved")}
                                            disabled={updatingId === approval.approval_id}
                                            className="rounded border border-primary/30 bg-primary/10 px-3 py-1.5 text-xs text-primary/80 hover:bg-primary/20 disabled:opacity-50"
                                        >
                                            Approve
                                        </button>
                                        <button
                                            type="button"
                                            onClick={() => updateApproval(approval.approval_id, "rejected")}
                                            disabled={updatingId === approval.approval_id}
                                            className="rounded border border-red-400/30 bg-red-400/10 px-3 py-1.5 text-xs text-red-400/80 hover:bg-red-400/20 disabled:opacity-50"
                                        >
                                            Reject
                                        </button>
                                        </>
                                    )}
                                </div>
                            </div>
                        </article>
                    );
                })}
            </div>
        </div>
    );
}
