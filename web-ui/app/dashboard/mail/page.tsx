"use client";

import { useCallback, useEffect, useState } from "react";

/* ── Types ──────────────────────────────────────────────────────────── */

type Thread = {
  thread_id: string;
  inbox_id: string;
  subject: string;
  preview: string;
  labels: string[];
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
  const [draftSending, setDraftSending] = useState<string | null>(null);

  /* ── Fetchers ─── */
  const fetchThreads = useCallback(async (inboxId?: string) => {
    try {
      const params = new URLSearchParams();
      if (inboxId) params.set("inbox_id", inboxId);
      const res = await fetch(`${API_BASE}/api/v1/ops/agentmail/threads?${params}`);
      if (!res.ok) throw new Error(`threads ${res.status}`);
      const data = await res.json();
      setThreads(data.threads || []);
      setInboxes(data.inboxes || []);
    } catch (e: unknown) {
      console.error("Failed to fetch threads", e);
    }
  }, []);

  const fetchDrafts = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/agentmail/drafts`);
      if (!res.ok) return;
      const data = await res.json();
      setDrafts(data.drafts || []);
    } catch {
      /* non-critical */
    }
  }, []);

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/agentmail`);
      if (!res.ok) return;
      const data = await res.json();
      setStatus(data);
    } catch {
      /* non-critical */
    }
  }, []);

  const fetchAll = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      await Promise.all([
        fetchThreads(selectedInbox || undefined),
        fetchDrafts(),
        fetchStatus(),
      ]);
    } catch (e: unknown) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  }, [fetchThreads, fetchDrafts, fetchStatus, selectedInbox]);

  const fetchThreadMessages = useCallback(async (thread: Thread) => {
    setSelectedThread(thread);
    setMsgsLoading(true);
    try {
      const params = new URLSearchParams();
      if (thread.inbox_id) params.set("inbox_id", thread.inbox_id);
      const res = await fetch(
        `${API_BASE}/api/v1/ops/agentmail/threads/${thread.thread_id}/messages?${params}`
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

  const approveDraft = useCallback(async (draftId: string) => {
    setDraftSending(draftId);
    try {
      const res = await fetch(`${API_BASE}/api/v1/ops/agentmail/drafts/${draftId}/send`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(`send draft ${res.status}`);
      await fetchDrafts();
    } catch (e) {
      console.error("Failed to send draft", e);
    } finally {
      setDraftSending(null);
    }
  }, [fetchDrafts]);

  /* ── Effects ─── */
  useEffect(() => {
    fetchAll();
    const interval = setInterval(fetchAll, 15_000);
    return () => clearInterval(interval);
  }, [fetchAll]);

  useEffect(() => {
    if (selectedInbox !== undefined) {
      fetchThreads(selectedInbox || undefined);
    }
  }, [selectedInbox, fetchThreads]);

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
            </div>
          )}
        </div>
        <button
          onClick={() => fetchAll()}
          style={{
            background: TOKENS.cyanDim,
            color: TOKENS.cyan,
            border: "none",
            borderRadius: 0,
            padding: "6px 14px",
            fontFamily: TOKENS.fontMono,
            fontSize: 11,
            fontWeight: 600,
            cursor: "pointer",
            letterSpacing: "0.05em",
          }}
        >
          ↻ REFRESH
        </button>
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
            <SectionTitle icon="edit_note" label="DRAFT QUEUE" count={drafts.length} />
          </div>
          <div style={{ flex: 1, overflowY: "auto", padding: "0 16px 16px" }}>
            {drafts.length === 0 ? (
              <EmptyState message="No pending drafts" />
            ) : (
              drafts.map((d) => (
                <DraftCard
                  key={d.draft_id}
                  draft={d}
                  sending={draftSending === d.draft_id}
                  onApprove={() => approveDraft(d.draft_id)}
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
          <div style={{ padding: "16px 20px 8px" }}>
            <SectionTitle
              icon="forum"
              label="THREADS"
              count={threads.length}
            />
          </div>
          <div style={{ flex: 1, overflowY: "auto", padding: "0 20px 20px" }}>
            {loading && threads.length === 0 ? (
              <LoadingState />
            ) : threads.length === 0 ? (
              <EmptyState message="No threads found" />
            ) : (
              threads.map((t) => (
                <ThreadRow
                  key={`${t.inbox_id}-${t.thread_id}`}
                  thread={t}
                  selected={selectedThread?.thread_id === t.thread_id}
                  onClick={() => fetchThreadMessages(t)}
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
  onClick,
}: {
  thread: Thread;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        width: "100%",
        display: "flex",
        alignItems: "flex-start",
        gap: 12,
        padding: "12px 14px",
        background: selected ? TOKENS.cyanDim : "transparent",
        border: "none",
        borderLeft: selected
          ? `3px solid ${TOKENS.cyan}`
          : "3px solid transparent",
        textAlign: "left",
        cursor: "pointer",
        transition: "all 0.12s",
        marginBottom: 2,
      }}
      onMouseEnter={(e) => {
        if (!selected)
          (e.currentTarget as HTMLElement).style.background = TOKENS.surfaceLow;
      }}
      onMouseLeave={(e) => {
        if (!selected)
          (e.currentTarget as HTMLElement).style.background = "transparent";
      }}
    >
      {/* Inbox Badge */}
      <span
        style={{
          fontFamily: TOKENS.fontMono,
          fontSize: 9,
          fontWeight: 700,
          padding: "2px 6px",
          background: TOKENS.surfaceHigh,
          color: TOKENS.textSecondary,
          whiteSpace: "nowrap",
          marginTop: 2,
          letterSpacing: "0.05em",
        }}
      >
        {inboxShortName(thread.inbox_id).toUpperCase()}
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
        {thread.preview && (
          <div
            style={{
              fontSize: 12,
              color: TOKENS.textMuted,
              overflow: "hidden",
              textOverflow: "ellipsis",
              whiteSpace: "nowrap",
              marginTop: 2,
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
  );
}

function DraftCard({
  draft,
  sending,
  onApprove,
}: {
  draft: Draft;
  sending: boolean;
  onApprove: () => void;
}) {
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
          disabled={sending}
          style={{
            background: sending ? TOKENS.amberDim : TOKENS.amber,
            color: sending ? TOKENS.amber : TOKENS.bg,
            border: "none",
            borderRadius: 0,
            padding: "4px 12px",
            fontFamily: TOKENS.fontMono,
            fontSize: 10,
            fontWeight: 700,
            cursor: sending ? "wait" : "pointer",
            letterSpacing: "0.05em",
          }}
        >
          {sending ? "SENDING…" : "APPROVE & SEND"}
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
            fontFamily: TOKENS.fontMono,
            fontSize: 12,
            fontWeight: 600,
            color: TOKENS.cyan,
          }}
        >
          {senderShortName(message.from)}
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
