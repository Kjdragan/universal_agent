"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { StorageRootSource } from "@/types/agent";

type VpsScope = "workspaces" | "artifacts";

type FileEntry = {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number | null;
};

type ExplorerPanelProps = {
  initialScope?: VpsScope;
  initialPath?: string;
  initialRootSource?: StorageRootSource;
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

function isMarkdownFilePath(path: string): boolean {
  return /\.md(?:own)?$/i.test(path.trim());
}

function isImageFilePath(path: string): boolean {
  return /\.(png|jpe?g|gif|webp|bmp|svg|avif|ico)$/i.test(path.trim());
}

function isProtectedRuntimeDbPath(path: string): boolean {
  return /\.(db|db-shm|db-wal)$/i.test(path.trim());
}

export function ExplorerPanel({
  initialScope = "workspaces",
  initialPath = "",
  initialRootSource = "local",
}: ExplorerPanelProps) {
  const [scope, setScope] = useState<VpsScope>(initialScope);
  const [path, setPath] = useState(initialPath);
  const [rootSource, setRootSource] = useState<StorageRootSource>(initialRootSource);
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);
  const [previewTitle, setPreviewTitle] = useState("");
  const [previewText, setPreviewText] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewIsMarkdown, setPreviewIsMarkdown] = useState(false);
  const [previewImageUrl, setPreviewImageUrl] = useState("");
  const [selectedPaths, setSelectedPaths] = useState<Set<string>>(new Set());

  useEffect(() => {
    setScope(initialScope);
  }, [initialScope]);

  useEffect(() => {
    setPath(initialPath);
  }, [initialPath]);

  useEffect(() => {
    setRootSource(initialRootSource);
  }, [initialRootSource]);

  const locationLabel = useMemo(() => (path ? `/${path}` : "/"), [path]);
  const selectedHasProtectedCandidates = useMemo(
    () => Array.from(selectedPaths.values()).some((itemPath) => isProtectedRuntimeDbPath(itemPath)),
    [selectedPaths],
  );

  const loadEntries = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const res = await fetch(
        `/api/vps/files?scope=${scope}&root_source=${rootSource}&path=${encodeURIComponent(path)}`,
      );
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data?.detail || `Failed (${res.status})`);
      }
      const rows = Array.isArray(data?.files) ? (data.files as FileEntry[]) : [];
      rows.sort((a, b) => {
        if (a.is_dir === b.is_dir) return a.name.localeCompare(b.name);
        return a.is_dir ? -1 : 1;
      });
      setEntries(rows);
      setSelectedPaths((previous) => {
        const next = new Set<string>();
        const valid = new Set(rows.map((entry) => entry.path));
        previous.forEach((value) => {
          if (valid.has(value)) next.add(value);
        });
        return next;
      });
    } catch (err: any) {
      setEntries([]);
      setSelectedPaths(new Set());
      setError(err?.message || "Failed to load entries");
    } finally {
      setLoading(false);
    }
  }, [path, rootSource, scope]);

  useEffect(() => {
    const run = async () => {
      await loadEntries();
    };
    void run();
  }, [loadEntries]);

  const openEntry = async (entry: FileEntry) => {
    if (entry.is_dir) {
      setPath(entry.path);
      return;
    }
    const isImage = isImageFilePath(entry.name) || isImageFilePath(entry.path);
    setPreviewTitle(entry.path);
    setPreviewText("");
    setPreviewIsMarkdown(isMarkdownFilePath(entry.name) || isMarkdownFilePath(entry.path));
    setPreviewImageUrl("");
    setPreviewLoading(true);

    if (isImage) {
      setPreviewImageUrl(
        `/api/vps/file?scope=${scope}&root_source=${rootSource}&path=${encodeURIComponent(entry.path)}`,
      );
      setPreviewLoading(false);
      return;
    }

    try {
      const res = await fetch(
        `/api/vps/file?scope=${scope}&root_source=${rootSource}&path=${encodeURIComponent(entry.path)}`,
      );
      const text = await res.text();
      if (!res.ok) {
        throw new Error(text || `Failed (${res.status})`);
      }
      setPreviewText(text);
    } catch (err: any) {
      setPreviewText("");
      setError(err?.message || "Failed to open file");
    } finally {
      setPreviewLoading(false);
    }
  };

  const toggleSelected = (entryPath: string, checked: boolean) => {
    setSelectedPaths((previous) => {
      const next = new Set(previous);
      if (checked) next.add(entryPath);
      else next.delete(entryPath);
      return next;
    });
  };

  const selectAllVisible = () => {
    setSelectedPaths(new Set(entries.map((entry) => entry.path)));
  };

  const clearSelection = () => {
    setSelectedPaths(new Set());
  };

  const deleteSelected = async (forceProtected = false) => {
    if (!selectedPaths.size || deleting) return;
    const targets = Array.from(selectedPaths.values());
    const confirmMessage = forceProtected
      ? `Force-delete ${targets.length} selected item(s), including protected DB files? This cannot be undone.`
      : `Delete ${targets.length} selected item(s)? This cannot be undone.`;
    if (!window.confirm(confirmMessage)) return;

    setDeleting(true);
    setError("");
    try {
      const runDelete = async (allowProtected: boolean) => {
        const res = await fetch("/api/vps/files/delete", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            scope,
            root_source: rootSource,
            paths: targets,
            allow_protected: allowProtected,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          throw new Error(data?.detail || `Failed (${res.status})`);
        }
        return data;
      };

      let data = await runDelete(forceProtected);
      if (!forceProtected) {
        const protectedBlocked = Array.isArray(data?.errors)
          ? data.errors.filter((item: any) => item?.code === "protected_requires_override")
          : [];
        if (protectedBlocked.length > 0) {
          const shouldForce = window.confirm(
            `${protectedBlocked.length} selected path(s) are protected runtime DB files. Retry with force delete?`,
          );
          if (shouldForce) {
            data = await runDelete(true);
          }
        }
      }

      const deleted = Number(data?.deleted_count || 0);
      const failed = Number(data?.error_count || 0);
      if (failed > 0) {
        setError(`Deleted ${deleted} item(s), ${failed} failed. Retry with force for protected DB files if needed.`);
      }
      clearSelection();
      setPreviewImageUrl("");
      setPreviewText("");
      setPreviewTitle("");
      await loadEntries();
    } catch (err: any) {
      setError(err?.message || "Failed to delete selected entries");
    } finally {
      setDeleting(false);
    }
  };

  return (
    <div className="grid h-full min-h-0 gap-4 xl:grid-cols-2">
      <section className="flex min-h-0 flex-col rounded-xl border border-slate-800 bg-slate-900/60 p-3">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <h3 className="text-sm font-semibold">Explorer</h3>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => setScope("workspaces")}
              className={`rounded border px-2 py-1 text-xs uppercase tracking-wider ${scope === "workspaces" ? "border-cyan-700 bg-cyan-600/20 text-cyan-100" : "border-slate-700 bg-slate-900 text-slate-300"}`}
            >
              Sessions
            </button>
            <button
              type="button"
              onClick={() => setScope("artifacts")}
              className={`rounded border px-2 py-1 text-xs uppercase tracking-wider ${scope === "artifacts" ? "border-cyan-700 bg-cyan-600/20 text-cyan-100" : "border-slate-700 bg-slate-900 text-slate-300"}`}
            >
              Artifacts
            </button>
            <button
              type="button"
              onClick={() => setPath(parentPath(path))}
              disabled={!path}
              className="inline-flex h-7 w-7 items-center justify-center rounded-full border border-slate-700 bg-slate-900 text-sm text-slate-300 transition-colors hover:bg-slate-800 disabled:opacity-40"
              title="Go up one level"
              aria-label="Go up one level"
            >
              ↑
            </button>
          </div>
        </div>

        <div className="mb-3 flex items-center gap-2">
          {(["local", "mirror"] as const).map((option) => (
            <button
              key={option}
              type="button"
              onClick={() => setRootSource(option)}
              className={`rounded border px-2 py-1 text-[10px] uppercase tracking-wider ${rootSource === option ? "border-cyan-700 bg-cyan-600/20 text-cyan-100" : "border-slate-700 bg-slate-900 text-slate-300"}`}
            >
              {option}
            </button>
          ))}
          <span className="ml-auto text-[10px] uppercase tracking-wider text-slate-500">
            selected: {selectedPaths.size}
          </span>
        </div>

        <div className="mb-3 rounded border border-slate-700 bg-slate-950 px-2 py-2 text-xs font-mono text-slate-300">
          {rootSource}/{scope}:{locationLabel}
        </div>

        <div className="mb-3 flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={selectAllVisible}
            disabled={!entries.length}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[10px] uppercase tracking-wider text-slate-300 hover:bg-slate-800 disabled:opacity-40"
          >
            Select All
          </button>
          <button
            type="button"
            onClick={clearSelection}
            disabled={!selectedPaths.size}
            className="rounded border border-slate-700 bg-slate-900 px-2 py-1 text-[10px] uppercase tracking-wider text-slate-300 hover:bg-slate-800 disabled:opacity-40"
          >
            Clear
          </button>
          <button
            type="button"
            onClick={() => void deleteSelected(false)}
            disabled={!selectedPaths.size || deleting}
            className="rounded border border-red-700 bg-red-600/20 px-2 py-1 text-[10px] uppercase tracking-wider text-red-100 hover:bg-red-600/30 disabled:opacity-40"
          >
            {deleting ? "Deleting..." : "Delete Selected"}
          </button>
          <button
            type="button"
            onClick={() => void deleteSelected(true)}
            disabled={!selectedPaths.size || deleting || !selectedHasProtectedCandidates}
            className="rounded border border-amber-700 bg-amber-600/20 px-2 py-1 text-[10px] uppercase tracking-wider text-amber-100 hover:bg-amber-600/30 disabled:opacity-40"
            title="Required for runtime DB files (.db/.db-shm/.db-wal)"
          >
            Force Delete Protected
          </button>
        </div>

        {error && <div className="mb-2 text-sm text-red-300">{error}</div>}

        <div className="min-h-0 flex-1">
          {loading ? (
            <div className="text-sm text-slate-400">Loading...</div>
          ) : !entries.length ? (
            <div className="text-sm text-slate-400">No files in this directory.</div>
          ) : (
            <div className="h-full overflow-auto rounded border border-slate-800">
              {entries.map((entry) => (
                <div
                  key={`${scope}:${entry.path}`}
                  className="flex items-center gap-2 border-b border-slate-800 px-3 py-2 text-left text-sm hover:bg-slate-800/60"
                >
                  <input
                    type="checkbox"
                    checked={selectedPaths.has(entry.path)}
                    onChange={(event) => toggleSelected(entry.path, event.target.checked)}
                    className="h-3.5 w-3.5 rounded border-slate-600 bg-slate-900 text-cyan-500"
                    aria-label={`select ${entry.path}`}
                  />
                  <button
                    type="button"
                    onClick={() => void openEntry(entry)}
                    className="flex flex-1 items-center gap-2 text-left"
                  >
                    <span className="w-5">{entry.is_dir ? "📁" : "📄"}</span>
                    <span className="flex-1 truncate font-mono">{entry.name}</span>
                    <span className="text-[11px] text-slate-500">{entry.is_dir ? "" : formatBytes(entry.size)}</span>
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="flex min-h-0 flex-col rounded-xl border border-slate-800 bg-slate-900/60 p-3">
        <h3 className="mb-2 text-sm font-semibold">Preview {previewTitle ? `- ${previewTitle}` : ""}</h3>
        <div className="min-h-0 flex-1">
          {previewLoading ? (
            <div className="text-sm text-slate-400">Loading file...</div>
          ) : previewImageUrl ? (
            <div className="h-full overflow-auto rounded border border-slate-800 bg-slate-950/80 p-3">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={previewImageUrl}
                alt={previewTitle || "preview image"}
                className="max-h-full max-w-full object-contain"
              />
            </div>
          ) : previewText && previewIsMarkdown ? (
            <div className="h-full overflow-auto rounded border border-slate-800 bg-slate-950/80 p-3 text-[12px] leading-6 text-slate-200">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                className="prose prose-sm max-w-none prose-invert"
              >
                {previewText}
              </ReactMarkdown>
            </div>
          ) : previewText ? (
            <pre className="h-full overflow-auto rounded border border-slate-800 bg-slate-950/80 p-3 text-[12px] leading-5 text-slate-200">
              {previewText}
            </pre>
          ) : (
            <div className="text-sm text-slate-400">Select a file to preview.</div>
          )}
        </div>
      </section>
    </div>
  );
}
