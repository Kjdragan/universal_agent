"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { openOrFocusChatWindow } from "@/lib/chatWindow";
import {
  deleteSessionDirectoryEntry,
  fetchSessionDirectory,
  SessionDirectoryItem,
} from "@/lib/sessionDirectory";

function formatSessionTime(isoString?: string): { relative: string; absolute: string } | null {
  if (!isoString) return null;
  const date = new Date(isoString);
  if (isNaN(date.getTime())) return null;

  const now = Date.now();
  const diffMs = now - date.getTime();
  const diffSec = Math.floor(diffMs / 1000);
  const diffMin = Math.floor(diffSec / 60);
  const diffHr = Math.floor(diffMin / 60);
  const diffDays = Math.floor(diffHr / 24);

  let relative: string;
  if (diffSec < 60) relative = "just now";
  else if (diffMin < 60) relative = `${diffMin}m ago`;
  else if (diffHr < 24) relative = `${diffHr}h ago`;
  else if (diffDays < 7) relative = `${diffDays}d ago`;
  else relative = date.toLocaleDateString(undefined, { month: "short", day: "numeric" });

  const absolute = date.toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });

  return { relative, absolute };
}

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
      const firstLive = rows.find((row) => row.is_live_session !== false)?.session_id || "";
      setSelectedSession((prev) => {
        if (prev && rows.some((row) => row.session_id === prev)) return prev;
        return firstLive;
      });
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

  const selectedRow = useMemo(
    () => sortedSessions.find((row) => row.session_id === selectedSession) || null,
    [selectedSession, sortedSessions],
  );

  const selectedAttachable = Boolean(selectedRow?.is_live_session !== false && selectedSession);
  const selectedRunViewable = Boolean(selectedRow?.run_id);

  const openSelected = () => {
    if (!selectedSession) {
      openOrFocusChatWindow({ role: attachRole });
      return;
    }
    if (!selectedAttachable && selectedRow?.run_id) {
      openOrFocusChatWindow({ runId: selectedRow.run_id, role: "viewer" });
      return;
    }
    if (!selectedAttachable) {
      setError("The selected item is a run workspace, not a live session. Start a new chat or pick a live session.");
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
          <p className="text-sm text-muted-foreground">
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
            className="rounded-lg border border-primary/30/60 bg-primary/20 px-3 py-1.5 text-sm text-primary/90 hover:bg-primary/30"
          >
            New Session
          </button>
          <button
            type="button"
            onClick={openSelected}
            disabled={Boolean(selectedSession) && !selectedAttachable && !selectedRunViewable}
            className="rounded-lg border border-border bg-card/60 px-3 py-1.5 text-sm hover:bg-card"
          >
            Open/Focus Full Chat
          </button>
        </div>
      </div>

      <div className="rounded-xl border border-border bg-background/60 p-4 space-y-3">
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={refreshSessions}
            className="rounded-md border border-border bg-card/60 px-3 py-1.5 text-sm hover:bg-card"
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
            <div className="text-sm text-muted-foreground">No sessions found.</div>
          )}
          {sortedSessions.map((session) => {
            const active = selectedSession === session.session_id;
            const deleting = deletingSessionId === session.session_id;
            const createdTime = formatSessionTime(session.created_at);
            const activityTime = formatSessionTime(session.last_activity);
            return (
              <article
                key={session.session_id}
                className={[
                  "w-full rounded-lg border px-3 py-2 text-left transition",
                  active
                    ? "border-primary/25 bg-primary/10"
                    : "border-border bg-background/40 hover:bg-background/70",
                ].join(" ")}
              >
                <div className="flex items-center justify-between gap-2">
                  <button
                    type="button"
                    onClick={() => setSelectedSession(session.session_id)}
                    className="min-w-0 text-left"
                  >
                    <span className="block font-mono text-xs truncate">{session.session_id}</span>
                    <span className="text-[11px] text-muted-foreground">
                      {(session.source || "local")} · {session.status || "unknown"}
                    </span>
                    {session.run_id ? (
                      <span className="block text-[11px] text-primary/70 truncate">
                        run {session.run_id}
                        {session.attempt_count ? ` · ${session.attempt_count} attempt${session.attempt_count === 1 ? "" : "s"}` : ""}
                      </span>
                    ) : null}
                    {session.is_live_session === false ? (
                      <span className="block text-[11px] text-amber-300/80 truncate">
                        run workspace only · no live session attached
                      </span>
                    ) : null}
                    {session.description ? (
                      <span className="mt-0.5 block text-[11px] text-foreground/80/90 truncate" title={session.description}>
                        {session.description}
                      </span>
                    ) : (
                      <span className="mt-0.5 block text-[11px] text-muted italic truncate">no description yet</span>
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
                <div className="mt-1 flex items-center gap-3 text-[11px] text-muted-foreground truncate">
                  <span>owner: {session.owner || "unknown"} · memory: {session.memory_mode || "direct_only"}</span>
                  {createdTime && (
                    <span
                      className="shrink-0 text-[10px] text-muted-foreground/70"
                      title={`Started: ${createdTime.absolute}${activityTime ? ` · Last active: ${activityTime.absolute}` : ""}`}
                    >
                      🕐 {createdTime.relative}{activityTime ? ` · active ${activityTime.relative}` : ""}
                    </span>
                  )}
                </div>
                <div className="mt-1 text-[11px] text-muted truncate">
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
