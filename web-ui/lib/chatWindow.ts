export const CHAT_WINDOW_NAME = "ua-chat-window";

type ChatWindowOptions = {
  sessionId?: string | null;
  runId?: string | null;
  attachMode?: "default" | "tail";
  role?: "writer" | "viewer";
  newSession?: boolean;
  focusInput?: boolean;
  message?: string;
  autoSend?: boolean;
};

export function buildChatUrl(options?: ChatWindowOptions): string {
  const params = new URLSearchParams();
  const sessionId = (options?.sessionId || "").trim();
  const runId = (options?.runId || "").trim();
  if (sessionId) {
    params.set("session_id", sessionId);
  }
  if (runId) {
    params.set("run_id", runId);
  }
  if (options?.attachMode === "tail") {
    params.set("attach", "tail");
  }
  if (options?.role === "viewer") {
    params.set("role", "viewer");
  }
  if (options?.newSession) {
    params.set("new_session", "1");
  }
  if (options?.focusInput) {
    params.set("focus_input", "1");
  }
  const message = String(options?.message || "").trim();
  if (message) {
    params.set("message", message);
  }
  if (options?.autoSend) {
    params.set("auto_send", "1");
  }
  const query = params.toString();
  return query ? `/?${query}` : "/";
}

export function openOrFocusChatWindow(options?: ChatWindowOptions): void {
  if (typeof window === "undefined") {
    return;
  }
  const url = buildChatUrl(options);
  const opened = window.open(url, CHAT_WINDOW_NAME);
  if (opened && typeof opened.focus === "function") {
    opened.focus();
    return;
  }
  window.location.href = url;
}
