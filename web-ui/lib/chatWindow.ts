export const CHAT_WINDOW_NAME = "ua-chat-window";

type ChatWindowOptions = {
  sessionId?: string | null;
  attachMode?: "default" | "tail";
  role?: "writer" | "viewer";
};

export function buildChatUrl(options?: ChatWindowOptions): string {
  const params = new URLSearchParams();
  const sessionId = (options?.sessionId || "").trim();
  if (sessionId) {
    params.set("session_id", sessionId);
  }
  if (options?.attachMode === "tail") {
    params.set("attach", "tail");
  }
  if (options?.role === "viewer") {
    params.set("role", "viewer");
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
