"use client";

import { StorageRunWorkspaceItem } from "@/types/agent";
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
  sessions: StorageRunWorkspaceItem[];
  loading: boolean;
  rootSource: "local" | "mirror";
  onOpenRoot: (path: string) => void;
  onOpenRunLog: (path: string) => void;
};

export function SessionsTable({ sessions, loading, rootSource, onOpenRoot, onOpenRunLog }: SessionsTableProps) {
  if (loading) {
    return <div className="rounded-lg border border-border bg-background/50 p-4 text-sm text-muted-foreground">Loading run workspaces...</div>;
  }
  if (!sessions.length) {
    return <div className="rounded-lg border border-border bg-background/50 p-4 text-sm text-muted-foreground">No run workspaces found in {rootSource} storage root.</div>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-background/50">
      <table className="min-w-full text-left text-sm">
        <thead className="bg-background/80 text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Run Workspace</th>
            <th className="px-3 py-2">Source</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">Completed</th>
            <th className="px-3 py-2">Size</th>
            <th className="px-3 py-2">Actions</th>
          </tr>
        </thead>
        <tbody>
          {sessions.map((item) => (
            <tr key={item.session_id} className="border-t border-border/80">
              <td className="px-3 py-2">
                <div className="font-mono text-xs text-foreground">{item.session_id}</div>
                {item.run_id ? (
                  <div className="text-[11px] text-primary/80">
                    run {item.run_id}
                    {item.attempt_count ? ` · ${item.attempt_count} attempt${item.attempt_count === 1 ? "" : "s"}` : ""}
                    {item.run_kind ? ` · ${item.run_kind}` : ""}
                  </div>
                ) : null}
              </td>
              <td className="px-3 py-2 text-xs uppercase tracking-wide text-foreground/80">{item.source_type}</td>
              <td className="px-3 py-2 text-xs text-foreground/80">{item.status || (item.ready ? "ready" : "unknown")}</td>
              <td className="px-3 py-2 text-xs text-foreground/80">{formatEpoch(item.completed_at_epoch || item.updated_at_epoch || item.modified_epoch)}</td>
              <td className="px-3 py-2 text-xs text-foreground/80">{formatBytes(item.size_bytes)}</td>
              <td className="px-3 py-2">
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => onOpenRoot(item.root_path)}
                    className="rounded border border-border bg-background px-2 py-1 text-[11px] uppercase tracking-wider text-foreground hover:bg-card"
                  >
                    Open Root
                  </button>
                  <button
                    type="button"
                    onClick={() => item.run_log_path && onOpenRunLog(item.run_log_path)}
                    disabled={!item.run_log_path}
                    className="rounded border border-primary/30/70 bg-primary/15 px-2 py-1 text-[11px] uppercase tracking-wider text-primary/90 hover:bg-primary/25 disabled:opacity-40"
                  >
                    Run Log
                  </button>
                  <button
                    type="button"
                    onClick={() => navigator.clipboard.writeText(item.root_path)}
                    className="rounded border border-border bg-background px-2 py-1 text-[11px] uppercase tracking-wider text-foreground/80 hover:bg-card"
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
