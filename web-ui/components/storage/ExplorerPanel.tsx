"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Folder,
  FileText,
  FileCode,
  FileJson2,
  Image as ImageIcon,
  Terminal,
  ChevronRight,
  ArrowUp,
  Trash2,
  RefreshCw,
  Globe,
} from "lucide-react";
import { FilePreview } from "./FilePreview";
import { useFilePreview, detectFileType, type FileType } from "./useFilePreview";

type VpsScope = "workspaces" | "artifacts" | "vps";

type FileEntry = {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number | null;
};

type ExplorerPanelProps = {
  initialScope?: VpsScope;
  initialPath?: string;
  initialPreviewPath?: string;
};

function parentPath(path: string): string {
  if (!path) return "";
  const parts = path.split("/").filter(Boolean);
  parts.pop();
  return parts.join("/");
}

function formatBytes(bytes?: number | null): string {
  if (!bytes || bytes <= 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function isProtectedRuntimeDbPath(path: string): boolean {
  return /\.(db|db-shm|db-wal)$/i.test(path.trim());
}

function fileIcon(entry: FileEntry) {
  if (entry.is_dir) return <Folder className="h-4 w-4 text-cyan-400" />;
  const ft = detectFileType(entry.name);
  switch (ft) {
    case "markdown": return <FileText className="h-4 w-4 text-blue-400" />;
    case "json": return <FileJson2 className="h-4 w-4 text-yellow-400" />;
    case "html": return <Globe className="h-4 w-4 text-orange-400" />;
    case "code": return <FileCode className="h-4 w-4 text-emerald-400" />;
    case "log": return <Terminal className="h-4 w-4 text-slate-400" />;
    case "image": return <ImageIcon className="h-4 w-4 text-purple-400" />;
    default: return <FileText className="h-4 w-4 text-slate-400" />;
  }
}

export function ExplorerPanel({
  initialScope = "workspaces",
  initialPath = "",
  initialPreviewPath = "",
}: ExplorerPanelProps) {
  const [scope, setScope] = useState<VpsScope>(initialScope);
  const [path, setPath] = useState(initialPath);
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [deletingPaths, setDeletingPaths] = useState<Set<string>>(new Set());
  const preview = useFilePreview(scope);

  useEffect(() => { setScope(initialScope); }, [initialScope]);
  useEffect(() => { setPath(initialPath); }, [initialPath]);

  // ── Breadcrumb segments ──────────────────────────────────────────────
  const breadcrumbs = useMemo(() => {
    const segments = path.split("/").filter(Boolean);
    const crumbs: Array<{ label: string; path: string }> = [
      { label: scope === "workspaces" ? "Workspaces" : scope === "artifacts" ? "Artifacts" : "VPS Root", path: "" },
    ];
    let current = "";
    for (const seg of segments) {
      current = current ? `${current}/${seg}` : seg;
      crumbs.push({ label: seg, path: current });
    }
    return crumbs;
  }, [path, scope]);

  // ── Load entries ─────────────────────────────────────────────────────
  const loadEntries = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(
        `/api/vps/files?scope=${scope}&path=${encodeURIComponent(path)}`,
      );
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data?.detail || `Failed (${res.status})`);
      }
      if (Boolean(data?.is_file) && path) {
        setPath(parentPath(path));
        return;
      }
      const rows = Array.isArray(data?.files) ? (data.files as FileEntry[]) : [];
      rows.sort((a, b) => {
        if (a.is_dir === b.is_dir) return a.name.localeCompare(b.name);
        return a.is_dir ? -1 : 1;
      });
      setEntries(rows);
    } catch (err: any) {
      setEntries([]);
      setError(err?.message || "Failed to load entries");
    } finally {
      setLoading(false);
    }
  }, [path, scope]);

  useEffect(() => { void loadEntries(); }, [loadEntries]);

  useEffect(() => {
    const candidate = String(initialPreviewPath || "").trim();
    if (!candidate) return;
    if (parentPath(candidate) !== path) return;
    void preview.previewFile(candidate);
  }, [initialPreviewPath, path]);

  // ── Navigate ─────────────────────────────────────────────────────────
  const openEntry = async (entry: FileEntry) => {
    if (entry.is_dir) {
      setPath(entry.path);
      preview.clearPreview();
      return;
    }
    await preview.previewFile(entry.path);
  };

  // ── Delete single item ──────────────────────────────────────────────
  const deleteItem = async (entry: FileEntry) => {
    const isProtected = isProtectedRuntimeDbPath(entry.path);
    const label = entry.is_dir ? "directory" : "file";
    const msg = isProtected
      ? `Force-delete protected ${label} "${entry.name}"? This cannot be undone.`
      : `Delete ${label} "${entry.name}"? This cannot be undone.`;
    if (!window.confirm(msg)) return;

    setDeletingPaths((prev) => new Set(prev).add(entry.path));
    setError("");
    try {
      const res = await fetch("/api/vps/files/delete", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          scope,
          paths: [entry.path],
          allow_protected: isProtected,
        }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        throw new Error(data?.detail || `Failed (${res.status})`);
      }
      if (preview.title === entry.path || preview.title.startsWith(entry.path + "/")) {
        preview.clearPreview();
      }
      await loadEntries();
    } catch (err: any) {
      setError(err?.message || "Failed to delete");
    } finally {
      setDeletingPaths((prev) => {
        const next = new Set(prev);
        next.delete(entry.path);
        return next;
      });
    }
  };

  // ── Scope switch ─────────────────────────────────────────────────────
  const switchScope = (newScope: VpsScope) => {
    setScope(newScope);
    setPath("");
    preview.clearPreview();
  };

  return (
    <div className="grid h-full min-h-0 grid-cols-1 gap-4 xl:grid-cols-[minmax(320px,2fr)_3fr]">
      {/* ═══════════════════════════════════════════════════════════════ */}
      {/* LEFT PANEL — FILE TREE                                        */}
      {/* ═══════════════════════════════════════════════════════════════ */}
      <section className="flex min-h-0 flex-col rounded-xl border border-slate-700/50 bg-slate-900/60 backdrop-blur-sm">
        {/* ── Header ── */}
        <div className="flex items-center gap-2 border-b border-slate-700/40 px-4 py-3">
          <Folder className="h-4 w-4 text-cyan-400" />
          <h3 className="text-sm font-semibold text-slate-200">Explorer</h3>
          <div className="ml-auto flex items-center gap-1.5">
            {(["workspaces", "artifacts", "vps"] as const).map((s) => (
              <button
                key={s}
                type="button"
                onClick={() => switchScope(s)}
                className={`rounded-md px-2.5 py-1 text-[11px] font-medium uppercase tracking-wider transition-colors ${scope === s
                  ? "bg-cyan-500/15 text-cyan-300 ring-1 ring-cyan-500/30"
                  : "text-slate-400 hover:bg-slate-700/40 hover:text-slate-200"
                  }`}
              >
                {s === "workspaces" ? "Sessions" : s === "artifacts" ? "Artifacts" : "VPS"}
              </button>
            ))}
            <button
              type="button"
              onClick={() => void loadEntries()}
              className="ml-1 inline-flex h-7 w-7 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-slate-700/40 hover:text-slate-200"
              title="Refresh"
            >
              <RefreshCw className="h-3.5 w-3.5" />
            </button>
          </div>
        </div>

        {/* ── Breadcrumbs ── */}
        <div className="flex items-center gap-1 overflow-x-auto border-b border-slate-700/30 px-4 py-2 text-[12px]">
          {path && (
            <button
              type="button"
              onClick={() => setPath(parentPath(path))}
              className="mr-1 inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-slate-400 transition-colors hover:bg-slate-700/40 hover:text-slate-200"
              title="Go up"
            >
              <ArrowUp className="h-3.5 w-3.5" />
            </button>
          )}
          {breadcrumbs.map((crumb, i) => (
            <span key={crumb.path + i} className="flex shrink-0 items-center gap-1">
              {i > 0 && <ChevronRight className="h-3 w-3 text-slate-600" />}
              <button
                type="button"
                onClick={() => setPath(crumb.path)}
                className={`rounded px-1 py-0.5 transition-colors ${i === breadcrumbs.length - 1
                  ? "font-medium text-cyan-300"
                  : "text-slate-400 hover:text-slate-200"
                  }`}
              >
                {crumb.label}
              </button>
            </span>
          ))}
        </div>

        {/* ── Error ── */}
        {error && (
          <div className="mx-3 mt-2 rounded-md border border-red-700/50 bg-red-600/10 px-3 py-2 text-[12px] text-red-300">
            {error}
          </div>
        )}

        {/* ── File list ── */}
        <div className="min-h-0 flex-1 overflow-auto">
          {loading ? (
            <div className="flex items-center justify-center py-12">
              <div className="flex items-center gap-3 text-slate-400">
                <div className="h-4 w-4 animate-spin rounded-full border-2 border-cyan-500/30 border-t-cyan-500" />
                <span className="text-sm">Loading...</span>
              </div>
            </div>
          ) : !entries.length ? (
            <div className="flex items-center justify-center py-12 text-sm text-slate-500">
              {path ? "Empty directory" : "No files found"}
            </div>
          ) : (
            <div className="divide-y divide-slate-700/30">
              {entries.map((entry) => (
                <div
                  key={`${scope}:${entry.path}`}
                  className="group flex items-center gap-2 px-4 py-2 transition-colors hover:bg-slate-800/50"
                >
                  <button
                    type="button"
                    onClick={() => void openEntry(entry)}
                    className="flex flex-1 items-center gap-2.5 text-left min-w-0"
                  >
                    {fileIcon(entry)}
                    <span className="flex-1 truncate font-mono text-[13px] text-slate-200 group-hover:text-white">
                      {entry.name}
                    </span>
                    {!entry.is_dir && (
                      <span className="shrink-0 text-[11px] text-slate-500">
                        {formatBytes(entry.size)}
                      </span>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => void deleteItem(entry)}
                    disabled={deletingPaths.has(entry.path)}
                    className="shrink-0 rounded-md p-1 text-slate-600 opacity-30 transition-all group-hover:opacity-100 hover:bg-red-500/15 hover:text-red-400 disabled:opacity-50"
                    title={`Delete ${entry.name}`}
                  >
                    {deletingPaths.has(entry.path) ? (
                      <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-red-500/30 border-t-red-500" />
                    ) : (
                      <Trash2 className="h-3.5 w-3.5" />
                    )}
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* ── Footer ── */}
        <div className="border-t border-slate-700/30 px-4 py-2">
          <span className="text-[11px] text-slate-500">
            {entries.length} item{entries.length !== 1 ? "s" : ""}
          </span>
        </div>
      </section>

      {/* ═══════════════════════════════════════════════════════════════ */}
      {/* RIGHT PANEL — PREVIEW                                         */}
      {/* ═══════════════════════════════════════════════════════════════ */}
      <section className="flex min-h-0 flex-col rounded-xl border border-slate-700/50 bg-slate-900/60 p-4 backdrop-blur-sm">
        <FilePreview
          title={preview.title}
          content={preview.content}
          fileType={preview.fileType}
          isLoading={preview.isLoading}
          imageUrl={preview.imageUrl}
          error={preview.error}
          filePath={preview.title}
        />
      </section>
    </div>
  );
}
