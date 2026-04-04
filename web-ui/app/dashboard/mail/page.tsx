"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Image from "next/image";

/* ── Types ──────────────────────────────────────────────────────────── */

type Thread = {
  thread_id: string;
  inbox_id: string;
  subject: string;
  preview: string;
  labels: string[];
  senders: string[];
  recipients: string[];
  message_count: number;
  created_at: string;
  updated_at: string;
};

type Message = {
  message_id: string;
  thread_id: string;
  from: string;
  to: string;
  subject: string;
  text: string;
  labels: string[];
  created_at: string;
};

type Draft = {
  draft_id: string;
  inbox_id: string;
  to: string;
  subject: string;
  text_preview: string;
  send_status: string | null;
  send_at: string;
  updated_at: string;
  created_at: string;
};

type MailStatus = {
  inbox_address: string;
  messages_sent: number;
  messages_received: number;
  drafts_created: number;
  ws_connected: boolean;
  ws_reconnect_count: number;
  ws_fail_opened: boolean;
  last_error: string;
};

type MailFetchError = {
  inbox_id?: string;
  error: string;
};

const API_BASE = "/api/dashboard/gateway";

/* ── Helpers ─────────────────────────────────────────────────────────── */

function timeAgo(dateStr: string): string {
  if (!dateStr) return "--";
  const ts = new Date(dateStr).getTime();
  if (isNaN(ts)) return "--";
  const delta = Math.max(0, (Date.now() - ts) / 1000);
  if (delta < 60) return "just now";
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`;
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`;
  return `${Math.floor(delta / 86400)}d ago`;
}

function inboxShortName(inboxId: string): string {
  if (!inboxId) return "??";
  const at = inboxId.indexOf("@");
  return at > 0 ? inboxId.substring(0, at) : inboxId.substring(0, 12);
}

function senderShortName(from: string): string {
  if (!from) return "Unknown";
  const at = from.indexOf("@");
  return at > 0 ? from.substring(0, at) : from;
}

async function readErrorDetail(res: Response, label: string): Promise<string> {
  const raw = (await res.text().catch(() => "")).trim();
  if (!raw) return `${label} ${res.status}`;
  if (raw.includes("<html") || raw.includes("<!DOCTYPE html")) {
    return `${label} ${res.status}: upstream timeout`;
  }
  return `${label} ${res.status}: ${raw}`;
}

/** Map known email patterns to avatar image paths */
function getAvatar(emailOrName: string): { src: string; alt: string } | null {
  const lower = (emailOrName || "").toLowerCase();
  if (lower.includes("simone") || lower.includes("oddcity")) {
    return { src: "/assets/avatars/simone.png", alt: "Simone" };
  }
  if (lower.includes("kevin") || lower.includes("kev") || lower.includes("kjdragan")) {
    return { src: "/assets/avatars/kevin.png", alt: "Kevin" };
  }
  return null;
}

/* ── Design Tokens (Stitch: Kinetic Command Deck) ────────────────── */

const TOKENS = {
  bg: "#0b1326",
  surfaceDim: "#0f1a33",
  surfaceLow: "#131f3d",
  surfaceHigh: "#1a2847",
  cyan: "#22D3EE",
  cyanDim: "rgba(34,211,238,0.12)",
  amber: "#EE9800",
  amberDim: "rgba(238,152,0,0.12)",
  green: "#4ADE80",
  greenDim: "rgba(74,222,128,0.12)",
  red: "#EF4444",
  redDim: "rgba(239,68,68,0.12)",
  textPrimary: "#E2E8F0",
  textSecondary: "#BBC9CD",
  textMuted: "#64748B",
  ghostBorder: "rgba(187,201,205,0.15)",
  fontMono: "'JetBrains Mono', 'Fira Code', monospace",
  fontUi: "'Inter', system-ui, sans-serif",
};

/* ── Main Component ──────────────────────────────────────────────────── */

export default function MailPage() {
  const router = useRouter();
  const searchParams = useSearchParams();
  /* ── State ─── */
  const [threads, setThreads] = useState<Thread[]>([]);
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [status, setStatus] = useState<MailStatus | null>(null);
  const [inboxes, setInboxes] = useState<string[]>([]);
  const [selectedInbox, setSelectedInbox] = useState<string>("");
  const [selectedThread, setSelectedThread] = useState<Thread | null>(null);
  const [threadMessages, setThreadMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(true);
  const [msgsLoading, setMsgsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [syncWarning, setSyncWarning] = useState<string | null>(null);
  const [lastRefreshAt, setLastRefreshAt] = useState<number | null>(null);
  const [draftSending, setDraftSending] = useState<string | null>(null);
  const [draftDeleting, setDraftDeleting] = useState<string | null>(null);
  const [deletingThread, setDeletingThread] = useState<string | null>(null);
  const [viewMode, setViewMode] = useState<"inbox" | "sent">("inbox");
  const [selectedThreadIds, setSelectedThreadIds] = useState<Set<string>>(new Set());
  const [isBulkDeleting, setIsBulkDeleting] = useState(false);
  const deepLinkHandled = useRef(false);
  const refreshAbortRef = useRef<AbortController | null>(null);
  const refreshSeqRef = useRef(0);

  /* ── Fetchers ─── */
  const fetchThreads = useCallback(async (
    options?: { inboxId?: string; label?: string },
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams();
    if (options?.inboxId) params.set("inbox_id", options.inboxId);
    if (options?.label) params.set("label", options.label);
    const res = await fetch(`${API_BASE}/api/v1/ops/agentmail/threads?${params}`, {
      cache: "no-store",
      signal,
    });
    if (!res.ok) {
      throw new Error(await readErrorDetail(res, "threads"));
    }
    const data = await res.json();
    return {
      threads: data.threads || [],
      inboxes: data.inboxes || [],
      partial: Boolean(data.partial),
      errors: Array.isArray(data.errors) ? (data.errors as MailFetchError[]) : [],
    };
  }, []);

  const fetchDrafts = useCallback(async (signal?: AbortSignal) => {
    const res = await fetch(`${API_BASE}/api/v1/ops/agentmail/drafts`, {
      cache: "no-store",
      signal,
    });
    if (!res.ok) {
      throw new Error(await readErrorDetail(res, "drafts"));
    }
    const data = await res.json();
    return {
      drafts: data.drafts || [],
      partial: Boolean(data.partial),
      errors: Array.isArray(data.errors) ? (data.errors as MailFetchError[]) : [],
    };
  }, []);

  const fetchStatus = useCallback(async (signal?: AbortSignal) => {
    const res = await fetch(`${API_BASE}/api/v1/ops/agentmail`, {
      cache: "no-store",
      signal,
    });
    if (!res.ok) {
      throw new Error(await readErrorDetail(res, "status"));
    }
    return await res.json();
  }, []);

  const fetchAll = useCallback(async (mode: "initial" | "manual" | "poll" = "manual") => {
    const fetchId = refreshSeqRef.current + 1;
    refreshSeqRef.current = fetchId;
    refreshAbortRef.current?.abort();
    const controller = new AbortController();
    refreshAbortRef.current = controller;
    const foreground = mode !== "poll";

    if (foreground) {
      setLoading(true);
    }
    setError(null);
    setSyncWarning(null);
    const activeLabel = viewMode === "sent" ? "sent" : undefined;
    const results = await Promise.allSettled([
      fetchThreads(
        {
          inboxId: selectedInbox || undefined,
          label: activeLabel,
        },
        controller.signal,
      ),
      fetchDrafts(controller.signal),
      fetchStatus(controller.signal),
    ]);

    if (controller.signal.aborted || refreshSeqRef.current !== fetchId) {
      return;
    }

    const threadsResult = results[0];
    if (threadsResult.status === "fulfilled") {
      setThreads(threadsResult.value.threads);
      setInboxes(threadsResult.value.inboxes);
    }

    const draftsResult = results[1];
    if (draftsResult.status === "fulfilled") {
      setDrafts(draftsResult.value.drafts);
    }

    const statusResult = results[2];
    if (statusResult.status === "fulfilled") {
      setStatus(statusResult.value as MailStatus);
    }

    const failures = results
      .filter((result): result is PromiseRejectedResult => result.status === "rejected")
      .filter((result) => !String(result.reason || "").includes("AbortError"))
      .map((result) => String(result.reason));
    if (failures.length > 0) {
      setError(
        `Mail refresh incomplete. Showing last successful snapshot. ${failures.join(" | ")}`
      );
    } else {
      setLastRefreshAt(Date.now());
    }

    const warnings: string[] = [];
    for (const r of results) {
      if (r.status === "fulfilled" && r.value && typeof r.value === "object" && "partial" in r.value && r.value.partial) {
        const val = r.value as { partial: boolean; errors: MailFetchError[] };
        warnings.push(...(val.errors || []).map(entry => entry.inbox_id ? `${entry.inbox_id}: ${entry.error}` : entry.error));
      }
    }
    
    if (warnings.length > 0) {
      setSyncWarning(`Partial AgentMail data: ${warnings.join(" | ")}`);
    }
    if (foreground && refreshSeqRef.current === fetchId) {
      setLoading(false);
    }
  }, [fetchThreads, fetchDrafts, fetchStatus, selectedInbox, viewMode]);

  const fetchThreadMessages = useCallback(async (thread: Thread) => {
    setSelectedThread(thread);
    setMsgsLoading(true);
    try {
      const params = new URLSearchParams();
      if (thread.inbox_id) params.set("inbox_id", thread.inbox_id);
      const res = await fetch(
        `${API_BASE}/api/v1/ops/agentmail/threads/${thread.thread_id}/messages?${params}`,
        { cache: "no-store" }
      );
      if (!res.ok) throw new Error(`messages ${res.status}`);
      const data = await res.json();
      setThreadMessages(data.messages || []);
    } catch (e) {
      console.error("Failed to fetch thread messages", e);
      setThreadMessages([]);
    } finally {
      setMsgsLoading(false);
    }
  }, []);

  const approveDraft = useCallback(async (draft: Draft) => {
    setDraftSending(draft.draft_id);
    try {
      const params = new URLSearchParams();
      if (draft.inbox_id) params.set("inbox_id", draft.inbox_id);
      const res = await fetch(`${API_BASE}/api/v1/ops/agentmail/drafts/${draft.draft_id}/send?${params}`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(await readErrorDetail(res, "send draft"));
      const data = await fetchDrafts();
      setDrafts(data.drafts);
    } catch (e) {
      console.error("Failed to send draft", e);
      setError(`Failed to send draft ${draft.draft_id}: ${String(e)}`);
    } finally {
      setDraftSending(null);
    }
  }, [fetchDrafts]);

  const discardDraft = useCallback(async (draft: Draft) => {
    if (!confirm(`Discard draft "${draft.subject || "(no subject)"}"?`)) return;
    setDraftDeleting(draft.draft_id);
    try {
      const params = new URLSearchParams();
      if (draft.inbox_id) params.set("inbox_id", draft.inbox_id);
      const res = await fetch(`${API_BASE}/api/v1/ops/agentmail/drafts/${draft.draft_id}?${params}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(await readErrorDetail(res, "discard draft"));
      setDrafts((prev) => prev.filter((item) => item.draft_id !== draft.draft_id));
    } catch (e) {
      console.error("Failed to discard draft", e);
      setError(`Failed to discard draft ${draft.draft_id}: ${String(e)}`);
    } finally {
      setDraftDeleting(null);
    }
  }, []);

  const deleteThread = useCallback(async (thread: Thread) => {
    if (!confirm(`Delete thread "${thread.subject || '(no subject)'}"?`)) return;
    setDeletingThread(thread.thread_id);
    try {
      const params = new URLSearchParams();
      params.set("inbox_id", thread.inbox_id);
      const res = await fetch(
        `${API_BASE}/api/v1/ops/agentmail/threads/${thread.thread_id}?${params}`,
        { method: "DELETE" }
      );
      if (!res.ok) throw new Error(`delete ${res.status}`);
      // Remove from local state
      setThreads((prev) => prev.filter((t) => t.thread_id !== thread.thread_id));
      // Clear reader if we deleted the selected thread
      if (selectedThread?.thread_id === thread.thread_id) {
        setSelectedThread(null);
        setThreadMessages([]);
      }
    } catch (e) {
      console.error("Failed to delete thread", e);
      alert("Failed to delete thread — see console.");
    } finally {
      setDeletingThread(null);
    }
  }, [selectedThread]);

  const bulkDeleteThreads = useCallback(async () => {
    if (selectedThreadIds.size === 0) return;
    if (!confirm(`Delete ${selectedThreadIds.size} selected thread(s)?`)) return;
    setIsBulkDeleting(true);
    const payloadThreads = threads
      .filter((t) => selectedThreadIds.has(t.thread_id))
      .map((t) => ({ thread_id: t.thread_id, inbox_id: t.inbox_id }));
      
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/agentmail/threads/bulk_delete`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ threads: payloadThreads }),
      });
      if (!res.ok) throw new Error(`bulk delete ${res.status}`);
      const data = await res.json();
      const successIds = new Set<string>(
        (data.results || [])
        .filter((r: any) => r.success)
        .map((r: any) => r.thread_id)
      );
      setThreads((prev) => prev.filter((t) => !successIds.has(t.thread_id)));
      if (selectedThread && successIds.has(selectedThread.thread_id)) {
        setSelectedThread(null);
        setThreadMessages([]);
      }
      setSelectedThreadIds(new Set());
      if (data.success_count < payloadThreads.length) {
         alert(`Deleted ${data.success_count} of ${payloadThreads.length}. Check console for details.`);
      }
    } catch (e) {
      console.error("Failed to bulk delete threads", e);
      alert("Failed to bulk delete \u2014 see console.");
    } finally {
      setIsBulkDeleting(false);
    }
  }, [selectedThreadIds, threads, selectedThread]);

  useEffect(() => {
    setSelectedThreadIds(new Set());
  }, [viewMode]);

  /* ── Effects ─── */
  useEffect(() => {
    void fetchAll("initial");
    const interval = setInterval(() => {
      void fetchAll("poll");
    }, 15_000);
    return () => {
      clearInterval(interval);
      refreshAbortRef.current?.abort();
    };
  }, [fetchAll]);

  /* Deep-link: auto-select thread from ?thread= query param */
  useEffect(() => {
    if (deepLinkHandled.current || loading || threads.length === 0) return;
    const threadId = searchParams.get("thread");
    if (!threadId) return;
    const match = threads.find((t) => t.thread_id === threadId);
    if (match) {
      deepLinkHandled.current = true;
      fetchThreadMessages(match);
    }
  }, [threads, loading, searchParams, fetchThreadMessages]);

  /* ── Derived State ─── */
  const filteredThreads = threads.filter((t) => {
    // Sent view is already fetched from AgentMail's sent label.
    if (viewMode === "sent") {
      return true;
    }
    // If inbox view, show threads where someone else sent it (or unknown)
    return !t.senders || t.senders.length === 0 || t.senders.some((s) => !s.includes(t.inbox_id));
  });

  /* ── Render ─── */
  return (
    <div
      style={{
        background: TOKENS.bg,
        color: TOKENS.textPrimary,
        fontFamily: TOKENS.fontUi,
        minHeight: "100vh",
        display: "flex",
        flexDirection: "column",
      }}
    >
      {/* ══════════ Header Bar ══════════ */}
      <header
        style={{
          background: TOKENS.surfaceDim,
          padding: "12px 24px",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          borderBottom: `1px solid ${TOKENS.ghostBorder}`,
        }}
      >
        <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
          <span
            className="material-symbols-outlined"
            style={{ fontSize: 22, color: TOKENS.cyan }}
          >
            mail
          </span>
          <h1
            style={{
              fontFamily: TOKENS.fontMono,
              fontSize: 15,
              fontWeight: 700,
              letterSpacing: "0.1em",
              margin: 0,
              color: TOKENS.textPrimary,
            }}
          >
            AGENTMAIL
          </h1>
          {status && (
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 16,
                marginLeft: 24,
                fontFamily: TOKENS.fontMono,
                fontSize: 11,
                color: TOKENS.textMuted,
              }}
            >
              <span>
                <span style={{ color: TOKENS.cyan }}>▲</span>{" "}
                {status.messages_sent ?? 0} sent
              </span>
              <span>
                <span style={{ color: TOKENS.green }}>▼</span>{" "}
                {status.messages_received ?? 0} recv
              </span>
              <span>
                <span style={{ color: TOKENS.amber }}>◆</span>{" "}
                {status.drafts_created ?? 0} drafts
              </span>
              <span
                style={{
                  color: status.ws_connected ? TOKENS.green : TOKENS.red,
                  fontWeight: 600,
                }}
              >
                {status.ws_connected ? "● WS LIVE" : "○ WS OFF"}
              </span>
              <span>
                {lastRefreshAt ? `↻ ${timeAgo(new Date(lastRefreshAt).toISOString())}` : "↻ pending"}
              </span>
            </div>
          )}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span
            className="material-symbols-outlined"
            onClick={() => void fetchAll("manual")}
            title="Refresh"
            style={{
              fontSize: 22,
              color: TOKENS.cyan,
              cursor: "pointer",
              padding: 4,
              transition: "opacity 0.15s",
            }}
            onMouseEnter={(e) => { (e.target as HTMLElement).style.opacity = "0.7"; }}
            onMouseLeave={(e) => { (e.target as HTMLElement).style.opacity = "1"; }}
          >
            refresh
          </span>
          <span
            className="material-symbols-outlined"
            onClick={() => {
              const w = window.open("/?new_session=1&focus_input=1", "ua-chat-window");
              if (w) w.focus();
            }}
            title="Chat"
            style={{
              fontSize: 20,
              color: TOKENS.textMuted,
              cursor: "pointer",
              padding: 4,
              transition: "color 0.15s",
            }}
            onMouseEnter={(e) => { (e.target as HTMLElement).style.color = TOKENS.cyan; }}
            onMouseLeave={(e) => { (e.target as HTMLElement).style.color = TOKENS.textMuted; }}
          >
            chat
          </span>
          <span
            className="material-symbols-outlined"
            onClick={() => router.push("/dashboard")}
            title="Home"
            style={{
              fontSize: 20,
              color: TOKENS.textMuted,
              cursor: "pointer",
              padding: 4,
              transition: "color 0.15s",
            }}
            onMouseEnter={(e) => { (e.target as HTMLElement).style.color = TOKENS.cyan; }}
            onMouseLeave={(e) => { (e.target as HTMLElement).style.color = TOKENS.textMuted; }}
          >
            home
          </span>
        </div>
      </header>

      {/* ══════════ Inbox Filter Tabs ══════════ */}
      <div
        style={{
          background: TOKENS.surfaceDim,
          padding: "0 24px",
          display: "flex",
          gap: 0,
          borderBottom: `1px solid ${TOKENS.ghostBorder}`,
        }}
      >
        <FilterTab
          label="ALL"
          active={selectedInbox === ""}
          onClick={() => setSelectedInbox("")}
          count={threads.length}
        />
        {inboxes.map((ib) => (
          <FilterTab
            key={ib}
            label={inboxShortName(ib).toUpperCase()}
            active={selectedInbox === ib}
            onClick={() => setSelectedInbox(ib)}
            count={threads.filter((t) => t.inbox_id === ib).length}
          />
        ))}
      </div>

      {/* ══════════ Error Banner ══════════ */}
      {error && (
        <div
          style={{
            background: TOKENS.redDim,
            color: TOKENS.red,
            padding: "8px 24px",
            fontFamily: TOKENS.fontMono,
            fontSize: 12,
          }}
        >
          ⚠ {error}
        </div>
      )}
      {syncWarning && (
        <div
          style={{
            background: TOKENS.amberDim,
            color: TOKENS.amber,
            padding: "8px 24px",
            fontFamily: TOKENS.fontMono,
            fontSize: 12,
          }}
        >
          ⚠ {syncWarning}
        </div>
      )}

      {/* ══════════ Main 3-Column Layout ══════════ */}
      <div style={{ display: "flex", flex: 1, minHeight: 0 }}>
        {/* ── Left Sidebar: Drafts + Stats ── */}
        <aside
          style={{
            width: 280,
            minWidth: 280,
            background: TOKENS.surfaceDim,
            borderRight: `1px solid ${TOKENS.ghostBorder}`,
            display: "flex",
            flexDirection: "column",
            overflow: "hidden",
          }}
        >
          {/* Draft Queue */}
          <div style={{ padding: "16px 16px 8px" }}>
            <SectionTitle icon="edit_note" label="PENDING DRAFTS" count={drafts.length} />
          </div>
          <div style={{ flex: 1, overflowY: "auto", padding: "0 16px 16px" }}>
            {drafts.length === 0 ? (
              <EmptyState message="No pending manual-review drafts" />
            ) : (
              drafts.map((d) => (
                <DraftCard
                  key={d.draft_id}
                  draft={d}
                  sending={draftSending === d.draft_id}
                  deleting={draftDeleting === d.draft_id}
                  onApprove={() => approveDraft(d)}
                  onDiscard={() => discardDraft(d)}
                />
              ))
            )}
          </div>

          {/* Stats Panel */}
          {status && (
            <div
              style={{
                borderTop: `1px solid ${TOKENS.ghostBorder}`,
                padding: 16,
              }}
            >
              <SectionTitle icon="monitoring" label="SYSTEM STATUS" />
              <div
                style={{
                  display: "grid",
                  gridTemplateColumns: "1fr 1fr",
                  gap: 8,
                  marginTop: 8,
                }}
              >
                <StatCell label="Sent" value={status.messages_sent} color={TOKENS.cyan} />
                <StatCell
                  label="Received"
                  value={status.messages_received}
                  color={TOKENS.green}
                />
                <StatCell
                  label="Drafts"
                  value={status.drafts_created}
                  color={TOKENS.amber}
                />
                <StatCell
                  label="WS Reconnects"
                  value={status.ws_reconnect_count}
                  color={
                    status.ws_reconnect_count > 5 ? TOKENS.red : TOKENS.textMuted
                  }
                />
              </div>
              {status.last_error && (
                <div
                  style={{
                    marginTop: 8,
                    fontSize: 10,
                    fontFamily: TOKENS.fontMono,
                    color: TOKENS.red,
                    background: TOKENS.redDim,
                    padding: "4px 6px",
                    wordBreak: "break-all",
                  }}
                >
                  {status.last_error}
                </div>
              )}
            </div>
          )}
        </aside>

        {/* ── Center: Thread List ── */}
        <div
          style={{
            flex: 1,
            minWidth: 0,
            display: "flex",
            flexDirection: "column",
            borderRight: selectedThread
              ? `1px solid ${TOKENS.ghostBorder}`
              : "none",
          }}
        >
          <div
            style={{
              padding: "16px 20px 8px",
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
            }}
          >
            {selectedThreadIds.size > 0 ? (
              <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                <span style={{ fontSize: 12, color: TOKENS.textPrimary, fontWeight: 600 }}>
                  {selectedThreadIds.size} selected
                </span>
                <button
                  onClick={bulkDeleteThreads}
                  disabled={isBulkDeleting}
                  style={{
                    background: TOKENS.redDim,
                    color: TOKENS.red,
                    border: `1px solid ${TOKENS.red}`,
                    padding: "4px 10px",
                    borderRadius: 4,
                    fontSize: 11,
                    fontWeight: 600,
                    cursor: isBulkDeleting ? "not-allowed" : "pointer",
                    display: "flex",
                    alignItems: "center",
                    gap: 6,
                    opacity: isBulkDeleting ? 0.6 : 1,
                  }}
                >
                  <span className="material-symbols-outlined" style={{ fontSize: 14 }}>delete</span>
                  {isBulkDeleting ? "Deleting..." : "Delete"}
                </button>
                <button
                  onClick={() => setSelectedThreadIds(new Set())}
                  style={{
                    background: "transparent",
                    color: TOKENS.textMuted,
                    border: "none",
                    fontSize: 11,
                    cursor: "pointer",
                  }}
                >
                  Cancel
                </button>
              </div>
            ) : (
              <SectionTitle
                icon="forum"
                label="THREADS"
                count={filteredThreads.length}
              />
            )}
            {/* View Toggle: Inbox | Sent */}
            <div
              style={{
                display: "flex",
                background: TOKENS.surfaceHigh,
                padding: 2,
                borderRadius: 4,
              }}
            >
              <button
                onClick={() => setViewMode("inbox")}
                style={{
                  background: viewMode === "inbox" ? TOKENS.surfaceDim : "transparent",
                  color: viewMode === "inbox" ? TOKENS.textPrimary : TOKENS.textMuted,
                  border: "none",
                  padding: "4px 12px",
                  fontSize: 11,
                  fontFamily: TOKENS.fontMono,
                  fontWeight: 600,
                  cursor: "pointer",
                  borderRadius: 2,
                  transition: "all 0.15s",
                }}
              >
                INBOX
              </button>
              <button
                onClick={() => setViewMode("sent")}
                style={{
                  background: viewMode === "sent" ? TOKENS.surfaceDim : "transparent",
                  color: viewMode === "sent" ? TOKENS.textPrimary : TOKENS.textMuted,
                  border: "none",
                  padding: "4px 12px",
                  fontSize: 11,
                  fontFamily: TOKENS.fontMono,
                  fontWeight: 600,
                  cursor: "pointer",
                  borderRadius: 2,
                  transition: "all 0.15s",
                }}
              >
                SENT
              </button>
            </div>
          </div>
          {/* Select All Row */}
          {filteredThreads.length > 0 && (
            <div style={{ padding: "8px 20px 8px 32px", borderBottom: `1px solid ${TOKENS.ghostBorder}`, display: "flex", alignItems: "center", gap: 8 }}>
              <input
                type="checkbox"
                checked={filteredThreads.length > 0 && selectedThreadIds.size === filteredThreads.length}
                onChange={(e) => {
                  if (e.target.checked) {
                    setSelectedThreadIds(new Set(filteredThreads.map(t => t.thread_id)));
                  } else {
                    setSelectedThreadIds(new Set());
                  }
                }}
                style={{ cursor: "pointer", accentColor: TOKENS.cyan }}
              />
              <span style={{ fontSize: 11, color: TOKENS.textMuted, fontFamily: TOKENS.fontUi, userSelect: "none", cursor: "pointer" }} onClick={() => {
                if (selectedThreadIds.size === filteredThreads.length) {
                  setSelectedThreadIds(new Set());
                } else {
                  setSelectedThreadIds(new Set(filteredThreads.map(t => t.thread_id)));
                }
              }}>Select All</span>
            </div>
          )}
          <div style={{ flex: 1, overflowY: "auto", padding: "0 20px 20px" }}>
            {loading && filteredThreads.length === 0 ? (
              <LoadingState />
            ) : filteredThreads.length === 0 ? (
              <EmptyState message={`No ${viewMode} threads found`} />
            ) : (
              filteredThreads.map((t) => (
                <ThreadRow
                  key={`${t.inbox_id}-${t.thread_id}`}
                  thread={t}
                  selected={selectedThread?.thread_id === t.thread_id}
                  deleting={deletingThread === t.thread_id}
                  viewMode={viewMode}
                  checked={selectedThreadIds.has(t.thread_id)}
                  onToggleCheck={() => {
                    setSelectedThreadIds(prev => {
                      const next = new Set(prev);
                      if (next.has(t.thread_id)) next.delete(t.thread_id);
                      else next.add(t.thread_id);
                      return next;
                    });
                  }}
                  onClick={() => fetchThreadMessages(t)}
                  onDelete={() => deleteThread(t)}
                />
              ))
            )}
          </div>
        </div>

        {/* ── Right: Message Reader ── */}
        {selectedThread && (
          <div
            style={{
              width: "40%",
              minWidth: 340,
              display: "flex",
              flexDirection: "column",
              background: TOKENS.surfaceDim,
            }}
          >
            <div
              style={{
                padding: "16px 20px 12px",
                display: "flex",
                alignItems: "flex-start",
                justifyContent: "space-between",
                borderBottom: `1px solid ${TOKENS.ghostBorder}`,
              }}
            >
              <div>
                <div
                  style={{
                    fontSize: 14,
                    fontWeight: 600,
                    color: TOKENS.textPrimary,
                    lineHeight: 1.3,
                  }}
                >
                  {selectedThread.subject || "(no subject)"}
                </div>
                <div
                  style={{
                    fontSize: 11,
                    fontFamily: TOKENS.fontMono,
                    color: TOKENS.textMuted,
                    marginTop: 4,
                  }}
                >
                  {inboxShortName(selectedThread.inbox_id)} ·{" "}
                  {selectedThread.message_count} messages
                </div>
              </div>
              <button
                onClick={() => {
                  setSelectedThread(null);
                  setThreadMessages([]);
                }}
                style={{
                  background: "none",
                  border: "none",
                  color: TOKENS.textMuted,
                  cursor: "pointer",
                  fontSize: 18,
                  lineHeight: 1,
                  padding: 4,
                }}
              >
                ✕
              </button>
            </div>
            <div style={{ flex: 1, overflowY: "auto", padding: "12px 20px" }}>
              {msgsLoading ? (
                <LoadingState />
              ) : threadMessages.length === 0 ? (
                <EmptyState message="No messages in thread" />
              ) : (
                threadMessages.map((m) => (
                  <MessageBubble key={m.message_id} message={m} />
                ))
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

/* ── Sub-components ─────────────────────────────────────────────────── */

function FilterTab({
  label,
  active,
  onClick,
  count,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
  count?: number;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        background: active ? TOKENS.cyanDim : "transparent",
        color: active ? TOKENS.cyan : TOKENS.textMuted,
        border: "none",
        borderBottom: active ? `2px solid ${TOKENS.cyan}` : "2px solid transparent",
        padding: "10px 16px",
        fontFamily: TOKENS.fontMono,
        fontSize: 11,
        fontWeight: 600,
        letterSpacing: "0.05em",
        cursor: "pointer",
        display: "flex",
        alignItems: "center",
        gap: 6,
        transition: "all 0.15s",
      }}
    >
      {label}
      {count !== undefined && (
        <span
          style={{
            background: active ? TOKENS.cyan : TOKENS.ghostBorder,
            color: active ? TOKENS.bg : TOKENS.textMuted,
            fontSize: 9,
            fontWeight: 700,
            padding: "1px 5px",
            borderRadius: 0,
            minWidth: 16,
            textAlign: "center",
          }}
        >
          {count}
        </span>
      )}
    </button>
  );
}

function SectionTitle({
  icon,
  label,
  count,
}: {
  icon: string;
  label: string;
  count?: number;
}) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 8,
        fontFamily: TOKENS.fontMono,
        fontSize: 11,
        fontWeight: 700,
        letterSpacing: "0.1em",
        color: TOKENS.textMuted,
      }}
    >
      <span
        className="material-symbols-outlined"
        style={{ fontSize: 16, color: TOKENS.cyan }}
      >
        {icon}
      </span>
      {label}
      {count !== undefined && (
        <span
          style={{
            background: TOKENS.ghostBorder,
            color: TOKENS.textSecondary,
            fontSize: 9,
            fontWeight: 700,
            padding: "1px 5px",
            borderRadius: 0,
            marginLeft: 4,
          }}
        >
          {count}
        </span>
      )}
    </div>
  );
}

function ThreadRow({
  thread,
  selected,
  deleting,
  viewMode,
  checked,
  onToggleCheck,
  onClick,
  onDelete,
}: {
  thread: Thread;
  selected: boolean;
  deleting?: boolean;
  viewMode: "inbox" | "sent";
  checked?: boolean;
  onToggleCheck?: () => void;
  onClick: () => void;
  onDelete: () => void;
}) {
  const [hovered, setHovered] = useState(false);
  const showTo = viewMode === "sent";

  let partyLabel = "";
  if (showTo) {
    const recips = thread.recipients || [];
    partyLabel = recips.length > 0 ? `To: ${recips.map(senderShortName).join(", ")}` : "To: (unknown)";
  } else {
    // Determine the primary sender (who isn't the inbox itself, if possible)
    const senders = thread.senders || [];
    const externalSenders = senders.filter((s) => !s.includes(thread.inbox_id));
    const displaySenders = externalSenders.length > 0 ? externalSenders : senders;
    partyLabel = displaySenders.length > 0 ? `From: ${displaySenders.map(senderShortName).join(", ")}` : "From: (unknown)";
  }

  return (
    <div
      style={{
        position: "relative",
        marginBottom: 2,
        opacity: deleting ? 0.4 : 1,
        transition: "opacity 0.2s",
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <button
        onClick={onClick}
        style={{
          width: "100%",
          display: "flex",
          alignItems: "flex-start",
          gap: 12,
          padding: "12px 14px",
          background: selected ? TOKENS.cyanDim : hovered ? TOKENS.surfaceLow : "transparent",
          border: "none",
          borderLeft: selected
            ? `3px solid ${TOKENS.cyan}`
            : "3px solid transparent",
          textAlign: "left",
          cursor: "pointer",
          transition: "all 0.12s",
        }}
      >
        <div style={{ display: "flex", alignItems: "center", height: "100%", marginTop: 4 }} onClick={(e) => e.stopPropagation()}>
          <input
            type="checkbox"
            checked={!!checked}
            onChange={(e) => {
              e.stopPropagation();
              onToggleCheck?.();
            }}
            style={{ cursor: "pointer", accentColor: TOKENS.cyan, width: 14, height: 14 }}
          />
        </div>
        {/* Direction Icon or Badge */}
        <span
          className="material-symbols-outlined"
          style={{
            fontSize: 20,
            color: showTo ? TOKENS.cyan : TOKENS.green,
            marginTop: 2,
            opacity: 0.8,
          }}
        >
          {showTo ? "send" : "inbox"}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: TOKENS.textPrimary,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
            }}
          >
            {thread.subject || "(no subject)"}
          </div>
          <div
            style={{
              fontSize: 11,
              fontFamily: TOKENS.fontMono,
              color: showTo ? TOKENS.cyan : TOKENS.green,
              marginTop: 4,
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {partyLabel}
          </div>
          {thread.preview && (
            <div
              style={{
                fontSize: 12,
                color: TOKENS.textMuted,
                overflow: "hidden",
                textOverflow: "ellipsis",
                whiteSpace: "nowrap",
                marginTop: 4,
              }}
            >
              {thread.preview}
            </div>
          )}
          <div
            style={{
              display: "flex",
              gap: 10,
              marginTop: 4,
              fontFamily: TOKENS.fontMono,
              fontSize: 10,
              color: TOKENS.textMuted,
            }}
          >
            <span>{thread.message_count} msg{thread.message_count !== 1 && "s"}</span>
            <span>{timeAgo(thread.updated_at || thread.created_at)}</span>
          </div>
        </div>
      </button>
      {/* Trash icon — bottom-right on hover */}
      {hovered && !deleting && (
        <span
          className="material-symbols-outlined"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
          title="Delete thread"
          style={{
            position: "absolute",
            bottom: 8,
            right: 10,
            fontSize: 16,
            color: TOKENS.textMuted,
            cursor: "pointer",
            padding: 2,
            transition: "color 0.15s",
            zIndex: 2,
          }}
          onMouseEnter={(e) => { (e.target as HTMLElement).style.color = TOKENS.red; }}
          onMouseLeave={(e) => { (e.target as HTMLElement).style.color = TOKENS.textMuted; }}
        >
          delete
        </span>
      )}
    </div>
  );
}

function DraftCard({
  draft,
  sending,
  deleting,
  onApprove,
  onDiscard,
}: {
  draft: Draft;
  sending: boolean;
  deleting: boolean;
  onApprove: () => void;
  onDiscard: () => void;
}) {
  const busy = sending || deleting;
  return (
    <div
      style={{
        background: TOKENS.surfaceLow,
        padding: "10px 12px",
        marginBottom: 6,
        borderLeft: `3px solid ${TOKENS.amber}`,
      }}
    >
      <div
        style={{
          fontSize: 12,
          fontWeight: 600,
          color: TOKENS.textPrimary,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {draft.subject || "(no subject)"}
      </div>
      <div
        style={{
          fontSize: 11,
          color: TOKENS.textMuted,
          marginTop: 2,
          fontFamily: TOKENS.fontMono,
        }}
      >
        → {draft.to || "?"}
      </div>
      <div
        style={{
          fontSize: 10,
          color: TOKENS.textMuted,
          marginTop: 4,
          fontFamily: TOKENS.fontMono,
        }}
      >
        {draft.send_status ? `${draft.send_status} · ` : ""}created {timeAgo(draft.created_at)}
      </div>
      {draft.text_preview && (
        <div
          style={{
            fontSize: 11,
            color: TOKENS.textSecondary,
            marginTop: 4,
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
          }}
        >
          {draft.text_preview}
        </div>
      )}
      <div style={{ display: "flex", gap: 6, marginTop: 8 }}>
        <button
          onClick={onApprove}
          disabled={busy}
          style={{
            background: sending ? TOKENS.amberDim : TOKENS.amber,
            color: sending ? TOKENS.amber : TOKENS.bg,
            border: "none",
            borderRadius: 0,
            padding: "4px 12px",
            fontFamily: TOKENS.fontMono,
            fontSize: 10,
            fontWeight: 700,
            cursor: busy ? "wait" : "pointer",
            letterSpacing: "0.05em",
          }}
        >
          {sending ? "SENDING…" : "APPROVE & SEND"}
        </button>
        <button
          onClick={onDiscard}
          disabled={busy}
          style={{
            background: deleting ? TOKENS.redDim : "transparent",
            color: TOKENS.red,
            border: `1px solid ${TOKENS.red}`,
            borderRadius: 0,
            padding: "4px 10px",
            fontFamily: TOKENS.fontMono,
            fontSize: 10,
            fontWeight: 700,
            cursor: busy ? "wait" : "pointer",
            letterSpacing: "0.05em",
            opacity: busy && !deleting ? 0.5 : 1,
          }}
        >
          {deleting ? "DISCARDING…" : "DISCARD"}
        </button>
      </div>
    </div>
  );
}

function MessageBubble({ message }: { message: Message }) {
  return (
    <div
      style={{
        background: TOKENS.surfaceLow,
        padding: "12px 14px",
        marginBottom: 8,
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 6,
        }}
      >
        <span
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          {getAvatar(message.from) ? (
            <Image
              src={getAvatar(message.from)!.src}
              alt={getAvatar(message.from)!.alt}
              width={22}
              height={22}
              style={{ borderRadius: 0, objectFit: "cover" }}
            />
          ) : (
            <span
              className="material-symbols-outlined"
              style={{ fontSize: 18, color: TOKENS.textMuted }}
            >
              person
            </span>
          )}
          <span
            style={{
              fontFamily: TOKENS.fontMono,
              fontSize: 12,
              fontWeight: 600,
              color: TOKENS.cyan,
            }}
          >
            {senderShortName(message.from)}
          </span>
        </span>
        <span
          style={{
            fontFamily: TOKENS.fontMono,
            fontSize: 10,
            color: TOKENS.textMuted,
          }}
        >
          {timeAgo(message.created_at)}
        </span>
      </div>
      {message.to && (
        <div
          style={{
            fontSize: 10,
            fontFamily: TOKENS.fontMono,
            color: TOKENS.textMuted,
            marginBottom: 6,
          }}
        >
          → {message.to}
        </div>
      )}
      <div
        style={{
          fontSize: 13,
          lineHeight: 1.55,
          color: TOKENS.textSecondary,
          whiteSpace: "pre-wrap",
          wordBreak: "break-word",
        }}
      >
        {message.text || "(empty message)"}
      </div>
    </div>
  );
}

function StatCell({
  label,
  value,
  color,
}: {
  label: string;
  value: number;
  color: string;
}) {
  return (
    <div
      style={{
        background: TOKENS.surfaceLow,
        padding: "8px 10px",
        textAlign: "center",
      }}
    >
      <div
        style={{
          fontFamily: TOKENS.fontMono,
          fontSize: 18,
          fontWeight: 700,
          color,
          lineHeight: 1,
        }}
      >
        {value}
      </div>
      <div
        style={{
          fontFamily: TOKENS.fontMono,
          fontSize: 9,
          color: TOKENS.textMuted,
          marginTop: 4,
          letterSpacing: "0.05em",
        }}
      >
        {label.toUpperCase()}
      </div>
    </div>
  );
}

function EmptyState({ message }: { message: string }) {
  return (
    <div
      style={{
        padding: 32,
        textAlign: "center",
        fontFamily: TOKENS.fontMono,
        fontSize: 12,
        color: TOKENS.textMuted,
      }}
    >
      {message}
    </div>
  );
}

function LoadingState() {
  return (
    <div
      style={{
        padding: 32,
        textAlign: "center",
        fontFamily: TOKENS.fontMono,
        fontSize: 12,
        color: TOKENS.textMuted,
      }}
    >
      Loading…
    </div>
  );
}
