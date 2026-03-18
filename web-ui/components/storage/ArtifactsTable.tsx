"use client";

import { StorageArtifactItem } from "@/types/agent";
import { formatDateTimeTz } from "@/lib/timezone";

function formatEpoch(value?: number | null): string {
  return formatDateTimeTz(value, { placeholder: "-" });
}

type ArtifactsTableProps = {
  artifacts: StorageArtifactItem[];
  loading: boolean;
  onOpenPath: (path: string) => void;
  onOpenFile: (path: string) => void;
};

export function ArtifactsTable({ artifacts, loading, onOpenPath, onOpenFile }: ArtifactsTableProps) {
  if (loading) {
    return <div className="rounded-lg border border-border bg-background/50 p-4 text-sm text-muted-foreground">Loading artifacts...</div>;
  }
  if (!artifacts.length) {
    return <div className="rounded-lg border border-border bg-background/50 p-4 text-sm text-muted-foreground">No artifact runs found in mirror.</div>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-border bg-background/50">
      <table className="min-w-full text-left text-sm">
        <thead className="bg-background/80 text-xs uppercase tracking-wider text-muted-foreground">
          <tr>
            <th className="px-3 py-2">Run</th>
            <th className="px-3 py-2">Status</th>
            <th className="px-3 py-2">Video</th>
            <th className="px-3 py-2">Updated</th>
            <th className="px-3 py-2">Actions</th>
          </tr>
        </thead>
        <tbody>
          {artifacts.map((item) => (
            <tr key={item.path} className="border-t border-border/80">
              <td className="px-3 py-2">
                <div className="font-medium text-foreground">{item.title}</div>
                <div className="font-mono text-[11px] text-muted-foreground">{item.slug}</div>
              </td>
              <td className="px-3 py-2 text-xs text-foreground/80">{item.status || "unknown"}</td>
              <td className="px-3 py-2 text-xs text-foreground/80">
                {item.video_id ? (
                  <span className="font-mono">{item.video_id}</span>
                ) : item.video_url ? (
                  <a className="text-primary underline" href={item.video_url} target="_blank" rel="noopener noreferrer">
                    Link
                  </a>
                ) : (
                  "-"
                )}
              </td>
              <td className="px-3 py-2 text-xs text-foreground/80">{formatEpoch(item.updated_at_epoch)}</td>
              <td className="px-3 py-2">
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => onOpenPath(item.path)}
                    className="rounded border border-border bg-background px-2 py-1 text-[11px] uppercase tracking-wider text-foreground hover:bg-card"
                  >
                    Open Root
                  </button>
                  <button
                    type="button"
                    onClick={() => item.manifest_path && onOpenFile(item.manifest_path)}
                    disabled={!item.manifest_path}
                    className="rounded border border-primary/30/70 bg-primary/15 px-2 py-1 text-[11px] uppercase tracking-wider text-primary/90 hover:bg-primary/25 disabled:opacity-40"
                  >
                    Manifest
                  </button>
                  <button
                    type="button"
                    onClick={() => item.readme_path && onOpenFile(item.readme_path)}
                    disabled={!item.readme_path}
                    className="rounded border border-border bg-background px-2 py-1 text-[11px] uppercase tracking-wider text-foreground/80 hover:bg-card disabled:opacity-40"
                  >
                    README
                  </button>
                  <button
                    type="button"
                    onClick={() => item.implementation_path && onOpenFile(item.implementation_path)}
                    disabled={!item.implementation_path}
                    className="rounded border border-border bg-background px-2 py-1 text-[11px] uppercase tracking-wider text-foreground/80 hover:bg-card disabled:opacity-40"
                  >
                    Implementation
                  </button>
                  <button
                    type="button"
                    onClick={() => navigator.clipboard.writeText(item.path)}
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
