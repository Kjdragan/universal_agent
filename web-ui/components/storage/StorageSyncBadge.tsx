"use client";

import { StorageSyncState } from "@/types/agent";

const STATUS_STYLE: Record<StorageSyncState, { label: string; className: string }> = {
  in_sync: {
    label: "In Sync",
    className: "border-emerald-700/60 bg-emerald-600/15 text-emerald-200",
  },
  behind: {
    label: "Behind",
    className: "border-amber-700/60 bg-amber-600/15 text-amber-200",
  },
  syncing: {
    label: "Syncing",
    className: "border-cyan-700/60 bg-cyan-600/15 text-cyan-100",
  },
  unknown: {
    label: "Unknown",
    className: "border-slate-700 bg-slate-800/70 text-slate-300",
  },
  error: {
    label: "Error",
    className: "border-red-700/60 bg-red-600/15 text-red-200",
  },
};

type StorageSyncBadgeProps = {
  state: StorageSyncState;
  pendingReadyCount?: number;
  className?: string;
};

export function StorageSyncBadge({ state, pendingReadyCount = 0, className = "" }: StorageSyncBadgeProps) {
  const style = STATUS_STYLE[state] || STATUS_STYLE.unknown;
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs font-semibold uppercase tracking-wider ${style.className} ${className}`}
      title={pendingReadyCount > 0 ? `${pendingReadyCount} completed run(s) waiting to sync` : "No pending completed runs"}
    >
      {style.label}
      {pendingReadyCount > 0 ? `(${pendingReadyCount})` : ""}
    </span>
  );
}
