"use client";

import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

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

export function ExplorerPanel({ initialScope = "workspaces", initialPath = "" }: ExplorerPanelProps) {
  const [scope, setScope] = useState<VpsScope>(initialScope);
  const [path, setPath] = useState(initialPath);
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [previewTitle, setPreviewTitle] = useState("");
  const [previewText, setPreviewText] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewIsMarkdown, setPreviewIsMarkdown] = useState(false);
  const [previewImageUrl, setPreviewImageUrl] = useState("");

  useEffect(() => {
    setScope(initialScope);
  }, [initialScope]);

  useEffect(() => {
    setPath(initialPath);
  }, [initialPath]);

  const locationLabel = useMemo(() => (path ? `/${path}` : "/"), [path]);

  useEffect(() => {
    let ignore = false;
    const run = async () => {
      setLoading(true);
      setError("");
      try {
        const res = await fetch(`/api/vps/files?scope=${scope}&path=${encodeURIComponent(path)}`);
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data?.detail || `Failed (${res.status})`);
        }
        const rows = Array.isArray(data?.files) ? (data.files as FileEntry[]) : [];
        rows.sort((a, b) => {
          if (a.is_dir === b.is_dir) return a.name.localeCompare(b.name);
          return a.is_dir ? -1 : 1;
        });
        if (!ignore) {
          setEntries(rows);
        }
      } catch (err: any) {
        if (!ignore) {
          setEntries([]);
          setError(err?.message || "Failed to load entries");
        }
      } finally {
        if (!ignore) setLoading(false);
      }
    };
    void run();
    return () => {
      ignore = true;
    };
  }, [scope, path]);

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
      setPreviewImageUrl(`/api/vps/file?scope=${scope}&path=${encodeURIComponent(entry.path)}`);
      setPreviewLoading(false);
      return;
    }

    try {
      const res = await fetch(`/api/vps/file?scope=${scope}&path=${encodeURIComponent(entry.path)}`);
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

        <div className="mb-3 rounded border border-slate-700 bg-slate-950 px-2 py-2 text-xs font-mono text-slate-300">
          {scope}:{locationLabel}
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
                <button
                  key={`${scope}:${entry.path}`}
                  type="button"
                  onClick={() => void openEntry(entry)}
                  className="flex w-full items-center gap-2 border-b border-slate-800 px-3 py-2 text-left text-sm hover:bg-slate-800/60"
                >
                  <span className="w-5">{entry.is_dir ? "📁" : "📄"}</span>
                  <span className="flex-1 truncate font-mono">{entry.name}</span>
                  <span className="text-[11px] text-slate-500">{entry.is_dir ? "" : formatBytes(entry.size)}</span>
                </button>
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
