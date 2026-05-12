"use client";

import { FormEvent, useCallback, useMemo, useState, useSyncExternalStore } from "react";

import { buildChatUrl } from "@/lib/chatWindow";

const API_BASE = "/api/dashboard/gateway";
const COMMAND_HISTORY_KEY = "ua.system_command_history.v1";
const COMMAND_HISTORY_MAX = 12;

type ChatHistoryEntry = {
  id: string;
  at: string;
  source_page: string;
  text: string;
};

function asText(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function readHistoryFromStorage(): ChatHistoryEntry[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(COMMAND_HISTORY_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed
      .filter((row) => row && typeof row === "object" && typeof row.text === "string")
      .slice(0, COMMAND_HISTORY_MAX) as ChatHistoryEntry[];
  } catch {
    return [];
  }
}

const HISTORY_SERVER_SNAPSHOT: ChatHistoryEntry[] = [];

// External-store subscribers — notified whenever we write history back.
const historyListeners = new Set<() => void>();
let cachedHistory: ChatHistoryEntry[] | null = null;

function getHistorySnapshot(): ChatHistoryEntry[] {
  if (typeof window === "undefined") return HISTORY_SERVER_SNAPSHOT;
  if (cachedHistory === null) {
    cachedHistory = readHistoryFromStorage();
  }
  return cachedHistory;
}

function subscribeHistory(listener: () => void): () => void {
  historyListeners.add(listener);
  return () => {
    historyListeners.delete(listener);
  };
}

function writeHistory(next: ChatHistoryEntry[]): void {
  cachedHistory = next.slice(0, COMMAND_HISTORY_MAX);
  try {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(COMMAND_HISTORY_KEY, JSON.stringify(cachedHistory));
    }
  } catch {
    // Ignore persistence failures (private mode / quota).
  }
  for (const listener of historyListeners) listener();
}

type SimoneChatBarProps = {
  sourcePage: string;
  onSuccess?: () => void;
};

/**
 * Dashboard fly-out replacement for the legacy regex-classified system
 * command bar. Submit opens a fresh Simone chat session in a new tab with
 * the operator's message pre-loaded and auto-sent — the standard 3-panel
 * agent-flow view (chat / events / files) runs the query while the dashboard
 * stays in the original tab.
 *
 * The submit path is purely client-side window.open; no POST. Task Hub
 * registration happens server-side via the `simone_chat` lifecycle hooks
 * once the new session's first websocket message lands.
 */
export default function SimoneChatBar({ sourcePage, onSuccess }: SimoneChatBarProps) {
  const [text, setText] = useState("");
  const [uploadingImage, setUploadingImage] = useState(false);
  const [imageError, setImageError] = useState("");
  const history = useSyncExternalStore(
    subscribeHistory,
    getHistorySnapshot,
    () => HISTORY_SERVER_SNAPSHOT,
  );

  const placeholder = useMemo(() => {
    if (sourcePage.includes("/tutorials")) {
      return "Ask Simone about this tutorial (a new chat tab will open)";
    }
    if (sourcePage.includes("/cron-jobs")) {
      return "Ask Simone (e.g., 'schedule a daily briefing at 7am')";
    }
    if (sourcePage.includes("/mission-control")) {
      return "Ask Simone (e.g., 'archive the Tokenrip quarantine email')";
    }
    return "Ask Simone (a new chat tab will open and run your query)";
  }, [sourcePage]);

  const clearHistory = useCallback(() => {
    writeHistory([]);
  }, []);

  const appendHistory = useCallback(
    (entry: ChatHistoryEntry) => {
      const current = getHistorySnapshot();
      const next = [entry, ...current.filter((row) => row.id !== entry.id)].slice(
        0,
        COMMAND_HISTORY_MAX,
      );
      writeHistory(next);
    },
    [],
  );

  const handlePaste = async (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    const items = e.clipboardData?.items;
    if (!items) return;

    for (let i = 0; i < items.length; i++) {
      if (items[i].type.indexOf("image") !== -1) {
        const file = items[i].getAsFile();
        if (!file) continue;

        e.preventDefault();
        setUploadingImage(true);
        setImageError("");

        try {
          const base64 = await new Promise<string>((resolve, reject) => {
            const reader = new FileReader();
            reader.onload = () => resolve(reader.result as string);
            reader.onerror = reject;
            reader.readAsDataURL(file);
          });

          const response = await fetch(`${API_BASE}/api/v1/vision/describe`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              image_base64: base64,
              prompt: "Describe this image in detail.",
            }),
          });

          const payload = await response.json().catch(() => ({}));
          if (!response.ok) {
            throw new Error(payload.detail || `Vision API failed (${response.status})`);
          }

          if (payload.ok && payload.description) {
            setText(
              (prev) =>
                prev + (prev ? "\n\n" : "") + `[Attached Image Description: ${payload.description}]\n`,
            );
          } else {
            throw new Error("Failed to get image description.");
          }
        } catch (err: any) {
          setImageError(err?.message || "Failed to process pasted image.");
        } finally {
          setUploadingImage(false);
        }
        break;
      }
    }
  };

  const handleSubmit = (event: FormEvent) => {
    event.preventDefault();
    const value = asText(text);
    if (!value) return;
    if (typeof window === "undefined") return;

    // Open the standard 3-panel chat view in a NEW tab. `newSession: true`
    // forces a fresh session id; `auto_send: true` triggers Simone to run
    // the query as soon as the websocket attaches. We deliberately bypass
    // `openOrFocusChatWindow` (which reuses a fixed window name) and call
    // window.open with target="_blank" so each submit spawns its own tab.
    const url = buildChatUrl({
      newSession: true,
      message: value,
      autoSend: true,
      focusInput: true,
    });
    window.open(url, "_blank");

    appendHistory({
      id: `${Date.now()}_${Math.random().toString(16).slice(2, 8)}`,
      at: new Date().toISOString(),
      source_page: sourcePage,
      text: value,
    });
    setText("");
    onSuccess?.();
  };

  const historyRows = history.slice(0, 5);

  return (
    <section className="mb-4 rounded-xl border border-border bg-background/70 p-3">
      <form onSubmit={handleSubmit} className="flex flex-col">
        <div className="flex flex-row items-center justify-between mb-2">
          <div className="flex flex-row items-center gap-3">
            <h2 className="text-xs font-semibold uppercase tracking-[0.14em] text-foreground/80">
              Ask Simone
            </h2>
            <div className="flex flex-row items-center gap-2 text-[11px] text-muted-foreground font-mono">
              <span>Opens a new chat tab with this query already running</span>
              <span className="text-muted">|</span>
              <span>route: {sourcePage}</span>
            </div>
          </div>
          <button
            type="submit"
            disabled={uploadingImage || !asText(text)}
            className="rounded-md border border-primary/30/70 bg-primary/15 px-3 py-1.5 text-[11px] font-medium text-primary/90 hover:bg-primary/20 disabled:opacity-50 transition-colors shrink-0"
          >
            {uploadingImage ? "Analyzing Image..." : "Send to Simone"}
          </button>
        </div>

        <textarea
          value={text}
          onChange={(event) => setText(event.target.value)}
          onPaste={handlePaste}
          placeholder={placeholder}
          rows={2}
          className="w-full resize-y rounded-md border border-border bg-background/70 px-3 py-2 text-sm text-foreground outline-none focus:border-primary mb-0 block"
        />
      </form>
      {imageError && (
        <div className="mt-2 rounded border border-red-400/30/70 bg-red-400/10 px-2 py-1 text-xs text-red-400/80">
          {imageError}
        </div>
      )}
      <div className="mt-1 rounded border border-border bg-background/30 p-2">
        <div className="mb-1 flex items-center justify-between">
          <span className="text-[11px] uppercase tracking-[0.14em] text-muted-foreground">
            Recent Queries
          </span>
          <button
            type="button"
            onClick={clearHistory}
            className="text-[11px] text-muted-foreground hover:text-foreground/80"
          >
            Clear
          </button>
        </div>
        {historyRows.length === 0 && (
          <div className="text-xs text-muted-foreground">No chat history yet on this browser.</div>
        )}
        <div className="space-y-1">
          {historyRows.map((row) => (
            <div
              key={row.id}
              className="rounded border border-border/50 bg-background/50 px-2 py-1 text-xs text-foreground/80"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="text-muted-foreground">
                  {new Date(row.at).toLocaleString(undefined, {
                    month: "short",
                    day: "numeric",
                    hour: "numeric",
                    minute: "2-digit",
                  })}
                </span>
                <button
                  type="button"
                  onClick={() => setText(row.text)}
                  className="text-[11px] text-primary hover:text-primary/90"
                >
                  Reuse
                </button>
              </div>
              <div className="truncate text-foreground">{row.text}</div>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
