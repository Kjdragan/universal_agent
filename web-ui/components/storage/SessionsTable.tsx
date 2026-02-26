"use client";

import { StorageSessionItem } from "@/types/agent";
import { formatDateTimeTz } from "@/lib/timezone";

function formatEpoch(value?: number | null): string {
  return formatDateTimeTz(value, { placeholder: "-" });
}

function formatBytes(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return "-";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

type SessionsTableProps = {
  sessions: StorageSessionItem[];
  loading: boolean;
  rootSource: "local" | "mirror";
  onOpenRoot: (path: string) => void;
  onOpenRunLog: (path: string) => void;
};

export function SessionsTable({ sessions, loading, rootSource, onOpenRoot, onOpenRunLog }: SessionsTableProps) {
  if (loading) {
    return <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4 text-sm text-slate-400">Loading sessions...</div>;
  }
  if (!sessions.length) {
    return <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4 text-sm text-slate-400">No sessions found in {rootSource} storage root.</div>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-900/50">
      <table className="min-w-full text-left text-sm">
        <thead className="bg-slate-900/80 text-xs uppercase tracking-wider text-slate-400">
          <tr>
            <th className="px-3 py-2">Session</th>
            <th className="px-3 py-2">Source</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">Completed</th>
            <th className="px-3 py-2">Size</th>
            <th className="px-3 py-2">Actions</th>
          </tr>
        </thead>
        <tbody>
          {sessions.map((item) => (
            <tr key={item.session_id} className="border-t border-slate-800/80">
              <td className="px-3 py-2 font-mono text-xs text-slate-200">{item.session_id}</td>
              <td className="px-3 py-2 text-xs uppercase tracking-wide text-slate-300">{item.source_type}</td>
              <td className="px-3 py-2 text-xs text-slate-300">{item.status || (item.ready ? "ready" : "unknown")}</td>
              <td className="px-3 py-2 text-xs text-slate-300">{formatEpoch(item.completed_at_epoch || item.updated_at_epoch || item.modified_epoch)}</td>
              <td className="px-3 py-2 text-xs text-slate-300">{formatBytes(item.size_bytes)}</td>
              <td className="px-3 py-2">
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => onOpenRoot(item.root_path)}
                    className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] uppercase tracking-wider text-slate-200 hover:bg-slate-800"
                  >
                    Open Root
                  </button>
                  <button
                    type="button"
                    onClick={() => item.run_log_path && onOpenRunLog(item.run_log_path)}
                    disabled={!item.run_log_path}
                    className="rounded border border-cyan-700/70 bg-cyan-600/15 px-2 py-1 text-[11px] uppercase tracking-wider text-cyan-100 hover:bg-cyan-600/25 disabled:opacity-40"
                  >
                    Run Log
                  </button>
                  <button
                    type="button"
                    onClick={() => navigator.clipboard.writeText(item.root_path)}
                    className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[11px] uppercase tracking-wider text-slate-300 hover:bg-slate-800"
                  >
                    Copy Path
                  </button>
                </div>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
