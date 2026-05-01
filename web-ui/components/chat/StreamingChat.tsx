/**
 * StreamingChat — Track C Commit 1.
 *
 * Self-contained live-chat component that connects to the WebSocket
 * gateway, subscribes to streaming events, and lets the user reply via
 * `sendInputResponse`. Built fresh on top of the existing `lib/websocket`
 * primitives — does NOT extract from `app/page.tsx`. The legacy root
 * viewer keeps working unchanged; Track C C9 retires it later.
 *
 * Foundation only: minimal event rendering + functional composer. Future
 * Track C work (after re-evaluation) can add tool-call UIs, sub-agent
 * attribution, work-product handoff, approvals, etc.
 *
 * The viewer route (`/dashboard/viewer/<kind>/<id>`) renders this when
 * the live-writer feature flag is on (Track C Commit 2).
 */

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { getWebSocket } from "@/lib/websocket";
import type {
  ConnectionStatus,
  EventType,
  WebSocketEvent,
} from "@/types/agent";

// ── Local message model ──────────────────────────────────────────────────────
//
// Intentionally simple: every WebSocket event we care about gets condensed
// to a single message row with role, content, timestamp, and the raw
// event type for diagnostics. Rich tool-call cards, work-product previews,
// sub-agent timeline, and approval prompts are deliberately deferred to
// later Track C commits — this commit lands the foundation contract only.

export type StreamingMessage = {
  id: string;
  role: "user" | "assistant" | "system" | "tool" | "thinking" | "error";
  content: string;
  ts: number;
  source: EventType;
};

export type StreamingChatProps = {
  /** Existing session to attach to (live writer mode). */
  sessionId?: string | null;
  /** Run id for hydration cross-check (display only). */
  runId?: string | null;
  /**
   * When true, ignore sessionId and start a brand-new session. Used by
   * the future /dashboard/compose route. Defaults to false.
   */
  newSession?: boolean;
  /** Optional seed message dispatched once the session is connected. */
  initialMessage?: string | null;
  /** When true, send the initialMessage automatically. */
  autoSend?: boolean;
  /** Hide composer (read-only mode). */
  readOnly?: boolean;
  /** Optional className for the outer container. */
  className?: string;
};

// ── Helpers ──────────────────────────────────────────────────────────────────

const PAN_PATTERN = /\b(?:\d[ -]?){13,19}\b/g;

function maskPan(text: string): string {
  // Defense-in-depth carryover from Track B hydration: any card-shaped
  // digit run gets reduced to ••••<last4>. Cheap and prevents accidental
  // surfacing of raw card data through chat events.
  return text.replace(PAN_PATTERN, (match) => {
    const digits = match.replace(/\D/g, "");
    if (digits.length < 13) return match;
    return "••••" + digits.slice(-4);
  });
}

function newId(): string {
  return `msg_${Date.now()}_${Math.random().toString(36).slice(2, 8)}`;
}

function eventToMessage(event: WebSocketEvent): StreamingMessage | null {
  const ts = typeof event.timestamp === "number" ? event.timestamp : Date.now() / 1000;
  const source = event.type;
  const data = (event.data ?? {}) as Record<string, unknown>;

  switch (source) {
    case "text": {
      const content = String((data as { text?: unknown }).text ?? "");
      if (!content) return null;
      return { id: newId(), role: "assistant", content: maskPan(content), ts, source };
    }
    case "thinking": {
      const content = String((data as { content?: unknown }).content ?? "");
      if (!content) return null;
      return { id: newId(), role: "thinking", content: maskPan(content), ts, source };
    }
    case "tool_call": {
      const name = String((data as { tool_name?: unknown }).tool_name ?? "tool");
      const inputObj = (data as { tool_input?: unknown }).tool_input;
      const inputJson =
        inputObj && typeof inputObj === "object"
          ? JSON.stringify(inputObj).slice(0, 200)
          : String(inputObj ?? "");
      return {
        id: newId(),
        role: "tool",
        content: `→ ${name}(${inputJson})`,
        ts,
        source,
      };
    }
    case "tool_result": {
      const summary = String((data as { result?: unknown }).result ?? "").slice(0, 400);
      return {
        id: newId(),
        role: "tool",
        content: `← ${maskPan(summary)}`,
        ts,
        source,
      };
    }
    case "error": {
      const msg = String((data as { message?: unknown }).message ?? "error");
      return { id: newId(), role: "error", content: msg, ts, source };
    }
    case "input_required": {
      const prompt = String((data as { prompt?: unknown }).prompt ?? "agent is waiting for input");
      return { id: newId(), role: "system", content: prompt, ts, source };
    }
    default:
      return null;
  }
}

function extractInputId(event: WebSocketEvent): string | null {
  if (event.type !== "input_required") return null;
  const data = (event.data ?? {}) as Record<string, unknown>;
  const id = data.input_id;
  return typeof id === "string" && id.trim() ? id.trim() : null;
}

// ── Subscribed event types ───────────────────────────────────────────────────

const SUBSCRIBED_EVENTS: EventType[] = [
  "text",
  "thinking",
  "tool_call",
  "tool_result",
  "error",
  "input_required",
  "session_info",
  "iteration_end",
];

// ── Component ────────────────────────────────────────────────────────────────

export default function StreamingChat({
  sessionId,
  runId,
  newSession = false,
  initialMessage,
  autoSend = false,
  readOnly = false,
  className,
}: StreamingChatProps) {
  const [messages, setMessages] = useState<StreamingMessage[]>([]);
  const [status, setStatus] = useState<ConnectionStatus>("disconnected");
  const [pendingInputId, setPendingInputId] = useState<string | null>(null);
  const [composerValue, setComposerValue] = useState<string>(initialMessage ?? "");
  const [isSending, setIsSending] = useState(false);

  const seededRef = useRef(false);
  const messagesEndRef = useRef<HTMLDivElement | null>(null);

  // Connect on mount, disconnect on unmount.
  useEffect(() => {
    const ws = getWebSocket();

    const unsubscribers: Array<() => void> = [];

    unsubscribers.push(
      ws.onStatus((next) => setStatus(next)),
    );

    for (const eventType of SUBSCRIBED_EVENTS) {
      unsubscribers.push(
        ws.on(eventType, (event) => {
          const inputId = extractInputId(event);
          if (inputId) setPendingInputId(inputId);
          const msg = eventToMessage(event);
          if (msg) {
            setMessages((prev) => [...prev, msg]);
          }
        }),
      );
    }

    ws.connect();

    if (newSession) {
      ws.startNewSession();
    } else if (sessionId) {
      ws.attachToSession(sessionId);
    }

    return () => {
      unsubscribers.forEach((fn) => fn());
      // Note: we don't disconnect on unmount because the WebSocket is a
      // shared singleton (lib/websocket.ts:getWebSocket). Other surfaces
      // may still need it. Cleanup of the singleton happens when the user
      // navigates away from any chat-using page entirely.
    };
  }, [sessionId, newSession]);

  // Auto-send the seed message once we're connected.
  useEffect(() => {
    if (!autoSend || seededRef.current) return;
    if (status !== "connected") return;
    if (!initialMessage) return;
    seededRef.current = true;
    submitInput(initialMessage);
    setComposerValue("");
    // submitInput is stable enough for this one-shot effect; not memoized
    // intentionally (avoids extra closure churn for a foundation commit).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, autoSend, initialMessage]);

  // Auto-scroll to newest message.
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages.length]);

  const submitInput = useCallback(
    (raw: string) => {
      const text = raw.trim();
      if (!text) return;
      const ws = getWebSocket();
      const local: StreamingMessage = {
        id: newId(),
        role: "user",
        content: maskPan(text),
        ts: Date.now() / 1000,
        source: "input_response",
      };
      setMessages((prev) => [...prev, local]);
      setIsSending(true);
      try {
        // sendInputResponse requires a pending input_id from the agent;
        // when there's none yet (e.g. first user message kicking off the
        // session), we use a synthetic id so the gateway can route it.
        const inputId = pendingInputId ?? `user_${Date.now()}`;
        ws.sendInputResponse(inputId, text);
        setPendingInputId(null);
      } catch (err) {
        setMessages((prev) => [
          ...prev,
          {
            id: newId(),
            role: "error",
            content: err instanceof Error ? err.message : String(err),
            ts: Date.now() / 1000,
            source: "error",
          },
        ]);
      } finally {
        setIsSending(false);
      }
    },
    [pendingInputId],
  );

  const onComposerSubmit = useCallback(
    (e: React.FormEvent) => {
      e.preventDefault();
      if (!composerValue.trim()) return;
      submitInput(composerValue);
      setComposerValue("");
    },
    [composerValue, submitInput],
  );

  const statusBadge = useMemo<{ color: string; label: string }>(() => {
    switch (status) {
      case "connected":
        return { color: "#7fffd4", label: "live" };
      case "connecting":
        return { color: "#ffcc70", label: "connecting…" };
      case "disconnected":
        return { color: "#ff7070", label: "disconnected" };
      default:
        return { color: "#888", label: status };
    }
  }, [status]);

  return (
    <div
      className={className}
      style={{
        display: "flex",
        flexDirection: "column",
        height: "100%",
        background: "#0a0a0a",
        color: "#ddd",
        fontFamily: "system-ui, -apple-system, sans-serif",
      }}
    >
      {/* Header */}
      <div
        style={{
          padding: "8px 12px",
          borderBottom: "1px solid #222",
          display: "flex",
          gap: 12,
          alignItems: "center",
          fontSize: 12,
        }}
      >
        <span
          style={{
            display: "inline-block",
            width: 8,
            height: 8,
            borderRadius: "50%",
            background: statusBadge.color,
          }}
        />
        <span style={{ color: statusBadge.color, textTransform: "uppercase", letterSpacing: "0.04em" }}>
          {statusBadge.label}
        </span>
        {sessionId ? <span style={{ opacity: 0.6 }}>session: {sessionId}</span> : null}
        {runId ? <span style={{ opacity: 0.6 }}>run: {runId}</span> : null}
      </div>

      {/* Messages */}
      <div
        style={{
          flex: 1,
          overflowY: "auto",
          padding: 12,
        }}
      >
        {messages.length === 0 ? (
          <div style={{ color: "#888", fontSize: 13, padding: 16 }}>
            {status === "connected"
              ? "Waiting for events…"
              : "Connecting…"}
          </div>
        ) : (
          messages.map((m) => (
            <div
              key={m.id}
              style={{
                marginBottom: 10,
                padding: 8,
                borderLeft: `3px solid ${
                  m.role === "user"
                    ? "#5a8eff"
                    : m.role === "assistant"
                    ? "#7fffd4"
                    : m.role === "tool"
                    ? "#ffcc70"
                    : m.role === "thinking"
                    ? "#888"
                    : m.role === "error"
                    ? "#ff7070"
                    : "#666"
                }`,
                background: "#181818",
                fontSize: 13,
              }}
            >
              <div style={{ fontSize: 11, color: "#888", marginBottom: 4 }}>
                <strong style={{ textTransform: "uppercase", letterSpacing: "0.04em" }}>
                  {m.role}
                </strong>
                <span style={{ marginLeft: 8 }}>{new Date(m.ts * 1000).toISOString().slice(11, 19)}</span>
                <span style={{ marginLeft: 8, opacity: 0.5 }}>{m.source}</span>
              </div>
              <pre
                style={{
                  margin: 0,
                  whiteSpace: "pre-wrap",
                  fontFamily: "ui-monospace, SF Mono, monospace",
                  color: "#ddd",
                }}
              >
                {m.content}
              </pre>
            </div>
          ))
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Composer */}
      {!readOnly ? (
        <form
          onSubmit={onComposerSubmit}
          style={{
            borderTop: "1px solid #222",
            padding: 8,
            display: "flex",
            gap: 8,
            background: "#0d0d0d",
          }}
        >
          <input
            type="text"
            value={composerValue}
            onChange={(e) => setComposerValue(e.target.value)}
            placeholder={
              status === "connected" ? "Send a message…" : "Connecting…"
            }
            disabled={status !== "connected" || isSending}
            style={{
              flex: 1,
              padding: "8px 12px",
              background: "#181818",
              border: "1px solid #333",
              borderRadius: 6,
              color: "#ddd",
              fontSize: 13,
              fontFamily: "system-ui, sans-serif",
            }}
            data-testid="streaming-chat-composer"
          />
          <button
            type="submit"
            disabled={!composerValue.trim() || status !== "connected" || isSending}
            style={{
              padding: "8px 16px",
              background: "#5469d4",
              border: "none",
              borderRadius: 6,
              color: "#fff",
              fontSize: 13,
              fontWeight: 600,
              cursor: composerValue.trim() && status === "connected" ? "pointer" : "not-allowed",
              opacity: composerValue.trim() && status === "connected" ? 1 : 0.5,
            }}
          >
            {isSending ? "…" : "Send"}
          </button>
        </form>
      ) : null}
    </div>
  );
}
