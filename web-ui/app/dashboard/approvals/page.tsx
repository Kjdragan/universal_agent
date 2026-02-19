"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

const API_BASE = "/api/dashboard/gateway";

type Approval = {
    approval_id: string;
    phase_id?: string;
    status: string;
    summary?: string;
    requested_by?: string;
    approved_by?: string;
    created_at?: string;
    updated_at?: string;
    metadata?: Record<string, unknown>;
};

export default function ApprovalsPage() {
    const [approvals, setApprovals] = useState<Approval[]>([]);
    const [loading, setLoading] = useState(true);
    const [updatingId, setUpdatingId] = useState<string | null>(null);
    const [statusFilter, setStatusFilter] = useState<string>("all");

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
            try {
                const res = await fetch(
                    `${API_BASE}/api/v1/ops/approvals/${encodeURIComponent(approvalId)}`,
                    {
                        method: "PATCH",
                        headers: { "Content-Type": "application/json" },
                        body: JSON.stringify({ status }),
                    },
                );
                if (!res.ok) return;
                const data = await res.json();
                const updated = data.approval as Approval;
                setApprovals((prev) =>
                    prev.map((item) => (item.approval_id === approvalId ? updated : item)),
                );
            } finally {
                setUpdatingId((prev) => (prev === approvalId ? null : prev));
            }
        },
        [],
    );

    const statusColor = (status: string) => {
        switch (status) {
            case "pending":
                return "text-amber-300";
            case "approved":
                return "text-emerald-300";
            case "rejected":
                return "text-rose-300";
            default:
                return "text-slate-400";
        }
    };

    const FILTER_OPTIONS = ["all", "pending", "approved", "rejected"] as const;

    return (
        <div className="space-y-6">
            <div className="flex items-center justify-between">
                <div>
                    <h1 className="text-xl font-semibold tracking-tight">Approvals</h1>
                    <p className="text-sm text-slate-400">
                        Review and manage execution approval requests.
                    </p>
                </div>
                <div className="flex items-center gap-2">
                    <Link
                        href="/"
                        className="rounded-lg border border-cyan-700/60 bg-cyan-600/15 px-3 py-1.5 text-xs font-semibold uppercase tracking-wider text-cyan-100 hover:bg-cyan-600/25"
                    >
                        Back to Home
                    </Link>
                    <div className="flex rounded-lg border border-slate-700 bg-slate-800/60 text-xs">
                        {FILTER_OPTIONS.map((opt) => (
                            <button
                                key={opt}
                                type="button"
                                onClick={() => setStatusFilter(opt)}
                                className={[
                                    "px-2.5 py-1.5 capitalize transition",
                                    statusFilter === opt
                                        ? "bg-cyan-500/15 text-cyan-200"
                                        : "text-slate-400 hover:text-slate-200",
                                ].join(" ")}
                            >
                                {opt}
                            </button>
                        ))}
                    </div>
                    <button
                        type="button"
                        onClick={load}
                        className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-sm hover:bg-slate-800"
                    >
                        Refresh
                    </button>
                </div>
            </div>

            {loading && approvals.length === 0 && (
                <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-6 text-center text-sm text-slate-400">
                    Loading approvals…
                </div>
            )}

            {!loading && approvals.length === 0 && (
                <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-6 text-center text-sm text-slate-400">
                    No approvals found{statusFilter !== "all" ? ` with status "${statusFilter}"` : ""}.
                </div>
            )}

            <div className="space-y-3">
                {approvals.map((approval) => (
                    <article
                        key={approval.approval_id}
                        className="rounded-xl border border-slate-800 bg-slate-900/70 p-4"
                    >
                        <div className="flex items-start justify-between gap-3">
                            <div className="min-w-0 flex-1">
                                <div className="flex items-center gap-2">
                                    <p className="truncate font-mono text-xs text-slate-200">
                                        {approval.approval_id}
                                    </p>
                                    <span
                                        className={`text-[11px] font-medium uppercase tracking-[0.14em] ${statusColor(approval.status)}`}
                                    >
                                        {approval.status}
                                    </span>
                                </div>
                                {approval.summary && (
                                    <p className="mt-1.5 text-sm text-slate-300">{approval.summary}</p>
                                )}
                                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-slate-500">
                                    {approval.requested_by && <span>Requested by: {approval.requested_by}</span>}
                                    {approval.approved_by && <span>Decided by: {approval.approved_by}</span>}
                                    {approval.created_at && <span>Created: {approval.created_at}</span>}
                                    {approval.phase_id && <span>Phase: {approval.phase_id}</span>}
                                </div>
                            </div>

                            {approval.status === "pending" && (
                                <div className="flex shrink-0 gap-2">
                                    <button
                                        type="button"
                                        onClick={() => updateApproval(approval.approval_id, "approved")}
                                        disabled={updatingId === approval.approval_id}
                                        className="rounded border border-emerald-800/70 bg-emerald-900/20 px-3 py-1.5 text-xs text-emerald-200 hover:bg-emerald-900/35 disabled:opacity-50"
                                    >
                                        Approve
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => updateApproval(approval.approval_id, "rejected")}
                                        disabled={updatingId === approval.approval_id}
                                        className="rounded border border-rose-800/70 bg-rose-900/20 px-3 py-1.5 text-xs text-rose-200 hover:bg-rose-900/35 disabled:opacity-50"
                                    >
                                        Reject
                                    </button>
                                </div>
                            )}
                        </div>
                    </article>
                ))}
            </div>
        </div>
    );
}
