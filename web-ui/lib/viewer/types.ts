// Mirrors src/universal_agent/viewer/resolver.py:SessionViewTarget +
// hydration.py output. Single source of truth lives on the backend; this
// file just types the JSON shape we already deserialize.
//
// DO NOT add producer-specific fields here. If a UI panel needs new data,
// extend the backend hydration payload and add the field here in the same
// PR — that prevents the per-producer URL/state drift that motivated
// Track B in the first place.

export type SessionViewTarget = {
  target_kind: "run" | "session";
  target_id: string;
  run_id: string | null;
  session_id: string | null;
  workspace_dir: string;
  is_live_session: boolean;
  source: string;
  viewer_href: string;
};

export type HistoryMessage = {
  role: string;
  ts: number | null;
  content: string;
  sub_agent: string | null;
  tool_calls: Array<Record<string, unknown>>;
};

export type LogEntry = {
  ts: number | null;
  level: string;
  channel: string;
  message: string;
};

export type WorkspaceEntry = {
  name: string;
  type: "file" | "dir";
  size: number;
  mtime: number | null;
};

export type Readiness = {
  state: "pending" | "ready" | "failed";
  reason: string | null;
  marker_ts: number | null;
};

export type HydrationPayload = {
  target: SessionViewTarget;
  history: HistoryMessage[];
  history_truncated_to: number | null;
  logs: LogEntry[];
  logs_cursor: number | null;
  workspace_root: string;
  workspace_entries: WorkspaceEntry[];
  readiness: Readiness;
};

export type ResolveInput = {
  session_id?: string | null;
  run_id?: string | null;
  workspace_dir?: string | null;
  workspace_name?: string | null;
};
