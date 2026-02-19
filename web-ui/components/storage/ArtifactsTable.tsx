"use client";

import { StorageArtifactItem } from "@/types/agent";

function formatEpoch(value?: number | null): string {
  if (!value || !Number.isFinite(value)) return "-";
  return new Date(value * 1000).toLocaleString();
}

type ArtifactsTableProps = {
  artifacts: StorageArtifactItem[];
  loading: boolean;
  onOpenPath: (path: string) => void;
  onOpenFile: (path: string) => void;
};

export function ArtifactsTable({ artifacts, loading, onOpenPath, onOpenFile }: ArtifactsTableProps) {
  if (loading) {
    return <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4 text-sm text-slate-400">Loading artifacts...</div>;
  }
  if (!artifacts.length) {
    return <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-4 text-sm text-slate-400">No artifact runs found in mirror.</div>;
  }

  return (
    <div className="overflow-x-auto rounded-lg border border-slate-800 bg-slate-900/50">
      <table className="min-w-full text-left text-sm">
        <thead className="bg-slate-900/80 text-xs uppercase tracking-wider text-slate-400">
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
            <tr key={item.path} className="border-t border-slate-800/80">
              <td className="px-3 py-2">
                <div className="font-medium text-slate-100">{item.title}</div>
                <div className="font-mono text-[11px] text-slate-400">{item.slug}</div>
              </td>
              <td className="px-3 py-2 text-xs text-slate-300">{item.status || "unknown"}</td>
              <td className="px-3 py-2 text-xs text-slate-300">
                {item.video_id ? (
                  <span className="font-mono">{item.video_id}</span>
                ) : item.video_url ? (
                  <a className="text-cyan-300 underline" href={item.video_url} target="_blank" rel="noopener noreferrer">
                    Link
                  </a>
                ) : (
                  "-"
                )}
              </td>
              <td className="px-3 py-2 text-xs text-slate-300">{formatEpoch(item.updated_at_epoch)}</td>
              <td className="px-3 py-2">
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => onOpenPath(item.path)}
                    className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-[11px] uppercase tracking-wider text-slate-200 hover:bg-slate-800"
                  >
                    Open Root
                  </button>
                  <button
                    type="button"
                    onClick={() => item.manifest_path && onOpenFile(item.manifest_path)}
                    disabled={!item.manifest_path}
                    className="rounded border border-cyan-700/70 bg-cyan-600/15 px-2 py-1 text-[11px] uppercase tracking-wider text-cyan-100 hover:bg-cyan-600/25 disabled:opacity-40"
                  >
                    Manifest
                  </button>
                  <button
                    type="button"
                    onClick={() => item.readme_path && onOpenFile(item.readme_path)}
                    disabled={!item.readme_path}
                    className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[11px] uppercase tracking-wider text-slate-300 hover:bg-slate-800 disabled:opacity-40"
                  >
                    README
                  </button>
                  <button
                    type="button"
                    onClick={() => item.implementation_path && onOpenFile(item.implementation_path)}
                    disabled={!item.implementation_path}
                    className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[11px] uppercase tracking-wider text-slate-300 hover:bg-slate-800 disabled:opacity-40"
                  >
                    Implementation
                  </button>
                  <button
                    type="button"
                    onClick={() => navigator.clipboard.writeText(item.path)}
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
