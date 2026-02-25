"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { openOrFocusChatWindow } from "@/lib/chatWindow";
import {
  deleteSessionDirectoryEntry,
  fetchSessionDirectory,
  SessionDirectoryItem,
} from "@/lib/sessionDirectory";

export default function DashboardChatPage() {
  const [sessions, setSessions] = useState<SessionDirectoryItem[]>([]);
  const [selectedSession, setSelectedSession] = useState<string>("");
  const [attachRole, setAttachRole] = useState<"writer" | "viewer">("writer");
  const [loading, setLoading] = useState(false);
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshSessions = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const rows = await fetchSessionDirectory(300);
      setSessions(rows);
      setSelectedSession((prev) => prev || rows[0]?.session_id || "");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  const sortedSessions = useMemo(() => {
    const copy = [...sessions];
    copy.sort((a, b) => {
      const aTs = Date.parse(a.last_activity || "") || 0;
      const bTs = Date.parse(b.last_activity || "") || 0;
      return bTs - aTs;
    });
    return copy;
  }, [sessions]);

  const openSelected = () => {
    if (!selectedSession) {
      openOrFocusChatWindow({ role: attachRole });
      return;
    }
    openOrFocusChatWindow({ sessionId: selectedSession, attachMode: "tail", role: attachRole });
  };

  const deleteSession = async (sessionId: string) => {
    const target = sortedSessions.find((row) => row.session_id === sessionId);
    const label = target?.session_id || sessionId;
    const ok = window.confirm(`Delete session ${label}? This cannot be undone.`);
    if (!ok) return;

    setError(null);
    setDeletingSessionId(sessionId);
    try {
      await deleteSessionDirectoryEntry(sessionId);
      await refreshSessions();
      setSelectedSession((prev) => (prev === sessionId ? "" : prev));
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setDeletingSessionId(null);
    }
  };

  return (
    <div className="h-full min-h-[80vh] space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold tracking-tight">Chat Launcher</h1>
          <p className="text-sm text-slate-400">
            Open or focus the dedicated full-screen chat tab. Choose writer or viewer attach mode.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() =>
              openOrFocusChatWindow({
                role: "writer",
                newSession: true,
                focusInput: true,
              })
            }
            className="rounded-lg border border-emerald-700/60 bg-emerald-500/20 px-3 py-1.5 text-sm text-emerald-100 hover:bg-emerald-500/30"
          >
            New Session
          </button>
          <button
            type="button"
            onClick={openSelected}
            className="rounded-lg border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-sm hover:bg-slate-800"
          >
            Open/Focus Full Chat
          </button>
        </div>
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-900/60 p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={refreshSessions}
            className="rounded-md border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-sm hover:bg-slate-800"
            disabled={loading}
          >
            {loading ? "Refreshing..." : "Refresh Sessions"}
          </button>
        </div>

        {error && (
          <div className="rounded-md border border-red-700/60 bg-red-900/20 p-2 text-xs text-red-200">
            {error}
          </div>
        )}

        <div className="space-y-2 max-h-[50vh] overflow-y-auto">
          {sortedSessions.length === 0 && (
            <div className="text-sm text-slate-400">No sessions found.</div>
          )}
          {sortedSessions.map((session) => {
            const active = selectedSession === session.session_id;
            const deleting = deletingSessionId === session.session_id;
            return (
              <article
                key={session.session_id}
                className={[
                  "w-full rounded-lg border px-3 py-2 text-left transition",
                  active
                    ? "border-cyan-500/40 bg-cyan-500/10"
                    : "border-slate-800 bg-slate-950/40 hover:bg-slate-900/70",
                ].join(" ")}
              >
                <div className="flex items-center justify-between gap-2">
                  <button
                    type="button"
                    onClick={() => setSelectedSession(session.session_id)}
                    className="min-w-0 text-left"
                  >
                    <span className="block font-mono text-xs truncate">{session.session_id}</span>
                    <span className="text-[11px] text-slate-400">
                      {(session.source || "local")} · {session.status || "unknown"}
                    </span>
                    {session.description ? (
                      <span className="mt-0.5 block text-[11px] text-slate-300/90 truncate" title={session.description}>
                        {session.description}
                      </span>
                    ) : (
                      <span className="mt-0.5 block text-[11px] text-slate-600 italic truncate">no description yet</span>
                    )}
                  </button>
                  <button
                    type="button"
                    onClick={() => deleteSession(session.session_id)}
                    disabled={deleting}
                    className="rounded border border-red-700/70 bg-red-900/20 px-2 py-0.5 text-[11px] text-red-200 hover:bg-red-900/30 disabled:opacity-60"
                  >
                    {deleting ? "Deleting..." : "Delete"}
                  </button>
                </div>
                <div className="mt-1 text-[11px] text-slate-500 truncate">
                  owner: {session.owner || "unknown"} · memory: {session.memory_mode || "direct_only"}
                </div>
                <div className="mt-1 text-[11px] text-slate-600 truncate">
                  {session.workspace_dir || "workspace: n/a"}
                </div>
              </article>
            );
          })}
        </div>
      </div>
    </div>
  );
}
