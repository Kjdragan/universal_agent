"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

type SourceMode = "local_session" | "local_artifacts" | "vps_workspaces" | "vps_artifacts";

type SessionInfo = {
  session_id: string;
  workspace?: string;
  user_id?: string;
};

type FileEntry = {
  name: string;
  path: string;
  is_dir: boolean;
  size?: number | null;
  modified?: number | null;
};

const SOURCE_OPTIONS: Array<{ value: SourceMode; label: string }> = [
  { value: "local_session", label: "Local Session" },
  { value: "local_artifacts", label: "Local Artifacts" },
  { value: "vps_workspaces", label: "VPS Workspaces (Mirror)" },
  { value: "vps_artifacts", label: "VPS Artifacts (Mirror)" },
];

function encodePath(path: string): string {
  return path
    .split("/")
    .filter(Boolean)
    .map((part) => encodeURIComponent(part))
    .join("/");
}

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
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

export default function FilesPage() {
  const [mode, setMode] = useState<SourceMode>("local_session");
  const [sessions, setSessions] = useState<SessionInfo[]>([]);
  const [sessionId, setSessionId] = useState("");
  const [path, setPath] = useState("");
  const [entries, setEntries] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [previewTitle, setPreviewTitle] = useState("");
  const [previewText, setPreviewText] = useState("");
  const [previewLoading, setPreviewLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [syncMessage, setSyncMessage] = useState("");

  const needsSession = mode === "local_session";
  const canBrowse = !needsSession || Boolean(sessionId);

  const locationLabel = useMemo(() => {
    if (!path) return "/";
    return `/${path}`;
  }, [path]);

  useEffect(() => {
    const loadSessions = async () => {
      try {
        const res = await fetch("/api/sessions");
        const data = await res.json();
        const rows = Array.isArray(data?.sessions) ? (data.sessions as SessionInfo[]) : [];
        setSessions(rows);
        if (!sessionId && rows.length > 0) {
          setSessionId(rows[0].session_id);
        }
      } catch {
        setSessions([]);
      }
    };
    void loadSessions();
  }, [sessionId]);

  useEffect(() => {
    if (!canBrowse) {
      setEntries([]);
      return;
    }
    const loadEntries = async () => {
      setLoading(true);
      setError("");
      try {
        let url = "";
        if (mode === "local_session") {
          url = `/api/files?session_id=${encodeURIComponent(sessionId)}&path=${encodeURIComponent(path)}`;
        } else if (mode === "local_artifacts") {
          url = `/api/artifacts?path=${encodeURIComponent(path)}`;
        } else if (mode === "vps_workspaces") {
          url = `/api/vps/files?scope=workspaces&path=${encodeURIComponent(path)}`;
        } else {
          url = `/api/vps/files?scope=artifacts&path=${encodeURIComponent(path)}`;
        }
        const res = await fetch(url);
        const data = await res.json();
        if (!res.ok) {
          throw new Error(data?.detail || `Request failed (${res.status})`);
        }
        const rows = Array.isArray(data?.files) ? (data.files as FileEntry[]) : [];
        rows.sort((a, b) => {
          if (a.is_dir === b.is_dir) return a.name.localeCompare(b.name);
          return a.is_dir ? -1 : 1;
        });
        setEntries(rows);
      } catch (err: any) {
        setEntries([]);
        setError(err?.message || "Failed to load files.");
      } finally {
        setLoading(false);
      }
    };
    void loadEntries();
  }, [canBrowse, mode, path, sessionId]);

  const handleOpenEntry = async (entry: FileEntry) => {
    if (entry.is_dir) {
      setPath(entry.path);
      setPreviewTitle("");
      setPreviewText("");
      return;
    }

    setPreviewLoading(true);
    setPreviewTitle(entry.path);
    setPreviewText("");
    setError("");
    try {
      let url = "";
      if (mode === "local_session") {
        url = `/api/files/${encodeURIComponent(sessionId)}/${encodePath(entry.path)}`;
      } else if (mode === "local_artifacts") {
        url = `/api/artifacts/files/${encodePath(entry.path)}`;
      } else if (mode === "vps_workspaces") {
        url = `/api/vps/file?scope=workspaces&path=${encodeURIComponent(entry.path)}`;
      } else {
        url = `/api/vps/file?scope=artifacts&path=${encodeURIComponent(entry.path)}`;
      }
      const res = await fetch(url);
      const text = await res.text();
      if (!res.ok) {
        throw new Error(text || `File read failed (${res.status})`);
      }
      setPreviewText(text);
    } catch (err: any) {
      setPreviewText("");
      setError(err?.message || "Failed to open file.");
    } finally {
      setPreviewLoading(false);
    }
  };

  const handleSyncVps = async () => {
    setSyncing(true);
    setSyncMessage("");
    try {
      const res = await fetch("/api/vps/sync/now", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (!res.ok) {
        const detail = typeof data?.detail === "string" ? data.detail : JSON.stringify(data?.detail || data);
        throw new Error(detail || "VPS sync failed.");
      }
      const stdout = String(data?.stdout || "").trim();
      setSyncMessage(stdout ? `Sync complete.\n${stdout}` : "Sync complete.");
      setPath("");
    } catch (err: any) {
      setSyncMessage(err?.message || "VPS sync failed.");
    } finally {
      setSyncing(false);
    }
  };

  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 p-4 md:p-6">
      <div className="mx-auto w-full max-w-7xl space-y-4">
        <div className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-lg font-semibold tracking-tight">File Browser</h1>
            <div className="ml-auto flex items-center gap-2">
              <Link
                href="/"
                className="rounded-md border border-cyan-700 bg-cyan-600/20 px-3 py-2 text-xs uppercase tracking-widest text-cyan-100 hover:bg-cyan-600/30"
              >
                Back to App
              </Link>
            </div>
          </div>

          <div className="mt-4 grid gap-3 md:grid-cols-4">
            <label className="flex flex-col gap-1 text-xs text-slate-300">
              Source
              <select
                value={mode}
                onChange={(e) => {
                  setMode(e.target.value as SourceMode);
                  setPath("");
                  setPreviewTitle("");
                  setPreviewText("");
                }}
                className="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm"
              >
                {SOURCE_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </label>

            <label className="flex flex-col gap-1 text-xs text-slate-300">
              Session
              <select
                disabled={!needsSession}
                value={sessionId}
                onChange={(e) => {
                  setSessionId(e.target.value);
                  setPath("");
                  setPreviewTitle("");
                  setPreviewText("");
                }}
                className="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm disabled:opacity-50"
              >
                {!sessions.length && <option value="">No sessions</option>}
                {sessions.map((s) => (
                  <option key={s.session_id} value={s.session_id}>
                    {s.session_id}
                  </option>
                ))}
              </select>
            </label>

            <div className="flex flex-col gap-1 text-xs text-slate-300">
              <span>Path</span>
              <div className="rounded border border-slate-700 bg-slate-950 px-2 py-2 text-sm font-mono truncate">
                {locationLabel}
              </div>
            </div>

            <div className="flex items-end gap-2">
              <button
                type="button"
                onClick={() => setPath(parentPath(path))}
                disabled={!path}
                className="rounded border border-slate-700 bg-slate-900 px-3 py-2 text-xs uppercase tracking-widest disabled:opacity-40"
              >
                Up
              </button>
              {(mode === "vps_workspaces" || mode === "vps_artifacts") && (
                <button
                  type="button"
                  onClick={handleSyncVps}
                  disabled={syncing}
                  className="rounded border border-emerald-700 bg-emerald-600/20 px-3 py-2 text-xs uppercase tracking-widest text-emerald-100 disabled:opacity-50"
                >
                  {syncing ? "Syncing..." : "Sync VPS Now"}
                </button>
              )}
            </div>
          </div>

          {syncMessage && (
            <pre className="mt-3 max-h-36 overflow-auto rounded border border-slate-700 bg-slate-950/80 p-2 text-[11px] text-slate-300">
              {syncMessage}
            </pre>
          )}
          {error && <div className="mt-3 text-sm text-red-400">{error}</div>}
        </div>

        <div className="grid gap-4 lg:grid-cols-2">
          <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-3">
            <h2 className="mb-2 text-xs uppercase tracking-widest text-slate-400">Entries</h2>
            {!canBrowse ? (
              <div className="text-sm text-slate-400">Select a session to browse files.</div>
            ) : loading ? (
              <div className="text-sm text-slate-400">Loading...</div>
            ) : !entries.length ? (
              <div className="text-sm text-slate-400">No files in this location.</div>
            ) : (
              <div className="max-h-[60vh] overflow-auto rounded border border-slate-800">
                {entries.map((entry) => (
                  <button
                    key={entry.path}
                    type="button"
                    onClick={() => handleOpenEntry(entry)}
                    className="flex w-full items-center gap-2 border-b border-slate-800 px-3 py-2 text-left text-sm hover:bg-slate-800/60"
                  >
                    <span className="w-5">{entry.is_dir ? "📁" : "📄"}</span>
                    <span className="flex-1 truncate font-mono">{entry.name}</span>
                    <span className="text-[11px] text-slate-500">{entry.is_dir ? "" : formatBytes(entry.size)}</span>
                  </button>
                ))}
              </div>
            )}
          </section>

          <section className="rounded-xl border border-slate-800 bg-slate-900/70 p-3">
            <h2 className="mb-2 text-xs uppercase tracking-widest text-slate-400">
              Preview {previewTitle ? `- ${previewTitle}` : ""}
            </h2>
            {previewLoading ? (
              <div className="text-sm text-slate-400">Loading file...</div>
            ) : previewText ? (
              <pre className="max-h-[60vh] overflow-auto rounded border border-slate-800 bg-slate-950/80 p-3 text-[12px] leading-5 text-slate-200">
                {previewText}
              </pre>
            ) : (
              <div className="text-sm text-slate-400">Select a file to view content.</div>
            )}
          </section>
        </div>
      </div>
    </main>
  );
}
